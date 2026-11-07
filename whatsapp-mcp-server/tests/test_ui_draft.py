"""The draft of a form: what changed, leaving with unsaved changes, restoring defaults, locked fields (CFG-06, CFG-07)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.core.draft import Draft, LockedField  # noqa: E402
from ui.core.registry import Field, JsonFile, Section  # noqa: E402
from ui.core.secrets import CLEAR  # noqa: E402


def section():
    rows = Field("rows", "Rows", "records", id_key="name", fields=(Field("name", "Name", "text"),))
    return Section(id="demo", title="Demo", icon="x", order=1, storage=JsonFile(lambda: "/x"), validate=lambda v: [], fields=(
        Field("mode", "Mode", "choice", options=("allowlist", "denylist"), default="denylist"),
        Field("size", "Size", "int", bounds=(1, 10)),
        Field("tags", "Tags", "list"),
        Field("override_token", "Override", "secret"),
        rows))


def original():
    return {"mode": "allowlist", "size": 5, "tags": ["a", "b"], "override_token": {"set": True}, "rows": [{"name": "n1"}, {"name": "n2"}]}


def draft(locked=()):
    return Draft(section(), original(), locked=locked)


def test_a_fresh_draft_has_no_changes():
    d = draft()

    assert d.dirty is False and d.changes() == {}


def test_setting_a_value_marks_the_draft_and_setting_it_back_clears_the_mark():
    d = draft()

    d.set("size", 6)
    assert d.dirty and d.changes() == {"size": 6}
    d.set("size", 5)
    assert not d.dirty and d.changes() == {}


def test_a_missing_key_and_an_explicit_none_are_the_same_value():
    d = Draft(section(), {"mode": "allowlist"})

    d.set("size", None)

    assert not d.dirty


def test_the_order_of_a_list_is_a_change():
    d = draft()

    d.set("tags", ["b", "a"])

    assert d.dirty and d.changes() == {"tags": ["b", "a"]}


def test_an_unchanged_secret_is_not_a_change_but_a_new_one_is():
    d = draft()

    d.set("override_token", {"set": True})
    assert d.changes() == {}
    d.set("override_token", "a-new-secret-value")
    assert d.changes() == {"override_token": "a-new-secret-value"}
    d.set("override_token", CLEAR)
    assert d.changes() == {"override_token": CLEAR}


def test_an_unknown_field_is_an_error():
    with pytest.raises(KeyError):
        draft().set("nonexistent", 1)


def test_the_draft_does_not_share_its_lists_with_the_caller():
    source = original()
    d = Draft(section(), source)

    d.set("tags", d.values["tags"] + ["c"])

    assert source["tags"] == ["a", "b"]


def test_leaving_a_clean_form_needs_no_confirmation():  # CFG-06
    assert draft().leave() is True


def test_leaving_with_unsaved_changes_requires_confirmation():  # CFG-06
    d = draft()
    d.set("size", 9)

    assert d.leave() is False and d.dirty and d.values["size"] == 9
    assert d.leave(confirmed=True) is True
    assert not d.dirty and d.values["size"] == 5


def test_discard_goes_back_to_what_was_loaded():
    d = draft()
    d.set("size", 9)
    d.set("tags", [])

    d.discard()

    assert d.values == original() and not d.dirty


def test_after_a_save_the_draft_takes_the_saved_values_as_its_base():
    d = draft()
    d.set("size", 9)

    d.saved({**original(), "size": 9})

    assert not d.dirty and d.values["size"] == 9


def test_a_locked_field_cannot_be_changed():  # CFG-04
    d = draft(locked={"size"})

    with pytest.raises(LockedField, match="size"):
        d.set("size", 6)
    assert d.values["size"] == 5 and not d.dirty


def test_restoring_a_default_asks_first_and_changes_nothing_until_confirmed():  # CFG-07
    d = draft()

    assert d.restore_default("size") is False
    assert d.values["size"] == 5 and not d.dirty


def test_a_confirmed_restore_removes_the_custom_value():  # CFG-07
    d = draft()

    assert d.restore_default("size", confirmed=True) is True
    assert d.values["size"] is None and d.changes() == {"size": None}


def test_restoring_a_field_that_already_has_the_default_is_no_change():  # CFG-07
    d = Draft(section(), {})

    d.restore_default("size", confirmed=True)

    assert not d.dirty


def test_restoring_a_locked_field_is_refused():  # CFG-04
    with pytest.raises(LockedField):
        draft(locked={"size"}).restore_default("size", confirmed=True)


def test_restoring_the_whole_section_asks_first_and_skips_locked_fields():  # CFG-07
    d = draft(locked={"size"})

    assert d.restore_defaults() is False and not d.dirty
    assert d.restore_defaults(confirmed=True) is True
    assert d.values["mode"] is None and d.values["tags"] is None and d.values["size"] == 5


def test_removing_an_item_asks_first_and_then_removes_that_one_only():  # UX-05
    d = draft()

    assert d.remove_item("rows", 0) is False and len(d.values["rows"]) == 2
    assert d.remove_item("rows", 0, confirmed=True) is True
    assert d.values["rows"] == [{"name": "n2"}] and d.dirty


def test_removing_an_item_that_is_not_there_is_an_error():
    with pytest.raises(IndexError):
        draft().remove_item("rows", 5, confirmed=True)


def test_adding_an_item_is_a_change_without_confirmation():
    d = draft()

    d.add_item("rows", {"name": "n3"})

    assert d.dirty and d.values["rows"][-1] == {"name": "n3"}


def test_errors_name_the_field_and_do_not_repeat_the_value():  # CFG-02
    d = draft()
    d.set("size", 99)
    d.set("mode", "nonsense-value")

    errors = d.errors()

    assert set(errors) == {"size", "mode"} and "nonsense-value" not in errors["mode"]


def test_a_valid_draft_has_no_errors():
    assert draft().errors() == {}


def test_restore_default_requires_confirmation_then_removes_override():  # CFG-07
    d = draft()

    assert d.restore_default("size") is False and d.values["size"] == 5       # asked, nothing removed
    assert d.restore_default("size", confirmed=True) is True                  # confirmed, now removed
    assert d.values["size"] is None and d.changes() == {"size": None}
