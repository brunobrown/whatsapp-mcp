"""Testes do monitor, parte 4: monitor_reply e seus guardrails (RESP-01..10)."""
import json
import os
import stat
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import monitor  # noqa: E402
import send_policy  # noqa: E402
import whatsapp  # noqa: E402
from tests.test_monitor_scan import ANA, ANA_LID, BOB, DM_ANA, GROUP, GROUP2, ME, add, make  # noqa: E402

TOKEN = "token-do-bruno"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone(timedelta(hours=-3)))


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "monitor_profiles.json"))
    monkeypatch.setattr(monitor, "AUDIT_PATH", str(tmp_path / "monitor_audit.log"))
    return c


@pytest.fixture
def sent(monkeypatch):
    """Troca send_message do servidor: guarda cada chamada e devolve sucesso."""
    calls = []

    def fake(recipient, message, override_token=None, mentions=None, reply_to=None, dry_run=False):
        calls.append({"recipient": recipient, "message": message, "override_token": override_token,
                      "mentions": mentions, "reply_to": reply_to, "dry_run": dry_run})
        if dry_run:
            return whatsapp.SendResult(True, "Dry run: nothing was sent", preview={
                "text": message, "chat_jid": recipient, "chat_name": "Grupo 1", "mentions": [],
                "reply_to": {"id": reply_to, "sender": ANA, "text": "oi"}})
        return whatsapp.SendResult(True, f"Message sent to {recipient}", message_id="SENT1",
                                   timestamp="2026-10-21T12:00:00-03:00", chat_jid=recipient)
    monkeypatch.setattr(whatsapp, "send_message", fake)
    return calls


def real_sends(calls):
    return [c for c in calls if not c["dry_run"]]


def config(**fields):
    body = {"targets": ["me"], "approval_token": TOKEN, **fields}
    with open(monitor.CONFIG_PATH, "w") as f:
        json.dump({"profiles": {"p": body}}, f)


def reply(message_id="HIT", text="Resposta de teste", chat_jid=GROUP, **kwargs):
    kwargs.setdefault("approval_token", TOKEN)
    kwargs.setdefault("now", NOW)
    return monitor.monitor_reply("p", chat_jid, message_id, text, **kwargs)


def audit_lines():
    if not os.path.exists(monitor.AUDIT_PATH):
        return []
    with open(monitor.AUDIT_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def put_audit(**fields):
    entry = {"ts": NOW.isoformat(), "profile": "p", "chat_jid": GROUP, "sender": ANA, "result": "sent", **fields}
    with open(monitor.AUDIT_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def seed(conn):
    add(conn, "HIT", f"@{ME} pode ver o deploy?", chat=GROUP, sender=ANA)


def test_reply_to_a_triggering_message_is_sent_in_its_chat_quoting_it(conn, sent):  # RESP-02
    seed(conn)
    config()

    out = reply()

    assert out["success"] is True and out["message_id"] == "SENT1" and out["chat_jid"] == GROUP
    assert out["mode"] == "draft" and out["profile"] == "p"
    assert real_sends(sent) == [{"recipient": GROUP, "message": "Resposta de teste", "override_token": None,
                                 "mentions": None, "reply_to": "HIT", "dry_run": False}]


def test_the_send_policy_still_applies(conn, tmp_path, monkeypatch):  # RESP-02
    seed(conn)
    config()
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"mode": "denylist", "deny": [GROUP]}))
    monkeypatch.setattr(send_policy, "POLICY_PATH", str(policy))
    monkeypatch.setattr(send_policy, "AUDIT_PATH", str(tmp_path / "send_audit.log"))

    with mock.patch("whatsapp.requests.post") as post:
        out = reply()

    assert out["success"] is False and out["error_code"] == "SEND_BLOCKED_BY_POLICY"
    post.assert_not_called()
    assert [(l["result"], l["error_code"]) for l in audit_lines()] == [("blocked", "SEND_BLOCKED_BY_POLICY")]


def test_a_failed_send_is_audited_as_failed(conn, monkeypatch):  # RESP-09
    seed(conn)
    config()
    monkeypatch.setattr(whatsapp, "send_message", lambda *a, **k: whatsapp.SendResult(
        False, "bridge fora", error_code=None) if not a[5] else whatsapp.SendResult(True, "ok", preview={}))

    out = reply()

    assert out["success"] is False and out["message"] == "bridge fora"
    assert [l["result"] for l in audit_lines()] == ["failed"]


