"""Testes do P1-9: menções reais, resposta (reply_to) e dry_run no send_message."""
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
DM = "555181993271@s.whatsapp.net"
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
                     [(GROUP, "Projeto Elbratec"), (CLIENT, "Software Elbratec"), (DM, "Teilor")])
    conn.executemany(
        "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?, ?, ?, ?, '2026-10-21 10:00:00', ?)",
        [("Q1", GROUP, "160889625940144", "podem revisar a proposta?", 0),
         ("MINE", GROUP, "555100000000", "revisando", 1),
         ("OTHER", CLIENT, "160889625940144", "outro chat", 0),
         ("DMQ", DM, "555181993271", "oi do dm", 0)],
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
    main.send_message(GROUP, "*Aviso* @+55518175-1110 revise,\n> citação _agora_")

    body = sent(post)
    assert body["message"] == "*Aviso* @555181751110 revise,\n> citação _agora_"
    assert body["mentions"] == ["555181751110@s.whatsapp.net"]


@pytest.mark.parametrize("token, digits, jid", [
    ("@+55 51", None, None),   # espaço não faz parte do token: nada é convertido
    ("@555181993271", "555181993271", "555181993271@s.whatsapp.net"),
    ("@160889625940144", "160889625940144", "160889625940144@lid"),
    ("@+55(51)98199-3271", "5551981993271", "5551981993271@s.whatsapp.net"),
])
def test_mention_token_forms(db, post, token, digits, jid):  # MENT-01
    main.send_message(GROUP, f"oi {token}!")

    body = sent(post)
    if digits is None:
        assert "mentions" not in body
        return
    assert body["message"] == f"oi @{digits}!"
    assert body["mentions"] == [jid]


@pytest.mark.parametrize("text", ["ano @2024 foi bom", "mail a@b.com", "@+55 sem número", "preço @ 100", "id@5551"])
def test_text_that_is_not_a_mention_is_untouched(db, post, text):  # MENT-03
    main.send_message(GROUP, text)

    assert sent(post)["message"] == text
    assert "mentions" not in sent(post)


def test_plain_text_sends_exactly_as_before(db, post):  # MENT-03
    main.send_message(GROUP, "*oi* _tudo_ bem")

    assert sent(post) == {"recipient": GROUP, "message": "*oi* _tudo_ bem"}


def test_same_person_twice_is_one_mention(db, post):  # MENT-01
    main.send_message(GROUP, "@+555181993271 e de novo @555181993271")

    assert sent(post)["mentions"] == ["555181993271@s.whatsapp.net"]
    assert sent(post)["message"] == "@555181993271 e de novo @555181993271"


def test_explicit_mentions_are_added_without_duplicates(db, post):  # MENT-02
    main.send_message(GROUP, "oi @555181993271", mentions=[
        "555181993271", "160889625940144@lid", "160889625940144", "+55 (11) 99999-0000",
    ])

    assert sent(post)["mentions"] == [
        "555181993271@s.whatsapp.net", "160889625940144@lid", "5511999990000@s.whatsapp.net",
    ]


def test_invalid_explicit_mention_is_refused(db, post):  # MENT-02
    out = main.send_message(GROUP, "oi", mentions=["Teilor"])

    assert out["success"] is False and out["error_code"] == "INVALID_MENTION"
    post.assert_not_called()


def test_reply_to_is_sent_to_the_bridge(db, post):  # REPLY-01
    main.send_message(GROUP, "revisado", reply_to="Q1")

    assert sent(post)["reply_to"] == "Q1"


def test_reply_to_a_message_of_another_chat_is_refused(db, post):  # REPLY-02
    out = main.send_message(GROUP, "oi", reply_to="OTHER")

    assert out["success"] is False and out["error_code"] == "REPLY_TARGET_NOT_FOUND"
    post.assert_not_called()


def test_reply_to_unknown_message_is_refused(db, post):  # REPLY-02
    out = main.send_message(GROUP, "oi", reply_to="NAOEXISTE")

    assert out["success"] is False and out["error_code"] == "REPLY_TARGET_NOT_FOUND"
    post.assert_not_called()


def test_reply_in_a_direct_chat_found_by_phone(db, post):  # REPLY-01
    main.send_message("555181993271", "oi", reply_to="DMQ")

    assert sent(post)["reply_to"] == "DMQ"


def test_dry_run_does_not_touch_the_bridge(db, post):  # DRY-01
    out = main.send_message("Projeto Elbratec", "*Aviso* @+555181993271", reply_to="Q1", dry_run=True)

    post.assert_not_called()
    assert out["success"] is True and out["dry_run"] is True
    assert out["preview"] == {
        "text": "*Aviso* @555181993271",
        "chat_jid": GROUP,
        "chat_name": "Projeto Elbratec",
        "mentions": [{"token": "555181993271", "jid": "555181993271@s.whatsapp.net"}],
        "reply_to": {"id": "Q1", "sender": "160889625940144", "text": "podem revisar a proposta?"},
    }
    assert "message_id" not in out


def test_dry_run_without_mentions_or_reply(db, post):  # DRY-01
    out = main.send_message(GROUP, "oi", dry_run=True)

    assert out["preview"]["mentions"] == [] and out["preview"]["reply_to"] is None


def test_dry_run_shows_the_policy_block(db, post, tmp_path):  # DRY-02
    with open(send_policy.POLICY_PATH, "w") as f:
        json.dump({"mode": "denylist", "deny": [CLIENT]}, f)

    out = main.send_message(CLIENT, "oi", dry_run=True)

    assert out["success"] is False and out["error_code"] == "SEND_BLOCKED_BY_POLICY"
    assert "preview" not in out
    post.assert_not_called()


def test_dry_run_is_not_audited(db, post):  # DRY-03
    main.send_message(GROUP, "oi", dry_run=True)

    assert not os.path.exists(send_policy.AUDIT_PATH)


def test_dry_run_with_unknown_reply_target(db, post):  # DRY-04
    out = main.send_message(GROUP, "oi", reply_to="NAOEXISTE", dry_run=True)

    assert out["success"] is False and out["error_code"] == "REPLY_TARGET_NOT_FOUND"


def test_real_send_with_mentions_is_audited_by_size_of_the_final_text(db, post):  # compatibilidade com P1-8
    main.send_message(GROUP, "@+555181993271 oi")

    [line] = [json.loads(l) for l in open(send_policy.AUDIT_PATH)]
    assert line["result"] == "sent" and "oi" not in json.dumps(line) and line["size"] == len("@555181993271 oi")
