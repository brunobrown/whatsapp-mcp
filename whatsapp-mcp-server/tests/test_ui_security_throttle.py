"""Attempt delay and idle clock (AUTH-04, AUTH-05)."""
import pytest

from ui.security.throttle import IdleClock, Throttle


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


def fail(th, origin, times=1):
    for _ in range(times):
        th.failed(origin)


def test_a_fresh_origin_may_try_at_once(clock):
    assert Throttle(clock=clock).retry_after("a") == 0


@pytest.mark.parametrize("failures,delay", [(1, 1), (2, 2), (3, 4), (4, 8), (5, 16), (6, 32), (7, 60), (8, 60), (30, 60)])
def test_the_delay_after_n_failures_is_min_2_pow_n_minus_1_and_60(clock, failures, delay):
    th = Throttle(clock=clock)
    fail(th, "a", failures)
    assert th.retry_after("a") == delay


def test_the_delay_runs_down_with_time_and_then_allows_a_try(clock):
    th = Throttle(clock=clock)
    fail(th, "a", 4)                                 # 8 s
    clock.now += 3
    assert th.retry_after("a") == 5
    clock.now += 5
    assert th.retry_after("a") == 0


def test_an_attempt_made_while_waiting_is_not_counted(clock):
    th = Throttle(clock=clock)
    fail(th, "a", 3)                                 # 4 s
    assert th.try_now("a") is False                  # refused without being checked
    assert th.try_now("a") is False
    clock.now += 4
    assert th.retry_after("a") == 0
    th.failed("a")                                   # this is failure number 4, not 6
    assert th.retry_after("a") == 8


def test_try_now_allows_when_not_waiting(clock):
    assert Throttle(clock=clock).try_now("a") is True


def test_origins_are_counted_apart(clock):
    th = Throttle(clock=clock)
    fail(th, "a", 3)
    assert th.retry_after("b") == 0


def test_failures_from_every_origin_together_slow_everyone_down(clock):
    th = Throttle(clock=clock)
    for i in range(5):
        th.failed(f"origin-{i}")                     # one failure each: still within what everyone is allowed
    assert th.retry_after("a-new-origin") == 0
    for i in range(5, 20):
        th.failed(f"origin-{i}")
    assert 0 < th.retry_after("a-new-origin") <= 60


def test_a_success_clears_the_origin(clock):
    th = Throttle(clock=clock)
    fail(th, "a", 4)
    clock.now += 8
    th.succeeded("a")
    th.failed("a")
    assert th.retry_after("a") == 1


def test_counts_are_forgotten_after_15_quiet_minutes(clock):
    th = Throttle(clock=clock)
    fail(th, "a", 10)
    clock.now += 15 * 60 + 1
    assert th.retry_after("a") == 0
    th.failed("a")
    assert th.retry_after("a") == 1                  # counting starts again from one


def test_counts_are_kept_inside_the_quiet_time(clock):
    th = Throttle(clock=clock)
    fail(th, "a", 3)
    clock.now += 14 * 60
    th.failed("a")
    assert th.retry_after("a") == 8


def test_the_number_of_remembered_origins_is_bounded(clock):
    th = Throttle(clock=clock, max_origins=50)
    for i in range(500):
        th.failed(f"origin-{i}")
        clock.now += 0.001
    assert th.tracked_origins() <= 50


def test_flooding_with_new_origins_does_not_forget_a_recent_offender(clock):
    th = Throttle(clock=clock, max_origins=50)
    fail(th, "attacker", 5)
    for i in range(200):
        th.failed(f"origin-{i}")
    assert th.retry_after("attacker") > 0


# -- idle clock -----------------------------------------------------------------------------------
def test_a_session_stays_alive_while_it_is_used(clock):
    idle = IdleClock(limit=100, clock=clock)
    for _ in range(5):
        clock.now += 90
        assert idle.touch() is True
    assert idle.lapsed is False


def test_a_session_lapses_after_the_limit(clock):
    idle = IdleClock(limit=100, clock=clock)
    clock.now += 100
    assert idle.lapsed is False                      # exactly at the limit it is still alive
    clock.now += 1
    assert idle.lapsed is True


def test_a_lapsed_session_does_not_revive(clock):
    idle = IdleClock(limit=100, clock=clock)
    clock.now += 101
    assert idle.touch() is False
    assert idle.lapsed is True
    assert idle.touch() is False
    assert idle.remaining() == 0


def test_a_lapse_noticed_late_still_counts(clock):
    idle = IdleClock(limit=100, clock=clock)
    clock.now += 500
    assert idle.touch() is False


def test_remaining_counts_down_and_is_renewed_by_touch(clock):
    idle = IdleClock(limit=100, clock=clock)
    clock.now += 30
    assert idle.remaining() == 70
    idle.touch()
    assert idle.remaining() == 100