@pytest.mark.parametrize("message_id, chat", [("NOPE", GROUP), ("HIT", GROUP2), ("HIT", "120363999@g.us")])
def test_message_that_is_not_there(conn, sent, message_id, chat):  # RESP-01
    seed(conn)
    config()

    out = reply(message_id, chat_jid=chat)

    assert out["success"] is False and out["error_code"] == "NOT_A_TRIGGER" and real_sends(sent) == []


def test_messages_that_do_not_fire_the_profile(conn, sent):  # RESP-01
    add(conn, "PLAIN", "sem menção", chat=GROUP, sender=ANA)
    add(conn, "MINE", f"@{ME} eu mesmo", chat=GROUP, from_me=1)
    add(conn, "OFFTOPIC", f"@{ME} almoço?", chat=GROUP, sender=ANA)
    add(conn, "DENIED", f"@{ME} deploy", chat=GROUP, sender=BOB)
    add(conn, "FROM", "só do número", chat=GROUP, sender=ANA)
    config(topics=["deploy"], senders={"deny": [BOB]})

    for message_id in ("PLAIN", "MINE", "OFFTOPIC", "DENIED"):
        assert reply(message_id)["error_code"] == "NOT_A_TRIGGER", message_id
    assert real_sends(sent) == [] and [l["result"] for l in audit_lines()] == []


def test_trigger_kind_is_checked_against_the_profile(conn, sent):  # RESP-01
    add(conn, "DM", "oi no privado", chat=DM_ANA, sender=ANA)
    config(triggers=["mention"])

    assert reply("DM", chat_jid=DM_ANA)["error_code"] == "NOT_A_TRIGGER"
    config(triggers=["direct"])
    assert reply("DM", chat_jid=DM_ANA)["success"] is True
    assert real_sends(sent)[0]["recipient"] == DM_ANA


def test_an_old_message_can_still_be_answered(conn, sent):  # RESP-01 (a idade só vale para o monitor)
    conn.execute("INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me, quoted_id) "
                 "VALUES ('OLD', ?, ?, ?, '2026-10-01 10:00:00+00:00', 0, NULL)", (GROUP, ANA, f"@{ME} antigo"))
    conn.commit()
    config()

    assert reply("OLD")["success"] is True


def test_the_agent_cannot_pick_another_chat(conn, sent):  # RESP-01
    seed(conn)
    add(conn, "HIT2", f"@{ME} no outro grupo", chat=GROUP2, sender=ANA)
    config()

    out = reply("HIT", chat_jid=GROUP2)   # a mensagem HIT não existe no grupo 2

    assert out["error_code"] == "NOT_A_TRIGGER" and real_sends(sent) == []
    assert reply("HIT2", chat_jid=GROUP2)["success"] is True and real_sends(sent)[0]["recipient"] == GROUP2


def test_auto_mode_sends_without_a_token_only_to_trusted_senders(conn, sent):  # RESP-03
    add(conn, "TRUSTED", f"@{ME} oi", sender=ANA)
    add(conn, "TRUSTED_LID", f"@{ME} oi", sender=ANA_LID, minute=56)
    add(conn, "STRANGER", f"@{ME} oi", sender=BOB, minute=57)
    config(mode="auto", auto_senders=[ANA], limits={"cooldown_seconds": 1, "replies_per_chat_per_hour": 20,
                                                    "replies_per_sender_per_hour": 20})

    ok = reply("TRUSTED", approval_token=None)
    ok_lid = reply("TRUSTED_LID", approval_token=None)
    refused = reply("STRANGER", approval_token=None)
    approved = reply("STRANGER")

    assert ok["success"] is True and ok["mode"] == "auto" and ok_lid["success"] is True
    assert refused["success"] is False and refused["error_code"] == "APPROVAL_REQUIRED"
    assert approved["success"] is True and approved["mode"] == "draft"
    assert len(real_sends(sent)) == 3
    assert [(l["mode"], l["approval_used"]) for l in audit_lines()] == [
        ("auto", False), ("auto", False), ("draft", False), ("draft", True)]


def test_draft_mode_needs_the_token(conn, sent):  # RESP-04
    seed(conn)
    config()

    for token in (None, "", "errado", TOKEN + "x", TOKEN[:-1]):
        out = reply(approval_token=token)
        assert out["success"] is False and out["error_code"] == "APPROVAL_REQUIRED", token
        assert out["preview"]["text"] == "Resposta de teste" and out["mode"] == "draft"
    assert real_sends(sent) == []


