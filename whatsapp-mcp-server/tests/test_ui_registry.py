"""The registry of sections: the one place that says what the interface shows (EXT-01, CFG-11)."""
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.core import registry  # noqa: E402
from ui.core.registry import EnvKeys, Field, JsonFile, Problem, ReadOnly, Registry, Section  # noqa: E402


def ok(values):
    return []


def section(id="demo", order=50, fields=None, storage=None, **extra):
    return Section(id=id, title=id.title(), icon="settings", order=order, storage=storage or JsonFile(lambda: "/x/demo.json"),
                   fields=tuple(fields if fields is not None else [Field("name", "Name", "text")]), validate=ok, **extra)


def test_every_kind_the_design_names_is_accepted():
    for kind in ("bool", "int", "float", "text", "choice", "multichoice", "secret", "list", "records"):
        kwargs = {}
        if kind in ("choice", "multichoice"):
            kwargs["options"] = ("a", "b")
        if kind == "records":
            kwargs["fields"] = (Field("n", "N", "text"),)
        if kind == "secret":
            kwargs["secret"] = True
        assert Field("thing", "Thing", kind, **kwargs).kind == kind


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="kind"):
        Field("thing", "Thing", "colour")


def test_a_choice_needs_options_and_records_need_fields():
    with pytest.raises(ValueError, match="options"):
        Field("mode", "Mode", "choice")
    with pytest.raises(ValueError, match="fields"):
        Field("items", "Items", "records")


@pytest.mark.parametrize("key, label", [("override_token", "Override"), ("webhook_secret", "x"), ("pw", "Password"),
                                         ("db_key", "Database"), ("x", "API token"), ("passwd", "x")])
def test_a_field_that_looks_like_a_secret_must_be_declared_secret(key, label):
    with pytest.raises(ValueError, match="secret"):
        Field(key, label, "text")
    assert Field(key, label, "secret", secret=True).secret


def test_a_secret_field_nested_in_records_is_checked_too():
    with pytest.raises(ValueError, match="secret"):
        Field("endpoints", "Endpoints", "records", fields=(Field("secret_value", "S", "text"),))


def test_a_field_of_kind_secret_is_always_secret():
    assert Field("thing", "Thing", "secret").secret is True


def test_ids_are_plain_lowercase_words():
    for bad in ("", "Has Space", "../etc", "1abc", "a-b"):
        with pytest.raises(ValueError, match="id"):
            section(id=bad)


def test_the_same_id_cannot_be_registered_twice():
    reg = Registry()
    reg.register(section("one"))

    with pytest.raises(ValueError, match="already"):
        reg.register(section("one"))


def test_sections_come_back_in_order_then_by_title():
    reg = Registry()
    for s in (section("c", order=30), section("b", order=10), section("a", order=10)):
        reg.register(s)

    assert [s.id for s in reg.sections()] == ["a", "b", "c"]


def test_an_environment_field_must_be_one_the_server_and_bridge_apply():
    with pytest.raises(ValueError, match="EDITABLE_ENV"):
        Field("path", "Path", "text", env_var="WHATSAPP_SEND_POLICY")
    with pytest.raises(ValueError, match="EDITABLE_ENV"):
        Field("home", "Home", "text", env_var="HOME")
    assert Field("tz", "Zone", "text", env_var="WHATSAPP_TIMEZONE").env_var == "WHATSAPP_TIMEZONE"


def test_a_read_only_field_may_show_a_variable_that_is_never_editable():
    shown = Field("db", "Encryption", "text", env_var="WHATSAPP_DB_ENCRYPTION", readonly=True)

    assert shown.readonly and shown.env_var == "WHATSAPP_DB_ENCRYPTION"


def test_env_storage_sections_only_hold_environment_fields():
    with pytest.raises(ValueError, match="env_var"):
        section(storage=EnvKeys(), fields=[Field("name", "Name", "text")])


def test_a_section_without_fields_is_refused():
    with pytest.raises(ValueError, match="field"):
        section(fields=[])


def test_two_fields_of_a_section_cannot_share_a_key():
    with pytest.raises(ValueError, match="repeated"):
        section(fields=[Field("a", "A", "text"), Field("a", "B", "text")])


def test_restart_scope_is_none_the_bridge_or_the_server():
    assert section(restart="bridge").restart == "bridge"
    with pytest.raises(ValueError, match="restart"):
        section(restart="everything")


