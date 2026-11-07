"""Sign-in flow (AUTH-01, AUTH-02, AUTH-04, AUTH-06, AUTH-07): neutral messages, delay, setup code in the log only."""
import dataclasses

import pytest

from ui.security.auth import AuthStore
from ui.security.signin import SignInFlow
from ui.security.throttle import Throttle

PASSWORD = "a long enough password"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeSession:
    def __init__(self):
        self.signed_in = False
        self.signed_out = False

    def sign_in(self):
        self.signed_in = True

    def sign_out(self):
        self.signed_out = True


@pytest.fixture
def env():
    clock = Clock()
    log = []
    flow = SignInFlow(AuthStore(clock=clock), Throttle(clock=clock), announce=log.append, clock=clock)
    return flow, log, clock, FakeSession()


def with_password(env, password=PASSWORD):
    flow, log, clock, session = env
    assert flow.request_code("o").announced
    out = flow.setup(session, "o", log[-1], password, password)
    assert out.ok, out
    flow.sign_out(FakeSession())
    return password


def everything_in(result):
    return repr(dataclasses.asdict(result)) + str(result)


# -- state ----------------------------------------------------------------------------------------
def test_the_state_follows_the_password_file(env):
    flow = env[0]
    assert flow.state() == "setup"
    with_password(env)
    assert flow.state() == "login"


def test_a_broken_file_is_its_own_state_and_cannot_sign_in(env, tmp_path):
    import localconfig, os
    os.makedirs(localconfig.config_dir(), exist_ok=True)
    open(os.path.join(localconfig.config_dir(), "ui_auth.json"), "w").write("garbage")
    flow, _, _, session = env
    assert flow.state() == "broken"
    out = flow.sign_in(session, "o", "whatever password")
    assert out.ok is False and session.signed_in is False


# -- the setup code -------------------------------------------------------------------------------
def test_sign_in_without_a_password_points_to_the_code_and_never_returns_it(env):
    flow, log, _, session = env
    flow.request_code("o")
    out = flow.sign_in(session, "o", "anything at all")
    assert out.ok is False and session.signed_in is False
    assert "code" in out.message.lower() and "log" in out.message.lower()
    assert log[0].replace("-", "") not in everything_in(out).replace("-", "")


def test_the_code_goes_to_the_announcer_only(env):
    flow, log, _, _ = env
    out = flow.request_code("o")
    assert out.announced is True and len(log) == 1
    assert log[0].replace("-", "") not in everything_in(out).replace("-", "")
    assert log[0] not in (out.message or "")


def test_the_code_can_be_requested_once_per_30_seconds(env):
    flow, log, clock, _ = env
    assert flow.request_code("o").announced is True
    clock.now += 29
    again = flow.request_code("o")
    assert again.announced is False and len(log) == 1
    clock.now += 2
    assert flow.request_code("o").announced is True and len(log) == 2


def test_the_code_is_not_announced_when_a_password_exists(env):
    flow, log, _, _ = env
    with_password(env)
    n = len(log)
    assert flow.request_code("o").announced is False
    assert len(log) == n


def test_setup_creates_the_password_and_signs_in(env):
    flow, log, _, session = env
    flow.request_code("o")
    out = flow.setup(session, "o", log[-1], PASSWORD, PASSWORD)
    assert out.ok is True and session.signed_in is True
    assert flow.sign_in(FakeSession(), "o", PASSWORD).ok is True


def test_setup_with_a_wrong_code_is_refused_and_slows_down_the_origin(env):
    flow, log, _, session = env
    flow.request_code("o")
    out = flow.setup(session, "o", "AAAA-BBBB-CCCC", PASSWORD, PASSWORD)
    assert out.ok is False and session.signed_in is False and flow.state() == "setup"
    blocked = flow.setup(session, "o", log[-1], PASSWORD, PASSWORD)       # even the right code waits
    assert blocked.ok is False and blocked.wait and blocked.wait >= 1


def test_setup_refuses_a_password_that_is_too_short_and_a_mismatched_confirmation(env):
    flow, log, _, session = env
    flow.request_code("o")
    short = flow.setup(session, "o", log[-1], "short", "short")
    assert short.ok is False and "12" in short.message
    mismatch = flow.setup(session, "o", log[-1], PASSWORD, PASSWORD + "x")
    assert mismatch.ok is False and "match" in mismatch.message.lower()
    assert flow.state() == "setup"
    assert flow.setup(session, "o", log[-1], PASSWORD, PASSWORD).ok is True      # the code was not spent


