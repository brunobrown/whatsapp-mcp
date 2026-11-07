"""Variables kept in ui_settings.json: where each value comes from, what is locked, what needs a restart (CFG-01, CFG-04, CFG-08)."""
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import uisettings  # noqa: E402
from ui.core.envlayer import EnvLayer  # noqa: E402
from ui.core.registry import EnvKeys, Field, Problem, Section  # noqa: E402
from ui.core.store import ABSENT  # noqa: E402

TYPES = ("image", "video", "audio", "document")


def media(validate=lambda values: []):
    return Section(id="media", title="Media", icon="x", order=1, storage=EnvKeys(), validate=validate, fields=(
        Field("auto", "Auto-download", "bool", default=True, env_var="WHATSAPP_AUTO_DOWNLOAD"),
        Field("max_mb", "Largest file (MB)", "int", default=25, bounds=(1, 2048), env_var="WHATSAPP_AUTO_DOWNLOAD_MAX_MB"),
        Field("types", "Types", "multichoice", options=TYPES, default=["image", "document", "audio"],
              env_var="WHATSAPP_AUTO_DOWNLOAD_TYPES"),
        Field("zone", "Time zone", "text", env_var="WHATSAPP_TIMEZONE"),
        Field("encryption", "Database encryption", "text", env_var="WHATSAPP_DB_ENCRYPTION", readonly=True)))


def settings_path(tmp_path):
    return tmp_path / "config" / "ui_settings.json"


def write_settings(tmp_path, env):
    path = settings_path(tmp_path)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"version": 1, "env": env}))
    os.chmod(path, 0o600)


def file_env(tmp_path):
    return json.loads(settings_path(tmp_path).read_text())["env"]


def state(layer, key, section=None):
    return next(s for s in layer.state(section or media()) if s.key == key)


def test_every_editable_variable_has_a_process_that_reads_it():
    layer = EnvLayer(environ={})

    assert {layer.scope_of(v) for v in uisettings.EDITABLE_ENV} <= {"bridge", "server"}
    assert layer.scope_of("WHATSAPP_TIMEZONE") == "server" and layer.scope_of("WHATSAPP_AUTO_DOWNLOAD") == "bridge"
    assert layer.scope_of("WHATSAPP_MCP_MODE") == "server" and layer.scope_of("WHATSAPP_UI_PORT") == "bridge"


