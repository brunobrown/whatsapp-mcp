"""Password store (AUTH-02, AUTH-03, AUTH-06, AUTH-07): scrypt hash with its own salt, 0600 file, one-time setup code."""
import json
import os
import shutil
import stat
import unicodedata
from pathlib import Path

import pytest

import localconfig
from ui.security.auth import AuthStore

FIXTURES = Path(__file__).parent / "fixtures" / "ui"
KNOWN = "correct horse battery staple"          # the password the fixture file was made with


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def auth(clock):
    return AuthStore(clock=clock)


def auth_path():
    return Path(localconfig.config_dir()) / "ui_auth.json"


def make_password(auth, password="a long enough password"):
    code = auth.issue_code()
    out = auth.create(code, password)
    assert out.ok, out
    return password


def test_without_a_file_there_is_no_password_and_nothing_verifies(auth):
    assert auth.status() == "none"
    assert auth.verify(KNOWN) is False
    assert not auth_path().exists()


def test_only_the_hash_is_stored_in_a_0600_file(auth):
    password = make_password(auth, "this is my secret phrase")
    raw = auth_path().read_text()
    data = json.loads(raw)
    assert set(data) == {"version", "scrypt", "salt", "hash"}
    assert data["version"] == 1 and set(data["scrypt"]) == {"n", "r", "p"}
    assert len(bytes.fromhex(data["salt"])) == 16 and len(bytes.fromhex(data["hash"])) == 32
    assert password not in raw and "secret phrase" not in raw
    assert stat.S_IMODE(os.stat(auth_path()).st_mode) == 0o600
    assert auth.status() == "set"
    assert auth.verify(password) is True and auth.verify(password + "x") is False


def test_each_password_gets_its_own_salt(clock):
    first, second = AuthStore(clock=clock), AuthStore(clock=clock)
    make_password(first, "the same password!!")
    one = json.loads(auth_path().read_text())
    auth_path().unlink()
    make_password(second, "the same password!!")
    two = json.loads(auth_path().read_text())
    assert one["salt"] != two["salt"] and one["hash"] != two["hash"]


def test_the_fixture_written_by_0_6_0_verifies_the_known_password(auth):
    os.makedirs(auth_path().parent, exist_ok=True)
    shutil.copy(FIXTURES / "ui_auth.json", auth_path())
    os.chmod(auth_path(), 0o600)
    assert auth.status() == "set"
    assert auth.verify(KNOWN) is True
    assert auth.verify("correct horse battery stapl") is False
    assert auth.verify("") is False


def test_a_password_is_compared_after_unicode_normalisation(auth):
    password = make_password(auth, "café café café")
    assert auth.verify(unicodedata.normalize("NFD", password)) is True


@pytest.mark.parametrize("short", ["", "short", "a" * 11])
def test_a_password_under_12_characters_is_refused_on_create(auth, short):
    out = auth.create(auth.issue_code(), short)
    assert out.ok is False and out.reason == "weak_password"
    assert not auth_path().exists()


def test_twelve_characters_are_enough(auth):
    assert auth.create(auth.issue_code(), "a" * 12).ok is True


def test_a_password_under_12_characters_is_refused_on_change(auth):
    password = make_password(auth)
    out = auth.change(password, "a" * 11)
    assert out.ok is False and out.reason == "weak_password"
    assert auth.verify(password) is True


def test_change_needs_the_current_password(auth):
    password = make_password(auth)
    before = auth_path().read_bytes()
    out = auth.change("not the current one", "another long password")
    assert out.ok is False and out.reason == "wrong_password"
    assert auth_path().read_bytes() == before
    out = auth.change(password, "another long password")
    assert out.ok is True
    assert auth.verify("another long password") is True and auth.verify(password) is False
    assert stat.S_IMODE(os.stat(auth_path()).st_mode) == 0o600


def test_a_huge_password_is_refused_without_work(auth):
    make_password(auth)
    assert auth.verify("x" * 100_000) is False
    assert auth.change("x" * 100_000, "another long password").ok is False


