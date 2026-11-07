"""The assembled app (MIG-01, MIG-02, MIG-03): one port for the SPA and the API, and 401 for everything else."""
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from ui.api import security
from ui.api.app import create_app
from ui.api.services import Settings, build_services
from ui_api_rig import PASSWORD, install_fixture, install_password

INDEX = "<!doctype html><html lang=\"en\"><body><div id=\"root\">spa</div></body></html>"
TOKEN = "example-override-token-0000"
OPEN = {("GET", "/api/session"), ("POST", "/api/session/code"), ("POST", "/api/session/setup"),
        ("POST", "/api/session/login")}


@pytest.fixture
def web(tmp_path):
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text(INDEX)
    return root


def build(web, port=8765):
    install_password()
    settings = Settings(host="127.0.0.1", port=port)
    services = build_services({})
    return create_app(services, settings, web, notice=lambda line: None), services, settings


def client_for(app, port=8765):
    return TestClient(app, base_url=f"http://127.0.0.1:{port}")


def sign_in(client):
    token = client.get("/api/session").json()["csrf"]
    out = client.post("/api/session/login", json={"password": PASSWORD}, headers={security.CSRF_HEADER: token})
    assert out.json()["ok"], out.text
    return {security.CSRF_HEADER: out.json()["csrf"]}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_root_serves_spa_and_api_on_one_port(web):
    port = free_port()
    if port < 1024:
        pytest.skip("ephemeral port below 1024")
    app, _, settings = build(web, port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                                           server_header=False, proxy_headers=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started
        page = httpx.get(f"http://127.0.0.1:{port}/")
        deep = httpx.get(f"http://127.0.0.1:{port}/s/send_policy")
        api = httpx.get(f"http://127.0.0.1:{port}/api/session")
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    assert page.status_code == 200 and "spa" in page.text
    assert deep.status_code == 200 and "spa" in deep.text
    assert api.status_code == 200 and api.json()["state"] in ("setup", "login") and "csrf" in api.json()
    assert api.headers["cache-control"] == "no-store" and "spa" not in api.text


def test_api_without_session_is_401_with_no_config_body(web):
    app, services, _ = build(web)
    path = install_fixture(services, "send_policy", "send_policy.json")
    client = client_for(app)

    for url in ("/api/sections", "/api/sections/send_policy", "/api/sections/ui_auth", "/api/ops/status",
                "/api/ops/counters", "/api/ops/drafts", "/api/ops/audit/send", "/api/anything-else"):
        response = client.get(url)
        assert response.status_code == 401, url
        assert set(response.json()) == {"code", "message"} and response.json()["code"] == "unauthenticated"
        for leaked in (TOKEN, "Example Group", "allowlist", "send_policy", "Send policy", path):
            assert leaked not in response.text, (url, leaked)
    for method, url in (("put", "/api/sections/send_policy"), ("post", "/api/sections/send_policy/validate"),
                        ("post", "/api/account/password"), ("delete", "/api/session")):
        assert client.request(method.upper(), url, json={}).status_code == 401, url


def test_the_open_routes_are_exactly_session_state_code_setup_and_login(web):
    app, *_ = build(web)
    answered = set()
    for route in app.routes:
        if not route.path.startswith("/api"):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            url = route.path.replace("{section_id}", "media").replace("{kind}", "send")
            response = client_for(app).request(method, url)       # no cookie at all
            if response.status_code != 401:
                answered.add((method, route.path))
    # a write with no session and no token is refused with 403 (CSRF), so only the open GET shows here; check the writes
    # of the open routes with a fresh anonymous session
    client = client_for(app)
    token = client.get("/api/session").json()["csrf"]
    for method, url in sorted(OPEN - {("GET", "/api/session")}):
        response = client.request(method, url, json={}, headers={security.CSRF_HEADER: token})
        assert response.status_code != 401, url
        answered.add((method, url))
    assert answered == OPEN


def test_an_anonymous_session_is_still_401_everywhere_but_the_open_routes(web):
    app, *_ = build(web)
    client = client_for(app)
    token = client.get("/api/session").json()["csrf"]
    headers = {security.CSRF_HEADER: token}

    assert client.get("/api/sections").status_code == 401
    assert client.put("/api/sections/media", json={"version": "x", "changes": {}}, headers=headers).status_code == 401
    assert client.post("/api/account/password", json={}, headers=headers).status_code == 401


def test_sign_in_opens_the_api_and_sign_out_closes_it(web):
    app, *_ = build(web)
    client = client_for(app)
    headers = sign_in(client)

    assert client.get("/api/sections").status_code == 200
    assert client.get("/api/sections/media").status_code == 200
    assert client.delete("/api/session", headers=headers).status_code == 200
    assert client.get("/api/sections").status_code == 401


def test_a_write_over_the_whole_app_needs_the_token_and_changes_nothing_without_it(web):
    app, services, _ = build(web)
    path = install_fixture(services, "send_policy", "send_policy.json")
    before = open(path, "rb").read()
    client = client_for(app)
    headers = sign_in(client)
    version = client.get("/api/sections/send_policy").json()["version"]
    body = {"version": version, "changes": {"mode": "denylist"}}

    assert client.put("/api/sections/send_policy", json=body).status_code == 403
    assert open(path, "rb").read() == before
    assert client.put("/api/sections/send_policy", json=body, headers=headers).status_code == 200
    assert open(path, "rb").read() != before


def test_every_response_has_the_headers_and_a_wrong_host_is_421(web):
    app, *_ = build(web)
    client = client_for(app)

    for url in ("/", "/api/session", "/api/nothing", "/s/anything"):
        response = client.get(url)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'self'" in response.headers["content-security-policy"]
    assert TestClient(app, base_url="http://evil.example:8765").get("/").status_code == 421


def test_an_unknown_api_path_after_sign_in_is_a_json_404_and_a_wrong_verb_is_405(web):
    app, *_ = build(web)
    client = client_for(app)
    headers = sign_in(client)

    missing = client.get("/api/nothing")
    wrong = client.post("/api/ops/status", json={}, headers=headers)

    assert missing.status_code == 404 and missing.json()["code"] == "not_found"
    assert wrong.status_code == 405


def test_the_app_starts_without_the_spa_and_the_api_still_answers(tmp_path):
    install_password()
    lines = []
    app = create_app(build_services({}), Settings(host="127.0.0.1", port=8765), tmp_path / "none", notice=lines.append)
    client = client_for(app)

    assert "npm run build" in client.get("/").text
    assert client.get("/api/session").json()["state"] == "login"
    assert len(lines) == 1
