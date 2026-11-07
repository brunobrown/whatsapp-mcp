"""The channel section: the fields of channel.json, validated by channel.parse_config (CFG-01, CFG-02, CFG-11)."""
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import channel  # noqa: E402
from ui.core.registry import Problem, default  # noqa: E402
from ui.core.store import ABSENT, ConfigStore  # noqa: E402
from ui.sections.channel import SECTION  # noqa: E402

NUMBERS = [f for f in SECTION.fields if f.kind in ("int", "float")]


def on(**extra):
    return {"enabled": True, "profiles": ["support"], **extra}


def test_the_section_is_registered_and_restarts_the_server():  # CFG-08
    assert SECTION in default.sections() and SECTION.restart == "server"


def test_the_file_is_where_the_server_reads_it(tmp_path, monkeypatch):  # CFG-12
    assert SECTION.storage.path() == channel.config_path() == str(tmp_path / "config" / "channel.json")
    monkeypatch.setenv("WHATSAPP_CHANNEL_CONFIG", str(tmp_path / "elsewhere.json"))
    assert SECTION.storage.path() == str(tmp_path / "elsewhere.json")


def test_the_fields_are_exactly_the_keys_the_server_accepts():  # CFG-11
    assert {f.key for f in SECTION.fields} == channel._KEYS


def test_the_defaults_shown_are_the_defaults_of_the_server():  # CFG-01
    defaults = {f.key: f.default for f in SECTION.fields}
    cfg = channel.Config()

    assert defaults["enabled"] is cfg.enabled and defaults["include_excerpt"] is cfg.include_excerpt
    for key in ("excerpt_chars", "batch_seconds", "min_interval_seconds", "max_batch", "poll_interval_seconds"):
        assert defaults[key] == getattr(cfg, key)


@pytest.mark.parametrize("field", NUMBERS, ids=lambda f: f.key)
def test_the_bounds_of_a_number_are_the_bounds_the_server_applies(field):  # CFG-02, CFG-11
    low, high = field.bounds
    step = 1 if field.kind == "int" else 0.01

    assert SECTION.validate(on(**{field.key: low})) == [] and SECTION.validate(on(**{field.key: high})) == []
    for outside in (low - step, high + step):
        assert [p.field for p in SECTION.validate(on(**{field.key: outside}))] == [field.key]
        assert field.check(outside) is not None            # the form refuses it before it is even sent
    assert field.check(low) is None and field.check(high) is None


def test_a_valid_channel_has_no_problems():  # CFG-02
    assert SECTION.validate(on(polls=["ID1"], include_excerpt=True, excerpt_chars=80)) == []
    assert SECTION.validate({"enabled": False}) == []


@pytest.mark.parametrize("values, field", [
    (on(profiles="a"), "profiles"),
    (on(profiles=[""]), "profiles"),
    (on(profiles=["p"] * 21), "profiles"),
    (on(include_excerpt="yes"), "include_excerpt"),
    (on(max_batch=0), "max_batch"),
    ({"enabled": True}, None),
    ({"enabled": True, "profiles": ["a"], "colour": 1}, None),
])
def test_an_invalid_channel_is_refused_with_the_field_when_there_is_one(values, field):  # CFG-02
    problems = SECTION.validate(values)

    assert len(problems) == 1 and problems[0].field == field and problems[0].message


def test_a_file_that_is_not_an_object_is_refused():  # CFG-02
    assert SECTION.validate([]) == [Problem(None, "channel.json must be a JSON object")]


def test_what_is_saved_is_a_channel_the_server_then_loads_as_enabled():  # CFG-03
    values = on(polls=["ID1"], excerpt_chars=90, batch_seconds=0)

    assert ConfigStore().save(SECTION, values, ABSENT).ok

    cfg = channel.load_config()
    assert (cfg.error, cfg.enabled, cfg.profiles, cfg.polls, cfg.excerpt_chars, cfg.batch_seconds) == (None, True, ["support"], ["ID1"], 90, 0.0)
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(channel.config_path()).st_mode) == 0o600


def test_the_excerpt_option_warns_about_text_from_strangers():  # CFG-01
    assert "desconhecido" in SECTION.field("include_excerpt").risk
