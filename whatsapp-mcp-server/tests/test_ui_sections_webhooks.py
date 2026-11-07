"""Webhooks section (CFG-01, CFG-02, CFG-10, CFG-11): endpoints of webhooks.json as records, checked by the bridge."""
import json
import os
import re
import shutil
import stat
from pathlib import Path

import pytest

import localconfig
from ui.config.form import FormFlow
from ui.config.registry import JsonFile, Registry
from ui.config.store import ConfigStore
from ui.sections import webhooks as section_module
from ui.security.bridge import WebhookCheck

FIXTURE = Path(__file__).parent / "fixtures" / "ui" / "webhooks.json"
BRIDGE_SOURCE = Path(__file__).resolve().parents[2] / "whatsapp-bridge" / "webhook.go"
SECTION = section_module.SECTION
SECRET = "0123456789abcdef0123456789abcdef0123"


class FakeBridge:
    def __init__(self):
        self.reply = WebhookCheck(True, True)
        self.seen = []

    def validate_webhooks(self, text):
        self.seen.append(text)
        return self.reply


@pytest.fixture
def ctx(monkeypatch):
    bridge = FakeBridge()
    monkeypatch.setattr(section_module, "CLIENT", bridge)
    target = Path(localconfig.config_dir()) / "webhooks.json"
    reg = Registry()
    reg.register(SECTION)
    store = ConfigStore(reg)

    def flow():
        f = FormFlow("webhooks", reg, store)
        f.load()
        return f

    return flow, target, bridge


def put_fixture(target):
    os.makedirs(target.parent, exist_ok=True)
    shutil.copy(FIXTURE, target)
    os.chmod(target, 0o600)


def endpoint(name="hook", **extra):
    return {"name": name, "url": "https://example.com/h", "secret": SECRET, "events": ["message"], **extra}


def test_the_section_describes_the_file_the_bridge_reads(ctx):
    _, target, _ = ctx
    assert SECTION.id == "webhooks" and SECTION.restart == "bridge" and not SECTION.read_only
    assert isinstance(SECTION.storage, JsonFile) and SECTION.storage.path() == str(target)
    assert {f.key for f in SECTION.fields} == {"enabled", "endpoints"}
    assert SECTION.field("endpoints").kind == "records" and SECTION.field("endpoints").id_key == "name"


def test_the_endpoint_fields_are_the_keys_the_bridge_reads():
    source = BRIDGE_SOURCE.read_text()
    struct = re.search(r"type webhookEndpointConfig struct \{(.*?)\n\}", source, re.S).group(1)
    keys = set(re.findall(r'json:"([a-z_]+)"', struct))
    assert {f.key for f in SECTION.field("endpoints").fields} == keys
    top = re.search(r"type webhookFile struct \{(.*?)\n\}", source, re.S).group(1)
    assert {f.key for f in SECTION.fields} == set(re.findall(r'json:"([a-z_]+)"', top))


def test_the_event_choices_are_the_events_the_bridge_knows():
    source = BRIDGE_SOURCE.read_text()
    block = re.search(r"// The event types an endpoint can ask for\.\nconst \((.*?)\n\)", source, re.S).group(1)
    events = set(re.findall(r'= "([a-z_]+)"', block))
    options = set(SECTION.field("endpoints").sub_field("events").options)
    assert options == events | {"*"}


def test_the_secret_is_a_secret_and_never_shown(ctx):
    flow, target, _ = ctx
    assert SECTION.field("endpoints").sub_field("secret").kind == "secret"
    put_fixture(target)
    view = flow().view()
    assert view.values["endpoints"][0]["secret"] == {"set": True}
    assert "0123456789abcdef" not in repr(view)


def test_the_fixture_loads_and_saves_unchanged(ctx):
    flow, target, bridge = ctx
    put_fixture(target)
    before = json.loads(target.read_text())
    f = flow()
    assert f.view().status == "ready"
    assert bridge.seen and json.loads(bridge.seen[-1]) == before         # the bridge is asked about the file as it is
    f.apply({"endpoints": f.view().values["endpoints"]})
    outcome = f.save()
    assert outcome.ok and outcome.unchanged
    assert json.loads(target.read_text()) == before


