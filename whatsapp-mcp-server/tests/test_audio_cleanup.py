"""send_audio_message deletes the converted .ogg after sending, and only that file."""
import os
import sqlite3
import sys
import tempfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import whatsapp  # noqa: E402

GROUP = "120363222@g.us"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);"
        "CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,"
        " is_from_me BOOLEAN, media_type TEXT, PRIMARY KEY (id, chat_jid));"
        f"INSERT INTO chats VALUES ('{GROUP}', 'Test', NULL);")
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "voice.mp3"
    path.write_bytes(b"audio")
    return str(path)


def fake_convert(created):
    """Imitates ffmpeg: creates a real temporary .ogg and records its path."""
    def convert(_input):
        handle = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        handle.write(b"ogg")
        handle.close()
        created.append(handle.name)
        return handle.name
    return convert


def response(status=200, body=None):
    resp = mock.Mock(status_code=status, text="ok")
    resp.json.return_value = body or {"success": True, "message": "sent", "message_id": "A1", "chat_jid": GROUP}
    return resp


def test_converted_file_is_removed_after_a_successful_send(db, source):  # AUD-01
    created, posted = [], []
    with mock.patch("whatsapp.audio.convert_to_opus_ogg_temp", fake_convert(created)), \
         mock.patch("whatsapp.requests.post", side_effect=lambda *a, **k: (posted.append(k["json"]["media_path"]), response())[1]):
        out = whatsapp.send_audio_message(GROUP, source)

    assert out.success is True
    assert posted == created, "the bridge must be given the converted file"
    assert not os.path.exists(created[0]), "the converted .ogg must be gone"
    assert os.path.exists(source), "the user's original file must stay"


def test_converted_file_is_removed_when_the_send_fails(db, source):  # AUD-01
    created = []
    with mock.patch("whatsapp.audio.convert_to_opus_ogg_temp", fake_convert(created)), \
         mock.patch("whatsapp.requests.post", return_value=response(500)):
        out = whatsapp.send_audio_message(GROUP, source)

    assert out.success is False
    assert not os.path.exists(created[0])


def test_converted_file_is_removed_when_the_request_raises(db, source):  # AUD-01
    import requests
    created = []
    with mock.patch("whatsapp.audio.convert_to_opus_ogg_temp", fake_convert(created)), \
         mock.patch("whatsapp.requests.post", side_effect=requests.ConnectionError("down")):
        out = whatsapp.send_audio_message(GROUP, source)

    assert out.success is False
    assert not os.path.exists(created[0])


def test_an_ogg_given_by_the_user_is_never_removed(db, tmp_path):  # AUD-02
    ogg = tmp_path / "voice.ogg"
    ogg.write_bytes(b"ogg")
    with mock.patch("whatsapp.audio.convert_to_opus_ogg_temp") as convert, \
         mock.patch("whatsapp.requests.post", return_value=response()):
        out = whatsapp.send_audio_message(GROUP, str(ogg))

    assert out.success is True
    convert.assert_not_called()
    assert ogg.exists()


def test_failing_to_remove_the_temp_file_does_not_change_the_result(db, source):  # AUD-03
    created = []
    with mock.patch("whatsapp.audio.convert_to_opus_ogg_temp", fake_convert(created)), \
         mock.patch("whatsapp.requests.post", return_value=response()), \
         mock.patch("whatsapp.os.unlink", side_effect=OSError("busy")):
        out = whatsapp.send_audio_message(GROUP, source)

    assert out.success is True and out.message_id == "A1"
    os.unlink(created[0])  # the test cleans up what it kept alive


def test_a_failed_conversion_leaves_nothing_to_remove(db, source):  # AUD-01
    with mock.patch("whatsapp.audio.convert_to_opus_ogg_temp", side_effect=RuntimeError("no ffmpeg")), \
         mock.patch("whatsapp.requests.post") as post:
        out = whatsapp.send_audio_message(GROUP, source)

    assert out.success is False and "ffmpeg" in out.message
    post.assert_not_called()
