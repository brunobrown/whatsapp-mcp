"""Tests for send_poll, vote_poll and delete_message: validation, send policy, dry_run and audit."""
import json
import os
import sqlite3
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import send_policy  # noqa: E402
import whatsapp  # noqa: E402

GROUP = "120363222@g.us"
BLOCKED = "120363111@g.us"
OK_BODY = {"success": True, "message": "Poll sent", "message_id": "POLL1",
           "timestamp": "2026-11-06T10:30:00-03:00", "chat_jid": GROUP}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);"
        "CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,"
        " is_from_me BOOLEAN, media_type TEXT, PRIMARY KEY (id, chat_jid));"
        "CREATE TABLE polls (id TEXT, chat_jid TEXT, author TEXT, question TEXT, options_json TEXT,"
        " selectable_count INTEGER, allow_add_option BOOLEAN, hide_voter_names BOOLEAN, PRIMARY KEY (id, chat_jid));"
        "CREATE TABLE poll_votes (poll_id TEXT, chat_jid TEXT, voter TEXT, hashes_json TEXT, timestamp_ms INTEGER,"
        " PRIMARY KEY (poll_id, chat_jid, voter));"
    )
    conn.executemany("INSERT INTO chats VALUES (?, ?, NULL)", [(GROUP, "Test"), (BLOCKED, "Client")])
    conn.executemany(
        "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?, ?, ?, ?, '2026-11-06 10:00:00', ?)",
        [("POLL1", GROUP, "alice", "[poll] Lunch?", 0), ("MYPOLL", GROUP, "551100000001", "[poll] Ours", 1),
         ("TXT", GROUP, "alice", "hi", 0), ("MINE", GROUP, "551100000001", "my text", 1)])
    conn.executemany("INSERT INTO polls VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                     [("POLL1", GROUP, "alice", "Lunch?", json.dumps(["Yes", "No", "Maybe"]), 0),
                      ("MYPOLL", GROUP, "551100000001", "Ours", json.dumps(["Yes", "No"]), 1)])
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    return path


@pytest.fixture
def post():
    resp = mock.Mock(status_code=200, text="ok")
    resp.json.return_value = OK_BODY
    with mock.patch("whatsapp.requests.post", return_value=resp) as p:
        yield p


def sent(post):
    return post.call_args.kwargs["json"]


def audit_lines():
    if not os.path.exists(send_policy.AUDIT_PATH):
        return []
    return [json.loads(line) for line in open(send_policy.AUDIT_PATH)]


def block(chat):
    with open(send_policy.POLICY_PATH, "w") as f:
        json.dump({"mode": "denylist", "deny": [chat]}, f)


def test_send_poll_posts_the_poll_to_the_bridge(db, post):  # SEND-01
    out = whatsapp.send_poll("Test", "Lunch?", ["Yes", "No"], allow_multiple=False,
                             allow_add_option=True, hide_voter_names=True)

    assert out.success is True and out.message_id == "POLL1" and out.chat_jid == GROUP
    assert post.call_args.args[0].endswith("/send_poll")
    assert sent(post) == {"recipient": GROUP, "question": "Lunch?", "options": ["Yes", "No"],
                          "allow_multiple": False, "allow_add_option": True, "hide_voter_names": True}


def test_send_poll_defaults(db, post):  # SEND-01, SEND-03
    whatsapp.send_poll(GROUP, "Q", ["A", "B"])
    body = sent(post)
    assert body["allow_multiple"] is True and body["allow_add_option"] is False and body["hide_voter_names"] is False


@pytest.mark.parametrize("question, options", [
    ("", ["A", "B"]),
    ("   ", ["A", "B"]),
    ("q" * 256, ["A", "B"]),
    ("Q", ["A"]),
    ("Q", []),
    ("Q", [str(n) for n in range(13)]),
    ("Q", ["A", ""]),
    ("Q", ["A", "x" * 101]),
    ("Q", ["Yes", "yes"]),
    ("Q", ["A", 3]),
    ("Q", "AB"),
])
def test_invalid_polls_are_refused_before_anything_is_sent(db, post, question, options):  # SEND-04
    out = whatsapp.send_poll(GROUP, question, options)

    assert out.success is False and out.error_code == "INVALID_POLL"
    post.assert_not_called()
    assert audit_lines() == []


def test_the_limits_themselves_are_accepted(db, post):  # SEND-04
    out = whatsapp.send_poll(GROUP, "q" * 255, ["x" * 100] + [str(n) for n in range(11)])
    assert out.success is True


def test_send_poll_without_recipient(db, post):  # SEND-04
    out = whatsapp.send_poll("", "Q", ["A", "B"])
    assert out.success is False
    post.assert_not_called()


def test_dry_run_returns_the_preview_and_touches_nothing(db, post):  # SEND-05
    out = whatsapp.send_poll("Test", "Lunch?", ["Yes", "No"], allow_multiple=False, dry_run=True)

    post.assert_not_called()
    assert out.success is True
    assert out.preview == {"question": "Lunch?", "options": ["Yes", "No"], "allow_multiple": False,
                           "allow_add_option": False, "hide_voter_names": False,
                           "chat_jid": GROUP, "chat_name": "Test"}
    assert audit_lines() == []


