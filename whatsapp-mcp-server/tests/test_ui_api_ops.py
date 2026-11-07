"""Operation routes (OPS-01, OPS-02 over HTTP): only the listed fields leave; a down bridge is `unavailable`."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import drafts
import monitor
import send_policy
from ui.api.routes_ops import ops_routes
from ui.sections import ops
from ui.security.bridge import BridgeResult
from ui_api_rig import Rig

PRIVATE = "the private words of a message"


class FakeClient:
    def __init__(self, result):
        self.result = result

    def status(self):
        return self.result


def bridge_status(**extra):
    base = {"connected": True, "logged_in": True, "phone": "15550100001", "version": "abc123",
            "started_at": "2026-11-07T10:00:00Z", "uptime_seconds": 120, "last_event_at": None,
            "last_message_at": None, "last_connected_at": None, "last_disconnected_at": None,
            "pending_media_retries": 0, "webhooks": {"enabled": True, "endpoints": [
                {"name": "hook", "delivered": 5, "failed": 1, "dropped": 0, "queued": 2, "last_error": "",
                 "last_success_at": None, "persistent": False, "recovered": 0}]}}
    return BridgeResult(True, data={**base, **extra})


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(ops, "CLIENT", FakeClient(bridge_status()))
    return Rig(lambda r: ops_routes())


def test_every_ops_route_needs_a_session(rig):
    anon = rig.anonymous()

    for path in ("status", "counters", "drafts", "audit/send", "audit/monitor"):
        assert anon.get(f"/api/ops/{path}").status_code == 401


def test_status_carries_only_the_listed_fields(monkeypatch, rig):
    leaky = bridge_status(api_token="tok-123", address="http://10.0.0.1:8080", text=PRIVATE)
    leaky.data["webhooks"]["endpoints"][0].update(url="https://example.com/h", secret="s" * 40)
    monkeypatch.setattr(ops, "CLIENT", FakeClient(leaky))
    client, _ = rig.signed_in()

    response = client.get("/api/ops/status")

    shown = response.json()["status"]
    assert response.status_code == 200 and shown["connected"] is True and shown["uptime_seconds"] == 120
    assert set(shown["webhooks"]["endpoints"][0]) == {"name", "delivered", "failed", "dropped", "queued", "last_error",
                                                      "last_success_at", "persistent", "recovered"}
    for leaked in ("tok-123", "10.0.0.1", PRIVATE, "example.com", "s" * 40):
        assert leaked not in response.text


@pytest.mark.parametrize("path", ["status", "counters"])
def test_a_bridge_that_is_down_answers_unavailable_with_the_reason(monkeypatch, rig, path):
    down = BridgeResult(False, reason="unreachable", message="The bridge is not responding.")
    monkeypatch.setattr(ops, "CLIENT", FakeClient(down))
    client, _ = rig.signed_in()

    response = client.get(f"/api/ops/{path}")

    assert response.status_code == 200
    if path == "status":
        assert response.json() == {"unavailable": "unreachable", "message": "The bridge is not responding."}
    else:
        assert "unavailable" not in response.json()          # the counters of the files still count


def test_counters_have_the_documented_keys_and_no_text(rig):
    client, _ = rig.signed_in()

    body = client.get("/api/ops/counters").json()

    assert set(body) <= {"window_hours", "since", "messages", "monitor_replies", "sends", "pending_drafts", "webhooks"}
    assert body["window_hours"] == 24 and body["webhooks"]["delivered"] == 5


def test_drafts_come_newest_first_without_their_text(tmp_path, monkeypatch, rig):
    now = datetime.now(timezone.utc)
    drafts.save("assistant", "123@g.us", "M1", "15550100009", PRIVATE, now=now - timedelta(minutes=10))
    drafts.save("assistant", "123@g.us", "M2", "15550100008", PRIVATE + " two", now=now - timedelta(minutes=1))
    client, _ = rig.signed_in()

    response = client.get("/api/ops/drafts")

    body = response.json()
    assert [r["message_id"] for r in body["rows"]] == ["M2", "M1"] and body["count"] == 2
    assert set(body["rows"][0]) == {"id", "profile", "chat_jid", "message_id", "sender", "created_at", "expires_at"}
    assert PRIVATE not in response.text


@pytest.mark.parametrize("kind, path_of", [("send", lambda: send_policy.AUDIT_PATH), ("monitor", lambda: monitor.AUDIT_PATH)])
def test_the_audit_rows_come_newest_first_with_only_listed_keys(rig, kind, path_of):
    with open(path_of(), "w") as f:
        for n in (1, 2, 3):
            f.write(json.dumps({"ts": f"2026-11-07T10:0{n}:00Z", "result": f"r{n}", "message_id": f"M{n}", "text": PRIVATE}) + "\n")
        f.write("not json\n")
    client, _ = rig.signed_in()

    response = client.get(f"/api/ops/audit/{kind}")

    assert [r["result"] for r in response.json()["rows"]] == ["r3", "r2", "r1"]
    assert PRIVATE not in response.text and all("text" not in r for r in response.json()["rows"])


@pytest.mark.parametrize("kind", ["other", "ui_auth", "..", "SEND", "send%2F..%2Fx"])
def test_an_unknown_audit_log_is_404(rig, kind):
    client, _ = rig.signed_in()

    response = client.get(f"/api/ops/audit/{kind}")

    assert response.status_code == 404


def test_an_empty_audit_log_is_an_empty_list(rig):
    client, _ = rig.signed_in()

    assert client.get("/api/ops/audit/send").json() == {"rows": []}


def test_ops_routes_only_read(rig):
    client, headers = rig.signed_in()

    for path in ("status", "counters", "drafts", "audit/send"):
        assert client.post(f"/api/ops/{path}", json={}, headers=headers).status_code == 405
        assert client.delete(f"/api/ops/{path}", headers=headers).status_code == 405
