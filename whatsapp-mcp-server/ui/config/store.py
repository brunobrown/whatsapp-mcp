"""Reading and writing the JSON file of a registered section.

The path comes from the registry, never from a caller. A file is read together with its version (the hash of its
bytes). A write is accepted only if the file still has the version the caller read, goes to a private temporary
file in the same folder and replaces the old file in one step, so a reader never sees half a file. A file that
exists but cannot be used is reported and is replaced only when the caller confirms. The text of a file that
cannot be used is never copied into a message, because it may hold a secret.
"""
import hashlib
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import filemode
from ui.config.fields import Problem
from ui.config.registry import Registry

ABSENT = "absent"          # the version of a file that does not exist


@dataclass
class Loaded:
    values: Dict[str, Any] = field(repr=False)            # may hold secrets: never printed
    version: str
    exists: bool = False
    error: Optional[str] = None                           # why the file cannot be used at all
    unreadable: bool = False                              # saving would replace something that could not be understood
    problems: List[Problem] = field(default_factory=list)  # what the section's validator says about the values
    warnings: List[str] = field(default_factory=list)


@dataclass
class SaveResult:
    ok: bool
    version: Optional[str] = None
    errors: List[Problem] = field(default_factory=list)
    conflict: bool = False
    needs_confirmation: bool = False


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: str) -> Tuple[Optional[bytes], Optional[str]]:
    """(bytes or None when absent, a reason when the file cannot be read)."""
    try:
        with open(path, "rb") as f:
            return f.read(), None
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"the file cannot be read ({e.strerror})"


def _parse(data: bytes) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return {}, "the file is not valid JSON"
    if not isinstance(parsed, dict):
        return {}, "the file must contain a JSON object"
    return parsed, None


class ConfigStore:
    def __init__(self, registry: Registry) -> None:
        self._registry = registry

    def load(self, section_id: str) -> Loaded:
        path = self._registry.path_of(section_id)
        data, problem = _read(path)
        if problem:
            return Loaded({}, "unreadable", error=problem, unreadable=True)
        if data is None:
            return Loaded({}, ABSENT)
        loaded = Loaded({}, _version(data), exists=True)
        warning = filemode.open_access_warning(path)
        if warning:
            loaded.warnings.append(warning)
        values, loaded.error = _parse(data)
        if loaded.error:
            loaded.unreadable = True
            return loaded
        loaded.values = values
        loaded.problems = list(self._registry.get(section_id).validator(values))
        return loaded

    def save(self, section_id: str, values: Dict[str, Any], expected_version: str, confirm_replace: bool = False) -> SaveResult:
        section = self._registry.get(section_id)
        path = self._registry.path_of(section_id)
        problems = list(section.validator(values))
        if problems:
            return SaveResult(False, errors=problems)
        current, problem = _read(path)
        if problem:
            return SaveResult(False, errors=[Problem(None, problem)])
        if (ABSENT if current is None else _version(current)) != expected_version:
            return SaveResult(False, errors=[Problem(None, "the file changed on disk after it was loaded")], conflict=True)
        if current is not None and _parse(current)[1] and not confirm_replace:
            return SaveResult(False, errors=[Problem(None, "the current file could not be understood and would be replaced")],
                              needs_confirmation=True)
        data = (json.dumps(values, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            _write_private(path, data)
        except OSError as e:
            return SaveResult(False, errors=[Problem(None, f"the file could not be written ({e.strerror})")])
        return SaveResult(True, version=_version(data))


def _write_private(path: str, data: bytes) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=folder)        # created 0600
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if sys.platform != "win32":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        try:
            dir_fd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
