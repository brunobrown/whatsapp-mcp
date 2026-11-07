"""ui_settings.json: variables the UI saved are applied as defaults at start; the environment always wins (CFG-04)."""
import ast
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import uisettings  # noqa: E402


def write(tmp_path, data, mode=0o600):
    path = tmp_path / "config" / uisettings.FILE_NAME
    path.parent.mkdir(exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    os.chmod(path, mode)
    return path


def settings(env):
    return {"version": 1, "env": env}


def test_a_variable_missing_from_the_environment_is_filled_from_the_file(tmp_path):
    write(tmp_path, settings({"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50"}))
    environ = {}

    result = uisettings.apply(environ)

    assert environ == {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50"}
    assert result.applied == ["WHATSAPP_AUTO_DOWNLOAD_MAX_MB"]


def test_the_environment_wins_over_the_file(tmp_path):
    write(tmp_path, settings({"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50", "WHATSAPP_TIMEZONE": "America/Recife"}))
    environ = {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "10"}

    result = uisettings.apply(environ)

    assert environ == {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "10", "WHATSAPP_TIMEZONE": "America/Recife"}
    assert result.applied == ["WHATSAPP_TIMEZONE"]


@pytest.mark.parametrize("name", ["WHATSAPP_DB_KEY", "WHATSAPP_DB_KEY_FILE", "WHATSAPP_CONFIG_DIR",
                                  "WHATSAPP_DB_ENCRYPTION", "WHATSAPP_SEND_POLICY", "WHATSAPP_MONITOR_CONFIG",
                                  "WHATSAPP_SEND_AUDIT_LOG", "WHATSAPP_UI_ALLOW_REMOTE", "PATH", "HOME"])
def test_a_variable_outside_the_editable_list_is_never_applied(tmp_path, name):
    write(tmp_path, settings({name: "x"}))
    environ = {}

    result = uisettings.apply(environ)

    assert environ == {}
    assert name in result.ignored_keys


def test_a_value_that_is_not_text_is_not_applied(tmp_path):
    write(tmp_path, settings({"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": 50, "WHATSAPP_TIMEZONE": ["x"]}))
    environ = {}

    result = uisettings.apply(environ)

    assert environ == {}
    assert sorted(result.ignored_keys) == ["WHATSAPP_AUTO_DOWNLOAD_MAX_MB", "WHATSAPP_TIMEZONE"]


def test_no_file_changes_nothing(tmp_path):
    environ = {"A": "1"}

    result = uisettings.apply(environ)

    assert environ == {"A": "1"} and result.applied == [] and result.problem is None


@pytest.mark.parametrize("content", ["{not json", "[]", '{"env": []}', '{"version": 1}'])
def test_a_file_that_cannot_be_used_is_ignored_with_the_reason(tmp_path, content):
    write(tmp_path, content)
    environ = {}

    result = uisettings.apply(environ)

    assert environ == {} and result.applied == []
    if content == '{"version": 1}':
        assert result.problem is None      # no env section: nothing to apply, nothing wrong
    else:
        assert result.problem and uisettings.FILE_NAME in result.problem


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_a_file_that_other_users_can_read_is_ignored(tmp_path):
    write(tmp_path, settings({"WHATSAPP_TIMEZONE": "America/Recife"}), mode=0o644)
    environ = {}

    result = uisettings.apply(environ)

    assert environ == {}
    assert "chmod 600" in result.problem


def test_the_content_of_the_file_is_never_in_the_problem_text(tmp_path):
    write(tmp_path, '{"env": {"WHATSAPP_TIMEZONE": "secret-value-123" ')

    result = uisettings.apply({})

    assert "secret-value-123" not in result.problem


def test_the_editable_list_leaves_out_secrets_paths_and_the_remote_opt_in():
    forbidden = {"WHATSAPP_DB_KEY", "WHATSAPP_DB_KEY_FILE", "WHATSAPP_CONFIG_DIR", "WHATSAPP_DB_ENCRYPTION",
                 "WHATSAPP_SEND_POLICY", "WHATSAPP_SEND_AUDIT_LOG", "WHATSAPP_MONITOR_CONFIG",
                 "WHATSAPP_MONITOR_AUDIT_LOG", "WHATSAPP_MONITOR_DRAFTS", "WHATSAPP_CHANNEL_CONFIG",
                 "WHATSAPP_CHANNEL_STATE", "WHATSAPP_UI_ALLOW_REMOTE", "WEBHOOK_SECRET", "WHATSAPP_OWN_NUMBER"}

    assert forbidden.isdisjoint(uisettings.EDITABLE_ENV)
    assert {"WHATSAPP_AUTO_DOWNLOAD", "WHATSAPP_MCP_MODE", "WHATSAPP_SESSION_TAINT", "WHATSAPP_UI_PORT"} <= set(uisettings.EDITABLE_ENV)


def test_main_applies_the_file_first_and_only_when_it_is_the_program():
    tree = ast.parse(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py")).read())
    body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    first_project_import = next(i for i, n in enumerate(body) if isinstance(n, ast.Import)
                                and any(a.name in ("monitor", "channel", "whatsapp") for a in n.names)
                                or isinstance(n, ast.ImportFrom) and n.module == "whatsapp")
    names = [ast.unparse(n) for n in body[:first_project_import]]

    assert "import uisettings" in names
    guard = next(n for n in body[:first_project_import] if isinstance(n, ast.If))
    assert "__name__" in ast.unparse(guard.test) and "uisettings.apply()" in ast.unparse(guard)
