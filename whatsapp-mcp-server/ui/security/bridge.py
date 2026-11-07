"""The interface's view of the bridge: its status, and the check of a webhooks file by the bridge's own rules.

Every call gives an answer and none raises. The API token is read from disk on each call so a restarted bridge is
followed. A reason is one of a few fixed sentences: it carries neither the token nor the address.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

import localconfig

DEFAULT_BASE_URL = "http://localhost:8080/api"
TIMEOUT_SECONDS = 5

_MESSAGES = {
    "unreachable": "The bridge is not responding.",
    "unauthorized": "The bridge refused the interface's access token. Restart the bridge so they use the same one.",
    "unsupported": "This bridge does not offer that function. Update the bridge.",
    "unexpected": "The bridge gave an answer that could not be understood.",
}


@dataclass(frozen=True)
class BridgeResult:
    ok: bool
    data: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None            # unreachable, unauthorized, unsupported or unexpected
    message: Optional[str] = None


@dataclass(frozen=True)
class WebhookCheck:
    ran: bool                               # False when the bridge could not do the check
    ok: bool = False
    error: Optional[str] = None             # why the bridge refused the file (only when it ran)
    reason: Optional[str] = None            # why it could not run
    message: Optional[str] = None


def _failure(reason: str) -> BridgeResult:
    return BridgeResult(False, reason=reason, message=_MESSAGES[reason])


def _problem(response: Any) -> Optional[str]:
    """The reason a reply is not usable, or None."""
    if response.status_code in (401, 403):
        return "unauthorized"
    if response.status_code == 404:
        return "unsupported"
    return None


class BridgeClient:
    def __init__(self, base_url: Optional[str] = None):
        self._base = (base_url or DEFAULT_BASE_URL).rstrip("/")

    def status(self) -> BridgeResult:
        try:
            response = requests.get(f"{self._base}/status", timeout=TIMEOUT_SECONDS, headers=localconfig.auth_headers())
        except requests.RequestException:
            return _failure("unreachable")
        reason = _problem(response)
        if reason:
            return _failure(reason)
        try:
            body = response.json()
        except ValueError:
            return _failure("unexpected")
        if response.status_code != 200 or not isinstance(body, dict):
            return _failure("unexpected")
        return BridgeResult(True, data=body)

    def validate_webhooks(self, text: str) -> WebhookCheck:
        """Ask the bridge whether `text` is an acceptable webhooks.json. Nothing is written or enabled."""
        try:
            response = requests.post(f"{self._base}/config/validate-webhooks", data=text.encode("utf-8"),
                                     timeout=TIMEOUT_SECONDS,
                                     headers={**localconfig.auth_headers(), "Content-Type": "application/json"})
        except requests.RequestException:
            return self._could_not("unreachable")
        if response.status_code == 413:
            return WebhookCheck(True, False, error="The file is too large for the bridge to check.")
        reason = _problem(response)
        if reason:
            return self._could_not(reason)
        try:
            body = response.json()
        except ValueError:
            return self._could_not("unexpected")
        if response.status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
            return WebhookCheck(True, True)
        if (response.status_code == 200 and isinstance(body, dict) and body.get("ok") is False
                and isinstance(body.get("error"), str)):
            return WebhookCheck(True, False, error=body["error"])
        return self._could_not("unexpected")

    @staticmethod
    def _could_not(reason: str) -> WebhookCheck:
        return WebhookCheck(False, reason=reason, message=f"The check could not run. {_MESSAGES[reason]}")
