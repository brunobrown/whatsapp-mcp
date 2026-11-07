"""The send policy section: the fields of send_policy.json, validated by the project's own rules (CFG-01, CFG-02, CFG-11)."""
import json
import os
import re
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import filemode  # noqa: E402
import send_policy  # noqa: E402
from ui.core.problems import from_message, listing  # noqa: E402
from ui.core.registry import Problem, all_sections, default  # noqa: E402
from ui.core.secrets import mask  # noqa: E402
from ui.core.store import ABSENT, ConfigStore  # noqa: E402
from ui.sections.send_policy import SECTION  # noqa: E402

SERVER = os.path.dirname(os.path.dirname(__file__))


def test_the_section_is_registered_and_found_by_discovery():  # EXT-01
    assert SECTION in default.sections() and "send_policy" in [s.id for s in all_sections()]


def test_the_file_is_the_one_the_send_code_reads_at_the_moment_it_is_used(tmp_path, monkeypatch):  # CFG-12
    monkeypatch.setattr(send_policy, "POLICY_PATH", str(tmp_path / "elsewhere.json"))

    assert SECTION.storage.path() == str(tmp_path / "elsewhere.json")


def test_every_field_is_a_key_the_project_reads_and_every_key_of_the_example_is_a_field():  # CFG-11
    source = open(os.path.join(SERVER, "send_policy.py")).read() + open(os.path.join(SERVER, "taint.py")).read()
    example = json.load(open(os.path.join(SERVER, "send_policy.example.json")))

    for field in SECTION.fields:
        assert re.search(rf'["\']{field.key}["\']', source), f"{field.key} is not read anywhere in the project"
    assert set(example) <= {f.key for f in SECTION.fields}


def test_defaults_show_when_there_is_no_file():  # CFG-01
    loaded = ConfigStore().load(SECTION)

    assert loaded.values == {} and loaded.version == ABSENT
    assert {f.key: f.default for f in SECTION.fields if f.default is not None} == {
        "mode": "denylist", "allow": [], "deny": [], "session_taint": "block"}


@pytest.mark.parametrize("values", [{}, {"mode": "allowlist", "allow": ["Project Alpha", "123@g.us"], "deny": ["+55 11 9"], "override_token": "mine-0123"},
                                    {"session_taint": "off"}])
def test_a_valid_policy_has_no_problems(values):  # CFG-02
    assert SECTION.validate(values) == []


@pytest.mark.parametrize("values, field", [
    ({"mode": "both"}, "mode"),
    ({"allow": "Project Alpha"}, "allow"),
    ({"deny": ["ok", 3]}, "deny"),
    ({"allow": ["a@b@c"]}, "allow"),
    ({"override_token": 7}, "override_token"),
    ({"session_taint": "maybe"}, "session_taint"),
    ({"override_token": "replace-with-your-own-value"}, "override_token"),
])
def test_an_invalid_policy_is_refused_with_the_field_it_belongs_to(values, field):  # CFG-02
    problems = SECTION.validate(values)

    assert [p.field for p in problems] == [field] and problems[0].message


def test_a_policy_that_is_not_an_object_is_a_problem_of_the_whole_section():  # CFG-02
    assert SECTION.validate([]) == [Problem(None, "the policy file must contain a JSON object")]


def test_what_is_saved_is_what_the_send_code_then_loads(tmp_path):  # CFG-03
    store = ConfigStore()
    values = {"mode": "allowlist", "allow": ["Project Alpha"], "deny": ["Client Beta"], "override_token": "mine-0123456"}

    assert store.save(SECTION, values, ABSENT).ok

    assert send_policy._load() == (values, None)
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(send_policy.POLICY_PATH).st_mode) == 0o600
        assert filemode.open_access_warning(send_policy.POLICY_PATH) is None


def test_the_saved_policy_really_blocks_what_it_says():  # CFG-03
    directory = send_policy.Directory(jids_by_name=lambda n: [], name_of=lambda j: None, is_phone=lambda t: False,
                                      ids_of=lambda j: [j], ready=lambda: None)
    ConfigStore().save(SECTION, {"mode": "denylist", "deny": ["120363111@g.us"]}, ABSENT)

    blocked = send_policy.evaluate(send_policy.resolve_target("120363111@g.us", directory), None, directory)
    other = send_policy.evaluate(send_policy.resolve_target("120363222@g.us", directory), None, directory)

    assert blocked.allowed is False and other.allowed is True


def test_the_override_token_never_leaves_in_the_masked_state():  # CFG-10
    values = {"mode": "denylist", "override_token": "mine-0123456789"}

    masked = mask(SECTION, values)

    assert masked["override_token"] == {"set": True} and "mine-0123456789" not in json.dumps(masked)


def test_turning_the_session_defence_off_carries_a_warning_text():  # CFG-01
    assert SECTION.field("session_taint").risk and "vazar" in SECTION.field("session_taint").risk


def test_problems_from_a_project_message_name_the_field_only_when_the_message_starts_with_one():
    assert from_message('"mode" must be one of allowlist, denylist', ["mode"]) == Problem("mode", '"mode" must be one of allowlist, denylist')
    assert from_message("excerpt_chars must be a whole number", ["excerpt_chars"]).field == "excerpt_chars"
    assert from_message("the policy file must contain a JSON object", ["mode"]).field is None
    assert from_message("unknown key 'x'", ["x"]).field is None
    assert listing(None, ["a"]) == [] and listing("", ["a"]) == [] and listing("a bad", ["a"])[0].field == "a"
