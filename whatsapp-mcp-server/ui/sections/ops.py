"""What the read-only panels show: the bridge's state, counters, pending drafts and the audit logs.

Each function returns only the fields it lists here, so a field added to a log later cannot reach the screen by
accident. No message text, no draft text, no name; and nothing in this module acts: it cannot send, approve,
discard or change anything.
"""
import json
import os
from collections import deque
from typing import Any, Dict, List, Optional, Union

import drafts
import metrics
import monitor
import send_policy
from ui.core.bridge import BridgeClient, Status, Unavailable

MAX_ROWS = 200
_TAIL_BYTES = 256 * 1024
STATUS_KEYS = ("connected", "logged_in", "phone", "version", "started_at", "uptime_seconds", "last_event_at", "last_message_at",
               "last_connected_at", "last_disconnected_at", "pending_media_retries")
DRAFT_KEYS = ("id", "profile", "chat_jid", "message_id", "sender", "created_at", "expires_at")
AUDIT_KEYS = {
    "send": ("ts", "kind", "chat_jid", "size", "result", "rule", "override", "message_id"),
    "monitor": ("ts", "profile", "chat_jid", "sender", "message_id", "triggers", "mode", "approval_used", "result", "size",
                "error_code", "reply_id"),
}


def bridge_status(client: Optional[BridgeClient] = None) -> Union[Status, Unavailable]:
    result = (client or BridgeClient()).status()
    if isinstance(result, Status):
        return Status({k: result.data[k] for k in STATUS_KEYS if k in result.data})
    return result


def counters(window_hours: float = 24, client: Optional[BridgeClient] = None) -> Dict[str, Any]:
    """The counters of get_metrics: counts only. The bridge's webhook counters ride along when the bridge answers."""
    status = (client or BridgeClient()).status()
    return metrics.collect(window_hours=window_hours, bridge_status=status.data if isinstance(status, Status) else None)


def pending_drafts() -> List[Dict[str, Any]]:
    """Who asked and when, never the drafted text. Newest first."""
    rows = [{k: e.get(k) for k in DRAFT_KEYS} for e in drafts.listing()["drafts"]]
    return sorted(rows, key=lambda r: str(r.get("created_at")), reverse=True)


def _tail(path: str, limit: int) -> List[str]:
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - _TAIL_BYTES))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > _TAIL_BYTES:
        lines = lines[1:]            # the first line of the slice may be cut
    return list(deque(lines, maxlen=limit))


def audit_tail(kind: str, limit: int = 50) -> List[Dict[str, Any]]:
    """The newest entries of the send or monitor audit log, newest first, reduced to the listed fields."""
    keys = AUDIT_KEYS[kind]
    path = send_policy.AUDIT_PATH if kind == "send" else monitor.AUDIT_PATH
    rows = []
    for line in reversed(_tail(path, max(0, min(int(limit), MAX_ROWS)))):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            rows.append({k: entry[k] for k in keys if k in entry})
    return rows
