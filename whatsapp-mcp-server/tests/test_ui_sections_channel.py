"""Channel section (CFG-01, CFG-02, CFG-11, CFG-03): the fields of channel.json, checked by channel.py."""
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

import channel
from ui.config.form import FormFlow
from ui.config.registry import JsonFile, Registry
from ui.config.store import ConfigStore
from ui.sections import channel as section_module

FIXTURE = Path(__file__).parent / "fixtures" / "ui" / "channel.json"
SECTION = section_module.SECTION


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    target = tmp_path / "cfg" / "channel.json"
    monkeypatch.setenv("WHATSAPP_CHANNEL_CONFIG", str(target))
    reg = Registry()
    reg.register(SECTION)
    store = ConfigStore(reg)

    def flow():
        f = FormFlow("channel", reg, store)
        f.load()
        return f

    return flow, target


def put_fixture(target):
    os.makedirs(target.parent, exist_ok=True)
    shutil.copy(FIXTURE, target)
    os.chmod(target, 0o600)


def test_the_section_describes_the_file_the_server_reads(ctx):
    _, target = ctx
    assert SECTION.id == "channel" and SECTION.restart == "server" and not SECTION.read_only
    assert isinstance(SECTION.storage, JsonFile) and SECTION.storage.path() == str(target)


def test_every_field_is_a_key_channel_py_reads_and_every_key_has_a_field():
    keys = {f.key for f in SECTION.fields}
    assert keys == channel._KEYS


def test_the_bounds_and_defaults_are_the_ones_of_the_project():
    parsed = channel.parse_config({"enabled": True, "profiles": ["p"]})
    for f in SECTION.fields:
        if f.key in ("excerpt_chars", "batch_seconds", "min_interval_seconds", "max_batch", "poll_interval_seconds"):
            assert getattr(parsed, f.key) == f.default, f.key
            low, high = f.bounds
            for value in (low, high):
                assert channel.parse_config({"enabled": True, "profiles": ["p"], f.key: value}).error is None, (f.key, value)
            for value in (low - 1, high + 1):
                assert channel.parse_config({"enabled": True, "profiles": ["p"], f.key: value}).error, (f.key, value)


def test_the_fixture_loads_and_saves_in_the_same_shape(ctx):
    flow, target = ctx
    put_fixture(target)
    before = json.loads(target.read_text())
    f = flow()
    view = f.view()
    assert view.status == "ready" and view.values["profiles"] == ["assistant"] and view.values["excerpt_chars"] == 120
    f.apply({"excerpt_chars": "100"})
    assert f.save().ok is True
    after = json.loads(target.read_text())
    assert set(after) == set(before) and after["excerpt_chars"] == 100
    assert {k: v for k, v in after.items() if k != "excerpt_chars"} == {k: v for k, v in before.items() if k != "excerpt_chars"}


def test_the_file_keeps_mode_0600_after_a_save(ctx):
    flow, target = ctx
    put_fixture(target)
    os.chmod(target, 0o600)
    f = flow()
    f.apply({"include_excerpt": True})
    assert f.save().ok is True
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    g = flow()
    g.apply({"max_batch": "30"})
    assert g.save().ok is True
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert channel.load_config().error is None                   # the channel itself accepts what was written


def test_a_new_file_is_created_private_and_the_channel_accepts_it(ctx):
    flow, target = ctx
    f = flow()
    f.apply({"enabled": True, "profiles": "support\nsales"})
    assert f.save().ok is True
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    cfg = channel.load_config()
    assert cfg.enabled and cfg.profiles == ["support", "sales"] and cfg.error is None


def test_the_project_refuses_an_enabled_channel_with_nothing_to_announce(ctx):
    flow, target = ctx
    f = flow()
    f.apply({"enabled": True})
    outcome = f.save()
    assert outcome.ok is False and "profiles" in outcome.errors and "at least one" in outcome.errors["profiles"]
    assert not target.exists()


@pytest.mark.parametrize("key,raw,fragment", [
    ("excerpt_chars", "300", "at most 280"),
    ("max_batch", "0", "at least 1"),
    ("batch_seconds", "abc", "number"),
    ("excerpt_chars", "1.5", "whole number"),
])
def test_values_out_of_range_are_refused_beside_their_field(ctx, key, raw, fragment):
    flow, target = ctx
    f = flow()
    f.apply({"enabled": True, "profiles": "p", key: raw})
    outcome = f.save()
    assert outcome.ok is False and fragment in outcome.errors[key]
    assert not target.exists()


def test_saving_calls_channel_py_validation(ctx, monkeypatch):
    flow, target = ctx
    seen = []
    monkeypatch.setattr(channel, "parse_config", lambda raw, name="x": seen.append(raw) or channel.Config(error="max_batch is refused by the project"))
    f = flow()
    f.apply({"max_batch": "5"})
    outcome = f.save()
    assert seen and seen[-1]["max_batch"] == 5
    assert outcome.errors == {"max_batch": "max_batch is refused by the project"}
    assert not target.exists()


def test_the_risky_switches_warn():
    risky = {f.key for f in SECTION.fields if f.risk}
    assert {"enabled", "include_excerpt"} <= risky
