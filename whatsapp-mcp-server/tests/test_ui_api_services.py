"""Shared services of the API (MIG-01) and the setup code announcement (AUTH-02)."""
import os
import re
import shutil

import localconfig
from ui.api import services as svc

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "ui")
CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}\b")


def put_password_file():
    os.makedirs(localconfig.config_dir(), exist_ok=True)
    target = os.path.join(localconfig.config_dir(), "ui_auth.json")
    shutil.copy(os.path.join(FIXTURES, "ui_auth.json"), target)
    os.chmod(target, 0o600)


def test_services_are_built_once_from_the_startup_environment():
    startup = {"WHATSAPP_TIMEZONE": "Europe/Lisbon"}

    services = svc.build_services(startup)
    startup["WHATSAPP_TIMEZONE"] = "Asia/Tokyo"            # later changes are not the startup environment
    startup["WHATSAPP_LOG_MESSAGES"] = "true"

    assert services.env.origin("WHATSAPP_TIMEZONE") == "environment"
    assert services.env.environ["WHATSAPP_TIMEZONE"] == "Europe/Lisbon"
    assert services.env.origin("WHATSAPP_LOG_MESSAGES") == "default"


def test_services_hold_the_registry_of_every_section_and_the_shared_parts():
    services = svc.build_services({})

    assert [s.id for s in services.registry.sections()][:3] == ["send_policy", "monitor", "webhooks"]
    assert services.signin.state() == "setup"
    assert services.auth.status() == "none"
    assert services.idle_seconds > 0
    assert services.throttle is not None and services.store is not None


def test_the_announcement_prints_the_code_once_and_the_code_works():
    services = svc.build_services({})
    lines = []

    svc.announce_setup_code(services, lines.append)

    assert len(lines) == 1
    code = CODE.search(lines[0]).group(0)
    assert services.auth.create(code, "a password with enough length").ok


def test_the_announcement_prints_nothing_when_a_password_exists():
    put_password_file()
    services = svc.build_services({})
    lines = []

    svc.announce_setup_code(services, lines.append)

    assert lines == [] and services.signin.state() == "login"


def test_a_code_asked_for_through_the_flow_goes_to_the_log_and_not_to_the_caller():
    lines = []
    services = svc.build_services({}, announce=lines.append)

    asked = services.signin.request_code("origin-a")

    assert asked.announced and len(lines) == 1 and CODE.search(lines[0])
    assert CODE.search(lines[0]).group(0) not in repr(asked)
