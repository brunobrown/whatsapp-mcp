"""Signing in, creating the first password, changing it and signing out.

Messages are plain and never say why a password was refused: a wrong password, an unknown state and a throttled
attempt read the same to someone guessing. The setup code is handed to `announce` (the log of the bridge) and is
never part of a result. A session is anything with `sign_in()` and `sign_out()`.
"""
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ui.security.auth import MIN_PASSWORD_LENGTH, AuthStore
from ui.security.throttle import Throttle

CODE_REQUEST_INTERVAL_SECONDS = 30

FAILED = "Sign-in failed. Check the details and try again."
NEEDS_CODE = "No password has been created yet. Use the setup code printed in the bridge log to create one."
BROKEN = "The password file cannot be used. Remove ui_auth.json from the configuration directory to start over."
WEAK = f"The password must have at least {MIN_PASSWORD_LENGTH} characters."
MISMATCH = "The two passwords do not match."
UNAVAILABLE = "The password could not be saved. Check the configuration directory and try again."


def _wait_message(seconds: int) -> str:
    return f"Too many attempts. Wait {seconds} seconds and try again."


@dataclass(frozen=True)
class Result:
    ok: bool
    message: Optional[str] = None
    wait: Optional[int] = None            # seconds to wait before the next attempt, when one is being waited out


@dataclass(frozen=True)
class CodeRequest:
    announced: bool
    message: Optional[str] = None


class SignInFlow:
    def __init__(self, auth: AuthStore, throttle: Throttle, announce: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic):
        self._auth = auth
        self._throttle = throttle
        self._announce = announce
        self._clock = clock
        self._last_code: Optional[float] = None

    def state(self) -> str:
        """"setup" (no password yet), "login" or "broken"."""
        return {"none": "setup", "set": "login"}.get(self._auth.status(), "broken")

    def _waiting(self, origin: str) -> Optional[Result]:
        wait = self._throttle.retry_after(origin)
        return Result(False, _wait_message(wait), wait) if wait else None

    # -- the setup code ----------------------------------------------------------------------------
    def request_code(self, origin: str) -> CodeRequest:
        """Print a setup code in the log. The code never comes back to the caller."""
        now = self._clock()
        if self.state() != "setup":
            return CodeRequest(False, "A setup code is not needed.")
        if self._last_code is not None and now - self._last_code < CODE_REQUEST_INTERVAL_SECONDS:
            return CodeRequest(False, f"A code was printed a moment ago. Wait {CODE_REQUEST_INTERVAL_SECONDS} seconds "
                                      "before asking for another.")
        code = self._auth.issue_code()
        if code is None:
            return CodeRequest(False, "A setup code is not needed.")
        self._last_code = now
        self._announce(code)
        return CodeRequest(True, "A setup code was printed in the bridge log. It is valid for 15 minutes.")

    def setup(self, session: Any, origin: str, code: Any, password: Any, confirm: Any) -> Result:
        waiting = self._waiting(origin)
        if waiting:
            return waiting
        if password != confirm:
            return Result(False, MISMATCH)
        out = self._auth.create(code, password)
        if out.ok:
            self._throttle.succeeded(origin)
            session.sign_in()
            return Result(True)
        return self._refused(origin, out.reason)

    def _refused(self, origin: str, reason: Optional[str]) -> Result:
        if reason == "weak_password":
            return Result(False, WEAK)
        if reason == "unavailable":
            return Result(False, UNAVAILABLE)
        if reason == "broken":
            return Result(False, BROKEN)
        if reason == "already_set":
            return Result(False, FAILED)
        self._throttle.failed(origin)
        return Result(False, FAILED)

    # -- signing in and out ------------------------------------------------------------------------
    def sign_in(self, session: Any, origin: str, password: Any) -> Result:
        state = self.state()
        if state == "setup":
            return Result(False, NEEDS_CODE)
        if state == "broken":
            return Result(False, BROKEN)
        waiting = self._waiting(origin)
        if waiting:
            return waiting
        if self._auth.verify(password):
            self._throttle.succeeded(origin)
            session.sign_in()
            return Result(True)
        self._throttle.failed(origin)
        return Result(False, FAILED)

    def sign_out(self, session: Any) -> None:
        session.sign_out()

    def change_password(self, origin: str, current: Any, new: Any, confirm: Any) -> Result:
        waiting = self._waiting(origin)
        if waiting:
            return waiting
        if new != confirm:
            return Result(False, MISMATCH)
        if self._auth.status() != "set":
            return Result(False, FAILED)
        out = self._auth.change(current, new)
        if out.ok:
            self._throttle.succeeded(origin)
            return Result(True, "Password changed.")
        if out.reason == "wrong_password":
            self._throttle.failed(origin)
            return Result(False, FAILED)
        return Result(False, WEAK if out.reason == "weak_password" else UNAVAILABLE)
