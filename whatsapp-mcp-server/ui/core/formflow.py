"""The flow of one section's form: load, edit, check, save, conflict.

No Flet here. The screen shows what this holds and calls what this offers; the rules (what a typed text means, what
may be saved, what a conflict is, that a secret is kept unless replaced) live here, where the tests reach them.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ui.core.draft import Draft, LockedField
from ui.core.envlayer import EnvLayer
from ui.core.registry import EnvKeys, Field, JsonFile, Problem, ReadOnly, Section
from ui.core.secrets import CLEAR, mask, merge
from ui.core.store import ABSENT, ConfigStore
from ui.core.inputs import from_raw

BRIDGE = "_bridge"


@dataclass
class SaveOutcome:
    ok: bool
    unchanged: bool = False
    conflict: bool = False
    needs_confirmation: bool = False
    errors: Dict[str, str] = field(default_factory=dict)       # by field key
    general: List[str] = field(default_factory=list)           # what is not about one field
    restarts: List[str] = field(default_factory=list)          # "bridge" and/or "server"

    @property
    def restart(self) -> Optional[str]:
        return self.restarts[0] if self.restarts else None


def blank_item(f: Field) -> Dict[str, Any]:
    return {sub.key: [] if sub.kind in ("list", "multichoice") else None for sub in f.fields}


def _top(section: Section, key: Optional[str]) -> Optional[str]:
    head = (key or "").split(".")[0]
    return head if head in {f.key for f in section.fields} else None


def _is_set(value: Any) -> bool:
    return value is not None and value != [] and value != {"set": False} and value != ""


class FormFlow:
    def __init__(self, section: Section, store: ConfigStore, env: EnvLayer):
        self.section, self.store, self.env = section, store, env
        self.status = "empty"
        self.banner: Optional[str] = None
        self.warnings: List[str] = []
        self.version = ABSENT
        self.draft = Draft(section, {})
        self._parse: Dict[str, str] = {}
        self._typed: Dict[str, str] = {}
        self._touched: set = set()
        self._server: Dict[str, str] = {}
        self._origin: Dict[str, str] = {}
        self._locked: set = set()
        self._states: Dict[str, Any] = {}
        self._unreadable = False

    # -- kind of storage --------------------------------------------------------------------------
    @property
    def _env(self) -> bool:
        return isinstance(self.section.storage, EnvKeys)

    @property
    def _read_only(self) -> bool:
        return isinstance(self.section.storage, ReadOnly)

    # -- loading ----------------------------------------------------------------------------------
    def load(self) -> None:
        self._parse, self._typed, self._touched, self._server = {}, {}, set(), {}
        self.banner, self.warnings, self._unreadable = None, [], False
        problems: List[Problem] = []
        if self._read_only:
            values = mask(self.section, self.section.storage.values())
            self._locked = {f.key for f in self.section.fields}
            self._origin = {f.key: "default" for f in self.section.fields}
            self.status, self.version = "ready", ABSENT
        elif self._env:
            loaded = self.env.load()
            self._states = {s.key: s for s in self.env.state(self.section)}
            values = {k: s.value for k, s in self._states.items()}
            self._locked = {k for k, s in self._states.items() if s.locked}
            self._origin = {k: s.origin for k, s in self._states.items()}
            self.version, self._unreadable, problems = loaded.version, loaded.unreadable, list(loaded.errors)
            self.warnings = list(loaded.warnings)
            self.status = "empty" if not loaded.exists else "ready"
        else:
            loaded = self.store.load(self.section)
            form = self.section.decode(loaded.values) if not loaded.unreadable else self.section.decode({})
            values = mask(self.section, form)
            self._locked = {f.key for f in self.section.fields if f.readonly}
            self._origin = {f.key: "file" if _is_set(values.get(f.key)) else "default" for f in self.section.fields}
            self.version, self._unreadable, problems = loaded.version, loaded.unreadable, list(loaded.errors)
            self.warnings = list(loaded.warnings)
            self.status = "empty" if not loaded.exists else "ready"
        general = []
        for problem in problems:
            if problem.field == BRIDGE:
                self.status, self.banner = "unavailable", problem.message
            elif _top(self.section, problem.field):
                self._server[_top(self.section, problem.field)] = problem.message
            else:
                general.append(problem.message)
        if general:
            self.banner = self.banner or "; ".join(general)
        if (self._server or general) and self.status != "unavailable":
            self.status = "error"
        self.draft = Draft(self.section, values, locked=self._locked)

    reload = load

    # -- what the screen shows ----------------------------------------------------------------------
    @property
    def dirty(self) -> bool:
        return self.draft.dirty or bool(self._parse)

    @property
    def errors(self) -> Dict[str, str]:
        out = dict(self._server)
        for f in self.section.fields:
            if f.key in self._touched:
                value = self.draft.values.get(f.key)
                why = f.check(value) if not f.secret or isinstance(value, str) else None
                if why:
                    out[f.key] = why
        out.update(self._parse)
        return out

    @property
    def can_save(self) -> bool:
        return not self._read_only and self.status != "unavailable" and not self._parse and not {
            k for k in self.errors if k in self._touched or k in self._parse}

    def shown(self, key: str) -> Any:
        value = self.draft.values.get(key)
        return self.section.field(key).default if value is None else value

    def typed(self, key: str) -> Optional[str]:
        return self._typed.get(key)

    def origin(self, key: str) -> str:
        return self._origin.get(key, "default")

    def is_locked(self, key: str) -> bool:
        return key in self._locked

    def env_var(self, key: str) -> Optional[str]:
        return self.section.field(key).env_var

    def pending(self, key: str) -> Optional[str]:
        state = self._states.get(key)
        return state.pending_restart if state else None

    # -- editing --------------------------------------------------------------------------------------
    def set(self, key: str, raw: Any) -> bool:
        f = self.section.field(key)
        if key in self._locked or f.readonly:
            return False
        self._touched.add(key)
        self._server.pop(key, None)
        self._parse.pop(key, None)
        self._typed.pop(key, None)
        if f.secret:
            if raw is CLEAR or raw == CLEAR:
                self.draft.set(key, CLEAR)
            elif not raw:
                self.draft.set(key, self.draft.original(key))
            else:
                self.draft.set(key, raw)
            return True
        parsed = from_raw(f, raw)
        if f.kind in ("text", "int", "float", "list") and isinstance(raw, str):
            self._typed[key] = raw               # shown back exactly as typed: "2." must not become "2" while the person is still typing
        if parsed.error:
            self._parse[key] = parsed.error
            return True
        self.draft.set(key, raw if f.kind == "records" else parsed.value)
        return True

    def add_item(self, key: str) -> None:
        self._touched.add(key)
        self.draft.add_item(key, blank_item(self.section.field(key)))

    def remove_item(self, key: str, index: int, confirmed: bool = False) -> bool:
        self._touched.add(key)
        return self.draft.remove_item(key, index, confirmed)

    def restore_default(self, key: str, confirmed: bool = False) -> bool:
        done = self.draft.restore_default(key, confirmed)
        if done:
            self._parse.pop(key, None)
            self._typed.pop(key, None)
            self._server.pop(key, None)
        return done

    def discard(self) -> None:
        self.draft.discard()
        self._parse, self._typed, self._touched, self._server = {}, {}, set(), {}

    def leave(self, confirmed: bool = False) -> bool:
        if self.dirty and not confirmed:
            return False
        self.discard()
        return True

    # -- saving ---------------------------------------------------------------------------------------
    def _refuse(self, errors: Dict[str, str], general: Optional[List[str]] = None, **extra) -> SaveOutcome:
        for key, message in errors.items():
            self._server[key] = message
            self._touched.add(key)
        return SaveOutcome(False, errors=errors, general=general or [], **extra)

    def _problems(self, problems: List[Problem]) -> SaveOutcome:
        errors: Dict[str, str] = {}
        general: List[str] = []
        for problem in problems:
            key = _top(self.section, problem.field)
            if key:
                errors[key] = f"{errors[key]}; {problem.message}" if key in errors else problem.message
            else:
                general.append(problem.message)
        return self._refuse(errors, general)

    def save(self, confirmed_replace: bool = False) -> SaveOutcome:
        if self._read_only or self.status == "unavailable":
            return SaveOutcome(False, general=[self.banner or "esta seção não pode ser alterada"])
        self._touched.update(f.key for f in self.section.fields)
        current = self.errors
        if current:
            return SaveOutcome(False, errors=current)
        if not self.dirty:
            return SaveOutcome(True, unchanged=True)
        if self._unreadable and not confirmed_replace:
            return SaveOutcome(False, needs_confirmation=True, general=["o arquivo atual não pôde ser lido e será substituído"])
        return self._save_env() if self._env else self._save_file()

    def _save_file(self) -> SaveOutcome:
        fresh = self.store.load(self.section)
        if fresh.version != self.version:
            return SaveOutcome(False, conflict=True, general=["o arquivo mudou no disco depois de carregado; recarregue para ver o que mudou"])
        old_form = self.section.decode(fresh.values) if not fresh.unreadable else self.section.decode({})
        merged = merge(self.section, old_form, self.draft.changes())
        checked = self.section.check_form(merged)
        if checked:
            return self._problems(checked)
        result = self.store.save(self.section, self.section.encode(merged, fresh.values if not fresh.unreadable else {}), self.version)
        if not result.ok:
            if result.conflict:
                return SaveOutcome(False, conflict=True, general=[p.message for p in result.errors])
            return self._problems(result.errors)
        changed = bool(self.draft.changes())
        self.load()
        return SaveOutcome(True, restarts=[self.section.restart] if self.section.restart and changed else [])

    def _save_env(self) -> SaveOutcome:
        changes: Dict[str, Any] = {}
        for key, value in self.draft.changes().items():
            changes[key] = None if value == self.section.field(key).default else value
        result = self.env.save(self.section, changes, self.version)
        if not result.ok:
            if result.conflict:
                return SaveOutcome(False, conflict=True, general=[p.message for p in result.errors])
            return self._problems(result.errors)
        scopes = sorted({self.env.scope_of(self.section.field(k).env_var) for k in changes})
        self.load()
        return SaveOutcome(True, restarts=scopes)
