"""P1-9 tests: real mentions, replies (reply_to) and dry_run in send_message."""
import json
import os
import sqlite3
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import send_policy  # noqa: E402
import whatsapp  # noqa: E402

GROUP = "120363222@g.us"
CLIENT = "120363111@g.us"
DM = "551188880101@s.whatsapp.net"
OK_BODY = {"success": True, "message": "Message sent", "message_id": "ID1",
           "timestamp": "2026-10-21T10:30:00-03:00", "chat_jid": GROUP}


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);"
        "CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,"
        " is_from_me BOOLEAN, media_type TEXT, PRIMARY KEY (id, chat_jid));"
    )
    conn.executemany("INSERT INTO chats VALUES (?, ?, NULL)",
                     [(GROUP, "Project Alpha"), (CLIENT, "Client Beta"), (DM, "Alex")])
    conn.executemany(
        "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?, ?, ?, ?, '2026-10-21 10:00:00', ?)",
        [("Q1", GROUP, "100000000000001", "can you review the proposal?", 0),
         ("MINE", GROUP, "551100000001", "reviewing", 1),
         ("OTHER", CLIENT, "100000000000001", "other chat", 0),
         ("DMQ", DM, "551188880101", "hi from dm", 0)],
    )
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


def test_phone_mention_becomes_a_real_mention(db, post):  # MENT-01, MENT-07
    main.send_message(GROUP, "*Notice* @+55117777-0202 review,\n> quote _now_")

    body = sent(post)
    assert body["message"] == "*Notice* @551177770202 review,\n> quote _now_"
    assert body["mentions"] == ["551177770202@s.whatsapp.net"]


@pytest.mark.parametrize("token, digits, jid", [
    ("@+55 51", None, None),   # space is not part of the token: nothing is converted
    ("@551188880101", "551188880101", "551188880101@s.whatsapp.net"),
    ("@100000000000001", "100000000000001", "100000000000001@lid"),
    ("@+55(11)98888-0101", "5511988880101", "5511988880101@s.whatsapp.net"),
])
def test_mention_token_forms(db, post, token, digits, jid):  # MENT-01
    main.send_message(GROUP, f"hi {token}!")

    body = sent(post)
    if digits is None:
        assert "mentions" not in body
        return
    assert body["message"] == f"hi @{digits}!"
    assert body["mentions"] == [jid]


@pytest.mark.parametrize("text", ["year @2024 was good", "mail a@b.com", "@+55 no number", "price @ 100", "id@5551"])
def test_text_that_is_not_a_mention_is_untouched(db, post, text):  # MENT-03
    main.send_message(GROUP, text)

    assert sent(post)["message"] == text
    assert "mentions" not in sent(post)


def test_plain_text_sends_exactly_as_before(db, post):  # MENT-03
    main.send_message(GROUP, "*hi* _all_ good")

    assert sent(post) == {"recipient": GROUP, "message": "*hi* _all_ good"}


def test_same_person_twice_is_one_mention(db, post):  # MENT-01
    main.send_message(GROUP, "@+551188880101 and again @551188880101")

    assert sent(post)["mentions"] == ["551188880101@s.whatsapp.net"]
    assert sent(post)["message"] == "@551188880101 and again @551188880101"


def test_explicit_mentions_are_added_without_duplicates(db, post):  # MENT-02
    main.send_message(GROUP, "hi @551188880101", mentions=[
        "551188880101", "100000000000001@lid", "100000000000001", "+55 (11) 99999-0000",
    ])

    assert sent(post)["mentions"] == [
        "551188880101@s.whatsapp.net", "100000000000001@lid", "5511999990000@s.whatsapp.net",
    ]


def test_invalid_explicit_mention_is_refused(db, post):  # MENT-02
    out = main.send_message(GROUP, "hi", mentions=["Alex"])

    assert out["success"] is False and out["error_code"] == "INVALID_MENTION"
    post.assert_not_called()


def test_reply_to_is_sent_to_the_bridge(db, post):  # REPLY-01
    main.send_message(GROUP, "reviewed", reply_to="Q1")

    assert sent(post)["reply_to"] == "Q1"


def test_reply_to_a_message_of_another_chat_is_refused(db, post):  # REPLY-02
    out = main.send_message(GROUP, "hi", reply_to="OTHER")

    assert out["success"] is False and out["error_code"] == "REPLY_TARGET_NOT_FOUND"
    post.assert_not_called()


def test_reply_to_unknown_message_is_refused(db, post):  # REPLY-02
    out = main.send_message(GROUP, "hi", reply_to="DOESNOTEXIST")

    assert out["success"] is False and out["error_code"] == "REPLY_TARGET_NOT_FOUND"
    post.assert_not_called()


def test_reply_in_a_direct_chat_found_by_phone(db, post):  # REPLY-01
    main.send_message("551188880101", "hi", reply_to="DMQ")

    assert sent(post)["reply_to"] == "DMQ"


