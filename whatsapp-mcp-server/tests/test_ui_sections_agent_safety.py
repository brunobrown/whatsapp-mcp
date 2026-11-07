"""The agent safety section: read-only mode and session taint, as the server reads them (CFG-01, CFG-02, CFG-04, CFG-11)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import server_mode  # noqa: E402
import taint  # noqa: E402
import uisettings  # noqa: E402
from ui.core.draft import Draft  # noqa: E402
from ui.core.envlayer import SCOPES, EnvLayer  # noqa: E402
from ui.core.registry import EnvKeys, default  # noqa: E402
from ui.core.store import ABSENT  # noqa: E402
from ui.sections.agent_safety import SECTION  # noqa: E402


def test_the_section_is_registered_and_kept_in_the_settings_file():
    assert SECTION in default.sections() and isinstance(SECTION.storage, EnvKeys)


def test_the_variables_are_the_ones_the_server_reads_and_both_belong_to_the_server():  # CFG-11
    assert [f.env_var for f in SECTION.fields] == [server_mode.ENV, taint.ENV]
    assert {SCOPES[f.env_var] for f in SECTION.fields} == {"server"}
    assert {f.env_var for f in SECTION.fields} <= set(uisettings.EDITABLE_ENV)


def test_the_choices_are_the_values_the_server_understands(monkeypatch):  # CFG-11
    mode, tainted = SECTION.field("mcp_mode"), SECTION.field("session_taint")

    assert set(mode.options) == {server_mode.FULL, server_mode.READ_ONLY}
    for value in mode.options:
        monkeypatch.setenv(server_mode.ENV, value)
        assert server_mode.current() == value
    for value in tainted.options:
        monkeypatch.setenv(taint.ENV, value)
        assert taint.mode() == value


def test_the_defaults_shown_are_what_the_server_does_with_nothing_set(monkeypatch):  # CFG-01
    monkeypatch.delenv(server_mode.ENV, raising=False)
    monkeypatch.delenv(taint.ENV, raising=False)

    assert SECTION.field("mcp_mode").default == server_mode.current()
    assert SECTION.field("session_taint").default == taint.mode()


def test_a_saved_choice_is_the_one_the_server_then_applies(monkeypatch):  # CFG-04
    layer = EnvLayer(environ={})
    assert layer.save(SECTION, {"mcp_mode": "read_only", "session_taint": "off"}, layer.load().version).ok
    applied = {}
    uisettings.apply(applied)
    for name, value in applied.items():
        monkeypatch.setenv(name, value)

    assert server_mode.is_read_only() is True and taint.mode() == "off"


@pytest.mark.parametrize("changes", [{"mcp_mode": "readonly"}, {"mcp_mode": "FULL"}, {"mcp_mode": ""}, {"session_taint": "maybe"},
                                      {"session_taint": True}])
def test_a_value_the_server_would_not_understand_cannot_be_saved(tmp_path, changes):  # CFG-02
    result = EnvLayer(environ={}).save(SECTION, changes, ABSENT)

    assert result.ok is False and not (tmp_path / "config" / "ui_settings.json").exists()


def test_only_the_choices_that_weaken_a_defence_ask_for_confirmation():  # CFG-01
    mode, tainted = SECTION.field("mcp_mode"), SECTION.field("session_taint")

    assert mode.risky("full") and not mode.risky("read_only")
    assert tainted.risky("off") and not tainted.risky("block")
    assert "enviar" in mode.risk and "vazar" in tainted.risk


def test_going_back_to_the_default_is_a_normal_draft_change():  # CFG-07
    draft = Draft(SECTION, {"mcp_mode": "read_only"})

    assert draft.restore_default("mcp_mode", confirmed=True) and draft.changes() == {"mcp_mode": None}


def test_the_environment_of_the_process_locks_the_field_and_says_which_variable():  # CFG-04
    layer = EnvLayer(environ={server_mode.ENV: "read_only"})
    state = next(s for s in layer.state(SECTION) if s.key == "mcp_mode")

    assert (state.locked, state.env_var, state.origin, state.value) == (True, server_mode.ENV, "env", "read_only")