def test_the_send_policy_blocks_a_poll(db, post):  # SEND-06
    block(BLOCKED)

    out = whatsapp.send_poll(BLOCKED, "Q", ["A", "B"])
    dry = whatsapp.send_poll(BLOCKED, "Q", ["A", "B"], dry_run=True)

    assert out.success is False and out.error_code == send_policy.BLOCKED
    assert dry.success is False and dry.error_code == send_policy.BLOCKED and dry.preview is None
    post.assert_not_called()
    assert [line["result"] for line in audit_lines()] == ["blocked"]


def test_a_real_poll_send_is_audited_without_the_text(db, post):  # SEND-06
    whatsapp.send_poll(GROUP, "Secret lunch?", ["Yes", "No"])

    [line] = audit_lines()
    assert line["kind"] == "poll" and line["result"] == "sent" and line["message_id"] == "POLL1"
    assert "Secret" not in json.dumps(line) and "Yes" not in json.dumps(line)


def test_vote_poll_posts_the_options_with_the_polls_own_spelling(db, post):  # VOTE-01
    out = whatsapp.vote_poll("Test", "POLL1", ["yes", "MAYBE"])

    assert out.success is True
    assert post.call_args.args[0].endswith("/vote_poll")
    assert sent(post) == {"chat_jid": GROUP, "message_id": "POLL1", "options": ["Yes", "Maybe"]}


def test_vote_poll_with_no_options_retracts_the_vote(db, post):  # VOTE-02
    out = whatsapp.vote_poll(GROUP, "POLL1", [])

    assert out.success is True and sent(post)["options"] == []


@pytest.mark.parametrize("poll, options", [
    ("POLL1", ["Never"]),
    ("POLL1", ["Yes", "yes"]),
    ("MYPOLL", ["Yes", "No"]),   # single answer accepts only one vote
    ("POLL1", "Yes"),
])
def test_invalid_votes_are_refused_before_anything_is_sent(db, post, poll, options):  # VOTE-03
    out = whatsapp.vote_poll(GROUP, poll, options)

    assert out.success is False and out.error_code == "INVALID_VOTE"
    post.assert_not_called()


def test_single_answer_poll_accepts_one_option(db, post):  # VOTE-03
    assert whatsapp.vote_poll(GROUP, "MYPOLL", ["No"]).success is True


@pytest.mark.parametrize("message_id", ["NOPE", "TXT"])
def test_vote_in_something_that_is_not_a_known_poll(db, post, message_id):  # VOTE-04
    out = whatsapp.vote_poll(GROUP, message_id, ["Yes"])

    assert out.success is False and out.error_code == "POLL_NOT_FOUND"
    post.assert_not_called()


def test_vote_dry_run_previews_without_touching_the_bridge(db, post):  # VOTE-06
    out = whatsapp.vote_poll("Test", "POLL1", ["yes"], dry_run=True)

    post.assert_not_called()
    assert out.success is True and out.preview == {
        "question": "Lunch?", "options": ["Yes"], "chat_jid": GROUP, "chat_name": "Test", "message_id": "POLL1"}
    assert audit_lines() == []


def test_vote_dry_run_still_validates_the_options(db, post):  # VOTE-06
    out = whatsapp.vote_poll(GROUP, "POLL1", ["Never"], dry_run=True)
    assert out.success is False and out.error_code == "INVALID_VOTE"


def test_the_send_policy_blocks_a_vote(db, post):  # VOTE-06
    block(GROUP)

    out = whatsapp.vote_poll(GROUP, "POLL1", ["Yes"])

    assert out.success is False and out.error_code == send_policy.BLOCKED
    post.assert_not_called()


def test_a_vote_is_audited_by_kind(db, post):  # VOTE-06
    whatsapp.vote_poll(GROUP, "POLL1", ["Yes"])

    [line] = audit_lines()
    assert line["kind"] == "poll_vote" and line["result"] == "sent"


def test_delete_message_posts_a_message_of_ours(db, post):  # DEL-01
    out = whatsapp.delete_message("Test", "MINE")

    assert out.success is True
    assert post.call_args.args[0].endswith("/delete_message")
    assert sent(post) == {"chat_jid": GROUP, "message_id": "MINE"}


def test_delete_message_can_delete_our_own_poll(db, post):  # DEL-01
    assert whatsapp.delete_message(GROUP, "MYPOLL").success is True


def test_delete_message_refuses_what_is_not_ours_or_unknown(db, post):  # DEL-02
    someone_elses = whatsapp.delete_message(GROUP, "TXT")
    unknown = whatsapp.delete_message(GROUP, "NOPE")

    assert someone_elses.success is False and someone_elses.error_code == "NOT_OWN_MESSAGE"
    assert unknown.success is False and unknown.error_code == "MESSAGE_NOT_FOUND"
    post.assert_not_called()


