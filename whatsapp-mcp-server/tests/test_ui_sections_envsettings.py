"""Environment sections (CFG-01, CFG-04, CFG-08, CFG-11): media, privacy, time zone and interface; parity with the code
and the README for every editable variable."""
import json
import os
import re
from pathlib import Path

import pytest

import uisettings
from ui.config.envlayer import SETTINGS_ID, EnvLayer
from ui.config.form import FormFlow
from ui.config.registry import EnvKeys, JsonFile, ReadOnly, Registry
from ui.config.store import ConfigStore
from ui.sections import envsettings

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()
FORBIDDEN = {"WHATSAPP_CONFIG_DIR", "WHATSAPP_DB_KEY", "WHATSAPP_DB_KEY_FILE", "WHATSAPP_DB_ENCRYPTION",
             "WHATSAPP_UI_ALLOW_REMOTE", "WHATSAPP_UI_COMMAND", "WHATSAPP_OWN_NUMBER", "WEBHOOK_SECRET"}


@pytest.fixture(scope="module")
def registry():
    reg = Registry()
    reg.discover("ui.sections")
    return reg


def env_fields(registry):
    return [(s, f) for s in registry.sections() if isinstance(s.storage, EnvKeys) for f in s.fields]


# -- parity --------------------------------------------------------------------------------------
def test_every_editable_variable_appears_in_exactly_one_section(registry):
    seen = {}
    for section, f in env_fields(registry):
        seen.setdefault(f.env_var, []).append(f"{section.id}.{f.key}")
    assert sorted(seen) == sorted(uisettings.EDITABLE_ENV)
    assert all(len(places) == 1 for places in seen.values()), seen


def test_no_environment_field_lacks_a_real_variable(registry):
    for section, f in env_fields(registry):
        assert f.env_var in uisettings.EDITABLE_ENV, f"{section.id}.{f.key}"
        assert f"`{f.env_var}`" in README, f"{f.env_var} is not in the README"


def test_every_editable_variable_is_in_the_readme_configuration_table():
    table = README.split("## Configuration", 1)[1].split("\n## ", 1)[0]
    for var in uisettings.EDITABLE_ENV:
        assert re.search(rf"^\| `{var}` \|", table, re.M), var


def test_secrets_paths_and_the_configuration_directory_are_never_editable(registry):
    assert not FORBIDDEN & set(uisettings.EDITABLE_ENV)
    for var in uisettings.EDITABLE_ENV:
        assert not re.search(r"(KEY|TOKEN|SECRET|PASSWORD|PATH|FILE|DIR|COMMAND|REMOTE)$", var), var
    for section, f in env_fields(registry):
        assert f.kind != "secret" and f.env_var not in FORBIDDEN, f"{section.id}.{f.key}"


def test_every_environment_section_has_one_restart_scope_of_the_process_that_reads_it(registry):
    scopes = {s.id: s.restart for s in registry.sections() if isinstance(s.storage, EnvKeys)}
    assert scopes == {"agent_safety": "server", "media": "bridge", "privacy": "bridge", "timezone": "server", "interface": "bridge"}


def test_every_json_field_is_a_key_the_project_reads(registry):
    """No field of a file section is invented: each key is in the code that reads the file."""
    sources = {
        "send_policy": ["send_policy.py", "taint.py"],
        "monitor": ["monitor.py"],
        "channel": ["channel.py"],
        "webhooks": ["../whatsapp-bridge/webhook.go"],
    }
    server = Path(__file__).resolve().parents[1]
    for section in registry.sections():
        if not isinstance(section.storage, JsonFile):
            continue
        text = "".join((server / name).read_text() for name in sources[section.id])
        keys = []
        for f in section.fields:
            keys += [f.key] if f.kind != "records" else [sub.key for sub in f.fields]
        for key in keys:
            file_key = key
            if section.id == "monitor":
                file_key = re.sub(r"^limit_", "", re.sub(r"^senders_", "", key))
            assert file_key in text, f"{section.id}.{key} is not in the code that reads the file"


def test_the_registry_lists_every_section_in_one_order_without_the_settings_file(registry):
    ids = [s.id for s in registry.sections()]
    assert ids == ["send_policy", "monitor", "webhooks", "channel", "agent_safety", "database", "media", "privacy",
                   "timezone", "interface"]
    assert SETTINGS_ID not in ids


def test_defaults_match_the_bridge():
    source = (ROOT / "whatsapp-bridge" / "main.go").read_text()
    default_mb = int(re.search(r"defaultAutoDownloadMaxMB = (\d+)", source).group(1))
    types = re.search(r'types := "([a-z,]+)"', source).group(1).split(",")
    media = envsettings.MEDIA
    assert media.field("auto_download_max_mb").default == default_mb
    assert media.field("auto_download_types").default == types
    assert set(media.field("auto_download_types").options) >= set(types)


