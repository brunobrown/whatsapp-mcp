import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import send_policy  # noqa: E402
import whatsapp  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """No test reads the user's real token or key."""
    monkeypatch.setenv("WHATSAPP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("WHATSAPP_DB_KEY", raising=False)
    monkeypatch.delenv("WHATSAPP_DB_KEY_FILE", raising=False)


@pytest.fixture(autouse=True)
def isolated_send_files(tmp_path, monkeypatch):
    """No test reads the real policy or writes to the real audit log."""
    monkeypatch.setattr(send_policy, "POLICY_PATH", str(tmp_path / "no_policy.json"))
    monkeypatch.setattr(send_policy, "AUDIT_PATH", str(tmp_path / "send_audit.log"))


@pytest.fixture(autouse=True)
def isolated_messages_db(tmp_path, monkeypatch):
    """No test reads the bridge's real database: the default path points to a file that does not exist.
    Tests that need a database swap the path for their own."""
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(tmp_path / "no_db" / "messages.db"))


@pytest.fixture(autouse=True)
def isolated_drafts(tmp_path, monkeypatch):
    """No test writes the real pending-drafts file."""
    monkeypatch.setenv("WHATSAPP_MONITOR_DRAFTS", str(tmp_path / "monitor_drafts.json"))
