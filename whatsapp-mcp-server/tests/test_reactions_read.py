"""get_message_reactions: list of current reactions, count per emoji and errors."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import whatsapp  # noqa: E402

A, B = "a@g.us", "b@g.us"
SCHEMA = """
    CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
    CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
        is_from_me BOOLEAN, media_type TEXT, PRIMARY KEY (id, chat_jid));
    INSERT INTO chats VALUES ('a@g.us', 'Project', NULL), ('b@g.us', 'Family', NULL),
        ('5511999990000@s.whatsapp.net', 'Maria', NULL);
    INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES
        ('M1', 'a@g.us', 'alice', 'hi', '2026-11-06 10:00:00+00:00', 0),
        ('MINE', 'a@g.us', '5599999999999', 'mine', '2026-11-06 10:01:00+00:00', 1),
        ('M1', 'b@g.us', 'alice', 'hi', '2026-11-06 10:00:00+00:00', 0),
        ('D1', '5511999990000@s.whatsapp.net', 'maria', 'hi', '2026-11-06 10:00:00+00:00', 0);"""
TABLE = """CREATE TABLE reactions (target_id TEXT, chat_jid TEXT, reactor TEXT, emoji TEXT, timestamp_ms INTEGER,
    PRIMARY KEY (target_id, chat_jid, reactor));"""


def make(tmp_path, monkeypatch, with_table=True):
    path = str(tmp_path / "messages.db")
    c = sqlite3.connect(path)
    c.executescript(SCHEMA + (TABLE if with_table else ""))
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    return c


@pytest.fixture
def conn(tmp_path, monkeypatch):
    return make(tmp_path, monkeypatch)


def react(conn, reactor, emoji, at=1000, target="M1", chat=A):
    conn.execute("INSERT OR REPLACE INTO reactions VALUES (?,?,?,?,?)", (target, chat, reactor, emoji, at))
    conn.commit()


def test_lists_the_current_reactions_with_counts(conn):  # REACT-08
    react(conn, "5511111111111", "👍", at=1_794_000_000_000)
    react(conn, "5522222222222", "👍", at=1_794_000_001_000)
    react(conn, "5533333333333", "❤️", at=1_794_000_002_000)

    out = whatsapp.get_message_reactions("M1", A)

    assert out["success"] is True and out["message_id"] == "M1" and out["chat_jid"] == A and out["chat_name"] == "Project"
    assert out["total"] == 3
    assert [(r["reactor"], r["emoji"]) for r in out["reactions"]] == [
        ("5511111111111", "👍"), ("5522222222222", "👍"), ("5533333333333", "❤️")]
    assert out["counts"] == {"👍": 2, "❤️": 1}
    assert out["reactions"][0]["timestamp"] == "2026-11-06T21:20:00+00:00"
    assert all(r["reactor_name"] for r in out["reactions"])


def test_a_retracted_reaction_does_not_count(conn):  # REACT-08
    react(conn, "5511111111111", "👍")
    react(conn, "5522222222222", "")

    out = whatsapp.get_message_reactions("M1", A)

    assert out["total"] == 1 and out["counts"] == {"👍": 1}


def test_our_own_reaction_is_named_me(conn):  # REACT-08
    react(conn, "5599999999999", "🙏")
    out = whatsapp.get_message_reactions("MINE", A)
    assert out["total"] == 0  # reactions are per message: this one was on M1

    react(conn, "5599999999999", "🙏", target="MINE")
    assert whatsapp.get_message_reactions("MINE", A)["reactions"][0]["reactor_name"] == "Me"


def test_a_message_without_reactions_has_empty_lists(conn):  # REACT-08
    out = whatsapp.get_message_reactions("M1", A)
    assert out["success"] is True and out["reactions"] == [] and out["counts"] == {} and out["total"] == 0


def test_unknown_message_is_not_found(conn):  # REACT-08
    out = whatsapp.get_message_reactions("NOPE", A)
    assert out["success"] is False and out["error_code"] == "MESSAGE_NOT_FOUND"


def test_the_same_id_in_two_chats_is_ambiguous_without_chat_jid(conn):  # REACT-08
    react(conn, "5511111111111", "👍", chat=A)
    react(conn, "5522222222222", "❤️", chat=B)

    out = whatsapp.get_message_reactions("M1")
    assert out["success"] is False and out["error_code"] == "AMBIGUOUS_MESSAGE"
    assert whatsapp.get_message_reactions("M1", B)["counts"] == {"❤️": 1}


def test_unique_id_is_found_without_chat_jid(conn):  # REACT-08
    react(conn, "5511111111111", "👍", target="D1", chat="5511999990000@s.whatsapp.net")
    out = whatsapp.get_message_reactions("D1")
    assert out["success"] is True and out["chat_jid"] == "5511999990000@s.whatsapp.net" and out["counts"] == {"👍": 1}


def test_direct_chat_is_found_under_the_other_addressing(conn):  # REACT-08
    react(conn, "5511111111111", "👍", target="D1", chat="5511999990000@s.whatsapp.net")
    out = whatsapp.get_message_reactions("D1", "5511999990000@lid")
    assert out["success"] is True and out["counts"] == {"👍": 1}


def test_database_without_the_reactions_table(tmp_path, monkeypatch):  # REACT-08 (old database)
    make(tmp_path, monkeypatch, with_table=False)
    out = whatsapp.get_message_reactions("M1", A)
    assert out["success"] is True and out["reactions"] == [] and out["total"] == 0


def test_message_id_is_required(conn):  # REACT-08
    assert whatsapp.get_message_reactions("")["success"] is False
