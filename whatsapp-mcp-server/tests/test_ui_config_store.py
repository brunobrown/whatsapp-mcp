"""Atomic JSON store (CFG-03, CFG-05, CFG-09, CFG-12): versioned reads, atomic 0600 writes, conflicts refused."""
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from ui.config.fields import Field, Problem
from ui.config.registry import EnvKeys, JsonFile, Registry, Section
from ui.config.store import ABSENT, ConfigStore

FIXTURES = Path(__file__).parent / "fixtures" / "ui"


@pytest.fixture
def env(tmp_path):
    target = tmp_path / "cfg" / "thing.json"
    calls = {"problems": []}
    reg = Registry()
    reg.register(Section(id="thing", title="Thing", order=1, storage=JsonFile(lambda: str(target)),
                         fields=(Field("name", "Name", "text"),), validator=lambda values: list(calls["problems"])))
    reg.register(Section(id="vars", title="Vars", order=2, storage=EnvKeys(), fields=()))
    return ConfigStore(reg), target, calls


def test_a_missing_file_loads_as_absent_with_no_values(env):
    store, target, _ = env
    loaded = store.load("thing")
    assert loaded.exists is False and loaded.version == ABSENT and loaded.values == {} and loaded.error is None
    assert not target.exists()                       # loading never creates anything


def test_a_write_leaves_a_0600_file_and_the_directory_private(env):
    store, target, _ = env
    result = store.save("thing", {"name": "x"}, ABSENT)
    assert result.ok and result.errors == []
    assert json.loads(target.read_text()) == {"name": "x"}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_the_version_returned_by_a_save_is_the_one_a_load_sees(env):
    store, _, _ = env
    saved = store.save("thing", {"name": "x"}, ABSENT)
    assert store.load("thing").version == saved.version != ABSENT


def test_a_replaced_file_keeps_0600_even_if_the_old_one_was_open(env):
    store, target, _ = env
    store.save("thing", {"name": "x"}, ABSENT)
    os.chmod(target, 0o644)
    version = store.load("thing").version
    assert store.save("thing", {"name": "y"}, version).ok
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_no_partial_file_and_no_leftover_when_the_replace_fails(env, monkeypatch):
    store, target, _ = env
    store.save("thing", {"name": "old"}, ABSENT)
    before = target.read_bytes()
    version = store.load("thing").version

    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    result = store.save("thing", {"name": "new"}, version)
    assert result.ok is False and result.errors and result.conflict is False
    assert target.read_bytes() == before
    assert [p.name for p in target.parent.iterdir()] == ["thing.json"]


def test_an_unwritable_directory_reports_the_error_and_keeps_the_previous_value(env, monkeypatch):
    store, target, _ = env
    store.save("thing", {"name": "old"}, ABSENT)
    before = target.read_bytes()
    version = store.load("thing").version

    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(tempfile, "mkstemp", denied)
    result = store.save("thing", {"name": "new"}, version)
    assert result.ok is False
    assert "could not be written" in result.errors[0].message and result.errors[0].key is None
    assert target.read_bytes() == before


def test_a_file_changed_since_loading_is_a_conflict_and_is_not_overwritten(env):
    store, target, _ = env
    store.save("thing", {"name": "one"}, ABSENT)
    version = store.load("thing").version
    target.write_text(json.dumps({"name": "edited elsewhere"}))
    result = store.save("thing", {"name": "mine"}, version)
    assert result.ok is False and result.conflict is True
    assert json.loads(target.read_text()) == {"name": "edited elsewhere"}


def test_a_file_that_appeared_after_an_absent_load_is_a_conflict(env):
    store, target, _ = env
    target.parent.mkdir(parents=True)
    target.write_text('{"name": "someone else"}')
    result = store.save("thing", {"name": "mine"}, ABSENT)
    assert result.conflict is True
    assert json.loads(target.read_text()) == {"name": "someone else"}


def test_a_file_removed_since_loading_is_a_conflict(env):
    store, target, _ = env
    store.save("thing", {"name": "one"}, ABSENT)
    version = store.load("thing").version
    target.unlink()
    assert store.save("thing", {"name": "mine"}, version).conflict is True
    assert not target.exists()


