"""The interface in a real browser: login gate, first password, editing, three widths, the keyboard (AUTH-01, UX-01, UX-02)."""
import json
import os

import pytest

pytest.importorskip("playwright")

from tests.e2e.conftest import CAPTURES, PASSWORD, open_page  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("WHATSAPP_UI_E2E") != "1", reason="browser tests run only with WHATSAPP_UI_E2E=1")


def text_of(page):
    return page.evaluate("Array.from(document.querySelectorAll('flt-semantics')).map(e => (e.getAttribute('aria-label') || e.textContent || '')).join(' | ')")


def type_into(page, label, text, exact=False):
    """Type as a person does: Flutter listens to key events, not to a value set from outside. The field holds its text
    inside the engine, so whether it all arrived is judged by what the screen does next (see attempt)."""
    box = page.get_by_label(label, exact=exact)
    box.click()
    box.press("Control+A")
    box.press("Delete")
    box.press_sequentially(text, delay=60)
    page.wait_for_timeout(250)


def opened(page):
    """The signed-in interface: the rail's "Status" destination on a wide window, the menu button on a narrow one."""
    return page.get_by_role("button", name="Status Tab").or_(page.get_by_label("Abrir o menu")).first


def attempt(page, what, fill, button):
    """A software-rendered browser can drop keys: fill the form and press the button up to three times, until the interface opens."""
    for _ in range(3):
        fill()
        page.get_by_role("button", name=button).click()
        try:
            opened(page).wait_for(state="attached", timeout=15000)
            return
        except Exception:
            continue
    raise AssertionError(f"{what} did not open the interface; the screen says: " + text_of(page)[:500])


def sign_up(page, ui):
    def fill():
        type_into(page, "Código de configuração", ui.code)
        type_into(page, "Nova senha", PASSWORD, exact=True)
        type_into(page, "Repita a nova senha", PASSWORD)
    attempt(page, "creating the password", fill, "Criar senha")


def signed_in(browser, ui, width=1280, height=900):
    page = open_page(browser, ui, width, height)
    if page.get_by_label("Código de configuração").count():
        sign_up(page, ui)
    else:
        attempt(page, "signing in", lambda: type_into(page, "Senha", PASSWORD, exact=True), "Entrar")
    return page


def test_e2e_login_gate_shows_no_configuration(browser, ui):  # AUTH-01
    page = open_page(browser, ui)

    shown = text_of(page)

    assert "Criar a senha" in shown or "Entrar" in shown
    for word in ("Política de envio", "Webhooks", "Mídia", "Segurança do agente", "Status"):
        assert word not in shown, f"{word!r} is on screen before the password"


def test_e2e_the_first_password_is_created_with_the_code_from_the_log_and_opens_the_interface(browser, ui):  # AUTH-02
    assert ui.code, ui.log.read()
    page = open_page(browser, ui)

    sign_up(page, ui)
    shown = text_of(page)

    assert "Status" in shown and "Política de envio" in shown
    auth = json.loads((ui.config / "ui_auth.json").read_text())
    assert set(auth) == {"version", "scrypt", "salt", "hash"} and PASSWORD not in json.dumps(auth)


def test_e2e_a_value_edited_in_the_browser_is_saved_to_the_settings_file(browser, ui):  # CFG-04
    page = signed_in(browser, ui)

    page.get_by_role("button", name="Mídia Tab").click()                 # the rail exposes each destination as "<name> Tab n of N"
    field = page.get_by_role("textbox", name="Tamanho máximo baixado")
    field.wait_for(state="attached", timeout=20000)
    field.click(force=True)                                              # a neighbouring semantics node covers the field's box
    field.press("End")
    for _ in range(6):                                                   # select-all is not reliable in the engine's field: erase the old "25" by hand
        field.press("Backspace")
    field.press_sequentially("50", delay=60)
    for _ in range(20):                                                  # Flutter does not scroll a button into view for a click: reach it by Tab
        page.keyboard.press("Tab")
        page.wait_for_timeout(150)
        if "Salvar" in (page.evaluate("document.activeElement && (document.activeElement.getAttribute('aria-label') || document.activeElement.textContent || '')") or ""):
            break
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)

    saved = json.loads((ui.config / "ui_settings.json").read_text())
    assert saved["env"]["WHATSAPP_AUTO_DOWNLOAD_MAX_MB"] == "50"
    assert "Reinicie o bridge" in text_of(page)


def test_e2e_layout_adapts_without_horizontal_scroll(browser, ui):  # UX-01
    for width, height in ((360, 740), (768, 1024), (1280, 900)):
        page = signed_in(browser, ui, width, height)
        page.wait_for_timeout(500)

        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        shown = text_of(page)

        assert overflow <= 1, f"{width}px scrolls sideways by {overflow}px"
        if width < 600:
            assert "Abrir o menu" in shown
        else:
            assert "Navegação principal" in shown
        if CAPTURES:
            os.makedirs(CAPTURES, exist_ok=True)
            page.screenshot(path=os.path.join(CAPTURES, f"layout-{width}.png"))


def tab_until(page, label_part, limit=30):
    """Press Tab until the focused control is named like label_part; returns the names of every control passed on the way."""
    seen = []
    for _ in range(limit):
        page.keyboard.press("Tab")
        page.wait_for_timeout(150)
        name = page.evaluate("document.activeElement && (document.activeElement.tagName === 'INPUT' ? 'input:' + document.activeElement.value : "
                             "(document.activeElement.getAttribute('aria-label') || document.activeElement.textContent || ''))") or ""
        seen.append(name)
        if label_part in name:
            return seen
    raise AssertionError(f"Tab never reached {label_part!r}; it passed {seen}")


def test_e2e_every_control_is_keyboard_operable(browser, ui):  # UX-02
    signed_in(browser, ui)                                        # makes sure a password exists
    page = open_page(browser, ui)                                 # a new browser context: the login screen

    for _ in range(3):                                            # the password box takes focus on its own; a dropped key is retried
        page.wait_for_function("document.activeElement && document.activeElement.tagName === 'INPUT'", timeout=20000)
        page.keyboard.type(PASSWORD, delay=60)
        page.keyboard.press("Enter")
        try:
            opened(page).wait_for(state="attached", timeout=15000)
            break
        except Exception:
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
    assert "Política de envio" in text_of(page), "the keyboard did not sign in"

    tab_until(page, "Mídia")                                  # Tab reaches a destination of the rail ...
    page.keyboard.press("Enter")                                  # ... and Enter opens it
    page.get_by_role("textbox", name="Tamanho máximo baixado").wait_for(state="attached", timeout=20000)
    passed = tab_until(page, "Salvar") + tab_until(page, "Salvar")       # one full turn of the focus ring, wherever it started: every control shows up
    for control in ("Baixar mídia ao chegar", "image", "video", "audio", "document", "Guardar o conteúdo", "Gravar o texto", "Salvar"):
        assert any(control in name for name in passed), f"Tab skipped {control!r}; it passed {passed}"
    assert len({name for name in passed if name.startswith("input:")}) == 2, f"the two number boxes were not both reached: {passed}"
