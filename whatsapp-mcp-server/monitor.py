"""Message monitor: profiles with guardrails, the scan that finds the messages that refer to an account, and guarded replies.

The profiles live in monitor_profiles.json, read on every call:

    {"profiles": {"name": {"targets": ["me", "+5581999990000"], "triggers": ["mention", "reply", "direct"],
                           "mode": "draft", "approval_token": "...", "limits": {...}, "instructions": "..."}}}

The server enforces what it can check (who may trigger, which chats, topics by keyword, mode, approval, rate and size
limits, audit). `instructions` is only guidance for the agent: the server does not understand what a reply says.
A file that does not validate blocks every profile (fail closed).
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import send_policy

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("WHATSAPP_MONITOR_CONFIG") or os.path.join(_HERE, "monitor_profiles.json")
AUDIT_PATH = os.environ.get("WHATSAPP_MONITOR_AUDIT_LOG") or os.path.join(_HERE, "monitor_audit.log")

CONFIG_INVALID = "MONITOR_CONFIG_INVALID"
PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
PROFILE_DISABLED = "PROFILE_DISABLED"

TRIGGERS = ("mention", "reply", "direct", "from")
DEFAULT_TRIGGERS = ("mention", "reply", "direct")
MODES = ("draft", "auto")
DEFAULT_LIMITS = {
    "replies_per_chat_per_hour": 6,
    "replies_per_sender_per_hour": 4,
    "cooldown_seconds": 20,
    "max_reply_chars": 1500,
}
DEFAULT_MAX_EVENT_AGE_MINUTES = 60
_PROFILE_KEYS = {"enabled", "targets", "triggers", "chats", "senders", "topics", "mode", "auto_senders",
                 "approval_token", "limits", "max_event_age_minutes", "instructions"}
_PHONE_FORMAT = re.compile(r"\+?[0-9 ().\-]+", re.ASCII)


@dataclass
class Profile:
    name: str
    enabled: bool = True
    targets: List[str] = field(default_factory=list)          # "me" or the digits of a phone / LID
    triggers: List[str] = field(default_factory=lambda: list(DEFAULT_TRIGGERS))
    chats: List[str] = field(default_factory=list)            # canonical JIDs; empty = every chat
    senders_allow: List[str] = field(default_factory=list)    # digits; empty = anyone
    senders_deny: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)           # lower-cased keywords; empty = any text
    mode: str = "draft"
    auto_senders: List[str] = field(default_factory=list)
    approval_token: Optional[str] = None
    limits: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    max_event_age_minutes: int = DEFAULT_MAX_EVENT_AGE_MINUTES
    instructions: str = ""


def normalize_number(text) -> Optional[str]:
    """The digits of a phone number, LID or user JID (7 to 20 digits), or None."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if "@" in text:
        jid = send_policy.canonical_jid(text)
        if not jid or jid.endswith("@g.us"):
            return None
        text = jid.split("@")[0]
    elif not _PHONE_FORMAT.fullmatch(text):
        return None
    digits = "".join(c for c in text if c in "0123456789")
    return digits if 7 <= len(digits) <= 20 else None


class _Invalid(Exception):
    pass


