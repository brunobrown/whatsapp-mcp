"""Send policy section (CFG-01, CFG-02, CFG-10, CFG-11): the fields of send_policy.json, checked by the project."""
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

import send_policy
import taint
from ui.config.form import FormFlow
from ui.config.registry import JsonFile, Registry
from ui.config.store import ConfigStore
from ui.sections import send_policy as section_module

FIXTURE = Path(__file__).parent / "fixtures" / "ui" / "send_policy.json"
SECTION = section_module.SECTION
TOKEN = "example-override-token-0000"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    target = tmp_path / "policy" / "send_policy.json"
    monkeypatch.setattr(send_policy, "POLICY_PATH", str(target))
    reg = Registry()
    reg.register(SECTION)
    store = ConfigStore(reg)

    def flow():
        f = FormFlow("send_policy", reg, store)
        f.load()
        return f

    return flow, target, store


def put_fixture(target):
    os.makedirs(target.parent, exist_ok=True)
    shutil.copy(FIXTURE, target)
    os.chmod(target, 0o600)


def test_the_section_describes_the_file_the_project_reads(ctx):
    _, target, _ = ctx
    assert SECTION.id == "send_policy" and SECTION.read_only is False and SECTION.restart is None
    assert isinstance(SECTION.storage, JsonFile) and SECTION.storage.path() == str(target)
    assert SECTION.title and SECTION.description


def test_every_field_is_a_key_the_project_reads():
    source = Path(send_policy.__file__).read_text() + Path(taint.__file__).read_text()
    assert [f.key for f in SECTION.fields], "the section has fields"
    for f in SECTION.fields:
        assert f'"{f.key}"' in source, f"{f.key} is not read by send_policy.py or taint.py"


def test_the_fields_cover_the_documented_keys():
    assert {f.key for f in SECTION.fields} == {"mode", "allow", "deny", "override_token", "session_taint"}


def test_the_override_token_is_a_secret_and_lists_are_lists():
    kinds = {f.key: f.kind for f in SECTION.fields}
    assert kinds["override_token"] == "secret"
    assert kinds["allow"] == kinds["deny"] == "list"
    assert set(SECTION.field("mode").options) == {"allowlist", "denylist"}
    assert SECTION.field("mode").default == "denylist"


def test_the_fixture_loads_with_the_token_masked(ctx):
    flow, target, _ = ctx
    put_fixture(target)
    view = flow().view()
    assert view.status == "ready"
    assert view.values["mode"] == "allowlist"
    assert view.values["allow"] == ["Example Group", "15550100001"]
    assert view.values["deny"] == ["15550100002"]
    assert view.values["override_token"] == {"set": True}
    assert TOKEN not in repr(view)


def test_saving_a_change_keeps_the_shape_of_the_fixture(ctx):
    flow, target, _ = ctx
    put_fixture(target)
    before = json.loads(target.read_text())
    f = flow()
    f.apply({"allow": "Example Group\n15550100001\nAnother Group"})
    outcome = f.save()
    assert outcome.ok is True
    after = json.loads(target.read_text())
    assert set(after) == set(before)
    assert after["allow"] == ["Example Group", "15550100001", "Another Group"]
    assert {k: v for k, v in after.items() if k != "allow"} == {k: v for k, v in before.items() if k != "allow"}
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_saving_without_changes_writes_nothing(ctx):
    flow, target, _ = ctx
    put_fixture(target)
    before = target.read_bytes()
    assert flow().save().unchanged is True
    assert target.read_bytes() == before


def test_a_new_policy_can_be_created_with_a_token(ctx):
    flow, target, _ = ctx
    f = flow()
    f.apply({"mode": "allowlist", "allow": "Team", "override_token": "a brand new token"})
    assert f.save().ok is True
    assert json.loads(target.read_text()) == {"mode": "allowlist", "allow": ["Team"], "override_token": "a brand new token"}
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_saving_calls_the_projects_validation(ctx, monkeypatch):
    flow, target, _ = ctx
    seen = []

    def refuse(policy):
        seen.append(policy)
        return None, '"allow" is refused by the project'

    monkeypatch.setattr(send_policy, "parse_policy", refuse)
    f = flow()
    f.apply({"allow": "Team"})
    outcome = f.save()
    assert seen and seen[-1]["allow"] == ["Team"]
    assert outcome.ok is False and "allow" in outcome.errors and "refused by the project" in outcome.errors["allow"]
    assert not target.exists()


def test_an_invalid_jid_is_refused_by_the_project_rule_beside_its_field(ctx):
    flow, target, _ = ctx
    f = flow()
    f.apply({"deny": "12345@nowhere.example"})
    outcome = f.save()
    assert outcome.ok is False and "deny" in outcome.errors
    assert "not a valid JID" in outcome.errors["deny"]
    assert not target.exists()


def test_a_session_taint_value_outside_the_known_ones_is_refused(ctx):
    flow, target, _ = ctx
    f = flow()
    f.apply({"session_taint": "sometimes"})
    outcome = f.save()
    assert outcome.ok is False and "session_taint" in outcome.errors


def test_the_taint_mode_chosen_here_is_the_one_the_project_applies(ctx, monkeypatch):
    flow, target, _ = ctx
    monkeypatch.delenv(taint.ENV, raising=False)
    f = flow()
    f.apply({"session_taint": "off"})
    assert f.save().ok is True
    assert taint.mode(taint._policy_value(str(target))) == "off"


def test_a_broken_file_is_reported_and_kept(ctx):
    flow, target, _ = ctx
    os.makedirs(target.parent)
    target.write_text("{not json")
    view = flow().view()
    assert view.status == "error" and view.banner
    f = flow()
    f.apply({"allow": "Team"})
    outcome = f.save()
    assert outcome.ok is False and outcome.needs_confirmation is True
    assert target.read_text() == "{not json"


def test_the_token_stays_when_it_is_not_touched_and_clears_on_request(ctx):
    flow, target, _ = ctx
    put_fixture(target)
    f = flow()
    f.apply({"mode": "denylist"})
    assert f.save().ok is True
    assert json.loads(target.read_text())["override_token"] == TOKEN
    f = flow()
    f.apply({"override_token": {"clear": True}})
    assert f.save().ok is True
    assert "override_token" not in json.loads(target.read_text())
