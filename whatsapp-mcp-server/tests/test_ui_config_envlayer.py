"""Environment layer (CFG-04, CFG-08): editable variables in ui_settings.json with origin, lock and pending restart."""
import json
import os

import pytest

import uisettings
from ui.config.fields import Field, Problem
from ui.config.registry import EnvKeys, Registry, Section
from ui.config.store import ABSENT, ConfigStore
from ui.config.envlayer import EnvLayer

AUTO = "WHATSAPP_AUTO_DOWNLOAD"
MAX_MB = "WHATSAPP_AUTO_DOWNLOAD_MAX_MB"
TZ = "WHATSAPP_TIMEZONE"
MODE = "WHATSAPP_MCP_MODE"


def settings_path():
    return uisettings.path()


def write_settings(env, raw=None):
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(raw if raw is not None else json.dumps({"version": 1, "env": env}))
    os.chmod(path, 0o600)


def read_settings():
    with open(settings_path()) as f:
        return json.load(f)


def registry():
    reg = Registry()
    reg.register(Section(id="media", title="Media", order=1, storage=EnvKeys(), restart="bridge", fields=(
        Field("auto_download", "Auto download", "bool", default=False, env_var=AUTO),
        Field("max_mb", "Max MB", "int", default=20, bounds=(1, 500), env_var=MAX_MB))))
    reg.register(Section(id="agent", title="Agent", order=2, storage=EnvKeys(), restart="server", fields=(
        Field("timezone", "Time zone", "text", default="", env_var=TZ),
        Field("mode", "Mode", "choice", default="normal", options=("normal", "restricted"), env_var=MODE))))
    return reg


def layer(environ=None, baseline=None, runtime=None, reg=None):
    reg = reg or registry()
    return EnvLayer(reg, ConfigStore(reg), environ={} if environ is None else environ, baseline=baseline,
                    runtime_dir=runtime)


def states(lay, section="media"):
    return {s.key: s for s in lay.state(section)}


def test_a_variable_set_in_the_environment_is_locked_and_names_itself():
    s = states(layer({AUTO: "true"}))["auto_download"]
    assert s.locked is True and s.origin == "environment" and s.env_var == AUTO and s.value is True


def test_a_value_from_the_file_is_typed_and_marked_as_file():
    write_settings({MAX_MB: "25", AUTO: "true"})
    s = states(layer())
    assert s["max_mb"].value == 25 and s["max_mb"].origin == "file" and s["max_mb"].locked is False
    assert s["auto_download"].value is True


def test_a_variable_in_neither_place_shows_its_default():
    s = states(layer())["max_mb"]
    assert s.value == 20 and s.default == 20 and s.origin == "default" and s.locked is False


def test_the_environment_wins_over_the_file():
    write_settings({MAX_MB: "25"})
    s = states(layer({MAX_MB: "99"}))["max_mb"]
    assert s.value == 99 and s.origin == "environment" and s.locked


def test_a_value_that_cannot_be_read_falls_back_to_the_default():
    write_settings({MAX_MB: "lots"})
    s = states(layer())["max_mb"]
    assert s.value == 20


def test_a_corrupt_settings_file_is_ignored_with_a_reason_and_defaults_are_used():
    for raw in ("{not json", "[1]", json.dumps({"version": 1, "env": ["x"]}), json.dumps({"version": 1, "env": {AUTO: 5}})):
        write_settings({}, raw=raw)
        lay = layer()
        assert lay.problem and "ui_settings.json" in lay.problem
        assert states(lay)["max_mb"].value == 20 and states(lay)["max_mb"].origin == "default"
    write_settings({MAX_MB: "30"})
    assert layer().problem is None


def test_only_variables_in_the_editable_list_are_read_from_the_file():
    write_settings({MAX_MB: "30", "WHATSAPP_CONFIG_DIR": "/elsewhere", "OTHER": "x"}, raw=None)
    lay = layer()
    assert lay.file_env() == {MAX_MB: "30"}


