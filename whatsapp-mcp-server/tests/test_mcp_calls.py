"""Each tool, called through the MCP server itself, returns what it declares.

mcp 2.x validates each tool's return value against the declared type; calling the function directly skips
that validation. Here every registered tool is called through `call_tool`, against a test database and with
a mocked bridge, and none may turn into a tool error."""
import asyncio
import os
import sqlite3
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import whatsapp  # noqa: E402

GROUP = "120363222@g.us"
DM = "5511999990000@s.whatsapp.net"


@pytest.fixture
def world(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.executescript(f"""
        CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
        CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
            is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB,
            file_enc_sha256 BLOB, file_length INTEGER, local_path TEXT, mimetype TEXT, forwarded BOOLEAN,
            quoted_id TEXT, PRIMARY KEY (id, chat_jid));
        CREATE TABLE polls (id TEXT, chat_jid TEXT, author TEXT, question TEXT, options_json TEXT,
            selectable_count INTEGER, allow_add_option BOOLEAN, hide_voter_names BOOLEAN, PRIMARY KEY (id, chat_jid));
        CREATE TABLE poll_votes (poll_id TEXT, chat_jid TEXT, voter TEXT, hashes_json TEXT, timestamp_ms INTEGER,
            PRIMARY KEY (poll_id, chat_jid, voter));
        INSERT INTO chats VALUES ('{GROUP}', 'Test', '2026-11-06 10:00:00+00:00'), ('{DM}', 'Maria', '2026-11-06 09:00:00+00:00');
        INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES
            ('M1', '{GROUP}', '5511999990000', 'hi', '2026-11-06 10:00:00+00:00', 0),
            ('MINE', '{GROUP}', '5511900000000', 'mine', '2026-11-06 10:01:00+00:00', 1),
            ('P1', '{GROUP}', '5511999990000', '[poll] Q', '2026-11-06 10:02:00+00:00', 0),
            ('D1', '{DM}', '5511999990000', 'hi maria', '2026-11-06 09:00:00+00:00', 0);
        INSERT INTO polls VALUES ('P1', '{GROUP}', '5511999990000', 'Q', '["Yes","No"]', 0, 0, 0);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"ogg")
    doc = tmp_path / "doc.txt"
    doc.write_bytes(b"x")
    return {"audio": str(audio), "doc": str(doc)}


def arguments(files):
    return {
        "search_contacts": {"query": "Maria"},
        "list_messages": {},
        "list_chats": {},
        "get_chat": {"chat_jid": GROUP},
        "get_direct_chat_by_contact": {"sender_phone_number": "5511999990000"},
        "get_contact_chats": {"jid": DM},
        "get_last_interaction": {"jid": DM},
        "get_message_context": {"message_id": "M1"},
        "get_message_thread": {"message_id": "M1", "chat_jid": GROUP},
        "get_poll_results": {"message_id": "P1", "chat_jid": GROUP},
        "list_polls": {"chat_jid": GROUP},
        "get_monitor_profiles": {},
        "monitor_messages": {"targets": ["5511999990000"]},
        "get_group_participants": {"chat_jid": GROUP},
        "get_connection_status": {},
        "send_message": {"recipient": GROUP, "message": "hi"},
        "send_file": {"recipient": GROUP, "media_path": files["doc"]},
        "send_audio_message": {"recipient": GROUP, "media_path": files["audio"]},
        "send_poll": {"recipient": GROUP, "question": "Q?", "options": ["A", "B"]},
        "vote_poll": {"chat_jid": GROUP, "message_id": "P1", "options": ["Yes"]},
        "react_to_message": {"chat_jid": GROUP, "message_id": "M1", "emoji": "👍"},
        "get_message_reactions": {"message_id": "M1", "chat_jid": GROUP},
        "delete_message": {"chat_jid": GROUP, "message_id": "MINE"},
        "monitor_reply": {"profile": "none", "chat_jid": GROUP, "message_id": "M1", "text": "hi"},
        "download_media": {"message_id": "M1", "chat_jid": GROUP},
        "download_media_batch": {"chat_jid": GROUP},
    }


def bridge_answer():
    resp = mock.Mock(status_code=200, text="ok")
    resp.json.return_value = {"success": True, "message": "ok", "message_id": "X1", "chat_jid": GROUP,
                              "participants": [], "filename": "f", "path": "/tmp/f"}
    return resp


def test_every_registered_tool_is_covered_by_the_call_table(world):  # MCP2-02
    registered = {t.name for t in asyncio.run(main.mcp.list_tools())}
    assert registered == set(arguments(world)), "register the new tool in the call table"


@pytest.mark.parametrize("name", sorted(arguments({"audio": "", "doc": ""})))
def test_calling_the_tool_through_the_server_does_not_fail_validation(world, name):  # MCP2-02
    args = arguments(world)[name]
    with mock.patch("whatsapp.requests.post", return_value=bridge_answer()), \
         mock.patch("whatsapp.requests.get", return_value=bridge_answer()):
        result = asyncio.run(main.mcp.call_tool(name, args))

    text = " ".join(getattr(block, "text", "") for block in getattr(result, "content", []))
    assert not result.is_error, f"{name} failed through the server: {text[:300]}"


def test_converted_tools_keep_every_field_and_show_times_as_iso_text(world):  # MCP2-02
    chats = main.list_chats()
    test_chat = next(c for c in chats if c["jid"] == GROUP)
    assert test_chat["name"] == "Test" and isinstance(test_chat["last_message_time"], str)
    assert {"jid", "name", "last_message_time", "last_message", "last_sender", "last_is_from_me"} <= set(test_chat)

    chat = main.get_chat(GROUP)
    assert chat["jid"] == GROUP and chat["last_message_time"].startswith("2026-11-06T10:")
    assert main.get_chat("does-not-exist@g.us") is None

    context = main.get_message_context("M1", before=0, after=1, chat_jid=GROUP)
    assert context["message"]["id"] == "M1" and context["message"]["timestamp"].startswith("2026-11-06T10:00")
    assert [m["id"] for m in context["after"]] == ["MINE"] and context["before"] == []

    contacts = main.search_contacts("Maria")
    assert contacts and set(contacts[0]) == {"phone_number", "name", "jid"}
