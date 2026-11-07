"""Session taint: which chats the agent has read in this session.

The danger of an agent that can send is that text written by a stranger tells it to pass on what it read elsewhere.
So the server remembers, for the life of the process (one client session), every chat whose content it handed to
the agent. A send to chat D is allowed only when every chat read is D itself: replying where you read is fine,
carrying what you read in one chat to another is not. A read that cannot say which chat it came from counts as
all of them. There is no tool to clear it: only restarting the server ends the session.

A release is per call, with the send policy's override token (the same deliberate speed bump as the rest); the
monitor's approval token does not release it. Replies from `monitor_reply` look only at reads that did not come
from the monitor, since the profile's chats, senders and limits already scope those.

Mode: `WHATSAPP_SESSION_TAINT` (`block` default, `off`) or `"session_taint"` in send_policy.json; an unknown value
means `block`.
"""
import contextlib
import contextvars
import json
import logging
import os
import threading
from typing import Callable, Iterable, Optional, Set

logger = logging.getLogger(__name__)

ENV = "WHATSAPP_SESSION_TAINT"
BLOCKED = "SEND_BLOCKED_BY_SESSION_TAINT"
ALL = "*"
GENERAL, MONITOR, LOOSE = "general", "monitor", "loose"

_lock = threading.Lock()
_read = {GENERAL: set(), MONITOR: set()}
_scope: contextvars.ContextVar = contextvars.ContextVar("taint_scope", default=GENERAL)


def mark(chat_jid: Optional[str], source: str = GENERAL) -> None:
    """Content of this chat reached the agent. None or empty means the chat is unknown: every chat."""
    with _lock:
        _read[source].add(chat_jid or ALL)


def mark_many(chats: Iterable[Optional[str]], source: str = GENERAL) -> None:
    for chat in chats:
        mark(chat, source)


def reset() -> None:
    """For tests and for process start. No tool calls this."""
    with _lock:
        for s in _read.values():
            s.clear()


def snapshot() -> dict:
    with _lock:
        return {k: sorted(v) for k, v in _read.items()}


@contextlib.contextmanager
def monitor_reply_scope(cross_chat_replies: bool = False):
    """Inside this block a send is a monitor_reply. Only when its profile accepts cross-chat replies do the
    reads that came from the monitor stop counting."""
    token = _scope.set(LOOSE if cross_chat_replies else GENERAL)
    try:
        yield
    finally:
        _scope.reset(token)


def mode(policy_value: Optional[str] = None) -> str:
    value = os.environ.get(ENV)
    if value is None:
        value = policy_value
    if value is None:
        return "block"
    return "off" if str(value).strip().lower() == "off" else "block"


def _policy_value(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None
    return raw.get("session_taint") if isinstance(raw, dict) else None


def check(dest_jid: str, same_chat: Callable[[str, str], bool], policy_path: str) -> Optional[str]:
    """None when the send may go on; else the reason it is blocked (no message text in it)."""
    if mode(_policy_value(policy_path)) == "off":
        return None
    with _lock:
        seen: Set[str] = set(_read[GENERAL])
        if _scope.get() != LOOSE:
            seen |= _read[MONITOR]
    others = sorted(c for c in seen if c == ALL or not (c == dest_jid or same_chat(c, dest_jid)))
    if not others:
        return None
    return ("this session has read content from " + ("a chat other than the destination" if others != [ALL] else
            "chats it cannot name") + ", and a send to a different chat could carry it out. Reply in the chat you read, "
            "or have the owner restart the session, or pass the send policy's override_token for this one send.")
