"""Settings the web interface saved in ui_settings.json, applied as defaults before anything reads the environment.

The file only fills in a variable that is missing from the environment, so the environment always wins and the
modules keep reading their variables as before. Only the variables in EDITABLE_ENV can come from the file: never a
secret, a key, the configuration directory, a path to another file or the opt-in to a remote bind.
"""
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from typing import List, MutableMapping, Optional

import localconfig

FILE_NAME = "ui_settings.json"

# The same list is in whatsapp-bridge/uisettings.go; a test of the bridge compares the two.
EDITABLE_ENV = (
    "WHATSAPP_AUTO_DOWNLOAD",
    "WHATSAPP_AUTO_DOWNLOAD_MAX_MB",
    "WHATSAPP_AUTO_DOWNLOAD_TYPES",
    "WHATSAPP_MEDIA_RETENTION_DAYS",
    "WHATSAPP_KEEP_DELETED_CONTENT",
    "WHATSAPP_LOG_MESSAGES",
    "WHATSAPP_TIMEZONE",
    "WHATSAPP_MCP_MODE",
    "WHATSAPP_SESSION_TAINT",
    "WHATSAPP_MONITOR_DRAFT_TTL_HOURS",
    "WHATSAPP_UI",
    "WHATSAPP_UI_HOST",
    "WHATSAPP_UI_PORT",
)


@dataclass
class Result:
    applied: List[str] = field(default_factory=list)
    ignored_keys: List[str] = field(default_factory=list)
    problem: Optional[str] = None      # why the whole file was ignored; never carries its content


def path() -> str:
    return os.path.join(localconfig.config_dir(), FILE_NAME)


def _load(file_path: str):
    """(values, problem) of the file: values is {} when there is no file or nothing usable."""
    try:
        mode = stat.S_IMODE(os.stat(file_path).st_mode)
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        return {}, f"{FILE_NAME} cannot be read ({e.strerror})"
    if sys.platform != "win32" and mode & 0o077:
        return {}, f"{FILE_NAME} can be read by other users (mode {mode:04o}); run chmod 600 {file_path}"
    try:
        with open(file_path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, RecursionError):
        return {}, f"{FILE_NAME} is not valid JSON"
    if not isinstance(raw, dict):
        return {}, f"{FILE_NAME} must be a JSON object"
    values = raw.get("env", {})
    if not isinstance(values, dict):
        return {}, f'{FILE_NAME}: "env" must be an object'
    return values, None


def apply(environ: Optional[MutableMapping[str, str]] = None) -> Result:
    """Fill the editable variables that are missing from environ (os.environ by default) from the file."""
    environ = os.environ if environ is None else environ
    values, problem = _load(path())
    result = Result(problem=problem)
    for name, value in values.items():
        if name not in EDITABLE_ENV or not isinstance(value, str):
            result.ignored_keys.append(str(name))
        elif name not in environ:
            environ[name] = value
            result.applied.append(name)
    if result.problem:
        print(f"ui_settings: ignored: {result.problem}", file=sys.stderr)
    if result.ignored_keys:
        print(f"ui_settings: ignored keys: {', '.join(sorted(result.ignored_keys))}", file=sys.stderr)
    return result