# -- the setup code ---------------------------------------------------------------------------------
def test_the_code_works_once(auth):
    code = auth.issue_code()
    assert auth.create(code, "a long enough password").ok is True
    auth_path().unlink()
    out = auth.create(code, "a long enough password")
    assert out.ok is False and out.reason == "invalid_code"


def test_the_code_is_not_the_password_and_is_typed_forgivingly(auth):
    code = auth.issue_code()
    assert isinstance(code, str) and len(code.replace("-", "")) >= 12
    assert auth.create(code.lower().replace("-", " "), "a long enough password").ok is True


def test_a_wrong_code_is_refused_and_nothing_is_written(auth):
    auth.issue_code()
    out = auth.create("WRONG-CODE-0000", "a long enough password")
    assert out.ok is False and out.reason == "invalid_code"
    assert not auth_path().exists()


def test_the_code_expires_after_15_minutes(auth, clock):
    code = auth.issue_code()
    clock.now += 15 * 60 - 1
    assert auth.create(code, "a" * 12).ok is True            # still valid just before 15 minutes
    auth_path().unlink()
    code = auth.issue_code()
    clock.now += 15 * 60 + 1
    out = auth.create(code, "a" * 12)
    assert out.ok is False and out.reason == "invalid_code"
    assert auth.status() == "none"


def test_five_wrong_tries_burn_the_code(auth):
    code = auth.issue_code()
    for _ in range(5):
        assert auth.create("WRONG-CODE-0000", "a" * 12).reason == "invalid_code"
    out = auth.create(code, "a" * 12)                          # the right one comes too late
    assert out.ok is False and out.reason == "invalid_code"


def test_four_wrong_tries_leave_the_code_alive(auth):
    code = auth.issue_code()
    for _ in range(4):
        auth.create("WRONG-CODE-0000", "a" * 12)
    assert auth.create(code, "a" * 12).ok is True


def test_a_weak_password_does_not_burn_the_code(auth):
    code = auth.issue_code()
    assert auth.create(code, "short").reason == "weak_password"
    assert auth.create(code, "a long enough password").ok is True


def test_a_new_code_replaces_the_old_one_and_resets_the_tries(auth):
    old = auth.issue_code()
    for _ in range(5):
        auth.create("WRONG-CODE-0000", "a" * 12)
    new = auth.issue_code()
    assert new != old
    assert auth.create(old, "a" * 12).ok is False
    assert auth.create(new, "a" * 12).ok is True


def test_no_code_is_issued_once_a_password_exists(auth):
    make_password(auth)
    assert auth.issue_code() is None
    out = auth.create("ANY-CODE", "another long password")
    assert out.ok is False and out.reason == "already_set"


# -- a file that cannot be used --------------------------------------------------------------------
@pytest.mark.parametrize("content", [
    "not json",
    "[]",
    "{}",
    json.dumps({"version": 1, "scrypt": {"n": 32768, "r": 8, "p": 1}, "salt": "zz", "hash": "00"}),
    json.dumps({"version": 1, "scrypt": {"n": 3, "r": 8, "p": 1}, "salt": "00" * 16, "hash": "00" * 32}),
    json.dumps({"version": 1, "scrypt": {"n": 2 ** 40, "r": 8, "p": 1}, "salt": "00" * 16, "hash": "00" * 32}),
    json.dumps({"version": 2, "scrypt": {"n": 32768, "r": 8, "p": 1}, "salt": "00" * 16, "hash": "00" * 32}),
])
def test_a_malformed_file_is_broken_and_is_never_overwritten(auth, content):
    os.makedirs(auth_path().parent, exist_ok=True)
    auth_path().write_text(content)
    assert auth.status() == "broken"
    assert auth.verify(KNOWN) is False
    assert auth.issue_code() is None
    out = auth.create("ANY-CODE", "a long enough password")
    assert out.ok is False and out.reason == "broken"
    assert auth_path().read_text() == content
