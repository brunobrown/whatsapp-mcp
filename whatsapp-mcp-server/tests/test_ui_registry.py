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
