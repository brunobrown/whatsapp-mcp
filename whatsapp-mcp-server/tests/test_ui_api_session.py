"""Session and account routes (AUTH-01, AUTH-02, AUTH-04, AUTH-05, AUTH-06, AUTH-07 over HTTP)."""
import os
import re
import shutil

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient

import localconfig
from ui.api import security
from ui.api.routes_session import PUBLIC, session_routes
from ui.api.services import Settings, build_services
from ui.api.sessions import SessionStore

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "ui")
FIXTURE_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a brand new passphrase"
FAILED = "Sign-in failed. Check the details and try again."
BASE = "http://127.0.0.1:8765"
CODE = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def install_password_fixture():
    os.makedirs(localconfig.config_dir(), exist_ok=True)
    target = os.path.join(localconfig.config_dir(), "ui_auth.json")
    shutil.copy(os.path.join(FIXTURES, "ui_auth.json"), target)
    os.chmod(target, 0o600)


class Rig:
    def __init__(self, idle=3600.0):
        self.log = []
        self.clock = Clock()
        self.settings = Settings(host="127.0.0.1", port=8765)
        self.services = build_services({}, announce=self.log.append)
        self.sessions = SessionStore(idle_seconds=idle, clock=self.clock)
        self.app = Starlette(routes=session_routes(self.services, self.sessions, self.settings),
                             middleware=[Middleware(security.SecurityMiddleware, sessions=self.sessions,
                                                    settings=self.settings, public=PUBLIC)])

    def client(self):
        return TestClient(self.app, base_url=BASE)


def start(client):
    """GET /api/session and return (body, headers carrying the token)."""
    body = client.get("/api/session").json()
    return body, {security.CSRF_HEADER: body["csrf"]}


def sign_in(client, password=FIXTURE_PASSWORD):
    body, headers = start(client)
    out = client.post("/api/session/login", json={"password": password}, headers=headers)
    assert out.status_code == 200 and out.json()["ok"], out.text
    return {security.CSRF_HEADER: out.json()["csrf"]}


@pytest.fixture
def rig():
    return Rig()


@pytest.fixture
def rig_with_password():
    install_password_fixture()
    return Rig()


# -- GET /api/session ------------------------------------------------------------------------------
def test_the_state_without_a_password_is_setup_and_carries_nothing_else(rig):
    client = rig.client()

    response = client.get("/api/session")

    assert response.status_code == 200
    assert set(response.json()) == {"state", "csrf"} and response.json()["state"] == "setup"
    assert "httponly" in response.headers["set-cookie"].lower()


def test_the_state_with_a_password_is_login(rig_with_password):
    assert rig_with_password.client().get("/api/session").json()["state"] == "login"


def test_the_state_with_an_unusable_password_file_is_broken_and_says_so(rig):
    os.makedirs(localconfig.config_dir(), exist_ok=True)
    with open(os.path.join(localconfig.config_dir(), "ui_auth.json"), "w") as f:
        f.write("not json")

    body = rig.client().get("/api/session").json()

    assert body["state"] == "broken" and "ui_auth.json" in body["message"]


def test_the_same_cookie_keeps_the_same_anonymous_session(rig):
    client = rig.client()

    first = client.get("/api/session").json()
    second = client.get("/api/session").json()

    assert first["csrf"] == second["csrf"] and rig.sessions.count() == 1


# -- the setup code and the first password ---------------------------------------------------------
def test_the_setup_code_goes_to_the_log_and_never_into_a_response(rig):
    client = rig.client()
    _, headers = start(client)

    response = client.post("/api/session/code", json={}, headers=headers)

    assert response.status_code == 200 and response.json()["announced"] is True
    assert len(rig.log) == 1 and CODE.fullmatch(rig.log[0])
    assert rig.log[0] not in response.text and rig.log[0].replace("-", "") not in response.text


def test_asking_for_a_code_needs_the_csrf_token(rig):
    client = rig.client()
    start(client)

    assert client.post("/api/session/code", json={}).status_code == 403
    assert rig.log == []


