"""The monitor section: profiles as flat records in the form, nested in monitor_profiles.json (CFG-01, CFG-02, CFG-10, CFG-11)."""
import copy
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import monitor  # noqa: E402
from ui.core.draft import Draft  # noqa: E402
from ui.core.registry import Problem, default  # noqa: E402
from ui.core.secrets import mask, merge  # noqa: E402
from ui.core.store import ABSENT, ConfigStore  # noqa: E402
from ui.sections.monitor import SECTION, decode, encode  # noqa: E402

SERVER = os.path.dirname(os.path.dirname(__file__))
FIELDS = {f.key: f for f in SECTION.field("profiles").fields}


def example():
    """monitor.example.json with its placeholder tokens replaced (the project refuses the placeholder on purpose)."""
    data = json.load(open(os.path.join(SERVER, "monitor.example.json")))
    for number, profile in enumerate(data["profiles"].values()):
        if "approval_token" in profile:
            profile["approval_token"] = f"approval-token-number-{number}"
    return data


def test_the_section_is_registered_and_reads_the_projects_file(tmp_path, monkeypatch):  # CFG-12
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "somewhere.json"))

    assert SECTION in default.sections() and SECTION.storage.path() == str(tmp_path / "somewhere.json")


def test_the_example_file_goes_through_the_form_and_back_to_the_same_profiles():  # CFG-01
    original = example()
    expected, problem = monitor.parse_profiles(original)
    assert problem is None

    again, problem = monitor.parse_profiles(encode(decode(original), original))

    assert problem is None and again == expected


def test_decode_flattens_senders_and_limits_into_the_record():
    record = decode({"profiles": {"p": {"targets": ["me"], "senders": {"deny": ["5511900001111"]},
                                        "limits": {"cooldown_seconds": 5}, "mode": "auto"}}})["profiles"][0]

    assert (record["name"], record["targets"], record["mode"]) == ("p", ["me"], "auto")
    assert (record["senders_allow"], record["senders_deny"]) == ([], ["5511900001111"])
    assert record["limit_cooldown_seconds"] == 5 and record["limit_max_reply_chars"] is None
    assert record["enabled"] is None and record["triggers"] is None


def test_encode_rebuilds_the_nesting_and_leaves_out_what_is_unset_or_empty():
    form = {"profiles": [{"name": "p", "targets": ["me"], "aliases": [], "enabled": False, "mode": None, "triggers": [],
                          "senders_allow": [], "senders_deny": ["551"], "limit_cooldown_seconds": 5, "limit_max_reply_chars": None,
                          "instructions": "", "max_event_age_minutes": None}]}

    assert encode(form, {}) == {"profiles": {"p": {"targets": ["me"], "enabled": False, "senders": {"deny": ["551"]},
                                                   "limits": {"cooldown_seconds": 5}, "instructions": ""}}}


def test_encode_keeps_the_order_of_the_profiles():
    form = {"profiles": [{"name": n, "targets": ["me"]} for n in ("b", "a", "c")]}

    assert list(encode(form, {})["profiles"]) == ["b", "a", "c"]


def test_a_file_that_is_not_shaped_like_profiles_decodes_to_no_profiles():
    for value in ({}, {"profiles": []}, {"profiles": {"p": "text"}}, []):
        assert isinstance(decode(value)["profiles"], list)


def test_every_option_of_the_project_is_a_field_and_every_field_is_an_option():  # CFG-11
    covered = set()
    for key in FIELDS:
        if key == "name":
            continue
        if key in ("senders_allow", "senders_deny"):
            covered.add("senders")
        elif key.startswith("limit_"):
            assert key[len("limit_"):] in monitor.DEFAULT_LIMITS
            covered.add("limits")
        else:
            covered.add(key)

    assert covered == set(monitor._PROFILE_KEYS)