def test_delete_message_dry_run_previews_the_text_and_deletes_nothing(db, post):  # DEL-04
    out = whatsapp.delete_message("Test", "MINE", dry_run=True)

    post.assert_not_called()
    assert out.success is True and out.preview == {
        "message_id": "MINE", "chat_jid": GROUP, "chat_name": "Test", "text": "my text"}
    assert audit_lines() == []


def test_delete_message_dry_run_still_refuses_what_is_not_ours(db, post):  # DEL-04
    out = whatsapp.delete_message(GROUP, "TXT", dry_run=True)
    assert out.success is False and out.error_code == "NOT_OWN_MESSAGE"


def test_the_send_policy_blocks_a_delete(db, post):  # DEL-04
    block(GROUP)

    out = whatsapp.delete_message(GROUP, "MINE")

    assert out.success is False and out.error_code == send_policy.BLOCKED
    post.assert_not_called()


def test_a_delete_is_audited_by_kind_without_the_text(db, post):  # DEL-04
    whatsapp.delete_message(GROUP, "MINE")

    [line] = audit_lines()
    assert line["kind"] == "delete" and line["result"] == "sent" and "my text" not in json.dumps(line)


def test_quiz_answer_is_posted_and_makes_the_poll_single_answer(db, post):  # QUIZ-01, QUIZ-03
    out = whatsapp.send_poll("Test", "Capital?", ["Brasília", "Rio"], quiz_answer="brasília")
    assert out.success is True
    assert sent(post)["quiz_answer"] == "Brasília"
    assert sent(post)["allow_multiple"] is False


@pytest.mark.parametrize("answer", ["Sao Paulo", "", "  ", 3, ["A"]])
def test_a_quiz_answer_that_is_not_an_option_is_refused(db, post, answer):  # QUIZ-02
    out = whatsapp.send_poll(GROUP, "Q", ["A", "B"], quiz_answer=answer)
    assert out.success is False and out.error_code == "INVALID_POLL"
    post.assert_not_called()


def test_a_quiz_that_lets_people_add_options_is_refused(db, post):  # QUIZ-02
    out = whatsapp.send_poll(GROUP, "Q", ["A", "B"], quiz_answer="A", allow_add_option=True)
    assert out.error_code == "INVALID_POLL"
    post.assert_not_called()


def test_a_quiz_dry_run_shows_single_answer_and_the_answer(db, post):  # QUIZ-03
    out = whatsapp.send_poll(GROUP, "Q", ["A", "B"], allow_multiple=True, quiz_answer="b", dry_run=True)
    assert out.preview["allow_multiple"] is False and out.preview["quiz_answer"] == "B"
    post.assert_not_called()


@pytest.mark.parametrize("minutes", [1, 30, 10080])
def test_closes_in_minutes_is_posted(db, post, minutes):  # END-01
    assert whatsapp.send_poll(GROUP, "Q", ["A", "B"], closes_in_minutes=minutes).success is True
    assert sent(post)["closes_in_minutes"] == minutes


@pytest.mark.parametrize("minutes", [0, -5, 10081, 1.5, "30", True, []])
def test_a_bad_deadline_is_refused(db, post, minutes):  # END-02
    out = whatsapp.send_poll(GROUP, "Q", ["A", "B"], closes_in_minutes=minutes)
    assert out.success is False and out.error_code == "INVALID_POLL"
    post.assert_not_called()


def test_dry_run_shows_the_deadline(db, post):  # END-03
    out = whatsapp.send_poll(GROUP, "Q", ["A", "B"], closes_in_minutes=60, dry_run=True)
    assert out.preview["closes_in_minutes"] == 60 and "quiz_answer" not in out.preview
    post.assert_not_called()


def test_without_quiz_or_deadline_the_body_and_preview_have_no_new_keys(db, post):  # END-03, COMPAT-01
    whatsapp.send_poll(GROUP, "Q", ["A", "B"])
    assert "quiz_answer" not in sent(post) and "closes_in_minutes" not in sent(post)
    preview = whatsapp.send_poll(GROUP, "Q", ["A", "B"], dry_run=True).preview
    assert "quiz_answer" not in preview and "closes_in_minutes" not in preview


def test_the_tool_takes_quiz_answer_and_closes_in_minutes(db, post):  # QUIZ-01, END-01
    import asyncio
    import main
    schema = {t.name: t.input_schema for t in asyncio.run(main.mcp.list_tools())}["send_poll"]
    assert {"quiz_answer", "closes_in_minutes"} <= set(schema["properties"])
    assert not {"quiz_answer", "closes_in_minutes"} & set(schema["required"])


def test_the_tool_forwards_quiz_answer_and_closes_in_minutes_to_the_bridge(db, post):  # QUIZ-01, END-01
    import main
    out = main.send_poll(GROUP, "Q", ["Alpha", "Beta"], quiz_answer="beta", closes_in_minutes=5)
    assert out["success"] is True
    assert sent(post)["quiz_answer"] == "Beta" and sent(post)["closes_in_minutes"] == 5


def test_spaces_around_the_quiz_answer_are_ignored(db, post):  # QUIZ-01
    assert whatsapp.send_poll(GROUP, "Q", ["A", "B"], quiz_answer=" b ").success is True
    assert sent(post)["quiz_answer"] == "B"
