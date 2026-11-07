"""Simple counters for diagnosing the setup: what came in, what the profiles answered, what the policy blocked,
how the webhooks are doing. Counts only: no message text, name, chat id, sender or token appears in the result.
"""
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional

import dbcrypto
import drafts
import monitor
import send_policy
import whatsapp

MAX_WINDOW_HOURS = 24 * 90


def _entries(path: str, since: datetime) -> Iterable[dict]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    for text in lines:
        try:
            entry = json.loads(text)
            when = datetime.fromisoformat(entry["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(entry, dict) and when.tzinfo is not None and when >= since:
            yield entry


def _messages(since: datetime) -> Optional[Dict[str, int]]:
    try:
        conn = whatsapp._open_chats()
    except (*dbcrypto.DB_ERRORS, send_policy.DirectoryError):
        return None
    try:
        stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")
        rows = conn.execute("SELECT is_from_me, COUNT(*) FROM messages "
                            "WHERE strftime('%Y-%m-%d %H:%M:%f', timestamp) >= ? GROUP BY is_from_me", (stamp,)).fetchall()
        counts = {bool(flag): n for flag, n in rows}
        return {"received": counts.get(False, 0), "from_this_account": counts.get(True, 0)}
    except dbcrypto.DB_ERRORS:
        return None
    finally:
        conn.close()


def _webhooks(status: dict) -> Optional[dict]:
    hooks = status.get("webhooks")
    if status.get("bridge_reachable") is False or not isinstance(hooks, dict):
        return None
    total = Counter()
    for endpoint in hooks.get("endpoints") or []:
        for key in ("delivered", "failed", "dropped", "queued"):
            value = endpoint.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                total[key] += value
    return {"enabled": bool(hooks.get("enabled")), "endpoints": len(hooks.get("endpoints") or []),
            **{k: total[k] for k in ("delivered", "failed", "dropped", "queued")}}


def collect(window_hours=24, now: Optional[datetime] = None, bridge_status: Optional[dict] = None) -> dict:
    if isinstance(window_hours, bool) or not isinstance(window_hours, (int, float)) \
            or not 0 < window_hours <= MAX_WINDOW_HOURS:
        return {"success": False, "error_code": "INVALID_ARGUMENT",
                "message": f"window_hours must be above 0 and at most {MAX_WINDOW_HOURS}"}
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=window_hours)

    replies: Dict[str, Counter] = {}
    codes: Counter = Counter()
    for entry in _entries(monitor.AUDIT_PATH, since):
        name = str(entry.get("profile", "?"))[:80]
        replies.setdefault(name, Counter())[str(entry.get("result", "?"))] += 1
        if entry.get("error_code"):
            codes[str(entry["error_code"])] += 1

    sends = Counter(str(e.get("result", "?")) for e in _entries(send_policy.AUDIT_PATH, since))
    overrides = sum(1 for e in _entries(send_policy.AUDIT_PATH, since) if e.get("override"))

    return {
        "success": True,
        "window_hours": window_hours,
        "since": since.isoformat(),
        "messages": _messages(since),
        "monitor_replies": {"by_profile": {k: dict(v) for k, v in sorted(replies.items())}, "error_codes": dict(codes)},
        "sends": {"by_result": dict(sends), "with_override": overrides},
        "pending_drafts": drafts.listing(now=now)["count"],
        "webhooks": _webhooks(bridge_status or {}),
    }
