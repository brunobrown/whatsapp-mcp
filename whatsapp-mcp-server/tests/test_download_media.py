"""Testes do DLERR-01..06: a ferramenta download_media reporta o erro real do bridge."""
import os
import sys
from unittest import mock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402

BRIDGE_403 = (
    "Failed to download media: download failed with status code 403 "
    "(retry also failed: timed out waiting for media retry response from WhatsApp "
    "(make sure your phone is online, unlocked, and WhatsApp is not force-stopped))"
)
HINT = (
    "O link da mídia expirou e o celular não respondeu ao pedido de reenvio. "
    "Abra o WhatsApp no celular conectado ao bridge, desbloqueado e online, e tente de novo."
)


def fake_response(status, payload):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


def call(response=None, exc=None):
    target = mock.Mock(return_value=response, side_effect=exc)
    with mock.patch("whatsapp.requests.post", target):
        return main.download_media("MSG", "chat@g.us")


def test_link_expired_phone_offline():  # DLERR-01
    out = call(fake_response(500, {"success": False, "message": BRIDGE_403}))
    assert out["success"] is False
    assert out["error_code"] == "LINK_EXPIRED_PHONE_OFFLINE"
    assert out["message"] == BRIDGE_403
    assert out["hint"] == HINT


@pytest.mark.parametrize("bridge_msg", [
    "Failed to download media: not a media message",
    "Failed to download media: failed to find message: sql: no rows in result set",
])
def test_media_not_found(bridge_msg):  # DLERR-02
    out = call(fake_response(500, {"success": False, "message": bridge_msg}))
    assert out["success"] is False
    assert out["error_code"] == "MEDIA_NOT_FOUND"
    assert out["message"] == bridge_msg
    assert "hint" not in out


@pytest.mark.parametrize("exc", [
    requests.ConnectionError("connection refused"),
    requests.Timeout("read timed out"),
])
def test_bridge_unreachable(exc):  # DLERR-03
    out = call(exc=exc)
    assert out["success"] is False
    assert out["error_code"] == "BRIDGE_UNREACHABLE"
    assert str(exc) in out["message"]
    assert "hint" not in out


def test_unknown_error_keeps_bridge_message():  # DLERR-04
    msg = "Failed to download media: disk full"
    out = call(fake_response(500, {"success": False, "message": msg}))
    assert out["error_code"] == "UNKNOWN"
    assert out["message"] == msg
    assert "hint" not in out


def test_403_without_retry_timeout_is_not_phone_offline():  # DLERR-04 (fronteira de DLERR-01)
    msg = "Failed to download media: download failed with status code 403"
    out = call(fake_response(500, {"success": False, "message": msg}))
    assert out["error_code"] == "UNKNOWN"


def test_success_shape():  # DLERR-05
    out = call(fake_response(200, {"success": True, "message": "ok", "path": "/x/y.jpg"}))
    assert out == {
        "success": True,
        "message": "Media downloaded successfully",
        "file_path": "/x/y.jpg",
    }


def test_no_stdout(capsys):  # DLERR-06
    call(fake_response(200, {"success": True, "message": "ok", "path": "/x/y.jpg"}))
    call(fake_response(500, {"success": False, "message": BRIDGE_403}))
    call(exc=requests.ConnectionError("boom"))
    assert capsys.readouterr().out == ""