def test_values_that_fail_validation_are_refused_and_nothing_is_written(env):
    store, target, calls = env
    calls["problems"] = [Problem("name", "is not acceptable")]
    result = store.save("thing", {"name": "x"}, ABSENT)
    assert result.ok is False and result.errors == [Problem("name", "is not acceptable")]
    assert not target.exists()


def test_an_invalid_json_file_is_reported_and_not_overwritten_on_its_own(env):
    store, target, _ = env
    target.parent.mkdir(parents=True)
    target.write_text("{ this is not json ")
    loaded = store.load("thing")
    assert loaded.unreadable is True and loaded.error and "not valid JSON" in loaded.error
    assert "this is not json" not in loaded.error
    result = store.save("thing", {"name": "x"}, loaded.version)
    assert result.ok is False and result.needs_confirmation is True
    assert target.read_text() == "{ this is not json "


def test_an_unreadable_file_is_replaced_only_after_confirmation(env):
    store, target, _ = env
    target.parent.mkdir(parents=True)
    target.write_text("[1, 2]")                                  # valid JSON, not an object
    loaded = store.load("thing")
    assert loaded.unreadable is True and "object" in loaded.error
    result = store.save("thing", {"name": "x"}, loaded.version, confirm_replace=True)
    assert result.ok is True
    assert json.loads(target.read_text()) == {"name": "x"}


def test_a_file_that_fails_validation_is_loaded_with_the_problems_and_can_be_corrected(env):
    store, target, calls = env
    target.parent.mkdir(parents=True)
    target.write_text('{"name": "bad"}')
    calls["problems"] = [Problem("name", "is not acceptable")]
    loaded = store.load("thing")
    assert loaded.values == {"name": "bad"} and loaded.problems == [Problem("name", "is not acceptable")]
    calls["problems"] = []
    assert store.save("thing", {"name": "good"}, loaded.version).ok is True


def test_a_file_that_cannot_be_read_is_reported_and_never_replaced(env, monkeypatch):
    store, target, _ = env
    target.parent.mkdir(parents=True)
    target.write_text('{"name": "x"}')
    real_open = open

    def deny(path, *a, **k):
        if str(path) == str(target):
            raise PermissionError(13, "Permission denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", deny)
    loaded = store.load("thing")
    assert loaded.unreadable is True and "cannot be read" in loaded.error
    result = store.save("thing", {"name": "y"}, loaded.version, confirm_replace=True)
    assert result.ok is False
    monkeypatch.undo()
    assert json.loads(target.read_text()) == {"name": "x"}


def test_a_file_open_to_other_users_loads_with_a_warning(env):
    store, target, _ = env
    store.save("thing", {"name": "x"}, ABSENT)
    os.chmod(target, 0o644)
    assert store.load("thing").warnings


def test_an_unknown_section_or_one_without_a_json_file_never_reaches_a_path(env, monkeypatch):
    store, _, _ = env
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: (opened.append(p), real_open(p, *a, **k))[1])
    for bad in ("../../etc/passwd", "missing", "/etc/passwd"):
        with pytest.raises(KeyError):
            store.load(bad)
        with pytest.raises(KeyError):
            store.save(bad, {}, ABSENT)
    with pytest.raises(ValueError):
        store.load("vars")
    assert opened == []


@pytest.mark.parametrize("name", ["send_policy", "monitor_profiles", "webhooks", "channel", "ui_settings"])
def test_the_files_written_by_0_6_0_load_and_the_files_written_now_have_the_same_shape(tmp_path, name):
    target = tmp_path / f"{name}.json"
    shutil.copy(FIXTURES / f"{name}.json", target)
    reg = Registry()
    reg.register(Section(id=name, title=name, order=1, storage=JsonFile(lambda: str(target)), fields=()))
    store = ConfigStore(reg)
    loaded = store.load(name)
    assert loaded.error is None and loaded.exists and loaded.values == json.loads((FIXTURES / f"{name}.json").read_text())
    other = tmp_path / "again" / f"{name}.json"
    reg2 = Registry()
    reg2.register(Section(id=name, title=name, order=1, storage=JsonFile(lambda: str(other)), fields=()))
    assert ConfigStore(reg2).save(name, loaded.values, ABSENT).ok
    assert other.read_bytes() == (FIXTURES / f"{name}.json").read_bytes()
