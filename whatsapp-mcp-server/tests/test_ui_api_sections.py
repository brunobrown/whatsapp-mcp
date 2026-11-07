"""Section routes (MIG-03, MIG-04, CFG-01..03 over HTTP): schema, check, save, and every refusal."""
import json
import os

import pytest

import monitor
from ui.api.forms import FormService
from ui.api.routes_sections import section_routes
from ui.sections import webhooks as webhooks_section
from ui.security.bridge import WebhookCheck
from ui_api_rig import Rig, install_fixture

TOKEN = "example-override-token-0000"


class FakeBridge:
    def validate_webhooks(self, text):
        return WebhookCheck(True, True)


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(webhooks_section, "CLIENT", FakeBridge())
    return Rig(lambda r: section_routes(FormService(r.services)))


def read(path):
    with open(path) as f:
        return json.load(f)


def settings_file():
    return os.path.join(os.environ["WHATSAPP_CONFIG_DIR"], "ui_settings.json")


# -- the menu and the schema -----------------------------------------------------------------------
def test_the_section_list_needs_a_session(rig):
    assert rig.anonymous().get("/api/sections").status_code == 401


def test_the_menu_lists_sections_and_pages(rig):
    client, _ = rig.signed_in()

    listing = client.get("/api/sections").json()

    ids = [e["id"] for e in listing]
    assert ids[0] == "status" and "send_policy" in ids and "database" in ids and ids[-1] == "account"
    assert {e["kind"] for e in listing} == {"section", "page"}
    assert "ui_auth" not in ids and "ui_settings" not in ids


def test_section_schema_carries_defaults_origin_lock_and_restart():
    rig = Rig(lambda r: section_routes(FormService(r.services)),
              startup_environ={"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "50"})
    client, headers = rig.signed_in()

    body = client.get("/api/sections/media").json()

    fields = {f["key"]: f for f in body["fields"]}
    assert fields["auto_download_max_mb"]["default"] == 25 and fields["auto_download_max_mb"]["bounds"] == [0.01, 10240]
    assert fields["auto_download_types"]["default"] == ["image", "document", "audio"]
    assert body["restart"] == "bridge"
    locked = body["state"]["auto_download_max_mb"]
    assert locked["origin"] == "environment" and locked["locked"] is True
    assert body["values"]["auto_download_max_mb"] == 50
    assert body["state"]["auto_download"] == {"origin": "default", "locked": False, "pending": None}

    saved = client.put("/api/sections/media", json={"version": body["version"], "changes": {"media_retention_days": "30"}},
                       headers=headers)
    assert saved.status_code == 200 and saved.json()["restarts"] == ["bridge"]
    again = client.get("/api/sections/media").json()
    assert again["state"]["media_retention_days"] == {"origin": "file", "locked": False, "pending": "bridge"}
    assert again["values"]["media_retention_days"] == 30


def test_a_section_file_of_0_6_0_is_read_unchanged_with_its_secret_masked(rig):
    path = install_fixture(rig.services, "send_policy", "send_policy.json")
    before = open(path, "rb").read()
    client, _ = rig.signed_in()

    response = client.get("/api/sections/send_policy")

    body = response.json()
    assert body["values"]["mode"] == "allowlist" and body["values"]["override_token"] == {"set": True}
    assert TOKEN not in response.text
    assert open(path, "rb").read() == before


def test_every_0_6_0_fixture_section_opens(rig, tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "monitor_profiles.json"))
    for section, name in (("send_policy", "send_policy.json"), ("monitor", "monitor_profiles.json"),
                          ("webhooks", "webhooks.json"), ("channel", "channel.json")):
        install_fixture(rig.services, section, name)
    client, _ = rig.signed_in()

    for section in ("send_policy", "monitor", "webhooks", "channel"):
        body = client.get(f"/api/sections/{section}").json()
        assert body["status"] == "ready", (section, body["banner"])
    assert client.get("/api/sections/monitor").json()["values"]["profiles"][0]["name"] == "assistant"


@pytest.mark.parametrize("section", ["nope", "ui_auth", "ui_settings", "status", "..%2Fsecrets", "send_policy%00"])
def test_an_unknown_section_is_404_for_every_verb(rig, section):
    client, headers = rig.signed_in()
    body = {"version": "x", "changes": {}}

    assert client.get(f"/api/sections/{section}").status_code == 404
    assert client.post(f"/api/sections/{section}/validate", json=body, headers=headers).status_code == 404
    assert client.put(f"/api/sections/{section}", json=body, headers=headers).status_code == 404


def test_the_unknown_section_answer_names_no_path(rig):
    client, _ = rig.signed_in()

    body = client.get("/api/sections/ui_auth").json()

    assert body == {"code": "not_found", "message": "There is no such section."}


# -- validate --------------------------------------------------------------------------------------
def test_validate_reports_field_errors_and_writes_nothing(rig):
    client, headers = rig.signed_in()
    version = client.get("/api/sections/media").json()["version"]

    bad = client.post("/api/sections/media/validate", headers=headers,
                      json={"version": version, "changes": {"media_retention_days": "ten", "auto_download_max_mb": "5"}})
    good = client.post("/api/sections/media/validate", headers=headers,
                       json={"version": version, "changes": {"media_retention_days": "10"}})

    assert bad.status_code == 200
    assert bad.json() == {"errors": {"media_retention_days": "must be a whole number"}, "general": [], "can_save": False}
    assert good.json() == {"errors": {}, "general": [], "can_save": True}
    assert not os.path.exists(settings_file())


def test_validate_names_a_record_field_with_a_dotted_key(rig, tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "monitor_profiles.json"))
    client, headers = rig.signed_in()
    version = client.get("/api/sections/monitor").json()["version"]

    out = client.post("/api/sections/monitor/validate", headers=headers, json={
        "version": version, "changes": {"profiles": [{"name": "a", "targets": ["not a number"]}]}}).json()

    assert out["can_save"] is False and "phone number" in out["errors"]["profiles.0.targets"]


