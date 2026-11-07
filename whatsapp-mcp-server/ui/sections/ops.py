"""Read-only operation data for the status and activity pages.

Nothing here writes, sends or approves, and nothing here returns message text: every answer is built by copying
listed keys of a known kind, so a key the source adds later does not leave by accident. Rows of a log come newest
first and at most MAX_ROWS; a line of a log that cannot be read is skipped.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import drafts
import metrics
import monitor
import send_policy
from ui.security.bridge import BridgeClient

MAX_ROWS = 200
TAIL_BYTES = 2 * 1024 * 1024
MAX_TEXT = 200
CLIENT = BridgeClient()

# kind of value allowed per key: b bool, i whole number, s text, n text or null, l list of text
_STATUS = {"connected": "b", "logged_in": "b", "phone": "s", "version": "s", "started_at": "s", "uptime_seconds": "i",
           "last_event_at": "n", "last_message_at": "n", "last_connected_at": "n", "last_disconnected_at": "n",
           "pending_media_retries": "i"}
_ENDPOINT = {"name": "s", "delivered": "i", "failed": "i", "dropped": "i", "queued": "i", "last_error": "s",
             "last_success_at": "n", "persistent": "b", "recovered": "i"}
_DRAFT = {"id": "s", "profile": "s", "chat_jid": "s", "message_id": "s", "sender": "s", "created_at": "s", "expires_at": "s"}
_AUDIT = {
    "send": {"ts": "s", "kind": "s", "chat_jid": "n", "chat_name": "n", "size": "i", "result": "s", "rule": "s",
             "override": "b", "message_id": "s"},
    "monitor": {"ts": "s", "profile": "s", "chat_jid": "s", "sender": "s", "message_id": "s", "triggers": "l", "mode": "s",
                "approval_used": "b", "result": "s", "size": "i", "error_code": "s", "reply_id": "s"},
}


def _clean(kind: str, value: Any) -> Any:
    """The value cut to size when it is of the kind, else the marker _DROP."""
    if kind == "b":
        return value if isinstance(value, bool) else _DROP
    if kind == "i":
        return value if isinstance(value, int) and not isinstance(value, bool) else _DROP
    if kind == "l":
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return [v[:MAX_TEXT] for v in value[:20]]
        return _DROP
    if kind == "n" and value is None:
        return None
    return value[:MAX_TEXT] if isinstance(value, str) else _DROP


_DROP = object()


def _pick(source: Any, allowed: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(source, dict):
        for key, kind in allowed.items():
            if key in source:
                value = _clean(kind, source[key])
                if value is not _DROP:
                    out[key] = value
    return out


def _unavailable(result) -> Dict[str, Any]:
    return {"unavailable": result.reason, "message": result.message}


# -- status ---------------------------------------------------------------------------------------
def status(client: Optional[BridgeClient] = None) -> Dict[str, Any]:
    result = (client or CLIENT).status()
    if not result.ok:
        return _unavailable(result)
    shown = _pick(result.data, _STATUS)
    hooks = result.data.get("webhooks")
    if isinstance(hooks, dict):
        shown["webhooks"] = {"enabled": hooks.get("enabled") is True,
                             **({"error": _clean("s", hooks["error"])} if isinstance(hooks.get("error"), str) else {}),
                             "endpoints": [_pick(e, _ENDPOINT) for e in hooks.get("endpoints") or [] if isinstance(e, dict)]}
    return {"status": shown}


# -- counters -------------------------------------------------------------------------------------
def _counts(source: Any) -> Dict[str, int]:
    return {str(k)[:80]: v for k, v in source.items() if isinstance(v, int) and not isinstance(v, bool)} \
        if isinstance(source, dict) else {}


def counters(client: Optional[BridgeClient] = None, window_hours: int = 24) -> Dict[str, Any]:
    result = (client or CLIENT).status()
    bridge = {**result.data, "bridge_reachable": True} if result.ok else {"bridge_reachable": False}
    raw = metrics.collect(window_hours, bridge_status=bridge)
    if not raw.get("success"):
        return {"unavailable": "invalid", "message": "The counters could not be read."}
    out: Dict[str, Any] = {"window_hours": raw["window_hours"], "since": raw["since"]}
    messages = raw.get("messages")
    out["messages"] = _pick(messages, {"received": "i", "from_this_account": "i"}) if isinstance(messages, dict) else None
    replies = raw.get("monitor_replies") or {}
    out["monitor_replies"] = {
        "by_profile": {str(name)[:80]: _counts(by) for name, by in (replies.get("by_profile") or {}).items()},
        "error_codes": _counts(replies.get("error_codes"))}
    sends = raw.get("sends") or {}
    out["sends"] = {"by_result": _counts(sends.get("by_result")),
                    "with_override": sends["with_override"] if isinstance(sends.get("with_override"), int) else 0}
    out["pending_drafts"] = raw["pending_drafts"] if isinstance(raw.get("pending_drafts"), int) else 0
    hooks = raw.get("webhooks")
    out["webhooks"] = _pick(hooks, {"enabled": "b", "endpoints": "i", "delivered": "i", "failed": "i", "dropped": "i",
                                    "queued": "i"}) if isinstance(hooks, dict) else None
    return out


# -- drafts ---------------------------------------------------------------------------------------
def _when(value: Any) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def drafts_view() -> Dict[str, Any]:
    """The pending drafts without their text, newest first."""
    entries = [e for e in drafts.listing()["drafts"] if isinstance(e, dict)]
    entries.sort(key=lambda e: _when(e.get("created_at")), reverse=True)
    rows = [_pick(e, _DRAFT) for e in entries[:MAX_ROWS]]
    return {"rows": rows, "count": len(rows)}


# -- audit logs -----------------------------------------------------------------------------------
def _tail_lines(path: str) -> List[str]:
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - TAIL_BYTES)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]                       # the first line of the tail may be cut
    return [line.decode("utf-8", errors="replace") for line in lines]


def audit(kind: str) -> Dict[str, Any]:
    """The newest rows of the send or the monitor audit log."""
    if kind not in _AUDIT:
        raise ValueError(f"unknown audit log {kind!r}")
    path = send_policy.AUDIT_PATH if kind == "send" else monitor.AUDIT_PATH
    rows: List[Dict[str, Any]] = []
    for line in reversed(_tail_lines(path)):
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(entry, dict):
            rows.append(_pick(entry, _AUDIT[kind]))
            if len(rows) >= MAX_ROWS:
                break
    return {"rows": rows}
