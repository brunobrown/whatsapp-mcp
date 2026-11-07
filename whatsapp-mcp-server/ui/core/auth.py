"""The password of the interface.

It is created once, with a code printed in the log of the bridge (so only someone who can read the log can set it),
and kept only as a scrypt hash with its own salt in ui_auth.json (0600). The code lives 15 minutes, works once and
is burnt by a few wrong tries. Nothing here is a screen; the throttling of tries is in throttle.py.
"""
import hashlib
import hmac
import json
import secrets
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ui.core.registry import Field, JsonFile, Problem, Section
from ui.core.store import ABSENT, ConfigStore, config_path

FILE_NAME = "ui_auth.json"
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
CODE_LIFETIME_SECONDS = 15 * 60
MAX_CODE_ATTEMPTS = 5
DEFAULT_PARAMS = (2 ** 15, 8, 1)         # n, r, p
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0, 1, I, O
_MAX_N = 2 ** 20


@dataclass
class AuthResult:
    ok: bool
    reason: Optional[str] = None      # invalid_code, weak_password, already_set, wrong_password, unavailable


def _shape_ok(data: Any) -> bool:
    try:
        n, r, p = data["scrypt"]["n"], data["scrypt"]["r"], data["scrypt"]["p"]
        return (data["version"] == 1 and all(type(x) is int for x in (n, r, p)) and 2 <= n <= _MAX_N and n & (n - 1) == 0
                and 1 <= r <= 32 and 1 <= p <= 16 and len(bytes.fromhex(data["salt"])) == 16
                and len(bytes.fromhex(data["hash"])) == 32)
    except (KeyError, TypeError, ValueError):
        return False


def _check_file(values: Dict[str, Any]) -> List[Problem]:
    return [] if _shape_ok(values) else [Problem(None, "ui_auth.json tem formato inválido")]


AUTH_SECTION = Section(id="ui_auth", title="ui_auth.json", icon="lock", order=0, storage=JsonFile(lambda: config_path(FILE_NAME)),
                       fields=(Field("scrypt", "scrypt", "text"),), validate=_check_file)


def _normalise(password: str) -> bytes:
    return unicodedata.normalize("NFC", password).encode("utf-8")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(_normalise(password), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=128 * n * r * 2 + 1024 * 1024 * 8)


class AuthStore:
    def __init__(self, store: Optional[ConfigStore] = None, clock: Callable[[], float] = time.time,
                 params: Tuple[int, int, int] = DEFAULT_PARAMS):
        self._store = store or ConfigStore()
        self._clock = clock
        self._params = params
        self._code: Optional[str] = None
        self._code_expires = 0.0
        self._attempts = 0

    # -- state -------------------------------------------------------------------------------------
    def _read(self):
        loaded = self._store.load(AUTH_SECTION)
        return loaded

    def state(self) -> str:
        """"absent" (no password yet), "set", or "broken" (a file that cannot be used: remove it to start over)."""
        loaded = self._read()
        if not loaded.exists:
            return "absent" if not loaded.errors else "broken"
        return "set" if not loaded.errors and _shape_ok(loaded.values) else "broken"

    def needs_setup(self) -> bool:
        return self.state() == "absent"

    # -- the code ----------------------------------------------------------------------------------
    def start_setup(self) -> Optional[str]:
        """A new code to print in the log, replacing any earlier one; None when a password exists."""
        if not self.needs_setup():
            return None
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
        self._code = raw
        self._code_expires = self._clock() + CODE_LIFETIME_SECONDS
        self._attempts = 0
        return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"

    def _code_ok(self, typed: str) -> bool:
        if self._code is None or self._clock() > self._code_expires or self._attempts >= MAX_CODE_ATTEMPTS:
            return False
        cleaned = "".join(ch for ch in (typed or "").upper() if ch.isalnum())
        if hmac.compare_digest(cleaned.encode(), self._code.encode()):
            return True
        self._attempts += 1
        return False

    # -- rules for the password --------------------------------------------------------------------
    @staticmethod
    def _password_problem(password: Any) -> Optional[str]:
        if (not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH or len(password) > MAX_PASSWORD_LENGTH
                or "\x00" in password or not password.strip()):
            return "weak_password"
        return None

    def _write(self, password: str, expected_version: str) -> AuthResult:
        n, r, p = self._params
        salt = secrets.token_bytes(16)
        data = {"version": 1, "scrypt": {"n": n, "r": r, "p": p}, "salt": salt.hex(), "hash": _derive(password, salt, n, r, p).hex()}
        saved = self._store.save(AUTH_SECTION, data, expected_version)
        return AuthResult(True) if saved.ok else AuthResult(False, "unavailable")

    # -- creating, checking, changing --------------------------------------------------------------
    def create(self, code: str, password: str) -> AuthResult:
        if self.state() != "absent":
            return AuthResult(False, "already_set")
        if not self._code_ok(code):
            return AuthResult(False, "invalid_code")
        if self._password_problem(password):
            return AuthResult(False, "weak_password")
        result = self._write(password, ABSENT)
        if result.ok:
            self._code = None
        return result

    def verify(self, password: str) -> bool:
        loaded = self._read()
        if not loaded.exists or loaded.errors or not _shape_ok(loaded.values) or not isinstance(password, str) \
                or len(password) > MAX_PASSWORD_LENGTH:
            return False
        v = loaded.values
        s = v["scrypt"]
        derived = _derive(password, bytes.fromhex(v["salt"]), s["n"], s["r"], s["p"])
        return hmac.compare_digest(derived, bytes.fromhex(v["hash"]))

    def change(self, current: str, new: str) -> AuthResult:
        if not self.verify(current):
            return AuthResult(False, "wrong_password")
        if self._password_problem(new):
            return AuthResult(False, "weak_password")
        return self._write(new, self._read().version)
