"""The state of one browser session: nothing is loaded before the password, nothing survives leaving (AUTH-01, AUTH-05, CFG-10)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

pytest.importorskip("flet")

from ui.app.state import AppState, NotAuthenticated  # noqa: E402
from ui.core.registry import Field, JsonFile, Section  # noqa: E402
from ui.core.throttle import IdleClock  # noqa: E402

TOKEN = "override-0123456789-secret"


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def state(clock):
    return AppState(idle=IdleClock(60, clock=clock))


def section(id="send_policy"):
    return Section(id=id, title="Política", icon="x", order=1, storage=JsonFile(lambda: "/x"), validate=lambda v: [],
                   fields=(Field("mode", "Modo", "text"), Field("override_token", "Token", "secret")))


def test_unauthenticated_state_exposes_no_sections(state):  # AUTH-01
    assert state.authenticated is False and state.section_id is None and dict(state.data) == {}


def test_nothing_can_be_opened_or_stored_before_the_password(state):  # AUTH-01
    with pytest.raises(NotAuthenticated):
        state.open("send_policy")
    with pytest.raises(NotAuthenticated):
        state.put(section(), {"mode": "allowlist"})
    assert state.section_id is None and dict(state.data) == {}


def test_after_signing_in_a_section_can_be_opened_and_its_masked_data_kept(state):  # AUTH-01
    state.sign_in()
    state.open("send_policy")
    state.put(section(), {"mode": "allowlist", "override_token": TOKEN})

    assert state.authenticated and state.section_id == "send_policy"
    assert state.data["send_policy"] == {"mode": "allowlist", "override_token": {"set": True}}


def test_no_secret_value_enters_the_state(state):  # CFG-10
    state.sign_in()
    state.put(section(), {"mode": "x", "override_token": TOKEN})

    assert TOKEN not in json.dumps(dict(state.data), default=str) and TOKEN not in repr(state) and TOKEN not in str(state.__dict__)


def test_signing_out_clears_everything(state):  # AUTH-05
    state.sign_in()
    state.open("send_policy")
    state.put(section(), {"mode": "x"})

    state.sign_out()

    assert state.authenticated is False and state.section_id is None and dict(state.data) == {}
    with pytest.raises(NotAuthenticated):
        state.put(section(), {"mode": "y"})


def test_a_session_idle_too_long_is_cleared_when_checked(state, clock):  # AUTH-05
    state.sign_in()
    state.put(section(), {"mode": "x"})
    clock.now += 61

    assert state.check_idle() is True
    assert state.authenticated is False and dict(state.data) == {}


def test_activity_keeps_the_session_alive(state, clock):  # AUTH-05
    state.sign_in()
    clock.now += 50
    state.touch()
    clock.now += 50

    assert state.check_idle() is False and state.authenticated is True


def test_checking_idle_before_signing_in_does_nothing(state, clock):  # AUTH-05
    clock.now += 1000

    assert state.check_idle() is False


def test_signing_in_again_starts_a_fresh_idle_period(state, clock):  # AUTH-05
    state.sign_in()
    clock.now += 61
    state.check_idle()

    state.sign_in()

    assert state.authenticated and state.check_idle() is False


def test_the_state_tells_a_subscribed_screen_when_it_changes(state):  # EXT-02
    heard = []

    def listener(sender, field):          # Flet keeps listeners weakly: the screen that subscribes holds this reference
        heard.append(field)
    state.subscribe(listener)

    state.sign_in()
    state.open("send_policy")

    assert "authenticated" in heard and "section_id" in heard


def test_a_restart_notice_is_kept_once_per_process_and_cleared_on_sign_out(state):  # CFG-08
    state.sign_in()
    state.note_restart("bridge")
    state.note_restart("bridge")
    state.note_restart("server")

    assert sorted(state.restart) == ["bridge", "server"]
    state.sign_out()
    assert list(state.restart) == []