def test_discovery_finds_new_modules_without_a_central_list(tmp_path, monkeypatch):  # EXT-01
    package = tmp_path / "demo_sections"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name in ("alpha", "beta"):
        (package / f"{name}.py").write_text(textwrap.dedent(f'''
            from ui.core.registry import Field, JsonFile, Section, default
            default.register(Section(id="{name}", title="{name}", icon="x", order=1, storage=JsonFile(lambda: "/x/{name}.json"),
                                     fields=(Field("a", "A", "text"),), validate=lambda v: []))
        '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    reg = Registry()
    monkeypatch.setattr(registry, "default", reg)     # the modules register on the default registry when imported

    found = reg.discover("demo_sections")

    assert sorted(found) == ["alpha", "beta"] and sorted(s.id for s in reg.sections()) == ["alpha", "beta"]
    assert reg.discover("demo_sections") == []       # a second look adds nothing and breaks nothing


def test_validate_returns_problems_with_the_field_they_belong_to():
    problem = Problem("mode", "must be allowlist or denylist")

    assert (problem.field, problem.message) == ("mode", "must be allowlist or denylist")
    assert Problem(None, "the file is invalid").field is None


def test_read_only_storage_has_no_path_to_write():
    assert isinstance(ReadOnly(), ReadOnly) and not hasattr(ReadOnly(), "path")


# ---- Field.check: the generic rule for one value (type, bounds, options, size, no control characters) ----

def check(kind, value, **extra):
    return Field("thing", "Thing", kind, **extra).check(value)


def test_an_unset_value_is_always_fine():
    for kind in ("bool", "int", "text", "list"):
        assert check(kind, None) is None


@pytest.mark.parametrize("value", [True, False])
def test_a_bool_accepts_only_booleans(value):
    assert check("bool", value) is None


@pytest.mark.parametrize("value", ["true", 1, 0, "", []])
def test_a_bool_refuses_anything_else(value):
    assert check("bool", value)


def test_an_int_respects_its_bounds_and_is_not_a_bool():
    assert check("int", 5, bounds=(1, 10)) is None
    assert check("int", 0, bounds=(1, 10)) and check("int", 11, bounds=(1, 10))
    assert check("int", True) and check("int", 2.5) and check("int", "3")


def test_a_float_refuses_nan_and_infinity_and_respects_bounds():
    assert check("float", 2.5, bounds=(0, 60)) is None and check("float", 3, bounds=(0, 60)) is None
    assert check("float", float("nan")) and check("float", float("inf")) and check("float", 61, bounds=(0, 60))
    assert check("float", True)


def test_text_has_a_size_limit_and_no_control_characters():
    assert check("text", "hello") is None
    assert check("text", "x" * 11, max_length=10)
    assert check("text", "a\x00b") and check("text", "a\nb") and check("text", "a\x7fb")
    assert check("text", 5)


def test_a_choice_must_be_one_of_its_options():
    assert check("choice", "a", options=("a", "b")) is None
    assert check("choice", "c", options=("a", "b")) and check("choice", 1, options=("a", "b"))


def test_a_multichoice_is_a_list_of_distinct_options():
    assert check("multichoice", ["a", "b"], options=("a", "b", "c")) is None
    assert check("multichoice", ["a", "a"], options=("a", "b")) and check("multichoice", ["z"], options=("a",))
    assert check("multichoice", "a", options=("a",))


def test_a_list_is_a_bounded_list_of_clean_texts():
    assert check("list", ["a", "b"]) is None
    assert check("list", "a") and check("list", [1]) and check("list", ["x\n"]) and check("list", ["x" * 11], max_length=10)
    assert check("list", ["x"] * 201)


def test_a_secret_is_a_clean_bounded_text():
    assert check("secret", "s" * 40, secret=True) is None
    assert check("secret", "a\nb", secret=True) and check("secret", 5, secret=True)


def test_records_check_each_field_and_refuse_unknown_keys():
    sub = (Field("name", "Name", "text"), Field("n", "N", "int", bounds=(1, 3)))
    records = Field("rows", "Rows", "records", fields=sub)

    assert records.check([{"name": "a", "n": 2}, {"name": "b"}]) is None
    assert "n" in records.check([{"name": "a", "n": 9}])
    assert "extra" in records.check([{"name": "a", "extra": 1}])
    assert records.check({"name": "a"}) and records.check(["a"]) and records.check([{}] * 101)


def test_an_error_message_never_repeats_the_value():
    message = Field("thing", "Thing", "choice", options=("a",)).check("top-secret-value")

    assert "top-secret-value" not in message


# ---- Field.risky: confirm only the choices that weaken a defence ----

def test_a_field_without_a_warning_is_never_risky():
    assert Field("x", "X", "bool").risky(True) is False


def test_a_warning_with_values_applies_to_those_values_only():
    field = Field("mode", "Mode", "choice", options=("draft", "auto"), risk="sem aprovação", risk_when=("auto",))

    assert field.risky("auto") is True and field.risky("draft") is False and field.risky(None) is False


def test_a_warning_without_values_always_applies():
    assert Field("host", "Host", "text", risk="exposto").risky("anything") is True
