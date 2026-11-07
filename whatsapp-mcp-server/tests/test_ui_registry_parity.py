"""The registry against the code and the README: no setting the project does not have, none it has left out (CFG-10, CFG-11)."""
import glob
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import localconfig  # noqa: E402
import uisettings  # noqa: E402
from ui.core.registry import ReadOnly, all_sections  # noqa: E402
from ui.core.secrets import mask  # noqa: E402
from ui.sections.database import SECTION as DATABASE, values  # noqa: E402

SERVER = os.path.dirname(os.path.dirname(__file__))
ROOT = os.path.dirname(SERVER)
README = open(os.path.join(ROOT, "README.md")).read()


def code_that_reads_settings() -> str:
    """The project's own code, leaving out the lists of names (uisettings) and the interface's registry."""
    files = [f for f in glob.glob(os.path.join(SERVER, "*.py")) if os.path.basename(f) != "uisettings.py"]
    files += [os.path.join(SERVER, "ui", "launcher.py")]
    files += [f for f in glob.glob(os.path.join(ROOT, "whatsapp-bridge", "*.go")) if not f.endswith("_test.go") and not f.endswith("uisettings.go")]
    return "\n".join(open(f, encoding="utf-8").read() for f in files)


def all_fields(fields=None):
    for section in (all_sections() if fields is None else []):
        yield from walk(section.fields)


def walk(fields):
    for f in fields:
        yield f
        yield from walk(f.fields)


def test_registry_matches_code_and_readme():  # CFG-11
    code = code_that_reads_settings()
    declared = {f.env_var for f in all_fields() if f.env_var}

    for var in sorted(declared):
        assert f'"{var}"' in code, f"{var} is not read anywhere in the project's code"
        assert re.search(rf"\| `{var}` \|", README), f"{var} is not in the README table"
    editable = set(uisettings.EDITABLE_ENV)
    assert editable <= declared, f"editable variables with no field: {sorted(editable - declared)}"
    assert {v for v in declared if v not in editable} <= {"WHATSAPP_DB_ENCRYPTION", "WHATSAPP_DB_KEY_FILE"}   # shown, never edited


def test_no_field_that_can_be_edited_points_at_a_file_a_key_or_the_configuration_directory():  # CFG-12
    for section in all_sections():
        for f in walk(section.fields):
            if f.env_var and not f.readonly:
                assert not re.search(r"KEY|CONFIG|POLICY|AUDIT|DRAFTS|STATE|COMMAND|ALLOW_REMOTE", f.env_var), f.env_var


def test_every_variable_of_the_readme_that_names_a_secret_or_a_path_is_not_editable():  # CFG-11
    for var in re.findall(r"\| `(WHATSAPP_[A-Z_]+|WEBHOOK_SECRET)` \|", README):
        if re.search(r"KEY|CONFIG_DIR|SEND_POLICY|AUDIT_LOG|MONITOR_CONFIG|MONITOR_DRAFTS|CHANNEL_|ALLOW_REMOTE|UI_COMMAND|SECRET|OWN_NUMBER|ENCRYPTION", var):
            assert var not in uisettings.EDITABLE_ENV, var


def test_secret_like_field_names_must_be_declared_secret():  # CFG-10
    pattern = re.compile(r"token|secret|passw|(?:^|[_\s-])key(?:$|[_\s-])", re.I)

    for section in all_sections():
        for f in walk(section.fields):
            if pattern.search(f.key) or pattern.search(f.label):
                assert f.secret, f"{section.id}.{f.key} looks like a secret and is not declared one"


@pytest.mark.parametrize("example, section_id", [("send_policy.example.json", "send_policy"), ("monitor.example.json", "monitor")])
def test_every_secret_in_an_example_file_is_a_secret_field(example, section_id):  # CFG-10
    secret_keys = set()

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if re.search(r"token|secret", key, re.I):
                    secret_keys.add(key)
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)
    collect(json.load(open(os.path.join(SERVER, example))))
    section = next(s for s in all_sections() if s.id == section_id)
    secret_fields = {f.key for f in walk(section.fields) if f.secret}

    assert secret_keys and secret_keys <= secret_fields


