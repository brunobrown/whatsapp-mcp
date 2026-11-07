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
_MAX_ITEMS = 200
_MAX_RECORDS = 100
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


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
    id_key: Optional[str] = None              # records: the sub-field that names a record (to keep its secret when records move)
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
        if self.id_key is not None and self.id_key not in keys:
            raise ValueError(f"field {self.key!r}: id_key {self.id_key!r} is not one of its fields")

    def check(self, value: Any) -> Optional[str]:
        """Why value is not acceptable for this field (in Portuguese, never repeating the value), or None."""
        if value is None:
            return None
        kind = self.kind
        if kind == "bool":
            return None if isinstance(value, bool) else "deve ser verdadeiro ou falso"
        if kind in ("int", "float"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "int" and not isinstance(value, int)):
                return "deve ser um número inteiro" if kind == "int" else "deve ser um número"
            if value != value or value in (float("inf"), float("-inf")):
                return "deve ser um número finito"
            if self.bounds and not self.bounds[0] <= value <= self.bounds[1]:
                return f"deve estar entre {self.bounds[0]} e {self.bounds[1]}"
            return None
        if kind in ("text", "secret"):
            return self._check_text(value)
        if kind == "choice":
            return None if isinstance(value, str) and value in self.options else f"deve ser um de: {', '.join(self.options)}"
        if kind == "multichoice":
            if not isinstance(value, list) or not all(isinstance(v, str) and v in self.options for v in value):
                return f"deve ser uma lista com valores de: {', '.join(self.options)}"
            return None if len(set(value)) == len(value) else "não pode repetir valores"
        if kind == "list":
            if not isinstance(value, list) or len(value) > _MAX_ITEMS:
                return f"deve ser uma lista de até {_MAX_ITEMS} textos"
            for item in value:
                problem = self._check_text(item)
                if problem:
                    return f"item inválido: {problem}"
            return None
        if kind == "records":
            if not isinstance(value, list) or len(value) > _MAX_RECORDS or not all(isinstance(r, dict) for r in value):
                return f"deve ser uma lista de até {_MAX_RECORDS} itens"
            known = {f.key: f for f in self.fields}
            for record in value:
                for key, sub in record.items():
                    if key not in known:
                        return f"campo desconhecido: {key}"
                    problem = known[key].check(sub)
                    if problem:
                        return f"{key}: {problem}"
            return None
        return "tipo desconhecido"

    def _check_text(self, value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return "deve ser um texto"
        if len(value) > self.max_length:
            return f"deve ter no máximo {self.max_length} caracteres"
        if _CONTROL.search(value):
            return "não pode ter caracteres de controle"
        return None


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
    # The form holds flat values by field key; the file may be shaped otherwise. decode: file -> form values;
    # encode: (form values, the file as it was) -> the file to write. Both are the identity by default.
    decode: Callable[[Dict[str, Any]], Dict[str, Any]] = lambda file_values: file_values
    encode: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]] = lambda form_values, previous: form_values
    # Rules that need to see the whole form (a repeated name, say), run before encode; [] when the form is fine.
    check_form: Callable[[Dict[str, Any]], List["Problem"]] = lambda form_values: []

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
