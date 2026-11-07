"""Form flow (CFG-02, CFG-03, CFG-05, CFG-07, CFG-08, CFG-09, CFG-10): load, apply, validate, save, report."""
import json
import os

import pytest

import uisettings
from ui.config.envlayer import EnvLayer
from ui.config.fields import Field, Problem
from ui.config.form import FormFlow
from ui.config.registry import EnvKeys, JsonFile, ReadOnly, Registry, Section
from ui.config.store import ConfigStore

MAX_MB = "WHATSAPP_AUTO_DOWNLOAD_MAX_MB"


@pytest.fixture
def ctx(tmp_path):
    target = tmp_path / "policy.json"
    extra = {"problems": []}
    reg = Registry()
    reg.register(Section(
        id="policy", title="Policy", order=1, storage=JsonFile(lambda: str(target)), restart=None,
        validator=lambda values: list(extra["problems"]),
        fields=(Field("mode", "Mode", "choice", default="deny", options=("deny", "allow")),
                Field("count", "Count", "int", bounds=(1, 10)),
                Field("allow", "Allow", "list", max_length=20),
                Field("override_token", "Token", "secret"),
                Field("endpoints", "Endpoints", "records", id_key="name", fields=(
                    Field("name", "Name", "text"), Field("secret", "Secret", "secret"))))))
    reg.register(Section(id="channel", title="Channel", order=2, storage=JsonFile(lambda: str(tmp_path / "channel.json")),
                         restart="server", fields=(Field("enabled", "Enabled", "bool", default=False),)))
    reg.register(Section(id="media", title="Media", order=3, storage=EnvKeys(), restart="bridge", fields=(
        Field("max_mb", "Max MB", "int", default=20, bounds=(1, 500), env_var=MAX_MB),)))
    reg.register(Section(id="facts", title="Facts", order=4, storage=ReadOnly(), fields=(Field("path", "Path", "text"),)))
    store = ConfigStore(reg)
    environ = {}
    layer = EnvLayer(reg, store, environ=environ)

    def flow(section_id):
        f = FormFlow(section_id, reg, store, layer)
        f.load()
        return f

    return flow, target, extra, environ, layer, tmp_path


def disk(target):
    return json.loads(target.read_text())


def test_loading_an_absent_file_shows_the_defaults_as_empty(ctx):
    flow, target, *_ = ctx
    view = flow("policy").load()
    assert view.status == "empty" and view.values == {"override_token": {"set": False}}
    assert view.version == "absent" and view.read_only is False
    assert view.state["mode"] == {"origin": "default", "locked": False, "pending": None}
    assert not target.exists()


def test_invalid_values_are_refused_with_a_message_per_field_and_nothing_is_written(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"mode": "everyone", "count": "99", "allow": "x" * 50})
    outcome = f.save()
    assert outcome.ok is False and outcome.unchanged is False
    assert set(outcome.errors) == {"mode", "count", "allow"}
    assert "at most 10" in outcome.errors["count"] and "allow" in outcome.errors["mode"]
    assert not target.exists()


def test_text_that_cannot_be_read_as_a_number_is_a_field_error_that_does_not_echo_it(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"count": "12abc"})
    outcome = f.save()
    assert list(outcome.errors) == ["count"] and "12abc" not in outcome.errors["count"]
    assert not target.exists()
    assert f.validate().errors == outcome.errors


def test_raw_text_goes_through_the_one_parse_rule_and_a_valid_save_is_written(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"count": " 7 ", "allow": "alpha\n\n beta ", "mode": "allow"})
    outcome = f.save()
    assert outcome.ok and outcome.errors == {} and outcome.general == [] and outcome.restarts == []
    assert disk(target) == {"count": 7, "allow": ["alpha", "beta"], "mode": "allow"}
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_a_stale_version_is_a_conflict_and_the_file_is_untouched(ctx):
    flow, target, *_ = ctx
    first = flow("policy")
    first.apply({"count": "3"})
    assert first.save().ok
    mine = flow("policy")
    stale = mine.version
    other = flow("policy")
    other.apply({"count": "4"})
    assert other.save().ok
    mine.version = stale
    mine.apply({"count": "5"})
    outcome = mine.save()
    assert outcome.ok is False and outcome.conflict is True
    assert disk(target)["count"] == 4


