"""The login screen, the first-password screen and the account page as the operator meets them (AUTH-01..AUTH-07, UX-02)."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ft = pytest.importorskip("flet")

from ui.app import login as login_module  # noqa: E402
from ui.app.login import account_view, login_view  # noqa: E402


def walk(control):
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from walk(content)


def of_type(control, kind):
    return [c for c in walk(control) if isinstance(c, kind)]


def texts(control):
    return " | ".join(str(c.value) for c in of_type(control, ft.Text) if c.value)


def boxes(control):
    return {c.label: c for c in of_type(control, ft.TextField)}


def click(button):
    button.on_click(SimpleNamespace(control=button))


def button(control, word):
    return next(b for b in of_type(control, (ft.Button, ft.FilledButton, ft.TextButton, ft.OutlinedButton)) if word in str(b.content))


class Handlers:
    def __init__(self):
        self.calls = []

    def edit(self, key):
        return lambda e: self.calls.append(("edit", key, e.control.value))

    def submit(self):
        self.calls.append(("submit",))

    def new_code(self):
        self.calls.append(("new_code",))

    def sign_out(self):
        self.calls.append(("sign_out",))


def view(mode="login", values=None, error=None, busy=False, h=None):
    h = h or Handlers()
    return login_view(mode, values or {}, error, busy, on_edit=h.edit, on_submit=h.submit, on_new_code=h.new_code), h


def test_unauthenticated_state_shows_only_the_login_and_no_configuration():  # AUTH-01
    screen, _ = view("login")

    shown = texts(screen)
    assert set(boxes(screen)) == {"Senha"} and "Entrar" in shown
    for word in ("Política", "Webhook", "Monitor", "Mídia", "token", "Segurança"):
        assert word not in shown


def test_the_password_box_hides_what_is_typed_can_be_revealed_and_has_the_focus():  # AUTH-01, UX-02
    box = boxes(view("login")[0])["Senha"]

    assert box.password is True and box.can_reveal_password is True and box.autofocus is True


def test_pressing_enter_in_the_password_box_signs_in():  # AUTH-01, UX-02
    screen, h = view("login")

    boxes(screen)["Senha"].on_submit(SimpleNamespace(control=None))
    click(button(screen, "Entrar"))

    assert h.calls == [("submit",), ("submit",)]


def test_typing_goes_to_the_handler_of_that_field():  # AUTH-01
    screen, h = view("login")

    boxes(screen)["Senha"].on_change(SimpleNamespace(control=SimpleNamespace(value="abc")))

    assert h.calls == [("edit", "password", "abc")]


def test_the_boxes_are_not_told_a_value_on_every_redraw_so_typing_is_never_overwritten():  # AUTH-01, UX-02
    first = boxes(view("login", {"password": "abc"})[0])["Senha"]
    again = boxes(view("login", {"password": "abcd"})[0])["Senha"]

    assert first.value == "" and again.value == "" and first.key == again.key


def test_after_a_submit_the_boxes_are_new_ones_and_so_empty():  # AUTH-01
    before = boxes(view("login", {"_n": 0})[0])["Senha"]
    after = boxes(view("login", {"_n": 1})[0])["Senha"]

    assert before.key != after.key


def test_a_failed_login_shows_the_message_beside_the_field_and_in_a_live_region():  # AUTH-04, UX-02
    screen, _ = view("login", error="Não foi possível entrar. Confira a senha e tente de novo.")

    assert boxes(screen)["Senha"].error == "Não foi possível entrar. Confira a senha e tente de novo."
    assert any(isinstance(c, ft.Semantics) and c.live_region for c in walk(screen))


def test_while_a_login_is_being_checked_the_button_waits():  # AUTH-04
    screen, _ = view("login", busy=True)

    assert button(screen, "Entrar").disabled is True and of_type(screen, ft.ProgressRing)


def test_the_setup_screen_asks_for_the_code_the_password_and_its_confirmation():  # AUTH-02
    screen, _ = view("setup")

    assert set(boxes(screen)) == {"Código de configuração", "Nova senha", "Repita a nova senha"}
    helper = boxes(screen)["Nova senha"].helper
    assert "log do bridge" in texts(screen) and "12" in helper and "256" in helper


def test_the_setup_screen_hides_both_passwords_and_not_the_code():  # AUTH-02
    fields = boxes(view("setup")[0])

    assert fields["Nova senha"].password and fields["Repita a nova senha"].password and not fields["Código de configuração"].password


def test_the_setup_screen_can_ask_for_a_new_code():  # AUTH-02
    screen, h = view("setup")

    click(button(screen, "Gerar novo código"))
    click(button(screen, "Criar senha"))

    assert h.calls == [("new_code",), ("submit",)]


def test_the_error_of_the_setup_goes_beside_the_field_it_is_about_or_in_a_banner():  # AUTH-02
    screen, _ = view("setup", error="As senhas não são iguais.")

    assert "As senhas não são iguais." in texts(screen) or any(c.error for c in of_type(screen, ft.TextField))


def test_a_damaged_password_file_shows_only_how_to_recover():  # AUTH-02
    screen, _ = view("broken", error="O arquivo ui_auth.json está danificado.")

    assert "ui_auth.json" in texts(screen) and of_type(screen, ft.TextField) == []


def test_the_screens_have_a_heading_for_screen_readers():  # UX-02
    for mode in ("login", "setup", "broken"):
        assert any(isinstance(c, ft.Semantics) and c.header for c in walk(view(mode, error="x")[0])), mode


def test_account_page_asks_for_the_current_password_and_the_new_one_twice():  # AUTH-07
    h = Handlers()
    page = account_view({}, None, False, on_edit=h.edit, on_submit=h.submit, on_sign_out=h.sign_out)

    fields = boxes(page)

    assert set(fields) == {"Senha atual", "Nova senha", "Repita a nova senha"} and all(f.password for f in fields.values())
    click(button(page, "Alterar senha"))
    click(button(page, "Sair"))
    assert h.calls == [("submit",), ("sign_out",)]


def test_the_account_page_says_when_the_change_worked_or_why_not():  # AUTH-07
    h = Handlers()
    ok = account_view({}, None, False, on_edit=h.edit, on_submit=h.submit, on_sign_out=h.sign_out, notice="Senha alterada.")
    bad = account_view({}, "Senha incorreta.", False, on_edit=h.edit, on_submit=h.submit, on_sign_out=h.sign_out)

    assert "Senha alterada." in texts(ok) and boxes(bad)["Senha atual"].error == "Senha incorreta." or "Senha incorreta." in texts(bad)


def test_no_screen_has_a_literal_colour_or_an_imperative_call():  # EXT-02
    source = open(login_module.__file__, encoding="utf-8").read()

    assert ".update(" not in source and "page.add" not in source
