"""Send policy and audit log.

The policy file (send_policy.json) says where the MCP server may send:

    {"mode": "denylist" | "allowlist", "allow": [...], "deny": [...], "override_token": "..."}

Entries are JIDs, phone numbers or exact chat names. `deny` always wins. The file is read on every
send. A missing file allows everything; a broken one blocks everything. The override token is a
deliberate speed bump for one-off releases, not a security boundary: whoever can read the file can
read the token.
"""
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
POLICY_PATH = os.environ.get("WHATSAPP_SEND_POLICY") or os.path.join(_HERE, "send_policy.json")
AUDIT_PATH = os.environ.get("WHATSAPP_SEND_AUDIT_LOG") or os.path.join(_HERE, "send_audit.log")

BLOCKED = "SEND_BLOCKED_BY_POLICY"
NOT_FOUND = "RECIPIENT_NOT_FOUND"
AMBIGUOUS = "RECIPIENT_AMBIGUOUS"
INVALID = "RECIPIENT_INVALID"
UNAVAILABLE = "DIRECTORY_UNAVAILABLE"
_MAX_LOGGED = 200
_ASCII_DIGITS = "0123456789"
_MODES = ("allowlist", "denylist")


class DirectoryError(Exception):
    """The chat directory (bridge database) could not be read, so the policy cannot be checked."""


# user[.agent][:device]@server; a legacy group id is two numbers joined by a hyphen
_JID = re.compile(r"(?P<user>[0-9]+(?:-[0-9]+)?)(?:\.[0-9]+)?(?::[0-9]+)?@(?P<server>[^@\s]+)")
_SERVERS = ("s.whatsapp.net", "lid", "g.us")


def canonical_jid(text: str) -> Optional[str]:
    """The plain `user@server` the bridge will address, or None when text is not a destination we accept.

    Agent and device parts are dropped and the server is lower-cased, so the policy sees the same JID the
    bridge does. Anything else after the first `@` (extra `@`, spaces, other servers) is refused.
    """
    match = _JID.fullmatch(text.strip())
    if not match:
        return None
    user, server = match.group("user"), match.group("server").lower()
    if server not in _SERVERS or ("-" in user and server != "g.us"):
        return None
    return f"{user}@{server}"


@dataclass
class Directory:
    """What the policy needs to know about chats; whatsapp.py supplies it from the bridge database."""
    jids_by_name: Callable[[str], List[str]]   # exact, case-insensitive chat name -> JIDs
    name_of: Callable[[str], Optional[str]]    # JID (or the same person under another id) -> chat name
    ids_of: Callable[[str], List[str]]         # every id a person is stored under (phone, LID)
    is_phone: Callable[[str], bool]            # whether text is a phone number rather than a chat name
    ready: Callable[[], None]                  # raises DirectoryError when the chat directory is unreadable


@dataclass
class Target:
    """Where a send goes, after resolving names to a JID."""
    ok: bool
    recipient: str = ""            # what to hand to the bridge
    jid: str = ""
    chat_name: Optional[str] = None
    error_code: Optional[str] = None
    message: str = ""


@dataclass
class Decision:
    allowed: bool
    rule: str = ""
    override: bool = False
    message: str = ""


def resolve_target(recipient: str, directory: Directory) -> Target:
    """Turn a JID, phone number or chat name into the recipient the bridge understands."""
    recipient = recipient.strip()
    if "@" in recipient:
        jid = canonical_jid(recipient)
        if jid is None:
            return Target(False, error_code=INVALID,
                          message=f"Not a valid destination: {recipient[:_MAX_LOGGED]!r}. Use a phone number, "
                                  "a JID such as 123@g.us or 123@s.whatsapp.net, or a chat name. Nothing was sent.")
        return Target(True, jid, jid, directory.name_of(jid))
    if directory.is_phone(recipient):
        digits = "".join(c for c in recipient if c in _ASCII_DIGITS)
        jid = f"{digits}@s.whatsapp.net"
        return Target(True, digits, jid, directory.name_of(jid))
    try:
        jids = directory.jids_by_name(recipient)
    except DirectoryError as e:
        return Target(False, error_code=UNAVAILABLE, message=f"Could not look up the chat name: {e}. Nothing was sent.")
    if not jids:
        return Target(False, error_code=NOT_FOUND, message=f'No chat is named "{recipient}". Nothing was sent.')
    if len(jids) > 1:
        return Target(False, error_code=AMBIGUOUS,
                      message=f'More than one chat is named "{recipient}": {", ".join(jids)}. Use the JID. Nothing was sent.')
    return Target(True, jids[0], jids[0], directory.name_of(jids[0]))


