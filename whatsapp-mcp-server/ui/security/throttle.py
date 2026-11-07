"""How fast a password may be tried, and when an unused session ends.

After n failures from one origin, or n beyond a small allowance from all origins together, the next attempt has to wait
min(2^(n-1), 60) seconds. An attempt made during the wait is refused without being counted or checked. Counts are
forgotten after 15 quiet minutes and only a bounded number of origins is remembered. An IdleClock lapses once the
session has been unused for its limit and stays lapsed.
"""
import math
import time
from typing import Callable, Dict, Tuple

MAX_DELAY_SECONDS = 60
QUIET_SECONDS = 15 * 60
MAX_ORIGINS = 1000
OVERALL_ALLOWANCE = 10        # failures from all origins together that cost nobody a wait
IDLE_LIMIT_SECONDS = 30 * 60


def delay_after(failures: int) -> int:
    return 0 if failures <= 0 else min(2 ** min(failures - 1, 6), MAX_DELAY_SECONDS)


class _Count:
    __slots__ = ("failures", "last")

    def __init__(self) -> None:
        self.failures = 0
        self.last = 0.0


class Throttle:
    def __init__(self, clock: Callable[[], float] = time.monotonic, quiet: float = QUIET_SECONDS,
                 max_origins: int = MAX_ORIGINS):
        self._clock = clock
        self._quiet = quiet
        self._max = max_origins
        self._origins: Dict[str, _Count] = {}
        self._overall = _Count()

    def _live(self, count: _Count) -> int:
        """The failures of `count`, forgotten once it has been quiet long enough."""
        if count.failures and self._clock() - count.last > self._quiet:
            count.failures = 0
        return count.failures

    def retry_after(self, origin: str) -> int:
        """Whole seconds the origin has to wait before trying again; 0 when it may try now."""
        now = self._clock()
        wait = 0.0
        for count, allowance in ((self._origins.get(origin), 0), (self._overall, OVERALL_ALLOWANCE)):
            if count is not None and self._live(count):
                wait = max(wait, count.last + delay_after(count.failures - allowance) - now)
        return max(0, math.ceil(wait))

    def try_now(self, origin: str) -> bool:
        """True when an attempt may be checked. A refusal here is not a failure and is not counted."""
        return self.retry_after(origin) == 0

    def failed(self, origin: str) -> None:
        now = self._clock()
        count = self._origins.get(origin)
        if count is None:
            self._evict()
            count = self._origins[origin] = _Count()
        self._live(count)
        self._live(self._overall)
        for c in (count, self._overall):
            c.failures += 1
            c.last = now

    def succeeded(self, origin: str) -> None:
        self._origins.pop(origin, None)
        self._overall.failures = 0

    def tracked_origins(self) -> int:
        return len(self._origins)

    def _evict(self) -> None:
        """Make room for one more origin: first the ones gone quiet, then the least recently active."""
        if len(self._origins) < self._max:
            return
        for key in [k for k, c in self._origins.items() if not self._live(c)]:
            del self._origins[key]
        while len(self._origins) >= self._max:
            oldest: Tuple[str, float] = min(((k, c.last) for k, c in self._origins.items()), key=lambda kv: kv[1])
            del self._origins[oldest[0]]


class IdleClock:
    def __init__(self, limit: float = IDLE_LIMIT_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._limit = limit
        self._clock = clock
        self._last = clock()
        self._lapsed = False

    @property
    def lapsed(self) -> bool:
        if not self._lapsed and self._clock() - self._last > self._limit:
            self._lapsed = True
        return self._lapsed

    def touch(self) -> bool:
        """Count a use. False when the session had already lapsed; it is not renewed."""
        if self.lapsed:
            return False
        self._last = self._clock()
        return True

    def remaining(self) -> int:
        if self.lapsed:
            return 0
        return max(0, math.ceil(self._limit - (self._clock() - self._last)))
