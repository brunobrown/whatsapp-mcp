"""Testes do P1-8: política de envio, override_token e log de auditoria."""
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

CLIENT = "120363111@g.us"          # "Software Elbratec": grupo do cliente
TECH = "120363222@g.us"            # "Projeto Elbratec": grupo técnico
TWIN_A, TWIN_B = "120363333@g.us", "120363444@g.us"   # dois chats com o mesmo nome
LID, PHONE = "160889625940144", "555181993271"
OK_BODY = {"success": True, "message": "Message sent", "message_id": "ID1",
           "timestamp": "2026-10-21T10:30:00-03:00", "chat_jid": TECH}
TOKEN = "segredo-do-bruno"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);"
        "CREATE TABLE contacts_identity (id INTEGER PRIMARY KEY AUTOINCREMENT, lid TEXT, phone TEXT,"
        " full_name TEXT, push_name TEXT, updated_at TIMESTAMP);"
    )
    conn.executemany("INSERT INTO chats VALUES (?, ?, NULL)", [
        (CLIENT, "Software Elbratec"), (TECH, "Projeto Elbratec"),
        (TWIN_A, "Gêmeo"), (TWIN_B, "gêmeo"),
        (f"{LID}@lid", "Teilor"),
    ])
    conn.execute("INSERT INTO contacts_identity (lid, phone, full_name) VALUES (?, ?, 'Teilor')", (LID, PHONE))
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    return path


def write_policy(path_holder, **policy):
    with open(send_policy.POLICY_PATH, "w") as f:
        json.dump(policy, f)


