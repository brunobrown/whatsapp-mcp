"""Section registry (EXT-01, EXT-03, CFG-12)."""
import sys
import textwrap

import pytest

from ui.config.fields import Field, Problem
from ui.config.registry import EnvKeys, JsonFile, ReadOnly, Registry, Section


def make(section_id="alpha", order=10, fields=None, storage=None, **kw):
    return Section(id=section_id, title=section_id.title(), order=order,
                   storage=storage or JsonFile(lambda: "/tmp/never-opened.json"),
                   fields=fields if fields is not None else (Field("name", "Name", "text"),), **kw)


def test_a_section_registered_at_runtime_appears_in_order_without_touching_anything_else():
    reg = Registry()
    reg.register(make("beta", order=20))
    reg.register(make("alpha", order=10))
    assert [s.id for s in reg.sections()] == ["alpha", "beta"]
    assert reg.get("beta").title == "Beta"


def test_a_duplicate_section_id_is_refused():
    reg = Registry()
    reg.register(make("alpha"))
    with pytest.raises(ValueError):
        reg.register(make("alpha", order=99))
    assert len(reg.sections()) == 1


def test_a_repeated_field_key_in_a_section_is_refused():
    with pytest.raises(ValueError):
        make(fields=(Field("name", "Name", "text"), Field("name", "Again", "int")))


def test_an_unknown_section_id_is_an_error_not_a_path():
    reg = Registry()
    with pytest.raises(KeyError):
        reg.get("../../etc/passwd")
    with pytest.raises(KeyError):
        reg.path_of("missing")


def test_only_the_declared_path_of_a_registered_json_section_is_ever_resolved():
    reg = Registry()
    reg.register(make("alpha", storage=JsonFile(lambda: "/data/alpha.json")))
    assert reg.path_of("alpha") == "/data/alpha.json"


def test_a_section_without_a_json_file_has_no_path():
    reg = Registry()
    reg.register(make("env", storage=EnvKeys()))
    reg.register(make("facts", storage=ReadOnly(), fields=()))
    for sid in ("env", "facts"):
        with pytest.raises(ValueError):
            reg.path_of(sid)


def test_a_hidden_section_is_reachable_by_id_but_not_listed():
    reg = Registry()
    reg.register(make("visible"))
    reg.register(make("hidden", listed=False))
    assert [s.id for s in reg.sections()] == ["visible"]
    assert reg.get("hidden").id == "hidden"


def test_the_restart_scope_is_bridge_server_or_none():
    assert make().restart is None
    assert make(restart="bridge").restart == "bridge"
    assert make(restart="server").restart == "server"
    with pytest.raises(ValueError):
        make(restart="nowhere")


def test_the_validator_defaults_to_accepting_everything_and_reports_problems():
    assert make().validator({"anything": 1}) == []
    sec = make(validator=lambda values: [Problem("name", "bad")])
    assert sec.validator({}) == [Problem("name", "bad")]


def test_a_section_finds_its_fields_by_key():
    sec = make()
    assert sec.field("name").label == "Name"
    with pytest.raises(KeyError):
        sec.field("missing")


def test_a_section_is_read_only_when_its_storage_is():
    assert make(storage=ReadOnly(), fields=()).read_only is True
    assert make().read_only is False


def test_discovery_registers_the_sections_a_package_declares(tmp_path, monkeypatch):
    pkg = tmp_path / "fake_sections"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "one.py").write_text(textwrap.dedent('''
        from ui.config.fields import Field
        from ui.config.registry import JsonFile, Section
        SECTION = Section(id="one", title="One", order=2, storage=JsonFile(lambda: "x.json"), fields=(Field("a", "A", "text"),))
    '''))
    (pkg / "two.py").write_text(textwrap.dedent('''
        from ui.config.fields import Field
        from ui.config.registry import ReadOnly, Section
        SECTIONS = (Section(id="two", title="Two", order=1, storage=ReadOnly(), fields=()),)
    '''))
    (pkg / "helper.py").write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        reg = Registry()
        reg.discover("fake_sections")
        assert [s.id for s in reg.sections()] == ["two", "one"]
    finally:
        for name in [m for m in sys.modules if m.startswith("fake_sections")]:
            del sys.modules[name]


def test_a_section_in_a_test_shows_without_changing_the_shell():  # EXT-03
    reg = Registry()
    reg.register(make("probe", order=1))
    assert [s.id for s in reg.sections()] == ["probe"]


def test_a_section_without_a_translation_keeps_the_file_shape():
    section = make("plain", order=1)
    assert section.decode({"a": [1]}) == {"a": [1]}
    assert section.encode({"a": [1]}) == {"a": [1]}
