"""Warn when a file that holds a token can be read by other users of the machine."""
import logging
import os
import stat
import sys
from typing import Dict, Optional

logger = logging.getLogger(__name__)
_reported: Dict[str, bool] = {}   # path -> whether the last look found it open (so a warning repeats only after a change)


def open_access_warning(path: str) -> Optional[str]:
    """A warning when path exists and group or others have any access to it (POSIX only), else None."""
    if sys.platform == "win32":
        return None
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None
    if not mode & 0o077:
        return None
    return (f"{path} holds a token and can be read by other users (mode {mode:04o}). "
            f"Restrict it with: chmod 600 {path}")


def warn_if_open(path: str) -> Optional[str]:
    """The warning for path, also logged the first time it is seen open (again only if it closes and opens again)."""
    warning = open_access_warning(path)
    if warning and not _reported.get(path):
        logger.warning(warning)
    _reported[path] = bool(warning)
    return warning
