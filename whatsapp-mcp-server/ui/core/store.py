"""Reading and writing the files of a section.

The path of a file comes from the Section (the project's own path for it), never from a screen. A file is read
with the hash of its bytes as its version; a write is accepted only if the file still has the version that was
read, is validated first, goes to a private temporary file in the same folder and replaces the old one in a single
step. The text of a file that cannot be used is never kept, because it may hold a secret.
"""
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import filemode
import localconfig
from ui.core.registry import JsonFile, Problem, Section

ABSENT = "absent"      # the version of a file that does not exist


@dataclass
class Loaded:
    values: Dict[str, Any] = field(repr=False)     # may hold secrets: never printed
    version: str
    errors: List[Problem] = field(default_factory=list)
    exists: bool = False
    warnings: List[str] = field(default_factory=list)
    unreadable: bool = False      # the file exists but could not be read as an object: saving replaces it, so ask first


@dataclass
class SaveResult:
    ok: bool
    errors: List[Problem] = field(default_factory=list)
    new_version: Optional[str] = None
    conflict: bool = False


def config_path(name: str) -> str:
    """A file in the configuration directory, by plain name: no folder, no dots in front, no '..'."""
    if (not name or name != os.path.basename(name) or "\\" in name or "/" in name
            or name.startswith(".") or ".." in name):
        raise ValueError(f"{name!r} is not a plain file name")
    return os.path.join(localconfig.config_dir(), name)


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: str):
    """(bytes or None, error text or None)."""
    try:
        with open(path, "rb") as f:
            return f.read(), None
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"the file cannot be read ({e.strerror})"


class ConfigStore:
    def load(self, section: Section) -> Loaded:
        path = self._path(section)
        data, problem = _read(path)
        if problem:
            return Loaded({}, ABSENT, [Problem(None, problem)], unreadable=True)
        if data is None:
            return Loaded({}, ABSENT)
        loaded = Loaded({}, _version(data), exists=True)
        warning = filemode.open_access_warning(path)
        if warning:
            loaded.warnings.append(warning)
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (ValueError, RecursionError):
            loaded.errors.append(Problem(None, "the file is not valid JSON"))
            loaded.unreadable = True
            return loaded
        if not isinstance(parsed, dict):
            loaded.errors.append(Problem(None, "the file must contain a JSON object"))
            loaded.unreadable = True
            return loaded
        loaded.values = parsed
        loaded.errors.extend(section.validate(parsed))
        return loaded

    def save(self, section: Section, values: Dict[str, Any], expected_version: str) -> SaveResult:
        problems = section.validate(values)
        if problems:
            return SaveResult(False, list(problems))
        path = self._path(section)
        current, problem = _read(path)
        if problem:
            return SaveResult(False, [Problem(None, problem)])
        if (ABSENT if current is None else _version(current)) != expected_version:
            return SaveResult(False, [Problem(None, "the file changed on disk after it was loaded")], conflict=True)
        data = (json.dumps(values, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            _write_private(path, data)
        except OSError as e:
            return SaveResult(False, [Problem(None, f"the file could not be written ({e.strerror})")])
        return SaveResult(True, new_version=_version(data))

    @staticmethod
    def _path(section: Section) -> str:
        if not isinstance(section.storage, JsonFile):
            raise ValueError(f"section {section.id!r} is not stored in a JSON file")
        return section.storage.path()


def _write_private(path: str, data: bytes) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=folder)     # created 0600
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
        try:
            dir_fd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
