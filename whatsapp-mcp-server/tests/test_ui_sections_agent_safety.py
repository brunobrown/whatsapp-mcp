"""Agent safety section (CFG-01, CFG-04, CFG-08, CFG-11): environment variables of the MCP server, with risk warnings."""
import json
import os

import pytest

import drafts
import server_mode
import taint
import uisettings
from ui.config.envlayer import EnvLayer
from ui.config.form import FormFlow
from ui.config.registry import EnvKeys, Registry
from ui.config.store import ConfigStore
from ui.sections import agent_safety as section_module

SECTION = section_module.SECTION


@pytest.fixture
def ctx(tmp_path):
    reg = Registry()
    reg.register(SECTION)
    store = ConfigStore(reg)
    environ = {}
    layer = EnvLayer(reg, store, environ=environ, runtime_dir=str(tmp_path / "runtime"))

    def flow():
        f = FormFlow("agent_safety", reg, store, layer)
        f.load()
        return f

    return flow, environ, layer


def test_the_section_is_kept_in_the_settings_file_and_restarts_the_server():
    assert isinstance(SECTION.storage, EnvKeys) and SECTION.restart == "server"
    assert SECTION.id == "agent_safety" and not SECTION.read_only


def test_every_variable_is_editable_and_names_the_real_one():
    variables = [f.env_var for f in SECTION.fields]
    assert variables == [server_mode.ENV, taint.ENV, "WHATSAPP_MONITOR_DRAFT_TTL_HOURS"]
    assert all(v in uisettings.EDITABLE_ENV for v in variables)


def test_options_and_defaults_are_the_projects():
    mode = SECTION.field("mode")
    assert set(mode.options) == {server_mode.FULL, server_mode.READ_ONLY} and mode.default == server_mode.FULL
    taint_field = SECTION.field("session_taint")
    assert taint_field.default == taint.mode(None)
    assert set(taint_field.options) == {"block", "off"}
    ttl = SECTION.field("draft_ttl_hours")
    assert ttl.default == drafts.DEFAULT_TTL_HOURS and ttl.bounds[1] == drafts.MAX_TTL_HOURS and ttl.bounds[0] > 0


def test_values_that_weaken_a_defence_carry_a_risk_text_and_risk_when():
    taint_field, mode = SECTION.field("session_taint"), SECTION.field("mode")
    assert taint_field.risk and taint_field.risk_when == ("off",)
    assert mode.risk and mode.risk_when == (server_mode.FULL,)
    for f in SECTION.fields:
        assert bool(f.risk) == bool(f.risk_when)
        assert set(f.risk_when) <= set(f.options)


def test_a_change_is_saved_to_the_settings_file_and_asks_for_a_server_restart(ctx):
    flow, environ, layer = ctx
    f = flow()
    f.apply({"mode": server_mode.READ_ONLY, "draft_ttl_hours": "12"})
    outcome = f.save()
    assert outcome.ok is True and outcome.restarts == ["server"]
    saved = json.loads(open(uisettings.path()).read())
    assert saved == {"version": 1, "env": {server_mode.ENV: "read_only", "WHATSAPP_MONITOR_DRAFT_TTL_HOURS": "12"}}
    assert (os.stat(uisettings.path()).st_mode & 0o777) == 0o600
    view = flow().view()
    assert view.values["mode"] == "read_only" and view.values["draft_ttl_hours"] == 12.0
    assert view.state["mode"]["origin"] == "file"


def test_a_value_from_the_environment_locks_the_field(ctx):
    flow, environ, _ = ctx
    environ[taint.ENV] = "off"
    f = flow()
    view = f.view()
    assert view.values["session_taint"] == "off"
    assert view.state["session_taint"] == {"origin": "environment", "locked": True, "pending": None}
    f.apply({"session_taint": "block"})
    outcome = f.save()
    assert outcome.ok is False and taint.ENV in outcome.errors["session_taint"]


def test_values_outside_the_projects_limits_are_refused(ctx):
    flow, *_ = ctx
    for changes, key in (({"draft_ttl_hours": "200"}, "draft_ttl_hours"), ({"draft_ttl_hours": "0"}, "draft_ttl_hours"),
                         ({"mode": "anything"}, "mode"), ({"session_taint": "maybe"}, "session_taint")):
        f = flow()
        f.apply(changes)
        outcome = f.save()
        assert outcome.ok is False and key in outcome.errors, changes
    assert not os.path.exists(uisettings.path())


def test_an_unknown_value_is_never_written_so_the_project_never_reads_it_as_something_else(ctx):
    flow, *_ = ctx
    f = flow()
    f.apply({"mode": "read-only"})                           # the project would run read-only; the section refuses it
    assert f.save().ok is False


def test_going_back_to_the_default_removes_the_saved_value(ctx):
    flow, *_ = ctx
    f = flow()
    f.apply({"session_taint": "off"})
    assert f.save().ok is True
    g = flow()
    g.apply({}, restore=["session_taint"])
    assert g.save().ok is True
    assert json.loads(open(uisettings.path()).read())["env"] == {}