def test_the_client_version_is_the_one_checked(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"count": "3"})
    f.version = "a-version-the-file-never-had"
    assert f.save().conflict is True
    assert not target.exists()


def test_applying_nothing_is_an_unchanged_save_and_writes_nothing(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"count": "3"})
    f.save()
    before = target.read_bytes()
    g = flow("policy")
    g.apply({"count": "3"})
    outcome = g.save()
    assert outcome.ok is True and outcome.unchanged is True
    assert target.read_bytes() == before


def test_restoring_a_default_removes_the_custom_value(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"count": "3", "mode": "allow"})
    f.save()
    g = flow("policy")
    g.apply({}, restore=["count"])
    outcome = g.save()
    assert outcome.ok and disk(target) == {"mode": "allow"}


def test_restoring_a_secret_removes_it(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"override_token": "abc"})
    f.save()
    g = flow("policy")
    g.apply({}, restore=["override_token"])
    assert g.save().ok and "override_token" not in disk(target)


def test_restoring_an_unknown_key_is_an_error(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({}, restore=["nope"])
    assert "nope" in f.save().errors


def test_restoring_a_locked_field_is_skipped_and_the_variable_keeps_its_value(ctx):
    flow, target, extra, environ, layer, tmp = ctx
    f = flow("media")
    f.apply({"max_mb": "40"})
    assert f.save().ok
    environ[MAX_MB] = "99"
    g = flow("media")
    g.apply({}, restore=["max_mb"])
    outcome = g.save()
    assert outcome.ok and outcome.unchanged
    with open(uisettings.path()) as fh:
        assert json.load(fh)["env"] == {MAX_MB: "40"}


def test_a_locked_field_cannot_be_changed_and_names_its_variable(ctx):
    flow, _, _, environ, *_ = ctx
    environ[MAX_MB] = "99"
    f = flow("media")
    view = f.load()
    assert view.state["max_mb"]["locked"] is True and view.state["max_mb"]["origin"] == "environment"
    f.apply({"max_mb": "10"})
    outcome = f.save()
    assert outcome.ok is False and MAX_MB in outcome.errors["max_mb"]


def test_a_valid_save_reports_the_restart_scope_and_a_section_without_one_reports_none(ctx):
    flow, *_ = ctx
    f = flow("channel")
    f.apply({"enabled": True})
    assert f.save().restarts == ["server"]
    g = flow("media")
    g.apply({"max_mb": "40"})
    outcome = g.save()
    assert outcome.ok and outcome.restarts == ["bridge"]
    assert flow("media").load().state["max_mb"] == {"origin": "file", "locked": False, "pending": "bridge"}


def test_a_refused_or_unchanged_save_reports_no_restart(ctx):
    flow, *_ = ctx
    f = flow("channel")
    f.apply({"enabled": "maybe"})
    assert f.save().restarts == []
    g = flow("channel")
    g.apply({"enabled": False})
    g.save()
    h = flow("channel")
    h.apply({"enabled": False})
    assert h.save().restarts == []


def test_the_view_never_carries_a_secret_and_a_secret_is_kept_replaced_or_cleared(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"override_token": "TOPSECRET", "endpoints": [{"name": "a", "secret": "S-A"}]})
    assert f.save().ok
    assert "TOPSECRET" not in json.dumps(f.view().__dict__)
    g = flow("policy")
    assert "TOPSECRET" not in json.dumps(g.load().__dict__) and g.load().values["override_token"] == {"set": True}
    g.apply({"count": "2", "endpoints": [{"name": "a", "secret": {"set": True}}]})
    assert g.save().ok and disk(target)["override_token"] == "TOPSECRET" and disk(target)["endpoints"][0]["secret"] == "S-A"
    h = flow("policy")
    h.apply({"override_token": {"clear": True}})
    assert h.save().ok and "override_token" not in disk(target)
    i = flow("policy")
    i.apply({"override_token": "new"})
    assert i.save().ok and disk(target)["override_token"] == "new"


