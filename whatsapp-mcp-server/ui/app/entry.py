"""The root of the interface: what one browser session is made of, how a page is chosen, how an idle session ends.

main(page) is the one place that sets up the page (theme, fonts, the notification service) and renders App, as the
documentation of Flet shows; from there everything is a component. What is shared by every session (the password, the
throttle of guesses, the stores) is built once in build_services.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import flet as ft

from ui.app import theme
from ui.app.form import SectionForm
from ui.app.login import AccountPage, LoginScreen
from ui.app.notifier import Notice, Notifier
from ui.app.ops_pages import ActivityPage, StatusPage
from ui.app.shell import NavEntry, Shell, nav_entries
from ui.app.state import AppState
from ui.core.auth import AuthStore
from ui.core.envlayer import EnvLayer
from ui.core.formflow import FormFlow
from ui.core.loginflow import LoginFlow
from ui.core.registry import Section, all_sections
from ui.core.store import ConfigStore
from ui.core.throttle import DEFAULT_IDLE_SECONDS, IdleClock, Throttle

IDLE_CHECK_SECONDS = 15
IDLE_WARN_SECONDS = 120
TOAST_SECONDS = 6
PAGES = [NavEntry("status", "Status", "monitor_heart", 0), NavEntry("activity", "Rascunhos e auditoria", "history", 90),
         NavEntry("account", "Conta", "person", 100)]


# ---- what the whole process shares ----------------------------------------------------------------------
@dataclass
class Services:
    auth: AuthStore
    throttle: Throttle
    store: ConfigStore
    env: EnvLayer
    idle_seconds: float = DEFAULT_IDLE_SECONDS


def build_services(startup_environ: Optional[Dict[str, str]] = None, idle_seconds: float = DEFAULT_IDLE_SECONDS) -> Services:
    store = ConfigStore()
    return Services(AuthStore(store), Throttle(), store, EnvLayer(environ=startup_environ if startup_environ is not None else None, store=store),
                    idle_seconds)


def announce_setup_code(services: Services, out: Callable[[str], Any] = lambda line: print(line, flush=True)) -> None:
    """Write the code that sets the first password in the log, where only whoever can read the log sees it."""
    code = services.auth.start_setup()
    if code:
        out(f"ui: código de configuração da interface: {code} (vale por 15 minutos e funciona uma vez; use-o para criar a senha)")


# ---- what the session shows -----------------------------------------------------------------------------
@ft.observable
@dataclass
class Toast:
    notice: Optional[Notice] = None


@dataclass
class Session:
    state: AppState
    toast: Toast
    entries: List[NavEntry]
    notify: Callable[[Notice], Any]
    make_login_flow: Callable[[], LoginFlow]
    make_form_flow: Callable[[Section], FormFlow]
    watcher: "IdleWatcher" = None


class IdleWatcher:
    """Looks at the idle clock now and then: warns once before the session lapses, ends it when it has."""

    def __init__(self, state: AppState, notify: Callable[[Notice], Any], interval: float = IDLE_CHECK_SECONDS,
                 sleep: Callable[[float], Any] = asyncio.sleep, warn_before: float = IDLE_WARN_SECONDS):
        self._state, self._notify, self._interval, self._sleep, self._warn_before = state, notify, interval, sleep, warn_before
        self._stopped, self._warned = False, False

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        while not self._stopped:
            await self._sleep(self._interval)
            if self._stopped:
                return
            if not self._state.authenticated:
                self._warned = False
                continue
            if self._state.check_idle():
                self._warned = False
                continue
            if not self._warned and self._state.idle.remaining <= self._warn_before:
                self._warned = True
                self._notify(Notice.session_ending())
            elif self._state.idle.remaining > self._warn_before:
                self._warned = False


def session_for(page: Any, services: Services) -> Session:
    state = AppState(idle=IdleClock(services.idle_seconds))
    toast = Toast()
    notifier = Notifier()
    notifications = next((s for s in getattr(page, "services", []) if type(s).__name__ == "Notifications"), None)

    def show(notice: Notice) -> None:
        toast.notice = notice

    async def deliver(notice: Notice) -> None:
        await notifier.deliver(notice, notifications, show)
        if toast.notice is notice:
            await asyncio.sleep(TOAST_SECONDS + 0.5)
            if toast.notice is notice:
                toast.notice = None

    def notify(notice: Notice) -> None:
        if state.authenticated or notice.key == "session_ending":
            page.run_task(deliver, notice)

    return Session(
        state=state, toast=toast, entries=nav_entries(all_sections(), PAGES), notify=notify,
        make_login_flow=lambda: LoginFlow(services.auth, services.throttle, state, getattr(page, "client_ip", None),
                                          announce=lambda code: print(f"ui: código de configuração da interface: {code} (vale por 15 minutos)", flush=True)),
        make_form_flow=lambda section: FormFlow(section, services.store, services.env))


# ---- choosing the page ----------------------------------------------------------------------------------
def route(session: Session, page_id: str) -> ft.Control:
    if page_id == "status":
        return StatusPage()
    if page_id == "activity":
        return ActivityPage()
    if page_id == "account":
        return AccountPage(session.make_login_flow, on_sign_out=session.state.sign_out, key="account")
    section = next((s for s in all_sections() if s.id == page_id), None)
    if section is None:
        return ft.Text("Esta página não existe.", size=16)
    return SectionForm(section, session.make_form_flow, session.notify, session.state.note_restart, session.state.set_dirty, key=section.id)


@ft.component
def ToastHost(toast: Toast):
    notice = toast.notice
    ft.use_dialog(ft.SnackBar(content=ft.Text(f"{notice.title}. {notice.body}", size=16), show_close_icon=True, duration=ft.Duration(seconds=TOAST_SECONDS))
                  if notice else None)
    return ft.Container(width=0, height=0)


@ft.component
def App(session: Session, state: AppState):
    """state is passed on its own as well, because a component only listens to the observables it is given directly."""
    watcher = ft.use_ref(None)

    def start() -> None:
        watcher.current = IdleWatcher(state, session.notify)
        ft.context.page.run_task(watcher.current.run)

    ft.use_effect(start, [], cleanup=lambda: watcher.current.stop() if watcher.current else None)
    body = (Shell(session.entries, lambda page_id: route(session, page_id), state, on_sign_out=state.sign_out) if state.authenticated
            else LoginScreen(session.make_login_flow, notice=state.ended))
    # a Column, not a Stack: the body expands, and an expanded child is only valid inside a Row or a Column
    return ft.Column(expand=True, spacing=0, controls=[ft.Container(expand=True, content=body), ToastHost(session.toast)])


# ---- the program ------------------------------------------------------------------------------------------
def main(page: ft.Page, services: Services) -> None:
    """One browser session: configure the page once, then render. Everything after this is a component."""
    theme.configure(page)
    try:
        import flet_notifications as ftn      # noqa: PLC0415  (an optional dependency)
        page.services.append(ftn.Notifications())
    except ImportError:
        pass
    session = session_for(page, services)
    page.render(App, session, session.state)


def serve(settings, assets_dir: Optional[str] = None) -> None:
    from ui.launcher import ASSETS_DIR      # noqa: PLC0415
    services = build_services(settings.startup_environ)
    announce_setup_code(services)
    ft.run(lambda page: main(page, services), host=settings.host, port=settings.port, view=ft.AppView.WEB_BROWSER,
           assets_dir=assets_dir or ASSETS_DIR)
