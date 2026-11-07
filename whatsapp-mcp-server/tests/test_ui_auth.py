"""The password of the interface: created once with a code from the log, kept as a scrypt hash (AUTH-02, 03, 06, 07)."""
import json
import os
import re
import stat
import sys
import unicodedata

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.core.auth import CODE_LIFETIME_SECONDS, MAX_CODE_ATTEMPTS, MIN_PASSWORD_LENGTH, AuthStore  # noqa: E402

GOOD = "correct horse battery"
FAST = (16, 8, 1)       # cheap scrypt parameters for tests; the file records the ones it was written with


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def auth(clock):
    return AuthStore(clock=clock, params=FAST)


def path(tmp_path):
    return tmp_path / "config" / "ui_auth.json"


def test_without_a_file_the_password_is_not_set_up(auth):  # AUTH-02
    assert auth.state() == "absent" and auth.needs_setup() is True


def test_the_code_has_a_readable_shape(auth):  # AUTH-02
    code = auth.start_setup()

    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", code)


def test_a_password_is_created_with_the_code_and_then_it_is_no_longer_to_set_up(auth, tmp_path):  # AUTH-02
    code = auth.start_setup()

    result = auth.create(code, GOOD)

    assert result.ok and auth.state() == "set" and auth.needs_setup() is False
    assert auth.verify(GOOD) is True


def test_setup_requires_valid_single_use_code(auth, tmp_path):  # AUTH-02
    code = auth.start_setup()

    assert auth.create("AAAA-BBBB-CCCC", GOOD).reason == "invalid_code" and not path(tmp_path).exists()
    assert auth.create("", GOOD).reason == "invalid_code"
    assert auth.create(code, GOOD).ok
    assert auth.create(code, "another long password").reason == "already_set"      # the code does not work twice
    assert auth.verify(GOOD) and not auth.verify("another long password")


def test_the_code_is_accepted_however_it_is_typed(auth):  # AUTH-02
    code = auth.start_setup()

    assert auth.create(" " + code.lower().replace("-", " ") + " ", GOOD).ok


def test_the_code_expires(auth, clock, tmp_path):  # AUTH-02
    code = auth.start_setup()
    clock.now += CODE_LIFETIME_SECONDS + 1

    assert auth.create(code, GOOD).reason == "invalid_code" and not path(tmp_path).exists()


def test_the_code_still_works_just_before_it_expires(auth, clock):  # AUTH-02
    code = auth.start_setup()
    clock.now += CODE_LIFETIME_SECONDS - 1

    assert auth.create(code, GOOD).ok


def test_too_many_wrong_codes_burn_the_code(auth):  # AUTH-02
    code = auth.start_setup()
    for _ in range(MAX_CODE_ATTEMPTS):
        assert auth.create("AAAA-BBBB-CCCC", GOOD).reason == "invalid_code"

    assert auth.create(code, GOOD).reason == "invalid_code"          # even the right one is gone
    assert auth.create(auth.start_setup(), GOOD).ok                    # a new code works


def test_a_new_code_replaces_the_old_one(auth):  # AUTH-02
    first = auth.start_setup()
    second = auth.start_setup()

    assert first != second and auth.create(first, GOOD).reason == "invalid_code" and auth.create(second, GOOD).ok


def test_no_code_is_issued_once_there_is_a_password(auth):  # AUTH-02
    auth.create(auth.start_setup(), GOOD)

    assert auth.start_setup() is None


@pytest.mark.parametrize("password, reason", [("short", "weak_password"), ("x" * (MIN_PASSWORD_LENGTH - 1), "weak_password"),
                                               ("x" * 257, "weak_password"), ("twelve chars\x00", "weak_password"),
                                               ("            ", "weak_password")])
def test_password_shorter_than_twelve_is_refused(auth, tmp_path, password, reason):  # AUTH-06
    code = auth.start_setup()

    assert auth.create(code, password).reason == reason and not path(tmp_path).exists()
    assert auth.create(code, "x" * MIN_PASSWORD_LENGTH + "!").ok        # a refused password does not burn the code


def test_exactly_twelve_characters_are_enough(auth):  # AUTH-06
    assert auth.create(auth.start_setup(), "a" * MIN_PASSWORD_LENGTH).ok