def test_a_first_password_is_created_with_the_code_and_the_session_is_signed_in(rig):
    client = rig.client()
    body, headers = start(client)
    old_cookie = client.cookies.get(security.COOKIE_NAME)
    client.post("/api/session/code", json={}, headers=headers)

    out = client.post("/api/session/setup", headers=headers,
                      json={"code": rig.log[0], "password": NEW_PASSWORD, "confirm": NEW_PASSWORD})

    assert out.status_code == 200 and out.json()["ok"] is True
    assert out.json()["csrf"] and out.json()["csrf"] != body["csrf"]
    assert client.cookies.get(security.COOKIE_NAME) != old_cookie            # a new id after creating the password
    assert rig.log[0] not in out.text
    state = client.get("/api/session").json()
    assert state["state"] == "authenticated" and state["idle_remaining"] > 0
    assert rig.services.auth.verify(NEW_PASSWORD)


@pytest.mark.parametrize("password, confirm, fragment", [
    ("a brand new passphrase", "another passphrase!!", "do not match"),
    ("too short", "too short", "at least 12"),
])
def test_a_refused_password_says_why_and_signs_nobody_in(rig, password, confirm, fragment):
    client = rig.client()
    _, headers = start(client)
    client.post("/api/session/code", json={}, headers=headers)

    out = client.post("/api/session/setup", headers=headers,
                      json={"code": rig.log[0], "password": password, "confirm": confirm})

    assert out.status_code == 200 and out.json()["ok"] is False and fragment in out.json()["message"]
    assert client.get("/api/session").json()["state"] == "setup"


def test_a_wrong_code_is_refused_without_naming_the_cause(rig):
    client = rig.client()
    _, headers = start(client)
    client.post("/api/session/code", json={}, headers=headers)

    out = client.post("/api/session/setup", headers=headers,
                      json={"code": "WRONG-CODE-0000", "password": NEW_PASSWORD, "confirm": NEW_PASSWORD})

    assert out.json() == {"ok": False, "message": FAILED}
    assert rig.services.auth.status() == "none"


# -- sign-in (the 0.6.0 fixture) -------------------------------------------------------------------
def test_the_password_of_the_0_6_0_fixture_signs_in_and_changes_the_cookie(rig_with_password):
    client = rig_with_password.client()
    body, headers = start(client)
    old_cookie = client.cookies.get(security.COOKIE_NAME)

    out = client.post("/api/session/login", json={"password": FIXTURE_PASSWORD}, headers=headers)

    assert out.status_code == 200 and out.json()["ok"] is True and out.json()["csrf"] != body["csrf"]
    assert client.cookies.get(security.COOKIE_NAME) != old_cookie
    assert client.get("/api/session").json()["state"] == "authenticated"


def test_the_old_cookie_is_worthless_after_signing_in(rig_with_password):
    rig = rig_with_password
    client = rig.client()
    _, headers = start(client)
    old_cookie = client.cookies.get(security.COOKIE_NAME)
    client.post("/api/session/login", json={"password": FIXTURE_PASSWORD}, headers=headers)

    attacker = rig.client()
    attacker.cookies.set(security.COOKIE_NAME, old_cookie, domain="127.0.0.1")

    assert attacker.get("/api/session").json()["state"] == "login"


def test_a_wrong_password_gets_a_generic_message_then_a_wait(rig_with_password):
    client = rig_with_password.client()
    _, headers = start(client)

    first = client.post("/api/session/login", json={"password": "wrong password here"}, headers=headers)
    second = client.post("/api/session/login", json={"password": FIXTURE_PASSWORD}, headers=headers)

    assert first.status_code == 200 and first.json() == {"ok": False, "message": FAILED}
    assert second.status_code == 429                           # even the right password waits its turn
    assert second.json()["ok"] is False and second.json()["wait"] >= 1
    assert second.headers["retry-after"] == str(second.json()["wait"])


def test_signing_in_before_a_password_exists_points_to_the_setup_code(rig):
    client = rig.client()
    _, headers = start(client)

    out = client.post("/api/session/login", json={"password": "anything at all"}, headers=headers)

    assert out.json()["ok"] is False and "setup code" in out.json()["message"]


@pytest.mark.parametrize("path", ["/api/session/login", "/api/session/setup", "/api/account/password"])
@pytest.mark.parametrize("raw", [b"{not json", b"[1, 2]", b"\"text\""])
def test_a_body_that_is_not_a_json_object_is_a_400_and_signs_nobody_in(rig_with_password, path, raw):
    client = rig_with_password.client()
    headers = sign_in(client) if path == "/api/account/password" else start(client)[1]

    out = client.post(path, content=raw, headers={**headers, "Content-Type": "application/json"})

    assert out.status_code == 400 and out.json()["code"] == "bad_request"


