"""The flow of editing one section: load it, apply changes, validate, save, and say what happened.

A FormFlow is short-lived: build it, `load()` (which takes the current version of the file), set `version` to the
one the client loaded, `apply()` the client's changes, then `save()`. Nothing is written unless every value is
valid, and a file that changed since the client loaded it is a conflict, never overwritten.
"""
import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ui.config.envlayer import EnvLayer
from ui.config.fields import Problem
from ui.config.inputs import from_raw
from ui.config.registry import EnvKeys, JsonFile, Registry
from ui.config.secrets import mask, merge
from ui.config.store import ConfigStore


@dataclass
class FormView:
    section_id: str
    title: str
    description: str
    restart: Optional[str]
    read_only: bool
    status: str                        # "ready", "empty" (nothing saved yet) or "error" (the file has a problem)
    banner: Optional[str]
    warnings: List[str]
    version: str
    values: Dict[str, Any]             # secrets masked
    state: Dict[str, Dict[str, Any]]   # per field: origin, locked, pending


@dataclass
class Validation:
    errors: Dict[str, str] = field(default_factory=dict)
    general: List[str] = field(default_factory=list)

    @property
    def can_save(self) -> bool:
        return not self.errors and not self.general


@dataclass
class SaveOutcome:
    ok: bool = False
    unchanged: bool = False
    conflict: bool = False
    needs_confirmation: bool = False
    errors: Dict[str, str] = field(default_factory=dict)
    general: List[str] = field(default_factory=list)
    restarts: List[str] = field(default_factory=list)


def _split(problems: Sequence[Problem], errors: Dict[str, str], general: List[str]) -> None:
    for p in problems:
        if p.key is None:
            general.append(p.message)
        else:
            errors.setdefault(p.key, p.message)


