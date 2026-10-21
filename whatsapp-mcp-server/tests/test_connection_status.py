"""Testes do P2-11: ferramenta get_connection_status."""
import os
import sqlite3
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import whatsapp  # noqa: E402


class Reply:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code, self._body, self._bad = status, body, bad_json

    def json(self):
        if self._bad:
            raise ValueError("no json")
        return self._body


@pytest.fixture
def paths(tmp_path, monkeypatch):
    db = str(tmp_path / "messages.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
        CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
            is_from_me BOOLEAN, PRIMARY KEY (id, chat_jid));
        INSERT INTO messages VALUES ('A', 'c', 's', 'x', '2026-10-21 10:00:00-03:00', 0);
        INSERT INTO messages VALUES ('B', 'c', 's', 'x', '2026-10-21 12:00:00+00:00', 0);""")
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", db)
    monkeypatch.setattr(whatsapp, "BRIDGE_LOG_PATH", str(tmp_path / "bridge.log"))
    return tmp_path


def test_reachable_bridge_status_is_passed_through(paths, monkeypatch):  # MCPSTAT-01
    body = {"connected": True, "logged_in": True, "phone": "5581", "last_event_at": "2026-10-21T10:00:00Z",
            "pending_media_retries": 2}
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append((url, kw)) or Reply(200, body))

    out = whatsapp.get_connection_status()

    assert out == {**body, "bridge_reachable": True}
    assert calls[0][0] == "http://localhost:8080/api/status" and calls[0][1]["timeout"] > 0


def test_disconnected_bridge_keeps_its_last_event_time(paths, monkeypatch):  # MCPSTAT-01 (conectado=false vem do bridge)
    body = {"connected": False, "last_event_at": "2026-10-21T10:00:00Z"}
    monkeypatch.setattr(requests, "get", lambda url, **kw: Reply(200, body))

    out = whatsapp.get_connection_status()

    assert out["connected"] is False and out["last_event_at"] == "2026-10-21T10:00:00Z"
    assert out["bridge_reachable"] is True


def test_unreachable_bridge_reports_not_connected_with_last_signs_of_life(paths, monkeypatch):  # MCPSTAT-02
    (paths / "bridge.log").write_text("x")
    os.utime(paths / "bridge.log", (1792576800, 1792576800))  # 2026-10-21T10:00:00Z

    def refuse(url, **kw):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(requests, "get", refuse)

    out = whatsapp.get_connection_status()

    assert out["connected"] is False and out["bridge_reachable"] is False
    assert out["error_code"] == "BRIDGE_UNREACHABLE" and "refused" in out["message"]
    assert out["last_message_at"] == "2026-10-21T10:00:00-03:00"  # 13:00 UTC, depois das 12:00 UTC embora o texto seja menor
    from datetime import datetime
    assert datetime.fromisoformat(out["last_log_at"]).timestamp() == 1792576800


def test_unreachable_bridge_without_database_or_log(tmp_path, monkeypatch):  # MCPSTAT-02
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setattr(whatsapp, "BRIDGE_LOG_PATH", str(tmp_path / "missing.log"))
    monkeypatch.setattr(requests, "get", lambda url, **kw: (_ for _ in ()).throw(requests.Timeout("slow")))

    out = whatsapp.get_connection_status()

    assert out["connected"] is False and out["error_code"] == "BRIDGE_UNREACHABLE"
    assert out["last_message_at"] is None and out["last_log_at"] is None
    assert not (tmp_path / "missing.db").exists()  # consultar o status não cria banco


@pytest.mark.parametrize("reply", [Reply(500, {"error": "x"}), Reply(200, bad_json=True), Reply(200, ["lista"])])
def test_unexpected_answer_is_unknown_but_reachable(paths, monkeypatch, reply):  # MCPSTAT-03
    monkeypatch.setattr(requests, "get", lambda url, **kw: reply)

    out = whatsapp.get_connection_status()

    assert out["connected"] is False and out["bridge_reachable"] is True and out["error_code"] == "UNKNOWN"
    assert str(reply.status_code) in out["message"]


def test_tool_is_registered_and_returns_the_status(paths, monkeypatch):
    import main
    monkeypatch.setattr(requests, "get", lambda url, **kw: Reply(200, {"connected": True}))

    assert main.get_connection_status() == {"connected": True, "bridge_reachable": True}
    assert "BRIDGE_UNREACHABLE" in main.get_connection_status.__doc__
