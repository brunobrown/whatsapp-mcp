"""The root of the interface: what one session is made of, how a page is chosen, how an idle session ends (AUTH-05, EXT-01, EXT-02)."""
import ast
import asyncio
import glob
import os
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ft = pytest.importorskip("flet")

from flet.components.component import Renderer  # noqa: E402

from ui.app import entry  # noqa: E402
from ui.app.notifier import Notice  # noqa: E402
from ui.app.state import AppState  # noqa: E402
from ui.core.registry import all_sections  # noqa: E402
from ui.core.throttle import IdleClock  # noqa: E402

SERVER = os.path.dirname(os.path.dirname(__file__))


def fake_page(ip="127.0.0.1"):
    page = SimpleNamespace(services=[], client_ip=ip, rendered=[], tasks=[])
    page.render = lambda component, *args, **kwargs: page.rendered.append((component, args, kwargs))
    page.run_task = lambda fn, *args: page.tasks.append((fn, args))
    return page


def services(environ=None):
    return entry.build_services(startup_environ={} if environ is None else environ)


# ---- the process ----

def test_the_services_tell_the_environment_from_the_file_by_the_startup_environment(tmp_path):  # CFG-04
    sv = services({"WHATSAPP_TIMEZONE": "UTC"})

    assert sv.env.origin("WHATSAPP_TIMEZONE") == "env" and sv.env.origin("WHATSAPP_MCP_MODE") == "default"


def test_the_setup_code_is_written_to_the_log_only_while_there_is_no_password():  # AUTH-02
    sv = services()
    lines = []

    entry.announce_setup_code(sv, lines.append)

    assert len(lines) == 1 and re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", lines[0]) and "15 minutos" in lines[0]
    sv.auth.create(re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", lines[0]).group(0), "correct horse battery")
    again = []
    entry.announce_setup_code(sv, again.append)
    assert again == []


def test_the_code_announced_is_the_one_that_works_in_any_session():  # AUTH-02
    sv = services()
    lines = []
    entry.announce_setup_code(sv, lines.append)
    code = re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", lines[0]).group(0)

    flow = entry.session_for(fake_page("10.0.0.5"), sv).make_login_flow()

    assert flow.setup(code, "correct horse battery", "correct horse battery").ok


# ---- one session ----

def test_a_session_starts_signed_out_with_the_theme_and_the_notification_service():  # AUTH-01
    page = fake_page()

    entry.main(page, services())

    assert page.title == "WhatsApp MCP" and page.theme is not None and page.dark_theme is not None
    assert len(page.rendered) == 1 and page.rendered[0][0] is entry.App
    session = page.rendered[0][1][0]
    assert session.state.authenticated is False and dict(session.state.data) == {}
    assert any(type(s).__name__ == "Notifications" for s in page.services)


def test_each_session_has_its_own_state_but_shares_the_password_and_the_throttle():  # AUTH-04
    sv = services()
    one, two = fake_page(), fake_page()

    entry.main(one, sv)
    entry.main(two, sv)

    a, b = one.rendered[0][1][0], two.rendered[0][1][0]
    assert a.state is not b.state and a.make_login_flow()._auth is b.make_login_flow()._auth is sv.auth
    assert a.make_login_flow()._throttle is sv.throttle


def test_the_login_flow_counts_attempts_by_the_address_of_the_page():  # AUTH-04
    flow = entry.session_for(fake_page("203.0.113.7"), services()).make_login_flow()

    assert flow._ip == "203.0.113.7"


def test_the_idle_limit_of_a_session_is_thirty_minutes_by_default():  # AUTH-05
    assert entry.session_for(fake_page(), services()).state.idle.limit == 30 * 60


# ---- choosing a page ----

def render(session, page_id):
    with Renderer().with_context():
        return entry.route(session, page_id)


def test_each_fixed_page_is_a_component_of_its_own():  # EXT-03
    session = entry.session_for(fake_page(), services())
    session.state.sign_in()

    names = {pid: render(session, pid).fn.__name__ for pid in ("status", "activity", "account")}

    assert names == {"status": "StatusPage", "activity": "ActivityPage", "account": "AccountPage"}


def test_a_registered_section_gets_a_form_keyed_by_its_id():  # EXT-01
    session = entry.session_for(fake_page(), services())
    session.state.sign_in()

    node = render(session, "send_policy")

    assert node.fn.__name__ == "SectionForm" and node.args[0].id == "send_policy" and node.key == "send_policy"


def test_every_registered_section_is_routed_to_a_form():  # EXT-01
    session = entry.session_for(fake_page(), services())
    session.state.sign_in()

    for section in all_sections():
        assert render(session, section.id).fn.__name__ == "SectionForm", section.id


def test_a_page_that_does_not_exist_says_so_instead_of_failing():  # EXT-01
    session = entry.session_for(fake_page(), services())
    session.state.sign_in()

    node = render(session, "nonexistent")

    assert isinstance(node, ft.Text) and "não existe" in node.value


def test_the_menu_is_the_fixed_pages_around_every_registered_section():  # EXT-01
    session = entry.session_for(fake_page(), services())

    ids = [e.id for e in session.entries]

    assert ids[0] == "status" and ids[-1] == "account" and "activity" in ids
    assert {s.id for s in all_sections()} <= set(ids)


# ---- an idle session ----

class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


def test_an_idle_session_is_warned_once_then_ended_and_says_why():  # AUTH-05
    async def scenario():
        clock = Clock()
        state = AppState(idle=IdleClock(300, clock=clock))
        state.sign_in()
        sent = []
        steps = [100, 100, 90, 20]            # 200 s left, 100 s (the warning), 10 s, then lapsed

        async def sleep(seconds):
            clock.now += steps.pop(0) if steps else 0
            await asyncio.sleep(0)

        watcher = entry.IdleWatcher(state, sent.append, interval=15, sleep=sleep, warn_before=120)
        task = asyncio.create_task(watcher.run())
        await asyncio.sleep(0.05)
        watcher.stop()
        await asyncio.wait_for(task, 1)
        return state, sent

    state, sent = asyncio.run(scenario())

    assert [n.key for n in sent].count("session_ending") == 1
    assert state.authenticated is False and "inatividade" in state.ended


def test_an_active_session_is_never_ended_by_the_watcher():  # AUTH-05
    async def scenario():
        clock = Clock()
        state = AppState(idle=IdleClock(300, clock=clock))
        state.sign_in()
        sent = []

        async def sleep(seconds):
            clock.now += 100
            state.touch()
            await asyncio.sleep(0)

        watcher = entry.IdleWatcher(state, sent.append, interval=15, sleep=sleep, warn_before=120)
        task = asyncio.create_task(watcher.run())
        await asyncio.sleep(0.05)
        watcher.stop()
        await asyncio.wait_for(task, 1)
        return state, sent

    state, sent = asyncio.run(scenario())

    assert state.authenticated is True


def test_the_watcher_does_nothing_while_nobody_is_signed_in():  # AUTH-05
    async def scenario():
        clock = Clock()
        state = AppState(idle=IdleClock(300, clock=clock))
        sent = []

        async def sleep(seconds):
            clock.now += 1000
            await asyncio.sleep(0)

        watcher = entry.IdleWatcher(state, sent.append, interval=15, sleep=sleep, warn_before=120)
        task = asyncio.create_task(watcher.run())
        await asyncio.sleep(0.02)
        watcher.stop()
        await asyncio.wait_for(task, 1)
        return state, sent

    state, sent = asyncio.run(scenario())

    assert sent == [] and state.ended is None


# ---- the program ----

def test_the_program_refuses_to_start_on_a_bad_port_without_starting_flet():  # UI-04
    done = subprocess.run([sys.executable, "-m", "ui"], cwd=SERVER, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "WHATSAPP_UI_PORT": "abc", "WHATSAPP_CONFIG_DIR": "/nonexistent"})

    assert done.returncode == 1 and "WHATSAPP_UI_PORT" in done.stderr