# -- save ------------------------------------------------------------------------------------------
def test_a_save_writes_the_file_and_the_next_read_shows_it(rig):
    path = install_fixture(rig.services, "send_policy", "send_policy.json")
    client, headers = rig.signed_in()
    version = client.get("/api/sections/send_policy").json()["version"]

    out = client.put("/api/sections/send_policy", headers=headers,
                     json={"version": version, "changes": {"deny": "one\ntwo", "override_token": {"set": True}}})

    assert out.status_code == 200 and out.json()["ok"] is True and out.json()["errors"] == {}
    assert read(path)["deny"] == ["one", "two"] and read(path)["override_token"] == TOKEN
    assert client.get("/api/sections/send_policy").json()["values"]["deny"] == ["one", "two"]


def test_nothing_changed_is_ok_and_unchanged(rig):
    install_fixture(rig.services, "send_policy", "send_policy.json")
    client, headers = rig.signed_in()
    version = client.get("/api/sections/send_policy").json()["version"]

    out = client.put("/api/sections/send_policy", headers=headers, json={"version": version, "changes": {}})

    assert out.status_code == 200 and out.json()["unchanged"] is True


def test_a_refused_value_is_422_with_the_field_errors_and_the_file_is_not_written(rig):
    path = install_fixture(rig.services, "send_policy", "send_policy.json")
    before = open(path, "rb").read()
    client, headers = rig.signed_in()
    version = client.get("/api/sections/send_policy").json()["version"]

    out = client.put("/api/sections/send_policy", headers=headers,
                     json={"version": version, "changes": {"mode": "everyone"}})

    assert out.status_code == 422 and out.json()["ok"] is False
    assert out.json()["errors"]["mode"].startswith("must be one of")
    assert open(path, "rb").read() == before


def test_a_file_changed_after_it_was_loaded_is_409_conflict_and_is_kept_as_it_is(rig):
    path = install_fixture(rig.services, "send_policy", "send_policy.json")
    client, headers = rig.signed_in()
    version = client.get("/api/sections/send_policy").json()["version"]
    changed = read(path)
    changed["mode"] = "denylist"
    with open(path, "w") as f:
        json.dump(changed, f)

    out = client.put("/api/sections/send_policy", headers=headers, json={"version": version, "changes": {"deny": "mine"}})

    assert out.status_code == 409 and out.json()["conflict"] is True and out.json()["ok"] is False
    assert read(path) == changed


