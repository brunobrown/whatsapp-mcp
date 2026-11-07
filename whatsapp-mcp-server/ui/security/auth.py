"""The password of the interface.

Only a scrypt hash with its own random salt is kept, in ui_auth.json (mode 0600, written atomically). The first
password is created with a one-time setup code that the caller prints in the bridge log: it lives 15 minutes,
works once and is burnt by 5 wrong tries. A password has at least 12 characters, counted after Unicode
normalisation (the form that is hashed). A file that cannot be used is reported as broken and never overwritten.
"""
import hashlib
import hmac
import os
import secrets
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import localconfig
from ui.config.fields import Problem
from ui.config.registry import JsonFile, Registry, Section
from ui.config.store import ABSENT, ConfigStore

FILE_NAME = "ui_auth.json"
SECTION_ID = "ui_auth"
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
CODE_LIFETIME_SECONDS = 15 * 60
MAX_CODE_TRIES = 5
SCRYPT_PARAMS = (2 ** 15, 8, 1)                   # n, r, p
_MAX_N = 2 ** 20
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0, 1, I or O: easy to read from a log
_CODE_LENGTH = 12


@dataclass(frozen=True)
class Outcome:
    ok: bool
    # invalid_code, weak_password, wrong_password, already_set, broken, unavailable
    reason: Optional[str] = None


def _well_formed(data: Any) -> bool:
    try:
        params = data["scrypt"]
        n, r, p = params["n"], params["r"], params["p"]
        return (data["version"] == 1 and set(params) == {"n", "r", "p"} and all(type(x) is int for x in (n, r, p))
                and 2 <= n <= _MAX_N and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16
                and len(bytes.fromhex(data["salt"])) == 16 and len(bytes.fromhex(data["hash"])) == 32)
    except (KeyError, TypeError, ValueError):
        return False


def _file_problems(values: Dict[str, Any]) -> List[Problem]:
    return [] if _well_formed(values) else [Problem(None, "ui_auth.json has an invalid format")]


def _path() -> str:
    return os.path.join(localconfig.config_dir(), FILE_NAME)


AUTH_SECTION = Section(id=SECTION_ID, title=FILE_NAME, order=0, listed=False, storage=JsonFile(_path),
                       validator=_file_problems)


def _password_bytes(password: str) -> bytes:
    return unicodedata.normalize("NFC", password).encode("utf-8")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(_password_bytes(password), salt=salt, n=n, r=r, p=p, dklen=32,
                          maxmem=256 * n * r + 16 * 1024 * 1024)


def _weak(password: Any) -> bool:
    if not isinstance(password, str) or "\x00" in password:
        return True
    return not MIN_PASSWORD_LENGTH <= len(unicodedata.normalize("NFC", password)) <= MAX_PASSWORD_LENGTH


def _clean_code(typed: Any) -> str:
    return "".join(ch for ch in typed.upper() if ch.isalnum()) if isinstance(typed, str) else ""


class AuthStore:
    def __init__(self, store: Optional[ConfigStore] = None, clock: Callable[[], float] = time.monotonic,
                 params: Tuple[int, int, int] = SCRYPT_PARAMS):
        if store is None:
            registry = Registry()
            registry.register(AUTH_SECTION)
            store = ConfigStore(registry)
        self._store = store
        self._clock = clock
        self._params = params
        self._code: Optional[str] = None
        self._expires = 0.0
        self._tries = 0

    # -- state -------------------------------------------------------------------------------------
    def status(self) -> str:
        """"none" (no password yet), "set", or "broken" (a file that cannot be used)."""
        loaded = self._store.load(SECTION_ID)
        if not loaded.exists:
            return "broken" if loaded.error else "none"
        return "broken" if loaded.error or loaded.problems else "set"

    def verify(self, password: Any) -> bool:
        loaded = self._store.load(SECTION_ID)
        if (not loaded.exists or loaded.error or loaded.problems or not isinstance(password, str)
                or len(password) > MAX_PASSWORD_LENGTH):
            return False
        v = loaded.values
        s = v["scrypt"]
        derived = _derive(password, bytes.fromhex(v["salt"]), s["n"], s["r"], s["p"])
        return hmac.compare_digest(derived, bytes.fromhex(v["hash"]))

    # -- the setup code ----------------------------------------------------------------------------
    def issue_code(self) -> Optional[str]:
        """A new setup code for the caller to print in the log, replacing any earlier one. None unless a password
        can still be created."""
        if self.status() != "none":
            return None
        raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        self._code, self._expires, self._tries = raw, self._clock() + CODE_LIFETIME_SECONDS, 0
        return "-".join(raw[i:i + 4] for i in range(0, _CODE_LENGTH, 4))

    def _accept_code(self, typed: Any) -> bool:
        if self._code is None or self._clock() > self._expires or self._tries >= MAX_CODE_TRIES:
            return False
        if hmac.compare_digest(_clean_code(typed).encode(), self._code.encode()):
            return True
        self._tries += 1
        return False

    # -- creating and changing ---------------------------------------------------------------------
    def _write(self, password: str, expected_version: str) -> Outcome:
        n, r, p = self._params
        salt = secrets.token_bytes(16)
        record = {"version": 1, "scrypt": {"n": n, "r": r, "p": p}, "salt": salt.hex(),
                  "hash": _derive(password, salt, n, r, p).hex()}
        return Outcome(True) if self._store.save(SECTION_ID, record, expected_version).ok else Outcome(False, "unavailable")

    def create(self, code: Any, password: Any) -> Outcome:
        """Create the first password with the setup code. A refused password does not spend the code."""
        state = self.status()
        if state != "none":
            return Outcome(False, "already_set" if state == "set" else "broken")
        if not self._accept_code(code):
            return Outcome(False, "invalid_code")
        if _weak(password):
            return Outcome(False, "weak_password")
        out = self._write(password, ABSENT)
        if out.ok:
            self._code = None
        return out

    def change(self, current: Any, new: Any) -> Outcome:
        if not self.verify(current):
            return Outcome(False, "wrong_password")
        if _weak(new):
            return Outcome(False, "weak_password")
        return self._write(new, self._store.load(SECTION_ID).version)
