"""The form of one section.

form_view is a function of the flow: it shows what the flow holds and wires every control to FormActions. FormActions
is where a click becomes a call (and where a question to the operator is asked before something destructive or
risky). SectionForm is the thin component that keeps a flow alive, loads it off the event loop and shows the dialog.
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

import flet as ft

from ui.app.fields import MIN_TARGET, FieldProps, field_row
from ui.app.notifier import Notice
from ui.core.formflow import FormFlow
from ui.core.registry import JsonFile, Section

MAX_WIDTH = 760
_WORDS = {"bridge": "o bridge", "server": "o servidor MCP"}


@dataclass(frozen=True)
class Confirm:
    title: str
    body: str
    confirm_label: str
    action: Callable[[], None]


class FormActions:
    """What the buttons and the controls of a form do."""

    def __init__(self, flow: FormFlow, notify: Callable[[Notice], Any], ask: Callable[[Confirm], Any], refresh: Callable[[], Any],
                 note_restart: Callable[[str], Any], dirty_changed: Callable[[bool], Any] = lambda dirty: None):
        self.flow, self._notify, self._ask, self._note_restart = flow, notify, ask, note_restart
        self._redraw, self._dirty_changed = refresh, dirty_changed
        self.conflict = False
        self.general: List[str] = []
        self.restarts: List[str] = []

    @property
    def section(self) -> Section:
        return self.flow.section

    def _refresh(self) -> None:
        """Draw again, and tell the shell whether the form has unsaved changes (it asks before leaving)."""
        self._dirty_changed(self.flow.dirty)
        self._redraw()

    def _file_name(self) -> str:
        storage = self.section.storage
        return storage.path().replace("\\", "/").rsplit("/", 1)[-1] if isinstance(storage, JsonFile) else "ui_settings.json"

    # -- editing ------------------------------------------------------------------------------------
    def change(self, key: str) -> Callable[[Any], None]:
        def handler(raw: Any) -> None:
            field = self.section.field(key)
            if field.kind in ("choice", "bool") and field.risky(raw) and not field.risky(self.flow.shown(key)):
                self._ask(Confirm(f"Confirmar: {field.label}", field.risk or "", "Confirmar", lambda: self._apply(key, raw)))
                return
            self._apply(key, raw)
        return handler

    def _apply(self, key: str, raw: Any) -> None:
        self.flow.set(key, raw)
        self.conflict, self.general = False, []
        self._refresh()

    def add(self, key: str) -> None:
        self.flow.add_item(key)
        self._refresh()

    def remove(self, key: str, index: int) -> None:
        label = f"{self.section.field(key).label} {index + 1}"

        def do() -> None:
            self.flow.remove_item(key, index, confirmed=True)
            self._refresh()
        self._ask(Confirm(f"Remover {label}?", f"{label} sai da lista quando você salvar. Antes disso, descartar as alterações o traz de volta.", "Remover", do))

    def restore(self, key: str) -> None:
        label = self.section.field(key).label

        def do() -> None:
            self.flow.restore_default(key, confirmed=True)
            self._refresh()
        self._ask(Confirm(f"Restaurar o padrão de {label}?", f"O valor personalizado de {label} sai quando você salvar; passa a valer o padrão.", "Restaurar", do))

    def restore_all(self) -> None:
        def do() -> None:
            for field in self.section.fields:
                if not field.readonly and not self.flow.is_locked(field.key):
                    self.flow.restore_default(field.key, confirmed=True)
            self._refresh()
        self._ask(Confirm("Restaurar todos os padrões?", f"Todos os valores personalizados de {self.section.title} saem quando você salvar.", "Restaurar", do))

    def discard(self) -> None:
        if not self.flow.dirty:
            return

        def do() -> None:
            self.flow.discard()
            self.conflict, self.general = False, []
            self._refresh()
        self._ask(Confirm("Descartar as alterações?", "O que você mudou nesta seção e ainda não salvou será perdido.", "Descartar", do))

    def reload(self) -> None:
        self.flow.reload()
        self.conflict, self.general = False, []
        self._refresh()

    # -- saving -------------------------------------------------------------------------------------------
    def save(self, confirmed_replace: bool = False) -> None:
        outcome = self.flow.save(confirmed_replace=confirmed_replace)
        self.conflict, self.general = outcome.conflict, list(outcome.general)
        if outcome.needs_confirmation:
            self._ask(Confirm("Substituir o arquivo?", f"{self._file_name()} existe, mas não pôde ser lido. Salvar o substitui pelo que está na tela.",
                              "Substituir", lambda: self.save(confirmed_replace=True)))
        elif outcome.ok and not outcome.unchanged:
            self._notify(Notice.saved(self.section.title))
            for scope in outcome.restarts:
                self.restarts = sorted({*self.restarts, scope})
                self._note_restart(scope)
                self._notify(Notice.restart_needed(scope))
        elif not outcome.ok:
            self._notify(Notice.save_failed(self.section.title))
        self._refresh()


# ---- the view -------------------------------------------------------------------------------------------
def _banner(kind: str, text: str, *actions: ft.Control) -> ft.Control:
    colour = {"error": ft.Colors.ERROR, "warning": ft.Colors.TERTIARY}.get(kind, ft.Colors.PRIMARY)
    icon = {"error": ft.Icons.ERROR_OUTLINE, "warning": ft.Icons.WARNING_AMBER}.get(kind, ft.Icons.INFO_OUTLINE)
    label = {"error": "Erro", "warning": "Atenção"}.get(kind, "Informação")
    return ft.Semantics(live_region=True, container=True, content=ft.Container(
        padding=12, border=ft.Border.all(1, colour), border_radius=6, content=ft.Column(spacing=8, controls=[
            ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
                ft.Icon(icon, color=colour, size=22, semantics_label=label), ft.Text(text, size=16, expand=True, selectable=True)]),
            *actions])))


def loading_view(title: str) -> ft.Control:
    return ft.Semantics(live_region=True, container=True, content=ft.Row(spacing=12, controls=[
        ft.ProgressRing(width=24, height=24, stroke_width=3), ft.Text(f"Carregando {title}…", size=16)]))


def _heading(title: str) -> ft.Control:
    return ft.Semantics(header=True, heading_level=1, content=ft.Text(title, size=28, weight=ft.FontWeight.W_700))


def form_view(flow: FormFlow, actions: FormActions) -> ft.Control:
    section = flow.section
    read_only = not section.storage or type(section.storage).__name__ == "ReadOnly"
    parts: List[ft.Control] = [_heading(section.title)]
    if section.description:
        parts.append(ft.Text(section.description, size=16))

    if flow.status == "unavailable":
        parts.append(_banner("warning", flow.banner or "Não foi possível validar esta seção agora.",
                             ft.TextButton(content="Tentar de novo", height=MIN_TARGET, on_click=lambda e: actions.reload())))
    elif flow.status == "error":
        parts.append(_banner("error", flow.banner or "O que está salvo tem problemas. Corrija os campos marcados e salve."))
    elif flow.status == "empty" and not read_only:
        env = type(section.storage).__name__ == "EnvKeys"
        parts.append(_banner("info", "Nenhum valor desta seção foi personalizado; valem os padrões." if env
                             else "O arquivo ainda não existe; os valores abaixo são os padrões. Ele será criado quando você salvar."))
    for warning in flow.warnings:
        parts.append(_banner("warning", warning))
    if actions.conflict:
        parts.append(_banner("error", "O arquivo mudou no disco depois de carregado, e nada foi gravado. Suas alterações continuam na tela.",
                             ft.Button(content="Recarregar", height=MIN_TARGET, on_click=lambda e: actions.reload())))
    for message in actions.general:
        if not actions.conflict:
            parts.append(_banner("error", message))
    if actions.restarts:
        parts.append(_banner("info", "Reinicie " + " e ".join(_WORDS.get(s, s) for s in actions.restarts) + " para que as alterações valham."))

    errors = flow.errors
    for field in section.fields:
        key = field.key
        parts.append(field_row(FieldProps(
            field=field, value=flow.shown(key), on_change=actions.change(key), error=errors.get(key), text=flow.typed(key),
            origin=flow.origin(key) if not read_only else None, locked=flow.is_locked(key) or read_only, env_var=flow.env_var(key),
            pending_restart=flow.pending(key), on_restore=(lambda k=key: actions.restore(k)) if not read_only else None,
            on_add=(lambda k=key: actions.add(k)), on_remove=(lambda i, k=key: actions.remove(k, i)))))

    if not read_only:
        parts.append(ft.Row(wrap=True, spacing=12, run_spacing=12, controls=[
            ft.FilledButton(content="Salvar", icon=ft.Icons.SAVE_OUTLINED, height=MIN_TARGET,
                            disabled=not (flow.dirty and flow.can_save), on_click=lambda e: actions.save()),
            ft.OutlinedButton(content="Descartar", height=MIN_TARGET, disabled=not flow.dirty, on_click=lambda e: actions.discard()),
            ft.TextButton(content="Restaurar todos os padrões", height=MIN_TARGET, on_click=lambda e: actions.restore_all())]))
    return ft.Container(max_width=MAX_WIDTH, content=ft.Column(spacing=20, controls=parts))


def confirm_dialog(confirm: Confirm, close: Callable[[], None]) -> ft.AlertDialog:
    def accept(e) -> None:
        confirm.action()
        close()

    return ft.AlertDialog(
        modal=True, title=confirm.title, semantics_label=confirm.title, content=ft.Text(confirm.body, size=16),
        actions=[ft.TextButton(content="Cancelar", height=MIN_TARGET, autofocus=True, on_click=lambda e: close()),
                 ft.FilledButton(content=confirm.confirm_label, height=MIN_TARGET, on_click=accept)],
        actions_alignment=ft.MainAxisAlignment.END)


# ---- the component --------------------------------------------------------------------------------------
@ft.component
def SectionForm(section: Section, make_flow: Callable[[Section], FormFlow], notify: Callable[[Notice], Any], note_restart: Callable[[str], Any],
                dirty_changed: Callable[[bool], Any] = lambda dirty: None):
    loaded, set_loaded = ft.use_state(False)
    _tick, set_tick = ft.use_state(0)
    asking, set_asking = ft.use_state(None)
    flow, _ = ft.use_state(lambda: make_flow(section))
    actions, _ = ft.use_state(lambda: FormActions(flow, notify, set_asking, lambda: set_tick(lambda t: t + 1), note_restart, dirty_changed))

    async def load() -> None:
        await asyncio.to_thread(flow.load)
        if flow.status == "unavailable":
            notify(Notice.bridge_down())
        set_loaded(True)

    ft.use_effect(load, [section.id], cleanup=lambda: dirty_changed(False))
    ft.use_dialog(confirm_dialog(asking, close=lambda: set_asking(None)) if asking else None)
    return form_view(flow, actions) if loaded else loading_view(section.title)
