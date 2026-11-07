"""The registry of sections: the one place that says what the interface shows and edits.

A section is a Section: its title, where it is stored, its fields and how to validate them. Adding a feature
means adding a module to ui/sections that registers one; the shell, the form and the stores do not change.
Nothing here knows about a screen.
"""
import importlib
import pkgutil
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import uisettings

KINDS = ("bool", "int", "float", "text", "choice", "multichoice", "secret", "list", "records")
RESTART_SCOPES = (None, "bridge", "server")
_ID = re.compile(r"[a-z][a-z0-9_]*")
_SECRET_LOOKING = re.compile(r"token|secret|passw|passwd|(?:^|[_\s-])pw(?:$|[_\s-])|(?:^|[_\s-])key(?:$|[_\s-])", re.I)
_MAX_TEXT = 500


@dataclass(frozen=True)
class Problem:
    """Something wrong with a value: field is the key of the field it belongs to, or None for the whole section."""
    field: Optional[str]
    message: str


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str
    default: Any = None
    help: str = ""
    secret: bool = False
    env_var: Optional[str] = None             # the variable this field is stored as, for sections kept in ui_settings.json
    options: Tuple[str, ...] = ()             # choice and multichoice
    bounds: Optional[Tuple[float, float]] = None   # int and float: smallest and largest value
    max_length: int = _MAX_TEXT               # text and each item of a list
    fields: Tuple["Field", ...] = ()          # records: the fields of each record
    readonly: bool = False                    # shown, never edited
    risk: Optional[str] = None                # what to tell the operator before a value that weakens a defence

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"field {self.key!r}: kind must be one of {', '.join(KINDS)}")
        if self.kind == "secret":
            object.__setattr__(self, "secret", True)
        if self.kind in ("choice", "multichoice") and not self.options:
            raise ValueError(f"field {self.key!r}: a {self.kind} needs options")
        if self.kind == "records" and not self.fields:
            raise ValueError(f"field {self.key!r}: records need fields")
        if not self.secret and (_SECRET_LOOKING.search(self.key) or _SECRET_LOOKING.search(self.label)):
            raise ValueError(f"field {self.key!r} looks like a secret: declare it kind='secret' (secret=True)")
        if self.env_var and not self.readonly and self.env_var not in uisettings.EDITABLE_ENV:
            raise ValueError(f"field {self.key!r}: {self.env_var} is not in uisettings.EDITABLE_ENV, so it cannot be edited")
        keys = [f.key for f in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError(f"field {self.key!r}: a key is repeated among its fields")


@dataclass(frozen=True)
class JsonFile:
    """A JSON file whose path the section declares. The path comes from the project (a function, so it is read
    when used), never from what the screen sends."""
    path: Callable[[], str]


@dataclass(frozen=True)
class EnvKeys:
    """Variables kept in ui_settings.json; every field of the section names its variable."""


@dataclass(frozen=True)
class ReadOnly:
    """Shown, never written."""


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    icon: str
    order: int
    storage: Any
    fields: Tuple[Field, ...]
    validate: Callable[[Dict[str, Any]], List[Problem]]
    description: str = ""
    restart: Optional[str] = None             # None, "bridge" or "server": which process must restart for a change to apply

    def __post_init__(self):
        if not _ID.fullmatch(self.id or ""):
            raise ValueError(f"section id {self.id!r} must be lowercase letters, digits and _, starting with a letter")
        if not self.fields:
            raise ValueError(f"section {self.id!r} needs at least one field")
        keys = [f.key for f in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError(f"section {self.id!r}: a field key is repeated")
        if self.restart not in RESTART_SCOPES:
            raise ValueError(f"section {self.id!r}: restart must be None, 'bridge' or 'server'")
        if isinstance(self.storage, EnvKeys) and not all(f.env_var for f in self.fields):
            raise ValueError(f"section {self.id!r}: every field of an environment section needs an env_var")

    def field(self, key: str) -> Field:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)


class Registry:
    def __init__(self):
        self._sections: Dict[str, Section] = {}

    def register(self, section: Section) -> Section:
        if section.id in self._sections:
            raise ValueError(f"section {section.id!r} is already registered")
        self._sections[section.id] = section
        return section

    def sections(self) -> List[Section]:
        return sorted(self._sections.values(), key=lambda s: (s.order, s.title))

    def get(self, section_id: str) -> Section:
        return self._sections[section_id]

    def discover(self, package: str = "ui.sections") -> List[str]:
        """Import every module of the package, so each registers its section; returns the modules newly imported."""
        pkg = importlib.import_module(package)
        found = []
        for info in pkgutil.iter_modules(pkg.__path__):
            name = f"{package}.{info.name}"
            if name not in sys.modules:
                importlib.import_module(name)
                found.append(info.name)
        return found


default = Registry()


def register(section: Section) -> Section:
    return default.register(section)


def all_sections() -> List[Section]:
    """Every registered section, after looking for modules in ui.sections."""
    default.discover()
    return default.sections()
