"""Monitor profiles section (CFG-01, CFG-02, CFG-10, CFG-11, UX-05): profiles as records over monitor_profiles.json."""
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

import monitor
from ui.config.form import FormFlow
from ui.config.registry import JsonFile, Registry
from ui.config.store import ConfigStore
from ui.sections import monitor as section_module

FIXTURE = Path(__file__).parent / "fixtures" / "ui" / "monitor_profiles.json"
SECTION = section_module.SECTION


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    target = tmp_path / "cfg" / "monitor_profiles.json"
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(target))
    reg = Registry()
    reg.register(SECTION)
    store = ConfigStore(reg)

    def flow():
        f = FormFlow("monitor", reg, store)
        f.load()
        return f

    return flow, target


def put(target, data):
    os.makedirs(target.parent, exist_ok=True)
    target.write_text(json.dumps(data))
    os.chmod(target, 0o600)


def disk(target):
    return json.loads(target.read_text())


def record(name, **extra):
    return {"name": name, "targets": ["15550100001"], **extra}


def test_the_section_describes_the_file_the_project_reads(ctx):
    _, target = ctx
    assert SECTION.id == "monitor" and SECTION.restart is None and not SECTION.read_only
    assert isinstance(SECTION.storage, JsonFile) and SECTION.storage.path() == str(target)
    assert [f.key for f in SECTION.fields] == ["profiles"] and SECTION.field("profiles").kind == "records"
    assert SECTION.field("profiles").id_key == "name"


def test_the_record_fields_cover_exactly_what_a_profile_can_hold():
    """No field without a real key (CFG-11) and no real key without a field."""
    subs = {f.key: f for f in SECTION.field("profiles").fields}
    full = {}
    for key, f in subs.items():
        full[key] = {"bool": True, "int": 5, "list": ["15550100001"], "multichoice": ["mention"], "secret": "a-token-123",
                     "choice": "auto", "text": "x"}[f.kind]
    full["name"] = "p"
    full["aliases"] = ["helper"]
    full["triggers"] = ["mention", "alias"]
    full["allow_own_alias"] = True
    full["chats"] = full["auto_chats"] = ["123@g.us"]
    encoded = section_module.encode({"profiles": [full]})["profiles"]["p"]
    assert set(encoded) <= monitor._PROFILE_KEYS
    assert set(encoded) == monitor._PROFILE_KEYS                       # nothing the project reads is missing
    assert set(encoded["limits"]) == set(monitor.DEFAULT_LIMITS)
    assert set(encoded["senders"]) == {"allow", "deny"}
    profiles, problem = monitor.parse_profiles({"profiles": {"p": encoded}})
    assert problem is None and "p" in profiles


def test_the_fixture_decodes_to_a_record_and_encodes_back_unchanged():
    original = json.loads(FIXTURE.read_text())
    form = section_module.decode(original)
    assert form["profiles"][0]["name"] == "assistant" and form["profiles"][0]["senders_deny"] == ["15550100003"]
    assert form["profiles"][0]["limit_cooldown_seconds"] == 30
    assert section_module.encode(form) == original


def test_decode_of_a_file_without_profiles_invents_nothing():
    assert section_module.decode({}) == {}
    assert section_module.decode({"profiles": {}}) == {"profiles": []}
    assert section_module.decode({"profiles": "nonsense"}) == {}


def test_the_fixture_loads_and_saves_unchanged(ctx):
    flow, target = ctx
    os.makedirs(target.parent)
    shutil.copy(FIXTURE, target)
    os.chmod(target, 0o600)
    before = disk(target)
    f = flow()
    assert f.view().status == "ready"
    f.apply({"profiles": f.view().values["profiles"]})
    outcome = f.save()
    assert outcome.ok and outcome.unchanged
    assert disk(target) == before


def test_a_profile_can_be_added_edited_and_removed(ctx):
    flow, target = ctx
    f = flow()
    f.apply({"profiles": [record("one", mode="draft", limit_cooldown_seconds=15), record("two", aliases=["bot"], triggers=["alias"])]})
    assert f.save().ok is True
    assert disk(target) == {"profiles": {
        "one": {"targets": ["15550100001"], "mode": "draft", "limits": {"cooldown_seconds": 15}},
        "two": {"targets": ["15550100001"], "aliases": ["bot"], "triggers": ["alias"]}}}
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    f = flow()                                                     # edit
    f.apply({"profiles": [record("one", mode="auto"), record("two", aliases=["bot"], triggers=["alias"])]})
    assert f.save().ok is True
    assert disk(target)["profiles"]["one"] == {"targets": ["15550100001"], "mode": "auto"}
    f = flow()                                                     # remove (the SPA has already asked)
    f.apply({"profiles": [record("two", aliases=["bot"], triggers=["alias"])]})
    assert f.save().ok is True
    assert list(disk(target)["profiles"]) == ["two"]


