"""Testes do P2-12: download_media_batch (janela, erros por item, limites)."""
import os
import sqlite3
import sys
from unittest import mock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import whatsapp  # noqa: E402

CHAT = "g@g.us"
EXPIRED = ("Failed to download media: download failed with status code 403 (retry also failed: timed out waiting "
           "for media retry response from WhatsApp (make sure your phone is online, unlocked, and WhatsApp is not force-stopped))")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
        CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
            is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB,
            file_enc_sha256 BLOB, file_length INTEGER, local_path TEXT, mimetype TEXT, forwarded BOOLEAN,
            quoted_id TEXT, PRIMARY KEY (id, chat_jid));
        INSERT INTO chats VALUES ('g@g.us', 'Grupo', NULL), ('o@g.us', 'Outro', NULL);""")
    c.commit()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    monkeypatch.delenv("WHATSAPP_TIMEZONE", raising=False)
    return c


def add(conn, id, minute, media_type="image", chat=CHAT, size=1000, local_path=None, hour=10):
    conn.execute(
        "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me, media_type, filename, file_length, local_path) "
        "VALUES (?, ?, 's', '', ?, 0, ?, ?, ?, ?)",
        (id, chat, f"2026-10-21 {hour:02d}:{minute:02d}:00+00:00", media_type,
         f"{id}.bin" if media_type else None, size if media_type else None, local_path))
    conn.commit()


def reply(status, payload):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload
    r.text = str(payload)
    return r


class Bridge:
    """Responde /api/download por message_id e guarda as chamadas na ordem."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, url, json=None, **kwargs):
        self.calls.append((json["message_id"], kwargs))
        answer = self.answers.get(json["message_id"], reply(200, {"success": True, "path": f"/m/{json['message_id']}"}))
        if isinstance(answer, Exception):
            raise answer
        return answer


def run(conn, answers=None, **kwargs):
    bridge = Bridge(answers or {})
    with mock.patch("whatsapp.requests.post", bridge):
        out = main.download_media_batch(CHAT, **kwargs)
    return out, bridge


def statuses(out):
    return {i["message_id"]: i["status"] for i in out["items"]}


def test_valid_expired_and_text_in_one_window(conn):  # BATCH-01, BATCH-02, BATCH-03, BATCH-04
    add(conn, "OK", 1)
    add(conn, "TEXT", 2, media_type=None)
    add(conn, "OLD", 3, media_type="video")
    add(conn, "OK2", 4, media_type="document")

    out, bridge = run(conn, {"OLD": reply(500, {"success": False, "message": EXPIRED})})

    assert out["success"] is True
    assert [i["message_id"] for i in out["items"]] == ["OK", "OLD", "OK2"]  # cronológico, sem o texto
    assert [c[0] for c in bridge.calls] == ["OK", "OLD", "OK2"]  # a falha no meio não interrompe
    assert statuses(out) == {"OK": "downloaded", "OLD": "failed", "OK2": "downloaded"}
    ok, old = out["items"][0], out["items"][1]
    assert ok["path"] == "/m/OK" and ok["media_type"] == "image" and ok["filename"] == "OK.bin" and ok["timestamp"]
    assert old["path"] is None and old["error_code"] == "LINK_EXPIRED_PHONE_OFFLINE"
    assert old["message"] == EXPIRED and "celular" in old["hint"]
    assert (out["total"], out["downloaded"], out["already_downloaded"], out["failed"], out["skipped"]) == (3, 2, 0, 1, 0)
    assert out["truncated"] is False and out["aborted"] is None


def test_only_the_requested_chat_window_and_types(conn):  # BATCH-01
    add(conn, "IN", 30, hour=10)
    add(conn, "EARLY", 0, hour=9)
    add(conn, "LATE", 0, hour=12)
    add(conn, "VID", 31, media_type="video", hour=10)
    add(conn, "ELSEWHERE", 30, chat="o@g.us", hour=10)

    out, bridge = run(conn, after="2026-10-21T09:30:00+00:00", before="2026-10-21T11:00:00+00:00", types=["image"])

    assert [c[0] for c in bridge.calls] == ["IN"]
    assert out["total"] == 1


def test_default_types_cover_what_the_bridge_downloads(conn):  # BATCH-01
    for n, kind in enumerate(["image", "video", "audio", "document", "sticker"]):
        add(conn, kind.upper(), n, media_type=kind)

    out, _ = run(conn)

    assert [i["media_type"] for i in out["items"]] == ["image", "video", "audio", "document"]


def test_empty_window_is_success(conn):  # BATCH-06
    add(conn, "TEXT", 1, media_type=None)

    out, bridge = run(conn)

    assert out["success"] is True and out["total"] == 0 and out["items"] == [] and bridge.calls == []


