"""Slowing down guesses of the password, and letting an idle session lapse (AUTH-04, AUTH-05)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.core.throttle import FAILURE_MESSAGE, QUIET_RESET_SECONDS, IdleClock, Throttle, delay_after, wait_message  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def throttle(clock):
    return Throttle(clock=clock)


@pytest.mark.parametrize("failures, seconds", [(0, 0), (1, 1), (2, 2), (3, 4), (4, 8), (5, 16), (6, 32), (7, 60), (8, 60), (50, 60)])
def test_the_delay_doubles_with_each_failure_and_stops_at_a_minute(failures, seconds):  # AUTH-04
    assert delay_after(failures) == seconds


def test_a_first_attempt_is_never_held_back(throttle):  # AUTH-04
    assert throttle.wait("127.0.0.1") is None


def test_after_a_failure_the_next_attempt_waits_and_then_may_go(throttle, clock):  # AUTH-04
    throttle.failure("127.0.0.1")

    assert throttle.wait("127.0.0.1") == 1
    clock.now += 0.4
    assert throttle.wait("127.0.0.1") == pytest.approx(0.6)
    clock.now += 0.6
    assert throttle.wait("127.0.0.1") is None


def test_each_failure_lengthens_the_wait(throttle, clock):  # AUTH-04
    waits = []
    for _ in range(4):
        throttle.failure("127.0.0.1")
        waits.append(throttle.wait("127.0.0.1"))
        clock.now += waits[-1]

    assert waits == [1, 2, 4, 8]


def test_a_correct_password_clears_the_count(throttle, clock):  # AUTH-04
    for _ in range(3):
        throttle.failure("127.0.0.1")
        clock.now += 100
    throttle.success("127.0.0.1")
    throttle.failure("127.0.0.1")

    assert throttle.wait("127.0.0.1") == 1


def test_an_attempt_refused_while_waiting_does_not_lengthen_the_wait(throttle, clock):  # AUTH-04
    throttle.failure("127.0.0.1")
    for _ in range(20):
        assert throttle.wait("127.0.0.1") == 1        # asked again and again: nothing is counted

    clock.now += 1
    assert throttle.wait("127.0.0.1") is None


def test_failures_from_one_origin_slow_the_others_through_the_global_count(throttle, clock):  # AUTH-04
    for _ in range(3):
        throttle.failure("10.0.0.1")
        clock.now += 100
    throttle.failure("10.0.0.1")

    assert throttle.wait("10.0.0.2") == delay_after(4)         # another origin is held back too


def test_an_origin_with_its_own_failures_waits_for_the_longer_of_the_two(throttle, clock):  # AUTH-04
    throttle.failure("10.0.0.1")
    clock.now += 100
    for _ in range(3):
        throttle.failure("10.0.0.2")
        clock.now += 100
    throttle.failure("10.0.0.2")

    assert throttle.wait("10.0.0.2") == delay_after(5)         # 1 + 3 + 1 failures overall; 4 for this origin


def test_a_quiet_period_forgets_the_failures(throttle, clock):  # AUTH-04
    for _ in range(5):
        throttle.failure("127.0.0.1")
        clock.now += 100
    clock.now += QUIET_RESET_SECONDS

    throttle.failure("127.0.0.1")

    assert throttle.wait("127.0.0.1") == 1


def test_an_unknown_origin_is_one_origin_not_an_error(throttle):  # AUTH-04
    throttle.failure(None)

    assert throttle.wait(None) == 1


def test_the_table_of_origins_does_not_grow_without_bound(throttle, clock):  # AUTH-04
    for i in range(5000):
        throttle.failure(f"10.{i // 250}.{i % 250}.1")
        clock.now += 0.001

    assert throttle.tracked() <= 1000


def test_the_failure_message_is_the_same_for_every_cause():  # AUTH-04
    assert FAILURE_MESSAGE and "senha" in FAILURE_MESSAGE.lower()
    assert "7" in wait_message(6.2) and "segundo" in wait_message(6.2)
    assert FAILURE_MESSAGE not in wait_message(1)


def test_a_session_is_not_idle_at_first(clock):  # AUTH-05
    assert IdleClock(60, clock=clock).expired is False


def test_a_session_lapses_after_the_idle_limit(clock):  # AUTH-05
    idle = IdleClock(60, clock=clock)
    clock.now += 61

    assert idle.expired is True and idle.remaining == 0


def test_activity_renews_the_session(clock):  # AUTH-05
    idle = IdleClock(60, clock=clock)
    clock.now += 50
    idle.touch()
    clock.now += 50

    assert idle.expired is False and idle.remaining == pytest.approx(10)


def test_a_lapsed_session_stays_lapsed_whatever_happens_next(clock):  # AUTH-05
    idle = IdleClock(60, clock=clock)
    clock.now += 61
    idle.touch()                  # a late event cannot bring it back

    assert idle.expired is True


def test_the_default_limit_is_thirty_minutes(clock):  # AUTH-05
    assert IdleClock(clock=clock).limit == 30 * 60
