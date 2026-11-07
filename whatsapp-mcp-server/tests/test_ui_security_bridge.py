"""Bridge client (OPS-01): always a result, never an exception; no token and no address in a reason."""
import os

import pytest
import requests

import localconfig
import whatsapp
from ui.security.bridge import DEFAULT_BASE_URL, BridgeClient

TOKEN = "ab" * 32


class Reply:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code, self._body, self._bad = status, body, bad_json

    def json(self):
        if self._bad:
            raise ValueError("no json")
        return self._body


def write_token(value):
    os.makedirs(localconfig.config_dir(), exist_ok=True)
    with open(os.path.join(localconfig.config_dir(), "api_token"), "w") as f:
        f.write(value)


class Calls(list):
    state = None


@pytest.fixture
def calls(monkeypatch):
    seen = Calls()
    state = {"reply": Reply(200, {"connected": True}), "raise": None}

    def fake(method):
        def call(url, **kw):
            seen.append((method, url, kw))
            if state["raise"]:
                raise state["raise"]
            return state["reply"]
        return call

    monkeypatch.setattr(requests, "get", fake("GET"))
    monkeypatch.setattr(requests, "post", fake("POST"))
    seen.state = state
    return seen


def test_the_default_address_is_the_one_the_server_uses():
    assert DEFAULT_BASE_URL == whatsapp.WHATSAPP_API_BASE_URL


def test_a_good_status_is_returned_as_data(calls):
    calls.state["reply"] = Reply(200, {"connected": True, "phone": "1555", "uptime_seconds": 7})
    out = BridgeClient().status()
    assert out.ok is True and out.reason is None
    assert out.data == {"connected": True, "phone": "1555", "uptime_seconds": 7}
    assert calls[0][0] == "GET" and calls[0][1].endswith("/status") and calls[0][2]["timeout"] > 0


def test_the_token_is_read_from_disk_on_every_call(calls):
    client = BridgeClient()
    write_token(TOKEN)
    client.status()
    write_token("cd" * 32)
    client.status()
    assert calls[0][2]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert calls[1][2]["headers"]["Authorization"] == "Bearer " + "cd" * 32


@pytest.mark.parametrize("error", [requests.ConnectionError("http://localhost:8080 refused"), requests.Timeout("slow"),
                                   requests.RequestException("odd")])
def test_a_down_bridge_is_unreachable(calls, error):
    calls.state["raise"] = error
    out = BridgeClient().status()
    assert out.ok is False and out.reason == "unreachable" and out.data is None
    assert out.message


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_token_is_unauthorized(calls, status):
    calls.state["reply"] = Reply(status)
    out = BridgeClient().status()
    assert out.ok is False and out.reason == "unauthorized"


def test_a_missing_route_is_unsupported(calls):
    calls.state["reply"] = Reply(404)
    out = BridgeClient().status()
    assert out.ok is False and out.reason == "unsupported"


@pytest.mark.parametrize("reply", [Reply(200, None, bad_json=True), Reply(200, ["a list"]), Reply(200, "text"),
                                   Reply(500, {"connected": True}), Reply(302)])
def test_nonsense_is_unexpected(calls, reply):
    calls.state["reply"] = reply
    out = BridgeClient().status()
    assert out.ok is False and out.reason == "unexpected"


def test_neither_the_token_nor_the_address_appears_in_a_reason(calls):
    write_token(TOKEN)
    client = BridgeClient(base_url="http://10.9.8.7:4321/api")
    cases = [requests.ConnectionError(f"10.9.8.7:4321 Bearer {TOKEN}"), None]
    for error in cases:
        calls.state["raise"] = error
        for reply in (Reply(401), Reply(404), Reply(500), Reply(200, None, bad_json=True)):
            calls.state["reply"] = reply
            for out in (client.status(), client.validate_webhooks("{}")):
                text = f"{out.message} {out.reason} {getattr(out, 'error', None)}"
                assert TOKEN not in text and "10.9.8.7" not in text and "4321" not in text


# -- validating a webhooks file ----------------------------------------------------------------------
def test_the_file_text_is_posted_as_it_is(calls):
    calls.state["reply"] = Reply(200, {"ok": True})
    out = BridgeClient().validate_webhooks('{"enabled": false}')
    assert out.ok is True and out.ran is True and out.error is None
    method, url, kw = calls[0]
    assert method == "POST" and url.endswith("/config/validate-webhooks")
    assert kw["data"] == b'{"enabled": false}' and kw["timeout"] > 0


def test_the_bridge_can_refuse_a_file_and_say_why(calls):
    calls.state["reply"] = Reply(200, {"ok": False, "error": "endpoint 2: url must be https"})
    out = BridgeClient().validate_webhooks("{}")
    assert out.ran is True and out.ok is False and out.error == "endpoint 2: url must be https"


def test_a_413_is_a_clear_refusal_not_a_failure_to_run(calls):
    calls.state["reply"] = Reply(413)
    out = BridgeClient().validate_webhooks("{}")
    assert out.ran is True and out.ok is False and "too large" in out.error.lower()


def test_a_down_bridge_means_the_check_could_not_run(calls):
    calls.state["raise"] = requests.ConnectionError("x")
    out = BridgeClient().validate_webhooks("{}")
    assert out.ran is False and out.ok is False and out.reason == "unreachable"
    assert "could not" in out.message.lower()


def test_an_older_bridge_without_the_route_is_unsupported(calls):
    calls.state["reply"] = Reply(404)
    out = BridgeClient().validate_webhooks("{}")
    assert out.ran is False and out.reason == "unsupported"


@pytest.mark.parametrize("body", [{"ok": "yes"}, {"error": "x"}, ["x"], None, {"ok": False, "error": 5}])
def test_an_answer_that_is_not_ok_or_a_reason_is_unexpected(calls, body):
    calls.state["reply"] = Reply(200, body)
    out = BridgeClient().validate_webhooks("{}")
    assert out.ran is False and out.reason == "unexpected"