def test_file_already_on_disk_skips_the_bridge(conn, tmp_path):  # BATCH-05
    present = tmp_path / "a.jpg"
    present.write_bytes(b"x")
    add(conn, "HAVE", 1, local_path=str(present))
    add(conn, "GONE", 2, local_path=str(tmp_path / "apagado.jpg"))  # retenção apagou o arquivo

    out, bridge = run(conn)

    assert [c[0] for c in bridge.calls] == ["GONE"]
    assert out["items"][0]["status"] == "already_downloaded" and out["items"][0]["path"] == str(present)
    assert out["already_downloaded"] == 1 and out["downloaded"] == 1


def test_too_large_item_is_skipped_without_calling_the_bridge(conn):  # LIM-01
    add(conn, "BIG", 1, size=30 * 1024 * 1024)
    add(conn, "EDGE", 2, size=25 * 1024 * 1024)   # exatamente no limite: baixa
    add(conn, "UNKNOWN", 3, size=0)                # tamanho desconhecido: segue para o bridge

    out, bridge = run(conn, max_size_mb=25)

    assert [c[0] for c in bridge.calls] == ["EDGE", "UNKNOWN"]
    big = out["items"][0]
    assert big["status"] == "skipped" and big["error_code"] == "SKIPPED_TOO_LARGE" and "30" in big["message"]
    assert out["skipped"] == 1 and out["downloaded"] == 2


def test_max_items_keeps_the_oldest_and_flags_truncation(conn):  # LIM-02
    for n in range(5):
        add(conn, f"M{n}", n)

    out, bridge = run(conn, max_items=3)

    assert [c[0] for c in bridge.calls] == ["M0", "M1", "M2"]
    assert out["truncated"] is True and out["total"] == 3
    out, _ = run(conn, max_items=5)
    assert out["truncated"] is False


def test_each_download_gets_its_own_timeout_and_a_timeout_does_not_stop_the_batch(conn):  # LIM-03
    add(conn, "SLOW", 1)
    add(conn, "NEXT", 2)

    out, bridge = run(conn, {"SLOW": requests.Timeout("read timed out")}, timeout_seconds=7)

    assert [kw["timeout"] for _, kw in bridge.calls] == [7, 7]
    slow = out["items"][0]
    assert slow["status"] == "failed" and slow["error_code"] == "DOWNLOAD_TIMEOUT"
    assert out["items"][1]["status"] == "downloaded"


def test_default_timeout_is_longer_than_the_media_retry_wait(conn):  # LIM-03
    add(conn, "M", 1)

    _, bridge = run(conn)

    assert bridge.calls[0][1]["timeout"] == 120  # o bridge espera até 90 s pelo celular


def test_unreachable_bridge_stops_the_batch(conn):  # LIM-04
    for n in range(3):
        add(conn, f"M{n}", n)

    out, bridge = run(conn, {"M1": requests.ConnectionError("refused")})

    assert [c[0] for c in bridge.calls] == ["M0", "M1"]
    assert statuses(out) == {"M0": "downloaded", "M1": "failed", "M2": "skipped"}
    assert out["items"][1]["error_code"] == "BRIDGE_UNREACHABLE"
    assert out["aborted"] == "BRIDGE_UNREACHABLE" and out["skipped"] == 1 and out["total"] == 3


@pytest.mark.parametrize("kwargs", [
    {"types": ["image", "gif"]}, {"max_items": 0}, {"max_items": 201}, {"max_size_mb": 0}, {"max_size_mb": -1},
    {"timeout_seconds": 0}, {"timeout_seconds": 601}, {"after": "ontem"}, {"before": "amanhã"},
])
def test_invalid_arguments_are_refused_before_any_call(conn, kwargs):  # LIM-05
    add(conn, "M", 1)

    out, bridge = run(conn, **kwargs)

    assert out["success"] is False and out["error_code"] == "INVALID_ARGUMENT" and out["message"]
    assert bridge.calls == []


def test_empty_chat_jid_is_refused(conn):  # LIM-05
    with mock.patch("whatsapp.requests.post") as post:
        out = main.download_media_batch("")
    assert out["success"] is False and out["error_code"] == "INVALID_ARGUMENT"
    post.assert_not_called()


def test_download_media_without_timeout_keeps_calling_as_before():  # LIM-06
    with mock.patch("whatsapp.requests.post", return_value=reply(200, {"success": True, "path": "/p"})) as post:
        whatsapp.download_media("M", CHAT)
    assert "timeout" not in post.call_args.kwargs


def test_download_media_timeout_is_its_own_error_code():  # LIM-03
    with mock.patch("whatsapp.requests.post", side_effect=requests.Timeout("slow")):
        result = whatsapp.download_media("M", CHAT, timeout=3)
    assert result.success is False and result.error_code == "DOWNLOAD_TIMEOUT"
