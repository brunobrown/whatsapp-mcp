"""Testes de get_poll_results e list_polls: contagem de votos, anonimato, erros e banco antigo."""
import hashlib
import json
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
        is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB,
        file_enc_sha256 BLOB, file_length INTEGER, local_path TEXT, mimetype TEXT, forwarded BOOLEAN,
        quoted_id TEXT, PRIMARY KEY (id, chat_jid));
    INSERT INTO chats VALUES ('a@g.us', 'Projeto Alfa', NULL), ('b@g.us', 'Família', NULL);"""
POLL_TABLES = """
    CREATE TABLE polls (id TEXT, chat_jid TEXT, author TEXT, question TEXT, options_json TEXT,
        selectable_count INTEGER, allow_add_option BOOLEAN, hide_voter_names BOOLEAN, PRIMARY KEY (id, chat_jid));
    CREATE TABLE poll_votes (poll_id TEXT, chat_jid TEXT, voter TEXT, hashes_json TEXT, timestamp_ms INTEGER,
        PRIMARY KEY (poll_id, chat_jid, voter));"""


def h(name):
    return hashlib.sha256(name.encode()).hexdigest()


def make(tmp_path, monkeypatch, tables=POLL_TABLES):
    path = str(tmp_path / "messages.db")
    c = sqlite3.connect(path)
    c.executescript(SCHEMA + tables)
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    return c


@pytest.fixture
def conn(tmp_path, monkeypatch):
    return make(tmp_path, monkeypatch)


def add_poll(conn, id="P1", chat=A, question="Almoço?", options=("Sim", "Não", "Talvez"), selectable=0,
             author="alice", add_option=0, hide=0, minute=0, from_me=0):
    conn.execute("INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?,?,?,?,?,?)",
                 (id, chat, author, "[poll] " + question, f"2026-11-06 10:{minute:02d}:00+00:00", from_me))
    conn.execute("INSERT INTO polls VALUES (?,?,?,?,?,?,?,?)",
                 (id, chat, author, question, json.dumps(list(options)), selectable, add_option, hide))
    conn.commit()


def vote(conn, voter, names, poll="P1", chat=A, at=1000, raw=None):
    hashes = raw if raw is not None else [h(n) for n in names]
    conn.execute("INSERT OR REPLACE INTO poll_votes VALUES (?,?,?,?,?)", (poll, chat, voter, json.dumps(hashes), at))
    conn.commit()


def opt(out, text):
    return next(o for o in out["options"] if o["text"] == text)


def test_results_count_people_percent_and_voter_names(conn):  # RES-01, RES-02
    add_poll(conn)
    vote(conn, "5511111111111", ["Sim"], at=1)
    vote(conn, "5522222222222", ["Sim", "Talvez"], at=2)
    vote(conn, "5533333333333", ["Não"], at=3)

    out = whatsapp.get_poll_results("P1", A)

    assert out["success"] is True and out["message_id"] == "P1" and out["chat_jid"] == A
    assert out["chat_name"] == "Projeto Alfa" and out["question"] == "Almoço?"
    assert out["allow_multiple"] is True and out["allow_add_option"] is False and out["anonymous"] is False
    assert out["created_at"].startswith("2026-11-06T10:00:00") and out["created_by"] == "alice"
    assert [o["text"] for o in out["options"]] == ["Sim", "Não", "Talvez"]
    assert out["total_voters"] == 3 and out["unknown_votes"] == 0
    assert (opt(out, "Sim")["votes"], opt(out, "Sim")["percent"]) == (2, 66.7)
    assert (opt(out, "Não")["votes"], opt(out, "Não")["percent"]) == (1, 33.3)
    assert (opt(out, "Talvez")["votes"], opt(out, "Talvez")["percent"]) == (1, 33.3)
    assert opt(out, "Sim")["voters"] == ["5511111111111", "5522222222222"]  # in the order they voted


def test_single_answer_poll_reports_allow_multiple_false(conn):  # RES-01
    add_poll(conn, selectable=1)
    assert whatsapp.get_poll_results("P1", A)["allow_multiple"] is False


def test_anonymous_poll_counts_but_names_nobody(conn):  # RES-03
    add_poll(conn, hide=1)
    vote(conn, "5511111111111", ["Sim"])
    vote(conn, "5522222222222", ["Sim"], at=2)

    out = whatsapp.get_poll_results("P1", A)

    assert out["anonymous"] is True and out["total_voters"] == 2
    assert opt(out, "Sim")["votes"] == 2
    assert all(o["voters"] == [] for o in out["options"])


def test_retracted_vote_does_not_count(conn):  # RES-04
    add_poll(conn)
    vote(conn, "5511111111111", ["Sim"], at=1)
    vote(conn, "5522222222222", [], at=2)

    out = whatsapp.get_poll_results("P1", A)

    assert out["total_voters"] == 1
    assert all(o["votes"] == (1 if o["text"] == "Sim" else 0) for o in out["options"])
    assert opt(out, "Sim")["percent"] == 100.0


def test_poll_without_votes_has_zero_percent(conn):  # RES-02
    add_poll(conn)
    out = whatsapp.get_poll_results("P1", A)
    assert out["total_voters"] == 0 and all(o["votes"] == 0 and o["percent"] == 0.0 for o in out["options"])


def test_vote_for_an_unknown_option_is_counted_apart(conn):  # spec: unknown_votes
    add_poll(conn)
    vote(conn, "5511111111111", [], raw=[h("Opção nova")])
    vote(conn, "5522222222222", ["Sim"], at=2)

    out = whatsapp.get_poll_results("P1", A)

    assert out["unknown_votes"] == 1 and out["total_voters"] == 2
    assert opt(out, "Sim")["votes"] == 1


def test_our_own_vote_is_named_me(conn):  # RES-02
    add_poll(conn, author="5599999999999", from_me=1)
    vote(conn, "5599999999999", ["Sim"])
    assert opt(whatsapp.get_poll_results("P1", A), "Sim")["voters"] == ["Me"]


def test_poll_created_by_us_says_me(conn):  # RES-01
    add_poll(conn, author="5599999999999", from_me=1)
    assert whatsapp.get_poll_results("P1", A)["created_by"] == "Me"


def test_a_message_that_is_not_a_poll_is_not_found(conn):  # RES-05
    conn.execute("INSERT INTO messages (id, chat_jid, sender, content, timestamp) VALUES ('T1', ?, 'alice', 'oi', '2026-11-06 10:00:00+00:00')", (A,))
    conn.commit()
    out = whatsapp.get_poll_results("T1", A)
    assert out["success"] is False and out["error_code"] == "POLL_NOT_FOUND"


def test_same_id_in_two_chats_is_ambiguous_without_chat_jid(conn):  # RES-05
    add_poll(conn, chat=A)
    add_poll(conn, chat=B)

    out = whatsapp.get_poll_results("P1")
    assert out["success"] is False and out["error_code"] == "AMBIGUOUS_MESSAGE"
    assert whatsapp.get_poll_results("P1", B)["chat_jid"] == B


def test_poll_is_found_without_chat_jid_when_unique(conn):  # RES-05
    add_poll(conn)
    assert whatsapp.get_poll_results("P1")["chat_jid"] == A


def test_direct_chat_poll_is_found_under_the_other_addressing(conn):  # EDGE-01
    conn.execute("INSERT INTO chats VALUES ('5511999990000@s.whatsapp.net', 'Carol', NULL)")
    add_poll(conn, chat="5511999990000@s.whatsapp.net")
    out = whatsapp.get_poll_results("P1", "5511999990000@lid")
    assert out["success"] is True and out["chat_jid"] == "5511999990000@s.whatsapp.net"


def test_vote_stored_before_the_poll_is_counted_once_it_exists(conn):  # EDGE-02
    vote(conn, "5511111111111", ["Sim"])
    add_poll(conn)
    assert opt(whatsapp.get_poll_results("P1", A), "Sim")["votes"] == 1


def test_database_without_poll_tables(tmp_path, monkeypatch):  # banco antigo
    make(tmp_path, monkeypatch, tables="")
    out = whatsapp.get_poll_results("P1", A)
    assert out["success"] is False and out["error_code"] == "POLL_NOT_FOUND"
    listed = whatsapp.list_polls(A)
    assert listed["success"] is True and listed["polls"] == [] and listed["total"] == 0


def test_list_polls_newest_first_with_counts_and_paging(conn):  # LIST-01
    add_poll(conn, id="OLD", question="Antiga", minute=1)
    add_poll(conn, id="MID", question="Do meio", minute=2)
    add_poll(conn, id="NEW", question="Nova", minute=3)
    add_poll(conn, id="ELSEWHERE", chat=B, minute=4)
    vote(conn, "5511111111111", ["Sim"], poll="NEW")
    vote(conn, "5522222222222", ["Sim"], poll="NEW", at=2)
    vote(conn, "5533333333333", ["Não"], poll="NEW", at=3)

    first = whatsapp.list_polls(A, limit=2)

    assert first["success"] is True and first["total"] == 3 and first["has_more"] is True
    assert [p["message_id"] for p in first["polls"]] == ["NEW", "MID"]
    newest = first["polls"][0]
    assert newest["question"] == "Nova" and newest["total_voters"] == 3
    assert newest["created_at"].startswith("2026-11-06T10:03:00")
    assert newest["options"] == [{"text": "Sim", "votes": 2}, {"text": "Não", "votes": 1}, {"text": "Talvez", "votes": 0}]
    second = whatsapp.list_polls(A, limit=2, page=1)
    assert [p["message_id"] for p in second["polls"]] == ["OLD"] and second["has_more"] is False


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": -1}, {"page": -1}])
def test_list_polls_rejects_bad_paging(conn, kwargs):  # LIST-02
    out = whatsapp.list_polls(A, **kwargs)
    assert out["success"] is False and out["error_code"] == "INVALID_ARGUMENT"


def test_list_polls_orders_by_message_time_not_by_insertion(conn):  # LIST-01
    add_poll(conn, id="LATE", question="Mais nova", minute=9)    # inserida primeiro
    add_poll(conn, id="EARLY", question="Mais antiga", minute=1)
    add_poll(conn, id="MIDDLE", question="Do meio", minute=5)

    out = whatsapp.list_polls(A)

    assert [p["message_id"] for p in out["polls"]] == ["LATE", "MIDDLE", "EARLY"]


def test_voters_are_listed_in_the_order_they_voted_not_by_id(conn):  # RES-02
    add_poll(conn)
    vote(conn, "9999", ["Sim"], at=1)   # votou primeiro, mas o id é o maior
    vote(conn, "1111", ["Sim"], at=2)
    vote(conn, "5555", ["Sim"], at=3)

    assert opt(whatsapp.get_poll_results("P1", A), "Sim")["voters"] == ["9999", "1111", "5555"]


def test_a_vote_for_an_added_option_moves_from_unknown_to_that_option(conn):  # ED-05
    add_poll(conn, options=("Sim", "Não"))
    vote(conn, "5511111111111", [], raw=[h("Talvez")])   # voted for an option added after the poll
    vote(conn, "5522222222222", ["Sim"], at=2)

    before = whatsapp.get_poll_results("P1", A)
    assert before["unknown_votes"] == 1 and [o["text"] for o in before["options"]] == ["Sim", "Não"]

    conn.execute("UPDATE polls SET options_json = ? WHERE id = 'P1'", (json.dumps(["Sim", "Não", "Talvez"]),))
    conn.commit()
    after = whatsapp.get_poll_results("P1", A)

    assert after["unknown_votes"] == 0 and after["total_voters"] == 2
    assert (opt(after, "Talvez")["votes"], opt(after, "Talvez")["voters"]) == (1, ["5511111111111"])