@pytest.mark.parametrize("profile, field", [
    ({"targets": ["me"], "mode": "both"}, "profiles.p.mode"),
    ({"targets": ["12"]}, "profiles.p.targets"),
    ({"targets": ["me"], "senders": {"allow": ["abc"]}}, "profiles.p.senders_allow"),
    ({"targets": ["me"], "limits": {"cooldown_seconds": 0}}, "profiles.p.limit_cooldown_seconds"),
    ({"targets": ["me"], "max_event_age_minutes": 0}, "profiles.p.max_event_age_minutes"),
    ({"targets": ["me"], "approval_token": "x"}, "profiles.p.approval_token"),
    ({}, "profiles.p"),
])
def test_a_refused_profile_is_tied_to_its_profile_and_field(profile, field):  # CFG-02
    problems = SECTION.validate({"profiles": {"p": profile}})

    assert [p.field for p in problems] == [field] and problems[0].message


def test_a_valid_file_has_no_problems():  # CFG-02
    assert SECTION.validate(example()) == [] and SECTION.validate({}) == []


def test_a_file_of_the_wrong_shape_is_a_problem_of_the_whole_section():  # CFG-02
    assert [p.field for p in SECTION.validate([])] == ["profiles"]


@pytest.mark.parametrize("names, bad", [(["a", "a"], {1}), (["", "b"], {0}), (["a.b"], {0}), (["a\nb"], {0}), (["x" * 65], {0}), (["ok", "also ok"], set())])
def test_profile_names_must_be_unique_plain_and_short(names, bad):  # CFG-02
    problems = SECTION.check_form({"profiles": [{"name": n} for n in names]})

    assert {int(p.field.split(".")[1]) for p in problems} == bad and all(p.field.endswith(".name") for p in problems)


def test_a_profile_removed_in_the_form_is_gone_from_the_file():  # UX-05
    original = example()
    draft = Draft(SECTION, decode(original))
    first = draft.values["profiles"][0]["name"]

    assert draft.remove_item("profiles", 0, confirmed=True)
    written = encode(draft.values, original)

    assert first not in written["profiles"] and len(written["profiles"]) == len(original["profiles"]) - 1


def test_the_approval_token_is_masked_and_survives_a_round_trip_through_the_form():  # CFG-10
    original = {"profiles": {"p": {"targets": ["me"], "approval_token": "approval-0123456789"},
                             "q": {"targets": ["me"], "approval_token": "approval-other-9876"}}}
    form = decode(original)

    masked = mask(SECTION, form)
    assert [r["approval_token"] for r in masked["profiles"]] == [{"set": True}, {"set": True}]
    assert "approval-0123456789" not in json.dumps(masked)

    reordered = {"profiles": [dict(masked["profiles"][1]), dict(masked["profiles"][0], mode="auto")]}
    written = encode(merge(SECTION, form, reordered), original)
    assert written["profiles"]["p"]["approval_token"] == "approval-0123456789"
    assert written["profiles"]["q"]["approval_token"] == "approval-other-9876"
    assert written["profiles"]["p"]["mode"] == "auto" and list(written["profiles"]) == ["q", "p"]


def test_saving_through_the_store_makes_the_project_load_the_same_profiles(tmp_path, monkeypatch):  # CFG-03
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "monitor_profiles.json"))
    store = ConfigStore()
    file_values = encode({"profiles": [{"name": "support", "targets": ["me"], "mode": "draft", "topics": ["deploy"],
                                        "senders_deny": ["5511900001111"], "limit_cooldown_seconds": 30}]}, {})

    assert store.save(SECTION, file_values, ABSENT).ok

    profile, error = monitor.get_profile("support")
    assert error is None and profile.limits["cooldown_seconds"] == 30 and profile.senders_deny == ["5511900001111"]
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(monitor.CONFIG_PATH).st_mode) == 0o600


def test_decode_does_not_change_its_input():
    original = example()
    snapshot = copy.deepcopy(original)

    decode(original)

    assert original == snapshot


def test_risky_choices_carry_their_warning():  # CFG-01
    for key in ("mode", "cross_chat_replies"):
        assert FIELDS[key].risk


def test_the_field_checks_accept_what_a_loaded_example_holds():  # CFG-02
    form = decode(example())

    assert SECTION.field("profiles").check(form["profiles"]) is None