def test_a_field_whose_variable_is_not_editable_is_refused_on_save_and_never_written():
    reg = Registry()
    reg.register(Section(id="bad", title="Bad", order=1, storage=EnvKeys(), restart="server", fields=(
        Field("dir", "Dir", "text", env_var="WHATSAPP_CONFIG_DIR"),)))
    lay = layer(reg=reg)
    result = lay.save("bad", {"dir": "/x"}, ABSENT)
    assert result.ok is False and result.errors[0].key == "dir" and "not editable" in result.errors[0].message
    assert not os.path.exists(settings_path())
    assert lay.state("bad")[0].locked is True


def test_saving_writes_the_variable_as_text_keeping_the_others():
    write_settings({TZ: "UTC"})
    lay = layer()
    result = lay.save("media", {"max_mb": 40, "auto_download": True}, lay.version())
    assert result.ok, result.errors
    assert read_settings() == {"version": 1, "env": {TZ: "UTC", MAX_MB: "40", AUTO: "true"}}
    assert oct(os.stat(settings_path()).st_mode & 0o777) == "0o600"


def test_saving_none_returns_a_variable_to_its_default():
    write_settings({MAX_MB: "40", TZ: "UTC"})
    lay = layer()
    assert lay.save("media", {"max_mb": None}, lay.version()).ok
    assert read_settings()["env"] == {TZ: "UTC"}


def test_a_variable_defined_by_the_environment_cannot_be_saved_and_names_itself():
    lay = layer({MAX_MB: "99"})
    result = lay.save("media", {"max_mb": 10}, lay.version())
    assert result.ok is False
    assert result.errors[0].key == "max_mb" and MAX_MB in result.errors[0].message
    assert not os.path.exists(settings_path())


def test_an_invalid_value_is_refused_with_the_reason_and_nothing_is_written():
    lay = layer()
    result = lay.save("media", {"max_mb": 9999, "auto_download": "yes"}, lay.version())
    assert result.ok is False
    assert {p.key for p in result.errors} == {"max_mb", "auto_download"}
    assert not os.path.exists(settings_path())


def test_an_unknown_field_key_is_refused_and_a_readonly_field_cannot_change():
    reg = Registry()
    reg.register(Section(id="media", title="M", order=1, storage=EnvKeys(), restart="bridge", fields=(
        Field("ui", "UI", "bool", env_var="WHATSAPP_UI", readonly=True),)))
    lay = layer(reg=reg)
    assert lay.save("media", {"ui": True}, lay.version()).errors[0].key == "ui"
    assert lay.save("media", {"nope": 1}, lay.version()).errors[0].key == "nope"
    assert lay.state("media")[0].locked is True


def test_the_section_validator_runs_on_the_resulting_values():
    reg = Registry()
    reg.register(Section(id="media", title="M", order=1, storage=EnvKeys(), restart="bridge",
                         validator=lambda v: [Problem("max_mb", "too big for this mode")] if v.get("max_mb", 0) > 100 else [],
                         fields=(Field("max_mb", "Max", "int", env_var=MAX_MB, bounds=(1, 500)),)))
    lay = layer(reg=reg)
    assert lay.save("media", {"max_mb": 200}, lay.version()).errors == [Problem("max_mb", "too big for this mode")]
    assert lay.save("media", {"max_mb": 50}, lay.version()).ok


def test_a_stale_version_is_a_conflict_and_the_file_is_untouched():
    write_settings({MAX_MB: "30"})
    lay = layer()
    version = lay.version()
    write_settings({MAX_MB: "31"})
    result = lay.save("media", {"max_mb": 40}, version)
    assert result.conflict is True
    assert read_settings()["env"] == {MAX_MB: "31"}


def test_a_corrupt_settings_file_is_not_replaced_without_confirmation():
    write_settings({}, raw="{broken")
    lay = layer()
    result = lay.save("media", {"max_mb": 40}, lay.version())
    assert result.needs_confirmation is True
    with open(settings_path()) as f:
        assert f.read() == "{broken"
    assert lay.save("media", {"max_mb": 40}, lay.version(), confirm_replace=True).ok