# ---- the layer rules ----

APP_FILES = sorted(glob.glob(os.path.join(SERVER, "ui", "app", "*.py")))
FORBIDDEN_IMPORTS = {"os", "json", "shutil", "tempfile", "subprocess", "sqlite3", "requests", "pathlib", "glob",
                     "send_policy", "monitor", "channel", "drafts", "taint", "whatsapp", "metrics", "localconfig", "dbcrypto"}
PAGE_SETTINGS = re.compile(r"\bpage\.(theme|dark_theme|fonts|title|services|theme_mode|on_resize|bgcolor)\b\s*(=|\.append)")


def test_app_layer_has_no_file_io_or_imperative_updates():  # EXT-02
    assert APP_FILES
    for path in APP_FILES:
        source = open(path, encoding="utf-8").read()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        name = os.path.basename(path)
        assert not (imported & FORBIDDEN_IMPORTS), f"{name} imports {imported & FORBIDDEN_IMPORTS}"
        assert not re.search(r"\.update\(|\bpage\.(add|update|clean|show_dialog)\b", source), name
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not (calls & {"open", "exec", "eval", "__import__"}), name


def test_the_page_is_configured_in_one_place_each():  # EXT-02
    allowed = {"theme.py", "entry.py", "shell.py"}
    for path in APP_FILES:
        name = os.path.basename(path)
        if PAGE_SETTINGS.search(open(path, encoding="utf-8").read()):
            assert name in allowed, f"{name} sets a property of the page"
