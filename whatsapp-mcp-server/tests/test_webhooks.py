"""get_webhook_status and test_webhook: the view of the bridge's webhooks and the ping to one endpoint."""
import asyncio
import os
import sys
from unittest import mock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import whatsapp  # noqa: E402

HOOKS = {"enabled": True, "endpoints": [{"name": "local", "delivered": 3, "failed": 1, "dropped": 0, "queued": 2,
                                          "last_error": "http 500", "last_success_at": "2026-11-07T10:00:00Z"}]}


def response(status=200, body=None):
    r = mock.Mock(status_code=status, text="body")
    r.json.return_value = body
    return r


def test_the_two_tools_are_registered_with_their_parameters_and_annotations():  # HOOK-17, HOOK-18
    tools = {t.name: t for t in asyncio.run(main.mcp.list_tools())}
    assert set(tools["get_webhook_status"].input_schema["properties"]) == set()
    assert tools["get_webhook_status"].annotations.read_only_hint is True
    assert set(tools["test_webhook"].input_schema["properties"]) == {"name"}
    assert tools["test_webhook"].input_schema["required"] == ["name"]
    a = tools["test_webhook"].annotations
    assert (a.read_only_hint, a.destructive_hint) == (False, False)


def test_status_returns_the_webhooks_of_the_bridge():  # HOOK-17
    with mock.patch("whatsapp.requests.get", return_value=response(200, {"connected": True, "webhooks": HOOKS})) as get:
        out = whatsapp.get_webhook_status()
    assert out["webhooks"] == HOOKS
    assert out["success"] is True
    assert get.call_args.args[0].endswith("/status")


def test_status_of_a_bridge_without_the_field_says_disabled():  # HOOK-01
    with mock.patch("whatsapp.requests.get", return_value=response(200, {"connected": True})):
        out = whatsapp.get_webhook_status()
    assert out["success"] is True and out["webhooks"]["enabled"] is False and out["webhooks"]["endpoints"] == []


def test_status_when_the_bridge_is_down_is_an_error_not_an_empty_answer():  # HOOK-17
    with mock.patch("whatsapp.requests.get", side_effect=requests.ConnectionError("refused")):
        out = whatsapp.get_webhook_status()
    assert out["success"] is False and out["error_code"] == "BRIDGE_UNREACHABLE"


def test_status_when_the_bridge_refuses_the_token():  # HOOK-17
    with mock.patch("whatsapp.requests.get", return_value=response(401, None)):
        out = whatsapp.get_webhook_status()
    assert out["success"] is False and out["error_code"] == "BRIDGE_UNAUTHORIZED"


def test_the_test_tool_posts_the_name_to_the_bridge_and_returns_the_answer():  # HOOK-18
    answer = {"success": True, "name": "local", "status": 200, "latency_ms": 12, "error": ""}
    with mock.patch("whatsapp.requests.post", return_value=response(200, answer)) as post:
        out = whatsapp.test_webhook("local")
    assert post.call_args.args[0].endswith("/webhooks/test")
    assert post.call_args.kwargs["json"] == {"name": "local"}
    assert out["success"] is True and out["status"] == 200 and out["latency_ms"] == 12


def test_the_test_of_an_endpoint_that_answers_badly_is_reported_not_hidden():  # HOOK-18
    answer = {"success": False, "name": "local", "status": 500, "latency_ms": 5, "error": ""}
    with mock.patch("whatsapp.requests.post", return_value=response(200, answer)):
        out = whatsapp.test_webhook("local")
    assert out["success"] is False and out["status"] == 500


def test_the_test_of_an_unknown_endpoint():  # HOOK-18
    body = {"success": False, "error_code": "ENDPOINT_NOT_FOUND", "message": "No webhook endpoint is named \"x\""}
    with mock.patch("whatsapp.requests.post", return_value=response(404, body)):
        out = whatsapp.test_webhook("x")
    assert out["success"] is False and out["error_code"] == "ENDPOINT_NOT_FOUND"


@pytest.mark.parametrize("name", ["", "   ", None, 3])
def test_the_test_without_a_valid_name_never_calls_the_bridge(name):  # HOOK-18
    with mock.patch("whatsapp.requests.post") as post:
        out = whatsapp.test_webhook(name)
    assert out["success"] is False and out["error_code"] == "INVALID_ARGUMENT"
    post.assert_not_called()


def test_the_test_when_the_bridge_is_down_or_refuses():  # HOOK-18
    with mock.patch("whatsapp.requests.post", side_effect=requests.ConnectionError("refused")):
        assert whatsapp.test_webhook("local")["error_code"] == "BRIDGE_UNREACHABLE"
    with mock.patch("whatsapp.requests.post", return_value=response(403, None)):
        assert whatsapp.test_webhook("local")["error_code"] == "BRIDGE_UNAUTHORIZED"
    with mock.patch("whatsapp.requests.post", return_value=response(500, None)):
        assert whatsapp.test_webhook("local")["error_code"] == "UNKNOWN"


def test_the_tools_forward_to_the_functions():  # HOOK-17, HOOK-18
    with mock.patch("whatsapp.requests.get", return_value=response(200, {"webhooks": HOOKS})):
        assert main.get_webhook_status()["webhooks"] == HOOKS
    answer = {"success": True, "name": "local", "status": 200, "latency_ms": 1, "error": ""}
    with mock.patch("whatsapp.requests.post", return_value=response(200, answer)) as post:
        assert main.test_webhook("local")["status"] == 200
    assert post.call_args.kwargs["json"] == {"name": "local"}


def test_both_calls_carry_the_bridge_token():  # HOOK-17, HOOK-18
    headers = {"Authorization": "Bearer abc123"}
    with mock.patch("whatsapp.localconfig.auth_headers", return_value=headers):
        with mock.patch("whatsapp.requests.get", return_value=response(200, {"webhooks": HOOKS})) as get:
            whatsapp.get_webhook_status()
        with mock.patch("whatsapp.requests.post", return_value=response(200, {"success": True})) as post:
            whatsapp.test_webhook("local")
    assert get.call_args.kwargs["headers"] == headers
    assert post.call_args.kwargs["headers"] == headers
