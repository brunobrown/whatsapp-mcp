"""Static files and the page without the SPA (MIG-10): cache headers, deep links, /api 404s, no path traversal."""
import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from ui.api.static import MISSING_NOTICE, static_handler

INDEX = "<!doctype html><html lang=\"en\"><body><div id=\"root\"></div></body></html>"


def app_for(web_dir, notice=lambda line: None):
    async def ping(request):
        return JSONResponse({"pong": True})

    return Starlette(routes=[Route("/api/ping", ping)], exception_handlers={404: static_handler(web_dir, notice)})


@pytest.fixture
def web(tmp_path):
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "assets" / "app-3f2a9c.js").write_text("console.log('app')")
    (root / "assets" / "app-77be10.css").write_text("body{margin:0}")
    (root / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (root / ".hidden").write_text("dotfile")
    (tmp_path / "secret.txt").write_text("outside the web directory")
    return root


def asgi_get(app, path, method="GET"):
    """A request whose path is exactly `path`, as a server would hand it over after decoding."""
    sent = []

    delivered = []

    async def receive():
        if not delivered:
            delivered.append(True)
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()                       # the client stays connected until the response is done

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
             "headers": [(b"host", b"testserver")], "server": ("testserver", 80), "client": ("c", 1)}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], body.decode(errors="replace")


def test_the_root_serves_the_spa_and_must_be_revalidated(web):
    response = TestClient(app_for(web)).get("/")

    assert response.status_code == 200 and response.text == INDEX
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["content-type"].startswith("text/html")


def test_hashed_assets_are_cached_for_a_year_as_immutable(web):
    client = TestClient(app_for(web))

    js, css = client.get("/assets/app-3f2a9c.js"), client.get("/assets/app-77be10.css")

    assert js.text == "console.log('app')" and css.text == "body{margin:0}"
    for response in (js, css):
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in js.headers["content-type"] and css.headers["content-type"].startswith("text/css")


def test_index_html_asked_for_by_name_is_revalidated_and_other_files_too(web):
    client = TestClient(app_for(web))

    assert client.get("/index.html").headers["cache-control"] == "no-cache"
    favicon = client.get("/favicon.svg")
    assert favicon.status_code == 200 and favicon.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/s/send_policy", "/status", "/login", "/some/deep/link", "/assets/", "/assets",
                                  "/assets/missing.js", "/no-such.png"])
def test_a_path_of_the_spa_or_a_missing_file_returns_index_html(web, path):
    response = TestClient(app_for(web)).get(path)

    assert response.status_code == 200 and response.text == INDEX
    assert response.headers["cache-control"] == "no-cache"


def test_head_works_like_get_without_a_body(web):
    response = TestClient(app_for(web)).head("/assets/app-3f2a9c.js")

    assert response.status_code == 200 and response.content == b""
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_an_unknown_api_path_is_a_json_404_never_the_spa(web):
    client = TestClient(app_for(web))

    for path in ("/api/nothing", "/api", "/api/deep/er/path"):
        response = client.get(path)
        assert response.status_code == 404 and response.json()["code"] == "not_found"
        assert INDEX not in response.text
    assert client.get("/api/ping").json() == {"pong": True}


def test_a_write_to_a_path_nobody_serves_is_a_404_not_the_page(web):
    client = TestClient(app_for(web))

    assert client.post("/nothing", json={}).status_code == 404
    assert client.delete("/index.html").status_code == 404


@pytest.mark.parametrize("path", ["/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt",
                                  "/..\\secret.txt", "/assets/../index.html/../../secret.txt", "//etc/passwd", "/\x00/../secret.txt"])
def test_a_path_with_dots_never_leaves_the_web_directory(web, path):
    status, body = asgi_get(app_for(web), path)

    assert "outside the web directory" not in body and "root:" not in body
    assert status in (200, 404)


def test_a_dotfile_and_a_symlink_out_of_the_directory_are_not_served(web, tmp_path):
    (web / "link.txt").symlink_to(tmp_path / "secret.txt")
    client = TestClient(app_for(web))

    for path in ("/.hidden", "/link.txt"):
        response = client.get(path)
        assert "dotfile" not in response.text and "outside the web directory" not in response.text
        assert response.text == INDEX


# -- without ui/web --------------------------------------------------------------------------------
def test_server_starts_and_explains_when_web_dir_is_missing(tmp_path):
    lines = []
    client = TestClient(app_for(tmp_path / "does-not-exist", lines.append))

    root = client.get("/")
    api = client.get("/api/ping")
    unknown = client.get("/api/nothing")

    assert root.status_code == 200 and root.headers["content-type"].startswith("text/html")
    assert "whatsapp-mcp-ui-dist.tar.gz" in root.text and "npm ci" in root.text and "npm run build" in root.text
    assert "<script" not in root.text.lower() and "onload" not in root.text.lower()
    assert "tar.gz" in lines[0] and len(lines) == 1                 # the log says it once
    assert api.json() == {"pong": True}                              # the API still answers
    assert unknown.status_code == 404 and unknown.json()["code"] == "not_found"


def test_an_empty_web_directory_is_the_same_as_a_missing_one(tmp_path):
    (tmp_path / "web").mkdir()
    lines = []
    client = TestClient(app_for(tmp_path / "web", lines.append))

    assert "whatsapp-mcp-ui-dist.tar.gz" in client.get("/").text
    assert lines == [MISSING_NOTICE]


def test_no_web_directory_given_at_all_also_explains(tmp_path):
    client = TestClient(app_for(None))

    assert client.get("/").status_code == 200 and "npm run build" in client.get("/").text


def test_a_deep_link_without_the_spa_is_a_404_with_the_same_instructions(tmp_path):
    client = TestClient(app_for(tmp_path / "missing"))

    response = client.get("/s/send_policy")

    assert response.status_code == 404 and "npm run build" in response.text


def test_the_notice_is_silent_when_the_spa_is_there(web):
    lines = []

    app_for(web, lines.append)

    assert lines == []


def test_the_spa_appearing_later_is_served_without_a_restart(tmp_path):
    root = tmp_path / "late"
    client = TestClient(app_for(root))
    assert "npm run build" in client.get("/").text

    root.mkdir()
    (root / "index.html").write_text(INDEX)

    assert client.get("/").text == INDEX