def test_a_repeated_profile_name_is_refused_and_nothing_is_written(ctx):
    flow, target = ctx
    f = flow()
    f.apply({"profiles": [record("same"), record("same", mode="auto")]})
    outcome = f.save()
    assert outcome.ok is False and "profiles" in outcome.errors and "Name" in outcome.errors["profiles"]
    assert not target.exists()


@pytest.mark.parametrize("name", ["", " lead", "trail "])
def test_a_profile_needs_a_plain_name(ctx, name):
    flow, target = ctx
    f = flow()
    f.apply({"profiles": [record(name)]})
    outcome = f.save()
    assert outcome.ok is False and (outcome.errors or outcome.general)
    assert not target.exists()


def test_secrets_of_unchanged_profiles_survive_reordering_and_edits(ctx):
    flow, target = ctx
    put(target, {"profiles": {"a": {"targets": ["15550100001"], "approval_token": "token-of-a-1"},
                              "b": {"targets": ["15550100002"], "approval_token": "token-of-b-2"}}})
    f = flow()
    view = f.view().values["profiles"]
    assert [r["approval_token"] for r in view] == [{"set": True}, {"set": True}]
    assert "token-of-a-1" not in repr(f.view())
    f.apply({"profiles": [{"name": "b", "targets": ["15550100002"]}, {"name": "a", "targets": ["15550100009"]}]})
    assert f.save().ok is True
    saved = disk(target)["profiles"]
    assert list(saved) == ["b", "a"]
    assert saved["a"] == {"targets": ["15550100009"], "approval_token": "token-of-a-1"}
    assert saved["b"]["approval_token"] == "token-of-b-2"


def test_a_secret_can_be_replaced_or_cleared_per_profile(ctx):
    flow, target = ctx
    put(target, {"profiles": {"a": {"targets": ["15550100001"], "approval_token": "token-of-a-1"},
                              "b": {"targets": ["15550100002"], "approval_token": "token-of-b-2"}}})
    f = flow()
    f.apply({"profiles": [{"name": "a", "targets": ["15550100001"], "approval_token": "a brand new token"},
                          {"name": "b", "targets": ["15550100002"], "approval_token": {"clear": True}}]})
    assert f.save().ok is True
    saved = disk(target)["profiles"]
    assert saved["a"]["approval_token"] == "a brand new token" and "approval_token" not in saved["b"]


def test_values_are_checked_by_the_project_and_the_error_sits_beside_the_field(ctx):
    flow, target = ctx
    f = flow()
    f.apply({"profiles": [record("a", targets=["not a number"])]})
    outcome = f.save()
    assert outcome.ok is False
    assert "profiles.0.targets" in outcome.errors and "phone number" in outcome.errors["profiles.0.targets"]
    assert not target.exists()


@pytest.mark.parametrize("sub,value,message", [
    ("approval_token", "short", "at least"),
    ("approval_token", "replace-with-your-own-value", "placeholder"),
])
def test_token_rules_of_the_project_apply(ctx, sub, value, message):
    flow, target = ctx
    f = flow()
    f.apply({"profiles": [record("a", **{sub: value})]})
    outcome = f.save()
    assert outcome.ok is False and message in outcome.errors[f"profiles.0.{sub}"]


def test_a_profile_without_targets_or_aliases_is_refused_by_the_project(ctx):
    flow, target = ctx
    f = flow()
    f.apply({"profiles": [{"name": "empty"}]})
    outcome = f.save()
    assert outcome.ok is False and "needs targets or aliases" in " ".join(list(outcome.errors.values()) + outcome.general)
    assert not target.exists()


def test_saving_calls_the_projects_validation(ctx, monkeypatch):
    flow, target = ctx
    seen = []
    monkeypatch.setattr(monitor, "parse_profiles", lambda raw: seen.append(raw) or ({}, "profiles.a.mode must be one of nothing"))
    f = flow()
    f.apply({"profiles": [record("a")]})
    outcome = f.save()
    assert seen and seen[-1] == {"profiles": {"a": {"targets": ["15550100001"]}}}
    assert outcome.ok is False and "profiles.0.mode" in outcome.errors
    assert not target.exists()


def test_a_file_the_project_rejects_is_shown_with_its_error_and_not_overwritten(ctx):
    flow, target = ctx
    put(target, {"profiles": {"a": {"mode": "sideways", "targets": ["15550100001"]}}})
    f = flow()
    assert f.view().status == "error" and "mode" in f.view().banner
    assert disk(target)["profiles"]["a"]["mode"] == "sideways"
    f.apply({"profiles": [record("a", mode="draft")]})                 # correcting it is allowed
    assert f.save().ok is True
    assert disk(target)["profiles"]["a"]["mode"] == "draft"


def test_risky_choices_carry_a_warning():
    subs = {f.key: f for f in SECTION.field("profiles").fields}
    assert subs["mode"].risk and "auto" in subs["mode"].risk_when
    assert subs["cross_chat_replies"].risk and True in subs["cross_chat_replies"].risk_when
