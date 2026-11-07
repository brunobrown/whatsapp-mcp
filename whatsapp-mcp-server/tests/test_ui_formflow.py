"""The flow of one section's form: load, edit, check, save, conflict (CFG-02, CFG-03, CFG-05, CFG-06, CFG-07, CFG-09, CFG-10)."""
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import uisettings  # noqa: E402
from ui.core.envlayer import EnvLayer  # noqa: E402
from ui.core.formflow import FormFlow  # noqa: E402
from ui.core.registry import EnvKeys, Field, JsonFile, Problem, ReadOnly, Section  # noqa: E402
from ui.core.secrets import CLEAR  # noqa: E402
from ui.core.store import ABSENT, ConfigStore  # noqa: E402

SECRET = "override-0123456789-secret"


def policy_section(path, problems=None, restart=None):
    def validate(values):
        if problems is not None:
            return problems(values)
        return [Problem("mode", "modo inválido")] if values.get("mode") not in (None, "allowlist", "denylist") else []
    return Section(id="policy", title="Política", icon="x", order=1, storage=JsonFile(lambda: str(path)), validate=validate, restart=restart, fields=(
        Field("mode", "Modo", "choice", options=("allowlist", "denylist"), default="denylist"),
        Field("allow", "Permitir", "list", default=[]),
        Field("size", "Tamanho", "int", bounds=(1, 10)),
        Field("override_token", "Token", "secret"),
        Field("on", "Ativo", "bool", default=True)))


@pytest.fixture
def path(tmp_path):
    return tmp_path / "config" / "policy.json"


