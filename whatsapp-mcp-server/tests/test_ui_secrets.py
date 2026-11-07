"""Secrets never reach the state of a screen: they are masked on the way out and merged on the way back (CFG-10)."""
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.core.registry import Field, JsonFile, Section  # noqa: E402
from ui.core.secrets import CLEAR, mask, merge  # noqa: E402
from ui.core.store import ABSENT, Loaded  # noqa: E402

TOKEN = "override-0123456789-secret"
HOOK = "hook-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def section():
    endpoints = Field("endpoints", "Endpoints", "records", id_key="name", fields=(
        Field("name", "Name", "text"), Field("url", "URL", "text"),
        Field("secret", "Secret", "secret"), Field("events", "Events", "list")))
    return Section(id="demo", title="Demo", icon="x", order=1, storage=JsonFile(lambda: "/x"),
                   fields=(Field("mode", "Mode", "text"), Field("override_token", "Override", "secret"), endpoints),
                   validate=lambda v: [])


def stored():
    return {"mode": "allowlist", "override_token": TOKEN, "custom": {"keep": "me"},
            "endpoints": [{"name": "n8n", "url": "https://a.example", "secret": HOOK, "events": ["message"]},
                          {"name": "crm", "url": "https://b.example", "secret": "crm-secret-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]}


def test_mask_replaces_every_secret_with_whether_it_is_set():
    masked = mask(section(), stored())

    assert masked["override_token"] == {"set": True}
    assert [e["secret"] for e in masked["endpoints"]] == [{"set": True}, {"set": True}]


def test_mask_says_not_set_for_an_empty_or_missing_secret():
    masked = mask(section(), {"override_token": "", "endpoints": [{"name": "x"}]})

    assert masked["override_token"] == {"set": False} and masked["endpoints"][0]["secret"] == {"set": False}
    assert mask(section(), {})["override_token"] == {"set": False}


def test_mask_keeps_what_is_not_secret_and_only_what_the_section_declares():
    masked = mask(section(), stored())

    assert masked["mode"] == "allowlist" and masked["endpoints"][0]["url"] == "https://a.example"
    assert masked["endpoints"][0]["events"] == ["message"] and "custom" not in masked


def test_no_secret_value_in_masked_state_or_errors():  # CFG-10
    masked = mask(section(), stored())
    text = json.dumps(masked) + repr(masked) + str(masked)

    for secret in (TOKEN, HOOK, "crm-secret-bbbb"):
        assert secret not in text


def test_the_loaded_object_does_not_print_its_values():  # CFG-10
    loaded = Loaded(stored(), ABSENT)

    assert TOKEN not in repr(loaded) and HOOK not in repr(loaded) and TOKEN not in str(loaded)


def test_mask_does_not_change_its_input():
    original = stored()
    snapshot = copy.deepcopy(original)

    mask(section(), original)

    assert original == snapshot


@pytest.mark.parametrize("incoming", ["", None, {"set": True}, {"set": False}])
def test_merge_keeps_the_old_secret_when_the_screen_sends_nothing_new(incoming):
    merged = merge(section(), stored(), {"override_token": incoming})

    assert merged["override_token"] == TOKEN


def test_merge_replaces_a_secret_with_a_new_text():
    assert merge(section(), stored(), {"override_token": "a-new-value-0123456789"})["override_token"] == "a-new-value-0123456789"


def test_merge_removes_a_secret_only_when_asked_to():
    merged = merge(section(), stored(), {"override_token": CLEAR})

    assert "override_token" not in merged


@pytest.mark.parametrize("bad", [123, ["x"], {"value": "x"}, True])
def test_merge_refuses_a_secret_that_is_not_text(bad):
    with pytest.raises(ValueError, match="override_token"):
        merge(section(), stored(), {"override_token": bad})


def test_merge_keeps_what_the_section_does_not_declare():
    assert merge(section(), stored(), {"mode": "denylist"})["custom"] == {"keep": "me"}


def test_merge_sets_a_value_and_removes_a_key_sent_as_none():
    merged = merge(section(), stored(), {"mode": None})

    assert "mode" not in merged
    assert merge(section(), stored(), {"mode": "denylist"})["mode"] == "denylist"


def test_merge_matches_records_by_their_id_key_so_reordering_keeps_each_secret():
    incoming = {"endpoints": [
        {"name": "crm", "url": "https://b.example", "secret": ""},
        {"name": "n8n", "url": "https://a2.example", "secret": {"set": True}, "events": ["message"]}]}

    merged = merge(section(), stored(), incoming)

    assert [(e["name"], e["secret"]) for e in merged["endpoints"]] == [
        ("crm", "crm-secret-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"), ("n8n", HOOK)]
    assert merged["endpoints"][1]["url"] == "https://a2.example"


def test_merge_gives_a_new_record_only_the_secret_it_was_sent():
    merged = merge(section(), stored(), {"endpoints": [{"name": "new", "url": "https://c.example", "secret": "s" * 40},
                                                         {"name": "bare", "url": "https://d.example", "secret": ""}]})

    assert merged["endpoints"][0]["secret"] == "s" * 40 and "secret" not in merged["endpoints"][1]


def test_a_removed_record_takes_its_secret_with_it():
    merged = merge(section(), stored(), {"endpoints": [{"name": "crm", "url": "https://b.example", "secret": ""}]})

    assert [e["name"] for e in merged["endpoints"]] == ["crm"] and HOOK not in json.dumps(merged)


def test_a_renamed_record_does_not_inherit_the_secret_of_another_name():
    merged = merge(section(), stored(), {"endpoints": [{"name": "renamed", "url": "https://a.example", "secret": ""}]})

    assert "secret" not in merged["endpoints"][0]


def test_merge_does_not_change_its_inputs():
    old, new = stored(), {"override_token": "", "endpoints": [{"name": "crm", "url": "u", "secret": ""}]}
    snapshots = copy.deepcopy(old), copy.deepcopy(new)

    merge(section(), old, new)

    assert (old, new) == snapshots
