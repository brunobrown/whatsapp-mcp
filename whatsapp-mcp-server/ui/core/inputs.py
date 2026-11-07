"""What a person typed, and the value it stands for: the one rule, with no screen in it (the form flow and the controls share it)."""
import math
import re
from dataclasses import dataclass
from typing import Any, Optional

from ui.core.registry import Field

_TEXT_KINDS = ("text", "int", "float", "list", "secret")
_INTEGER = re.compile(r"[+-]?\d+")


@dataclass(frozen=True)
class Parsed:
    value: Any
    error: Optional[str] = None
    text: Optional[str] = None      # what was typed, kept so the screen can show it back when it is not a value yet


def from_raw(field: Field, raw: Any) -> Parsed:
    """The value a control's raw output stands for, or why it is not one."""
    if field.kind not in _TEXT_KINDS:
        return Parsed(raw)
    text = raw if isinstance(raw, str) else ""
    if field.kind == "list":
        return Parsed([line.strip() for line in text.splitlines() if line.strip()])
    stripped = text if field.kind == "secret" else text.strip()
    if stripped == "":
        return Parsed(None)
    if field.kind == "int":
        return Parsed(int(stripped)) if _INTEGER.fullmatch(stripped) else Parsed(None, "deve ser um número inteiro", text)
    if field.kind == "float":
        try:
            number = float(stripped.replace(",", "."))
        except ValueError:
            return Parsed(None, "deve ser um número", text)
        return Parsed(number) if math.isfinite(number) else Parsed(None, "deve ser um número", text)
    return Parsed(stripped)


def to_raw(field: Field, value: Any) -> str:
    """The text that shows a value in a text box."""
    if value is None or isinstance(value, dict):
        return ""
    if field.kind == "list":
        return "\n".join(value) if isinstance(value, list) else str(value)
    if field.kind == "float" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:g}"
    return str(value)