# -- pending restart ---------------------------------------------------------------------------------
def test_a_bridge_variable_is_pending_when_the_file_differs_from_what_the_interface_started_with():
    write_settings({MAX_MB: "25"})
    lay = layer(baseline={MAX_MB: "25"})
    assert lay.pending() == {"bridge": [], "server": []}
    assert lay.save("media", {"max_mb": 40}, lay.version()).ok
    assert lay.pending()["bridge"] == [MAX_MB]
    assert states(lay)["max_mb"].pending_restart == "bridge"
    assert states(lay)["auto_download"].pending_restart is None


def test_the_bridge_note_clears_when_the_value_is_back_to_what_the_bridge_has():
    write_settings({MAX_MB: "25"})
    lay = layer(baseline={MAX_MB: "25"})
    lay.save("media", {"max_mb": 40}, lay.version())
    lay.save("media", {"max_mb": 25}, lay.version())
    assert lay.pending()["bridge"] == []


def test_the_baseline_defaults_to_what_the_file_holds_when_the_layer_is_built():
    write_settings({MAX_MB: "25"})
    lay = layer()
    lay.save("media", {"max_mb": 40}, lay.version())
    assert lay.pending()["bridge"] == [MAX_MB]


def test_a_variable_the_environment_defines_is_never_pending():
    write_settings({MAX_MB: "25"})
    lay = layer({MAX_MB: "1"}, baseline={})
    assert lay.pending()["bridge"] == []


def note(tmp_path, pid, file_env):
    folder = tmp_path / "runtime"
    folder.mkdir(exist_ok=True)
    (folder / f"server-{pid}.json").write_text(json.dumps({"pid": pid, "file_env": file_env}))
    return str(folder)


def test_a_server_variable_stays_pending_while_a_running_server_holds_an_older_value(tmp_path):
    write_settings({TZ: "UTC"})
    runtime = note(tmp_path, os.getpid(), {TZ: "America/Sao_Paulo"})
    lay = layer(runtime=runtime)
    assert lay.pending()["server"] == [TZ]
    assert states(lay, "agent")["timezone"].pending_restart == "server"
    # still pending on a later look: nothing clears it but the restart
    assert layer(runtime=runtime).pending()["server"] == [TZ]


def test_the_note_clears_when_the_server_is_seen_restarted_with_the_new_value(tmp_path):
    write_settings({TZ: "UTC"})
    runtime = note(tmp_path, os.getpid(), {TZ: "America/Sao_Paulo"})
    lay = layer(runtime=runtime)
    assert lay.pending()["server"] == [TZ]
    note(tmp_path, os.getpid(), {TZ: "UTC"})
    assert lay.pending()["server"] == []


def test_the_note_of_a_server_that_is_gone_is_ignored_and_removed(tmp_path):
    write_settings({TZ: "UTC"})
    dead = 2 ** 22 + 12345
    runtime = note(tmp_path, dead, {TZ: "old"})
    assert layer(runtime=runtime).pending()["server"] == []
    assert not os.path.exists(os.path.join(runtime, f"server-{dead}.json"))


def test_unreadable_server_notes_are_skipped(tmp_path):
    write_settings({TZ: "UTC"})
    runtime = note(tmp_path, os.getpid(), {TZ: "old"})
    for name, body in (("server-1.json", "{nope"), ("server-2.json", json.dumps({"pid": "x", "file_env": {}})),
                       ("server-3.json", json.dumps({"pid": True, "file_env": {}})), ("other.json", "{}")):
        with open(os.path.join(runtime, name), "w") as f:
            f.write(body)
    assert layer(runtime=runtime).pending()["server"] == [TZ]


def test_with_no_runtime_folder_nothing_is_pending_for_the_server(tmp_path):
    write_settings({TZ: "UTC"})
    assert layer(runtime=str(tmp_path / "missing")).pending()["server"] == []
