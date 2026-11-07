"""The interface's view of the bridge: its status, and the check of a webhooks file.

Always an answer, never an exception: a bridge that is down, refuses the token or answers nonsense is an
Unavailable with a short reason that carries neither the token nor an address.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

import requests

import localconfig

DEFAULT_TIMEOUT = 3.0


@dataclass(frozen=True)
class Unavailable:
    reason: str      # unreachable, unauthorized, unexpected or unsupported


@dataclass(frozen=True)
class Status:
    data: Dict[str, Any]


@dataclass(frozen=True)
class WebhookCheck:
    ok: bool
    error: Optional[str]


class BridgeClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT):
        self._base_url = base_url
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        if self._base_url:
            return self._base_url
        import whatsapp      # the one place that knows the bridge's address   # noqa: PLC0415
        return whatsapp.WHATSAPP_API_BASE_URL

    def _call(self, method: str, path: str, payload: Optional[dict] = None):
        """(response, None) or (None, Unavailable). The token is read from disk now, so a restarted bridge is followed."""
        try:
            response = requests.request(method, f"{self.base_url}/{path}", json=payload, timeout=self._timeout,
                                        headers=localconfig.auth_headers())
        except requests.RequestException:
            return None, Unavailable("unreachable")
        if response.status_code == 401:
            return None, Unavailable("unauthorized")
        return response, None

    @staticmethod
    def _json(response) -> Optional[Any]:
        try:
            return response.json()
        except ValueError:
            return None

    def status(self) -> Union[Status, Unavailable]:
        response, problem = self._call("GET", "status")
        if problem:
            return problem
        body = self._json(response)
        if response.status_code != 200 or not isinstance(body, dict):
            return Unavailable("unexpected")
        return Status(body)

    def validate_webhooks(self, data: Dict[str, Any]) -> Union[WebhookCheck, Unavailable]:
        response, problem = self._call("POST", "config/validate-webhooks", data)
        if problem:
            return problem
        if response.status_code == 404:
            return Unavailable("unsupported")
        if response.status_code == 413:
            return WebhookCheck(False, "o arquivo é grande demais para ser validado")
        body = self._json(response)
        if response.status_code != 200 or not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
            return Unavailable("unexpected")
        error = body.get("error")
        return WebhookCheck(body["ok"], error if isinstance(error, str) and not body["ok"] else None)
