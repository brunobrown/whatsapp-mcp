"""Server mode: `full` (default) or `read_only`.

`WHATSAPP_MCP_MODE=read_only` makes an instance that cannot reach anyone: the tools that send, vote, react, delete,
answer for the monitor or ping a webhook are not registered at all, and the send path refuses too. It keeps the
tools that read, write local files (drafts, exports) and download media. An agent given only this instance cannot
be talked into leaking what it reads, because it has no way out: that breaks the "lethal trifecta".

An unknown value means `read_only`: a typo must never open permissions.
"""
import logging
import os
from typing import Iterable

logger = logging.getLogger(__name__)

ENV = "WHATSAPP_MCP_MODE"
FULL = "full"
READ_ONLY = "read_only"
READ_ONLY_MODE = "READ_ONLY_MODE"
HINT = "This server runs in read-only mode (WHATSAPP_MCP_MODE=read_only): it cannot send, vote, react or delete."
_warned = False


def current() -> str:
    global _warned
    value = os.environ.get(ENV, FULL).strip().lower() or FULL
    if value == FULL:
        return FULL
    if value != READ_ONLY and not _warned:
        _warned = True
        logger.warning("%s=%r is not 'full' or 'read_only'; running read-only", ENV, os.environ.get(ENV))
    return READ_ONLY


def is_read_only() -> bool:
    return current() == READ_ONLY


def removable(server, allowed_annotations: Iterable) -> list:
    """The tools a read-only server would not offer: those whose annotations are not in the allowed list
    (fail closed: no annotation, no tool)."""
    allowed = list(allowed_annotations)
    return [t.name for t in server._tool_manager.list_tools() if not any(t.annotations == a for a in allowed)]


def apply(server, allowed_annotations: Iterable) -> list:
    """Unregister the tools `removable` names. Returns the names removed. Does nothing in full mode."""
    if not is_read_only():
        return []
    names = removable(server, allowed_annotations)
    for name in names:
        server.remove_tool(name)
    return names
