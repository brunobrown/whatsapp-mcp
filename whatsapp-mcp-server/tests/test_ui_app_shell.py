"""The shell: navigation that fits the screen and is built from the registry (UX-01, UX-02, EXT-01, EXT-03)."""
import os
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ft = pytest.importorskip("flet")

from ui.app import shell as shell_module  # noqa: E402
from ui.app.form import Confirm  # noqa: E402
from ui.app.shell import NavEntry, Navigator, build_shell, layout_for, menu_sheet, nav_entries  # noqa: E402
from ui.app.state import AppState  # noqa: E402
from ui.core.registry import Field, JsonFile, Registry, Section, all_sections  # noqa: E402


def walk(control):
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from walk(content)
    for tile in getattr(control, "destinations", None) or []:
        yield tile


def of_type(control, kind):
    return [c for c in walk(control) if isinstance(c, kind)]


def texts(control):
    return " | ".join(str(c.value) for c in of_type(control, ft.Text) if c.value)


def section(id, title, order=50, icon="settings"):
    return Section(id=id, title=title, icon=icon, order=order, storage=JsonFile(lambda: "/x"), validate=lambda v: [],
                   fields=(Field("a", "A", "text"),))


PAGES = [NavEntry("status", "Status", "monitor_heart", 0), NavEntry("account", "Conta", "person", 100)]


def entries_for(*sections):
    registry = Registry()
    for s in sections:
        registry.register(s)
    return nav_entries(registry.sections(), PAGES)


class Recorder:
    def __init__(self, dirty=False):
        self.state = AppState()
        self.state.sign_in()
        self.state.dirty = dirty
        self.asked, self.opened = [], []
        self.navigator = Navigator(self.state, ask=self.asked.append, on_open=self.opened.append)


@pytest.mark.parametrize("width, expected", [(320, "narrow"), (360, "narrow"), (599, "narrow"), (600, "medium"), (768, "medium"),
                                              (1023, "medium"), (1024, "wide"), (1280, "wide"), (3000, "wide")])
def test_the_layout_follows_the_width_of_the_window(width, expected):  # UX-01
    assert layout_for(width) == expected


def test_an_unknown_width_gets_the_roomy_layout_instead_of_a_crash():  # UX-01
    assert layout_for(None) == "wide" and layout_for(0) == "narrow"


def test_the_entries_come_from_the_registry_and_the_pages_in_order():  # EXT-01
    entries = entries_for(section("zeta", "Zeta", 70), section("alpha", "Alfa", 10))

    assert [e.id for e in entries] == ["status", "alpha", "zeta", "account"]
    assert [e.kind for e in entries] == ["page", "section", "section", "page"]


def test_every_icon_of_the_real_sections_is_a_known_icon():  # UX-02
    for entry in nav_entries(all_sections(), PAGES):
        assert shell_module.icon_of(entry.icon) is not ft.Icons.CIRCLE_OUTLINED or entry.icon == "circle", entry.icon


def test_an_unknown_icon_name_falls_back_instead_of_failing():  # UX-02
    assert shell_module.icon_of("not-an-icon") == ft.Icons.CIRCLE_OUTLINED


def build(width, entries, selected="status", content=None, state=None):
    rec = Recorder()
    view = build_shell(entries=entries, selected=selected, width=width, content=content or ft.Text("conteúdo da página"),
                       navigator=rec.navigator, state=state or rec.state, on_sign_out=lambda: rec.opened.append("out"), open_menu=lambda: rec.opened.append("menu"))
    return view, rec


def test_a_wide_window_has_an_extended_rail_with_every_entry(): # UX-01
    entries = entries_for(section("alpha", "Alfa", 10))
    view, _ = build(1280, entries, selected="alpha")

    rail = of_type(view, ft.NavigationRail)[0]

    assert rail.extended is True and [d.label for d in rail.destinations] == ["Status", "Alfa", "Conta"] and rail.selected_index == 1
    assert of_type(view, ft.NavigationBar) == []


def test_a_medium_window_has_a_compact_rail_that_still_shows_labels():  # UX-01
    view, _ = build(768, entries_for(section("alpha", "Alfa", 10)))

    rail = of_type(view, ft.NavigationRail)[0]

    assert rail.extended is False and rail.label_type == ft.NavigationRailLabelType.ALL and rail.scrollable is True


def test_a_narrow_window_has_no_rail_but_a_menu_button_and_the_title_of_the_page():  # UX-01
    view, rec = build(360, entries_for(section("alpha", "Alfa", 10)), selected="alpha")

    assert of_type(view, ft.NavigationRail) == []
    button = [b for b in of_type(view, ft.IconButton) if b.tooltip == "Abrir o menu"]
    assert len(button) == 1 and "Alfa" in texts(view)
    button[0].on_click(SimpleNamespace(control=button[0]))
    assert rec.opened == ["menu"]


def test_the_menu_sheet_lists_every_entry_and_marks_the_current_one():  # UX-01
    entries = entries_for(section("alpha", "Alfa", 10))
    rec = Recorder()
    sheet = menu_sheet(entries, "alpha", rec.navigator, close=lambda: rec.opened.append("closed"))

    tiles = of_type(sheet, ft.ListTile)

    assert isinstance(sheet, ft.BottomSheet) and [t.title for t in tiles] == ["Status", "Alfa", "Conta"] and [t.selected for t in tiles] == [False, True, False]
    tiles[2].on_click(SimpleNamespace(control=tiles[2]))
    assert rec.state.section_id == "account" and rec.opened[-1] == "closed"


