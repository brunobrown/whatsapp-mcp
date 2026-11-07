"""Raw text typed in a control, read as the value it stands for.

This is the one rule for turning text into a value: the interface sends what was typed and the server reads it
here. A text that cannot be read gives an English reason (never the text) and the raw text stays available so the
control can keep showing what was typed.
"""
import re
from dataclasses import dataclass
from typing import Any, Optional

from ui.config.fields import Field

_INT = re.compile(r"[+-]?\d+")
_DECIMAL = re.compile(r"[+-]?(\d+([.,]\d*)?|[.,]\d+)")


@dataclass(frozen=True)
class Parsed:
    ok: bool
    value: Any = None
    error: Optional[str] = None
    raw: Any = None


def from_raw(field: Field, raw: Any) -> Parsed:
    """The value `raw` stands for in `field`. Anything that is not text is passed on for the field check to judge."""
    if not isinstance(raw, str):
        return Parsed(True, raw, raw=raw)
    kind = field.kind
    if kind == "secret":
        return Parsed(True, raw or None, raw=raw)       # untrimmed: spaces may be part of the secret
    if kind == "list":
        items = [line.strip() for line in raw.splitlines()]
        return Parsed(True, [item for item in items if item], raw=raw)
    text = raw.strip()
    if kind in ("int", "float", "text") and not text:
        return Parsed(True, None, raw=raw)
    if kind == "int":
        if not _INT.fullmatch(text):
            return Parsed(False, error="must be a whole number", raw=raw)
        return Parsed(True, int(text), raw=raw)
    if kind == "float":
        if not _DECIMAL.fullmatch(text):
            return Parsed(False, error="must be a number, with a comma or a dot for decimals", raw=raw)
        return Parsed(True, float(text.replace(",", ".")), raw=raw)
    if kind == "text":
        return Parsed(True, text, raw=raw)
    return Parsed(True, raw, raw=raw)


def to_raw(field: Field, value: Any) -> str:
    """The text a control shows for `value`."""
    if value is None:
        return ""
    if field.kind == "list":
        return "\n".join(value)
    if field.kind == "float":
        return f"{value:g}"
    return str(value)
