"""The shell: navigation that fits the screen, built from what is registered.

It knows no section by name. The entries are the registered sections plus a few fixed pages, in order; the page
shown is whatever the caller gives it for the selected entry. A wide window gets an extended rail, a medium one a
compact rail with labels, a narrow one a title and a menu button that opens the same list in a sheet. Leaving a
page with unsaved changes asks first.
"""
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

import flet as ft

from ui.app.form import Confirm, confirm_dialog
from ui.app.state import AppState
from ui.core.registry import Section

NARROW_BELOW = 600
WIDE_FROM = 1024
APP_TITLE = "WhatsApp MCP"


@dataclass(frozen=True)
class NavEntry:
    id: str
    title: str
    icon: str
    order: int
    kind: str = "page"          # "section" (registered) or "page" (fixed)


def nav_entries(sections: Sequence[Section], pages: Sequence[NavEntry]) -> List[NavEntry]:
    entries = [NavEntry(s.id, s.title, s.icon, s.order, "section") for s in sections] + list(pages)
    return sorted(entries, key=lambda e: (e.order, e.title))


def icon_of(name: str):
    """The Flet icon for a name like "shield"; a plain circle when there is none, never an error."""
    return getattr(ft.Icons, (name or "").upper(), ft.Icons.CIRCLE_OUTLINED)


def layout_for(width: Optional[float]) -> str:
    if width is None:
        return "wide"
    return "narrow" if width < NARROW_BELOW else "medium" if width < WIDE_FROM else "wide"


class Navigator:
    """Opening a page, asking first when the one open has unsaved changes."""

    def __init__(self, state: AppState, ask: Callable[[Confirm], Any], on_open: Callable[[str], Any] = lambda page_id: None):
        self._state, self._ask, self._on_open = state, ask, on_open

    def _open(self, page_id: str) -> None:
        self._state.set_dirty(False)
        self._state.open(page_id)
        self._on_open(page_id)

    def go(self, page_id: str) -> None:
        self._state.touch()
        if self._state.section_id == page_id:
            return
        if self._state.dirty:
            self._ask(Confirm("Sair sem salvar?", "Há alterações que ainda não foram salvas nesta página. Se sair agora, elas serão perdidas.",
                              "Sair sem salvar", lambda: self._open(page_id)))
            return
        self._open(page_id)


def _header(state: AppState, on_sign_out: Callable[[], Any]) -> ft.Control:
    pending = " e ".join({"bridge": "o bridge", "server": "o servidor MCP"}.get(s, s) for s in state.restart)
    items: List[ft.Control] = [ft.Text(APP_TITLE, size=19, weight=ft.FontWeight.W_700, expand=True)]
    if pending:
        items.append(ft.Text(f"Reinício pendente: {pending}", size=14, color=ft.Colors.TERTIARY))
    items.append(ft.TextButton(content="Sair", icon=ft.Icons.LOGOUT, height=48, tooltip="Encerrar a sessão", on_click=lambda e: on_sign_out()))
    # not wrapping: the title expands, and an expanded child is invalid inside a Row that wraps (Flutter draws a grey box instead)
    return ft.Container(padding=ft.Padding.symmetric(horizontal=16, vertical=4), content=ft.Row(
        vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=items))


def _main(content: ft.Control) -> ft.Control:
    return ft.Semantics(label="Conteúdo principal", container=True, content=ft.Container(
        expand=True, padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        content=ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, controls=[content])))


def build_shell(entries: Sequence[NavEntry], selected: str, width: Optional[float], content: ft.Control, navigator: Navigator,
                state: AppState, on_sign_out: Callable[[], Any], open_menu: Callable[[], Any]) -> ft.Control:
    layout = layout_for(width)
    index = next((i for i, e in enumerate(entries) if e.id == selected), 0)
    if layout == "narrow":
        top = ft.Container(padding=ft.Padding.symmetric(horizontal=8, vertical=4), content=ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.IconButton(icon=ft.Icons.MENU, tooltip="Abrir o menu", on_click=lambda e: open_menu()),
                ft.Semantics(header=True, content=ft.Text(entries[index].title, size=19, weight=ft.FontWeight.W_700, expand=True)),
                ft.IconButton(icon=ft.Icons.LOGOUT, tooltip="Sair: encerrar a sessão", on_click=lambda e: on_sign_out())]))
        notice = [ft.Container(padding=ft.Padding.symmetric(horizontal=16), content=_header(state, on_sign_out))] if state.restart else []
        return ft.Column(expand=True, spacing=0, controls=[top, *notice, _main(content)])
    rail = ft.NavigationRail(
        destinations=[ft.NavigationRailDestination(icon=icon_of(e.icon), label=e.title) for e in entries], selected_index=index,
        extended=layout == "wide", min_extended_width=240, label_type=ft.NavigationRailLabelType.ALL, scrollable=True,
        on_change=lambda e: navigator.go(entries[int(e.control.selected_index)].id))
    return ft.Row(expand=True, spacing=0, controls=[
        ft.Semantics(label="Navegação principal", container=True, content=ft.Container(
            border=ft.Border(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)), content=rail)),
        ft.Column(expand=True, spacing=0, controls=[_header(state, on_sign_out), _main(content)])])


def menu_sheet(entries: Sequence[NavEntry], selected: str, navigator: Navigator, close: Callable[[], Any]) -> ft.BottomSheet:
    def tile(entry: NavEntry) -> ft.ListTile:
        def chosen(e) -> None:
            navigator.go(entry.id)
            close()
        return ft.ListTile(leading=ft.Icon(icon_of(entry.icon)), title=entry.title, selected=entry.id == selected, on_click=chosen)
    return ft.BottomSheet(scrollable=True, on_dismiss=lambda e: close(), content=ft.Column(
        tight=True, spacing=0, controls=[ft.Container(padding=16, content=ft.Semantics(header=True, content=ft.Text("Menu", size=19, weight=ft.FontWeight.W_700))),
                                          *[tile(e) for e in entries]]))


@ft.component
def Shell(entries: List[NavEntry], render_page: Callable[[str], ft.Control], state: AppState, on_sign_out: Callable[[], Any]):
    width, set_width = ft.use_state(lambda: ft.context.page.width)
    sheet_open, set_sheet = ft.use_state(False)
    asking, set_asking = ft.use_state(None)
    selected = state.section_id or entries[0].id
    navigator = Navigator(state, ask=set_asking)

    def watch_size() -> None:
        page = ft.context.page
        page.on_resize = lambda e: set_width(e.width)
        set_width(page.width)

    def stop_watching() -> None:
        ft.context.page.on_resize = None

    ft.use_effect(watch_size, [], cleanup=stop_watching)
    if asking:
        dialog = confirm_dialog(asking, close=lambda: set_asking(None))
    elif sheet_open:
        dialog = menu_sheet(entries, selected, navigator, close=lambda: set_sheet(False))
    else:
        dialog = None
    ft.use_dialog(dialog)
    return build_shell(entries, selected, width, render_page(selected), navigator, state, on_sign_out, open_menu=lambda: set_sheet(True))