class FormFlow:
    def __init__(self, section_id: str, registry: Registry, store: ConfigStore, envlayer: Optional[EnvLayer] = None):
        self.section = registry.get(section_id)
        self._store = store
        self._env = envlayer
        self.version = ""
        self._stored: Dict[str, Any] = {}
        self._draft: Dict[str, Any] = {}
        self._errors: Dict[str, str] = {}
        self._restored: List[str] = []
        self._status, self._banner, self._warnings = "ready", None, []
        self._states: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    # -- loading -----------------------------------------------------------------------------------
    @property
    def _is_env(self) -> bool:
        return isinstance(self.section.storage, EnvKeys)

    @property
    def _is_file(self) -> bool:
        return isinstance(self.section.storage, JsonFile)

    def load(self) -> FormView:
        s = self.section
        self._errors, self._restored = {}, []
        self._status, self._banner, self._warnings = "ready", None, []
        self._states = {f.key: {"origin": "default", "locked": s.read_only, "pending": None} for f in s.fields}
        if self._is_env:
            self._load_env()
        elif self._is_file:
            self._load_file()
        else:
            self.version, self._stored = "read-only", {}
        self._draft = copy.deepcopy(self._stored)
        self._loaded = True
        return self.view()

    def _load_file(self) -> None:
        loaded = self._store.load(self.section.id)
        self.version, self._stored = loaded.version, copy.deepcopy(loaded.values)
        self._warnings = list(loaded.warnings)
        if loaded.error:
            self._status, self._banner = "error", loaded.error
        elif loaded.problems:
            self._status, self._banner = "error", "; ".join(p.message for p in loaded.problems)
        elif not loaded.exists:
            self._status = "empty"
        for key in self._stored:
            if key in self._states:
                self._states[key]["origin"] = "file"

    def _load_env(self) -> None:
        env = self._env
        self.version = env.version()
        if env.problem:
            self._status, self._banner = "error", env.problem
        self._stored = {}
        for st in env.state(self.section.id):
            self._states[st.key] = {"origin": st.origin, "locked": st.locked, "pending": st.pending_restart}
            self._stored[st.key] = st.value
        self._custom = {st.key for st in env.state(self.section.id) if st.origin == "file"}

    # -- changing ----------------------------------------------------------------------------------
    def apply(self, changes: Dict[str, Any], restore: Sequence[str] = ()) -> None:
        """Apply the client's changes: raw text for text, numbers and lists, values for the rest, secrets as in
        ui.config.secrets. `restore` names the fields to take back to their defaults; locked fields are skipped."""
        s = self.section
        if not self._loaded:
            self.load()
        incoming: Dict[str, Any] = {}
        for key, raw in changes.items():
            if not self._editable(key):
                continue
            f = s.field(key)
            if f.kind in ("secret", "records"):
                incoming[key] = raw
                continue
            parsed = from_raw(f, raw)
            if parsed.ok:
                incoming[key] = parsed.value
            else:
                self._errors[key] = parsed.error
        merged = merge(s.fields, self._draft, incoming)
        _split(merged.problems, self._errors, [])
        self._draft = merged.values
        for key in restore:
            if key not in {f.key for f in s.fields}:
                self._errors[key] = "is not a field of this section"
            elif s.read_only:
                self._errors[key] = "is read-only"
            elif self._states[key]["locked"]:
                continue                                    # a locked field keeps its value
            else:
                self._draft.pop(key, None)
                self._restored.append(key)
                if self._is_env:
                    self._draft[key] = s.field(key).default

    def _editable(self, key: str) -> bool:
        """True when `key` may change; otherwise records why in the errors."""
        if key not in {f.key for f in self.section.fields}:
            self._errors[key] = "is not a field of this section"
        elif self.section.read_only or self.section.field(key).readonly:
            self._errors[key] = "is read-only"
        elif self._states[key]["locked"]:
            var = self.section.field(key).env_var
            self._errors[key] = (f"is set by the environment variable {var}; remove it from the environment to edit it here"
                                 if var else "is locked")
        else:
            return True
        return False

    # -- checking and saving -----------------------------------------------------------------------
    def validate(self) -> Validation:
        result = Validation(errors=dict(self._errors))
        for f in self.section.fields:
            if f.key in self._draft and f.key not in result.errors:
                why = f.check(self._draft[f.key])
                if why:
                    result.errors[f.key] = why
        if self._is_file and not result.errors:
            _split(self.section.validator(self._draft), result.errors, result.general)
        if self.section.read_only and not result.errors:
            result.general.append("this section is read-only")
        return result

    def view(self) -> FormView:
        s = self.section
        values = mask(s.fields, self._draft) if self._is_file else {f.key: self._draft.get(f.key) for f in s.fields}
        return FormView(s.id, s.title, s.description, s.restart, s.read_only, self._status, self._banner,
                        list(self._warnings), self.version, values, copy.deepcopy(self._states))

    def save(self, confirm_replace: bool = False) -> SaveOutcome:
        check = self.validate()
        if not check.can_save:
            return SaveOutcome(errors=check.errors, general=check.general)
        if self._unchanged():
            return SaveOutcome(ok=True, unchanged=True)
        if self._is_env:
            result = self._env.save(self.section.id, self._env_changes(), self.version, confirm_replace)
        else:
            result = self._store.save(self.section.id, self._draft, self.version, confirm_replace)
        outcome = SaveOutcome(ok=result.ok, conflict=result.conflict, needs_confirmation=result.needs_confirmation)
        _split(result.errors, outcome.errors, outcome.general)
        if result.ok and self.section.restart:
            outcome.restarts = [self.section.restart]
        return outcome

    def _unchanged(self) -> bool:
        if self._is_env:
            return not self._env_changes()
        return self._draft == self._stored

    def _env_changes(self) -> Dict[str, Any]:
        """Field key -> new typed value, or None to go back to the default."""
        changes: Dict[str, Any] = {}
        for f in self.section.fields:
            if self._states[f.key]["locked"]:
                continue
            key = f.key
            if key in self._restored:
                if key in self._custom:
                    changes[key] = None
            elif self._draft.get(key) != self._stored.get(key):
                changes[key] = self._draft.get(key)
        return changes
