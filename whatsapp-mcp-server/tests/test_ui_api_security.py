"""The security layer of the API (MIG-03, AUTH-05): Host, Origin, CSRF, JSON writes, the cookie and the headers."""
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ui.api import security
from ui.api.sessions import SessionStore
from ui.api.services import Settings

CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")
BASE = "http://127.0.0.1:8765"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def build(tmp_path, settings=None, guarded=True, idle=3600.0):
    settings = settings or Settings(host="127.0.0.1", port=8765)
    clock = Clock()
    sessions = SessionStore(idle_seconds=idle, clock=clock)
    target = tmp_path / "config.json"
    target.write_text("original")

    async def read(request):
        return JSONResponse({"ok": True})

    async def write(request):
        target.write_text("changed")
        return JSONResponse({"ok": True})

    async def open_login(request):
        session = sessions.create("test")
        session.sign_in()
        response = JSONResponse({"csrf": session.csrf})
        security.set_session_cookie(response, request, settings, session)
        return response

    async def page(request):
        return PlainTextResponse("page", headers={"Cache-Control": "public, max-age=60"})

    routes = [Route("/api/data", read), Route("/api/data", write, methods=["POST", "PUT", "DELETE"]),
              Route("/api/open", read), Route("/api/login", open_login), Route("/page", page)]
    public = {("GET", "/api/open"), ("GET", "/api/login")} if guarded else None
    app = Starlette(routes=routes, middleware=[Middleware(security.SecurityMiddleware, sessions=sessions,
                                                          settings=settings, public=public)])
    return app, sessions, clock, target


def client_for(app, base=BASE):
    return TestClient(app, base_url=base)


def signed_in(app, sessions, base=BASE):
    client = client_for(app, base)
    session = sessions.create("test")
    session.sign_in()
    client.cookies.set(security.COOKIE_NAME, session.id, domain=base.split("//")[1].split(":")[0])
    return client, session


# -- Host ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("base", ["http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765"])
def test_the_loopback_names_on_the_port_are_accepted(tmp_path, base):
    app, *_ = build(tmp_path)

    assert client_for(app, base).get("/api/open").status_code == 200


@pytest.mark.parametrize("base", ["http://testserver", "http://127.0.0.1:9999", "http://evil.example:8765",
                                  "http://192.168.1.20:8765"])
def test_a_wrong_host_is_421_with_no_configuration(tmp_path, base):
    app, sessions, *_ = build(tmp_path)

    response = client_for(app, base).get("/api/open")

    assert response.status_code == 421
    assert response.json()["code"] == "bad_host"
    assert "evil" not in response.text and "192.168" not in response.text


def test_a_wrong_host_is_refused_outside_the_api_too(tmp_path):
    app, *_ = build(tmp_path)

    assert client_for(app, "http://evil.example:8765").get("/page").status_code == 421


def test_a_configured_remote_host_is_accepted_only_with_the_opt_in(tmp_path):
    allowed = Settings(host="192.168.1.20", port=8765, remote=True, startup_environ={"WHATSAPP_UI_ALLOW_REMOTE": "true"})
    app, *_ = build(tmp_path, allowed)

    assert client_for(app, "http://192.168.1.20:8765").get("/api/open").status_code == 200
    assert client_for(app, "http://127.0.0.1:8765").get("/api/open").status_code == 200
    assert client_for(app, "http://evil.example:8765").get("/api/open").status_code == 421

    closed = Settings(host="192.168.1.20", port=8765)
    app2, *_ = build(tmp_path, closed)
    assert client_for(app2, "http://192.168.1.20:8765").get("/api/open").status_code == 421


def test_a_wildcard_bind_with_the_opt_in_accepts_the_name_the_operator_reaches_it_by(tmp_path):
    wild = Settings(host="0.0.0.0", port=8765, remote=True, startup_environ={"WHATSAPP_UI_ALLOW_REMOTE": "true"})
    app, *_ = build(tmp_path, wild)

    assert client_for(app, "http://mcp.example.net:8765").get("/api/open").status_code == 200


