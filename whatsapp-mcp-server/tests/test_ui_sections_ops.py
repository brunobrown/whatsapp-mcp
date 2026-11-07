"""Operation data (OPS-01, OPS-02): status, counters, pending drafts and audit tails, reduced to listed fields."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import drafts
import monitor
import send_policy
from ui.sections import ops
from ui.security.bridge import BridgeResult

SECRET_WORDS = "the private words of a message"


class FakeClient:
    def __init__(self, result):
        self.result = result

    def status(self):
        return self.result


def good_status(**extra):
    base = {
        "connected": True, "logged_in": True, "phone": "15550100001", "version": "abc123",
        "started_at": "2026-11-07T10:00:00Z", "uptime_seconds": 120, "last_event_at": "2026-11-07T10:01:00Z",
        "last_message_at": None, "last_connected_at": "2026-11-07T10:00:05Z", "last_disconnected_at": None,
        "pending_media_retries": 2,
        "webhooks": {"enabled": True, "endpoints": [
            {"name": "hook", "delivered": 5, "failed": 1, "dropped": 0, "queued": 2, "last_error": "HTTP 500",
             "last_success_at": "2026-11-07T10:00:30Z", "persistent": False, "recovered": 0}]},
    }
    return BridgeResult(True, data={**base, **extra})


# -- status ---------------------------------------------------------------------------------------
def test_status_passes_only_the_listed_keys():
    leaky = good_status(api_token="tok-123", address="http://10.0.0.1:8080", text=SECRET_WORDS)
    leaky.data["webhooks"]["endpoints"][0].update(url="https://example.com/h", secret="s" * 40)
    leaky.data["webhooks"]["error"] = "refused"
    out = ops.status(FakeClient(leaky))
    assert set(out) == {"status"}
    shown = out["status"]
    assert set(shown) == {"connected", "logged_in", "phone", "version", "started_at", "uptime_seconds", "last_event_at",
                          "last_message_at", "last_connected_at", "last_disconnected_at", "pending_media_retries", "webhooks"}
    assert shown["connected"] is True and shown["uptime_seconds"] == 120 and shown["pending_media_retries"] == 2
    assert set(shown["webhooks"]) == {"enabled", "error", "endpoints"}
    assert set(shown["webhooks"]["endpoints"][0]) == {"name", "delivered", "failed", "dropped", "queued", "last_error",
                                                       "last_success_at", "persistent", "recovered"}
    text = json.dumps(out)
    for leaked in ("tok-123", "10.0.0.1", SECRET_WORDS, "example.com", "s" * 40):
        assert leaked not in text


def test_status_drops_values_of_the_wrong_kind_and_cuts_long_text():
    out = ops.status(FakeClient(good_status(connected={"nested": 1}, version="v" * 1000, uptime_seconds=[1])))
    shown = out["status"]
    assert "connected" not in shown and "uptime_seconds" not in shown
    assert len(shown["version"]) <= 200


@pytest.mark.parametrize("reason", ["unreachable", "unauthorized", "unexpected", "unsupported"])
def test_a_bridge_that_cannot_answer_is_unavailable_with_the_reason(reason):
    out = ops.status(FakeClient(BridgeResult(False, reason=reason, message="The bridge is not responding.")))
    assert out == {"unavailable": reason, "message": "The bridge is not responding."}


# -- counters -------------------------------------------------------------------------------------
def write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write((line if isinstance(line, str) else json.dumps(line)) + "\n")


def recent(minutes=5):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).astimezone().isoformat(timespec="seconds")


def test_counters_are_the_projects_counts_without_text(tmp_path):
    write_lines(monitor.AUDIT_PATH, [
        {"ts": recent(), "profile": "assistant", "result": "sent", "text": SECRET_WORDS},
        {"ts": recent(), "profile": "assistant", "result": "blocked", "error_code": "RATE_LIMITED"}])
    write_lines(send_policy.AUDIT_PATH, [{"ts": recent(), "result": "sent", "override": True},
                                         {"ts": recent(), "result": "blocked"}])
    out = ops.counters(FakeClient(good_status()))
    assert set(out) <= {"window_hours", "since", "messages", "monitor_replies", "sends", "pending_drafts", "webhooks"}
    assert out["monitor_replies"]["by_profile"] == {"assistant": {"sent": 1, "blocked": 1}}
    assert out["monitor_replies"]["error_codes"] == {"RATE_LIMITED": 1}
    assert out["sends"] == {"by_result": {"sent": 1, "blocked": 1}, "with_override": 1}
    assert out["webhooks"]["delivered"] == 5 and out["webhooks"]["queued"] == 2
    assert out["window_hours"] == 24 and out["pending_drafts"] == 0
    assert SECRET_WORDS not in json.dumps(out)


def test_counters_with_a_bridge_that_does_not_answer_still_count_the_rest():
    out = ops.counters(FakeClient(BridgeResult(False, reason="unreachable", message="x")))
    assert "unavailable" not in out
    assert out["webhooks"] is None and out["sends"] == {"by_result": {}, "with_override": 0}


# -- pending drafts -------------------------------------------------------------------------------
def test_drafts_show_who_and_when_but_never_the_text(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_MONITOR_DRAFTS", str(tmp_path / "drafts.json"))
    now = datetime.now(timezone.utc)
    drafts.save("assistant", "123@g.us", "M1", "15550100009", SECRET_WORDS, now=now - timedelta(minutes=10))
    drafts.save("assistant", "123@g.us", "M2", "15550100008", SECRET_WORDS + " two", now=now - timedelta(minutes=1))
    out = ops.drafts_view()
    assert out["count"] == 2
    assert [r["message_id"] for r in out["rows"]] == ["M2", "M1"]                       # newest first
    for row in out["rows"]:
        assert set(row) == {"id", "profile", "chat_jid", "message_id", "sender", "created_at", "expires_at"}
    assert SECRET_WORDS not in json.dumps(out)


def test_no_drafts_is_an_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_MONITOR_DRAFTS", str(tmp_path / "none.json"))
    assert ops.drafts_view() == {"rows": [], "count": 0}


# -- audit tails ----------------------------------------------------------------------------------
def test_the_send_audit_keeps_only_listed_keys_newest_first():
    write_lines(send_policy.AUDIT_PATH, [
        {"ts": "2026-11-07T10:00:00-03:00", "kind": "text", "recipient": "Somebody", "chat_jid": "1@s.whatsapp.net",
         "chat_name": "A", "size": 5, "result": "sent", "rule": "", "override": False, "message_id": "M1",
         "text": SECRET_WORDS, "override_token": "tok"},
        {"ts": "2026-11-07T11:00:00-03:00", "kind": "text", "chat_jid": "2@g.us", "chat_name": "B", "size": 7,
         "result": "blocked", "rule": "not in the allowlist", "override": False}])
    out = ops.audit("send")
    assert [r["chat_jid"] for r in out["rows"]] == ["2@g.us", "1@s.whatsapp.net"]
    for row in out["rows"]:
        assert set(row) <= {"ts", "kind", "chat_jid", "chat_name", "size", "result", "rule", "override", "message_id"}
    assert out["rows"][1]["message_id"] == "M1" and out["rows"][0]["rule"] == "not in the allowlist"
    text = json.dumps(out)
    assert SECRET_WORDS not in text and "tok" not in text and "Somebody" not in text


def test_the_monitor_audit_keeps_only_listed_keys():
    write_lines(monitor.AUDIT_PATH, [
        {"ts": "2026-11-07T10:00:00-03:00", "profile": "assistant", "chat_jid": "1@g.us", "sender": "15550100009",
         "message_id": "M1", "triggers": ["mention", "reply"], "mode": "draft", "approval_used": False, "result": "draft",
         "size": 12, "error_code": "APPROVAL_REQUIRED", "reply_id": "R1", "text": SECRET_WORDS, "approval_token": "tok-x"}])
    row = ops.audit("monitor")["rows"][0]
    assert set(row) == {"ts", "profile", "chat_jid", "sender", "message_id", "triggers", "mode", "approval_used", "result",
                        "size", "error_code", "reply_id"}
    assert row["triggers"] == ["mention", "reply"] and row["approval_used"] is False and row["size"] == 12
    assert SECRET_WORDS not in json.dumps(row) and "tok-x" not in json.dumps(row)


def test_rows_are_capped_at_200_keeping_the_newest():
    write_lines(send_policy.AUDIT_PATH, [{"ts": f"2026-11-07T10:{i // 60:02d}:{i % 60:02d}-03:00", "result": "sent", "size": i}
                                         for i in range(450)])
    rows = ops.audit("send")["rows"]
    assert ops.MAX_ROWS == 200 and len(rows) == 200
    assert rows[0]["size"] == 449 and rows[-1]["size"] == 250


def test_a_corrupt_line_is_skipped():
    write_lines(send_policy.AUDIT_PATH, [
        {"ts": "2026-11-07T10:00:00-03:00", "result": "sent", "size": 1},
        "{this is not json", "[1, 2, 3]", "\"just text\"", "", "null",
        {"ts": "2026-11-07T10:00:02-03:00", "result": "blocked", "size": 2}])
    rows = ops.audit("send")["rows"]
    assert [r["size"] for r in rows] == [2, 1]


def test_a_missing_or_unreadable_log_is_an_empty_list(tmp_path, monkeypatch):
    assert ops.audit("send") == {"rows": []}
    monkeypatch.setattr(monitor, "AUDIT_PATH", str(tmp_path))                  # a directory cannot be read as a file
    assert ops.audit("monitor") == {"rows": []}


def test_a_huge_log_is_read_from_its_end(tmp_path):
    path = send_policy.AUDIT_PATH
    with open(path, "w") as f:
        for i in range(60000):
            f.write(json.dumps({"ts": "2026-11-07T10:00:00-03:00", "result": "sent", "size": i, "rule": "x" * 60}) + "\n")
    assert os.path.getsize(path) > ops.TAIL_BYTES
    rows = ops.audit("send")["rows"]
    assert len(rows) == 200 and rows[0]["size"] == 59999


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        ops.audit("everything")


def test_long_values_are_cut_and_odd_types_dropped():
    write_lines(send_policy.AUDIT_PATH, [{"ts": "2026-11-07T10:00:00-03:00", "rule": "r" * 5000, "size": {"a": 1},
                                          "override": "yes", "chat_name": ["x"]}])
    row = ops.audit("send")["rows"][0]
    assert len(row["rule"]) <= 200
    assert "size" not in row and "chat_name" not in row and "override" not in row