def test_without_a_configured_token_a_draft_is_never_sent(conn, sent):  # RESP-04
    seed(conn)
    with open(monitor.CONFIG_PATH, "w") as f:
        json.dump({"profiles": {"p": {"targets": ["me"]}}}, f)

    for token in (None, "", "qualquer", "None"):
        out = reply(approval_token=token)
        assert out["error_code"] == "APPROVAL_REQUIRED" and "no approval_token" in out["message"]
    assert real_sends(sent) == []


def test_the_correct_token_sends_a_draft(conn, sent):  # RESP-04
    seed(conn)
    config()

    out = reply(approval_token=TOKEN)

    assert out["success"] is True and out["mode"] == "draft" and len(real_sends(sent)) == 1
    assert audit_lines()[0]["approval_used"] is True


@pytest.mark.parametrize("text", ["", "   ", "\n\t", None, 5])
def test_empty_text(conn, sent, text):  # RESP-05
    seed(conn)
    config()

    out = reply(text=text)

    assert out["error_code"] == "INVALID_ARGUMENT" and real_sends(sent) == []


def test_text_size_limit(conn, sent):  # RESP-05
    seed(conn)
    config(limits={"max_reply_chars": 10})

    assert reply(text="x" * 11)["error_code"] == "INVALID_ARGUMENT"
    assert reply(text="x" * 10)["success"] is True
    assert len(real_sends(sent)) == 1


@pytest.mark.parametrize("kwargs", [{"message_id": ""}, {"message_id": "  "}, {"chat_jid": ""}, {"chat_jid": "Grupo 1"},
                                    {"chat_jid": "abc@example.net"}])
def test_invalid_ids(conn, sent, kwargs):  # RESP-05
    seed(conn)
    config()

    assert reply(**kwargs)["error_code"] == "INVALID_ARGUMENT" and real_sends(sent) == []


def test_cooldown_between_replies_in_a_chat(conn, sent):  # RESP-06
    seed(conn)
    config(limits={"cooldown_seconds": 30, "replies_per_chat_per_hour": 50, "replies_per_sender_per_hour": 50})
    put_audit(ts=(NOW - timedelta(seconds=10)).isoformat(), sender=BOB)

    out = reply()

    assert out["error_code"] == "RATE_LIMITED" and out["success"] is False and real_sends(sent) == []
    assert out["retry_after_seconds"] in (20, 21) and "30 s" in out["message"]
    put_audit(ts=(NOW - timedelta(seconds=100)).isoformat(), chat_jid=GROUP2)   # outro chat não conta
    assert reply(now=NOW + timedelta(seconds=25))["success"] is True


def test_replies_per_chat_per_hour(conn, sent):  # RESP-06
    seed(conn)
    config(limits={"cooldown_seconds": 1, "replies_per_chat_per_hour": 3, "replies_per_sender_per_hour": 50})
    for minutes in (50, 40, 30):
        put_audit(ts=(NOW - timedelta(minutes=minutes)).isoformat(), sender=BOB)

    out = reply()

    assert out["error_code"] == "RATE_LIMITED" and "3 replies per chat per hour" in out["message"]
    assert 599 <= out["retry_after_seconds"] <= 602     # a mais antiga (50 min) sai da janela em 10 min
    assert reply(now=NOW + timedelta(minutes=11))["success"] is True


def test_replies_per_sender_per_hour(conn, sent):  # RESP-06
    seed(conn)
    config(limits={"cooldown_seconds": 1, "replies_per_chat_per_hour": 50, "replies_per_sender_per_hour": 2})
    put_audit(ts=(NOW - timedelta(minutes=40)).isoformat(), chat_jid=GROUP2)
    put_audit(ts=(NOW - timedelta(minutes=20)).isoformat(), chat_jid=DM_ANA)

    out = reply()

    assert out["error_code"] == "RATE_LIMITED" and "2 replies per sender per hour" in out["message"]
    assert 1199 <= out["retry_after_seconds"] <= 1202 and real_sends(sent) == []


