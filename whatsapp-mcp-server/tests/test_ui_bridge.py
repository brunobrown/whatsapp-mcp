"""The interface's view of the bridge: its status, and the check of a webhooks file. Never an exception (OPS-01, CFG-02)."""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.core.bridge import BridgeClient, Status, Unavailable, WebhookCheck  # noqa: E402

TOKEN = "a" * 64


class Fake:
    """A bridge that answers what the test says and remembers what it was asked."""

    def __init__(self):
        self.status = 200
        self.body = b"{}"
        self.delay = 0.0
        self.requests = []


@pytest.fixture
def bridge(tmp_path):
    fake = Fake()

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            fake.requests.append({"method": self.command, "path": self.path, "auth": self.headers.get("Authorization"),
                                  "type": self.headers.get("Content-Type"), "body": self.rfile.read(length)})
            time.sleep(fake.delay)
            self.send_response(fake.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(fake.body)

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}/api"
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    (config / "api_token").write_text(TOKEN + "\n")
    yield fake
    server.shutdown()
    server.server_close()


def client(fake, timeout=2.0):
    return BridgeClient(base_url=fake.url, timeout=timeout)


def test_status_returns_what_the_bridge_says(bridge):  # OPS-01
    bridge.body = json.dumps({"connected": True, "logged_in": True, "phone": "5511", "uptime_seconds": 12}).encode()

    result = client(bridge).status()

    assert isinstance(result, Status) and result.data["connected"] is True and result.data["uptime_seconds"] == 12
    assert bridge.requests[0]["method"] == "GET" and bridge.requests[0]["path"] == "/api/status"


def test_the_token_is_read_from_disk_on_every_call(bridge, tmp_path):  # OPS-01
    c = client(bridge)
    c.status()
    (tmp_path / "config" / "api_token").write_text("b" * 64)
    c.status()

    assert [r["auth"] for r in bridge.requests] == ["Bearer " + TOKEN, "Bearer " + "b" * 64]


def test_a_refused_token_is_reported_as_unauthorized(bridge):  # OPS-01
    bridge.status = 401

    assert client(bridge).status() == Unavailable("unauthorized")


def test_a_bridge_that_is_not_running_is_unreachable(bridge):  # OPS-01
    bridge.url = "http://127.0.0.1:1/api"         # nothing listens on port 1

    assert client(bridge).status() == Unavailable("unreachable")


def test_a_bridge_that_does_not_answer_in_time_is_unreachable_and_quickly(bridge):  # OPS-01
    bridge.delay = 1.5
    started = time.monotonic()

    result = client(bridge, timeout=0.2).status()

    assert result == Unavailable("unreachable") and time.monotonic() - started < 1.2


@pytest.mark.parametrize("status, body", [(200, b"not json"), (200, b"[1]"), (500, b"{}"), (200, b"")])
def test_an_answer_that_makes_no_sense_is_unexpected(bridge, status, body):  # OPS-01
    bridge.status, bridge.body = status, body

    assert client(bridge).status() == Unavailable("unexpected")


def test_no_reason_carries_the_token_or_an_address(bridge):  # CFG-10
    for status in (401, 500):
        bridge.status = status
        reason = client(bridge).status().reason
        assert TOKEN not in reason and "127.0.0.1" not in reason
    bridge.url = "http://127.0.0.1:1/api"
    assert TOKEN not in client(bridge).status().reason


def test_a_webhooks_file_the_bridge_accepts(bridge):  # CFG-02
    bridge.body = b'{"ok": true}'
    data = {"endpoints": [{"name": "n", "url": "https://a.example", "secret": "s" * 40, "events": ["*"]}]}

    result = client(bridge).validate_webhooks(data)

    assert result == WebhookCheck(True, None)
    sent = bridge.requests[0]
    assert (sent["method"], sent["path"], sent["type"]) == ("POST", "/api/config/validate-webhooks", "application/json")
    assert json.loads(sent["body"]) == data and sent["auth"] == "Bearer " + TOKEN


def test_a_webhooks_file_the_bridge_refuses_comes_back_with_the_reason(bridge):  # CFG-02
    bridge.body = b'{"ok": false, "error": "endpoint \\"n\\": url must be http (loopback only) or https"}'

    result = client(bridge).validate_webhooks({"endpoints": []})

    assert result.ok is False and "loopback" in result.error


def test_a_bridge_without_the_endpoint_is_unsupported(bridge):  # CFG-02
    bridge.status = 404

    assert client(bridge).validate_webhooks({}) == Unavailable("unsupported")


def test_a_file_too_large_to_check_is_refused_with_a_reason(bridge):  # CFG-02
    bridge.status = 413

    result = client(bridge).validate_webhooks({})

    assert result.ok is False and "grande" in result.error


def test_validation_with_a_refused_token_is_unauthorized(bridge):  # CFG-02
    bridge.status = 401

    assert client(bridge).validate_webhooks({}) == Unavailable("unauthorized")


@pytest.mark.parametrize("body", [b"{}", b'{"ok": "yes"}', b"[]", b"nope"])
def test_a_validation_answer_without_a_clear_ok_is_unexpected(bridge, body):  # CFG-02
    bridge.body = body

    assert client(bridge).validate_webhooks({}) == Unavailable("unexpected")


def test_a_bridge_that_is_down_cannot_validate(bridge):  # CFG-02
    bridge.url = "http://127.0.0.1:1/api"

    assert client(bridge).validate_webhooks({}) == Unavailable("unreachable")
