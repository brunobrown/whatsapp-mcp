"""Secret masking and merging (CFG-10): secrets leave as {set: bool}; incoming text replaces, absence or {set}
keeps, {clear: true} removes; records match their secrets by id key."""
import json

from ui.config.fields import Field
from ui.config.secrets import mask, merge

TOKEN = Field("override_token", "Token", "secret")
MODE = Field("mode", "Mode", "text")
ENDPOINTS = Field("endpoints", "Endpoints", "records", id_key="name", fields=(
    Field("name", "Name", "text"),
    Field("url", "URL", "text"),
    Field("secret", "Secret", "secret"),
))
FIELDS = (MODE, TOKEN, ENDPOINTS)


def stored():
    return {"mode": "allow", "override_token": "TOPSECRET-1", "stray": "TOPSECRET-STRAY",
            "endpoints": [{"name": "a", "url": "https://a", "secret": "SECRET-A"},
                          {"name": "b", "url": "https://b", "secret": "SECRET-B"},
                          {"name": "c", "url": "https://c"}]}


def test_no_stored_secret_text_leaves_in_any_output():
    out = json.dumps(mask(FIELDS, stored()))
    for leaked in ("TOPSECRET-1", "TOPSECRET-STRAY", "SECRET-A", "SECRET-B"):
        assert leaked not in out


def test_a_secret_is_shown_as_set_or_not_set_and_other_values_stay():
    masked = mask(FIELDS, stored())
    assert masked["mode"] == "allow"
    assert masked["override_token"] == {"set": True}
    assert [e["secret"] for e in masked["endpoints"]] == [{"set": True}, {"set": True}, {"set": False}]
    assert [e["url"] for e in masked["endpoints"]] == ["https://a", "https://b", "https://c"]
    assert mask(FIELDS, {})["override_token"] == {"set": False}
    assert mask(FIELDS, {"override_token": ""})["override_token"] == {"set": False}


def test_a_value_that_is_not_a_declared_field_does_not_leave():
    assert "stray" not in mask(FIELDS, stored())


def test_masking_does_not_change_what_it_was_given():
    original = stored()
    mask(FIELDS, original)
    assert original == stored()


def test_incoming_text_replaces_the_secret():
    result = merge(FIELDS, stored(), {"override_token": "new-value"})
    assert result.problems == []
    assert result.values["override_token"] == "new-value"
    assert result.values["mode"] == "allow"


def test_absence_or_set_marker_keeps_the_secret():
    for incoming in ({}, {"override_token": {"set": True}}, {"override_token": {"set": False}}):
        result = merge(FIELDS, stored(), incoming)
        assert result.problems == []
        assert result.values["override_token"] == "TOPSECRET-1"


def test_clear_removes_the_secret():
    result = merge(FIELDS, stored(), {"override_token": {"clear": True}})
    assert result.problems == []
    assert "override_token" not in result.values


def test_a_secret_that_is_not_text_is_refused_and_the_stored_one_is_kept():
    for bad in (5, ["x"], None, True, "", {"clear": False}, {"clear": "yes"}, {"other": 1}, {"set": "yes"}):
        result = merge(FIELDS, stored(), {"override_token": bad})
        assert [p.key for p in result.problems] == ["override_token"], bad
        assert result.values["override_token"] == "TOPSECRET-1"


def test_a_refusal_never_repeats_the_secret():
    result = merge(FIELDS, stored(), {"override_token": ["leak-me"]})
    assert "leak-me" not in result.problems[0].message


def test_records_keep_each_secret_by_id_even_when_reordered():
    incoming = {"endpoints": [{"name": "b", "url": "https://b2", "secret": {"set": True}},
                              {"name": "a", "url": "https://a", "secret": {"set": True}}]}
    result = merge(FIELDS, stored(), incoming)
    assert result.problems == []
    assert result.values["endpoints"] == [{"name": "b", "url": "https://b2", "secret": "SECRET-B"},
                                          {"name": "a", "url": "https://a", "secret": "SECRET-A"}]


def test_removing_a_record_never_gives_its_secret_to_another():
    incoming = {"endpoints": [{"name": "b", "url": "https://b"}, {"name": "c", "url": "https://c"}]}
    result = merge(FIELDS, stored(), incoming)
    assert result.values["endpoints"] == [{"name": "b", "url": "https://b", "secret": "SECRET-B"},
                                          {"name": "c", "url": "https://c"}]


def test_a_new_record_does_not_inherit_a_secret_from_a_removed_one():
    incoming = {"endpoints": [{"name": "d", "url": "https://d", "secret": {"set": True}}]}
    result = merge(FIELDS, stored(), incoming)
    assert result.values["endpoints"] == [{"name": "d", "url": "https://d"}]


def test_a_record_can_replace_or_clear_its_own_secret():
    incoming = {"endpoints": [{"name": "a", "url": "https://a", "secret": "fresh"},
                              {"name": "b", "url": "https://b", "secret": {"clear": True}}]}
    result = merge(FIELDS, stored(), incoming)
    assert result.values["endpoints"] == [{"name": "a", "url": "https://a", "secret": "fresh"},
                                          {"name": "b", "url": "https://b"}]


def test_a_bad_secret_inside_a_record_is_reported_at_its_record():
    incoming = {"endpoints": [{"name": "a", "url": "https://a", "secret": 7}]}
    result = merge(FIELDS, stored(), incoming)
    assert [p.key for p in result.problems] == ["endpoints.0.secret"]


def test_records_without_an_id_key_cannot_keep_a_secret_by_position():
    plain = Field("rows", "Rows", "records", fields=(Field("label", "Label", "text"), Field("api_token", "Token", "secret")))
    result = merge((plain,), {"rows": [{"label": "x", "api_token": "S1"}]},
                   {"rows": [{"label": "x", "api_token": {"set": True}}]})
    assert result.values["rows"] == [{"label": "x"}]


def test_merging_does_not_change_what_it_was_given():
    original, incoming = stored(), {"override_token": "n", "endpoints": [{"name": "a", "url": "u", "secret": {"set": True}}]}
    merge(FIELDS, original, incoming)
    assert original == stored()
    assert incoming == {"override_token": "n", "endpoints": [{"name": "a", "url": "u", "secret": {"set": True}}]}