# -- Origin, CSRF and JSON -------------------------------------------------------------------------
def test_write_without_csrf_token_is_403_and_leaves_the_file_untouched(tmp_path):
    app, sessions, _, target = build(tmp_path)
    client, _ = signed_in(app, sessions)

    response = client.post("/api/data", json={"a": 1})

    assert response.status_code == 403 and response.json()["code"] == "csrf"
    assert target.read_text() == "original"


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_every_kind_of_write_needs_the_token(tmp_path, method):
    app, sessions, _, target = build(tmp_path)
    client, session = signed_in(app, sessions)

    refused = getattr(client, method)("/api/data", headers={security.CSRF_HEADER: "wrong"})
    allowed = getattr(client, method)("/api/data", headers={security.CSRF_HEADER: session.csrf})

    assert refused.status_code == 403 and allowed.status_code == 200
    assert target.read_text() == "changed"


def test_a_token_of_another_session_is_refused(tmp_path):
    app, sessions, _, target = build(tmp_path)
    client, _ = signed_in(app, sessions)
    other = sessions.create("x")

    response = client.post("/api/data", json={}, headers={security.CSRF_HEADER: other.csrf})

    assert response.status_code == 403 and target.read_text() == "original"


def test_reads_need_no_token(tmp_path):
    app, sessions, *_ = build(tmp_path)
    client, _ = signed_in(app, sessions)

    assert client.get("/api/data").status_code == 200


def test_a_foreign_origin_is_403_even_with_the_right_token(tmp_path):
    app, sessions, _, target = build(tmp_path)
    client, session = signed_in(app, sessions)

    for origin in ("http://evil.example", "null", "http://127.0.0.1:9999"):
        response = client.post("/api/data", json={}, headers={security.CSRF_HEADER: session.csrf, "Origin": origin})
        assert response.status_code == 403 and response.json()["code"] == "origin"
    assert target.read_text() == "original"


def test_the_origin_of_the_page_itself_is_accepted(tmp_path):
    app, sessions, _, target = build(tmp_path)
    client, session = signed_in(app, sessions)

    response = client.post("/api/data", json={}, headers={security.CSRF_HEADER: session.csrf, "Origin": BASE})

    assert response.status_code == 200 and target.read_text() == "changed"


def test_a_write_with_a_body_that_is_not_json_is_403(tmp_path):
    app, sessions, _, target = build(tmp_path)
    client, session = signed_in(app, sessions)
    headers = {security.CSRF_HEADER: session.csrf}

    form = client.post("/api/data", data={"a": "1"}, headers=headers)
    plain = client.post("/api/data", content=b"a=1", headers={**headers, "Content-Type": "text/plain"})
    typed = client.post("/api/data", content=b"{}", headers={**headers, "Content-Type": "application/json; charset=utf-8"})

    assert form.status_code == 403 and plain.status_code == 403 and form.json()["code"] == "content_type"
    assert typed.status_code == 200


def test_a_write_with_no_session_at_all_is_refused_without_touching_files(tmp_path):
    app, _, _, target = build(tmp_path, guarded=False)

    response = client_for(app).post("/api/data", json={})

    assert response.status_code == 403 and target.read_text() == "original"


# -- the guard for sessions ------------------------------------------------------------------------
def test_a_guarded_path_without_a_session_is_401_for_reads_and_writes(tmp_path):
    app, sessions, _, target = build(tmp_path)
    client = client_for(app)

    read = client.get("/api/data")
    write = client.post("/api/data", json={})
    unknown = client.get("/api/does-not-exist")

    assert read.status_code == write.status_code == unknown.status_code == 401
    assert read.json()["code"] == "unauthenticated" and target.read_text() == "original"


def test_an_anonymous_session_is_not_a_signed_in_one(tmp_path):
    app, sessions, *_ = build(tmp_path)
    client = client_for(app)
    anonymous = sessions.create("x")
    client.cookies.set(security.COOKIE_NAME, anonymous.id, domain="127.0.0.1")

    assert client.get("/api/data").status_code == 401
    assert client.get("/api/open").status_code == 200


def test_the_public_routes_answer_without_a_session(tmp_path):
    app, *_ = build(tmp_path)
    client = client_for(app)

    assert client.get("/api/open").status_code == 200
    assert client.get("/page").status_code == 200       # outside the API nothing is guarded here


