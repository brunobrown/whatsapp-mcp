"""The sections kept in ui_settings.json: media, general and interface (CFG-01, CFG-02, CFG-04, CFG-11)."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import drafts  # noqa: E402
import uisettings  # noqa: E402
from ui import launcher  # noqa: E402
from ui.core.envlayer import SCOPES, EnvLayer  # noqa: E402
from ui.core.registry import EnvKeys, Problem, default  # noqa: E402
from ui.core.store import ABSENT  # noqa: E402
from ui.sections import envsettings  # noqa: E402
from ui.sections.envsettings import GENERAL, INTERFACE, MEDIA  # noqa: E402

BRIDGE_MAIN = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "whatsapp-bridge", "main.go")).read()
SECTIONS = (MEDIA, GENERAL, INTERFACE)


def saved_environment(section, changes):
    """What a process would find in its environment after the interface saved changes."""
    layer = EnvLayer(environ={})
    result = layer.save(section, changes, layer.load().version)
    assert result.ok, result.errors
    environ = {}
    uisettings.apply(environ)
    return environ


def test_the_sections_are_registered_and_kept_as_environment_variables():  # CFG-04
    for section in SECTIONS:
        assert section in default.sections() and isinstance(section.storage, EnvKeys)


def test_every_variable_is_one_both_processes_apply_and_has_a_known_reader():  # CFG-11
    for section in SECTIONS:
        for field in section.fields:
            assert field.env_var in uisettings.EDITABLE_ENV and field.env_var in SCOPES


def test_the_readers_are_the_processes_the_readme_names():  # CFG-08
    readme = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "README.md")).read()
    for section in SECTIONS:
        for field in section.fields:
            if field.env_var.startswith("WHATSAPP_UI"):
                continue                      # these belong to this interface and are documented with it
            row = re.search(rf"\| `{field.env_var}` \| ([^|]+) \|", readme)
            assert row, f"{field.env_var} is not in the README table"
            assert ("bridge" if SCOPES[field.env_var] == "bridge" else "MCP server") in row.group(1), field.env_var


def test_the_media_defaults_are_the_bridges_defaults():  # CFG-01
    assert MEDIA.field("auto_download_max_mb").default == int(re.search(r"const defaultAutoDownloadMaxMB = (\d+)", BRIDGE_MAIN).group(1))
    assert ",".join(MEDIA.field("auto_download_types").default) == re.search(r'types := "([a-z,]+)"', BRIDGE_MAIN).group(1)
    assert MEDIA.field("auto_download").default is True and MEDIA.field("media_retention_days").default is None


def test_the_media_types_are_the_ones_the_bridge_knows():  # CFG-11
    assert set(MEDIA.field("auto_download_types").options) == {"image", "video", "audio", "document"}


def test_the_draft_validity_has_the_limits_of_the_code_that_reads_it():  # CFG-02, CFG-11
    field = GENERAL.field("draft_ttl_hours")

    assert field.default == drafts.DEFAULT_TTL_HOURS and field.bounds == (1, drafts.MAX_TTL_HOURS)


def test_a_saved_validity_is_the_one_the_draft_code_then_uses(monkeypatch):  # CFG-04
    environ = saved_environment(GENERAL, {"draft_ttl_hours": 48})
    monkeypatch.setenv("WHATSAPP_MONITOR_DRAFT_TTL_HOURS", environ["WHATSAPP_MONITOR_DRAFT_TTL_HOURS"])

    assert drafts.ttl().total_seconds() == 48 * 3600


def test_a_saved_time_zone_is_the_one_the_server_then_uses(monkeypatch):  # CFG-04
    import whatsapp
    environ = saved_environment(GENERAL, {"timezone": "America/Recife"})
    monkeypatch.setenv("WHATSAPP_TIMEZONE", environ["WHATSAPP_TIMEZONE"])

    assert str(whatsapp._default_zone()) == "America/Recife"


@pytest.mark.parametrize("zone, ok", [("America/Recife", True), ("UTC", True), ("Nowhere/Land", False), ("../etc/passwd", False), ("", True)])
def test_the_time_zone_must_be_a_known_name(zone, ok):  # CFG-02
    problems = GENERAL.validate({"timezone": zone})

    assert (problems == []) is ok and (ok or problems[0].field == "timezone")


def test_saved_media_settings_are_written_as_the_text_the_bridge_reads():  # CFG-04
    environ = saved_environment(MEDIA, {"auto_download": False, "auto_download_max_mb": 50, "auto_download_types": ["image", "video"],
                                        "media_retention_days": 30, "keep_deleted_content": True, "log_messages": True})

    assert environ == {"WHATSAPP_AUTO_DOWNLOAD": "false", "WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50", "WHATSAPP_AUTO_DOWNLOAD_TYPES": "image,video",
                       "WHATSAPP_MEDIA_RETENTION_DAYS": "30", "WHATSAPP_KEEP_DELETED_CONTENT": "true", "WHATSAPP_LOG_MESSAGES": "true"}


@pytest.mark.parametrize("changes", [{"auto_download_max_mb": 0}, {"media_retention_days": 0}, {"auto_download_types": ["gif"]},
                                      {"keep_deleted_content": "yes"}])
def test_a_media_value_out_of_range_is_refused_and_nothing_is_written(tmp_path, changes):  # CFG-02
    layer = EnvLayer(environ={})

    assert layer.save(MEDIA, changes, ABSENT).ok is False
    assert not (tmp_path / "config" / "ui_settings.json").exists()


def test_the_interface_defaults_are_the_launchers():  # CFG-01
    assert (INTERFACE.field("host").default, INTERFACE.field("port").default) == (launcher.DEFAULT_HOST, launcher.DEFAULT_PORT)
    assert INTERFACE.field("port").bounds == (launcher.MIN_PORT, launcher.MAX_PORT)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", ""])
def test_a_loopback_host_is_accepted(host):  # CFG-02
    assert INTERFACE.validate({"host": host}) == []


def test_a_remote_host_is_refused_unless_the_process_was_started_allowing_it(monkeypatch):  # CFG-02
    monkeypatch.setattr(envsettings, "environ", {})
    problems = INTERFACE.validate({"host": "0.0.0.0"})

    assert [p.field for p in problems] == ["host"] and "WHATSAPP_UI_ALLOW_REMOTE" in problems[0].message
    monkeypatch.setattr(envsettings, "environ", {"WHATSAPP_UI_ALLOW_REMOTE": "true"})
    assert INTERFACE.validate({"host": "0.0.0.0"}) == []


def test_a_host_the_launcher_would_refuse_cannot_be_saved(monkeypatch, tmp_path):  # CFG-02
    monkeypatch.setattr(envsettings, "environ", {})
    layer = EnvLayer(environ={})

    result = layer.save(INTERFACE, {"host": "0.0.0.0"}, ABSENT)

    assert result.ok is False and result.errors[0] == INTERFACE.validate({"host": "0.0.0.0"})[0]
    assert not (tmp_path / "config" / "ui_settings.json").exists()


def test_a_saved_port_is_the_one_the_launcher_then_uses():  # CFG-04
    environ = saved_environment(INTERFACE, {"port": 9123, "host": "localhost"})

    settings = launcher.resolve(environ)

    assert (settings.host, settings.port) == ("localhost", 9123)


def test_a_port_below_1024_is_refused():  # CFG-02
    assert EnvLayer(environ={}).save(INTERFACE, {"port": 80}, ABSENT).errors[0].field == "port"


def test_the_options_that_weaken_privacy_or_exposure_carry_a_warning():  # CFG-01
    assert MEDIA.field("keep_deleted_content").risk and MEDIA.field("log_messages").risk and INTERFACE.field("host").risk


def test_the_warning_applies_to_the_value_that_weakens_not_to_the_safe_one():  # CFG-01
    assert MEDIA.field("keep_deleted_content").risky(True) and not MEDIA.field("keep_deleted_content").risky(False)
    assert MEDIA.field("log_messages").risky(True) and not MEDIA.field("log_messages").risky(False)