def audit_lines():
    if not os.path.exists(send_policy.AUDIT_PATH):
        return []
    with open(send_policy.AUDIT_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def response(status=200, payload=OK_BODY):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


@pytest.fixture
def post():
    with mock.patch("whatsapp.requests.post", return_value=response()) as p:
        yield p


@pytest.fixture
def media_file(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"12345")
    return str(path)


def blocked(out):
    return out.get("success") is False and out.get("error_code") == "SEND_BLOCKED_BY_POLICY"


def test_denylist_blocks_a_denied_group(db, post):  # POLICY-01, 04, 05
    write_policy(None, mode="denylist", deny=["Software Elbratec"])

    out = main.send_message(CLIENT, "oi")

    assert blocked(out)
    assert "Software Elbratec" in out["message"] and CLIENT in out["message"]
    assert "deny" in out["message"] and "Nothing was sent" in out["message"]
    post.assert_not_called()


def test_denylist_allows_other_chats(db, post):  # POLICY-06
    write_policy(None, mode="denylist", deny=["Software Elbratec"])

    out = main.send_message(TECH, "oi")

    assert out == {"success": True, "message": "Message sent", "message_id": "ID1",
                   "timestamp": "2026-10-21T10:30:00-03:00", "chat_jid": TECH}
    post.assert_called_once()


def test_allowlist_blocks_what_is_not_listed(db, post):  # POLICY-02, 04
    write_policy(None, mode="allowlist", allow=[TECH])

    assert blocked(main.send_message(CLIENT, "oi"))
    post.assert_not_called()
    assert main.send_message(TECH, "oi")["success"] is True
    post.assert_called_once()


def test_missing_mode_means_denylist(db, post):  # Assumptions: mode ausente
    write_policy(None, deny=[CLIENT])

    assert blocked(main.send_message(CLIENT, "oi"))
    assert main.send_message(TECH, "oi")["success"] is True


def test_deny_wins_over_allow(db, post):  # POLICY-03
    write_policy(None, mode="allowlist", allow=[CLIENT, TECH], deny=[CLIENT])

    assert blocked(main.send_message(CLIENT, "oi"))
    assert main.send_message(TECH, "oi")["success"] is True


def test_no_policy_file_allows_everything(db, post):  # POLICY-07
    assert main.send_message(CLIENT, "oi")["success"] is True


@pytest.mark.parametrize("content", [
    "{not json", '{"mode": "bloqueia-tudo"}', '{"deny": "Software Elbratec"}',
    '{"allow": [1, 2]}', '["lista"]',
])
def test_invalid_policy_blocks_every_send(db, post, content):  # POLICY-08
    with open(send_policy.POLICY_PATH, "w") as f:
        f.write(content)

    out = main.send_message(TECH, "oi")

    assert blocked(out)
    assert "policy" in out["message"].lower()
    post.assert_not_called()


@pytest.mark.parametrize("tool", ["send_message", "send_file", "send_audio_message"])
def test_policy_applies_to_every_send_tool(db, post, media_file, tool, tmp_path):  # POLICY-09
    write_policy(None, mode="denylist", deny=[CLIENT])
    args = {"send_message": ("oi",), "send_file": (media_file,)}.get(tool)
    if tool == "send_audio_message":
        ogg = str(tmp_path / "a.ogg")
        open(ogg, "wb").write(b"x")
        args = (ogg,)

    out = getattr(main, tool)(CLIENT, *args)

    assert blocked(out)
    post.assert_not_called()
    assert getattr(main, tool)(TECH, *args)["success"] is True


def test_jid_rule_matches_only_that_jid(db, post):  # POLICY-10
    write_policy(None, mode="denylist", deny=[CLIENT])

    assert blocked(main.send_message(CLIENT, "oi"))
    assert main.send_message(TECH, "oi")["success"] is True


def test_name_rule_is_case_insensitive(db, post):  # POLICY-11
    write_policy(None, mode="denylist", deny=["SOFTWARE elbratec"])

    assert blocked(main.send_message(CLIENT, "oi"))


@pytest.mark.parametrize("rule, target", [
    (PHONE, f"{LID}@lid"), (f"{PHONE}@s.whatsapp.net", LID), (LID, PHONE), (f"{LID}@lid", f"{PHONE}@s.whatsapp.net"),
])
def test_phone_and_lid_of_the_same_person_are_the_same_destination(db, post, rule, target):  # POLICY-12
    write_policy(None, mode="denylist", deny=[rule])

    assert blocked(main.send_message(target, "oi"))
    post.assert_not_called()


def test_unrelated_person_is_not_matched_by_identity(db, post):  # POLICY-12 (sem falso positivo)
    write_policy(None, mode="denylist", deny=[PHONE])

    assert main.send_message("5511888880000", "oi")["success"] is True


def test_ambiguous_name_denies_all_and_allows_none(db, post):  # POLICY-13
    write_policy(None, mode="denylist", deny=["gêmeo"])
    assert blocked(main.send_message(TWIN_A, "oi")) and blocked(main.send_message(TWIN_B, "oi"))

    write_policy(None, mode="allowlist", allow=["gêmeo"])
    assert blocked(main.send_message(TWIN_A, "oi")) and blocked(main.send_message(TWIN_B, "oi"))
    post.assert_not_called()


def test_recipient_name_resolves_to_the_chat(db, post):  # POLICY-14
    out = main.send_message("projeto elbratec", "oi")

    assert out["success"] is True
    assert post.call_args.kwargs["json"]["recipient"] == TECH


def test_recipient_name_is_checked_against_the_policy_by_its_jid(db, post):  # POLICY-14 + 01
    write_policy(None, mode="denylist", deny=[CLIENT])

    assert blocked(main.send_message("Software Elbratec", "oi"))
    post.assert_not_called()


def test_unknown_or_ambiguous_recipient_name_is_refused(db, post):  # POLICY-15
    unknown = main.send_message("Grupo que não existe", "oi")
    twin = main.send_message("Gêmeo", "oi")

    assert unknown["success"] is False and unknown["error_code"] == "RECIPIENT_NOT_FOUND"
    assert twin["success"] is False and twin["error_code"] == "RECIPIENT_AMBIGUOUS"
    assert TWIN_A in twin["message"] and TWIN_B in twin["message"]
    post.assert_not_called()


def test_phone_numbers_and_jids_are_not_treated_as_names(db, post):  # POLICY-14 (compatibilidade)
    assert main.send_message("5511999990000", "oi")["success"] is True
    assert post.call_args.kwargs["json"]["recipient"] == "5511999990000"
    assert main.send_message("+55 (11) 99999-0000", "oi")["success"] is True
    assert main.send_message("999@g.us", "oi")["success"] is True
    assert post.call_args.kwargs["json"]["recipient"] == "999@g.us"


def test_override_token_releases_a_blocked_send(db, post):  # POLICY-16, 19
    write_policy(None, mode="denylist", deny=[CLIENT], override_token=TOKEN)

    out = main.send_message(CLIENT, "oi", override_token=TOKEN)

    assert out["success"] is True
    post.assert_called_once()
    assert audit_lines()[-1]["override"] is True and audit_lines()[-1]["result"] == "sent"


@pytest.mark.parametrize("token, configured", [("errado", TOKEN), (TOKEN, None), ("", TOKEN), (None, TOKEN)])
def test_wrong_or_missing_token_keeps_the_block(db, post, token, configured):  # POLICY-17
    policy = {"mode": "denylist", "deny": [CLIENT]}
    if configured:
        policy["override_token"] = configured
    write_policy(None, **policy)

    out = main.send_message(CLIENT, "oi", override_token=token)

    assert blocked(out)
    post.assert_not_called()


def test_token_never_appears_in_log_or_response(db, post):  # POLICY-18
    write_policy(None, mode="denylist", deny=[CLIENT], override_token=TOKEN)

    for attempt in (TOKEN, "chute-errado"):
        out = main.send_message(CLIENT, "oi", override_token=attempt)
        assert TOKEN not in json.dumps(out) and "chute-errado" not in json.dumps(out)
    raw = open(send_policy.AUDIT_PATH).read()
    assert TOKEN not in raw and "chute-errado" not in raw


def test_token_does_not_unlock_an_invalid_policy(db, post):  # POLICY-20
    with open(send_policy.POLICY_PATH, "w") as f:
        f.write('{"mode": "x", "override_token": "%s"}' % TOKEN)

    assert blocked(main.send_message(TECH, "oi", override_token=TOKEN))
    post.assert_not_called()


def test_audit_records_sent_blocked_and_failed(db, post):  # POLICY-21
    write_policy(None, mode="denylist", deny=[CLIENT])
    main.send_message(TECH, "olá")
    main.send_message(CLIENT, "proibido")
    post.return_value = response(500, {"success": False, "message": "boom"})
    main.send_message(TECH, "vai falhar")

    sent, blk, failed = audit_lines()

    assert (sent["result"], blk["result"], failed["result"]) == ("sent", "blocked", "failed")
    assert sent["kind"] == "message" and sent["recipient"] == TECH and sent["chat_jid"] == TECH
    assert sent["chat_name"] == "Projeto Elbratec" and sent["message_id"] == "ID1" and sent["override"] is False
    assert blk["chat_name"] == "Software Elbratec" and "deny" in blk["rule"] and "message_id" not in blk
    assert "message_id" not in failed
    assert all(line["ts"] for line in (sent, blk, failed))


def test_audit_kind_and_size_for_files(db, post, media_file):  # POLICY-21, 22
    main.send_file(TECH, media_file)

    [line] = audit_lines()

    assert line["kind"] == "file" and line["size"] == 5


def test_audit_never_stores_the_message_text(db, post):  # POLICY-22
    main.send_message(TECH, "texto-secreto-do-cliente")

    [line] = audit_lines()

    assert "texto-secreto" not in json.dumps(line)
    assert line["size"] == len("texto-secreto-do-cliente")


def test_audit_failure_does_not_stop_the_send(db, post, monkeypatch, caplog):  # POLICY-23
    monkeypatch.setattr(send_policy, "AUDIT_PATH", os.path.join(os.path.dirname(send_policy.POLICY_PATH), "nao", "existe", "log"))

    with caplog.at_level("WARNING"):
        out = main.send_message(TECH, "oi")

    assert out["success"] is True
    assert any("audit" in r.message.lower() for r in caplog.records)


def test_policy_is_read_on_every_send(db, post):  # POLICY-24
    assert main.send_message(CLIENT, "oi")["success"] is True
    write_policy(None, mode="denylist", deny=[CLIENT])
    assert blocked(main.send_message(CLIENT, "oi"))
    os.remove(send_policy.POLICY_PATH)
    assert main.send_message(CLIENT, "oi")["success"] is True


def test_empty_recipient_is_still_rejected_without_a_log_of_a_destination(db, post):  # compatibilidade
    out = main.send_message("", "oi")

    assert out["success"] is False
    post.assert_not_called()
