"""What a configuration field is, and whether a value is acceptable for it.

A Field describes one value of a section. `check` answers with a short English reason when a value is refused, or
None when it is acceptable. A reason never repeats the value, because the value may be a secret.
"""
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional, Tuple

KINDS = ("bool", "int", "float", "text", "choice", "multichoice", "secret", "list", "records")
DEFAULT_MAX_LENGTH = 1000
DEFAULT_MAX_ITEMS = 200
MAX_GROUP_LENGTH = 40
# A key with one of these words in it holds a secret; "key" alone is too common to count.
_SECRET_WORDS = {"token", "secret", "password", "passwd", "passphrase", "credential", "credentials", "apikey"}
_SECRET_PAIRS = {("api", "key"), ("private", "key"), ("secret", "key")}
_LINE_CONTROLS = {"\n", "\r", "\t"}


@dataclass(frozen=True)
class Problem:
    """A reason a value was refused. `key` is the field (a dotted path inside records), None for the whole section."""
    key: Optional[str]
    message: str


def looks_secret(key: str) -> bool:
    words = [w for w in re.split(r"[^a-z0-9]+", key.lower()) if w]
    return any(w in _SECRET_WORDS for w in words) or any(pair in _SECRET_PAIRS for pair in zip(words, words[1:]))


def _has_control(text: str, allowed: frozenset = frozenset()) -> bool:
    return any(unicodedata.category(ch) == "Cc" and ch not in allowed for ch in text)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str
    default: Any = None
    help: str = ""
    env_var: Optional[str] = None
    options: Tuple[Any, ...] = ()
    bounds: Optional[Tuple[float, float]] = None
    max_length: int = DEFAULT_MAX_LENGTH
    max_items: int = DEFAULT_MAX_ITEMS
    multiline: bool = False
    fields: Tuple["Field", ...] = ()
    id_key: Optional[str] = None
    readonly: bool = False
    risk: Optional[str] = None
    risk_when: Tuple[Any, ...] = ()
    group: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"field {self.key!r}: unknown kind {self.kind!r}")
        if looks_secret(self.key) and self.kind != "secret":
            raise ValueError(f"field {self.key!r} is named like a secret and must be declared with kind 'secret'")
        if len(self.group) > MAX_GROUP_LENGTH:
            raise ValueError(f"field {self.key!r}: group is longer than {MAX_GROUP_LENGTH} characters")
        if self.kind == "secret" and self.default is not None:
            raise ValueError(f"field {self.key!r}: a secret has no default")
        if self.bounds is not None and self.bounds[0] > self.bounds[1]:
            raise ValueError(f"field {self.key!r}: bounds are not ordered")
        if self.kind in ("choice", "multichoice") and not self.options:
            raise ValueError(f"field {self.key!r}: a choice needs options")
        if self.kind == "records":
            if not self.fields:
                raise ValueError(f"field {self.key!r}: records need sub-fields")
            keys = [f.key for f in self.fields]
            if len(set(keys)) != len(keys):
                raise ValueError(f"field {self.key!r}: sub-field keys repeat")
            if self.id_key is not None and self.id_key not in keys:
                raise ValueError(f"field {self.key!r}: id_key {self.id_key!r} is not a sub-field")

    @property
    def secret(self) -> bool:
        return self.kind == "secret"

    def sub_field(self, key: str) -> "Field":
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)

    # -- the check -----------------------------------------------------------------------------------
    def check(self, value: Any) -> Optional[str]:
        """The reason `value` is refused, or None. None as a value means "no value" and is always accepted."""
        if value is None:
            return None
        return getattr(self, f"_check_{self.kind}")(value)

    def _text_problem(self, value: Any, allow_lines: bool = False) -> Optional[str]:
        if not isinstance(value, str):
            return "must be text"
        if len(value) > self.max_length:
            return f"must be at most {self.max_length} characters"
        if _has_control(value, frozenset(_LINE_CONTROLS) if allow_lines else frozenset()):
            return "must not contain control characters"
        return None

    def _check_bool(self, value):
        return None if isinstance(value, bool) else "must be true or false"

    def _bounds_problem(self, value) -> Optional[str]:
        if self.bounds is not None:
            low, high = self.bounds
            if value < low:
                return f"must be at least {low:g}"
            if value > high:
                return f"must be at most {high:g}"
        return None

    def _check_int(self, value):
        if isinstance(value, bool) or not isinstance(value, int):
            return "must be a whole number"
        return self._bounds_problem(value)

    def _check_float(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return "must be a number"
        return self._bounds_problem(value)

    def _check_text(self, value):
        return self._text_problem(value, allow_lines=self.multiline)

    def _check_choice(self, value):
        if value in self.options and not isinstance(value, (list, dict)):
            return None
        return "must be one of: " + ", ".join(str(o) for o in self.options)

    def _check_multichoice(self, value):
        if not isinstance(value, list):
            return "must be a list"
        if any(item not in self.options or isinstance(item, (list, dict)) for item in value):
            return "every entry must be one of: " + ", ".join(str(o) for o in self.options)
        if len(set(value)) != len(value):
            return "must not repeat an entry"
        return None

    def _check_secret(self, value):
        if isinstance(value, str) and value == "":
            return "must not be empty"
        return self._text_problem(value)

    def _check_list(self, value):
        if not isinstance(value, list):
            return "must be a list"
        if len(value) > self.max_items:
            return f"must have at most {self.max_items} items"
        for item in value:
            problem = self._text_problem(item)
            if problem:
                return f"each item {problem}"
        return None

    def _check_records(self, value):
        if not isinstance(value, list):
            return "must be a list of records"
        known = {f.key: f for f in self.fields}
        seen = set()
        for number, record in enumerate(value, start=1):
            if not isinstance(record, dict):
                return f"record {number} must be an object"
            for key, item in record.items():
                sub = known.get(key)
                if sub is None:
                    return f"record {number} has a field that is not allowed"
                problem = sub.check(item)
                if problem:
                    return f"record {number}, {sub.label}: {problem}"
            if self.id_key is not None:
                ident = record.get(self.id_key)
                if ident in seen:
                    return f"record {number} repeats the {known[self.id_key].label}"
                seen.add(ident)
        return None
