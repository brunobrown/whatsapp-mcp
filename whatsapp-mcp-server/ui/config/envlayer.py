"""Environment variables the interface can edit, kept in ui_settings.json.

The environment wins over the file, which wins over the default. A variable is locked when the environment defines
it, and only the variables in `uisettings.EDITABLE_ENV` are ever read from or written to the file.

A change needs a restart of the process that reads the variable (the scope of the section that owns it):
  - bridge: the interface starts with the bridge, so what the interface read when it started is what the bridge
    read; the note stays while the file differs from that baseline;
  - server: each running MCP server leaves a note of what it read (uisettings.register_runtime); the note stays
    while a live server holds a value different from the file, and goes away when the server is seen restarted.
"""
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

import uisettings
from ui.config.fields import Field, Problem
from ui.config.registry import EnvKeys, JsonFile, Registry, Section
from ui.config.store import ConfigStore, SaveResult

_TRUE, _FALSE = {"true", "1", "yes", "on"}, {"false", "0", "no", "off"}
SETTINGS_ID = "ui_settings"


def _file_problems(values: Dict[str, Any]) -> List[Problem]:
    env = values.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        return [Problem(None, "ui_settings.json: \"env\" must be an object whose values are text")]
    return []


SETTINGS_SECTION = Section(id=SETTINGS_ID, title="ui_settings.json", order=0, listed=False,
                           storage=JsonFile(uisettings.path), validator=_file_problems)


def from_env(f: Field, text: str) -> Any:
    """The typed value a variable's text stands for; the default when it cannot be read."""
    text = text.strip()
    try:
        if f.kind == "bool":
            low = text.lower()
            return True if low in _TRUE else False if low in _FALSE else f.default
        if f.kind == "int":
            return int(text)
        if f.kind == "float":
            return float(text)
        if f.kind in ("list", "multichoice"):
            return [part.strip() for part in text.split(",") if part.strip()]
    except ValueError:
        return f.default
    return text


def to_env(f: Field, value: Any) -> str:
    if f.kind == "bool":
        return "true" if value else "false"
    if f.kind in ("list", "multichoice"):
        return ",".join(value)
    if f.kind == "float":
        return f"{value:g}"
    return str(value)


