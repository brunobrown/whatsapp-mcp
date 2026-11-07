"""Fixtures of the browser tests: a real interface process, a real browser, a private configuration directory."""
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

SERVER = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CAPTURES = os.environ.get("WHATSAPP_UI_E2E_CAPTURES")
PASSWORD = "correct horse battery"

pytestmark = pytest.mark.skipif(os.environ.get("WHATSAPP_UI_E2E") != "1", reason="browser tests run only with WHATSAPP_UI_E2E=1")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("WHATSAPP_UI_E2E") != "1":
        skip = pytest.mark.skip(reason="browser tests run only with WHATSAPP_UI_E2E=1")
        for item in items:
            if "e2e" in str(item.fspath):
                item.add_marker(skip)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def ui(tmp_path_factory):
    """The interface as `python -m ui` runs it: (url, configuration directory, the setup code from its log)."""
    config = tmp_path_factory.mktemp("e2e-config")
    port = free_port()
    env = {**os.environ, "WHATSAPP_CONFIG_DIR": str(config), "WHATSAPP_UI_PORT": str(port), "FLET_LOG_LEVEL": "info"}
    for name in [k for k in env if k.startswith(("FLET_", "WHATSAPP_UI_ALLOW", "WHATSAPP_MCP_MODE", "WHATSAPP_SESSION_TAINT"))]:
        env.pop(name)
    log = open(config / "ui.log", "w+")
    proc = subprocess.Popen([sys.executable, "-u", "-m", "ui"], cwd=SERVER, env=env, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(60):
        try:
            if urllib.request.urlopen(url, timeout=1).status == 200:
                break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        log.seek(0)
        pytest.fail("the interface did not start:\n" + log.read())
    time.sleep(0.5)
    log.seek(0)
    code = re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", log.read())
    yield SimpleNamespace(url=url, config=config, code=code.group(0) if code else None, log=log)
    proc.terminate()
    proc.wait(10)


from types import SimpleNamespace  # noqa: E402


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        flags = ["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"]       # a headless Chromium in CI or a container needs these
        try:
            b = p.chromium.launch(args=flags)
        except Exception:
            import glob
            candidates = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
            if not candidates:
                raise
            b = p.chromium.launch(executable_path=candidates[-1], args=flags)
        yield b
        b.close()


_OPEN = []


@pytest.fixture(autouse=True)
def close_what_a_test_opened():
    """Every page of a test is closed when it ends: each one is a whole Flutter engine, and they add up."""
    yield
    while _OPEN:
        try:
            _OPEN.pop().close()
        except Exception:
            pass


def open_page(browser, ui, width=1280, height=900):
    while _OPEN:                                   # one page at a time, also inside a test that changes the width
        try:
            _OPEN.pop().close()
        except Exception:
            pass
    context = browser.new_context(viewport={"width": width, "height": height})
    _OPEN.append(context)
    page = context.new_page()
    for _ in range(5):                                   # the accessibility tree is switched on by a click on a placeholder that may not exist yet
        try:
            page.goto(ui.url, wait_until="commit", timeout=20000)
            page.wait_for_selector("flt-semantics-placeholder, flt-glass-pane", state="attached", timeout=30000)
            page.evaluate("document.querySelector('flt-semantics-placeholder')?.click()")
            page.wait_for_timeout(800)
            page.wait_for_selector("input[aria-label]", state="attached", timeout=20000)      # the first screen's fields exist: callers can tell sign-up from sign-in
            break
        except Exception:
            continue
    else:
        raise AssertionError("the first screen never exposed a field")
    return page


def survives_a_browser_crash(test):
    """A software-rendered Chromium sometimes dies under load ("Target crashed"): that is the browser, not the interface, so the test runs once more.
    Any other failure, and a second crash, fail the test as they are."""
    import functools

    @functools.wraps(test)
    def run(*args, **kwargs):
        try:
            return test(*args, **kwargs)
        except Exception as error:
            if "crashed" not in str(error):
                raise
            while _OPEN:
                try:
                    _OPEN.pop().close()
                except Exception:
                    pass
            return test(*args, **kwargs)
    return run
