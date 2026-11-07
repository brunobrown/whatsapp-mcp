"""Turning a message of the project's own validators into a Problem tied to a field."""
import re
from typing import Iterable, List, Optional

from ui.core.registry import Problem

_LEADING_KEY = re.compile(r"""^["']?([A-Za-z_][A-Za-z0-9_]*)["']?(?=[\s:.,]|$)""")


def from_message(message: str, keys: Iterable[str]) -> Problem:
    """The message with the field it starts with, when it starts with one of keys ('"mode" must be...')."""
    match = _LEADING_KEY.match(message or "")
    return Problem(match.group(1) if match and match.group(1) in set(keys) else None, message)


def listing(message: Optional[str], keys: Iterable[str]) -> List[Problem]:
    """[] when the project had no complaint, else the one complaint as a Problem."""
    return [from_message(message, keys)] if message else []
