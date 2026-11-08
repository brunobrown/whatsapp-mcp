"""The encrypted databases, as facts: whether a key can be read and where it comes from, and the state of each file.

Nothing here edits anything and nothing here ever holds the key itself: only whether a key was found, where, and
whether it was usable.
"""
import os
from typing import Any, Dict

import dbcrypto
import localconfig
from ui.config.fields import Field
from ui.config.registry import ReadOnly, Section

FIELDS = (
    Field("encryption", "Encryption setting", "text", readonly=True,
          help="WHATSAPP_DB_ENCRYPTION as the bridge sees it: on encrypts the databases, off keeps them in plain SQLite.", group="Encryption"),
    Field("key_present", "Key found", "bool", readonly=True, help="Whether a usable master key can be read.", group="Master key"),
    Field("key_source", "Key source", "text", readonly=True, group="Master key"),
    Field("key_location", "Key location", "text", readonly=True, help="The variable or file the key is read from.", group="Master key"),
    Field("key_problem", "Key problem", "text", readonly=True, help="Why the key cannot be used, when it cannot.", group="Master key"),
    Field("messages_db", "Messages database", "text", readonly=True, help="encrypted, plain or missing.", group="Databases"),
    Field("whatsapp_db", "Session database", "text", readonly=True, help="encrypted, plain or missing.", group="Databases"),
)


def _state_of(path: str) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(len(dbcrypto.SQLITE_MAGIC))
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    if len(head) < len(dbcrypto.SQLITE_MAGIC):
        return "plain" if not head else "encrypted"
    return "plain" if head == dbcrypto.SQLITE_MAGIC else "encrypted"


def _key_origin() -> Dict[str, str]:
    if os.environ.get("WHATSAPP_DB_KEY", ""):
        return {"key_source": "environment variable", "key_location": "WHATSAPP_DB_KEY"}
    configured = os.environ.get("WHATSAPP_DB_KEY_FILE", "")
    if configured:
        return {"key_source": "key file named by WHATSAPP_DB_KEY_FILE", "key_location": configured}
    return {"key_source": "default key file", "key_location": os.path.join(localconfig.config_dir(), "db.key")}


def facts() -> Dict[str, Any]:
    import whatsapp           # the path of the messages database lives there; imported late to keep this module light
    origin = _key_origin()
    try:
        dbcrypto.master_key()
        present, problem = True, ""
    except dbcrypto.DatabaseKeyError as e:
        present, problem = False, e.detail
    setting = os.environ.get("WHATSAPP_DB_ENCRYPTION", "").strip().lower()
    messages = whatsapp.MESSAGES_DB_PATH
    return {
        "encryption": "off" if setting == "off" else "on",
        "key_present": present, **origin, "key_problem": problem,
        "messages_db": _state_of(messages),
        "whatsapp_db": _state_of(os.path.join(os.path.dirname(messages), "whatsapp.db")),
    }


SECTION = Section(
    id="database", title="Database", order=60, icon="database", storage=ReadOnly(), fields=FIELDS, facts=facts,
    description="Whether the databases are encrypted and where the key is read from. The key itself is never shown.")