def test_only_recent_sent_replies_of_this_profile_count(conn, sent):  # RESP-06
    seed(conn)
    config(limits={"cooldown_seconds": 1, "replies_per_chat_per_hour": 1, "replies_per_sender_per_hour": 1})
    put_audit(ts=(NOW - timedelta(minutes=61)).isoformat())                       # fora da janela
    put_audit(ts=(NOW - timedelta(minutes=5)).isoformat(), profile="outro")       # outro perfil
    put_audit(ts=(NOW - timedelta(minutes=5)).isoformat(), result="blocked")      # não foi enviada
    put_audit(ts=(NOW + timedelta(minutes=5)).isoformat())                        # do futuro: relógio errado, não conta
    with open(monitor.AUDIT_PATH, "a") as f:
        f.write("isto não é json\n{\"ts\": \"quebrado\"}\n")

    assert reply()["success"] is True


def test_unreadable_audit_log_blocks_the_reply(conn, sent, tmp_path, monkeypatch):  # RESP-07
    seed(conn)
    config()
    monkeypatch.setattr(monitor, "AUDIT_PATH", str(tmp_path))     # um diretório

    out = reply()

    assert out["error_code"] == "RATE_LIMIT_UNVERIFIABLE" and out["success"] is False and real_sends(sent) == []


def test_read_error_after_the_writable_check_also_blocks(conn, sent, monkeypatch):  # RESP-07
    seed(conn)
    config()
    monkeypatch.setattr(monitor, "_read_sent", mock.Mock(side_effect=PermissionError("negado")))

    out = reply()

    assert out["error_code"] == "RATE_LIMIT_UNVERIFIABLE" and "negado" in out["message"] and real_sends(sent) == []


def test_missing_audit_log_counts_zero_and_is_created_private(conn, sent):  # RESP-07, RESP-09
    seed(conn)
    config()
    assert not os.path.exists(monitor.AUDIT_PATH)

    assert reply()["success"] is True
    assert stat.S_IMODE(os.stat(monitor.AUDIT_PATH).st_mode) == 0o600


def test_dry_run_sends_nothing_audits_nothing_and_counts_nothing(conn, sent):  # RESP-08
    seed(conn)
    config(limits={"cooldown_seconds": 600, "replies_per_chat_per_hour": 1, "replies_per_sender_per_hour": 1})

    first = reply(dry_run=True, approval_token=None)
    second = reply(dry_run=True)

    for out in (first, second):
        assert out["success"] is True and out["dry_run"] is True and out["mode"] == "draft"
        assert out["requires_approval_token"] is True and out["matched"] == [{"trigger": "mention", "target": "me"}]
        assert out["preview"]["text"] == "Resposta de teste" and out["preview"]["reply_to"]["id"] == "HIT"
    assert real_sends(sent) == [] and audit_lines() == [] and not os.path.exists(monitor.AUDIT_PATH)
    assert reply()["success"] is True   # o limite de 1 por hora ainda estava livre


def test_dry_run_of_a_trusted_sender_does_not_ask_for_a_token(conn, sent):  # RESP-08
    seed(conn)
    config(mode="auto", auto_senders=[ANA])

    out = reply(dry_run=True, approval_token=None)

    assert out["mode"] == "auto" and out["requires_approval_token"] is False


def test_dry_run_still_shows_a_policy_block(conn, tmp_path, monkeypatch):  # RESP-08
    seed(conn)
    config()
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"mode": "denylist", "deny": [GROUP]}))
    monkeypatch.setattr(send_policy, "POLICY_PATH", str(policy))

    out = reply(dry_run=True)

    assert out["success"] is False and out["error_code"] == "SEND_BLOCKED_BY_POLICY" and audit_lines() == []


def test_dry_run_of_a_message_that_does_not_fire_is_refused(conn, sent):  # RESP-08, RESP-01
    add(conn, "PLAIN", "nada", sender=ANA)
    config()

    assert reply("PLAIN", dry_run=True)["error_code"] == "NOT_A_TRIGGER"


def test_audit_line_has_the_facts_and_never_the_text_or_the_token(conn, sent):  # RESP-09
    seed(conn)
    config()
    secret_text = "texto-secreto-da-resposta-12345"

    reply(text=secret_text)
    reply(text=secret_text, approval_token="chute-errado")

    raw = open(monitor.AUDIT_PATH).read()
    assert secret_text not in raw and TOKEN not in raw and "chute-errado" not in raw
    sent_line, blocked_line = audit_lines()
    assert sent_line["profile"] == "p" and sent_line["chat_jid"] == GROUP and sent_line["sender"] == ANA
    assert sent_line["message_id"] == "HIT" and sent_line["triggers"] == ["mention"] and sent_line["mode"] == "draft"
    assert sent_line["approval_used"] is True and sent_line["result"] == "sent" and sent_line["reply_id"] == "SENT1"
    assert sent_line["size"] == len(secret_text) and datetime.fromisoformat(sent_line["ts"]).tzinfo is not None
    assert blocked_line["result"] == "blocked" and blocked_line["error_code"] == "APPROVAL_REQUIRED"
    assert blocked_line["approval_used"] is False


