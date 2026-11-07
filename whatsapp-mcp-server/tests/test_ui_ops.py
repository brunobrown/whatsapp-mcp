"""What the read-only panels may show: state, counts, ids and times; never text, names or an action (OPS-01, OPS-02)."""
import ast
import json
import os
import sys
import time
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import drafts  # noqa: E402
import monitor  # noqa: E402
import send_policy  # noqa: E402
from ui.core.bridge import Status, Unavailable  # noqa: E402
from ui.sections import ops  # noqa: E402

TEXT = "TEXTO-SECRETO-DE-UMA-MENSAGEM"


class FakeClient:
    def __init__(self, answer):
        self.answer = answer

    def status(self):
        return self.answer


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(send_policy, "AUDIT_PATH", str(tmp_path / "send_audit.log"))
    monkeypatch.setattr(monitor, "AUDIT_PATH", str(tmp_path / "monitor_audit.log"))
    monkeypatch.setenv("WHATSAPP_MONITOR_DRAFTS", str(tmp_path / "drafts.json"))
    return tmp_path


def write_log(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write((entry if isinstance(entry, str) else json.dumps(entry)) + "\n")


def test_the_status_is_what_the_bridge_says_limited_to_the_listed_fields():  # OPS-01
    answer = Status({"connected": True, "logged_in": True, "phone": "5511", "uptime_seconds": 5, "history": TEXT, "bridge_reachable": True})

    shown = ops.bridge_status(FakeClient(answer))

    assert shown == Status({"connected": True, "logged_in": True, "phone": "5511", "uptime_seconds": 5})


def test_a_bridge_that_is_down_is_passed_on_as_unavailable():  # OPS-01
    assert ops.bridge_status(FakeClient(Unavailable("unreachable"))) == Unavailable("unreachable")


def test_the_counters_come_from_the_audit_logs_and_hold_numbers_only(logs):  # OPS-02
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    write_log(send_policy.AUDIT_PATH, [{"ts": now, "kind": "text", "recipient": TEXT, "result": "sent", "rule": "", "override": True},
                                       {"ts": now, "kind": "text", "recipient": TEXT, "result": "blocked", "override": False}])
    write_log(monitor.AUDIT_PATH, [{"ts": now, "profile": "support", "result": "sent", "error_code": None}])

    data = ops.counters(24, FakeClient(Unavailable("unreachable")))

    assert data["sends"] == {"by_result": {"sent": 1, "blocked": 1}, "with_override": 1}
    assert data["monitor_replies"]["by_profile"] == {"support": {"sent": 1}}
    assert TEXT not in json.dumps(data) and data["webhooks"] is None


def test_the_webhook_counters_of_the_bridge_are_included_when_it_answers(logs):  # OPS-01
    hooks = {"webhooks": {"enabled": True, "endpoints": [{"delivered": 3, "failed": 1, "dropped": 0, "queued": 2}]}}

    data = ops.counters(24, FakeClient(Status(hooks)))

    assert data["webhooks"] == {"enabled": True, "endpoints": 1, "delivered": 3, "failed": 1, "dropped": 0, "queued": 2}


def test_pending_drafts_show_who_asked_and_when_but_never_the_text(logs):  # OPS-02
    drafts.save("support", "120363@g.us", "M1", "5511999", TEXT)
    time.sleep(0.01)
    drafts.save("support", "120363@g.us", "M2", "5511888", TEXT + "-2")

    rows = ops.pending_drafts()

    assert [r["message_id"] for r in rows] == ["M2", "M1"]                       # newest first
    assert set(rows[0]) == set(ops.DRAFT_KEYS) and rows[0]["sender"] == "5511888"
    assert TEXT not in json.dumps(rows)


def test_expired_drafts_are_not_listed(logs):  # OPS-02
    drafts.save("support", "1@g.us", "OLD", "5511", TEXT, now=datetime(2020, 1, 1, tzinfo=timezone.utc))

    assert ops.pending_drafts() == []


def test_with_the_drafts_switched_off_there_are_none(logs, monkeypatch):  # OPS-02
    monkeypatch.setenv("WHATSAPP_MONITOR_DRAFTS", "off")

    assert ops.pending_drafts() == []


def test_the_send_audit_is_reduced_to_ids_times_and_results(logs):  # OPS-02
    write_log(send_policy.AUDIT_PATH, [{"ts": "2026-11-07T10:00:00-03:00", "kind": "text", "recipient": "Joana Silva", "chat_jid": "1@g.us",
                                        "chat_name": "Familia", "size": 12, "result": "sent", "rule": "", "override": False, "message_id": "A1",
                                        "message": TEXT}])

    rows = ops.audit_tail("send")

    assert rows == [{"ts": "2026-11-07T10:00:00-03:00", "kind": "text", "chat_jid": "1@g.us", "size": 12, "result": "sent", "rule": "",
                     "override": False, "message_id": "A1"}]
    assert "Joana" not in json.dumps(rows) and "Familia" not in json.dumps(rows) and TEXT not in json.dumps(rows)


def test_the_monitor_audit_is_reduced_to_the_listed_fields(logs):  # OPS-02
    write_log(monitor.AUDIT_PATH, [{"ts": "2026-11-07T10:00:00-03:00", "profile": "p", "chat_jid": "1@g.us", "sender": "5511", "message_id": "M",
                                    "triggers": ["mention"], "mode": "draft", "approval_used": False, "result": "drafted", "size": 9,
                                    "text": TEXT, "approval_token": "tok-abcdefgh"}])

    row = ops.audit_tail("monitor")[0]

    assert set(row) == {"ts", "profile", "chat_jid", "sender", "message_id", "triggers", "mode", "approval_used", "result", "size"}
    assert TEXT not in json.dumps(row) and "tok-abcdefgh" not in json.dumps(row)


def test_audit_rows_come_newest_first_and_bounded(logs):  # OPS-02
    write_log(send_policy.AUDIT_PATH, [{"ts": f"t{i:03d}", "result": "sent"} for i in range(300)])

    rows = ops.audit_tail("send", limit=5)

    assert [r["ts"] for r in rows] == ["t299", "t298", "t297", "t296", "t295"]
    assert len(ops.audit_tail("send", limit=10_000)) == ops.MAX_ROWS
    assert ops.audit_tail("send", limit=0) == [] and ops.audit_tail("send", limit=-3) == []


def test_damaged_lines_and_a_missing_file_are_not_errors(logs):  # OPS-02
    assert ops.audit_tail("monitor") == []
    write_log(monitor.AUDIT_PATH, ["{broken", "[1, 2]", json.dumps({"ts": "ok", "result": "sent"}), "", "\x00\x01"])

    assert ops.audit_tail("monitor") == [{"ts": "ok", "result": "sent"}]


def test_a_very_large_log_is_read_from_its_end_only(logs):  # OPS-02
    with open(send_policy.AUDIT_PATH, "w") as f:
        for i in range(60_000):
            f.write(json.dumps({"ts": f"t{i:06d}", "result": "sent", "rule": "x" * 20}) + "\n")
    started = time.monotonic()

    rows = ops.audit_tail("send", limit=3)

    assert [r["ts"] for r in rows] == ["t059999", "t059998", "t059997"] and time.monotonic() - started < 1.0


def test_an_unknown_kind_is_an_error():  # OPS-02
    with pytest.raises(KeyError):
        ops.audit_tail("everything")


def test_ops_panels_have_no_message_text_and_no_actions():  # OPS-02
    tree = ast.parse(open(ops.__file__, encoding="utf-8").read())
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    public = {n for n in dir(ops) if not n.startswith("_") and callable(getattr(ops, n)) and getattr(getattr(ops, n), "__module__", "") == ops.__name__}

    assert public == {"bridge_status", "counters", "pending_drafts", "audit_tail"}
    actions = {"send_message", "send_file", "send_audio_message", "send_poll", "vote_poll", "react_to_message", "delete_message",
               "monitor_reply", "save", "remove", "post", "put", "delete", "unlink", "write", "open_for_write", "approve", "discard"}
    assert not (used & actions), used & actions
    assert "text" not in ops.DRAFT_KEYS and all("text" not in keys for keys in ops.AUDIT_KEYS.values())