# -- behaviour -----------------------------------------------------------------------------------
@pytest.fixture
def ctx(tmp_path):
    reg = Registry()
    reg.discover("ui.sections")
    store = ConfigStore(reg)
    environ = {}
    layer = EnvLayer(reg, store, environ=environ, runtime_dir=str(tmp_path / "runtime"))

    def flow(section_id):
        f = FormFlow(section_id, reg, store, layer)
        f.load()
        return f

    return flow, environ


def saved_env():
    with open(uisettings.path()) as f:
        return json.load(f)["env"]


def test_media_values_are_saved_as_the_variables_the_bridge_reads(ctx):
    flow, _ = ctx
    f = flow("media")
    f.apply({"auto_download": False, "auto_download_max_mb": "12,5", "auto_download_types": ["image", "video"],
             "media_retention_days": "30"})
    outcome = f.save()
    assert outcome.ok and outcome.restarts == ["bridge"]
    assert saved_env() == {"WHATSAPP_AUTO_DOWNLOAD": "false", "WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "12.5",
                           "WHATSAPP_AUTO_DOWNLOAD_TYPES": "image,video", "WHATSAPP_MEDIA_RETENTION_DAYS": "30"}


def test_media_limits_are_enforced(ctx):
    flow, _ = ctx
    for changes, key in (({"auto_download_max_mb": "0"}, "auto_download_max_mb"), ({"media_retention_days": "0"}, "media_retention_days"),
                         ({"auto_download_types": ["image", "gif"]}, "auto_download_types"), ({"media_retention_days": "x"}, "media_retention_days")):
        f = flow("media")
        f.apply(changes)
        outcome = f.save()
        assert outcome.ok is False and key in outcome.errors, changes
    assert not os.path.exists(uisettings.path())


def test_an_empty_retention_goes_back_to_keeping_files(ctx):
    flow, _ = ctx
    f = flow("media")
    f.apply({"media_retention_days": "30"})
    assert f.save().ok
    g = flow("media")
    g.apply({"media_retention_days": ""})
    assert g.save().ok
    assert "WHATSAPP_MEDIA_RETENTION_DAYS" not in saved_env()


def test_privacy_switches_warn_and_save(ctx):
    flow, _ = ctx
    for key in ("keep_deleted_content", "log_messages"):
        field = envsettings.PRIVACY.field(key)
        assert field.risk and field.risk_when == (True,)
    f = flow("privacy")
    f.apply({"log_messages": True})
    assert f.save().ok
    assert saved_env() == {"WHATSAPP_LOG_MESSAGES": "true"}


def test_the_time_zone_must_be_an_iana_name_and_restarts_the_server(ctx):
    flow, _ = ctx
    f = flow("timezone")
    f.apply({"timezone": "Mars/Olympus"})
    outcome = f.save()
    assert outcome.ok is False and "timezone" in outcome.errors
    g = flow("timezone")
    g.apply({"timezone": "America/Recife"})
    outcome = g.save()
    assert outcome.ok and outcome.restarts == ["server"]
    assert saved_env() == {"WHATSAPP_TIMEZONE": "America/Recife"}


def test_interface_port_range_and_host_form(ctx):
    flow, _ = ctx
    for changes, key in (({"ui_port": "80"}, "ui_port"), ({"ui_port": "70000"}, "ui_port"), ({"ui_host": "a b"}, "ui_host"),
                         ({"ui_host": "http://x"}, "ui_host")):
        f = flow("interface")
        f.apply(changes)
        outcome = f.save()
        assert outcome.ok is False and key in outcome.errors, changes
    f = flow("interface")
    f.apply({"ui_port": "9000", "ui": False})
    outcome = f.save()
    assert outcome.ok and outcome.restarts == ["bridge"]
    assert saved_env() == {"WHATSAPP_UI_PORT": "9000", "WHATSAPP_UI": "false"}


def test_a_variable_set_in_the_environment_is_locked_and_names_the_variable(ctx):
    flow, environ = ctx
    environ["WHATSAPP_UI_PORT"] = "9100"
    f = flow("interface")
    view = f.view()
    assert view.values["ui_port"] == 9100 and view.state["ui_port"]["locked"] and view.state["ui_port"]["origin"] == "environment"
    f.apply({"ui_port": "9200"})
    outcome = f.save()
    assert outcome.ok is False and "WHATSAPP_UI_PORT" in outcome.errors["ui_port"]
