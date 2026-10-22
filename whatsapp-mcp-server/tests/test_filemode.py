"""Testes do T6: aviso quando arquivos com token têm acesso de grupo ou outros (WARN-01)."""
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import filemode  # noqa: E402
import monitor  # noqa: E402
import send_policy  # noqa: E402

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")


def make(tmp_path, name, mode, content="{}"):
    path = tmp_path / name
    path.write_text(content)
    os.chmod(path, mode)
    return str(path)


@posix_only
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666, 0o664, 0o650])
def test_a_file_open_to_group_or_others_is_reported(tmp_path, mode):  # WARN-01
    path = make(tmp_path, "secret.json", mode)

    warning = filemode.open_access_warning(path)

    assert warning and path in warning and f"chmod 600 {path}" in warning


@posix_only
@pytest.mark.parametrize("mode", [0o600, 0o400, 0o700, 0o500])
def test_a_private_file_is_not_reported(tmp_path, mode):  # WARN-01
    assert filemode.open_access_warning(make(tmp_path, "secret.json", mode)) is None


def test_a_missing_file_is_not_reported(tmp_path):  # WARN-01
    assert filemode.open_access_warning(str(tmp_path / "absent.json")) is None


def test_windows_is_skipped(tmp_path, monkeypatch):  # WARN-01
    path = make(tmp_path, "secret.json", 0o666)
    monkeypatch.setattr(sys, "platform", "win32")

    assert filemode.open_access_warning(path) is None


@posix_only
def test_the_warning_is_logged_once_per_state(tmp_path, caplog):  # WARN-01
    path = make(tmp_path, "secret.json", 0o644)
    filemode._reported.clear()

    with caplog.at_level(logging.WARNING, logger="filemode"):
        for _ in range(3):
            filemode.warn_if_open(path)
        os.chmod(path, 0o600)
        filemode.warn_if_open(path)
        os.chmod(path, 0o666)
        filemode.warn_if_open(path)

    assert len([r for r in caplog.records if path in r.getMessage()]) == 2   # abriu de novo depois de fechar


@posix_only
def test_monitor_listing_shows_the_warnings_of_both_files(tmp_path, monkeypatch):  # WARN-01
    profiles = make(tmp_path, "monitor_profiles.json", 0o644, json.dumps({"profiles": {"p": {"targets": ["me"]}}}))
    policy = make(tmp_path, "send_policy.json", 0o664)
    monkeypatch.setattr(monitor, "CONFIG_PATH", profiles)
    monkeypatch.setattr(send_policy, "POLICY_PATH", policy)

    out = monitor.list_profiles()

    assert out["success"] is True and len(out["warnings"]) == 2
    assert any(profiles in w for w in out["warnings"]) and any(policy in w for w in out["warnings"])


@posix_only
def test_monitor_listing_has_no_warnings_key_when_all_is_private(tmp_path, monkeypatch):  # WARN-01
    profiles = make(tmp_path, "monitor_profiles.json", 0o600, json.dumps({"profiles": {}}))
    monkeypatch.setattr(monitor, "CONFIG_PATH", profiles)
    monkeypatch.setattr(send_policy, "POLICY_PATH", str(tmp_path / "absent.json"))

    assert monitor.list_profiles() == {"success": True, "profiles": []}


@posix_only
def test_an_open_profiles_file_still_works(tmp_path, monkeypatch):  # WARN-01 (avisa, não bloqueia)
    profiles = make(tmp_path, "monitor_profiles.json", 0o644, json.dumps({"profiles": {"p": {"targets": ["me"]}}}))
    monkeypatch.setattr(monitor, "CONFIG_PATH", profiles)

    assert monitor.get_profile("p")[0].name == "p"


@posix_only
def test_an_open_send_policy_is_logged_and_still_applies(tmp_path, monkeypatch, caplog):  # WARN-01
    policy = make(tmp_path, "send_policy.json", 0o644, json.dumps({"mode": "denylist", "deny": ["120363@g.us"]}))
    monkeypatch.setattr(send_policy, "POLICY_PATH", policy)
    filemode._reported.clear()

    with caplog.at_level(logging.WARNING, logger="filemode"):
        loaded, problem = send_policy._load()

    assert problem is None and loaded["deny"] == ["120363@g.us"]
    assert any(policy in r.getMessage() for r in caplog.records)