@dataclass
class FieldState:
    key: str
    value: Any
    default: Any
    origin: str                      # "environment", "file" or "default"
    locked: bool
    env_var: Optional[str]
    scope: Optional[str]             # which process reads it: "bridge" or "server"
    pending_restart: Optional[str]   # the scope that must restart for the saved value to apply, or None


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class EnvLayer:
    def __init__(self, registry: Registry, store: ConfigStore, environ: Optional[Mapping[str, str]] = None,
                 baseline: Optional[Mapping[str, str]] = None, runtime_dir: Optional[str] = None):
        if not registry.has(SETTINGS_ID):
            registry.register(SETTINGS_SECTION)
        self._registry = registry
        self._store = store
        self.environ = os.environ if environ is None else environ
        self._runtime_dir = runtime_dir
        self._baseline = self.file_env() if baseline is None else dict(baseline)

    # -- the file ----------------------------------------------------------------------------------
    def _loaded(self):
        return self._store.load(SETTINGS_ID)

    @property
    def problem(self) -> Optional[str]:
        """Why the file is being ignored, or None. Never carries what the file holds."""
        loaded = self._loaded()
        if loaded.error:
            return f"ui_settings.json is ignored: {loaded.error}"
        if loaded.problems:
            return f"ui_settings.json is ignored: {loaded.problems[0].message}"
        return None

    def version(self) -> str:
        return self._loaded().version

    def file_env(self) -> Dict[str, str]:
        """The editable variables the file holds, as text; {} when the file is missing or unusable."""
        loaded = self._loaded()
        if loaded.error or loaded.problems:
            return {}
        env = loaded.values.get("env", {})
        return {k: v for k, v in env.items() if k in uisettings.EDITABLE_ENV}

    # -- reading -----------------------------------------------------------------------------------
    def origin(self, var: str) -> str:
        if var in self.environ:
            return "environment"
        return "file" if var in self.file_env() else "default"

    def state(self, section_id: str) -> List[FieldState]:
        section = self._registry.get(section_id)
        file_env = self.file_env()
        pending = self.pending()
        out = []
        for f in section.fields:
            var = f.env_var
            editable = var in uisettings.EDITABLE_ENV
            origin = self.origin(var) if editable else "default"
            raw = self.environ.get(var) if origin == "environment" else file_env.get(var)
            scope = section.restart
            out.append(FieldState(
                key=f.key, value=f.default if raw is None else from_env(f, raw), default=f.default, origin=origin,
                locked=origin == "environment" or f.readonly or not editable, env_var=var, scope=scope,
                pending_restart=scope if scope and var in pending.get(scope, []) else None))
        return out

    # -- restart -----------------------------------------------------------------------------------
    def _env_fields(self):
        for section in self._registry.sections() + [self._registry.get(SETTINGS_ID)]:
            if isinstance(section.storage, EnvKeys) and section.restart:
                for f in section.fields:
                    if f.env_var in uisettings.EDITABLE_ENV and f.env_var not in self.environ:
                        yield section.restart, f.env_var

    def pending(self) -> Dict[str, List[str]]:
        file_env = self.file_env()
        notes = self._server_notes()
        out: Dict[str, List[str]] = {"bridge": [], "server": []}
        for scope, var in self._env_fields():
            if scope == "bridge" and file_env.get(var) != self._baseline.get(var):
                out["bridge"].append(var)
            elif scope == "server" and any(read.get(var) != file_env.get(var) for read in notes):
                out["server"].append(var)
        return {scope: sorted(set(vars_)) for scope, vars_ in out.items()}

    def _server_notes(self) -> List[Dict[str, str]]:
        """What each running MCP server read from the file; the notes of servers that are gone are removed."""
        folder = self._runtime_dir or uisettings.runtime_dir()
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        reads = []
        for name in names:
            if not (name.startswith("server-") and name.endswith(".json")):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                pid, read = data["pid"], data["file_env"]
                if isinstance(pid, bool) or not isinstance(pid, int) or not isinstance(read, dict):
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if not _alive(pid):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            reads.append({k: v for k, v in read.items() if isinstance(v, str)})
        return reads

    # -- writing -----------------------------------------------------------------------------------
    def save(self, section_id: str, changes: Dict[str, Any], expected_version: str, confirm_replace: bool = False) -> SaveResult:
        """Apply `changes` (field key -> new typed value, None to go back to the default) to ui_settings.json."""
        section = self._registry.get(section_id)
        if not isinstance(section.storage, EnvKeys):
            raise ValueError(f"section {section_id!r} is not kept in ui_settings.json")
        env = dict(self.file_env())
        problems: List[Problem] = []
        for key, value in changes.items():
            try:
                f = section.field(key)
            except KeyError:
                problems.append(Problem(key, "is not a field of this section"))
                continue
            if f.readonly:
                problems.append(Problem(key, "is read-only"))
            elif f.env_var not in uisettings.EDITABLE_ENV:
                problems.append(Problem(key, "is not editable here"))
            elif f.env_var in self.environ:
                problems.append(Problem(key, f"is set by the environment variable {f.env_var}; remove it from the environment to edit it here"))
            else:
                why = f.check(value)
                if why:
                    problems.append(Problem(key, why))
                elif value is None:
                    env.pop(f.env_var, None)
                else:
                    env[f.env_var] = to_env(f, value)
        if problems:
            return SaveResult(False, errors=problems)
        resulting = {f.key: from_env(f, env[f.env_var]) for f in section.fields if f.env_var in env}
        problems = list(section.validator(resulting))
        if problems:
            return SaveResult(False, errors=problems)
        return self._store.save(SETTINGS_ID, {"version": 1, "env": env}, expected_version, confirm_replace)
