"""The draft of a form: what the operator changed, and the rules around leaving, restoring and removing.

Pure state, no screen. A value of None means "no custom value" (the default). A secret is held as the marker
{"set": bool} until the operator types a new one (or asks to clear it), so it never has the stored text.
"""
import copy
from typing import Any, Dict, Iterable, Optional

from ui.core.registry import Section


class LockedField(Exception):
    """The field cannot be changed: the environment defines it, or it is read-only."""


class Draft:
    def __init__(self, section: Section, original: Dict[str, Any], locked: Iterable[str] = ()):
        self.section = section
        self.locked = frozenset(locked)
        self._original = copy.deepcopy(original)
        self.values = copy.deepcopy(original)

    # -- what changed ------------------------------------------------------------------------------
    def _differs(self, key: str) -> bool:
        return self.values.get(key) != self._original.get(key)

    @property
    def dirty(self) -> bool:
        return any(self._differs(f.key) for f in self.section.fields)

    def changes(self) -> Dict[str, Any]:
        return {f.key: copy.deepcopy(self.values.get(f.key)) for f in self.section.fields if self._differs(f.key)}

    def errors(self) -> Dict[str, str]:
        out = {}
        for f in self.section.fields:
            why = f.check(self.values.get(f.key)) if not f.secret or isinstance(self.values.get(f.key), str) else None
            if why:
                out[f.key] = why
        return out

    # -- editing -----------------------------------------------------------------------------------
    def _field(self, key: str):
        f = self.section.field(key)          # KeyError for a field the section does not have
        if key in self.locked or f.readonly:
            raise LockedField(f"{key} cannot be changed here")
        return f

    def set(self, key: str, value: Any) -> None:
        self._field(key)
        self.values[key] = copy.deepcopy(value)

    def add_item(self, key: str, item: Dict[str, Any]) -> None:
        self._field(key)
        self.values[key] = list(self.values.get(key) or []) + [copy.deepcopy(item)]

    def remove_item(self, key: str, index: int, confirmed: bool = False) -> bool:
        """Remove the item at index; False (nothing done) until the operator has confirmed."""
        self._field(key)
        items = list(self.values.get(key) or [])
        if not 0 <= index < len(items):
            raise IndexError(f"{key} has no item {index}")
        if not confirmed:
            return False
        del items[index]
        self.values[key] = items
        return True

    def restore_default(self, key: str, confirmed: bool = False) -> bool:
        self._field(key)
        if not confirmed:
            return False
        self.values[key] = None
        return True

    def restore_defaults(self, confirmed: bool = False) -> bool:
        """Every field that can be changed goes back to its default; locked fields stay as they are."""
        if not confirmed:
            return False
        for f in self.section.fields:
            if f.key not in self.locked and not f.readonly:
                self.values[f.key] = None
        return True

    # -- leaving -----------------------------------------------------------------------------------
    def discard(self) -> None:
        self.values = copy.deepcopy(self._original)

    def leave(self, confirmed: bool = False) -> bool:
        """May the operator go elsewhere? Not with unsaved changes unless they confirm, which drops them."""
        if self.dirty and not confirmed:
            return False
        self.discard()
        return True

    def saved(self, new_original: Optional[Dict[str, Any]] = None) -> None:
        """The changes were stored: what is on screen is now the base."""
        self._original = copy.deepcopy(self.values if new_original is None else new_original)
        self.values = copy.deepcopy(self._original)
