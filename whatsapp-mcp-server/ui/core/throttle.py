"""Slowing down guesses of the password, and letting an idle session lapse.

After n failures the next attempt waits min(2^(n-1), 60) seconds, counted per origin and over all origins; the
longer of the two applies. An attempt refused for waiting is not counted and never reaches the password check.
Quiet for a while, the counts are forgotten. Nothing here knows about a screen.
"""
import time
from typing import Callable, Dict, Optional

QUIET_RESET_SECONDS = 15 * 60
MAX_DELAY_SECONDS = 60
MAX_ORIGINS = 1000
DEFAULT_IDLE_SECONDS = 30 * 60
FAILURE_MESSAGE = "Não foi possível entrar. Confira a senha e tente de novo."


def delay_after(failures: int) -> int:
    return 0 if failures <= 0 else min(2 ** (failures - 1), MAX_DELAY_SECONDS)


def wait_message(seconds: float) -> str:
    whole = int(seconds) + 1 if seconds != int(seconds) else int(seconds)
    return f"Muitas tentativas. Aguarde {whole} segundo{'s' if whole != 1 else ''} para tentar de novo."


class _Count:
    __slots__ = ("failures", "last")

    def __init__(self):
        self.failures = 0
        self.last = 0.0


class Throttle:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._origins: Dict[str, _Count] = {}
        self._all = _Count()

    def _fresh(self, count: _Count, now: float) -> None:
        if count.failures and now - count.last >= QUIET_RESET_SECONDS:
            count.failures = 0

    def _wait_of(self, count: _Count, now: float) -> float:
        self._fresh(count, now)
        return max(0.0, count.last + delay_after(count.failures) - now) if count.failures else 0.0

    def wait(self, origin: Optional[str]) -> Optional[float]:
        """Seconds the next attempt must wait, or None when it may go ahead."""
        now = self._clock()
        count = self._origins.get(origin or "unknown")
        seconds = max(self._wait_of(self._all, now), self._wait_of(count, now) if count else 0.0)
        return seconds if seconds > 0 else None

    def failure(self, origin: Optional[str]) -> None:
        now = self._clock()
        key = origin or "unknown"
        count = self._origins.get(key)
        if count is None:
            self._prune(now)
            count = self._origins[key] = _Count()
        for c in (count, self._all):
            self._fresh(c, now)
            c.failures += 1
            c.last = now

    def success(self, origin: Optional[str]) -> None:
        self._origins.pop(origin or "unknown", None)
        self._all.failures = 0

    def tracked(self) -> int:
        return len(self._origins)

    def _prune(self, now: float) -> None:
        for key in [k for k, c in self._origins.items() if now - c.last >= QUIET_RESET_SECONDS]:
            del self._origins[key]
        while len(self._origins) >= MAX_ORIGINS:
            del self._origins[min(self._origins, key=lambda k: self._origins[k].last)]


class IdleClock:
    """A session lapses after limit seconds without activity, and stays lapsed."""

    def __init__(self, limit: float = DEFAULT_IDLE_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self._clock = clock
        self._last = clock()
        self._lapsed = False

    @property
    def expired(self) -> bool:
        if not self._lapsed and self._clock() - self._last > self.limit:
            self._lapsed = True
        return self._lapsed

    @property
    def remaining(self) -> float:
        return 0 if self.expired else max(0.0, self.limit - (self._clock() - self._last))

    def touch(self) -> None:
        if not self.expired:
            self._last = self._clock()
