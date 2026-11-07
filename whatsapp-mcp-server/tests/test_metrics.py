"""get_metrics: counters from the audit logs, the database and the bridge status, never texts or ids (METRIC-*)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import drafts  # noqa: E402
import main  # noqa: E402
import metrics  # noqa: E402
import monitor  # noqa: E402
import send_policy  # noqa: E402
from tests.test_monitor_scan import ANA, GROUP, add, make  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def write(path, *entries):
    with open(path, "a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def stamp(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def test_monitor_replies_are_counted_per_profile_inside_the_window(tmp_path, monkeypatch):  # METRIC-01
    monkeypatch.setattr(monitor, "AUDIT_PATH", str(tmp_path / "audit.log"))
    write(monitor.AUDIT_PATH,
          {"ts": stamp(1), "profile": "a", "result": "sent"},
          {"ts": stamp(2), "profile": "a", "result": "blocked", "error_code": "APPROVAL_REQUIRED"},
          {"ts": stamp(3), "profile": "b", "result": "sent"},
          {"ts": stamp(30), "profile": "a", "result": "sent"})          # outside 24 h
    with open(monitor.AUDIT_PATH, "a") as f:
        f.write("not json\n" + json.dumps({"ts": "2026-10-21T10:00:00", "profile": "a", "result": "sent"}) + "\n")  # no timezone

    out = metrics.collect(24, now=NOW)

    assert out["monitor_replies"] == {"by_profile": {"a": {"sent": 1, "blocked": 1}, "b": {"sent": 1}},
                                      "error_codes": {"APPROVAL_REQUIRED": 1}}


def test_sends_are_counted_by_result_with_overrides(tmp_path, monkeypatch):  # METRIC-02
    monkeypatch.setattr(send_policy, "AUDIT_PATH", str(tmp_path / "send.log"))
    write(send_policy.AUDIT_PATH,
          {"ts": stamp(1), "result": "sent", "override": True},
          {"ts": stamp(1), "result": "blocked", "rule": "deny:x", "override": False},
          {"ts": stamp(2), "result": "blocked", "override": False})

    out = metrics.collect(24, now=NOW)

    assert out["sends"] == {"by_result": {"sent": 1, "blocked": 2}, "with_override": 1}


def test_messages_are_counted_by_direction_inside_the_window(tmp_path, monkeypatch):  # METRIC-03
    conn = make(tmp_path, monkeypatch)
    add(conn, "IN1", "x", sender=ANA, minute=55)                 # 2026-10-21 11:55 UTC
    add(conn, "IN2", "x", sender=ANA, minute=56)
    add(conn, "OUT", "x", from_me=1, minute=57)
    conn.execute("UPDATE messages SET timestamp = '2026-10-19 11:55:00+00:00' WHERE id = 'IN2'")   # two days old
    conn.commit()

    out = metrics.collect(24, now=NOW)

    assert out["messages"] == {"received": 1, "from_this_account": 1}


def test_messages_are_null_when_the_database_cannot_be_read(tmp_path, monkeypatch):  # METRIC-03
    monkeypatch.setattr(metrics.whatsapp, "MESSAGES_DB_PATH", str(tmp_path / "missing.db"))
    assert metrics.collect(24, now=NOW)["messages"] is None


def test_webhook_counters_are_summed_and_null_without_the_bridge():  # METRIC-04
    status = {"webhooks": {"enabled": True, "endpoints": [
        {"name": "a", "delivered": 5, "failed": 1, "dropped": 0, "queued": 2, "last_error": "secret detail"},
        {"name": "b", "delivered": 3, "failed": 0, "dropped": 4, "queued": 0}]}}

    assert metrics.collect(24, now=NOW, bridge_status=status)["webhooks"] == \
           {"enabled": True, "endpoints": 2, "delivered": 8, "failed": 1, "dropped": 4, "queued": 2}
    assert metrics.collect(24, now=NOW, bridge_status={"bridge_reachable": False})["webhooks"] is None
    assert metrics.collect(24, now=NOW)["webhooks"] is None


def test_pending_drafts_are_counted():  # METRIC-05
    drafts.save("p", GROUP, "M1", ANA, "x", now=NOW)
    assert metrics.collect(24, now=NOW)["pending_drafts"] == 1


def test_no_text_name_id_or_sender_leaks(tmp_path, monkeypatch):  # METRIC-06
    monkeypatch.setattr(monitor, "AUDIT_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setattr(send_policy, "AUDIT_PATH", str(tmp_path / "send.log"))
    write(monitor.AUDIT_PATH, {"ts": stamp(1), "profile": "a", "result": "sent", "chat_jid": GROUP, "sender": ANA})
    write(send_policy.AUDIT_PATH, {"ts": stamp(1), "result": "sent", "chat_jid": GROUP, "chat_name": "Secret Group",
                                   "recipient": "Secret Group", "rule": "allow:Secret Group"})

    text = json.dumps(metrics.collect(24, now=NOW))

    for leaked in (GROUP, ANA, "Secret Group"):
        assert leaked not in text


def test_bad_windows_are_refused():  # METRIC-07
    for bad in (0, -1, True, "24", float("nan"), metrics.MAX_WINDOW_HOURS + 1):
        assert metrics.collect(bad, now=NOW)["error_code"] == "INVALID_ARGUMENT"


def test_the_tool_passes_the_bridge_status(monkeypatch):  # METRIC-04
    monkeypatch.setattr(main, "whatsapp_get_connection_status",
                        lambda: {"bridge_reachable": True, "webhooks": {"enabled": False, "endpoints": []}})
    out = main.get_metrics(1)
    assert out["success"] and out["webhooks"]["enabled"] is False and out["window_hours"] == 1
