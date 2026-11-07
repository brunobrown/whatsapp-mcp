"""Webhook endpoints (webhooks.json, read by the bridge at start).

The file already has the shape of the form, so nothing is translated. The rules are the bridge's own: the file is
sent to it to be judged, and a bridge that cannot answer means the file cannot be saved, because it could not be
checked. The check reads the real secrets from the draft; its answers never repeat them.
"""
import json
import os
import re
from typing import Any, Dict, List

import localconfig
from ui.config.fields import Field, Problem
from ui.config.registry import JsonFile, Section
from ui.security.bridge import BridgeClient

CLIENT = BridgeClient()           # replaced in tests, or by the service that owns the bridge address

EVENTS = ("message", "poll_created", "poll_vote", "reaction", "message_edit", "message_deleted", "poll_closed",
          "connection", "ping", "*")
ENDPOINT_FIELDS = (
    Field("name", "Name", "text", max_length=64, help="How the endpoint is called in the status and in test_webhook."),
    Field("url", "URL", "text", max_length=2000,
          help="Where the bridge posts. https, or plain http only for 127.0.0.1, ::1 and localhost."),
    Field("secret", "Secret", "secret", help="Signs every post (HMAC-SHA256). At least 32 characters of random text."),
    Field("events", "Events", "multichoice", options=EVENTS, help='The events to send. "*" means all of them.'),
    Field("chats", "Chats", "list", help="Only events of these chats, one JID or number per line. Empty means every chat."),
    Field("include_content", "Include content", "bool", default=False,
          help="Add the message text, poll options and emoji to the event. They are other people's words.",
          risk="The text of messages leaves the bridge for this address.", risk_when=(True,)),
    Field("include_own", "Include own actions", "bool", default=False,
          help="Also send what this account does from another device."),
    Field("allow_private_network", "Allow private network", "bool", default=False,
          help="Let the address resolve to a private network.",
          risk="The bridge may post to addresses inside your network.", risk_when=(True,)),
    Field("persist_queue", "Keep the queue on disk", "bool", default=False,
          help="Events wait on disk, without their text, across a bridge restart."),
)
_SUBKEYS = {f.key for f in ENDPOINT_FIELDS}


def _path() -> str:
    return os.path.join(localconfig.config_dir(), "webhooks.json")


def _encode(values: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in values.items() if v is not None}
    if isinstance(out.get("endpoints"), list):
        out["endpoints"] = [{k: v for k, v in e.items() if v is not None} if isinstance(e, dict) else e
                            for e in out["endpoints"]]
    return out


def _where(error: str, values: Dict[str, Any]) -> str:
    """The record field the bridge's message is about: 'endpoints.N.field', 'endpoints.N' or '' for the file."""
    endpoints = values.get("endpoints") if isinstance(values.get("endpoints"), list) else []
    for index, ep in enumerate(endpoints):
        name = ep.get("name") if isinstance(ep, dict) else None
        label = f"endpoint {json.dumps(name)}:" if isinstance(name, str) and name else f"endpoint {index + 1}:"
        if error.startswith(label):
            rest = error[len(label):].lstrip()
            word = re.match(r"[a-z_]+", rest)
            sub = {"unknown": "events"}.get(word.group(0), word.group(0)) if word else ""
            return f"endpoints.{index}.{sub}" if sub in _SUBKEYS else f"endpoints.{index}"
    return ""


def _validate(values: Dict[str, Any]) -> List[Problem]:
    check = CLIENT.validate_webhooks(json.dumps(values))
    if not check.ran:
        return [Problem(None, check.message or "The check could not run.")]
    if check.ok:
        return []
    return [Problem(_where(check.error or "", values) or None, check.error or "The bridge refused the file.")]


SECTION = Section(
    id="webhooks", title="Webhooks", order=30, icon="webhook", storage=JsonFile(_path), restart="bridge",
    fields=(
        Field("enabled", "Enabled", "bool", default=True, help="Turn every endpoint off without deleting them."),
        Field("endpoints", "Endpoints", "records", id_key="name", fields=ENDPOINT_FIELDS,
              help="Programs of yours that the bridge tells, with a signed HTTP POST, when something happens."),
    ),
    validator=_validate, encode=_encode,
    description="Tell a program of yours when something happens. Read by the bridge at start; the bridge itself checks the file.")
