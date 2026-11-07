"""As cinco ferramentas de enquete estão registradas e respondem no formato das demais ferramentas de envio."""
import asyncio
import json
import os
import sqlite3
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import whatsapp  # noqa: E402

GROUP = "120363222@g.us"


def tool_schemas():
    return {t.name: t.input_schema for t in asyncio.run(main.mcp.list_tools())}


def test_the_five_tools_are_registered_with_their_parameters():  # SEND-01, RES-01, LIST-01, VOTE-01, DEL-01
    schemas = tool_schemas()
    expected = {
        "send_poll": ({"recipient", "question", "options", "allow_multiple", "allow_add_option", "hide_voter_names",
                       "override_token", "dry_run"}, {"recipient", "question", "options"}),
        "get_poll_results": ({"message_id", "chat_jid"}, {"message_id"}),
        "list_polls": ({"chat_jid", "limit", "page"}, {"chat_jid"}),
        "vote_poll": ({"chat_jid", "message_id", "options", "override_token", "dry_run"}, {"chat_jid", "message_id", "options"}),
        "delete_message": ({"chat_jid", "message_id", "override_token", "dry_run"}, {"chat_jid", "message_id"}),
    }
    for name, (properties, required) in expected.items():
        assert name in schemas, f"{name} is not registered"
        assert set(schemas[name]["properties"]) == properties, name
        assert set(schemas[name].get("required", [])) == required, name


def make_db(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);"
        "CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,"
        " is_from_me BOOLEAN, media_type TEXT, PRIMARY KEY (id, chat_jid));"
        "INSERT INTO chats VALUES ('120363222@g.us', 'Teste', NULL);")
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)


def test_send_poll_answers_like_the_other_send_tools(tmp_path, monkeypatch):  # SEND-01, SEND-05
    make_db(tmp_path, monkeypatch)
    resp = mock.Mock(status_code=200, text="ok")
    resp.json.return_value = {"success": True, "message": "Poll sent", "message_id": "P1",
                              "timestamp": "2026-11-06T10:30:00-03:00", "chat_jid": GROUP}
    with mock.patch("whatsapp.requests.post", return_value=resp) as post:
        sent = main.send_poll(GROUP, "Almoço?", ["Sim", "Não"])
        dry = main.send_poll(GROUP, "Almoço?", ["Sim", "Não"], dry_run=True)
        invalid = main.send_poll(GROUP, "Almoço?", ["Sim"])

    assert sent == {"success": True, "message": "Poll sent", "message_id": "P1",
                    "timestamp": "2026-11-06T10:30:00-03:00", "chat_jid": GROUP}
    assert dry["success"] is True and dry["dry_run"] is True and dry["preview"]["question"] == "Almoço?"
    assert invalid["success"] is False and invalid["error_code"] == "INVALID_POLL"
    assert post.call_count == 1  # only the real send reached the bridge


def test_read_tools_return_the_dictionaries_of_the_module(tmp_path, monkeypatch):  # RES-05, LIST-01
    make_db(tmp_path, monkeypatch)
    assert main.get_poll_results("NOPE")["error_code"] == "POLL_NOT_FOUND"
    assert main.list_polls(GROUP) == {"success": True, "chat_jid": GROUP, "polls": [], "total": 0,
                                      "page": 0, "limit": 20, "has_more": False, "untrusted_content": True}
