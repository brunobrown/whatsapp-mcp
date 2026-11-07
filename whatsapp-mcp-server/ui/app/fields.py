"""The controls for one field, and the rule that turns what a person typed into a value.

Everything here is a function of its props: the value, the error, where it came from, whether it is locked. A control
emits what it was given by the person (text, a boolean, a key, a list) and the form decides what it means; nothing
here reads a file, changes a control that exists, or knows a business rule.
"""
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

import flet as ft

from ui.core.registry import Field
from ui.core.secrets import CLEAR

MIN_TARGET = 48           # px: the least a touch target may measure
_TEXT_KINDS = ("text", "int", "float", "list", "secret")
_INTEGER = re.compile(r"[+-]?\d+")


# ---- what a person typed ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Parsed:
    value: Any
    error: Optional[str] = None
    text: Optional[str] = None      # what was typed, kept so the screen can show it back when it is not a value yet


def from_raw(field: Field, raw: Any) -> Parsed:
    """The value a control's raw output stands for, or why it is not one."""
    if field.kind not in _TEXT_KINDS:
        return Parsed(raw)
    text = raw if isinstance(raw, str) else ""
    if field.kind == "list":
        return Parsed([line.strip() for line in text.splitlines() if line.strip()])
    stripped = text if field.kind == "secret" else text.strip()
    if stripped == "":
        return Parsed(None)
    if field.kind == "int":
        return Parsed(int(stripped)) if _INTEGER.fullmatch(stripped) else Parsed(None, "deve ser um número inteiro", text)
    if field.kind == "float":
        try:
            number = float(stripped.replace(",", "."))
        except ValueError:
            return Parsed(None, "deve ser um número", text)
        return Parsed(number) if math.isfinite(number) else Parsed(None, "deve ser um número", text)
    return Parsed(stripped)


def to_raw(field: Field, value: Any) -> str:
    """The text that shows a value in a text box."""
    if value is None or isinstance(value, dict):
        return ""
    if field.kind == "list":
        return "\n".join(value) if isinstance(value, list) else str(value)
    if field.kind == "float" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:g}"
    return str(value)


# ---- the props of a field ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldProps:
    field: Field
    value: Any
    on_change: Callable[[Any], None]
    error: Optional[str] = None
    text: Optional[str] = None                    # what is being typed, when it is not a value yet
    origin: Optional[str] = None                  # "default", "file" or "env"
    locked: bool = False
    env_var: Optional[str] = None
    pending_restart: Optional[str] = None         # "bridge" or "server"
    on_restore: Optional[Callable[[], None]] = None
    on_add: Optional[Callable[[], None]] = None   # records
    on_remove: Optional[Callable[[int], None]] = None


_RESTART_WORDS = {"bridge": "o bridge", "server": "o servidor MCP"}
_ORIGIN_WORDS = {"default": "Padrão", "file": "Arquivo"}


def _note(value: str, colour=ft.Colors.ON_SURFACE_VARIANT, semantics: Optional[str] = None) -> ft.Text:
    return ft.Text(value, size=14, color=colour, selectable=True, semantics_label=semantics)


def _raw_text(p: FieldProps) -> str:
    if p.text is not None:
        return p.text
    return p.value if isinstance(p.value, str) and p.field.kind == "secret" else to_raw(p.field, p.value)


# ---- one control per kind -----------------------------------------------------------------------------
def _text_box(p: FieldProps) -> ft.TextField:
    f = p.field
    numeric = f.kind in ("int", "float")
    return ft.TextField(
        label=f.label, value=_raw_text(p), helper=f.help or None, error=p.error, disabled=p.locked,
        multiline=f.kind == "list", min_lines=3 if f.kind == "list" else None, max_lines=8 if f.kind == "list" else None,
        keyboard_type=ft.KeyboardType.NUMBER if numeric else (ft.KeyboardType.MULTILINE if f.kind == "list" else ft.KeyboardType.TEXT),
        max_length=None, on_change=lambda e: p.on_change(e.control.value))


def _secret_control(p: FieldProps) -> ft.Control:
    f = p.field
    is_set = isinstance(p.value, dict) and bool(p.value.get("set"))
    typing = p.value if isinstance(p.value, str) else ""
    box = ft.TextField(
        label=f.label, value=typing, password=True, can_reveal_password=True, disabled=p.locked, error=p.error,
        hint_text="Definido. Deixe em branco para manter." if is_set else "Não definido.", helper=f.help or None,
        on_change=lambda e: p.on_change(e.control.value))
    controls: List[ft.Control] = [box]
    if is_set and not p.locked:
        controls.append(ft.TextButton(content="Remover o valor", tooltip=f"Remover o valor definido de {f.label}", height=MIN_TARGET,
                                      on_click=lambda e: p.on_change(CLEAR)))
    return ft.Column(spacing=2, controls=controls)


def _switch(p: FieldProps) -> ft.Control:
    return ft.Container(min_height=MIN_TARGET, alignment=ft.Alignment.CENTER_LEFT, content=ft.Switch(
        label=p.field.label, value=bool(p.value), disabled=p.locked, on_change=lambda e: p.on_change(bool(e.control.value))))


