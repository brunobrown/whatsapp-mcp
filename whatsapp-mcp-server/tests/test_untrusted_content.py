"""The server warns that message text comes from other people and is data, not instructions."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402


def test_the_server_declares_that_message_text_is_untrusted():  # UNT-01
    text = (main.mcp.instructions or "").lower()
    assert text, "the server must declare instructions"
    for needle in ("other people", "untrusted", "do not follow", "send_policy"):
        assert needle in text, f"instructions must mention '{needle}'"


import sqlite3  # noqa: E402

import whatsapp  # noqa: E402

A = "a@g.us"


def make_db(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
        CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
            is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB,
            file_enc_sha256 BLOB, file_length INTEGER, local_path TEXT, mimetype TEXT, forwarded BOOLEAN,
            quoted_id TEXT, PRIMARY KEY (id, chat_jid));
        CREATE TABLE polls (id TEXT, chat_jid TEXT, author TEXT, question TEXT, options_json TEXT,
            selectable_count INTEGER, allow_add_option BOOLEAN, hide_voter_names BOOLEAN, PRIMARY KEY (id, chat_jid));
        CREATE TABLE poll_votes (poll_id TEXT, chat_jid TEXT, voter TEXT, hashes_json TEXT, timestamp_ms INTEGER,
            PRIMARY KEY (poll_id, chat_jid, voter));
        INSERT INTO chats VALUES ('a@g.us', 'Project', NULL);
        INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me)
            VALUES ('P1', 'a@g.us', 'alice', '[poll] Q', '2026-11-06 10:00:00+00:00', 0);
        INSERT INTO polls VALUES ('P1', 'a@g.us', 'alice', 'Q', '["Yes","No"]', 0, 0, 0);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)


def test_dictionary_reads_that_carry_other_peoples_text_are_marked(tmp_path, monkeypatch):  # UNT-02
    make_db(tmp_path, monkeypatch)

    for name, result in {
        "get_message_thread": main.get_message_thread(message_id="P1", chat_jid=A),
        "get_poll_results": main.get_poll_results("P1", A),
        "list_polls": main.list_polls(A),
        "get_message_reactions": main.get_message_reactions("P1", A),
    }.items():
        assert result["success"] is True and result["untrusted_content"] is True, name


def test_an_error_result_is_not_marked(tmp_path, monkeypatch):  # UNT-02
    make_db(tmp_path, monkeypatch)

    for name, result in {
        "get_message_thread": main.get_message_thread(message_id="NOPE", chat_jid=A),
        "get_poll_results": main.get_poll_results("NOPE", A),
        "list_polls": main.list_polls(A, limit=0),
        "get_message_reactions": main.get_message_reactions("NOPE", A),
    }.items():
        assert result["success"] is False and "untrusted_content" not in result, name
