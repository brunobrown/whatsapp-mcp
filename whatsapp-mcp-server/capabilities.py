"""What this server version can do, so a client that sees an older one knows which fields and tools to expect."""
from typing import Any, Dict, List, Optional

# The features of this code (not of the configuration). Names are stable; add one when a feature ships.
FEATURES: List[str] = ["poll_deadline_and_quiz", "poll_vote_watch", "webhooks", "message_monitor", "watch_cli", "pending_drafts", "metrics", "export_conversation", "deleted_messages", "poll_closed_event", "claude_channel", "read_only_mode", "session_taint"]


def describe(version: str, status: Dict[str, Any], channel: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """server_version, the features of this code and what is switched on right now (from the bridge's own status)."""
    hooks = status.get("webhooks") if isinstance(status.get("webhooks"), dict) else {}
    return {
        "server_version": version,
        "features": list(FEATURES),
        "enabled": {"webhooks": bool(hooks.get("enabled")), "channel": bool((channel or {}).get("enabled"))},
        "channel": {"enabled": bool((channel or {}).get("enabled")), "error": (channel or {}).get("error")},
    }
