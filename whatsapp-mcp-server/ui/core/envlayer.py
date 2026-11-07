"""Variables kept in ui_settings.json: where each value comes from, what is locked, what needs a restart.

The environment wins over the file, which wins over the default (the bridge and the server apply the file only
to variables the environment lacks). Both read it once, when they start, so a change is pending until the process
that reads the variable starts again:
  - for the bridge, the interface starts with the bridge, so what the interface read at its own start is what
    the bridge read;
  - for the MCP server, which the client starts on its own, each running server leaves a note (uisettings.
    register_runtime) with what it read, and the interface compares it with the file.
"""
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, MutableMapping, Optional

import uisettings
from ui.core.registry import EnvKeys, Field, JsonFile, Problem, Section
from ui.core.store import ABSENT, ConfigStore, Loaded, SaveResult, config_path

SCOPES = {
    "WHATSAPP_AUTO_DOWNLOAD": "bridge", "WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "bridge", "WHATSAPP_AUTO_DOWNLOAD_TYPES": "bridge",
    "WHATSAPP_MEDIA_RETENTION_DAYS": "bridge", "WHATSAPP_KEEP_DELETED_CONTENT": "bridge", "WHATSAPP_LOG_MESSAGES": "bridge",
    "WHATSAPP_UI": "bridge", "WHATSAPP_UI_HOST": "bridge", "WHATSAPP_UI_PORT": "bridge",
    "WHATSAPP_TIMEZONE": "server", "WHATSAPP_MCP_MODE": "server", "WHATSAPP_SESSION_TAINT": "server",
    "WHATSAPP_MONITOR_DRAFT_TTL_HOURS": "server",
}
_TRUE, _FALSE = {"true", "1", "yes", "on"}, {"false", "0", "no", "off"}


def _settings_file_check(values: Dict[str, Any]) -> List[Problem]:
    env = values.get("env", {})
    if not isinstance(env, dict) or not all(k in uisettings.EDITABLE_ENV and isinstance(v, str) for k, v in env.items()):
        return [Problem(None, "ui_settings.json: env deve ser um objeto com variáveis editáveis e valores em texto")]
    return []


# The file itself, as a section the store can read and write.
SETTINGS_SECTION = Section(
    id="ui_settings", title="ui_settings.json", icon="settings", order=0,
    storage=JsonFile(lambda: config_path(uisettings.FILE_NAME)),
    fields=(Field("version", "Versão", "int"), Field("env", "Variáveis", "list")),
    validate=_settings_file_check)


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
    origin: str                      # "env", "file" or "default"
    locked: bool
    readonly: bool
    env_var: str
    scope: str                       # which process reads it: "bridge" or "server"
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
    def __init__(self, environ: Optional[Mapping[str, str]] = None, store: Optional[ConfigStore] = None,
                 baseline: Optional[Dict[str, str]] = None):
        self.environ = os.environ if environ is None else environ
        self.store = store or ConfigStore()
        self._baseline = self._file_env() if baseline is None else dict(baseline)

    # -- reading ---------------------------------------------------------------------------------
    def scope_of(self, var: str) -> str:
        return SCOPES[var]

    def load(self) -> Loaded:
        return self.store.load(SETTINGS_SECTION)

    def _file_env(self) -> Dict[str, str]:
        env = self.load().values.get("env", {})
        return {k: v for k, v in env.items() if k in uisettings.EDITABLE_ENV and isinstance(v, str)} if isinstance(env, dict) else {}

    def origin(self, var: str) -> str:
        if var in self.environ:
            return "env"
        return "file" if var in self._file_env() else "default"

    def state(self, section: Section) -> List[FieldState]:
        file_env = self._file_env()
        pending = self.pending()
        states = []
        for f in section.fields:
            var = f.env_var
            origin = self.origin(var)
            raw = self.environ.get(var) if origin == "env" else file_env.get(var)
            scope = SCOPES.get(var, "bridge")
            states.append(FieldState(
                key=f.key, value=f.default if raw is None else from_env(f, raw), default=f.default, origin=origin,
                locked=origin == "env" or f.readonly, readonly=f.readonly, env_var=var, scope=scope,
                pending_restart=scope if var in pending.get(scope, []) else None))
        return states

    # -- restart ---------------------------------------------------------------------------------
    def pending(self) -> Dict[str, List[str]]:
        file_env = self._file_env()
        out = {"bridge": [], "server": []}
        for var in sorted(uisettings.EDITABLE_ENV):
            if var in self.environ:
                continue
            if SCOPES[var] == "bridge" and file_env.get(var) != self._baseline.get(var):
                out["bridge"].append(var)
        for read in self._server_notes():
            for var in sorted(uisettings.EDITABLE_ENV):
                if SCOPES[var] == "server" and var not in self.environ and read.get(var) != file_env.get(var) \
                        and var not in out["server"]:
                    out["server"].append(var)
        out["server"].sort()
        return out

    def _server_notes(self) -> List[Dict[str, str]]:
        """What each running MCP server read from the file; notes of servers that are gone are removed."""
        folder = uisettings.runtime_dir()
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
                pid = data["pid"]
                read = data["file_env"]
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

    # -- writing ---------------------------------------------------------------------------------
    def save(self, section: Section, changes: Dict[str, Any], expected_version: str) -> SaveResult:
        """Apply changes (field key -> new value, None to go back to the default) to ui_settings.json."""
        if not isinstance(section.storage, EnvKeys):
            raise ValueError(f"section {section.id!r} is not kept in ui_settings.json")
        loaded = self.load()
        env = dict(loaded.values.get("env", {})) if isinstance(loaded.values.get("env"), dict) else {}
        problems: List[Problem] = []
        for key, value in changes.items():
            f = section.field(key)                       # KeyError for a field the section does not have
            if f.readonly:
                problems.append(Problem(key, "somente leitura"))
            elif f.env_var in self.environ:
                problems.append(Problem(key, f"definido pela variável de ambiente {f.env_var}; remova-a do ambiente do processo para editar aqui"))
            else:
                why = f.check(value)
                if why:
                    problems.append(Problem(key, why))
                elif value is None:
                    env.pop(f.env_var, None)
                else:
                    env[f.env_var] = to_env(f, value)
        if problems:
            return SaveResult(False, problems)
        resulting = {}
        for f in section.fields:
            raw = env.get(f.env_var)
            if raw is not None:
                resulting[f.key] = from_env(f, raw)
        problems = list(section.validate(resulting))
        if problems:
            return SaveResult(False, problems)
        return self.store.save(SETTINGS_SECTION, {"version": 1, "env": env}, expected_version)
