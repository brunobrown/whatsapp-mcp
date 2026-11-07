"""ConfigStore: reading and writing the files of a section, safely (CFG-02, CFG-03, CFG-05, CFG-09, CFG-12)."""
import hashlib
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.core import store as store_module  # noqa: E402
from ui.core.registry import Field, JsonFile, Problem, Section  # noqa: E402
from ui.core.store import ABSENT, ConfigStore, config_path  # noqa: E402

SECRET = "tok-0123456789-do-not-show"


def make_section(path, problems=lambda values: []):
    return Section(id="demo", title="Demo", icon="x", order=1, storage=JsonFile(lambda: str(path)),
                   fields=(Field("mode", "Mode", "text"),), validate=problems)


def mode_must_be_set(values):
    return [] if values.get("mode") else [Problem("mode", "mode is required")]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_a_missing_file_loads_as_empty_with_the_absent_version(tmp_path):
    loaded = ConfigStore().load(make_section(tmp_path / "demo.json"))

    assert (loaded.values, loaded.version, loaded.errors, loaded.exists) == ({}, ABSENT, [], False)


def test_a_file_loads_with_the_hash_of_its_bytes_as_version(tmp_path):
    path = tmp_path / "demo.json"
    path.write_bytes(b'{"mode": "allowlist"}')

    loaded = ConfigStore().load(make_section(path))

    assert loaded.values == {"mode": "allowlist"} and loaded.version == sha(b'{"mode": "allowlist"}') and loaded.exists


def test_a_file_that_is_not_json_loads_with_an_error_and_is_left_alone(tmp_path):  # CFG-09
    path = tmp_path / "demo.json"
    content = ('{"mode": "x", "override_token": "%s"' % SECRET).encode()
    path.write_bytes(content)

    loaded = ConfigStore().load(make_section(path))

    assert loaded.values == {} and len(loaded.errors) == 1 and loaded.errors[0].field is None
    assert SECRET not in loaded.errors[0].message and SECRET not in repr(loaded)
    assert path.read_bytes() == content


def test_a_json_file_that_is_not_an_object_is_an_error(tmp_path):  # CFG-09
    path = tmp_path / "demo.json"
    path.write_text("[1, 2]")

    loaded = ConfigStore().load(make_section(path))

    assert loaded.values == {} and "object" in loaded.errors[0].message


def test_a_file_the_project_validator_refuses_loads_with_its_values_and_its_problems(tmp_path):  # CFG-09
    path = tmp_path / "demo.json"
    path.write_text('{"other": 1}')

    loaded = ConfigStore().load(make_section(path, mode_must_be_set))

    assert loaded.values == {"other": 1} and loaded.errors == [Problem("mode", "mode is required")]


def test_a_file_other_users_can_read_is_reported(tmp_path):
    path = tmp_path / "demo.json"
    path.write_text('{"mode": "x"}')
    os.chmod(path, 0o644)

    loaded = ConfigStore().load(make_section(path))

    if sys.platform == "win32":
        pytest.skip("POSIX modes")
    assert any("chmod 600" in w for w in loaded.warnings)


def test_save_refuses_invalid_values_and_writes_nothing(tmp_path):  # CFG-02
    path = tmp_path / "demo.json"
    section = make_section(path, mode_must_be_set)

    result = ConfigStore().save(section, {"mode": ""}, ABSENT)

    assert result.ok is False and result.errors == [Problem("mode", "mode is required")] and not result.conflict
    assert not path.exists() and list(tmp_path.iterdir()) == []


def test_save_does_not_touch_an_existing_file_when_the_values_are_invalid(tmp_path):  # CFG-02
    path = tmp_path / "demo.json"
    path.write_text('{"mode": "old"}')
    section = make_section(path, mode_must_be_set)
    version = ConfigStore().load(section).version

    ConfigStore().save(section, {"mode": ""}, version)

    assert path.read_text() == '{"mode": "old"}'


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_save_writes_a_private_file_even_with_an_open_umask(tmp_path):  # CFG-03
    path = tmp_path / "demo.json"
    previous = os.umask(0)
    try:
        result = ConfigStore().save(make_section(path), {"mode": "allowlist"}, ABSENT)
    finally:
        os.umask(previous)

    assert result.ok
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_save_writes_the_values_returns_the_new_version_and_leaves_no_temporary_file(tmp_path):  # CFG-03
    path = tmp_path / "demo.json"

    result = ConfigStore().save(make_section(path), {"mode": "allowlist", "list": ["é"]}, ABSENT)

    assert result.ok and json.loads(path.read_text(encoding="utf-8")) == {"mode": "allowlist", "list": ["é"]}
    assert result.new_version == sha(path.read_bytes())
    assert [p.name for p in tmp_path.iterdir()] == ["demo.json"]
    assert ConfigStore().load(make_section(path)).version == result.new_version


def test_a_failure_while_replacing_keeps_the_old_file_and_removes_the_temporary_one(tmp_path, monkeypatch):  # CFG-03
    path = tmp_path / "demo.json"
    path.write_text('{"mode": "old"}')
    section = make_section(path)
    version = ConfigStore().load(section).version

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(store_module.os, "replace", boom)

    result = ConfigStore().save(section, {"mode": "new"}, version)

    assert result.ok is False and "No space left" in result.errors[0].message
    assert path.read_text() == '{"mode": "old"}' and [p.name for p in tmp_path.iterdir()] == ["demo.json"]