def test_dry_run_does_not_touch_the_bridge(db, post):  # DRY-01
    out = main.send_message("Project Alpha", "*Notice* @+551188880101", reply_to="Q1", dry_run=True)

    post.assert_not_called()
    assert out["success"] is True and out["dry_run"] is True
    assert out["preview"] == {
        "text": "*Notice* @551188880101",
        "chat_jid": GROUP,
        "chat_name": "Project Alpha",
        "mentions": [{"token": "551188880101", "jid": "551188880101@s.whatsapp.net"}],
        "reply_to": {"id": "Q1", "sender": "100000000000001", "text": "can you review the proposal?"},
    }
    assert "message_id" not in out


def test_dry_run_without_mentions_or_reply(db, post):  # DRY-01
    out = main.send_message(GROUP, "hi", dry_run=True)

    assert out["preview"]["mentions"] == [] and out["preview"]["reply_to"] is None


def test_dry_run_shows_the_policy_block(db, post, tmp_path):  # DRY-02
    with open(send_policy.POLICY_PATH, "w") as f:
        json.dump({"mode": "denylist", "deny": [CLIENT]}, f)

    out = main.send_message(CLIENT, "hi", dry_run=True)

    assert out["success"] is False and out["error_code"] == "SEND_BLOCKED_BY_POLICY"
    assert "preview" not in out
    post.assert_not_called()


def test_dry_run_is_not_audited(db, post):  # DRY-03
    main.send_message(GROUP, "hi", dry_run=True)

    assert not os.path.exists(send_policy.AUDIT_PATH)


def test_dry_run_with_unknown_reply_target(db, post):  # DRY-04
    out = main.send_message(GROUP, "hi", reply_to="DOESNOTEXIST", dry_run=True)

    assert out["success"] is False and out["error_code"] == "REPLY_TARGET_NOT_FOUND"


def test_real_send_with_mentions_is_audited_by_size_of_the_final_text(db, post):  # compatibility with P1-8
    main.send_message(GROUP, "@+551188880101 hello")

    [line] = [json.loads(l) for l in open(send_policy.AUDIT_PATH)]
    assert line["result"] == "sent" and "hello" not in json.dumps(line) and line["size"] == len("@551188880101 hello")


def test_dry_run_blocked_or_unresolved_is_not_audited(db, post):  # DRY-03
    with open(send_policy.POLICY_PATH, "w") as f:
        json.dump({"mode": "denylist", "deny": [CLIENT]}, f)

    blocked = main.send_message(CLIENT, "hi", dry_run=True)
    missing = main.send_message("Chat that does not exist", "hi", dry_run=True)

    assert blocked["error_code"] == "SEND_BLOCKED_BY_POLICY" and missing["error_code"] == "RECIPIENT_NOT_FOUND"
    assert not os.path.exists(send_policy.AUDIT_PATH)


def test_real_blocked_send_is_still_audited(db, post):  # DRY-03 regression
    with open(send_policy.POLICY_PATH, "w") as f:
        json.dump({"mode": "denylist", "deny": [CLIENT]}, f)

    main.send_message(CLIENT, "hi")

    assert [json.loads(l)["result"] for l in open(send_policy.AUDIT_PATH)] == ["blocked"]


def test_too_many_mentions_are_refused(db, post):  # MENT-08
    text = " ".join(f"@55118888{n:04d}" for n in range(51))

    out = main.send_message(GROUP, text)

    assert out["success"] is False and out["error_code"] == "INVALID_MENTION" and "Too many" in out["message"]
    post.assert_not_called()


def test_fifty_mentions_are_accepted(db, post):  # MENT-08
    text = " ".join(f"@55118888{n:04d}" for n in range(50))

    assert main.send_message(GROUP, text)["success"] is True
    assert len(sent(post)["mentions"]) == 50


@pytest.mark.parametrize("text", [
    "see https://x.com/@1234567 now", "run `@12345678` in the shell", "deadline @2024-10-21 closed", "a@5511888801",
])
def test_urls_code_dates_and_emails_are_not_mentions(db, post, text):  # MENT-09
    main.send_message(GROUP, text)

    assert sent(post)["message"] == text and "mentions" not in sent(post)


@pytest.mark.parametrize("digits, converted, jid_server", [
    ("123456", False, None), ("1234567", True, "s.whatsapp.net"),
    ("5511988880101", True, "s.whatsapp.net"), ("55118888010100", True, "lid"),
    ("12345678901234567890", True, "lid"), ("123456789012345678901", False, None),
])
def test_mention_digit_boundaries(db, post, digits, converted, jid_server):  # MENT-01
    main.send_message(GROUP, f"hi @{digits} end")

    if converted:
        assert sent(post)["mentions"] == [f"{digits}@{jid_server}"]
    else:
        assert "mentions" not in sent(post) and sent(post)["message"] == f"hi @{digits} end"


def test_an_explicit_group_jid_is_not_a_person(db, post):  # MENT-02
    out = main.send_message(GROUP, "hi", mentions=[CLIENT])

    assert out["success"] is False and out["error_code"] == "INVALID_MENTION"
    post.assert_not_called()