def test_choosing_a_destination_of_the_rail_opens_that_page():  # UX-01
    view, rec = build(1280, entries_for(section("alpha", "Alfa", 10)))
    rail = of_type(view, ft.NavigationRail)[0]

    rail.on_change(SimpleNamespace(control=SimpleNamespace(selected_index=1), data="1"))

    assert rec.state.section_id == "alpha" and rec.opened == ["alpha"]


def test_the_landmarks_name_the_navigation_and_the_main_content():  # UX-02
    view, _ = build(1280, entries_for(section("alpha", "Alfa", 10)))

    labels = {c.label for c in walk(view) if isinstance(c, ft.Semantics)}

    assert {"Navegação principal", "Conteúdo principal"} <= labels


def test_the_content_of_the_selected_page_is_what_the_shell_shows():  # EXT-03
    view, _ = build(1280, entries_for(section("alpha", "Alfa", 10)), content=ft.Text("formulário da seção alpha"))

    assert "formulário da seção alpha" in texts(view)


def test_a_section_registered_later_appears_without_editing_the_shell():  # EXT-01
    before = entries_for(section("alpha", "Alfa", 10))
    after = entries_for(section("alpha", "Alfa", 10), section("brandnew", "Recém-chegada", 15))
    view, _ = build(1280, after, selected="brandnew")

    rail = of_type(view, ft.NavigationRail)[0]

    assert "Recém-chegada" not in [e.title for e in before]
    assert [d.label for d in rail.destinations] == ["Status", "Alfa", "Recém-chegada", "Conta"] and rail.selected_index == 2


def test_registered_test_section_is_rendered_by_shell():  # EXT-03
    from ui.app.form import FormActions, form_view
    from ui.core.envlayer import EnvLayer
    from ui.core.formflow import FormFlow
    from ui.core.registry import EnvKeys
    from ui.core.store import ConfigStore

    registry = Registry()
    registry.register(Section(id="brandnew", title="Recém-chegada", icon="settings", order=15, storage=EnvKeys(), validate=lambda v: [],
                              fields=(Field("zone", "Fuso da recém-chegada", "text", env_var="WHATSAPP_TIMEZONE"),)))
    entries = nav_entries(registry.sections(), PAGES)
    section_registered = registry.sections()[0]
    flow = FormFlow(section_registered, ConfigStore(), EnvLayer(environ={}))
    flow.load()
    page = form_view(flow, FormActions(flow, notify=lambda n: None, ask=lambda c: None, refresh=lambda: None, note_restart=lambda s: None))

    view, _ = build(1280, entries, selected="brandnew", content=page)

    rail = of_type(view, ft.NavigationRail)[0]
    assert [d.label for d in rail.destinations] == ["Status", "Recém-chegada", "Conta"] and rail.selected_index == 1
    assert "Recém-chegada" in texts(view) and "Fuso da recém-chegada" in " ".join(str(getattr(c, "label", "")) for c in walk(view))


def test_the_shell_never_names_a_section_of_the_project():  # EXT-01
    source = open(shell_module.__file__, encoding="utf-8").read()

    for name in ("send_policy", "monitor_profiles", "webhooks", "channel", "agent_safety", "envsettings", "database"):
        assert not re.search(rf"\b{name}\b", source), name


def test_the_header_shows_what_needs_a_restart_and_signs_out():  # CFG-08
    entries = entries_for(section("alpha", "Alfa", 10))
    view, rec = build(1280, entries)
    assert "Reinício" not in texts(view)

    rec.state.note_restart("bridge")
    rec.state.note_restart("server")
    view, rec = build(1280, entries, state=rec.state)
    assert "Reinício pendente: o bridge e o servidor MCP" in texts(view)
    out = [b for b in of_type(view, (ft.TextButton, ft.OutlinedButton)) if "Sair" in str(b.content)]
    out[0].on_click(SimpleNamespace(control=out[0]))
    assert rec.opened[-1] == "out"


def test_going_to_another_page_with_unsaved_changes_asks_first():  # CFG-06
    rec = Recorder(dirty=True)

    rec.navigator.go("alpha")

    assert rec.state.section_id is None and len(rec.asked) == 1 and isinstance(rec.asked[0], Confirm)
    assert "salvar" in (rec.asked[0].title + rec.asked[0].body).lower()
    rec.asked[0].action()
    assert rec.state.section_id == "alpha" and rec.state.dirty is False and rec.opened == ["alpha"]


def test_going_to_another_page_without_changes_goes_straight_there():  # CFG-06
    rec = Recorder(dirty=False)

    rec.navigator.go("alpha")

    assert rec.state.section_id == "alpha" and rec.asked == []


def test_going_to_the_page_already_open_does_nothing():  # CFG-06
    rec = Recorder(dirty=True)
    rec.state.open("alpha")

    rec.navigator.go("alpha")

    assert rec.asked == [] and rec.opened == []


def test_the_shell_has_no_imperative_calls():  # EXT-02
    source = open(shell_module.__file__, encoding="utf-8").read()

    assert ".update(" not in source and "page.add" not in source


def test_no_row_that_wraps_holds_an_expanded_child():  # UX-01
    """An expanded child is invalid in a Row that wraps: Flutter draws a grey box over the whole region."""
    entries = entries_for(section("alpha", "Alfa", 10))
    for width in (360, 768, 1280):
        view, _ = build(width, entries)
        for row in of_type(view, ft.Row):
            if row.wrap:
                assert not [c for c in row.controls if getattr(c, "expand", None)], (width, [type(c).__name__ for c in row.controls])