def test_the_database_section_is_read_only_and_never_shows_key_material(tmp_path, monkeypatch):  # CFG-10
    key = "0123456789abcdef" * 4
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "db.key").write_text(key)
    (folder / "api_token").write_text("f" * 64)

    shown = values()
    on_screen = mask(DATABASE, shown)

    assert isinstance(DATABASE.storage, ReadOnly) and all(f.readonly for f in DATABASE.fields)
    for text in (json.dumps(shown), json.dumps(on_screen)):
        assert key not in text and "f" * 64 not in text
    assert on_screen["db_key"] == {"set": True} and on_screen["api_token"] == {"set": True}
    assert on_screen["config_dir"] == localconfig.config_dir()


def test_the_database_section_says_when_a_key_or_token_is_missing_and_where_it_expects_it(tmp_path):  # CFG-01
    shown = values()
    on_screen = mask(DATABASE, shown)

    assert on_screen["db_key"] == {"set": False} and on_screen["api_token"] == {"set": False}
    assert str(tmp_path / "config" / "db.key") in shown["location"] and str(tmp_path / "config" / "api_token") in shown["location"]


def test_a_key_in_the_environment_is_reported_without_its_value(monkeypatch):  # CFG-10
    monkeypatch.setenv("WHATSAPP_DB_KEY", "ab" * 32)

    shown = values()

    assert mask(DATABASE, shown)["db_key"] == {"set": True}
    assert "WHATSAPP_DB_KEY" in shown["location"] and "ab" * 32 not in json.dumps(shown)


def test_a_key_file_elsewhere_is_reported_where_it_is(tmp_path, monkeypatch):  # CFG-01
    elsewhere = tmp_path / "vault" / "my.key"
    elsewhere.parent.mkdir()
    elsewhere.write_text("x")
    monkeypatch.setenv("WHATSAPP_DB_KEY_FILE", str(elsewhere))

    shown = values()

    assert mask(DATABASE, shown)["db_key"] == {"set": True} and str(elsewhere) in shown["location"]


def test_the_encryption_state_follows_the_variable(monkeypatch):  # CFG-01
    assert values()["encryption"] == "ligada"
    monkeypatch.setenv("WHATSAPP_DB_ENCRYPTION", "off")
    assert values()["encryption"].startswith("desligada")


def test_value_default_and_origin_of_a_field_follow_the_environment_and_the_file():  # CFG-01
    from ui.core.envlayer import EnvLayer
    from ui.core.formflow import FormFlow
    from ui.core.store import ConfigStore

    section = next(s for s in all_sections() if any(f.env_var == "WHATSAPP_AUTO_DOWNLOAD_MAX_MB" for f in s.fields))
    key = next(f.key for f in section.fields if f.env_var == "WHATSAPP_AUTO_DOWNLOAD_MAX_MB")

    def flow_with(environ):
        flow = FormFlow(section, ConfigStore(), EnvLayer(environ=environ))
        flow.load()
        return flow

    plain = flow_with({})
    assert (plain.origin(key), plain.shown(key)) == ("default", section.field(key).default) and section.field(key).default == 25
    layer = EnvLayer(environ={})
    assert layer.save(section, {key: 50}, layer.load().version).ok
    from_file = flow_with({})
    assert (from_file.origin(key), from_file.shown(key), section.field(key).default) == ("file", 50, 25)
    from_env = flow_with({"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "10"})
    assert (from_env.origin(key), from_env.shown(key), from_env.is_locked(key)) == ("env", 10, True)


def test_every_field_reports_value_default_and_origin():  # CFG-01
    from ui.core.envlayer import EnvLayer
    from ui.core.formflow import FormFlow
    from ui.core.store import ConfigStore

    sections = all_sections()
    assert len(sections) >= 9
    for section in sections:
        flow = FormFlow(section, ConfigStore(), EnvLayer(environ={}))
        flow.load()
        for f in section.fields:
            assert flow.origin(f.key) in ("default", "file", "env"), (section.id, f.key)
            flow.shown(f.key)                                           # the value (or the default) can always be had; what it is, is held by the test above
            assert f.default is None or f.kind in ("bool", "int", "float", "text", "choice", "multichoice", "list", "records", "secret")
