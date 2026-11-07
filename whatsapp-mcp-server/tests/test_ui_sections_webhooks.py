"""The webhooks section: the fields of webhooks.json, validated by the bridge itself (CFG-01, CFG-02, CFG-10, CFG-11)."""
import json
import os
import re
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.core.bridge import Unavailable, WebhookCheck  # noqa: E402
from ui.core.draft import Draft  # noqa: E402
from ui.core.registry import default  # noqa: E402
from ui.core.secrets import mask, merge  # noqa: E402
from ui.core.store import ABSENT, ConfigStore  # noqa: E402
from ui.sections import webhooks  # noqa: E402
from ui.sections.webhooks import BRIDGE_PROBLEM, SECTION  # noqa: E402

BRIDGE_SOURCE = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "whatsapp-bridge", "webhook.go")).read()
SECRET = "s3cret-" * 6


class FakeBridge:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def validate_webhooks(self, data):
        self.asked.append(data)
        return self.answer


@pytest.fixture
def bridge(monkeypatch):
    fake = FakeBridge(WebhookCheck(True, None))
    monkeypatch.setattr(webhooks, "client", fake)
    return fake


def endpoint(**extra):
    return {"name": "agent", "url": "https://hooks.example.com/in", "secret": SECRET, "events": ["message"], **extra}


def test_the_section_is_registered_and_restarts_the_bridge():  # CFG-08
    assert SECTION in default.sections() and SECTION.restart == "bridge"


def test_the_file_is_the_one_the_bridge_reads_in_the_configuration_directory(tmp_path):  # CFG-12
    assert re.search(r'webhookConfigFile\s*=\s*"webhooks\.json"', BRIDGE_SOURCE)
    assert SECTION.storage.path() == str(tmp_path / "config" / "webhooks.json")


def test_the_fields_of_an_endpoint_are_exactly_the_ones_the_bridge_knows():  # CFG-11
    struct = re.search(r"type webhookEndpointConfig struct \{(.*?)\n\}", BRIDGE_SOURCE, re.S).group(1)
    in_go = set(re.findall(r'json:"(\w+)"', struct))

    assert {f.key for f in SECTION.field("endpoints").fields} == in_go


def test_the_top_level_keys_are_the_ones_the_bridge_reads():  # CFG-11
    struct = re.search(r"type webhookFile struct \{(.*?)\n\}", BRIDGE_SOURCE, re.S).group(1)

    assert {f.key for f in SECTION.fields} == set(re.findall(r'json:"(\w+)"', struct))


def test_the_event_choices_are_exactly_the_events_the_bridge_emits():  # CFG-11
    constants = dict(re.findall(r'(event\w+)\s*=\s*"(\w+)"', BRIDGE_SOURCE))
    accepted = re.findall(r"(event\w+):\s*true", re.search(r"var webhookEventTypes = map\[string\]bool\{(.*?)\n\}", BRIDGE_SOURCE, re.S).group(1))
    in_go = {constants[c] for c in accepted}

    assert len(in_go) == 9
    assert {n for n in SECTION.field("endpoints").sub("events").options if n != "*"} == in_go


def test_a_file_the_bridge_accepts_has_no_problems_and_is_the_text_that_was_sent(bridge):  # CFG-02
    values = {"endpoints": [endpoint()]}

    assert SECTION.validate(values) == [] and bridge.asked == [values]


@pytest.mark.parametrize("error, field", [
    ('endpoint "agent": url must be http (loopback only) or https', "endpoints.agent.url"),
    ('endpoint "agent": secret must have at least 32 characters', "endpoints.agent.secret"),
    ('endpoint "agent": unknown event "x"', "endpoints.agent.events"),
    ('endpoint 2: name is required', "endpoints.1.name"),
    ("webhooks.json has no endpoints", "endpoints"),
    ("webhooks.json is not valid: wrong shape", "endpoints"),
])
def test_a_refusal_of_the_bridge_is_tied_to_the_endpoint_and_field(bridge, error, field):  # CFG-02
    bridge.answer = WebhookCheck(False, error)

    problems = SECTION.validate({"endpoints": []})

    assert [p.field for p in problems] == [field] and problems[0].message == error


@pytest.mark.parametrize("reason", ["unreachable", "unauthorized", "unsupported", "unexpected"])
def test_without_the_bridge_the_file_cannot_be_checked_and_the_reason_is_said(bridge, reason):  # CFG-02
    bridge.answer = Unavailable(reason)

    problems = SECTION.validate({"endpoints": [endpoint()]})

    assert [p.field for p in problems] == [BRIDGE_PROBLEM] and problems[0].message


def test_an_unreachable_bridge_blocks_saving_and_writes_nothing(bridge, tmp_path):  # CFG-02
    bridge.answer = Unavailable("unreachable")

    result = ConfigStore().save(SECTION, {"endpoints": [endpoint()]}, ABSENT)

    assert result.ok is False and result.errors[0].field == BRIDGE_PROBLEM
    assert not (tmp_path / "config" / "webhooks.json").exists()


def test_a_saved_file_is_private_as_the_bridge_demands(bridge, tmp_path):  # CFG-03
    assert ConfigStore().save(SECTION, {"endpoints": [endpoint(include_content=True)]}, ABSENT).ok

    path = tmp_path / "config" / "webhooks.json"
    assert json.loads(path.read_text())["endpoints"][0]["include_content"] is True
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_the_secret_is_masked_and_survives_editing_another_field(bridge):  # CFG-10
    stored = {"endpoints": [endpoint(), endpoint(name="crm", secret="crm-" + SECRET)]}
    masked = mask(SECTION, stored)
    text = json.dumps(masked)
    assert SECRET not in text and masked["endpoints"][0]["secret"] == {"set": True}

    edited = {"endpoints": [dict(masked["endpoints"][1]), dict(masked["endpoints"][0], persist_queue=True)]}
    merged = merge(SECTION, stored, edited)

    assert [(e["name"], e["secret"]) for e in merged["endpoints"]] == [("crm", "crm-" + SECRET), ("agent", SECRET)]
    assert merged["endpoints"][1]["persist_queue"] is True


def test_removing_an_endpoint_needs_confirmation(bridge):  # UX-05
    draft = Draft(SECTION, {"endpoints": [endpoint(), endpoint(name="crm")]})

    assert draft.remove_item("endpoints", 0) is False and len(draft.values["endpoints"]) == 2
    assert draft.remove_item("endpoints", 0, confirmed=True) and [e["name"] for e in draft.values["endpoints"]] == ["crm"]


def test_options_that_widen_what_leaves_the_bridge_carry_a_warning():  # CFG-01
    endpoint_field = SECTION.field("endpoints").sub
    assert endpoint_field("include_content").risk and endpoint_field("allow_private_network").risk


def test_the_field_checks_accept_a_normal_endpoint():  # CFG-02
    assert SECTION.field("endpoints").check([endpoint(chats=["120363000000000000@g.us"], include_own=False)]) is None
