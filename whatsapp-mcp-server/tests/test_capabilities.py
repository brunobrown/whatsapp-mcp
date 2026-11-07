"""get_connection_status says which server version answers and what it can do (CAP-*)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import capabilities  # noqa: E402
import main  # noqa: E402
import whatsapp  # noqa: E402


def test_tool_adds_version_features_and_enabled_to_the_bridge_status(monkeypatch):  # CAP-01
    monkeypatch.setattr(main, "whatsapp_get_connection_status",
                        lambda: {"connected": True, "bridge_reachable": True, "webhooks": {"enabled": True}})

    out = main.get_connection_status()

    assert out["connected"] is True and out["server_version"] == main.VERSION
    assert {"webhooks", "watch_cli"} <= set(out["features"])
    assert out["enabled"] == {"webhooks": True}


def test_fields_are_present_when_the_bridge_is_down(monkeypatch):  # CAP-02
    monkeypatch.setattr(whatsapp, "WHATSAPP_API_BASE_URL", "http://127.0.0.1:9/api")
    monkeypatch.setattr(main, "whatsapp_get_connection_status", whatsapp.get_connection_status)

    out = main.get_connection_status()

    assert out["bridge_reachable"] is False and out["error_code"] == "BRIDGE_UNREACHABLE"
    assert out["server_version"] == main.VERSION and out["enabled"] == {"webhooks": False}


def test_webhooks_are_off_when_the_bridge_reports_nothing_about_them():  # CAP-03
    assert capabilities.describe("1.0", {})["enabled"] == {"webhooks": False}
    assert capabilities.describe("1.0", {"webhooks": "x"})["enabled"] == {"webhooks": False}
    assert capabilities.describe("1.0", {"webhooks": {"enabled": False}})["enabled"] == {"webhooks": False}
