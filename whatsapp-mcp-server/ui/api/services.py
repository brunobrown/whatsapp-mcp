"""What the whole process shares: the registry, the stores, the password and the throttle of guesses.

`build_services` runs once, when the interface starts, with the environment as the process was started (before
ui_settings.json filled in what was missing), so the interface can tell a variable of the environment from a value of
the file.
"""
import os
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from ui.config.envlayer import EnvLayer
from ui.config.registry import Registry
from ui.config.store import ConfigStore
from ui.security.auth import AuthStore
from ui.security.signin import SignInFlow
from ui.security.throttle import IDLE_LIMIT_SECONDS, Throttle


@dataclass
class Services:
    registry: Registry
    store: ConfigStore
    env: EnvLayer
    auth: AuthStore
    throttle: Throttle
    signin: SignInFlow
    idle_seconds: float = IDLE_LIMIT_SECONDS


def _print_line(line: str) -> None:
    print(line, flush=True)


def code_line(code: str) -> str:
    return f"ui: setup code for the interface: {code} (valid for 15 minutes, works once; use it to create the password)"


def build_services(startup_environ: Optional[Mapping[str, str]] = None, idle_seconds: float = IDLE_LIMIT_SECONDS,
                   announce: Optional[Callable[[str], None]] = None) -> Services:
    """`announce` receives each setup code requested from the page; by default it is written to the log."""
    environ = dict(os.environ if startup_environ is None else startup_environ)
    registry = Registry()
    registry.discover("ui.sections")
    store = ConfigStore(registry)
    env = EnvLayer(registry, store, environ=environ)
    auth = AuthStore()
    throttle = Throttle()
    out = announce or (lambda code: _print_line(code_line(code)))
    return Services(registry, store, env, auth, throttle, SignInFlow(auth, throttle, out), idle_seconds)


def announce_setup_code(services: Services, out: Callable[[str], None] = _print_line) -> None:
    """Write the code that creates the first password in the log; nothing when a password exists."""
    code = services.auth.issue_code()
    if code:
        out(code_line(code))
