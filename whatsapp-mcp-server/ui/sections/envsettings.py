"""Settings that used to exist only as environment variables, kept in ui_settings.json by the interface.

Each section is one restart scope: what the bridge reads, and what the MCP server reads, are apart. Only
variables in uisettings.EDITABLE_ENV can be fields here; the environment still wins over the file.
"""
from typing import Any, Dict, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ui.config.fields import Field, Problem
from ui.config.registry import EnvKeys, Section

MEDIA_TYPES = ("image", "video", "audio", "document")

MEDIA = Section(
    id="media", title="Media downloads", order=70, icon="image", storage=EnvKeys(), restart="bridge",
    description="Which incoming media the bridge saves on arrival, and for how long. Read when the bridge starts.",
    fields=(
        Field("auto_download", "Save incoming media", "bool", default=True, env_var="WHATSAPP_AUTO_DOWNLOAD",
              help="Turn off to stop saving media when it arrives. download_media still works."),
        Field("auto_download_max_mb", "Largest file in MB", "float", default=25, env_var="WHATSAPP_AUTO_DOWNLOAD_MAX_MB",
              bounds=(0.01, 10240), help="Files declared larger than this are not saved on arrival."),
        Field("auto_download_types", "Types to save", "multichoice", default=["image", "document", "audio"],
              options=MEDIA_TYPES, env_var="WHATSAPP_AUTO_DOWNLOAD_TYPES"),
        Field("media_retention_days", "Delete saved files after (days)", "int", env_var="WHATSAPP_MEDIA_RETENTION_DAYS",
              bounds=(1, 36500), help="Leave empty to keep files. The messages stay; only the downloaded files are deleted."),
    ))

PRIVACY = Section(
    id="privacy", title="Privacy", order=71, icon="eye-off", storage=EnvKeys(), restart="bridge",
    description="What the bridge keeps and writes down. Read when the bridge starts.",
    fields=(
        Field("keep_deleted_content", "Keep deleted messages", "bool", default=False,
              env_var="WHATSAPP_KEEP_DELETED_CONTENT",
              help="When the author deletes a message for everyone, keep its text and media instead of erasing them.",
              risk="The sender asked for this content to disappear and it will stay.", risk_when=(True,)),
        Field("log_messages", "Write message text in the log", "bool", default=False, env_var="WHATSAPP_LOG_MESSAGES",
              help="Put the text of messages in the bridge log.",
              risk="The log is not encrypted and would hold the text of your conversations.", risk_when=(True,)),
    ))


def _timezone_problems(values: Dict[str, Any]) -> List[Problem]:
    name = values.get("timezone")
    if not name:
        return []
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return [Problem("timezone", "is not a known time zone (use an IANA name such as America/Recife)")]
    return []


TIMEZONE = Section(
    id="timezone", title="Time zone", order=72, icon="clock", storage=EnvKeys(), restart="server",
    validator=_timezone_problems,
    description="The zone for dates given without an offset. Read when the MCP server starts.",
    fields=(Field("timezone", "Time zone", "text", env_var="WHATSAPP_TIMEZONE", max_length=64,
                  help="An IANA name such as America/Recife. Leave empty to use the system zone."),))


def _interface_problems(values: Dict[str, Any]) -> List[Problem]:
    host = values.get("ui_host")
    if host is not None and (not host or any(ch.isspace() or ch in "/\\@" for ch in host)):
        return [Problem("ui_host", "must be an address or a host name")]
    return []


INTERFACE = Section(
    id="interface", title="Web interface", order=73, icon="monitor", storage=EnvKeys(), restart="bridge",
    validator=_interface_problems,
    description="Whether the bridge starts this interface and where it listens. Read when the bridge starts.",
    fields=(
        Field("ui", "Start the interface", "bool", default=True, env_var="WHATSAPP_UI",
              help="The bridge starts the interface with itself. Turn off to stop it."),
        Field("ui_host", "Address", "text", default="127.0.0.1", env_var="WHATSAPP_UI_HOST", max_length=253,
              help="Outside loopback it also needs WHATSAPP_UI_ALLOW_REMOTE=true in the bridge's environment, "
                   "which this interface cannot set.",
              risk="The interface would be reachable from other machines, and it has a password but no TLS.",
              risk_when=("0.0.0.0", "::")),
        Field("ui_port", "Port", "int", default=8765, env_var="WHATSAPP_UI_PORT", bounds=(1024, 65535)),
    ))

SECTIONS = (MEDIA, PRIVACY, TIMEZONE, INTERFACE)