def test_the_setup_messages_do_not_tell_why_a_code_failed(env):
    flow, log, clock, session = env
    flow.request_code("o")
    wrong = flow.setup(session, "o", "AAAA-BBBB-CCCC", PASSWORD, PASSWORD)
    clock.now += 16 * 60
    expired = flow.setup(session, "p", log[-1], PASSWORD, PASSWORD)
    assert wrong.message == expired.message


# -- signing in -----------------------------------------------------------------------------------
def test_the_right_password_signs_in(env):
    password = with_password(env)
    session = FakeSession()
    out = env[0].sign_in(session, "o", password)
    assert out.ok is True and session.signed_in is True


def test_a_wrong_password_gets_the_generic_message_and_starts_the_delay(env):
    flow = env[0]
    with_password(env)
    session = FakeSession()
    out = flow.sign_in(session, "o2", "this is not it at all")
    assert out.ok is False and session.signed_in is False and out.wait is None
    nxt = flow.sign_in(session, "o2", "this is not it either")
    assert nxt.ok is False and nxt.wait == 1
    assert "1" in nxt.message


def test_the_generic_message_is_the_same_for_every_wrong_password(env):
    flow, _, clock, _ = env
    with_password(env)
    messages = set()
    for i, wrong in enumerate(["", "x", "this is not it at all", PASSWORD.upper(), PASSWORD + " "]):
        clock.now += 100
        messages.add(flow.sign_in(FakeSession(), "o", wrong).message)
    assert len(messages) == 1
    message = messages.pop().lower()
    assert PASSWORD not in message


def test_an_attempt_while_waiting_is_not_checked_even_with_the_right_password(env):
    flow = env[0]
    password = with_password(env)
    flow.sign_in(FakeSession(), "o", "wrong password here")
    session = FakeSession()
    out = flow.sign_in(session, "o", password)
    assert out.ok is False and session.signed_in is False and out.wait == 1


def test_the_right_password_after_the_wait_signs_in_and_clears_the_delay(env):
    flow, _, clock, _ = env
    password = with_password(env)
    flow.sign_in(FakeSession(), "o", "wrong password here")
    clock.now += 2
    assert flow.sign_in(FakeSession(), "o", password).ok is True
    flow.sign_in(FakeSession(), "o", "wrong password here")
    assert flow.sign_in(FakeSession(), "o", "wrong password here").wait == 1     # counting restarted


def test_sign_out_ends_the_session(env):
    flow, _, _, session = env
    flow.sign_out(session)
    assert session.signed_out is True


# -- changing the password ------------------------------------------------------------------------
def test_change_needs_the_current_password_and_gives_the_generic_message(env):
    flow = env[0]
    password = with_password(env)
    out = flow.change_password("o", "not the current one", "another long password", "another long password")
    assert out.ok is False
    assert out.message == flow.sign_in(FakeSession(), "x", "nope nope nope nope").message
    assert flow.sign_in(FakeSession(), "z", password).ok is True              # unchanged


def test_change_replaces_the_password(env):
    flow, _, _, _ = env
    password = with_password(env)
    out = flow.change_password("o", password, "another long password", "another long password")
    assert out.ok is True
    assert flow.sign_in(FakeSession(), "q", "another long password").ok is True
    assert flow.sign_in(FakeSession(), "r", password).ok is False


def test_change_enforces_the_length_and_the_confirmation(env):
    flow = env[0]
    password = with_password(env)
    short = flow.change_password("o", password, "short", "short")
    assert short.ok is False and "12" in short.message
    mismatch = flow.change_password("o", password, "another long password", "another long passworD")
    assert mismatch.ok is False and "match" in mismatch.message.lower()
    assert flow.sign_in(FakeSession(), "z", password).ok is True


def test_wrong_current_passwords_are_throttled_like_wrong_sign_ins(env):
    flow = env[0]
    password = with_password(env)
    flow.change_password("o", "wrong wrong wrong", "another long password", "another long password")
    out = flow.change_password("o", password, "another long password", "another long password")
    assert out.ok is False and out.wait == 1
