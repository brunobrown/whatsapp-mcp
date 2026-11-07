"""The registry of sections: what the interface can show and which files it may open.

A section declares its fields, where its values are kept and how they are validated. Menu and pages are built from
the registry, so a new section is a new module that declares itself. A path is opened only if a registered section
declares it: nothing outside the registry is ever turned into a path.
"""
import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ui.config.fields import Field, Problem

RESTART_SCOPES = ("bridge", "server")


@dataclass(frozen=True)
class JsonFile:
    """Values kept in a JSON file; `path` is called each time so a changed configuration directory is followed."""
    path: Callable[[], str]


@dataclass(frozen=True)
class EnvKeys:
    """Values kept as environment variables, in ui_settings.json."""


@dataclass(frozen=True)
class ReadOnly:
    """Facts that are shown and never edited."""


Storage = Union[JsonFile, EnvKeys, ReadOnly]


def _identity(values: Dict[str, Any]) -> Dict[str, Any]:
    return values


def _no_problems(_values: Dict[str, Any]) -> List[Problem]:
    return []


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    order: int
    storage: Storage
    fields: Tuple[Field, ...] = ()
    validator: Callable[[Dict[str, Any]], List[Problem]] = field(default=_no_problems, repr=False)
    restart: Optional[str] = None
    description: str = ""
    icon: str = ""
    listed: bool = True
    # A file whose shape does not fit the form (entries keyed by name, say) is shown through these two: `decode`
    # turns the file's values into the form's, `encode` turns them back. The validator always sees the file shape.
    decode: Callable[[Dict[str, Any]], Dict[str, Any]] = field(default=_identity, repr=False)
    encode: Callable[[Dict[str, Any]], Dict[str, Any]] = field(default=_identity, repr=False)
    # For a ReadOnly section: the facts to show, read each time the section is opened.
    facts: Optional[Callable[[], Dict[str, Any]]] = field(default=None, repr=False)

    def __post_init__(self):
        keys = [f.key for f in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError(f"section {self.id!r}: a field key repeats")
        if self.restart is not None and self.restart not in RESTART_SCOPES:
            raise ValueError(f"section {self.id!r}: restart must be one of {RESTART_SCOPES} or None")

    @property
    def read_only(self) -> bool:
        return isinstance(self.storage, ReadOnly)

    def field(self, key: str) -> Field:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)


class Registry:
    def __init__(self) -> None:
        self._sections: Dict[str, Section] = {}

    def register(self, section: Section) -> Section:
        if section.id in self._sections:
            raise ValueError(f"section {section.id!r} is already registered")
        self._sections[section.id] = section
        return section

    def has(self, section_id: str) -> bool:
        return section_id in self._sections

    def get(self, section_id: str) -> Section:
        """Any registered section, listed or not. KeyError for an id the registry does not know."""
        return self._sections[section_id]

    def sections(self) -> List[Section]:
        """The listed sections, in menu order."""
        return sorted((s for s in self._sections.values() if s.listed), key=lambda s: (s.order, s.id))

    def path_of(self, section_id: str) -> str:
        """The file a registered section declares; the only way a file path is obtained."""
        section = self.get(section_id)
        if not isinstance(section.storage, JsonFile):
            raise ValueError(f"section {section_id!r} is not kept in a JSON file")
        return section.storage.path()

    def discover(self, package: str) -> None:
        """Import every module of `package` and register the SECTION or SECTIONS it declares."""
        pkg = importlib.import_module(package)
        for info in pkgutil.iter_modules(pkg.__path__):
            module = importlib.import_module(f"{package}.{info.name}")
            declared = [getattr(module, "SECTION", None), *getattr(module, "SECTIONS", ())]
            for section in declared:
                if isinstance(section, Section):
                    self.register(section)
