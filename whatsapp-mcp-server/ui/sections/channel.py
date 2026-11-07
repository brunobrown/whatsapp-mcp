"""The Claude Code channel (channel.json, read by the MCP server at start).

The rules come from channel.py: the values are given to its parser and its reason is shown beside the field.
"""
import re
from typing import Any, Dict, List

import channel
from ui.config.fields import Field, Problem
from ui.config.registry import JsonFile, Section

FIELDS = (
    Field("enabled", "Enabled", "bool", default=False,
          help="The channel is off unless this is on. It lets a notice wake an open Claude Code session.",
          risk="A notice from WhatsApp can wake an agent session that nobody is watching.", risk_when=(True,)),
    Field("profiles", "Monitor profiles", "list", max_items=20,
          help="Names of the monitor profiles whose messages are announced, one per line."),
    Field("polls", "Polls", "list", max_items=20, help="Message ids of the polls whose votes are announced, one per line."),
    Field("include_excerpt", "Include an excerpt", "bool", default=False,
          help="Add a short piece of the message text to the notice. It is another person's words.",
          risk="A piece of the text of messages reaches the agent inside the notice.", risk_when=(True,)),
    Field("excerpt_chars", "Excerpt length", "int", default=120, bounds=(1, 280)),
    Field("batch_seconds", "Batch window in seconds", "float", default=3, bounds=(0, 60),
          help="Messages that arrive within this time are announced together."),
    Field("min_interval_seconds", "Minimum seconds between notices", "float", default=5, bounds=(0, 3600)),
    Field("max_batch", "Largest batch", "int", default=20, bounds=(1, 100)),
    Field("poll_interval_seconds", "Seconds between checks", "float", default=2, bounds=(0.05, 60)),
)
_KEYS = {f.key for f in FIELDS}


def _validate(values: Dict[str, Any]) -> List[Problem]:
    error = channel.parse_config(values).error
    if error is None:
        return []
    word = re.match(r"[a-z_]+", error)
    key = word.group(0) if word and word.group(0) in _KEYS else None
    if key is None and error.startswith("list at least one"):
        key = "profiles"
    return [Problem(key, error)]


SECTION = Section(
    id="channel", title="Claude Code channel", order=40, icon="radio", storage=JsonFile(channel.config_path),
    restart="server", fields=FIELDS, validator=_validate,
    description="Tell an open Claude Code session that something happened. Off by default; read by the MCP server at start.")
