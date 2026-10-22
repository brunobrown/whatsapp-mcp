"""Testes do T3: o servidor MCP envia o token da API e reporta BRIDGE_UNAUTHORIZED (API-06)."""
import os
import sys
from unittest import mock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import localconfig  # noqa: E402
import main  # noqa: E402
import whatsapp  # noqa: E402

TOKEN = "ab" * 32


def write_token(content=TOKEN + "\n"):
    directory = localconfig.config_dir()
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "api_token"), "w") as f:
        f.write(content)


def reply(status=200, payload=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {}
    r.text = str(payload)
    return r


def bearer(call):
    return call.kwargs.get("headers", {}).get("Authorization")


def test_send_download_participants_and_status_send_the_token():  # API-06
    write_token()
    ok = reply(200, {"success": True, "message": "ok", "path": "/p", "participants": []})
    with mock.patch("whatsapp.requests.post", return_value=ok) as post, \
            mock.patch("whatsapp.requests.get", return_value=reply(200, {"connected": True})) as get:
        whatsapp._post_send({"recipient": "x", "message": "m"})
        whatsapp.download_media("M", "c@g.us")
        whatsapp.get_group_participants("g@g.us")
        whatsapp.get_connection_status()

    assert [bearer(c) for c in post.call_args_list] == [f"Bearer {TOKEN}"] * 3
    assert bearer(get.call_args) == f"Bearer {TOKEN}"


def test_the_token_is_read_on_every_call():  # API-06
    write_token()
    with mock.patch("whatsapp.requests.post", return_value=reply(200, {"success": True})) as post:
        whatsapp._post_send({})
        write_token("cd" * 32 + "\n")
        whatsapp._post_send({})

    assert [bearer(c) for c in post.call_args_list] == [f"Bearer {TOKEN}", "Bearer " + "cd" * 32]


@pytest.mark.parametrize("content", [None, "", "curto", "zz" * 32, "AB" * 32, TOKEN + "00"])
def test_without_a_valid_token_no_header_is_sent_and_nothing_breaks(content):  # API-06
    if content is not None:
        write_token(content)
    with mock.patch("whatsapp.requests.post", return_value=reply(200, {"success": True})) as post:
        out = whatsapp._post_send({})

    assert out.success is True and "Authorization" not in post.call_args.kwargs.get("headers", {})


def test_a_token_file_that_is_not_text_is_ignored():  # API-06
    os.makedirs(localconfig.config_dir(), exist_ok=True)
    with open(os.path.join(localconfig.config_dir(), "api_token"), "wb") as f:
        f.write(b"\xff\xfe\x00")

    assert localconfig.read_api_token() is None


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_send_is_bridge_unauthorized(status):  # API-06
    write_token()
    with mock.patch("whatsapp.requests.post", return_value=reply(status, {"success": False, "error_code": "UNAUTHORIZED"})):
        result = whatsapp._post_send({"recipient": "x", "message": "m"})
        tool = main.send_response(result)

    assert result.success is False and result.error_code == "BRIDGE_UNAUTHORIZED"
    assert tool["error_code"] == "BRIDGE_UNAUTHORIZED" and "Reinicie" in tool["message"] and TOKEN not in str(tool)


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_download_is_bridge_unauthorized_with_a_hint(status):  # API-06
    write_token()
    with mock.patch("whatsapp.requests.post", return_value=reply(status, {"success": False})):
        out = main.download_media("M", "c@g.us")

    assert out["success"] is False and out["error_code"] == "BRIDGE_UNAUTHORIZED"
    assert "Reinicie" in out["hint"] and TOKEN not in str(out)


@pytest.mark.parametrize("status", [401, 403])
def test_refused_participants_are_bridge_unauthorized(status):  # API-06
    write_token()
    with mock.patch("whatsapp.requests.post", return_value=reply(status, {"success": False})):
        out = main.get_group_participants("g@g.us")

    assert out["success"] is False and out["error_code"] == "BRIDGE_UNAUTHORIZED" and "Reinicie" in out["message"]


@pytest.mark.parametrize("status", [401, 403])
def test_refused_status_is_bridge_unauthorized_but_reachable(status):  # API-06
    write_token()
    with mock.patch("whatsapp.requests.get", return_value=reply(status, {"success": False})):
        out = whatsapp.get_connection_status()

    assert out["connected"] is False and out["bridge_reachable"] is True
    assert out["error_code"] == "BRIDGE_UNAUTHORIZED" and "Reinicie" in out["message"]


def test_a_refusal_with_a_body_that_is_not_json_is_still_unauthorized():  # API-06
    bad = reply(401)
    bad.json.side_effect = ValueError("no json")
    with mock.patch("whatsapp.requests.post", return_value=bad):
        assert whatsapp.download_media("M", "c@g.us").error_code == "BRIDGE_UNAUTHORIZED"
        assert whatsapp._post_send({}).error_code == "BRIDGE_UNAUTHORIZED"
        assert whatsapp.get_group_participants("g@g.us").error_code == "BRIDGE_UNAUTHORIZED"


def test_other_errors_keep_their_codes():  # API-06
    with mock.patch("whatsapp.requests.post", return_value=reply(500, {"success": False, "message": "Failed to download media: not a media message"})):
        assert whatsapp.download_media("M", "c@g.us").error_code == "MEDIA_NOT_FOUND"


def test_config_dir_follows_the_platform_rule(monkeypatch):  # API-06
    monkeypatch.delenv("WHATSAPP_CONFIG_DIR")
    monkeypatch.setenv("HOME", "/home/ana")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    monkeypatch.setattr(sys, "platform", "linux")
    assert localconfig.config_dir() == "/xdg/whatsapp-mcp"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert localconfig.config_dir() == "/home/ana/.config/whatsapp-mcp"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert localconfig.config_dir() == "/home/ana/Library/Application Support/whatsapp-mcp"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "C:\\Users\\ana\\AppData\\Roaming")
    assert localconfig.config_dir().endswith("whatsapp-mcp") and "AppData" in localconfig.config_dir()
    monkeypatch.setenv("WHATSAPP_CONFIG_DIR", "/forced")
    assert localconfig.config_dir() == "/forced"


def test_a_batch_stops_when_the_bridge_refuses_the_token():  # API-06
    import sqlite3
    import tempfile
    db = os.path.join(tempfile.mkdtemp(), "m.db")
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
        CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN,
            media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER,
            PRIMARY KEY (id, chat_jid));
        INSERT INTO chats VALUES ('g@g.us', 'G', NULL);
        INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me, media_type, filename, file_length)
            VALUES ('A', 'g@g.us', 's', '', '2026-10-21 10:00:00+00:00', 0, 'image', 'a', 1),
                   ('B', 'g@g.us', 's', '', '2026-10-21 10:01:00+00:00', 0, 'image', 'b', 1);""")
    c.commit()
    c.close()
    with mock.patch.object(whatsapp, "MESSAGES_DB_PATH", db), \
            mock.patch("whatsapp.requests.post", return_value=reply(401, {"success": False})) as post:
        out = main.download_media_batch("g@g.us")

    assert out["aborted"] == "BRIDGE_UNAUTHORIZED" and post.call_count == 1
    assert [i["status"] for i in out["items"]] == ["failed", "skipped"]
