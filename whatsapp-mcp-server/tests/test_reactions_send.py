"""react_to_message: emoji validation, known message, dry_run, send policy and audit."""
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
OK_BODY = {"success": True, "message": "Reaction sent", "message_id": "R1", "chat_jid": GROUP}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);"
        "CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,"
        " is_from_me BOOLEAN, media_type TEXT, PRIMARY KEY (id, chat_jid));"
        f"INSERT INTO chats VALUES ('{GROUP}', 'Test', NULL);"
        f"INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES"
        f" ('M1', '{GROUP}', 'alice', 'good morning', '2026-11-06 10:00:00', 0);")
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)


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


def test_reaction_is_posted_to_the_bridge(db, post):  # REACT-01
    out = whatsapp.react_to_message("Test", "M1", "👍")

    assert out.success is True and out.message_id == "R1"
    assert post.call_args.args[0].endswith("/react")
    assert sent(post) == {"chat_jid": GROUP, "message_id": "M1", "emoji": "👍"}


def test_empty_emoji_takes_the_reaction_back(db, post):  # REACT-02
    out = whatsapp.react_to_message(GROUP, "M1", "")

    assert out.success is True and sent(post)["emoji"] == ""


@pytest.mark.parametrize("emoji", ["😀" * 17, "👍 👍", "👍\n", " "])
def test_invalid_emoji_is_refused_before_anything_is_sent(db, post, emoji):  # REACT-03
    out = whatsapp.react_to_message(GROUP, "M1", emoji)

    assert out.success is False and out.error_code == "INVALID_EMOJI"
    post.assert_not_called()
    assert audit_lines() == []


def test_sixteen_characters_are_accepted(db, post):  # REACT-03
    assert whatsapp.react_to_message(GROUP, "M1", "😀" * 16).success is True


def test_a_non_text_emoji_argument_is_refused(db, post):  # REACT-03
    out = whatsapp.react_to_message(GROUP, "M1", 5)
    assert out.success is False and out.error_code == "INVALID_EMOJI"


def test_unknown_message_is_refused_before_sending(db, post):  # REACT-04
    out = whatsapp.react_to_message(GROUP, "NOPE", "👍")

    assert out.success is False and out.error_code == "MESSAGE_NOT_FOUND"
    post.assert_not_called()


def test_dry_run_previews_without_touching_the_bridge(db, post):  # REACT-05
    out = whatsapp.react_to_message("Test", "M1", "👍", dry_run=True)

    post.assert_not_called()
    assert out.success is True and out.preview == {
        "message_id": "M1", "chat_jid": GROUP, "chat_name": "Test", "emoji": "👍", "text": "good morning"}
    assert audit_lines() == []


def test_dry_run_still_refuses_an_unknown_message(db, post):  # REACT-05
    out = whatsapp.react_to_message(GROUP, "NOPE", "👍", dry_run=True)
    assert out.success is False and out.error_code == "MESSAGE_NOT_FOUND"


def test_the_send_policy_blocks_a_reaction(db, post):  # REACT-05
    with open(send_policy.POLICY_PATH, "w") as f:
        json.dump({"mode": "denylist", "deny": [GROUP]}, f)

    out = whatsapp.react_to_message(GROUP, "M1", "👍")

    assert out.success is False and out.error_code == send_policy.BLOCKED
    post.assert_not_called()
    assert [line["result"] for line in audit_lines()] == ["blocked"]


def test_a_reaction_is_audited_by_kind_without_the_text(db, post):  # REACT-05
    whatsapp.react_to_message(GROUP, "M1", "👍")

    [line] = audit_lines()
    assert line["kind"] == "reaction" and line["result"] == "sent" and "good morning" not in json.dumps(line)


def test_missing_arguments(db, post):  # REACT-01
    assert whatsapp.react_to_message("", "M1", "👍").success is False
    assert whatsapp.react_to_message(GROUP, "", "👍").success is False
    post.assert_not_called()


def test_react_to_message_answers_like_the_other_send_tools(db, post):  # REACT-01, REACT-05
    import main
    sent_ok = main.react_to_message(GROUP, "M1", "👍")
    dry = main.react_to_message(GROUP, "M1", "👍", dry_run=True)
    bad = main.react_to_message(GROUP, "M1", "👍 👍")

    assert sent_ok["success"] is True and sent_ok["message_id"] == "R1"
    assert dry["dry_run"] is True and dry["preview"]["emoji"] == "👍"
    assert bad["success"] is False and bad["error_code"] == "INVALID_EMOJI"
    assert post.call_count == 1