def _dropdown(p: FieldProps) -> ft.Dropdown:
    f = p.field
    return ft.Dropdown(label=f.label, value=p.value if p.value in f.options else None, disabled=p.locked, error_text=p.error,
                       helper_text=f.help or None, options=[ft.DropdownOption(key=o, text=o) for o in f.options],
                       on_select=lambda e: p.on_change(e.control.value))


def _checks(p: FieldProps) -> ft.Control:
    f = p.field
    chosen = list(p.value or [])

    def toggled(option: str):
        def handler(e):
            on = bool(e.control.value)
            p.on_change([o for o in f.options if (o == option and on) or (o != option and o in chosen)])
        return handler
    boxes = [ft.Checkbox(label=o, value=o in chosen, disabled=p.locked, on_change=toggled(o)) for o in f.options]
    body = [ft.Text(f.label, size=16, weight=ft.FontWeight.W_600), *boxes]
    if f.help:
        body.insert(1, _note(f.help))
    if p.error:
        body.append(_note(p.error, ft.Colors.ERROR))
    return ft.Semantics(label=f.label, container=True, content=ft.Column(spacing=0, controls=body))


def field_control(p: FieldProps) -> ft.Control:
    kind = p.field.kind
    if kind == "secret":
        return _secret_control(p)
    if kind == "bool":
        return _switch(p)
    if kind == "choice":
        return _dropdown(p)
    if kind == "multichoice":
        return _checks(p)
    if kind == "records":
        return records_control(p)
    return _text_box(p)


# ---- the row: the control and what to know about it ---------------------------------------------------
def field_row(p: FieldProps) -> ft.Control:
    f = p.field
    notes: List[ft.Control] = []
    if p.origin:
        origin = f"Variável de ambiente {p.env_var}" if p.origin == "env" else _ORIGIN_WORDS.get(p.origin, p.origin)
        notes.append(_note(origin))
    if p.pending_restart:
        notes.append(_note(f"Reinicie {_RESTART_WORDS.get(p.pending_restart, p.pending_restart)} para aplicar.", ft.Colors.TERTIARY))
    if p.on_restore and p.origin == "file" and not p.locked:
        notes.append(ft.TextButton(content="Restaurar padrão", tooltip=f"Restaurar o padrão de {f.label}", height=MIN_TARGET,
                                   on_click=lambda e: p.on_restore()))
    parts: List[ft.Control] = [field_control(p)]
    if notes:
        parts.append(ft.Row(wrap=True, spacing=12, run_spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=notes))
    if p.locked and p.origin == "env":
        parts.append(_note(f"Definido pela variável de ambiente {p.env_var}. Remova-a do ambiente do processo para editar aqui."))
    elif p.locked:
        parts.append(_note("Somente leitura."))
    if f.risky(p.value):
        parts.append(ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            ft.Icon(ft.Icons.WARNING_AMBER, color=ft.Colors.TERTIARY, size=20, semantics_label="Atenção"),
            ft.Text(f.risk, size=14, color=ft.Colors.TERTIARY, expand=True, selectable=True)]))
    return ft.Column(spacing=4, controls=parts)


# ---- records ------------------------------------------------------------------------------------------
def _with(item: dict, sub: Field, raw: Any) -> dict:
    if sub.secret:
        value = raw if raw is CLEAR or isinstance(raw, (str, dict)) else item.get(sub.key)
    else:
        parsed = from_raw(sub, raw)
        value = parsed.text if parsed.error else parsed.value       # an unreadable number stays as typed, and is flagged by Field.check
    return {**item, sub.key: value}


def records_control(p: FieldProps) -> ft.Control:
    f = p.field
    items = list(p.value or [])

    def edit(index: int, sub: Field):
        def handler(raw):
            changed = list(items)
            changed[index] = _with(items[index], sub, raw)
            p.on_change(changed)
        return handler

    cards: List[ft.Control] = []
    for index, item in enumerate(items):
        rows = [field_row(FieldProps(field=sub, value=item.get(sub.key), on_change=edit(index, sub), locked=p.locked)) for sub in f.fields]
        cards.append(ft.Container(padding=12, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=6, content=ft.Column(spacing=8, controls=[
            ft.Row(alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.Text(f"{f.label} {index + 1}", size=16, weight=ft.FontWeight.W_600),
                ft.TextButton(content="Remover", tooltip=f"Remover {f.label} {index + 1}", height=MIN_TARGET, disabled=p.locked,
                              on_click=(lambda e, i=index: p.on_remove(i)) if p.on_remove else None)]),
            *rows])))
    header: List[ft.Control] = [ft.Text(f.label, size=19, weight=ft.FontWeight.W_600)]
    if f.help:
        header.append(_note(f.help))
    body: List[ft.Control] = [*header, *cards] if cards else [*header, _note("Nenhum item. Use Adicionar para criar o primeiro.")]
    if p.error:
        body.append(_note(p.error, ft.Colors.ERROR))
    body.append(ft.Button(content=f"Adicionar {f.label.lower()}", icon=ft.Icons.ADD, height=MIN_TARGET, disabled=p.locked,
                          on_click=(lambda e: p.on_add()) if p.on_add else None))
    return ft.Column(spacing=12, controls=body)
