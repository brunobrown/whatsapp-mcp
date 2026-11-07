"""The login screen, the first-password screen and the account page.

The views are functions of what the flow reports. The components keep what is being typed in memory only while it
is typed (a submitted password is cleared at once) and run the check off the event loop, because a password hash
is meant to take a moment.
"""
import asyncio
from typing import Any, Callable, Dict, Optional

import flet as ft

from ui.app.fields import MIN_TARGET
from ui.core.auth import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from ui.core.loginflow import LoginFlow

_RULE = f"De {MIN_PASSWORD_LENGTH} a {MAX_PASSWORD_LENGTH} caracteres."
Editor = Callable[[str], Callable[[Any], None]]


def _box(label: str, key: str, values: Dict[str, str], on_edit: Editor, *, secret: bool = True, error: Optional[str] = None,
         helper: Optional[str] = None, focus: bool = False, on_enter: Optional[Callable[[], Any]] = None) -> ft.TextField:
    return ft.TextField(label=label, value=values.get(key, ""), password=secret, can_reveal_password=secret, autofocus=focus, error=error,
                        helper=helper, on_change=on_edit(key), on_submit=(lambda e: on_enter()) if on_enter else None)


def _heading(text: str) -> ft.Control:
    return ft.Semantics(header=True, heading_level=1, content=ft.Text(text, size=28, weight=ft.FontWeight.W_700))


def _live(text: Optional[str], colour=ft.Colors.ERROR) -> ft.Control:
    return ft.Semantics(live_region=True, container=True, content=ft.Text(text or "", size=16, color=colour, selectable=True, visible=bool(text)))


def _card(controls) -> ft.Control:
    return ft.Container(max_width=440, padding=24, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=8,
                        content=ft.Column(spacing=16, controls=list(controls)))


def login_view(mode: str, values: Dict[str, str], error: Optional[str], busy: bool, on_edit: Editor, on_submit: Callable[[], Any],
               on_new_code: Callable[[], Any], notice: Optional[str] = None) -> ft.Control:
    if mode == "broken":
        body = [_heading("Senha indisponível"), ft.Text(error or "", size=16, selectable=True)]
    elif mode == "setup":
        body = [
            _heading("Criar a senha"),
            ft.Text("Esta é a primeira vez. Para criar a senha, use o código escrito no log do bridge: no terminal onde ele roda ou em "
                    "store/bridge.log. O código vale por 15 minutos e só funciona uma vez.", size=16),
            _box("Código de configuração", "code", values, on_edit, secret=False, focus=True),
            _box("Nova senha", "password", values, on_edit, helper=_RULE),
            _box("Repita a nova senha", "confirm", values, on_edit, error=error, on_enter=on_submit),
            ft.Row(wrap=True, spacing=12, run_spacing=8, controls=[
                ft.FilledButton(content="Criar senha", height=MIN_TARGET, disabled=busy, on_click=lambda e: on_submit()),
                ft.TextButton(content="Gerar novo código", height=MIN_TARGET, disabled=busy, on_click=lambda e: on_new_code())]),
        ]
    else:
        body = [
            _heading("Entrar"),
            _box("Senha", "password", values, on_edit, error=error, focus=True, on_enter=on_submit),
            ft.Row(spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[
                ft.FilledButton(content="Entrar", height=MIN_TARGET, disabled=busy, on_click=lambda e: on_submit()),
                *([ft.ProgressRing(width=24, height=24, stroke_width=3)] if busy else [])]),
        ]
    if notice:
        body.insert(1, _live(notice, ft.Colors.TERTIARY))
    if mode != "broken":
        body.append(_live(error if mode == "setup" else None))
    elif error is None:
        body.append(_live(None))
    return ft.Container(expand=True, alignment=ft.Alignment.CENTER, padding=16, content=_card(body))


def account_view(values: Dict[str, str], error: Optional[str], busy: bool, on_edit: Editor, on_submit: Callable[[], Any],
                 on_sign_out: Callable[[], Any], notice: Optional[str] = None) -> ft.Control:
    return ft.Container(max_width=520, content=ft.Column(spacing=16, controls=[
        _heading("Conta"),
        ft.Text("Troque a senha da interface. É preciso informar a atual.", size=16),
        _box("Senha atual", "current", values, on_edit, error=error),
        _box("Nova senha", "password", values, on_edit, helper=_RULE),
        _box("Repita a nova senha", "confirm", values, on_edit, on_enter=on_submit),
        _live(notice, ft.Colors.PRIMARY),
        ft.Row(wrap=True, spacing=12, run_spacing=8, controls=[
            ft.FilledButton(content="Alterar senha", height=MIN_TARGET, disabled=busy, on_click=lambda e: on_submit()),
            ft.OutlinedButton(content="Sair", icon=ft.Icons.LOGOUT, height=MIN_TARGET, on_click=lambda e: on_sign_out())]),
    ]))


@ft.component
def LoginScreen(make_flow: Callable[[], LoginFlow], notice: Optional[str] = None):
    flow, _ = ft.use_state(lambda: make_flow())
    values, set_values = ft.use_state({})
    error, set_error = ft.use_state(None)
    busy, set_busy = ft.use_state(False)
    mode, set_mode = ft.use_state(lambda: flow.mode())

    def edit(key: str):
        return lambda e: set_values(lambda current: {**current, key: e.control.value})

    async def submit() -> None:
        typed = dict(values)
        set_busy(True)
        try:
            if mode == "setup":
                result = await asyncio.to_thread(flow.setup, typed.get("code", ""), typed.get("password", ""), typed.get("confirm", ""))
            else:
                result = await asyncio.to_thread(flow.login, typed.get("password", ""))
        finally:
            set_busy(False)
        set_values({})                                       # a submitted password does not stay in memory
        set_error(None if result.ok else result.message)
        set_mode(flow.mode())

    def new_code() -> None:
        result = flow.request_code()
        set_error(None if result.announced else result.message)

    return login_view(mode, values, error, busy, edit, lambda: ft.context.page.run_task(submit), new_code, notice)


@ft.component
def AccountPage(make_flow: Callable[[], LoginFlow], on_sign_out: Callable[[], Any]):
    flow, _ = ft.use_state(lambda: make_flow())
    values, set_values = ft.use_state({})
    error, set_error = ft.use_state(None)
    notice, set_notice = ft.use_state(None)
    busy, set_busy = ft.use_state(False)

    def edit(key: str):
        return lambda e: set_values(lambda current: {**current, key: e.control.value})

    async def submit() -> None:
        typed = dict(values)
        set_busy(True)
        try:
            result = await asyncio.to_thread(flow.change_password, typed.get("current", ""), typed.get("password", ""), typed.get("confirm", ""))
        finally:
            set_busy(False)
        set_values({})
        set_error(None if result.ok else result.message)
        set_notice("Senha alterada." if result.ok else None)

    return account_view(values, error, busy, edit, lambda: ft.context.page.run_task(submit), on_sign_out, notice)