def test_an_idle_session_is_401_and_says_so(tmp_path):
    app, sessions, clock, _ = build(tmp_path, idle=60)
    client, _ = signed_in(app, sessions)

    clock.now += 61
    response = client.get("/api/data")

    assert response.status_code == 401 and response.json()["code"] == "session_expired"
    assert "inactivity" in response.json()["message"]


def test_a_background_request_does_not_renew_the_idle_clock(tmp_path):
    app, sessions, clock, _ = build(tmp_path, idle=60)
    client, _ = signed_in(app, sessions)

    clock.now += 40
    assert client.get("/api/data", headers={security.BACKGROUND_HEADER: "1"}).status_code == 200
    clock.now += 40                                     # 80 s since the last real use

    assert client.get("/api/data").status_code == 401


def test_a_normal_request_renews_the_idle_clock(tmp_path):
    app, sessions, clock, _ = build(tmp_path, idle=60)
    client, _ = signed_in(app, sessions)

    clock.now += 40
    assert client.get("/api/data").status_code == 200
    clock.now += 40

    assert client.get("/api/data").status_code == 200


# -- the cookie ------------------------------------------------------------------------------------
def cookie_of(response):
    return response.headers["set-cookie"]


def test_the_cookie_is_http_only_same_site_strict_and_not_secure_on_plain_http(tmp_path):
    app, *_ = build(tmp_path, guarded=False)
    client = client_for(app)
    login = client.get("/api/login")

    cookie = cookie_of(login)
    assert cookie.startswith(f"{security.COOKIE_NAME}=")
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower() and "path=/" in cookie.lower()
    assert "secure" not in cookie.lower().replace("samesite", "")


def test_the_cookie_is_secure_only_with_the_opt_in_and_a_https_proxy(tmp_path):
    opt_in = Settings(host="127.0.0.1", port=8765, startup_environ={"WHATSAPP_UI_ALLOW_REMOTE": "true"})
    app, *_ = build(tmp_path, opt_in, guarded=False)
    app_closed, *_ = build(tmp_path, None, guarded=False)

    behind_proxy = client_for(app).get("/api/login", headers={"X-Forwarded-Proto": "https"})
    plain = client_for(app).get("/api/login")
    http_proxy = client_for(app).get("/api/login", headers={"X-Forwarded-Proto": "http"})
    no_opt_in = client_for(app_closed).get("/api/login", headers={"X-Forwarded-Proto": "https"})

    def secure(response):
        return "; secure" in cookie_of(response).lower()

    assert secure(behind_proxy) and not secure(plain) and not secure(http_proxy) and not secure(no_opt_in)


def test_the_cookie_value_is_the_session_id_and_it_is_new_each_time(tmp_path):
    app, sessions, *_ = build(tmp_path, guarded=False)

    first = client_for(app).get("/api/login")
    second = client_for(app).get("/api/login")

    one, two = (r.cookies.get(security.COOKIE_NAME) for r in (first, second))
    assert one and two and one != two and len(one) >= 32


# -- headers ---------------------------------------------------------------------------------------
def test_every_response_carries_the_security_headers(tmp_path):
    app, sessions, *_ = build(tmp_path)
    client = client_for(app)
    responses = [client.get("/api/open"), client.get("/api/data"), client.get("/page"),
                 client_for(app, "http://evil.example:8765").get("/api/open")]

    for response in responses:
        assert response.headers["content-security-policy"] == CSP
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_the_api_is_never_cached_and_other_paths_keep_their_own_policy(tmp_path):
    app, sessions, *_ = build(tmp_path)
    client, session = signed_in(app, sessions)

    assert client.get("/api/data").headers["cache-control"] == "no-store"
    assert client.get("/api/open").headers["cache-control"] == "no-store"
    assert client_for(app).get("/api/data").headers["cache-control"] == "no-store"      # 401 as well
    assert client.post("/api/data", json={}).headers["cache-control"] == "no-store"      # 403 as well
    assert client.get("/page").headers["cache-control"] == "public, max-age=60"


def test_no_cors_header_is_ever_added(tmp_path):
    app, sessions, *_ = build(tmp_path)
    client, _ = signed_in(app, sessions)

    response = client.get("/api/data", headers={"Origin": "http://evil.example"})

    assert not any(h.lower().startswith("access-control-") for h in response.headers)
