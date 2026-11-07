"""The send policy: who the MCP server may send to (send_policy.json, read on every send).

The rules come from send_policy.py, so the interface never carries its own copy of them.
"""
import re
from typing import Any, Dict, List

import send_policy
from ui.config.fields import Field, Problem
from ui.config.registry import JsonFile, Section

_QUOTED_KEY = re.compile(r'^"([a-z_]+)"')


def _validate(values: Dict[str, Any]) -> List[Problem]:
    _, problem = send_policy.parse_policy(values)
    if problem is None:
        return []
    named = _QUOTED_KEY.match(problem)
    key = named.group(1) if named and named.group(1) in {f.key for f in FIELDS} else None
    return [Problem(key, problem)]


FIELDS = (
    Field("mode", "Mode", "choice", default="denylist", options=("denylist", "allowlist"),
          help="Denylist sends anywhere that is not denied. Allowlist sends only to the chats listed under Allow."),
    Field("allow", "Allow", "list",
          help="Chats the server may send to when the mode is allowlist. One per line: a chat name, a phone number or a JID."),
    Field("deny", "Deny", "list",
          help="Chats the server never sends to. Deny always wins. One per line: a chat name, a phone number or a JID."),
    Field("override_token", "Override token", "secret",
          help="A value the agent can pass to release one blocked send. A deliberate speed bump, not a security boundary: "
               "whoever can read the file can read the token."),
    Field("session_taint", "Session taint", "choice", default="block", options=("block", "off"),
          help="Block sending to a different chat than the ones read in the session. The environment variable "
               "WHATSAPP_SESSION_TAINT wins over this value.",
          risk="Turning this off lets text read in one chat make the agent pass its content to another chat.",
          risk_when=("off",)),
)

SECTION = Section(
    id="send_policy", title="Send policy", order=10, icon="shield", storage=JsonFile(lambda: send_policy.POLICY_PATH),
    fields=FIELDS, validator=_validate,
    description="Where the MCP server may send messages. Read on every send; no restart needed.")