def _list_of_strings(value, where: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise _Invalid(f"{where} must be a list of non-empty texts")
    return value


def _numbers(value, where: str, allow_me: bool = False) -> List[str]:
    out = []
    for item in _list_of_strings(value, where):
        if allow_me and item.strip().lower() == "me":
            out.append("me")
            continue
        digits = normalize_number(item)
        if not digits:
            raise _Invalid(f"{where}: {item!r} is not a phone number, LID or JID" + (' (or "me")' if allow_me else ""))
        out.append(digits)
    return out


def _parse_profile(name: str, raw) -> Profile:
    where = f"profiles.{name}"
    if not isinstance(raw, dict):
        raise _Invalid(f"{where} must be an object")
    unknown = sorted(set(raw) - _PROFILE_KEYS)
    if unknown:
        raise _Invalid(f"{where}: unknown key(s) {', '.join(unknown)}")
    profile = Profile(name=name)

    if "enabled" in raw:
        if not isinstance(raw["enabled"], bool):
            raise _Invalid(f"{where}.enabled must be true or false")
        profile.enabled = raw["enabled"]

    profile.targets = _numbers(raw.get("targets"), f"{where}.targets", allow_me=True) if "targets" in raw else []
    if not profile.targets:
        raise _Invalid(f"{where}.targets must list at least one target")

    if "triggers" in raw:
        triggers = _list_of_strings(raw["triggers"], f"{where}.triggers")
        bad = [t for t in triggers if t not in TRIGGERS]
        if bad or not triggers:
            raise _Invalid(f"{where}.triggers: use one or more of {', '.join(TRIGGERS)}")
        profile.triggers = list(dict.fromkeys(triggers))

    if "chats" in raw:
        for chat in _list_of_strings(raw["chats"], f"{where}.chats"):
            jid = send_policy.canonical_jid(chat)
            if not jid:
                raise _Invalid(f"{where}.chats: {chat!r} is not a chat JID")
            profile.chats.append(jid)

    if "senders" in raw:
        senders = raw["senders"]
        if not isinstance(senders, dict) or set(senders) - {"allow", "deny"}:
            raise _Invalid(f"{where}.senders must be an object with allow and/or deny")
        profile.senders_allow = _numbers(senders.get("allow", []), f"{where}.senders.allow")
        profile.senders_deny = _numbers(senders.get("deny", []), f"{where}.senders.deny")

    if "topics" in raw:
        profile.topics = [t.strip().casefold() for t in _list_of_strings(raw["topics"], f"{where}.topics")]

    if "mode" in raw:
        if raw["mode"] not in MODES:
            raise _Invalid(f"{where}.mode must be one of {', '.join(MODES)}")
        profile.mode = raw["mode"]

    if "auto_senders" in raw:
        profile.auto_senders = _numbers(raw["auto_senders"], f"{where}.auto_senders")

    if "approval_token" in raw:
        if not isinstance(raw["approval_token"], str) or not raw["approval_token"]:
            raise _Invalid(f"{where}.approval_token must be a non-empty text")
        profile.approval_token = raw["approval_token"]

    if "limits" in raw:
        limits = raw["limits"]
        if not isinstance(limits, dict) or set(limits) - set(DEFAULT_LIMITS):
            raise _Invalid(f"{where}.limits: use only {', '.join(DEFAULT_LIMITS)}")
        for key, value in limits.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise _Invalid(f"{where}.limits.{key} must be a whole number of at least 1")
            profile.limits[key] = value

    if "max_event_age_minutes" in raw:
        age = raw["max_event_age_minutes"]
        if isinstance(age, bool) or not isinstance(age, int) or age < 1:
            raise _Invalid(f"{where}.max_event_age_minutes must be a whole number of at least 1")
        profile.max_event_age_minutes = age

    if "instructions" in raw:
        if not isinstance(raw["instructions"], str):
            raise _Invalid(f"{where}.instructions must be a text")
        profile.instructions = raw["instructions"]
    return profile


def load_profiles() -> Tuple[Dict[str, Profile], Optional[str]]:
    """(profiles by name, problem). No file means no profiles; a file that does not validate means a problem."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        return {}, f"the file cannot be read ({e})"
    try:
        raw = json.loads(text)
    except ValueError as e:
        return {}, f"the file is not valid JSON ({e})"
    try:
        if not isinstance(raw, dict) or set(raw) - {"profiles"}:
            raise _Invalid('the file must be an object with only a "profiles" key')
        if not isinstance(raw.get("profiles", {}), dict):
            raise _Invalid('"profiles" must be an object')
        return {name: _parse_profile(name, body) for name, body in raw.get("profiles", {}).items()}, None
    except _Invalid as e:
        return {}, str(e)


def error(code: str, message: str, **extra) -> dict:
    return {"success": False, "error_code": code, "message": message, **extra}


def get_profile(name: str) -> Tuple[Optional[Profile], Optional[dict]]:
    """The enabled profile called name, or the error answer to give."""
    profiles, problem = load_profiles()
    if problem:
        return None, error(CONFIG_INVALID, f"{CONFIG_PATH} is not usable, so no profile works: {problem}")
    profile = profiles.get(name)
    if profile is None:
        known = ", ".join(sorted(profiles)) or "none"
        return None, error(PROFILE_NOT_FOUND, f"There is no profile {name!r} (profiles: {known})")
    if not profile.enabled:
        return None, error(PROFILE_DISABLED, f"Profile {name!r} is turned off (enabled=false)")
    return profile, None


def describe(profile: Profile) -> dict:
    """A profile as shown to the agent. The approval token is never included."""
    return {
        "name": profile.name,
        "enabled": profile.enabled,
        "targets": profile.targets,
        "triggers": profile.triggers,
        "chats": profile.chats,
        "senders": {"allow": profile.senders_allow, "deny": profile.senders_deny},
        "topics": profile.topics,
        "mode": profile.mode,
        "auto_senders": profile.auto_senders,
        "limits": profile.limits,
        "max_event_age_minutes": profile.max_event_age_minutes,
        "instructions": profile.instructions,
        "has_approval_token": bool(profile.approval_token),
    }


def list_profiles() -> dict:
    profiles, problem = load_profiles()
    if problem:
        return error(CONFIG_INVALID, f"{CONFIG_PATH} is not usable, so no profile works: {problem}")
    return {"success": True, "profiles": [describe(p) for p in profiles.values()]}
