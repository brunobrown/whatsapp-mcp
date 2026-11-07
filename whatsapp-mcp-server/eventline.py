"""The one-line form of an event, shared by watch.py and webhook_receiver.py.

A line is `key=value` pairs separated by one space. It carries ids, types and times, never the text of a message,
a name, a question or an option: those come from other people. Values are cut down to a safe alphabet so that a
value can never add a space, a line break or a new key.
"""
import re
from typing import Iterable, Optional, Tuple

_UNSAFE = re.compile(r"[^A-Za-z0-9@.:+,_\-]")
MAX_VALUE = 120


def clean(value) -> str:
    """A value that fits in a `key=value` pair: unsafe characters become `_`, long values are cut."""
    text = _UNSAFE.sub("_", str(value))
    return text[:MAX_VALUE] or "-"


def line(pairs: Iterable[Tuple[str, Optional[object]]]) -> str:
    """`k=v k=v` from pairs, skipping the ones whose value is None. Keys are fixed by the code, not by data."""
    return " ".join(f"{key}={clean(value)}" for key, value in pairs if value is not None)
