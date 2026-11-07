"""Signing in, setting the first password, changing it, signing out (AUTH-01..AUTH-07)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.core.auth import MIN_PASSWORD_LENGTH, AuthStore  # noqa: E402
from ui.core.loginflow import CODE_REQUEST_INTERVAL, LoginFlow  # noqa: E402
from ui.core.throttle import FAILURE_MESSAGE, Throttle  # noqa: E402

GOOD = "correct horse battery"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class Session:
    def __init__(self):
        self.signed_in = False
        self.log = []

    def sign_in(self):
        self.signed_in = True
        self.log.append("in")

    def sign_out(self):
        self.signed_in = False
        self.log.append("out")


class World:
    def __init__(self, ip="127.0.0.1"):
        self.clock = Clock()
        self.auth = AuthStore(clock=self.clock, params=(16, 8, 1))
        self.throttle = Throttle(clock=self.clock)
        self.session = Session()
        self.announced = []
        self.verifies = 0
        original = self.auth.verify

        def counting(password):
            self.verifies += 1
            return original(password)
        self.auth.verify = counting
        self.flow = LoginFlow(self.auth, self.throttle, self.session, client_ip=ip, announce=self.announced.append, clock=self.clock)

    def with_password(self, password=GOOD):
        self.auth.create(self.auth.start_setup(), password)
        self.session.signed_in = False
        self.session.log.clear()
        self.verifies = 0
        return self


@pytest.fixture
def world():
    return World()


def test_without_a_password_the_screen_is_the_setup_and_with_one_it_is_the_login(world):  # AUTH-02
    assert world.flow.mode() == "setup"
    world.with_password()
    assert world.flow.mode() == "login"


def test_a_damaged_password_file_is_said_not_papered_over(world, tmp_path):  # AUTH-02
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "ui_auth.json").write_text("{broken")

    assert world.flow.mode() == "broken" and "ui_auth.json" in world.flow.broken_message()


def test_the_right_password_signs_in(world):  # AUTH-01
    world.with_password()

    result = world.flow.login(GOOD)

    assert result.ok and world.session.signed_in and result.message is None


def test_a_wrong_password_gets_the_same_generic_message_whoever_asks(world):  # AUTH-04
    world.with_password()

    result = world.flow.login("not the password")

    assert result.ok is False and result.message == FAILURE_MESSAGE and not world.session.signed_in
    assert "not the password" not in result.message


def test_after_failures_the_next_attempt_waits_and_is_not_even_checked(world):  # AUTH-04
    world.with_password()
    world.flow.login("wrong one")
    checked = world.verifies

    result = world.flow.login(GOOD)                                    # the right password, too soon

    assert result.ok is False and "Aguarde" in result.message and result.wait and world.verifies == checked and not world.session.signed_in
    world.clock.now += 1.5
    assert world.flow.login(GOOD).ok is True


def test_the_wait_grows_with_the_failures(world):  # AUTH-04
    world.with_password()
    waits = []
    for _ in range(4):
        world.flow.login("wrong")
        waits.append(world.flow.login("wrong").wait)
        world.clock.now += waits[-1]

    assert waits == sorted(waits) and waits[-1] > waits[0]


def test_a_success_clears_the_failures(world):  # AUTH-04
    world.with_password()
    world.flow.login("wrong")
    world.clock.now += 5
    world.flow.login(GOOD)
    world.flow.sign_out()

    world.flow.login("wrong")

    assert world.flow.login(GOOD).wait == 1


def test_each_client_is_counted_on_its_own_origin(world):  # AUTH-04
    world.with_password()
    other = LoginFlow(world.auth, world.throttle, Session(), client_ip="10.0.0.9", announce=lambda c: None, clock=world.clock)
    for _ in range(3):
        world.flow.login("wrong")
        world.clock.now += 100

    assert world.throttle.wait("127.0.0.1") is None                      # quiet long enough for its own count to ease
    other.login("wrong")
    assert world.throttle.wait("10.0.0.9") is not None


def test_logging_in_before_a_password_exists_points_to_the_setup(world):  # AUTH-02
    result = world.flow.login(GOOD)

    assert result.ok is False and "criar a senha" in result.message.lower() and not world.session.signed_in


def test_setup_creates_the_password_with_the_code_and_signs_in(world):  # AUTH-02
    code = world.flow.request_code().code

    result = world.flow.setup(code, GOOD, GOOD)

    assert result.ok and world.session.signed_in and world.flow.mode() == "login" and world.auth.verify(GOOD)


def test_setup_needs_the_two_passwords_to_agree_and_does_not_burn_the_code(world):  # AUTH-02
    code = world.flow.request_code().code

    mismatch = world.flow.setup(code, GOOD, GOOD + "x")

    assert mismatch.ok is False and "não são iguais" in mismatch.message
    assert world.flow.setup(code, GOOD, GOOD).ok


def test_password_shorter_than_twelve_is_refused_with_the_rule(world):  # AUTH-06
    code = world.flow.request_code().code

    result = world.flow.setup(code, "short", "short")

    assert result.ok is False and str(MIN_PASSWORD_LENGTH) in result.message
    assert world.flow.setup(code, GOOD, GOOD).ok                         # the code was not burnt by a weak password


def test_a_wrong_code_is_refused_without_saying_why_and_counts_as_a_failure(world):  # AUTH-02
    world.flow.request_code()

    result = world.flow.setup("AAAA-BBBB-CCCC", GOOD, GOOD)

    assert result.ok is False and "log" in result.message.lower() and not world.session.signed_in
    assert world.flow.setup("AAAA-BBBB-CCCC", GOOD, GOOD).wait


def test_the_code_is_written_to_the_log_with_its_lifetime_and_nothing_else(world):  # AUTH-02
    issued = world.flow.request_code()

    assert world.announced == [issued.code] and issued.announced is True


def test_asking_for_a_code_again_too_soon_is_refused_and_a_new_one_replaces_the_old(world):  # AUTH-02
    first = world.flow.request_code()
    soon = world.flow.request_code()

    assert soon.announced is False and "Aguarde" in soon.message and len(world.announced) == 1
    world.clock.now += CODE_REQUEST_INTERVAL + 1
    second = world.flow.request_code()
    assert second.announced and second.code != first.code
    assert world.flow.setup(first.code, GOOD, GOOD).ok is False
    world.clock.now += 2                                                  # the wrong code earned a short wait
    assert world.flow.setup(second.code, GOOD, GOOD).ok


def test_no_code_is_issued_once_there_is_a_password(world):  # AUTH-02
    world.with_password()

    result = world.flow.request_code()

    assert result.announced is False and result.code is None and world.announced == []


def test_setup_when_a_password_exists_is_refused(world):  # AUTH-02
    world.with_password()

    result = world.flow.setup("AAAA-BBBB-CCCC", "another long password", "another long password")

    assert result.ok is False and "já existe" in result.message.lower()


def test_changing_the_password_asks_for_the_current_one(world):  # AUTH-07
    world.with_password()

    wrong = world.flow.change_password("not the password", "a brand new password", "a brand new password")

    assert wrong.ok is False and wrong.message == FAILURE_MESSAGE and world.auth.verify(GOOD)
    world.clock.now += 2
    ok = world.flow.change_password(GOOD, "a brand new password", "a brand new password")
    assert ok.ok and world.auth.verify("a brand new password") and not world.auth.verify(GOOD)


def test_changing_the_password_checks_the_new_one_twice_and_for_length(world):  # AUTH-06, AUTH-07
    world.with_password()

    assert "não são iguais" in world.flow.change_password(GOOD, "a brand new password", "other long password!").message
    assert str(MIN_PASSWORD_LENGTH) in world.flow.change_password(GOOD, "short", "short").message
    assert world.auth.verify(GOOD)


def test_wrong_current_passwords_are_throttled_like_a_login(world):  # AUTH-04
    world.with_password()
    world.flow.change_password("wrong", "a brand new password", "a brand new password")

    result = world.flow.change_password(GOOD, "a brand new password", "a brand new password")

    assert result.ok is False and result.wait and world.auth.verify(GOOD)


def test_signing_out_ends_the_session(world):  # AUTH-05
    world.with_password()
    world.flow.login(GOOD)

    world.flow.sign_out()

    assert world.session.signed_in is False and world.session.log == ["in", "out"]


def test_wrong_password_generic_message_and_growing_delay(world):  # AUTH-04
    world.with_password()
    first = world.flow.login("wrong")
    world.clock.now += first.wait
    second = world.flow.login("also wrong")
    world.clock.now += second.wait
    third = world.flow.login("still wrong")

    assert first.message == second.message == third.message == FAILURE_MESSAGE
    assert 0 < first.wait < second.wait < third.wait