def test_a_change_keeps_the_secret_and_the_shape_of_the_fixture(ctx):
    flow, target, bridge = ctx
    put_fixture(target)
    before = json.loads(target.read_text())
    f = flow()
    changed = dict(f.view().values["endpoints"][0], include_content=True)
    f.apply({"endpoints": [changed]})
    assert f.save().ok is True
    after = json.loads(target.read_text())
    assert after["endpoints"][0]["secret"] == before["endpoints"][0]["secret"]
    assert after["endpoints"][0]["include_content"] is True
    assert set(after) == set(before) and set(after["endpoints"][0]) == set(before["endpoints"][0])
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert before["endpoints"][0]["secret"] in bridge.seen[-1]          # the bridge got the real secret to judge it


def test_saving_asks_the_bridge_and_a_refusal_is_shown_beside_the_endpoint(ctx):
    flow, target, bridge = ctx
    bridge.reply = WebhookCheck(True, False, error='endpoint "hook": url must be http (loopback only) or https')
    f = flow()
    f.apply({"endpoints": [endpoint("hook", url="ftp://x.example/h")]})
    outcome = f.save()
    assert outcome.ok is False
    assert outcome.errors == {"endpoints.0.url": 'endpoint "hook": url must be http (loopback only) or https'}
    assert not target.exists()
    assert json.loads(bridge.seen[-1])["endpoints"][0]["url"] == "ftp://x.example/h"


def test_an_error_about_the_whole_file_is_general(ctx):
    flow, target, bridge = ctx
    bridge.reply = WebhookCheck(True, False, error="webhooks.json has no endpoints")
    f = flow()
    f.apply({"endpoints": []})
    outcome = f.save()
    assert outcome.ok is False and outcome.general == ["webhooks.json has no endpoints"]
    assert not target.exists()


def test_a_down_bridge_says_the_check_could_not_run_and_nothing_is_saved(ctx):
    flow, target, bridge = ctx
    bridge.reply = WebhookCheck(False, reason="unreachable", message="The check could not run. The bridge is not responding.")
    f = flow()
    f.apply({"endpoints": [endpoint()]})
    outcome = f.save()
    assert outcome.ok is False
    assert outcome.general == ["The check could not run. The bridge is not responding."]
    assert not target.exists()


def test_a_repeated_endpoint_name_is_refused_before_the_bridge_is_asked(ctx):
    flow, target, bridge = ctx
    f = flow()
    f.apply({"endpoints": [endpoint("same"), endpoint("same")]})
    outcome = f.save()
    assert outcome.ok is False and "endpoints" in outcome.errors
    assert not target.exists()


def test_an_endpoint_can_be_added_and_removed(ctx):
    flow, target, _ = ctx
    f = flow()
    f.apply({"endpoints": [endpoint("a"), endpoint("b", chats=["15550100001"], persist_queue=True)]})
    assert f.save().ok is True
    saved = json.loads(target.read_text())
    assert [e["name"] for e in saved["endpoints"]] == ["a", "b"] and saved["endpoints"][1]["persist_queue"] is True
    f = flow()
    f.apply({"endpoints": [{"name": "b", "url": "https://example.com/h", "events": ["message"], "chats": ["15550100001"],
                            "persist_queue": True}]})
    assert f.save().ok is True
    saved = json.loads(target.read_text())
    assert [e["name"] for e in saved["endpoints"]] == ["b"] and saved["endpoints"][0]["secret"] == SECRET


def test_the_secret_of_a_kept_endpoint_survives_removing_another(ctx):
    flow, target, _ = ctx
    f = flow()
    f.apply({"endpoints": [endpoint("a"), endpoint("b", secret="b" * 40)]})
    assert f.save().ok is True
    f = flow()
    f.apply({"endpoints": [{"name": "b", "url": "https://example.com/h", "events": ["message"]}]})
    assert f.save().ok is True
    assert json.loads(target.read_text())["endpoints"][0]["secret"] == "b" * 40


def test_the_file_can_be_switched_off_as_a_whole(ctx):
    flow, target, _ = ctx
    f = flow()
    f.apply({"enabled": False, "endpoints": [endpoint()]})
    assert f.save().ok is True
    assert json.loads(target.read_text())["enabled"] is False


def test_a_broken_file_is_reported_and_kept(ctx):
    flow, target, _ = ctx
    os.makedirs(target.parent, exist_ok=True)
    target.write_text("{oops")
    assert flow().view().status == "error"
    f = flow()
    f.apply({"endpoints": [endpoint()]})
    assert f.save().needs_confirmation is True
    assert target.read_text() == "{oops"
