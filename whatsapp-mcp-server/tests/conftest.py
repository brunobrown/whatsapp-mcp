import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import send_policy  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Nenhum teste lê o token ou a chave reais do usuário."""
    monkeypatch.setenv("WHATSAPP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("WHATSAPP_DB_KEY", raising=False)
    monkeypatch.delenv("WHATSAPP_DB_KEY_FILE", raising=False)


@pytest.fixture(autouse=True)
def isolated_send_files(tmp_path, monkeypatch):
    """Nenhum teste lê a política real nem grava no log de auditoria real."""
    monkeypatch.setattr(send_policy, "POLICY_PATH", str(tmp_path / "no_policy.json"))
    monkeypatch.setattr(send_policy, "AUDIT_PATH", str(tmp_path / "send_audit.log"))