def test_rate_limited_attempts_are_audited_but_do_not_count(conn, sent):  # RESP-09, RESP-06
    seed(conn)
    config(limits={"cooldown_seconds": 600, "replies_per_chat_per_hour": 5, "replies_per_sender_per_hour": 5})

    first = reply(now=None)
    second = reply(now=None)

    assert first["success"] is True and second["error_code"] == "RATE_LIMITED"
    assert [(l["result"], l.get("error_code")) for l in audit_lines()] == [("sent", None), ("blocked", "RATE_LIMITED")]


@pytest.mark.parametrize("write, code", [
    (None, "PROFILE_NOT_FOUND"),
    ({"profiles": {"p": {"targets": ["me"], "enabled": False}}}, "PROFILE_DISABLED"),
    ("{quebrado", "MONITOR_CONFIG_INVALID"),
    ({"profiles": {"p": {"targets": ["me"], "mode": "x"}}}, "MONITOR_CONFIG_INVALID"),
])
def test_profile_problems_refuse_before_anything(conn, sent, write, code):  # RESP-10
    seed(conn)
    if write is None:
        write = {"profiles": {"outro": {"targets": ["me"]}}}
    with open(monitor.CONFIG_PATH, "w") as f:
        f.write(write if isinstance(write, str) else json.dumps(write))

    out = reply()

    assert out["success"] is False and out["error_code"] == code and sent == [] and audit_lines() == []


def test_concurrent_replies_cannot_both_pass_the_limit(conn, monkeypatch):  # RESP-06
    seed(conn)
    config(limits={"cooldown_seconds": 600, "replies_per_chat_per_hour": 5, "replies_per_sender_per_hour": 5})
    calls = []

    def slow_send(recipient, message, override_token=None, mentions=None, reply_to=None, dry_run=False):
        if dry_run:
            return whatsapp.SendResult(True, "ok", preview={})
        time.sleep(0.2)
        calls.append(message)
        return whatsapp.SendResult(True, "sent", message_id="S", timestamp="t", chat_jid=recipient)
    monkeypatch.setattr(whatsapp, "send_message", slow_send)
    results = []

    threads = [threading.Thread(target=lambda: results.append(reply(now=None))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert len(calls) == 1 and sorted(r.get("error_code", "ok") for r in results) == ["RATE_LIMITED"] * 3 + ["ok"]


def test_unknown_own_account(conn, sent, monkeypatch):  # RESP-01
    seed(conn)
    config()

    def unknown():
        raise monitor.OwnAccountUnknown("sem número")
    monkeypatch.setattr(monitor, "own_number", unknown)

    assert reply()["error_code"] == "OWN_ACCOUNT_UNKNOWN" and real_sends(sent) == []


def test_missing_database(tmp_path, monkeypatch, sent):  # RESP-01
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(tmp_path / "none.db"))
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "c.json"))
    monkeypatch.setattr(monitor, "AUDIT_PATH", str(tmp_path / "a.log"))
    monkeypatch.setattr(monitor, "own_number", lambda: ME)
    config()

    assert reply()["error_code"] == "DIRECTORY_UNAVAILABLE" and not (tmp_path / "none.db").exists()


def test_tool_is_registered_with_a_documented_contract():  # RESP-02..08
    doc = main.monitor_reply.__doc__

    for expected in ("approval_token", "dry_run", "APPROVAL_REQUIRED", "RATE_LIMITED", "NOT_A_TRIGGER",
                     "RATE_LIMIT_UNVERIFIABLE", "profile", "message_id", "chat_jid"):
        assert expected in doc


def test_tool_passes_its_arguments(conn, sent):  # RESP-02
    seed(conn)
    config()

    out = main.monitor_reply("p", GROUP, "HIT", "pelo tool", approval_token=TOKEN)

    assert out["success"] is True and real_sends(sent)[0]["message"] == "pelo tool"
