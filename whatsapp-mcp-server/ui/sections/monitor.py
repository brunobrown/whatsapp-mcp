"""Monitor profiles (monitor_profiles.json, read on every call).

The file keeps its profiles in an object keyed by name. The interface shows them as a list of records, one per
profile, with the name as the id: `decode` and `encode` translate between the two. The rules come from monitor.py.
"""
import re
from typing import Any, Dict, List

import monitor
from ui.config.fields import Field, Problem
from ui.config.registry import JsonFile, Section

_LIMIT_PREFIX = "limit_"
_LIMIT_LABELS = {
    "replies_per_chat_per_hour": "Replies per chat per hour",
    "replies_per_sender_per_hour": "Replies per sender per hour",
    "cooldown_seconds": "Cooldown in seconds",
    "max_reply_chars": "Maximum reply length",
}
_LIST_KEYS = ("targets", "aliases", "triggers", "chats", "auto_chats", "topics", "auto_senders")

PROFILE_FIELDS = (
    Field("name", "Name", "text", max_length=64, help="The name the agent passes to the monitor tools."),
    Field("enabled", "Enabled", "bool", default=True),
    Field("mode", "Mode", "choice", default="draft", options=tuple(monitor.MODES),
          help="Draft keeps every reply for the owner to approve. Auto sends it without a person approving it.",
          risk="Auto mode sends replies written by the agent without a person approving each one.", risk_when=("auto",)),
    Field("targets", "Targets", "list",
          help='The accounts this profile watches for, one per line: "me", a phone number, a LID or a JID.'),
    Field("aliases", "Aliases", "list", help="Names that call the agent, one per line. 2 to 40 characters, no spaces."),
    Field("triggers", "Triggers", "multichoice", options=tuple(monitor.TRIGGERS),
          help="What counts as a message for the agent. Leave empty to use the defaults."),
    Field("allow_own_alias", "Own messages may use an alias", "bool", default=False),
    Field("cross_chat_replies", "Replies across chats", "bool", default=False,
          help="Let a reply follow reads of other chats of this profile.",
          risk="A reply may carry content read in another chat into this one.", risk_when=(True,)),
    Field("chats", "Chats", "list", help="Only these chats are watched, one JID per line. Empty means all chats."),
    Field("auto_chats", "Chats for auto mode", "list", help="Chats where replies may be sent without approval, one JID per line."),
    Field("senders_allow", "Allowed senders", "list", help="Only these senders count, one per line. Empty means anyone."),
    Field("senders_deny", "Denied senders", "list", help="These senders never count, one per line."),
    Field("topics", "Topics", "list", help="Only messages with one of these words count, one per line."),
    Field("auto_senders", "Senders for auto mode", "list", help="Senders whose messages may be answered without approval."),
    Field("approval_token", "Approval token", "secret",
          help="A value the agent must pass to send a reply from this profile. A speed bump, not a security boundary."),
    *(Field(_LIMIT_PREFIX + key, label, "int", bounds=(1, 1_000_000),
            help=f"Default: {monitor.DEFAULT_LIMITS[key]}.") for key, label in _LIMIT_LABELS.items()),
    Field("max_event_age_minutes", "Maximum message age in minutes", "int", bounds=(1, 1_000_000),
          help=f"Older messages are ignored. Default: {monitor.DEFAULT_MAX_EVENT_AGE_MINUTES}."),
    Field("instructions", "Instructions", "text", multiline=True, max_length=4000,
          help="Guidance for the agent. The server does not enforce it."),
)
_SUBKEYS = {f.key for f in PROFILE_FIELDS}


def _record_of(name: str, body: Dict[str, Any]) -> Dict[str, Any]:
    record: Dict[str, Any] = {"name": name}
    for key, value in body.items():
        if key == "limits" and isinstance(value, dict):
            record.update({_LIMIT_PREFIX + k: v for k, v in value.items() if _LIMIT_PREFIX + k in _SUBKEYS})
        elif key == "senders" and isinstance(value, dict):
            record.update({f"senders_{k}": v for k, v in value.items() if f"senders_{k}" in _SUBKEYS})
        elif key in _SUBKEYS and key != "name":
            record[key] = value
    return record


def decode(values: Dict[str, Any]) -> Dict[str, Any]:
    profiles = values.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    return {"profiles": [_record_of(name, body) for name, body in profiles.items() if isinstance(body, dict)]}


def _body_of(record: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    limits = {k[len(_LIMIT_PREFIX):]: v for k, v in record.items() if k.startswith(_LIMIT_PREFIX) and v is not None}
    senders = {k[len("senders_"):]: v for k, v in record.items() if k.startswith("senders_") and v}
    for key, value in record.items():
        if key == "name" or value is None or key.startswith(_LIMIT_PREFIX) or key.startswith("senders_"):
            continue
        if (key in _LIST_KEYS or key == "instructions") and not value:
            continue
        body[key] = value
    if limits:
        body["limits"] = limits
    if senders:
        body["senders"] = senders
    return body


def encode(values: Dict[str, Any]) -> Dict[str, Any]:
    records = values.get("profiles", [])
    return {"profiles": {r.get("name", ""): _body_of(r) for r in records if isinstance(r, dict)}}


def _attribute(rest: str) -> str:
    """The record field a project message about `profiles.NAME<rest>` is about, '' when it is the profile as a whole."""
    named = re.match(r"\.([a-z_]+)(?:\.([a-z_]+))?", rest)
    if not named:
        return ""
    top, inner = named.groups()
    if top == "limits" and inner:
        return _LIMIT_PREFIX + inner
    if top == "senders" and inner:
        return f"senders_{inner}"
    return top if top in _SUBKEYS else ""


def _validate(values: Dict[str, Any]) -> List[Problem]:
    profiles = values.get("profiles", {}) if isinstance(values, dict) else {}
    if isinstance(profiles, dict):
        for name in profiles:
            if not isinstance(name, str) or not name or name != name.strip():
                return [Problem("profiles", "every profile needs a name without spaces at its ends")]
    _, problem = monitor.parse_profiles(values)
    if problem is None:
        return []
    if isinstance(profiles, dict):
        for index, name in enumerate(profiles):
            prefix = f"profiles.{name}"
            if problem.startswith(prefix):
                sub = _attribute(problem[len(prefix):])
                return [Problem(f"profiles.{index}.{sub}" if sub else f"profiles.{index}", problem)]
    return [Problem(None, problem)]


SECTION = Section(
    id="monitor", title="Monitor profiles", order=20, icon="eye", storage=JsonFile(lambda: monitor.CONFIG_PATH),
    fields=(Field("profiles", "Profiles", "records", id_key="name", fields=PROFILE_FIELDS,
                  help="Each profile says which messages reach the agent and how it may reply."),),
    validator=_validate, decode=decode, encode=encode,
    description="Which messages the monitor tools hand to the agent, and the guardrails on replies. Read on every call.")