# -- sign-out and idle -----------------------------------------------------------------------------
def test_sign_out_ends_the_session_and_clears_the_cookie(rig_with_password):
    client = rig_with_password.client()
    headers = sign_in(client)
    cookie = client.cookies.get(security.COOKIE_NAME)

    out = client.delete("/api/session", headers=headers)

    assert out.status_code == 200 and out.json() == {"ok": True}
    assert 'max-age=0' in out.headers["set-cookie"].lower() or "expires=" in out.headers["set-cookie"].lower()
    stale = rig_with_password.client()
    stale.cookies.set(security.COOKIE_NAME, cookie, domain="127.0.0.1")
    assert stale.get("/api/ops/status").status_code == 401
    assert stale.delete("/api/session", headers=headers).status_code == 401


def test_sign_out_without_a_session_is_401(rig_with_password):
    assert rig_with_password.client().delete("/api/session").status_code == 401


def test_a_session_idle_past_the_limit_is_back_at_login_with_the_reason():
    install_password_fixture()
    rig = Rig(idle=60)
    client = rig.client()
    sign_in(client)

    rig.clock.now += 61
    body = client.get("/api/session").json()

    assert body["state"] == "login" and "inactivity" in body["message"]


def test_a_background_poll_does_not_renew_the_idle_clock():
    install_password_fixture()
    rig = Rig(idle=60)
    client = rig.client()
    sign_in(client)

    rig.clock.now += 40
    polled = client.get("/api/session", headers={security.BACKGROUND_HEADER: "1"}).json()
    rig.clock.now += 40
    after = client.get("/api/session").json()

    assert polled["state"] == "authenticated" and polled["idle_remaining"] == 20
    assert after["state"] == "login"


def test_a_normal_request_renews_the_idle_clock():
    install_password_fixture()
    rig = Rig(idle=60)
    client = rig.client()
    sign_in(client)

    rig.clock.now += 40
    client.get("/api/session")
    rig.clock.now += 40

    assert client.get("/api/session").json()["state"] == "authenticated"


# -- changing the password -------------------------------------------------------------------------
def test_changing_the_password_asks_for_the_current_one(rig_with_password):
    rig = rig_with_password
    client = rig.client()
    headers = sign_in(client)

    out = client.post("/api/account/password", headers=headers,
                      json={"current": FIXTURE_PASSWORD, "new": NEW_PASSWORD, "confirm": NEW_PASSWORD})

    assert out.status_code == 200 and out.json() == {"ok": True, "message": "Password changed."}
    assert rig.services.auth.verify(NEW_PASSWORD) and not rig.services.auth.verify(FIXTURE_PASSWORD)


def test_a_wrong_current_password_changes_nothing(rig_with_password):
    rig = rig_with_password
    client = rig.client()
    headers = sign_in(client)

    out = client.post("/api/account/password", headers=headers,
                      json={"current": "not the current one", "new": NEW_PASSWORD, "confirm": NEW_PASSWORD})

    assert out.json()["ok"] is False
    assert rig.services.auth.verify(FIXTURE_PASSWORD)


def test_a_short_new_password_and_a_mismatch_are_refused(rig_with_password):
    client = rig_with_password.client()
    headers = sign_in(client)

    short = client.post("/api/account/password", headers=headers,
                        json={"current": FIXTURE_PASSWORD, "new": "short", "confirm": "short"})
    mismatch = client.post("/api/account/password", headers=headers,
                           json={"current": FIXTURE_PASSWORD, "new": NEW_PASSWORD, "confirm": NEW_PASSWORD + "x"})

    assert short.json()["ok"] is False and "12" in short.json()["message"]
    assert mismatch.json()["ok"] is False and "match" in mismatch.json()["message"]


def test_the_password_change_needs_a_session_and_the_token(rig_with_password):
    rig = rig_with_password
    anon = rig.client()
    body = {"current": FIXTURE_PASSWORD, "new": NEW_PASSWORD, "confirm": NEW_PASSWORD}
    assert anon.post("/api/account/password", json=body).status_code == 401

    client = rig.client()
    sign_in(client)
    assert client.post("/api/account/password", json=body).status_code == 403
    assert rig.services.auth.verify(FIXTURE_PASSWORD)