def test_save_is_refused_when_file_changed_on_disk(tmp_path):  # CFG-05
    path = tmp_path / "demo.json"
    path.write_text('{"mode": "old"}')
    section = make_section(path)
    version = ConfigStore().load(section).version
    path.write_text('{"mode": "edited by hand"}')

    result = ConfigStore().save(section, {"mode": "from the interface"}, version)

    assert result.ok is False and result.conflict is True
    assert path.read_text() == '{"mode": "edited by hand"}'


def test_save_is_refused_when_a_file_appeared_after_the_load_found_none(tmp_path):  # CFG-05
    path = tmp_path / "demo.json"
    section = make_section(path)
    version = ConfigStore().load(section).version
    path.write_text('{"mode": "someone was faster"}')

    result = ConfigStore().save(section, {"mode": "mine"}, version)

    assert result.conflict is True and path.read_text() == '{"mode": "someone was faster"}'


def test_save_is_refused_when_the_file_was_deleted_after_the_load(tmp_path):  # CFG-05
    path = tmp_path / "demo.json"
    path.write_text('{"mode": "old"}')
    section = make_section(path)
    version = ConfigStore().load(section).version
    path.unlink()

    assert ConfigStore().save(section, {"mode": "new"}, version).conflict is True


def test_an_invalid_file_can_be_replaced_by_valid_values_when_the_version_is_current(tmp_path):  # CFG-09
    path = tmp_path / "demo.json"
    path.write_text("{broken")
    section = make_section(path)
    version = ConfigStore().load(section).version

    result = ConfigStore().save(section, {"mode": "fixed"}, version)

    assert result.ok and json.loads(path.read_text()) == {"mode": "fixed"}


def test_a_stale_version_cannot_replace_an_invalid_file(tmp_path):  # CFG-09
    path = tmp_path / "demo.json"
    path.write_text("{broken")

    result = ConfigStore().save(make_section(path), {"mode": "fixed"}, ABSENT)

    assert result.conflict and path.read_text() == "{broken"


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs a non-root POSIX user")
def test_a_directory_that_cannot_be_written_gives_an_error_and_no_exception(tmp_path):  # CFG-03
    folder = tmp_path / "ro"
    folder.mkdir()
    path = folder / "demo.json"
    os.chmod(folder, 0o500)
    try:
        result = ConfigStore().save(make_section(path), {"mode": "x"}, ABSENT)
    finally:
        os.chmod(folder, 0o700)

    assert result.ok is False and result.errors and not path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_a_missing_directory_is_created_private(tmp_path):
    path = tmp_path / "new" / "demo.json"

    assert ConfigStore().save(make_section(path), {"mode": "x"}, ABSENT).ok
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symbolic_link_is_replaced_not_followed(tmp_path):  # CFG-12
    target = tmp_path / "elsewhere.txt"
    target.write_text("not a configuration file")
    path = tmp_path / "demo.json"
    path.symlink_to(target)
    section = make_section(path)

    result = ConfigStore().save(section, {"mode": "x"}, ConfigStore().load(section).version)

    assert result.ok and target.read_text() == "not a configuration file" and not path.is_symlink()


@pytest.mark.parametrize("name", ["../x.json", "a/b.json", "a\\b.json", ".hidden", "", "..", "/etc/passwd", "x/../y"])
def test_a_file_name_with_a_separator_or_dots_is_refused(name):  # CFG-12
    with pytest.raises(ValueError, match="file name"):
        config_path(name)


def test_config_path_puts_a_plain_name_in_the_configuration_directory(tmp_path):
    assert config_path("channel.json") == os.path.join(str(tmp_path / "config"), "channel.json")


def test_the_store_has_no_way_to_be_pointed_at_another_file():
    import inspect
    assert list(inspect.signature(ConfigStore.save).parameters) == ["self", "section", "values", "expected_version"]
    assert list(inspect.signature(ConfigStore.load).parameters) == ["self", "section"]


def test_a_file_that_cannot_be_read_as_an_object_is_marked_unreadable_and_a_refused_one_is_not(tmp_path):  # CFG-09
    path = tmp_path / "demo.json"
    path.write_text("{broken")
    assert ConfigStore().load(make_section(path)).unreadable is True
    path.write_text("[1]")
    assert ConfigStore().load(make_section(path)).unreadable is True
    path.write_text('{"other": 1}')
    assert ConfigStore().load(make_section(path, mode_must_be_set)).unreadable is False
    assert ConfigStore().load(make_section(tmp_path / "absent.json")).unreadable is False


def test_save_is_atomic_and_file_mode_is_0600(tmp_path, monkeypatch):  # CFG-03
    path = tmp_path / "demo.json"
    section = make_section(path)
    previous = os.umask(0)
    try:
        assert ConfigStore().save(section, {"mode": "old"}, ABSENT).ok
    finally:
        os.umask(previous)
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    version = ConfigStore().load(section).version

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(store_module.os, "replace", boom)
    result = ConfigStore().save(section, {"mode": "new"}, version)

    assert result.ok is False and json.loads(path.read_text()) == {"mode": "old"}      # the old file survives a failed replace
    assert [p.name for p in tmp_path.iterdir()] == ["demo.json"]                      # and no temporary file is left