def _load() -> Tuple[Optional[dict], Optional[str]]:
    """The policy, or (None, None) when there is no file, or (None, problem) when it is unusable."""
    try:
        with open(POLICY_PATH, encoding="utf-8") as f:
            policy = json.load(f)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"cannot read {os.path.basename(POLICY_PATH)}: {e}"
    if not isinstance(policy, dict):
        return None, "the policy file must contain a JSON object"
    if policy.get("mode", "denylist") not in _MODES:
        return None, f'"mode" must be one of {", ".join(_MODES)}'
    for key in ("allow", "deny"):
        value = policy.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None, f'"{key}" must be a list of texts'
    for key in ("allow", "deny"):
        for entry in policy.get(key, []):
            if "@" in entry and canonical_jid(entry) is None:
                return None, f'"{key}" entry {entry!r} is not a valid JID'
    token = policy.get("override_token")
    if token is not None and not isinstance(token, str):
        return None, '"override_token" must be a text'
    return policy, None


def _same_chat(a: str, b: str, directory: Directory) -> bool:
    if a == b:
        return True
    if a.endswith("@g.us") or b.endswith("@g.us"):
        return False
    return bool(set(directory.ids_of(a)) & set(directory.ids_of(b)))


def _is_name(entry: str, directory: Directory) -> bool:
    return "@" not in entry and not directory.is_phone(entry)


def _entry_jids(entry: str, directory: Directory) -> List[str]:
    if "@" in entry:
        return [canonical_jid(entry)]
    if directory.is_phone(entry):
        return [f"{''.join(c for c in entry if c in _ASCII_DIGITS)}@s.whatsapp.net"]
    return directory.jids_by_name(entry)


def _matching_entry(entries: List[str], target: Target, directory: Directory, is_allow: bool) -> Optional[str]:
    for entry in entries:
        jids = _entry_jids(entry, directory)
        if is_allow and len(jids) > 1:
            continue  # an ambiguous name never releases anything
        if any(_same_chat(j, target.jid, directory) for j in jids):
            return entry
        if not is_allow and _is_name(entry, directory) and target.chat_name and \
                target.chat_name.strip().casefold() == entry.strip().casefold():
            return entry  # the target's own name, even if the name lookup found nothing else
    return None


def _blocked(target: Target, reason: str) -> Decision:
    label = f'"{target.chat_name}" ({target.jid})' if target.chat_name else target.jid
    return Decision(False, reason, message=f"Send blocked by policy: chat {label} - {reason}. Nothing was sent.")


def _unverifiable(target: Target, why: str) -> Decision:
    """A block that no token releases: the policy could not be checked at all."""
    return _blocked(target, why)


def evaluate(target: Target, override_token: Optional[str], directory: Directory) -> Decision:
    policy, problem = _load()
    if problem:
        return _unverifiable(target, f"policy file is invalid ({problem})")
    if policy is None:
        return Decision(True)

    try:
        directory.ready()
        deny = _matching_entry(policy.get("deny", []), target, directory, is_allow=False)
        allowed = _matching_entry(policy.get("allow", []), target, directory, is_allow=True)
        name_rules = any(_is_name(e, directory) for e in policy.get("deny", []))
    except DirectoryError as e:
        return _unverifiable(target, f"the chat directory could not be read ({e})")

    if deny is not None:
        decision = _blocked(target, f'deny rule "{deny}"')
    elif policy.get("mode", "denylist") == "allowlist" and allowed is None:
        decision = _blocked(target, "not in the allowlist")
    elif name_rules and target.jid.endswith("@g.us") and not target.chat_name:
        decision = _blocked(target, "this group is not in the chat list, so deny rules by name cannot be checked")
    else:
        return Decision(True)

    configured = policy.get("override_token")
    if override_token and configured and hmac.compare_digest(override_token.encode(), configured.encode()):
        return Decision(True, rule=decision.rule, override=True)
    return decision


def audit(kind: str, recipient: str, target: Optional[Target], size: int, result: str,
          rule: str = "", override: bool = False, message_id: Optional[str] = None) -> None:
    """Append one JSON line per send attempt. Never the message text, never a token."""
    line = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kind": kind,
        "recipient": recipient[:_MAX_LOGGED],
        "chat_jid": target.jid if target else None,
        "chat_name": target.chat_name[:_MAX_LOGGED] if target and target.chat_name else None,
        "size": size,
        "result": result,
        "rule": rule,
        "override": override,
    }
    if message_id:
        line["message_id"] = message_id
    try:
        fd = os.open(AUDIT_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("Could not write the send audit log %s: %s", AUDIT_PATH, e)
