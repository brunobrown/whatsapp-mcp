"""Testes do SENT-08/09: as ferramentas de envio devolvem message_id, timestamp e chat_jid."""
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402

OK_BODY = {
    "success": True,
    "message": "Message sent to chat@g.us",
    "message_id": "3EB0ABC",
    "timestamp": "2026-10-21T10:30:00-03:00",
    "chat_jid": "chat@g.us",
}


def fake_response(status, payload):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


@pytest.fixture
def media_file(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"x")
    return str(path)


def invoke(tool, response, media_file):
    with mock.patch("whatsapp.requests.post", return_value=response):
        if tool == "send_message":
            return main.send_message("chat@g.us", "oi")
        if tool == "send_file":
            return getattr(main, "send_file")("chat@g.us", media_file)
        ogg = media_file.replace(".pdf", ".ogg")
        os.rename(media_file, ogg)
        return main.send_audio_message("chat@g.us", ogg)


TOOLS = ["send_message", "send_file", "send_audio_message"]


@pytest.mark.parametrize("tool", TOOLS)
def test_success_returns_identifiers(tool, media_file):  # SENT-08
    out = invoke(tool, fake_response(200, OK_BODY), media_file)
    assert out == {
        "success": True,
        "message": "Message sent to chat@g.us",
        "message_id": "3EB0ABC",
        "timestamp": "2026-10-21T10:30:00-03:00",
        "chat_jid": "chat@g.us",
    }


@pytest.mark.parametrize("tool", TOOLS)
def test_failure_has_no_message_id(tool, media_file):  # SENT-09
    body = {"success": False, "message": "Error sending message: boom"}
    out = invoke(tool, fake_response(500, body), media_file)
    assert out["success"] is False
    assert "message_id" not in out


@pytest.mark.parametrize("tool", TOOLS)
def test_failure_with_http_200_has_no_message_id(tool, media_file):  # SENT-09
    body = {"success": False, "message": "Not connected to WhatsApp", "message_id": "STALE"}
    out = invoke(tool, fake_response(200, body), media_file)
    assert out["success"] is False
    assert "message_id" not in out


def test_old_bridge_without_identifiers_still_succeeds():  # compatibilidade
    body = {"success": True, "message": "Message sent to 5511999990000"}
    with mock.patch("whatsapp.requests.post", return_value=fake_response(200, body)):
        out = main.send_message("5511999990000", "oi")
    assert out == {"success": True, "message": "Message sent to 5511999990000"}