def test_twelve_characters_are_counted_after_normalisation(auth, tmp_path):  # AUTH-06
    decomposed = "a\u0301" * 6                       # twelve code points, six visible characters

    assert auth.create(auth.start_setup(), decomposed).reason == "weak_password" and not path(tmp_path).exists()
    assert auth.create(auth.start_setup(), "a\u0301" * MIN_PASSWORD_LENGTH).ok          # twelve visible characters are enough


def test_twelve_characters_means_characters_not_bytes(auth):  # AUTH-06
    assert auth.create(auth.start_setup(), "é" * MIN_PASSWORD_LENGTH).ok


def test_auth_file_holds_hash_only_with_mode_0600(auth, tmp_path):  # AUTH-03
    code = auth.start_setup()
    auth.create(code, GOOD)

    raw = path(tmp_path).read_text()
    data = json.loads(raw)

    assert set(data) == {"version", "scrypt", "salt", "hash"} and data["version"] == 1
    assert data["scrypt"] == {"n": 16, "r": 8, "p": 1}
    assert re.fullmatch(r"[0-9a-f]{32}", data["salt"]) and re.fullmatch(r"[0-9a-f]{64}", data["hash"])
    assert GOOD not in raw and code not in raw and "correct" not in raw
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path(tmp_path)).st_mode) == 0o600


def test_the_same_password_gets_a_different_salt_and_hash_each_time(clock, tmp_path):  # AUTH-03
    first = AuthStore(clock=clock, params=FAST)
    first.create(first.start_setup(), GOOD)
    one = json.loads(path(tmp_path).read_text())
    path(tmp_path).unlink()
    second = AuthStore(clock=clock, params=FAST)
    second.create(second.start_setup(), GOOD)
    two = json.loads(path(tmp_path).read_text())

    assert one["salt"] != two["salt"] and one["hash"] != two["hash"]


def test_verify_accepts_the_password_and_nothing_else(auth):
    auth.create(auth.start_setup(), GOOD)

    assert auth.verify(GOOD) is True
    for wrong in ("", "correct horse batter", GOOD + " ", GOOD.upper(), "x" * 100000):
        assert auth.verify(wrong) is False


def test_verify_is_false_when_there_is_no_password_or_the_file_is_damaged(auth, tmp_path):
    assert auth.verify(GOOD) is False
    path(tmp_path).parent.mkdir()
    path(tmp_path).write_text("{broken")

    assert auth.verify(GOOD) is False and auth.state() == "broken" and auth.needs_setup() is False


def test_a_file_written_with_other_scrypt_parameters_still_verifies(clock, tmp_path):
    old = AuthStore(clock=clock, params=(16, 8, 1))
    old.create(old.start_setup(), GOOD)

    assert AuthStore(clock=clock, params=(32, 8, 1)).verify(GOOD) is True


def test_the_same_password_typed_with_other_unicode_forms_matches(auth):
    composed = "señor contraseña larga"
    auth.create(auth.start_setup(), composed)

    assert auth.verify(unicodedata.normalize("NFD", composed)) is True


def test_a_file_with_nonsense_parameters_is_damaged_not_a_crash(auth, tmp_path):
    path(tmp_path).parent.mkdir()
    path(tmp_path).write_text(json.dumps({"version": 1, "scrypt": {"n": 3, "r": 0, "p": -1}, "salt": "zz", "hash": "00"}))

    assert auth.state() == "broken" and auth.verify(GOOD) is False


def test_changing_the_password_requires_the_current_one(auth):  # AUTH-07
    auth.create(auth.start_setup(), GOOD)

    assert auth.change("not the password", "a brand new password").reason == "wrong_password"
    assert auth.verify(GOOD) and not auth.verify("a brand new password")


def test_change_password_requires_current_password(auth):  # AUTH-07
    auth.create(auth.start_setup(), GOOD)

    assert auth.change("not the current one", "a brand new password").reason == "wrong_password"
    assert auth.change("", "a brand new password").reason == "wrong_password"
    assert auth.verify(GOOD) is True and auth.verify("a brand new password") is False       # a refused change changes nothing
    assert auth.change(GOOD, "a brand new password").ok
    assert auth.verify("a brand new password") is True and auth.verify(GOOD) is False


def test_the_new_password_must_also_be_long_enough(auth):  # AUTH-06, AUTH-07
    auth.create(auth.start_setup(), GOOD)

    assert auth.change(GOOD, "short").reason == "weak_password" and auth.verify(GOOD)


def test_changing_without_a_password_set_is_refused(auth):  # AUTH-07
    assert auth.change(GOOD, "a brand new password").reason == "wrong_password"