def test_an_unreadable_file_needs_a_confirmation_to_be_replaced(rig):
    path = rig.services.registry.path_of("send_policy")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{broken")
    os.chmod(path, 0o600)
    client, headers = rig.signed_in()
    body = client.get("/api/sections/send_policy").json()
    assert body["status"] == "error" and body["banner"]
    request = {"version": body["version"], "changes": {"mode": "denylist"}}

    refused = client.put("/api/sections/send_policy", headers=headers, json=request)
    assert refused.status_code == 409 and refused.json()["needs_confirmation"] is True
    assert open(path).read() == "{broken"

    allowed = client.put("/api/sections/send_policy", headers=headers, json={**request, "confirm_replace": True})
    assert allowed.status_code == 200 and read(path)["mode"] == "denylist"


def test_restore_puts_a_setting_back_to_its_default(rig):
    client, headers = rig.signed_in()
    version = client.get("/api/sections/media").json()["version"]
    client.put("/api/sections/media", headers=headers, json={"version": version, "changes": {"media_retention_days": "9"}})
    assert read(settings_file())["env"]["WHATSAPP_MEDIA_RETENTION_DAYS"] == "9"
    version = client.get("/api/sections/media").json()["version"]

    out = client.put("/api/sections/media", headers=headers, json={"version": version, "changes": {},
                                                                    "restore": ["media_retention_days"]})

    assert out.status_code == 200
    assert "WHATSAPP_MEDIA_RETENTION_DAYS" not in read(settings_file()).get("env", {})


def test_a_locked_setting_cannot_be_changed_over_http():
    rig = Rig(lambda r: section_routes(FormService(r.services)), startup_environ={"WHATSAPP_TIMEZONE": "Europe/Lisbon"})
    client, headers = rig.signed_in()
    version = client.get("/api/sections/timezone").json()["version"]

    out = client.put("/api/sections/timezone", headers=headers, json={"version": version, "changes": {"timezone": "UTC"}})

    assert out.status_code == 422 and "WHATSAPP_TIMEZONE" in out.json()["errors"]["timezone"]
    assert not os.path.exists(settings_file())


def test_a_read_only_section_is_422_on_save(rig):
    client, headers = rig.signed_in()
    version = client.get("/api/sections/database").json()["version"]

    out = client.put("/api/sections/database", headers=headers, json={"version": version, "changes": {"key_source": "x"}})

    assert out.status_code == 422 and out.json()["ok"] is False


@pytest.mark.parametrize("body", [{}, {"version": 3, "changes": {}}, {"version": "v", "changes": []},
                                  {"version": "v", "changes": {}, "restore": "all"},
                                  {"version": "v", "changes": {}, "confirm_replace": "yes"}, [1], "x"])
def test_a_malformed_request_is_400_and_changes_nothing(rig, body):
    client, headers = rig.signed_in()

    for send in (lambda: client.post("/api/sections/media/validate", json=body, headers=headers),
                 lambda: client.put("/api/sections/media", json=body, headers=headers)):
        out = send()
        assert out.status_code == 400 and out.json()["code"] == "bad_request"
    assert not os.path.exists(settings_file())


# -- the refusals of the layer around the routes ---------------------------------------------------
def test_write_without_csrf_is_403_and_leaves_files_untouched(rig):
    path = install_fixture(rig.services, "send_policy", "send_policy.json")
    before = open(path, "rb").read()
    client, headers = rig.signed_in()
    version = client.get("/api/sections/send_policy").json()["version"]
    body = {"version": version, "changes": {"mode": "denylist"}}

    without = client.put("/api/sections/send_policy", json=body)
    wrong = client.put("/api/sections/send_policy", json=body, headers={"X-CSRF-Token": "x" * 43})
    validate = client.post("/api/sections/send_policy/validate", json=body)

    assert without.status_code == wrong.status_code == validate.status_code == 403
    assert open(path, "rb").read() == before
    assert not os.path.exists(settings_file())
    assert client.put("/api/sections/send_policy", json=body, headers=headers).status_code == 200


def test_without_a_session_nothing_is_read_or_written(rig):
    path = install_fixture(rig.services, "send_policy", "send_policy.json")
    before = open(path, "rb").read()
    anon = rig.anonymous()

    assert anon.get("/api/sections/send_policy").status_code == 401
    assert anon.put("/api/sections/send_policy", json={"version": "x", "changes": {"mode": "denylist"}}).status_code == 401
    assert open(path, "rb").read() == before