def write(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    os.chmod(path, 0o600)


def flow_for(path, **extra):
    flow = FormFlow(policy_section(path, **extra), ConfigStore(), EnvLayer(environ={}))
    flow.load()
    return flow


def file_of(path):
    return json.loads(path.read_text())


# ---- loading ----

def test_a_missing_file_is_an_empty_form_with_the_defaults_to_show(path):  # UX-03
    flow = flow_for(path)

    assert flow.status == "empty" and flow.version == ABSENT and not flow.draft.dirty and flow.can_save is True
    assert flow.origin("mode") == "default" and flow.shown("mode") == "denylist" and flow.shown("on") is True


def test_an_existing_file_shows_its_values_with_secrets_masked(path):  # CFG-01, CFG-10
    write(path, {"mode": "allowlist", "allow": ["a", "b"], "override_token": SECRET})

    flow = flow_for(path)

    assert flow.status == "ready" and flow.draft.values["mode"] == "allowlist" and flow.draft.values["allow"] == ["a", "b"]
    assert flow.draft.values["override_token"] == {"set": True} and SECRET not in repr(flow.draft.values) + repr(flow.banner)
    assert flow.origin("mode") == "file" and flow.origin("size") == "default"


def test_a_file_that_is_not_json_is_an_error_that_does_not_show_its_content(path):  # CFG-09
    write(path, '{"override_token": "%s"' % SECRET)

    flow = flow_for(path)

    assert flow.status == "error" and "JSON" in flow.banner and SECRET not in flow.banner
    assert path.read_text().startswith('{"override_token"')


def test_a_file_the_project_refuses_shows_what_it_holds_and_what_is_wrong(path):  # CFG-09
    write(path, {"mode": "both", "allow": ["x"]})

    flow = flow_for(path)

    assert flow.status == "error" and flow.draft.values["allow"] == ["x"] and flow.errors["mode"] == "modo inválido"


def test_a_check_that_cannot_be_made_blocks_saving_and_says_why(path):  # CFG-02
    write(path, {"mode": "allowlist"})
    flow = flow_for(path, problems=lambda v: [Problem("_bridge", "o bridge não responde")])

    assert flow.status == "unavailable" and flow.can_save is False and "bridge" in flow.banner


def test_a_file_other_users_can_read_is_warned_about(path):  # CFG-10
    write(path, {"mode": "allowlist"})
    os.chmod(path, 0o644)

    flow = flow_for(path)

    if sys.platform != "win32":
        assert any("chmod 600" in w for w in flow.warnings)


# ---- editing ----

def test_a_typed_value_updates_the_draft(path):  # CFG-02
    flow = flow_for(path)

    flow.set("size", "7")
    flow.set("allow", "x\ny")
    flow.set("mode", "allowlist")
    flow.set("on", False)

    assert flow.draft.changes() == {"size": 7, "allow": ["x", "y"], "mode": "allowlist", "on": False}
    assert flow.dirty and flow.errors == {}


def test_a_number_that_is_not_one_keeps_the_text_and_blocks_saving(path):  # CFG-02
    flow = flow_for(path)

    flow.set("size", "7x")

    assert flow.errors["size"] == "deve ser um número inteiro" and flow.typed("size") == "7x" and flow.can_save is False
    flow.set("size", "7")
    assert "size" not in flow.errors and flow.typed("size") is None and flow.can_save is True


def test_a_value_out_of_bounds_is_an_error_beside_the_field_as_soon_as_it_is_typed(path):  # CFG-02
    flow = flow_for(path)

    flow.set("size", "99")

    assert "entre 1 e 10" in flow.errors["size"] and flow.can_save is False


def test_a_secret_typed_is_a_change_and_blank_goes_back_to_what_was_stored(path):  # CFG-10
    write(path, {"override_token": SECRET})
    flow = flow_for(path)

    flow.set("override_token", "a-brand-new-value")
    assert flow.draft.changes() == {"override_token": "a-brand-new-value"}
    flow.set("override_token", "")
    assert flow.draft.changes() == {} and flow.draft.values["override_token"] == {"set": True}


def test_asking_to_remove_a_secret_is_a_change(path):  # CFG-10
    write(path, {"override_token": SECRET})
    flow = flow_for(path)

    flow.set("override_token", CLEAR)

    assert flow.draft.changes() == {"override_token": CLEAR}


def test_leaving_with_unsaved_changes_requires_confirmation(path):  # CFG-06
    flow = flow_for(path)
    flow.set("size", "3")

    assert flow.leave() is False and flow.dirty
    assert flow.leave(confirmed=True) is True and not flow.dirty and flow.draft.values.get("size") is None


def test_discard_goes_back_and_forgets_what_was_typed_wrong(path):  # CFG-06
    flow = flow_for(path)
    flow.set("size", "x")

    flow.discard()

    assert flow.errors == {} and flow.typed("size") is None and not flow.dirty and flow.can_save is True


# ---- saving ----

def test_save_with_an_invalid_value_writes_nothing(path):  # CFG-02
    write(path, {"mode": "allowlist"})
    flow = flow_for(path)
    flow.set("size", "99")

    outcome = flow.save()

    assert outcome.ok is False and "size" in outcome.errors and file_of(path) == {"mode": "allowlist"}


def test_save_writes_the_change_keeps_the_secret_and_the_keys_the_form_does_not_know(path):  # CFG-03, CFG-10
    write(path, {"mode": "allowlist", "override_token": SECRET, "custom": {"keep": "me"}})
    flow = flow_for(path)
    flow.set("allow", "Project Alpha")

    outcome = flow.save()

    assert outcome.ok and file_of(path) == {"mode": "allowlist", "override_token": SECRET, "custom": {"keep": "me"}, "allow": ["Project Alpha"]}
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not flow.dirty and flow.draft.values["allow"] == ["Project Alpha"] and flow.draft.values["override_token"] == {"set": True}
    assert flow.origin("allow") == "file"


def test_a_new_secret_replaces_the_old_and_a_removed_one_goes(path):  # CFG-10
    write(path, {"override_token": SECRET})
    flow = flow_for(path)
    flow.set("override_token", "the-new-secret-value")
    assert flow.save().ok and file_of(path)["override_token"] == "the-new-secret-value"

    flow.set("override_token", CLEAR)
    assert flow.save().ok and "override_token" not in file_of(path)


def test_save_with_nothing_changed_does_not_touch_the_file(path):
    write(path, {"mode": "allowlist"})
    before = os.stat(path).st_mtime_ns
    flow = flow_for(path)

    outcome = flow.save()

    assert outcome.ok and outcome.unchanged and os.stat(path).st_mtime_ns == before


def test_the_project_validator_can_refuse_what_the_form_accepted(path):  # CFG-02
    flow = flow_for(path, problems=lambda v: [Problem("allow", "entrada inválida")] if v.get("allow") else [])
    flow.set("allow", "a@b@c")

    outcome = flow.save()

    assert outcome.ok is False and outcome.errors["allow"] == "entrada inválida" and not path.exists()
    assert flow.dirty and flow.errors["allow"] == "entrada inválida"             # the changes are still there to fix


def test_a_problem_of_the_whole_form_comes_back_as_a_general_message(path):  # CFG-02
    flow = flow_for(path, problems=lambda v: [Problem(None, "o arquivo todo está errado")])
    flow.set("size", "3")

    outcome = flow.save()

    assert outcome.ok is False and outcome.general == ["o arquivo todo está errado"]


def test_save_is_refused_when_file_changed_on_disk_and_the_changes_stay_(path):  # CFG-05
    write(path, {"mode": "allowlist"})
    flow = flow_for(path)
    flow.set("size", "4")
    write(path, {"mode": "denylist", "edited": "by hand"})

    outcome = flow.save()

    assert outcome.ok is False and outcome.conflict is True
    assert file_of(path) == {"mode": "denylist", "edited": "by hand"} and flow.draft.changes() == {"size": 4}


def test_after_a_conflict_reloading_takes_the_file_and_drops_the_draft(path):  # CFG-05
    write(path, {"mode": "allowlist"})
    flow = flow_for(path)
    flow.set("size", "4")
    write(path, {"mode": "denylist"})
    flow.save()

    flow.reload()

    assert flow.draft.values["mode"] == "denylist" and not flow.dirty and flow.save().ok


def test_a_file_that_cannot_be_read_is_replaced_only_after_confirmation(path):  # CFG-09
    write(path, "{broken")
    flow = flow_for(path)
    flow.set("size", "3")

    first = flow.save()
    assert first.ok is False and first.needs_confirmation is True and path.read_text() == "{broken"
    second = flow.save(confirmed_replace=True)
    assert second.ok and file_of(path) == {"size": 3}


def test_an_invalid_file_that_still_parses_needs_no_confirmation_to_be_corrected(path):  # CFG-09
    write(path, {"mode": "both"})
    flow = flow_for(path)
    flow.set("mode", "allowlist")

    assert flow.save().ok and file_of(path) == {"mode": "allowlist"}


def test_a_saved_section_says_which_process_must_restart(path):  # CFG-08
    flow = flow_for(path, restart="bridge")
    flow.set("size", "3")

    assert flow.save().restart == "bridge"
    assert flow_for(path, restart="bridge").save().restart is None            # nothing changed, nothing to restart


def test_restoring_a_default_removes_the_key_from_the_file(path):  # CFG-07
    write(path, {"mode": "allowlist", "size": 5})
    flow = flow_for(path)

    assert flow.restore_default("size") is False and flow.draft.values["size"] == 5
    assert flow.restore_default("size", confirmed=True) is True
    assert flow.save().ok and file_of(path) == {"mode": "allowlist"}
    assert flow.origin("size") == "default"


def test_a_section_with_encode_and_decode_keeps_the_other_items_when_one_changes(tmp_path):  # CFG-03
    def decode(file):
        return {"items": [{"name": n, **body} for n, body in file.get("items", {}).items()]}

    def encode(form, previous):
        return {"items": {i["name"]: {k: v for k, v in i.items() if k != "name" and v not in (None, [])} for i in form["items"]}}
    path = tmp_path / "config" / "items.json"
    write(path, {"items": {"a": {"x": 1}, "b": {"x": 2}}})
    section = Section(id="items", title="Itens", icon="x", order=1, storage=JsonFile(lambda: str(path)), validate=lambda v: [], decode=decode, encode=encode,
                      fields=(Field("items", "Itens", "records", id_key="name", fields=(Field("name", "Nome", "text"), Field("x", "X", "int"))),))
    flow = FormFlow(section, ConfigStore(), EnvLayer(environ={}))
    flow.load()

    items = flow.draft.values["items"]
    flow.set("items", [items[0], {**items[1], "x": 9}])

    assert flow.save().ok and file_of(path) == {"items": {"a": {"x": 1}, "b": {"x": 9}}}


def test_the_form_level_check_runs_before_anything_is_written(tmp_path):  # CFG-02
    path = tmp_path / "config" / "items.json"
    section = Section(id="items", title="Itens", icon="x", order=1, storage=JsonFile(lambda: str(path)), validate=lambda v: [],
                      check_form=lambda form: [Problem("items.1.name", "nome repetido")] if len({i["name"] for i in form["items"]}) < len(form["items"]) else [],
                      fields=(Field("items", "Itens", "records", id_key="name", fields=(Field("name", "Nome", "text"),)),))
    flow = FormFlow(section, ConfigStore(), EnvLayer(environ={}))
    flow.load()
    flow.set("items", [{"name": "a"}, {"name": "a"}])

    outcome = flow.save()

    assert outcome.ok is False and "items" in outcome.errors and not path.exists()


def test_adding_a_blank_item_and_removing_one_with_confirmation(path):  # UX-05
    section = Section(id="items", title="Itens", icon="x", order=1, storage=JsonFile(lambda: str(path)), validate=lambda v: [],
                      fields=(Field("items", "Itens", "records", id_key="name",
                                    fields=(Field("name", "Nome", "text"), Field("tags", "Tags", "list"), Field("on", "Ativo", "bool"))),))
    flow = FormFlow(section, ConfigStore(), EnvLayer(environ={}))
    flow.load()

    flow.add_item("items")
    assert flow.draft.values["items"] == [{"name": None, "tags": [], "on": None}]
    assert flow.remove_item("items", 0) is False and len(flow.draft.values["items"]) == 1
    assert flow.remove_item("items", 0, confirmed=True) is True and flow.draft.values["items"] == []


# ---- sections kept in ui_settings.json ----

def media_section():
    return Section(id="media", title="Mídia", icon="x", order=1, storage=EnvKeys(), validate=lambda v: [], fields=(
        Field("auto", "Auto", "bool", default=True, env_var="WHATSAPP_AUTO_DOWNLOAD"),
        Field("max_mb", "MB", "float", default=25, bounds=(1, 100), env_var="WHATSAPP_AUTO_DOWNLOAD_MAX_MB"),
        Field("zone", "Fuso", "text", env_var="WHATSAPP_TIMEZONE")))


def env_flow(environ=None):
    flow = FormFlow(media_section(), ConfigStore(), EnvLayer(environ={} if environ is None else environ))
    flow.load()
    return flow


def test_an_environment_section_shows_the_effective_values_and_their_origin(tmp_path):  # CFG-01
    flow = env_flow()

    assert flow.status == "empty" and flow.shown("max_mb") == 25 and flow.origin("max_mb") == "default" and flow.env_var("max_mb") == "WHATSAPP_AUTO_DOWNLOAD_MAX_MB"


def test_a_field_the_environment_defines_is_locked_and_cannot_be_set(tmp_path):  # CFG-04
    flow = env_flow({"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "10"})

    assert flow.is_locked("max_mb") and flow.origin("max_mb") == "env" and flow.shown("max_mb") == 10
    assert flow.set("max_mb", "50") is False and not flow.dirty


def test_an_environment_section_saves_to_the_settings_file_and_reports_the_restart(tmp_path):  # CFG-04, CFG-08
    flow = env_flow()
    flow.set("max_mb", "50")
    flow.set("auto", False)

    outcome = flow.save()

    assert outcome.ok and outcome.restart == "bridge"
    applied = {}
    uisettings.apply(applied)
    assert applied == {"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50", "WHATSAPP_AUTO_DOWNLOAD": "false"}
    assert flow.origin("max_mb") == "file" and flow.pending("max_mb") == "bridge" and not flow.dirty


def test_going_back_to_the_default_removes_the_variable_from_the_file(tmp_path):  # CFG-07
    flow = env_flow()
    flow.set("max_mb", "50")
    flow.save()

    flow.set("max_mb", "25")             # the default again
    assert flow.save().ok

    assert flow.origin("max_mb") == "default"
    applied = {}
    uisettings.apply(applied)
    assert "WHATSAPP_AUTO_DOWNLOAD_MAX_MB" not in applied


def test_an_environment_section_refuses_a_value_out_of_bounds_with_nothing_written(tmp_path):  # CFG-02
    flow = env_flow()
    flow.set("max_mb", "500")

    outcome = flow.save()

    assert outcome.ok is False and "max_mb" in outcome.errors and not (tmp_path / "config" / "ui_settings.json").exists()


def test_an_environment_section_notices_the_file_changing_underneath(tmp_path):  # CFG-05
    flow = env_flow()
    flow.set("max_mb", "50")
    other = EnvLayer(environ={})
    other.save(media_section(), {"zone": "UTC"}, other.load().version)

    outcome = flow.save()

    assert outcome.ok is False and outcome.conflict is True


def test_a_read_only_section_cannot_be_changed_or_saved(tmp_path):  # CFG-04
    section = Section(id="db", title="Banco", icon="x", order=1, storage=ReadOnly(lambda: {"where": "/x"}), validate=lambda v: [],
                      fields=(Field("where", "Onde", "text", readonly=True),))
    flow = FormFlow(section, ConfigStore(), EnvLayer(environ={}))
    flow.load()

    assert flow.shown("where") == "/x" and flow.is_locked("where") and flow.can_save is False
    assert flow.set("where", "/y") is False
    assert flow.save().ok is False