def test_a_secret_that_is_not_text_is_refused_at_its_field(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"override_token": 12})
    assert "override_token" in f.save().errors and not target.exists()


def test_a_record_secret_error_lands_on_its_record(ctx):
    flow, *_ = ctx
    f = flow("policy")
    f.apply({"endpoints": [{"name": "a", "secret": 5}]})
    assert "endpoints.0.secret" in f.save().errors


def test_the_section_validator_decides_and_a_problem_without_a_field_is_general(ctx):
    flow, target, extra, *_ = ctx
    extra["problems"] = [Problem("mode", "not allowed now"), Problem(None, "the whole file is wrong")]
    f = flow("policy")
    f.apply({"count": "2"})
    outcome = f.save()
    assert outcome.ok is False and outcome.errors == {"mode": "not allowed now"} and outcome.general == ["the whole file is wrong"]
    assert not target.exists()


def test_an_unreadable_file_is_shown_with_the_error_and_replaced_only_after_confirmation(ctx):
    flow, target, *_ = ctx
    target.write_text("{ not json")
    f = flow("policy")
    view = f.load()
    assert view.status == "error" and "not valid JSON" in view.banner and "not json" not in view.banner
    f.apply({"count": "2"})
    outcome = f.save()
    assert outcome.ok is False and outcome.needs_confirmation is True
    assert target.read_text() == "{ not json"
    assert f.save(confirm_replace=True).ok and disk(target) == {"count": 2}


def test_an_existing_file_with_invalid_values_shows_the_project_error_and_can_be_corrected(ctx):
    flow, target, extra, *_ = ctx
    target.write_text(json.dumps({"mode": "deny", "count": 3}))
    extra["problems"] = [Problem("count", "is not accepted by the project")]
    f = flow("policy")
    view = f.load()
    assert view.status == "error" and "is not accepted by the project" in view.banner
    extra["problems"] = []
    f.apply({"count": "4"})
    assert f.save().ok and disk(target)["count"] == 4


def test_an_unknown_field_or_a_read_only_section_cannot_be_changed(ctx):
    flow, target, *_ = ctx
    f = flow("policy")
    f.apply({"nope": 1})
    assert "nope" in f.save().errors
    g = flow("facts")
    assert g.load().read_only is True
    g.apply({"path": "/x"})
    outcome = g.save()
    assert outcome.ok is False and (outcome.errors or outcome.general)


def test_keys_the_section_does_not_declare_stay_in_the_file_but_never_in_the_view(ctx):
    flow, target, *_ = ctx
    target.write_text(json.dumps({"count": 2, "future_key": "kept"}))
    f = flow("policy")
    assert "future_key" not in f.load().values
    f.apply({"count": "3"})
    assert f.save().ok and disk(target) == {"count": 3, "future_key": "kept"}


def test_an_environment_section_loads_values_origins_and_saves_to_the_settings_file(ctx):
    flow, *_ = ctx
    f = flow("media")
    view = f.load()
    assert view.values == {"max_mb": 20} and view.state["max_mb"]["origin"] == "default"
    f.apply({"max_mb": "bad"})
    assert "max_mb" in f.save().errors
    g = flow("media")
    g.apply({"max_mb": "40"})
    assert g.save().ok
    assert flow("media").load().values == {"max_mb": 40}


def test_the_stale_version_of_an_environment_section_is_a_conflict(ctx):
    flow, *_ = ctx
    f = flow("media")
    f.apply({"max_mb": "40"})
    f.version = "stale"
    assert f.save().conflict is True
