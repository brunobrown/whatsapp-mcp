"""Open the bridge's databases read-only, encrypted or not.

The bridge encrypts messages.db and whatsapp.db with SQLCipher. Its master key (WHATSAPP_DB_KEY, or the file in
WHATSAPP_DB_KEY_FILE, or db.key in the configuration directory) gives each database its own key through HKDF-SHA256;
this module derives the same bytes (see whatsapp-bridge/dbkey.go). The server never creates a key: only the bridge does.
"""
import hashlib
import hmac
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

import localconfig
import send_policy

SQLITE_MAGIC = b"SQLite format 3\x00"
KEY_UNAVAILABLE = "DATABASE_KEY_UNAVAILABLE"
UNREADABLE = "DATABASE_UNREADABLE"
_HKDF_INFO_PREFIX = "whatsapp-mcp/v1/"
_KEY_HEX = re.compile(r"[0-9a-fA-F]{64}")

try:  # the encrypted databases need it; plaintext ones do not
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:  # pragma: no cover - exercised by a test that hides the module
    sqlcipher = None

DB_ERRORS = (sqlite3.Error,) + ((sqlcipher.Error,) if sqlcipher else ())


class DatabaseKeyError(send_policy.DirectoryError):
    """The database is encrypted and cannot be opened: no usable key, or a key that does not open it.

    A DirectoryError, so that every place that already answers "the chat directory cannot be read" answers this too.
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.error_code = code
        self.detail = message


def _parse_key(text: str, source: str) -> bytes:
    text = text.strip()
    if not _KEY_HEX.fullmatch(text):
        raise DatabaseKeyError(KEY_UNAVAILABLE, f"{source} must hold 64 hexadecimal characters")
    return bytes.fromhex(text)


def master_key() -> bytes:
    """The master key from the first source that is set. A source that is set and unusable is an error, never a fallback."""
    value = os.environ.get("WHATSAPP_DB_KEY", "")
    if value:      # only blanks is a mistake to report, not "unset"
        return _parse_key(value, "WHATSAPP_DB_KEY")
    path = os.environ.get("WHATSAPP_DB_KEY_FILE", "")
    default = False
    if not path:
        path, default = os.path.join(localconfig.config_dir(), "db.key"), True
    try:
        with open(path, encoding="ascii") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        hint = "start the bridge once so that it creates it, or set WHATSAPP_DB_KEY / WHATSAPP_DB_KEY_FILE" if default else ""
        raise DatabaseKeyError(KEY_UNAVAILABLE, f"cannot read the database key file {path} ({type(e).__name__}); {hint}".rstrip("; "))
    return _parse_key(text, f"the key file {path}")


def hkdf_sha256(secret: bytes, info: bytes, length: int = 32, salt: Optional[bytes] = None) -> bytes:
    """HKDF (RFC 5869) with SHA-256."""
    prk = hmac.new(salt or b"\x00" * hashlib.sha256().digest_size, secret, hashlib.sha256).digest()
    output, block, counter = b"", b"", 1
    while len(output) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
        counter += 1
    return output[:length]


def derive_key(master: bytes, name: str) -> bytes:
    """The key of one database file (its name, e.g. messages.db): the same bytes the bridge derives."""
    return hkdf_sha256(master, (_HKDF_INFO_PREFIX + name).encode())


def is_plaintext(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def _is_empty_or_missing(path: str) -> bool:
    try:
        return os.path.getsize(path) == 0
    except OSError:
        return True


def connect_readonly(path: str):
    """A read-only connection to the database at path. A missing file is an error, never a new database.

    A plain SQLite file opens with the standard module; anything else is taken for an encrypted database and opened
    with the derived key.
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    if _is_empty_or_missing(path) or is_plaintext(path):
        return sqlite3.connect(uri, uri=True)
    if sqlcipher is None:
        raise DatabaseKeyError(UNREADABLE, f"{path} is encrypted and the sqlcipher3 package is not installed")
    key = derive_key(master_key(), os.path.basename(path))
    conn = sqlcipher.connect(uri, uri=True)
    try:
        conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher.Error:
        conn.close()
        raise DatabaseKeyError(UNREADABLE, f"{path} cannot be opened with the configured key (wrong key, or a damaged file)") from None
    return conn