def test_the_origin_is_the_environment_then_the_file_then_the_default(tmp_path):  # CFG-01
    write_settings(tmp_path, {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50", "WHATSAPP_TIMEZONE": "America/Recife"})
    layer = EnvLayer(environ={"WHATSAPP_TIMEZONE": "UTC"})

    assert layer.origin("WHATSAPP_TIMEZONE") == "env"
    assert layer.origin("WHATSAPP_AUTO_DOWNLOAD_MAX_MB") == "file"
    assert layer.origin("WHATSAPP_AUTO_DOWNLOAD") == "default"


def test_a_variable_set_but_empty_still_counts_as_the_environment(tmp_path):  # CFG-01
    write_settings(tmp_path, {"WHATSAPP_TIMEZONE": "America/Recife"})

    assert EnvLayer(environ={"WHATSAPP_TIMEZONE": ""}).origin("WHATSAPP_TIMEZONE") == "env"


def test_the_state_of_a_field_has_its_value_default_origin_and_variable(tmp_path):  # CFG-01
    write_settings(tmp_path, {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50", "WHATSAPP_AUTO_DOWNLOAD": "false",
                              "WHATSAPP_AUTO_DOWNLOAD_TYPES": "image, video"})
    layer = EnvLayer(environ={})

    size, auto, types, zone = (state(layer, k) for k in ("max_mb", "auto", "types", "zone"))

    assert (size.value, size.default, size.origin, size.env_var) == (50, 25, "file", "WHATSAPP_AUTO_DOWNLOAD_MAX_MB")
    assert (auto.value, auto.default, auto.origin) == (False, True, "file")
    assert (types.value, types.default) == (["image", "video"], ["image", "document", "audio"])
    assert (zone.value, zone.default, zone.origin) == (None, None, "default")


def test_a_value_from_the_environment_wins_and_is_locked_with_the_variable_named(tmp_path):  # CFG-04
    write_settings(tmp_path, {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50"})
    layer = EnvLayer(environ={"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "10"})

    size = state(layer, "max_mb")

    assert (size.value, size.origin, size.locked, size.env_var) == (10, "env", True, "WHATSAPP_AUTO_DOWNLOAD_MAX_MB")


def test_a_read_only_field_is_locked_whatever_its_origin():
    layer = EnvLayer(environ={"WHATSAPP_DB_ENCRYPTION": "on"})

    enc = state(layer, "encryption")

    assert enc.locked and enc.readonly and enc.value == "on"


def test_a_value_that_cannot_be_read_as_its_type_shows_the_default(tmp_path):
    write_settings(tmp_path, {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "lots", "WHATSAPP_AUTO_DOWNLOAD": "maybe"})
    layer = EnvLayer(environ={})

    assert state(layer, "max_mb").value == 25 and state(layer, "auto").value is True


def test_save_writes_text_values_in_a_private_file(tmp_path):  # CFG-04
    layer = EnvLayer(environ={})
    version = layer.load().version

    result = layer.save(media(), {"max_mb": 50, "auto": False, "types": ["image", "audio"]}, version)

    assert result.ok
    assert file_env(tmp_path) == {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50", "WHATSAPP_AUTO_DOWNLOAD": "false",
                                  "WHATSAPP_AUTO_DOWNLOAD_TYPES": "image,audio"}
    assert json.loads(settings_path(tmp_path).read_text())["version"] == 1
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(settings_path(tmp_path)).st_mode) == 0o600
    assert state(layer, "max_mb").value == 50


def test_save_keeps_the_variables_of_other_sections_and_removes_a_value_sent_as_none(tmp_path):  # CFG-04
    write_settings(tmp_path, {"WHATSAPP_TIMEZONE": "America/Recife", "WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50"})
    layer = EnvLayer(environ={})

    result = layer.save(media(), {"max_mb": None}, layer.load().version)

    assert result.ok and file_env(tmp_path) == {"WHATSAPP_TIMEZONE": "America/Recife"}
    assert layer.origin("WHATSAPP_AUTO_DOWNLOAD_MAX_MB") == "default"


@pytest.mark.parametrize("changes", [{"max_mb": 0}, {"max_mb": "50"}, {"max_mb": True}, {"auto": "yes"},
                                      {"types": ["gif"]}, {"zone": "x\ny"}])
def test_save_refuses_a_value_the_field_does_not_accept_and_writes_nothing(tmp_path, changes):  # CFG-02
    layer = EnvLayer(environ={})

    result = layer.save(media(), changes, layer.load().version)

    assert result.ok is False and result.errors and result.errors[0].field in changes
    assert not settings_path(tmp_path).exists()


def test_save_refuses_a_variable_that_is_not_editable_or_a_read_only_field(tmp_path):  # CFG-11
    layer = EnvLayer(environ={})

    assert layer.save(media(), {"encryption": "off"}, ABSENT).errors[0].field == "encryption"
    with pytest.raises(KeyError):
        layer.save(media(), {"nonexistent": "x"}, ABSENT)
    assert not settings_path(tmp_path).exists()


def test_save_refuses_to_change_a_field_the_environment_defines(tmp_path):  # CFG-04
    layer = EnvLayer(environ={"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "10"})

    result = layer.save(media(), {"max_mb": 50}, layer.load().version)

    assert result.ok is False and "WHATSAPP_AUTO_DOWNLOAD_MAX_MB" in result.errors[0].message
    assert not settings_path(tmp_path).exists()


def test_the_section_validator_runs_on_the_values_that_would_result(tmp_path):  # CFG-02
    seen = []

    def validator(values):
        seen.append(dict(values))
        return [Problem("zone", "fuso desconhecido")] if values.get("zone") == "Nowhere/Land" else []
    write_settings(tmp_path, {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50"})
    layer = EnvLayer(environ={})

    result = layer.save(media(validator), {"zone": "Nowhere/Land"}, layer.load().version)

    assert result.errors == [Problem("zone", "fuso desconhecido")]
    assert seen[-1]["max_mb"] == 50 and seen[-1]["zone"] == "Nowhere/Land"
    assert "WHATSAPP_TIMEZONE" not in file_env(tmp_path)


def test_save_refuses_when_the_file_changed_after_it_was_loaded(tmp_path):  # CFG-05
    write_settings(tmp_path, {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50"})
    layer = EnvLayer(environ={})
    version = layer.load().version
    write_settings(tmp_path, {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "75"})

    result = layer.save(media(), {"max_mb": 10}, version)

    assert result.conflict and file_env(tmp_path) == {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "75"}


def test_a_change_to_a_bridge_variable_is_pending_until_the_interface_restarts_with_the_bridge(tmp_path):  # CFG-08
    layer = EnvLayer(environ={})
    layer.save(media(), {"max_mb": 50}, layer.load().version)

    assert layer.pending() == {"bridge": ["WHATSAPP_AUTO_DOWNLOAD_MAX_MB"], "server": []}
    assert state(layer, "max_mb").pending_restart == "bridge"
    restarted = EnvLayer(environ={})           # the bridge started again, and with it the interface
    assert restarted.pending() == {"bridge": [], "server": []}
    assert state(restarted, "max_mb").pending_restart is None


def test_a_value_the_environment_overrides_is_never_pending(tmp_path):  # CFG-08
    layer = EnvLayer(environ={})
    layer.save(media(), {"max_mb": 50}, layer.load().version)

    overridden = EnvLayer(environ={"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "10"}, baseline={})

    assert overridden.pending()["bridge"] == []


def note(tmp_path, pid, file_env_value):
    folder = tmp_path / "config" / "runtime"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"server-{pid}.json"
    path.write_text(json.dumps({"pid": pid, "file_env": file_env_value}))
    return path


def test_a_running_server_that_read_an_older_value_makes_the_change_pending(tmp_path):  # CFG-08
    write_settings(tmp_path, {"WHATSAPP_TIMEZONE": "America/Recife"})
    note(tmp_path, os.getpid(), {"WHATSAPP_TIMEZONE": "UTC"})
    layer = EnvLayer(environ={})

    assert layer.pending()["server"] == ["WHATSAPP_TIMEZONE"]
    assert state(layer, "zone").pending_restart == "server"


def test_a_server_that_read_the_current_value_leaves_nothing_pending(tmp_path):  # CFG-08
    write_settings(tmp_path, {"WHATSAPP_TIMEZONE": "America/Recife"})
    note(tmp_path, os.getpid(), {"WHATSAPP_TIMEZONE": "America/Recife"})

    assert EnvLayer(environ={}).pending()["server"] == []


def test_a_server_that_read_nothing_and_a_variable_removed_since_both_count(tmp_path):  # CFG-08
    write_settings(tmp_path, {})
    note(tmp_path, os.getpid(), {"WHATSAPP_TIMEZONE": "UTC"})      # removed from the file after the server started

    assert EnvLayer(environ={}).pending()["server"] == ["WHATSAPP_TIMEZONE"]


def test_a_note_of_a_server_that_is_gone_is_ignored_and_removed(tmp_path):  # CFG-08
    write_settings(tmp_path, {"WHATSAPP_TIMEZONE": "America/Recife"})
    dead = 2 ** 22 + 12345
    path = note(tmp_path, dead, {"WHATSAPP_TIMEZONE": "UTC"})
    if sys.platform == "win32":
        pytest.skip("liveness is not checked on Windows")

    assert EnvLayer(environ={}).pending()["server"] == [] and not path.exists()


def test_a_damaged_note_is_ignored(tmp_path):
    write_settings(tmp_path, {"WHATSAPP_TIMEZONE": "America/Recife"})
    folder = tmp_path / "config" / "runtime"
    folder.mkdir(parents=True)
    (folder / "server-1.json").write_text("{not json")
    (folder / "server-abc.json").write_text("{}")
    (folder / "other.txt").write_text("x")

    assert EnvLayer(environ={}).pending()["server"] == []


def test_a_file_that_is_not_valid_is_reported_and_the_defaults_show(tmp_path):  # CFG-09
    path = settings_path(tmp_path)
    path.parent.mkdir()
    path.write_text("{broken")
    os.chmod(path, 0o600)
    layer = EnvLayer(environ={})

    assert layer.load().errors and state(layer, "max_mb").value == 25 and state(layer, "max_mb").origin == "default"
