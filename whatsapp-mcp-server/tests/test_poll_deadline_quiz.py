"""Reading back the deadline and the right answer of a poll (POLLX-*)."""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import export  # noqa: E402
import whatsapp  # noqa: E402
from test_polls_read import A, POLL_TABLES, SCHEMA, make, vote  # noqa: E402,F401
import test_polls_read  # noqa: E402

NEW_TABLES = POLL_TABLES.replace("hide_voter_names BOOLEAN,", "hide_voter_names BOOLEAN, ends_at_ms INTEGER, quiz BOOLEAN, "
                                 "correct_answer TEXT, closed_notified BOOLEAN,")


def add_poll(conn, id="P1", chat=A, question="Lunch?", options=("Yes", "No", "Maybe"), author="alice", from_me=0):
    """Like test_polls_read.add_poll, for a table with more columns."""
    conn.execute("INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?,?,?,?,?,?)",
                 (id, chat, author, "[poll] " + question, "2026-11-06 10:00:00+00:00", from_me))
    conn.execute("INSERT INTO polls (id, chat_jid, author, question, options_json, selectable_count, allow_add_option, "
                 "hide_voter_names) VALUES (?,?,?,?,?,?,?,?)", (id, chat, author, question, json.dumps(list(options)), 0, 0, 0))
    conn.commit()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    return make(tmp_path, monkeypatch, NEW_TABLES)


def extras(conn, id="P1", ends_ms=None, quiz=0, correct=None):
    conn.execute("UPDATE polls SET ends_at_ms = ?, quiz = ?, correct_answer = ? WHERE id = ?", (ends_ms, quiz, correct, id))
    conn.commit()


def now_ms():
    return int(time.time() * 1000)


def test_the_deadline_is_read_back_and_closed_follows_the_clock(conn):  # POLLX-07
    add_poll(conn, "OPEN")
    add_poll(conn, "DONE")
    add_poll(conn, "NONE")
    extras(conn, "OPEN", ends_ms=now_ms() + 3_600_000)
    extras(conn, "DONE", ends_ms=1_700_000_000_000)

    open_poll, done, none = (whatsapp.get_poll_results(i, A) for i in ("OPEN", "DONE", "NONE"))

    assert (open_poll["closed"], done["closed"], none["closed"]) == (False, True, False)
    assert done["closes_at"] == "2023-11-14T22:13:20+00:00" and none["closes_at"] is None
    assert open_poll["closes_at"].endswith("+00:00")


def test_the_right_answer_of_our_own_quiz_is_shown_and_marked_on_the_option(conn):  # POLLX-08
    add_poll(conn, "Q1", from_me=1, author="me-id", options=("Brasília", "Rio"))
    extras(conn, "Q1", quiz=1, correct="Brasília")
    vote(conn, "alice", ["Rio"], poll="Q1")

    out = whatsapp.get_poll_results("Q1", A)

    assert out["quiz"] is True and out["correct_answer"] == "Brasília"
    assert [(o["text"], o["correct"]) for o in out["options"]] == [("Brasília", True), ("Rio", False)]


def test_the_answer_of_a_quiz_by_someone_else_is_not_shown(conn):  # POLLX-08
    add_poll(conn, "Q2", from_me=0, options=("Brasília", "Rio"))
    extras(conn, "Q2", quiz=1, correct="Brasília")

    out = whatsapp.get_poll_results("Q2", A)

    assert out["quiz"] is True and out["correct_answer"] is None
    assert all("correct" not in o for o in out["options"])


def test_a_poll_that_is_not_a_quiz_has_no_answer(conn):  # POLLX-08
    add_poll(conn, "P1", from_me=1)
    extras(conn, "P1", quiz=0, correct="Yes")        # a stray value never shows without the quiz flag

    out = whatsapp.get_poll_results("P1", A)

    assert out["quiz"] is False and out["correct_answer"] is None and all("correct" not in o for o in out["options"])


def test_a_database_from_an_older_bridge_still_reads(tmp_path, monkeypatch):  # POLLX-09
    make(tmp_path, monkeypatch, POLL_TABLES)         # no deadline, quiz or answer columns
    c = __import__("sqlite3").connect(whatsapp.MESSAGES_DB_PATH)
    test_polls_read.add_poll(c, "P1")

    out = whatsapp.get_poll_results("P1", A)

    assert out["success"] and (out["closes_at"], out["closed"], out["quiz"], out["correct_answer"]) == (None, False, False, None)
    assert whatsapp.list_polls(A)["polls"][0]["message_id"] == "P1"


def test_the_export_masks_the_right_answer_when_anonymizing(conn):  # POLLX-10
    add_poll(conn, "Q1", from_me=1, author="me-id", options=("Call +5511987654321", "Rio"))
    extras(conn, "Q1", quiz=1, correct="Call +5511987654321")

    out = export.export_conversation(A, anonymize=True)

    (poll,) = out["polls"]
    assert poll["correct_answer"] == "Call [number]" and "5511987654321" not in json.dumps(out)


@pytest.mark.parametrize("ends", [2**62, -2**62, 9223372036854775807, -1, 0, 1000, "soon", 1e300])
def test_an_absurd_deadline_does_not_break_reading_the_chat(conn, ends):  # POLLX-12
    add_poll(conn, "BAD")
    add_poll(conn, "GOOD")
    extras(conn, "BAD", ends_ms=ends)
    extras(conn, "GOOD", ends_ms=1_700_000_000_000)

    bad, listing = whatsapp.get_poll_results("BAD", A), whatsapp.list_polls(A)

    assert bad["success"] is True and bad["closes_at"] is None and bad["closed"] is False
    assert listing["success"] is True and {p["message_id"] for p in listing["polls"]} == {"BAD", "GOOD"}
    assert export.export_conversation(A)["success"] is True
