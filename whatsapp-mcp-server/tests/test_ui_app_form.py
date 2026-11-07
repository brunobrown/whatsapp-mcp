"""The form of a section as the operator meets it: states, errors beside fields, confirmations, notices (UX-03, UX-04, UX-05, CFG-02..09)."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ft = pytest.importorskip("flet")

from ui.app import form as form_module  # noqa: E402
from ui.app.form import Confirm, FormActions, confirm_dialog, form_view, loading_view  # noqa: E402
from ui.app.notifier import Notice  # noqa: E402
from ui.core.envlayer import EnvLayer  # noqa: E402
from ui.core.formflow import FormFlow  # noqa: E402
from ui.core.registry import EnvKeys, Field, JsonFile, Problem, ReadOnly, Section  # noqa: E402
from ui.core.store import ConfigStore  # noqa: E402


def walk(control):
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from walk(content)
    for action in getattr(control, "actions", None) or []:
        yield from walk(action)


def of_type(control, kind):
    return [c for c in walk(control) if isinstance(c, kind)]


def texts(control):
    return " | ".join(str(c.value) for c in of_type(control, ft.Text) if c.value)


def buttons(control, word):
    return [b for b in of_type(control, (ft.Button, ft.FilledButton, ft.TextButton, ft.OutlinedButton)) if word in str(b.content)]


def click(button):
    button.on_click(SimpleNamespace(control=button))


def type_into(field_control, text):
    field_control.on_change(SimpleNamespace(control=SimpleNamespace(value=text)))


class Env:
    """Everything the form talks to, recorded."""

    def __init__(self):
        self.notices, self.asked, self.restarts, self.refreshes = [], [], [], 0

    def actions(self, flow):
        return FormActions(flow, notify=self.notices.append, ask=self.asked.append, refresh=self._refresh, note_restart=self.restarts.append)

    def _refresh(self):
        self.refreshes += 1


def make(section, environ=None):
    flow = FormFlow(section, ConfigStore(), EnvLayer(environ={} if environ is None else environ))
    flow.load()
    env = Env()
    return flow, env, env.actions(flow)


def policy(path, validate=lambda v: [], restart=None):
    return Section(id="policy", title="Política de envio", icon="x", order=1, storage=JsonFile(lambda: str(path)), validate=validate, restart=restart,
                   description="Define para quem o agente envia.", fields=(
                       Field("mode", "Modo", "choice", options=("allowlist", "denylist"), default="denylist"),
                       Field("size", "Tamanho", "int", bounds=(1, 10), help="De 1 a 10"),
                       Field("token_value", "Token de liberação", "secret"),
                       Field("session", "Contaminação", "choice", options=("block", "off"), default="block", risk_when=("off",), risk="Pode vazar dados"),
                       Field("items", "Itens", "records", id_key="name", fields=(Field("name", "Nome", "text"),))))


@pytest.fixture
def path(tmp_path):
    return tmp_path / "config" / "policy.json"


def write(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    os.chmod(path, 0o600)


# ---- the states a section can be in ----

def test_section_has_loading_empty_error_and_unavailable_states(path):  # UX-03
    assert "Carregando" in texts(loading_view("Política de envio")) and of_type(loading_view("x"), ft.ProgressRing)

    flow, env, actions = make(policy(path))
    assert "ainda não existe" in texts(form_view(flow, actions)).lower()                       # empty

    write(path, {"mode": "both"})
    flow, env, actions = make(policy(path, validate=lambda v: [Problem("mode", "modo inválido")]))
    view = form_view(flow, actions)
    mode = next(c for c in of_type(view, ft.Dropdown) if c.label == "Modo")
    assert flow.status == "error" and mode.error_text == "modo inválido" and "tem problemas" in texts(view)       # error

    flow, env, actions = make(policy(path, validate=lambda v: [Problem("_bridge", "o bridge não responde")]))
    view = form_view(flow, actions)
    assert flow.status == "unavailable" and "o bridge não responde" in texts(view)             # unavailable
    assert all(b.disabled for b in buttons(view, "Salvar"))


def test_the_form_shows_the_title_as_a_heading_and_the_description(path):  # UX-02
    flow, _, actions = make(policy(path))

    view = form_view(flow, actions)

    headings = [c for c in walk(view) if isinstance(c, ft.Semantics) and c.header]
    assert headings and "Política de envio" in texts(headings[0]) and "Define para quem o agente envia." in texts(view)


def test_every_field_of_the_section_has_a_row_with_its_label(path):
    flow, _, actions = make(policy(path))

    view = form_view(flow, actions)

    labels = {getattr(c, "label", None) for c in walk(view)}
    assert {"Modo", "Tamanho", "Token de liberação", "Contaminação"} <= labels


def test_a_file_other_users_can_read_is_warned_about_on_the_form(path):  # CFG-10
    write(path, {"mode": "allowlist"})
    os.chmod(path, 0o644)
    flow, _, actions = make(policy(path))

    if sys.platform != "win32":
        assert "chmod 600" in texts(form_view(flow, actions))


# ---- editing and the error beside the field ----

def test_a_number_typed_wrong_shows_its_error_beside_the_field_and_blocks_saving(path):  # CFG-02
    flow, env, actions = make(policy(path))
    view = form_view(flow, actions)
    box = next(c for c in of_type(view, ft.TextField) if c.label == "Tamanho")

    type_into(box, "7x")

    view = form_view(flow, actions)
    box = next(c for c in of_type(view, ft.TextField) if c.label == "Tamanho")
    assert box.error == "deve ser um número inteiro" and box.value == "7x"
    assert all(b.disabled for b in buttons(view, "Salvar")) and env.refreshes >= 1


def test_the_save_button_waits_for_a_change(path):
    flow, _, actions = make(policy(path))
    assert all(b.disabled for b in buttons(form_view(flow, actions), "Salvar"))

    flow.set("size", "3")

    assert not any(b.disabled for b in buttons(form_view(flow, actions), "Salvar"))


# ---- saving, and what is said about it ----

def test_saving_writes_the_file_and_says_so_once(path):  # UX-04
    flow, env, actions = make(policy(path))
    flow.set("size", "3")

    click(buttons(form_view(flow, actions), "Salvar")[0])

    assert json.loads(path.read_text()) == {"size": 3} and [n.key for n in env.notices] == ["saved:Política de envio"]
    assert not flow.dirty


def test_a_refused_save_says_it_failed_and_shows_why_beside_the_field(path):  # UX-04, CFG-02
    flow, env, actions = make(policy(path, validate=lambda v: [Problem("size", "tamanho recusado")]))
    flow.set("size", "3")

    click(buttons(form_view(flow, actions), "Salvar")[0])

    assert not path.exists() and [n.key for n in env.notices] == ["save_failed:Política de envio"]
    box = next(c for c in of_type(form_view(flow, actions), ft.TextField) if c.label == "Tamanho")
    assert box.error == "tamanho recusado"


def test_saving_a_section_that_needs_a_restart_says_which_process(path):  # CFG-08
    flow, env, actions = make(policy(path, restart="bridge"))
    flow.set("size", "3")

    click(buttons(form_view(flow, actions), "Salvar")[0])

    assert env.restarts == ["bridge"] and "restart:bridge" in [n.key for n in env.notices]


def test_a_conflict_offers_to_reload_and_keeps_the_changes(path):  # CFG-05
    write(path, {"mode": "allowlist"})
    flow, env, actions = make(policy(path))
    flow.set("size", "3")
    write(path, {"mode": "denylist"})

    click(buttons(form_view(flow, actions), "Salvar")[0])
    view = form_view(flow, actions)

    assert "mudou no disco" in texts(view) and flow.dirty
    reload = buttons(view, "Recarregar")
    assert len(reload) == 1
    click(reload[0])
    assert not flow.dirty and flow.draft.values["mode"] == "denylist"


def test_replacing_a_file_that_could_not_be_read_asks_first(path):  # CFG-09
    write(path, "{broken")
    flow, env, actions = make(policy(path))
    flow.set("size", "3")

    click(buttons(form_view(flow, actions), "Salvar")[0])

    assert path.read_text() == "{broken" and len(env.asked) == 1
    ask = env.asked[0]
    assert "substituir" in (ask.title + ask.body).lower() and "policy.json" in ask.body
    ask.action()
    assert json.loads(path.read_text()) == {"size": 3}


# ---- confirmations ----

def test_destructive_actions_require_confirmation(path):  # UX-05
    write(path, {"mode": "allowlist", "size": 5})
    flow, env, actions = make(policy(path))
    flow.add_item("items")
    flow.add_item("items")

    click(buttons(form_view(flow, actions), "Restaurar todos")[0])
    assert len(env.asked) == 1 and flow.draft.values["size"] == 5                              # asked, nothing done yet
    env.asked[0].action()
    assert flow.draft.values["size"] is None

    flow.discard()
    flow.add_item("items")
    flow.add_item("items")
    remove = buttons(form_view(flow, actions), "Remover")
    click(remove[0])
    assert len(env.asked) == 2 and len(flow.draft.values["items"]) == 2
    env.asked[1].action()
    assert len(flow.draft.values["items"]) == 1


def test_restoring_one_field_asks_first_and_says_which(path):  # CFG-07
    write(path, {"size": 5})
    flow, env, actions = make(policy(path))

    click(buttons(form_view(flow, actions), "Restaurar padrão")[0])

    assert len(env.asked) == 1 and "Tamanho" in env.asked[0].body and flow.draft.values["size"] == 5
    env.asked[0].action()
    assert flow.draft.values["size"] is None


def test_discarding_changes_asks_only_when_there_are_some(path):  # CFG-06
    flow, env, actions = make(policy(path))
    assert all(b.disabled for b in buttons(form_view(flow, actions), "Descartar"))

    flow.set("size", "3")
    click(buttons(form_view(flow, actions), "Descartar")[0])

    assert len(env.asked) == 1 and flow.dirty
    env.asked[0].action()
    assert not flow.dirty


def test_choosing_a_value_that_weakens_a_defence_asks_first_with_the_warning(path):  # CFG-01
    flow, env, actions = make(policy(path))
    view = form_view(flow, actions)
    dropdown = next(c for c in of_type(view, ft.Dropdown) if c.label == "Contaminação")

    dropdown.on_select(SimpleNamespace(control=SimpleNamespace(value="off")))

    assert flow.shown("session") == "block" and len(env.asked) == 1 and "Pode vazar dados" in env.asked[0].body
    env.asked[0].action()
    assert flow.shown("session") == "off"


def test_choosing_the_safe_value_asks_nothing(path):  # CFG-01
    write(path, {"session": "off"})
    flow, env, actions = make(policy(path))
    dropdown = next(c for c in of_type(form_view(flow, actions), ft.Dropdown) if c.label == "Contaminação")

    dropdown.on_select(SimpleNamespace(control=SimpleNamespace(value="block")))

    assert env.asked == [] and flow.shown("session") == "block"


def test_the_confirmation_dialog_names_the_action_and_can_be_declined(path):  # UX-05
    done = []
    dialog = confirm_dialog(Confirm("Remover Itens 1", "Isto remove o item.", "Remover", lambda: done.append(1)), close=lambda: done.append("closed"))

    assert isinstance(dialog, ft.AlertDialog) and dialog.modal is True
    assert "Remover Itens 1" in texts(dialog.title if isinstance(dialog.title, ft.Control) else ft.Text(dialog.title))
    cancel, confirm = buttons(dialog, "Cancelar")[0], buttons(dialog, "Remover")[0]
    click(cancel)
    assert done == ["closed"]
    click(confirm)
    assert done == ["closed", 1, "closed"]


# ---- read only and the environment ----

def test_a_read_only_section_has_no_buttons_to_change_anything(tmp_path):  # CFG-04
    section = Section(id="db", title="Banco", icon="x", order=1, storage=ReadOnly(lambda: {"where": "/x"}), validate=lambda v: [],
                      fields=(Field("where", "Onde", "text", readonly=True),))
    flow, _, actions = make(section)

    view = form_view(flow, actions)

    assert buttons(view, "Salvar") == [] and buttons(view, "Descartar") == [] and buttons(view, "Restaurar todos") == []
    assert all(c.disabled for c in of_type(view, ft.TextField))


def test_an_environment_field_is_locked_and_names_the_variable_on_the_form(tmp_path):  # CFG-04
    section = Section(id="media", title="Mídia", icon="x", order=1, storage=EnvKeys(), validate=lambda v: [],
                      fields=(Field("max_mb", "Tamanho máximo", "float", default=25, env_var="WHATSAPP_AUTO_DOWNLOAD_MAX_MB"),))
    flow, _, actions = make(section, {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "10"})

    view = form_view(flow, actions)

    assert of_type(view, ft.TextField)[0].disabled and "Definido pela variável de ambiente WHATSAPP_AUTO_DOWNLOAD_MAX_MB" in texts(view)


def test_a_change_waiting_for_a_restart_is_said_on_the_form(tmp_path):  # CFG-08
    section = Section(id="media", title="Mídia", icon="x", order=1, storage=EnvKeys(), validate=lambda v: [],
                      fields=(Field("max_mb", "Tamanho máximo", "float", default=25, bounds=(1, 100), env_var="WHATSAPP_AUTO_DOWNLOAD_MAX_MB"),))
    flow, env, actions = make(section)
    flow.set("max_mb", "50")
    click(buttons(form_view(flow, actions), "Salvar")[0])

    assert "Reinicie o bridge" in texts(form_view(flow, actions)) and env.restarts == ["bridge"]


def test_the_form_view_is_a_function_of_the_flow_with_no_imperative_calls():  # EXT-02
    source = open(form_module.__file__, encoding="utf-8").read()

    assert ".update(" not in source and "page.add" not in source


def test_the_form_tells_the_shell_when_it_gets_or_loses_unsaved_changes(path):  # CFG-06
    flow = FormFlow(policy(path), ConfigStore(), EnvLayer(environ={}))
    flow.load()
    marks = []
    actions = FormActions(flow, notify=lambda n: None, ask=lambda c: None, refresh=lambda: None, note_restart=lambda s: None, dirty_changed=marks.append)

    actions.change("size")("3")
    click(buttons(form_view(flow, actions), "Salvar")[0])

    assert marks == [True, False]
