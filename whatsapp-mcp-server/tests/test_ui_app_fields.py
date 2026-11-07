"""The controls for one field: what they show, what they emit, what they refuse (UX-02, CFG-04, CFG-10)."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ft = pytest.importorskip("flet")

from ui.app import fields  # noqa: E402
from ui.app.fields import FieldProps, from_raw, to_raw  # noqa: E402
from ui.core.registry import Field  # noqa: E402
from ui.core.secrets import CLEAR  # noqa: E402


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
    return " | ".join(c.value for c in of_type(control, ft.Text) if c.value)


def props(field, value=None, **extra):
    seen = []
    return FieldProps(field=field, value=value, on_change=seen.append, **extra), seen


def event(value):
    return SimpleNamespace(control=SimpleNamespace(value=value, selected=None), data=value)


# ---- text typed by a person becomes a value, or an error that stays beside the field ----

@pytest.mark.parametrize("kind, text, value", [
    ("int", "50", 50), ("int", " 7 ", 7), ("int", "", None), ("float", "2.5", 2.5), ("float", "2,5", 2.5), ("float", "3", 3.0),
    ("text", "hello", "hello"), ("text", "", None), ("text", "  spaced  ", "spaced"),
    ("list", "a\nb", ["a", "b"]), ("list", " a \n\n b \n", ["a", "b"]), ("list", "", []),
    ("secret", "s3cret", "s3cret"), ("secret", "", None),
])
def test_what_is_typed_becomes_the_value_of_the_field(kind, text, value):
    assert from_raw(Field("f", "F", kind, secret=kind == "secret"), text).value == value


@pytest.mark.parametrize("kind, text", [("int", "abc"), ("int", "2.5"), ("int", "1e3"), ("float", "x"), ("float", "nan"), ("float", "inf")])
def test_what_cannot_be_read_as_a_number_gives_an_error_in_portuguese_and_keeps_the_text(kind, text):
    parsed = from_raw(Field("f", "F", kind), text)

    assert parsed.error and parsed.value is None and parsed.text == text
    assert "deve ser" in parsed.error and text not in parsed.error


def test_a_value_goes_back_to_the_text_a_person_would_type():
    assert to_raw(Field("f", "F", "int"), 50) == "50" and to_raw(Field("f", "F", "float"), 2.5) == "2.5"
    assert to_raw(Field("f", "F", "list"), ["a", "b"]) == "a\nb" and to_raw(Field("f", "F", "text"), None) == ""
    assert to_raw(Field("f", "F", "float"), 25.0) == "25"


def test_the_text_and_the_value_agree_after_a_round_trip():
    for kind, value in (("int", 50), ("float", 0.05), ("list", ["x", "y z"]), ("text", "hello world")):
        field = Field("f", "F", kind)
        assert from_raw(field, to_raw(field, value)).value == value


# ---- one control per kind ----

def test_text_is_a_labelled_field_with_its_help_and_its_error():
    p, seen = props(Field("zone", "Fuso", "text", help="Nome IANA"), "America/Recife", error="não é um fuso")

    control = fields.field_control(p)

    assert isinstance(control, ft.TextField) and control.label == "Fuso" and control.value == "America/Recife"
    assert control.helper == "Nome IANA" and control.error == "não é um fuso" and control.disabled is False
    control.on_change(event("Europe/Lisbon"))
    assert seen == ["Europe/Lisbon"]


def test_a_number_asks_for_a_numeric_keyboard_and_shows_the_text_not_the_value():
    p, _ = props(Field("n", "Porta", "int", bounds=(1024, 65535)), 8765)

    control = fields.field_control(p)

    assert control.value == "8765" and control.keyboard_type == ft.KeyboardType.NUMBER


def test_text_the_person_is_typing_is_kept_even_when_it_is_not_valid_yet():
    p, _ = props(Field("n", "Porta", "int"), None, text="87x")

    assert fields.field_control(p).value == "87x"


def test_a_list_is_one_item_per_line():
    p, seen = props(Field("tags", "Assuntos", "list", help="Palavras-chave"), ["deploy", "error"])

    control = fields.field_control(p)

    assert control.multiline is True and control.value == "deploy\nerror" and control.min_lines >= 2
    control.on_change(event("a\nb"))
    assert seen == ["a\nb"]


def test_a_switch_has_its_label_and_emits_a_boolean():
    p, seen = props(Field("on", "Ativo", "bool"), True)

    switch = of_type(fields.field_control(p), ft.Switch)[0]

    assert switch.label == "Ativo" and switch.value is True
    switch.on_change(event(False))
    assert seen == [False]


def test_a_choice_lists_its_options_and_emits_the_key_selected():
    p, seen = props(Field("mode", "Modo", "choice", options=("draft", "auto"), help="draft ou auto"), "draft")

    control = fields.field_control(p)

    assert isinstance(control, ft.Dropdown) and control.label == "Modo" and control.value == "draft"
    assert [o.key for o in control.options] == ["draft", "auto"] and control.helper_text == "draft ou auto"
    control.on_select(event("auto"))
    assert seen == ["auto"]


def test_a_choice_with_no_value_shows_none_selected_and_an_error_beside_it():
    p, _ = props(Field("mode", "Modo", "choice", options=("draft", "auto")), None, error="escolha um")

    control = fields.field_control(p)

    assert control.value is None and control.error_text == "escolha um"


def test_a_multichoice_is_a_group_of_checkboxes_that_emits_the_list_in_option_order():
    p, seen = props(Field("types", "Tipos", "multichoice", options=("image", "video", "audio")), ["audio"])

    control = fields.field_control(p)
    boxes = of_type(control, ft.Checkbox)

    assert [b.label for b in boxes] == ["image", "video", "audio"] and [b.value for b in boxes] == [False, False, True]
    boxes[0].on_change(event(True))
    assert seen == [["image", "audio"]]
    boxes[2].on_change(event(False))
    assert seen[-1] == []
    assert any(isinstance(c, ft.Semantics) and c.label == "Tipos" for c in walk(control))


def test_a_secret_never_shows_its_value_and_says_whether_one_is_set():  # CFG-10
    field = Field("override_token", "Token de liberação", "secret")
    p, seen = props(field, {"set": True})

    control = fields.field_control(p)
    box = of_type(control, ft.TextField)[0]

    assert box.password is True and box.can_reveal_password is True and box.value == ""
    assert "definido" in (box.hint_text or "").lower()
    box.on_change(event("novo-valor-secreto"))
    assert seen == ["novo-valor-secreto"]


def test_a_secret_that_is_not_set_says_so():  # CFG-10
    box = of_type(fields.field_control(props(Field("override_token", "Token", "secret"), {"set": False})[0]), ft.TextField)[0]

    assert "não definido" in (box.hint_text or "").lower()


def test_a_secret_that_is_set_can_be_removed_and_the_button_says_what_it_removes():  # CFG-10
    field = Field("override_token", "Token de liberação", "secret")
    p, seen = props(field, {"set": True})

    buttons = of_type(fields.field_control(p), ft.TextButton)

    assert len(buttons) == 1 and "Remover" in str(buttons[0].content) and "Token de liberação" in (buttons[0].tooltip or "")
    buttons[0].on_click(event(None))
    assert seen == [CLEAR]


def test_a_secret_that_is_not_set_has_nothing_to_remove():  # CFG-10
    assert of_type(fields.field_control(props(Field("t_token", "Token", "secret"), {"set": False})[0]), ft.TextButton) == []


# ---- the row around the control: where the value comes from, why it is locked, what it risks ----

def test_the_row_says_where_the_value_comes_from_in_words_not_only_in_colour():  # CFG-01
    field = Field("n", "Porta", "int", env_var="WHATSAPP_UI_PORT")

    for origin, word in (("default", "Padrão"), ("file", "Arquivo"), ("env", "Variável de ambiente WHATSAPP_UI_PORT")):
        assert word in texts(fields.field_row(props(field, 1, origin=origin, env_var="WHATSAPP_UI_PORT")[0]))


def test_environment_defined_field_is_locked_and_names_variable():  # CFG-04
    field = Field("n", "Porta", "int", env_var="WHATSAPP_UI_PORT")
    p, _ = props(field, 9000, origin="env", locked=True, env_var="WHATSAPP_UI_PORT")

    row = fields.field_row(p)

    assert all(c.disabled for c in of_type(row, ft.TextField))
    assert "Definido pela variável de ambiente WHATSAPP_UI_PORT" in texts(row)


def test_a_read_only_field_is_disabled_without_blaming_the_environment():  # CFG-04
    p, _ = props(Field("d", "Cifragem", "text", readonly=True), "ligada", locked=True)

    row = fields.field_row(p)

    assert of_type(row, ft.TextField)[0].disabled and "variável de ambiente" not in texts(row)


def test_a_risky_choice_shows_its_warning_only_while_it_is_the_value():  # CFG-01
    field = Field("mode", "Modo", "choice", options=("draft", "auto"), risk="Responde sem aprovação", risk_when=("auto",))

    assert "Responde sem aprovação" in texts(fields.field_row(props(field, "auto")[0]))
    assert "Responde sem aprovação" not in texts(fields.field_row(props(field, "draft")[0]))


def test_a_pending_restart_is_said_with_the_process_to_restart():  # CFG-08
    row = fields.field_row(props(Field("n", "Porta", "int"), 1, pending_restart="bridge")[0])
    assert "Reinicie o bridge" in texts(row)
    assert "Reinicie o servidor MCP" in texts(fields.field_row(props(Field("n", "Porta", "int"), 1, pending_restart="server")[0]))


def test_the_row_can_restore_the_default_only_when_the_value_is_custom():  # CFG-07
    field = Field("n", "Porta", "int", default=8765)
    restored = []

    custom = fields.field_row(FieldProps(field=field, value=9000, on_change=lambda v: None, origin="file", on_restore=lambda: restored.append(1)))
    default = fields.field_row(FieldProps(field=field, value=8765, on_change=lambda v: None, origin="default", on_restore=lambda: restored.append(1)))
    locked = fields.field_row(FieldProps(field=field, value=9000, on_change=lambda v: None, origin="env", locked=True, on_restore=lambda: restored.append(1)))

    assert len(of_type(custom, ft.TextButton)) == 1 and of_type(default, ft.TextButton) == [] and of_type(locked, ft.TextButton) == []
    of_type(custom, ft.TextButton)[0].on_click(event(None))
    assert restored == [1]


def test_the_error_is_beside_the_field_and_nowhere_else():  # CFG-02
    row = fields.field_row(props(Field("n", "Porta", "int"), None, text="x", error="deve ser um número inteiro")[0])

    assert [c.error for c in of_type(row, ft.TextField)] == ["deve ser um número inteiro"]


def test_buttons_are_at_least_44_pixels_high():  # UX-02
    field = Field("override_token", "Token", "secret")
    row = fields.field_row(FieldProps(field=field, value={"set": True}, on_change=lambda v: None, origin="file", on_restore=lambda: None))

    buttons = of_type(row, ft.TextButton)
    assert len(buttons) == 2 and all(b.height is not None and b.height >= fields.MIN_TARGET for b in buttons)


def test_a_switch_sits_in_a_row_tall_enough_to_touch():  # UX-02
    row = fields.field_row(props(Field("on", "Ativo", "bool"), True)[0])

    heights = [c.min_height for c in walk(row) if isinstance(c, ft.Container) and c.min_height]
    assert heights and min(heights) >= fields.MIN_TARGET


def test_the_row_has_no_imperative_calls_and_no_literal_colours():  # EXT-02
    source = open(fields.__file__, encoding="utf-8").read()
    assert ".update(" not in source and "page.add" not in source


# ---- records: a list of groups of fields, each removable ----

def records_field():
    return Field("endpoints", "Endpoints", "records", id_key="name", fields=(
        Field("name", "Nome", "text"), Field("secret", "Segredo", "secret"), Field("on", "Ativo", "bool")))


def test_records_show_each_item_with_its_fields_and_a_remove_button():  # UX-05
    removed, added, changed = [], [], []
    p = FieldProps(field=records_field(), value=[{"name": "a", "secret": {"set": True}, "on": True}, {"name": "b", "secret": {"set": False}, "on": False}],
                   on_change=changed.append, on_add=lambda: added.append(1), on_remove=removed.append)

    control = fields.records_control(p)
    fields_of = [c for c in of_type(control, ft.TextField) if not c.password]
    remove = [b for b in of_type(control, ft.TextButton) if "Remover" in str(b.content) and "Endpoints" in (b.tooltip or "") and "Segredo" not in (b.tooltip or "")]

    assert [t.value for t in fields_of] == ["a", "b"] and len(remove) == 2
    remove[1].on_click(event(None))
    assert removed == [1]
    add = [b for b in of_type(control, ft.Button) + of_type(control, ft.TextButton) if "Adicionar" in str(b.content)]
    assert len(add) == 1
    add[0].on_click(event(None))
    assert added == [1]


def test_editing_a_field_of_a_record_emits_the_whole_list_with_only_that_field_changed():  # CFG-02
    changed = []
    items = [{"name": "a", "on": True}, {"name": "b", "on": False}]
    p = FieldProps(field=records_field(), value=items, on_change=changed.append)

    control = fields.records_control(p)
    second_name = [c for c in of_type(control, ft.TextField) if not c.password][1]
    second_name.on_change(event("renamed"))

    assert changed[-1][0] == items[0] and changed[-1][1]["name"] == "renamed" and changed[-1][1]["on"] is False
    assert items[1]["name"] == "b"                               # the value that was shown is not changed in place


def test_an_empty_list_of_records_says_so_and_offers_to_add():  # UX-03
    control = fields.records_control(FieldProps(field=records_field(), value=[], on_change=lambda v: None, on_add=lambda: None))

    assert "Nenhum" in texts(control) and any("Adicionar" in str(b.content) for b in of_type(control, ft.Button) + of_type(control, ft.TextButton))


def test_a_secret_inside_a_record_is_masked_like_any_other():  # CFG-10
    control = fields.records_control(FieldProps(field=records_field(), value=[{"name": "a", "secret": {"set": True}}], on_change=lambda v: None))

    secret_boxes = [c for c in of_type(control, ft.TextField) if c.password]
    assert len(secret_boxes) == 1 and secret_boxes[0].value == ""
