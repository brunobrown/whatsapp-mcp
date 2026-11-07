"""The form service (CFG-01, CFG-02, CFG-03, MIG-04): schema, values, raw text, conflicts and secrets as JSON."""
import json
import os
import shutil

import pytest

from ui.api.forms import FormService
from ui.api.services import build_services

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "ui")
TOKEN = "example-override-token-0000"


@pytest.fixture
def rig():
    services = build_services({})
    return services, FormService(services)


def put_fixture(services, section_id, name):
    path = services.registry.path_of(section_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copy(os.path.join(FIXTURES, name), path)
    os.chmod(path, 0o600)
    return path


def read(path):
    with open(path) as f:
        return json.load(f)


def edit(service, section_id, changes, restore=(), version=None, confirm_replace=False):
    flow = service.open(section_id)
    service.apply(flow, changes, restore, version if version is not None else flow.version)
    return flow, service.save(flow, confirm_replace)


# -- schema and values -----------------------------------------------------------------------------
def test_the_view_carries_the_fields_defaults_and_the_section_facts(rig):
    services, service = rig
    view = service.view(service.open("media"))

    assert (view["id"], view["title"], view["restart"], view["read_only"]) == ("media", "Media downloads", "bridge", False)
    by_key = {f["key"]: f for f in view["fields"]}
    assert list(by_key) == ["auto_download", "auto_download_max_mb", "auto_download_types", "media_retention_days"]
    size = by_key["auto_download_max_mb"]
    assert size["kind"] == "float" and size["default"] == 25 and size["bounds"] == [0.01, 10240]
    assert size["env_var"] == "WHATSAPP_AUTO_DOWNLOAD_MAX_MB" and size["secret"] is False
    assert by_key["auto_download_types"]["options"] == ["image", "video", "audio", "document"]
    assert set(size) >= {"key", "label", "kind", "default", "help", "secret", "env_var", "options", "bounds", "max_length",
                         "fields", "id_key", "readonly", "risk", "risk_when"}
    assert view["version"] and view["status"] in ("ready", "empty")
    json.dumps(view)                                              # everything in it is plain JSON


def test_a_risk_field_carries_its_warning_and_the_values_that_trigger_it(rig):
    _, service = rig
    f = {f["key"]: f for f in service.view(service.open("interface"))["fields"]}["ui_host"]

    assert f["risk"] and f["risk_when"] == ["0.0.0.0", "::"]


def test_origin_lock_and_pending_restart_come_from_the_environment_layer():
    services = build_services({"WHATSAPP_TIMEZONE": "Europe/Lisbon"})
    service = FormService(services)

    view = service.view(service.open("timezone"))

    assert view["state"]["timezone"]["origin"] == "environment" and view["state"]["timezone"]["locked"] is True
    assert view["values"]["timezone"] == "Europe/Lisbon"
    other = service.view(service.open("media"))
    assert other["state"]["auto_download"] == {"origin": "default", "locked": False, "pending": None}


def test_the_0_6_0_section_files_are_read_unchanged_and_the_secret_leaves_masked(rig):
    services, service = rig
    path = put_fixture(services, "send_policy", "send_policy.json")
    before = open(path, "rb").read()

    view = service.view(service.open("send_policy"))

    assert view["values"]["mode"] == "allowlist" and view["values"]["allow"] == ["Example Group", "15550100001"]
    assert view["values"]["override_token"] == {"set": True}
    assert TOKEN not in json.dumps(view)
    assert view["state"]["mode"]["origin"] == "file"
    assert open(path, "rb").read() == before


def test_a_read_only_section_shows_facts_and_refuses_changes(rig):
    _, service = rig
    flow = service.open("database")
    service.apply(flow, {"encryption": "off"})

    out = service.save(flow)

    assert service.view(flow)["read_only"] is True
    assert out["ok"] is False and out["errors"]


def test_only_listed_sections_open(rig):
    _, service = rig
    for bad in ("ui_auth", "ui_settings", "nope", "../etc/passwd", ""):
        with pytest.raises(KeyError):
            service.open(bad)


def test_the_listing_has_the_sections_and_the_pages_in_menu_order(rig):
    _, service = rig
    listing = service.listing()

    assert [e["id"] for e in listing][:3] == ["status", "send_policy", "monitor"]
    assert [e["id"] for e in listing][-2:] == ["activity", "account"]
    assert {e["kind"] for e in listing} == {"section", "page"}
    assert all(set(e) == {"id", "title", "icon", "order", "kind", "description", "restart", "pending"} for e in listing)
    assert "ui_auth" not in [e["id"] for e in listing]


def test_the_listing_marks_the_sections_whose_saved_change_waits_for_a_restart(rig):
    _, service = rig
    assert [e["id"] for e in service.listing() if e["pending"]] == []

    edit(service, "media", {"media_retention_days": "10"})

    marked = {e["id"]: e["pending"] for e in service.listing()}
    assert marked["media"] is True
    assert [name for name, pending in marked.items() if pending] == ["media"]       # the privacy section shares the scope


# -- raw text and saving ---------------------------------------------------------------------------
def test_raw_text_goes_through_the_one_parse_rule_and_is_saved_typed(rig):
    services, service = rig
    path = put_fixture(services, "send_policy", "send_policy.json")

    flow, out = edit(service, "send_policy", {"deny": "  first chat \n\nsecond chat\n", "mode": "denylist"})

    assert out["ok"] is True and out["errors"] == {}
    saved = read(path)
    assert saved["deny"] == ["first chat", "second chat"] and saved["mode"] == "denylist"
    assert saved["override_token"] == TOKEN                    # not mentioned: kept


def test_a_number_typed_as_text_is_read_by_the_rule_and_saved_to_the_settings_file(rig):
    services, service = rig

    _, out = edit(service, "media", {"auto_download_max_mb": " 12,5 ", "media_retention_days": "30"})

    assert out["ok"] is True and out["restarts"] == ["bridge"]
    env = read(os.path.join(os.environ["WHATSAPP_CONFIG_DIR"], "ui_settings.json"))["env"]
    assert env["WHATSAPP_AUTO_DOWNLOAD_MAX_MB"] == "12.5" and env["WHATSAPP_MEDIA_RETENTION_DAYS"] == "30"


def test_text_that_cannot_be_read_is_an_error_on_the_field_and_nothing_is_written(rig):
    services, service = rig
    path = put_fixture(services, "send_policy", "send_policy.json")
    before = open(path, "rb").read()
    flow = service.open("media")
    service.apply(flow, {"media_retention_days": "ten", "auto_download_max_mb": "99999"})

    check = service.validate(flow)
    out = service.save(flow)

    assert check["can_save"] is False and check["errors"]["media_retention_days"] == "must be a whole number"
    assert out["ok"] is False and out["errors"]["auto_download_max_mb"] == "must be at most 10240"
    assert open(path, "rb").read() == before
    assert not os.path.exists(os.path.join(os.environ["WHATSAPP_CONFIG_DIR"], "ui_settings.json"))


def test_a_key_that_is_not_a_field_is_refused(rig):
    _, service = rig
    flow = service.open("send_policy")
    service.apply(flow, {"admin": "yes"})

    assert service.validate(flow)["errors"] == {"admin": "is not a field of this section"}


def test_a_file_changed_since_the_client_loaded_it_is_a_conflict_and_is_not_overwritten(rig):
    services, service = rig
    path = put_fixture(services, "send_policy", "send_policy.json")
    loaded_version = service.view(service.open("send_policy"))["version"]
    outside = read(path)
    outside["mode"] = "denylist"
    with open(path, "w") as f:
        json.dump(outside, f)

    _, out = edit(service, "send_policy", {"deny": "mine"}, version=loaded_version)

    assert out["conflict"] is True and out["ok"] is False
    assert read(path) == outside


def test_an_unchanged_form_saves_nothing(rig):
    services, service = rig
    path = put_fixture(services, "send_policy", "send_policy.json")
    before = open(path, "rb").read()

    _, out = edit(service, "send_policy", {"mode": "allowlist"})

    assert out["ok"] is True and out["unchanged"] is True
    assert open(path, "rb").read() == before


def test_a_file_that_cannot_be_read_needs_a_confirmation_before_it_is_replaced(rig):
    services, service = rig
    path = services.registry.path_of("send_policy")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{broken")
    os.chmod(path, 0o600)

    _, refused = edit(service, "send_policy", {"mode": "denylist"})
    assert refused["needs_confirmation"] is True and refused["ok"] is False
    assert open(path).read() == "{broken"

    _, allowed = edit(service, "send_policy", {"mode": "denylist"}, confirm_replace=True)
    assert allowed["ok"] is True and read(path)["mode"] == "denylist"


def test_the_outcome_has_exactly_the_documented_keys(rig):
    _, service = rig
    _, out = edit(service, "media", {"auto_download": False})

    assert set(out) == {"ok", "unchanged", "conflict", "needs_confirmation", "errors", "general", "restarts"}


# -- secrets ---------------------------------------------------------------------------------------
def test_a_secret_can_be_replaced_kept_or_cleared(rig):
    services, service = rig
    path = put_fixture(services, "send_policy", "send_policy.json")

    edit(service, "send_policy", {"override_token": "a-new-token-value"})
    assert read(path)["override_token"] == "a-new-token-value"

    edit(service, "send_policy", {"override_token": {"set": True}, "deny": "x"})
    assert read(path)["override_token"] == "a-new-token-value"

    _, out = edit(service, "send_policy", {"override_token": {"clear": True}})
    assert out["ok"] is True and "override_token" not in read(path)


def test_a_secret_never_comes_back_not_even_after_a_save_or_a_failed_one(rig):
    services, service = rig
    put_fixture(services, "send_policy", "send_policy.json")
    flow = service.open("send_policy")
    service.apply(flow, {"override_token": "typed-new-secret-1", "mode": "nonsense"})

    shown = json.dumps([service.view(flow), service.validate(flow), service.save(flow)])

    assert "typed-new-secret-1" not in shown and TOKEN not in shown


def test_a_secret_marker_that_is_not_understood_is_refused_and_keeps_the_stored_secret(rig):
    services, service = rig
    path = put_fixture(services, "send_policy", "send_policy.json")

    _, out = edit(service, "send_policy", {"override_token": {"clear": "yes"}})

    assert out["ok"] is False and "override_token" in out["errors"]
    assert read(path)["override_token"] == TOKEN


# -- restoring defaults ----------------------------------------------------------------------------
def test_restore_takes_a_setting_back_to_its_default(rig):
    services, service = rig
    edit(service, "media", {"auto_download_max_mb": "40"})
    settings = os.path.join(os.environ["WHATSAPP_CONFIG_DIR"], "ui_settings.json")
    assert read(settings)["env"]["WHATSAPP_AUTO_DOWNLOAD_MAX_MB"] == "40"
    fresh = build_services({})
    service = FormService(fresh)

    _, out = edit(service, "media", {}, restore=["auto_download_max_mb"])

    assert out["ok"] is True
    assert "WHATSAPP_AUTO_DOWNLOAD_MAX_MB" not in read(settings).get("env", {})


def test_restoring_a_key_that_is_not_a_field_is_an_error(rig):
    _, service = rig
    flow = service.open("media")
    service.apply(flow, {}, restore=["nope", 5])

    assert service.validate(flow)["errors"] == {"nope": "is not a field of this section"}
