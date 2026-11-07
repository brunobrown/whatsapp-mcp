"""The state of one browser session, as an observable object the screens subscribe to.

Nothing is loaded before the password is checked and everything is dropped when the session ends or lapses. What
is kept of a section is its masked form: a secret is only ever {"set": bool} here.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import flet as ft

from ui.core.registry import Section
from ui.core.secrets import mask
from ui.core.throttle import IdleClock


class NotAuthenticated(Exception):
    """The operation needs a signed-in session."""


@ft.observable
@dataclass
class AppState:
    authenticated: bool = False
    section_id: Optional[str] = None
    data: Dict[str, Dict[str, Any]] = field(default_factory=dict)      # masked values by section id
    restart: List[str] = field(default_factory=list)                    # "bridge" and/or "server": what must restart
    dirty: bool = False                                                 # the open form has changes that were not saved
    idle: IdleClock = field(default_factory=IdleClock, repr=False)

    def _require(self) -> None:
        if not self.authenticated:
            raise NotAuthenticated("sign in first")

    def sign_in(self) -> None:
        self.idle = IdleClock(self.idle.limit, clock=self.idle._clock)
        self.authenticated = True

    def sign_out(self) -> None:
        self.authenticated = False
        self.section_id = None
        self.data = {}
        self.restart = []
        self.dirty = False

    def set_dirty(self, dirty: bool) -> None:
        if self.authenticated:
            self.dirty = dirty
            self.idle.touch()

    def check_idle(self) -> bool:
        """End the session if it lapsed; True when it did."""
        if self.authenticated and self.idle.expired:
            self.sign_out()
            return True
        return False

    def touch(self) -> None:
        if self.authenticated:
            self.idle.touch()

    def open(self, section_id: str) -> None:
        self._require()
        self.section_id = section_id

    def put(self, section: Section, values: Dict[str, Any]) -> None:
        """Keep the masked form of a section's values; the raw ones go no further than this call."""
        self._require()
        self.data = {**self.data, section.id: mask(section, values)}

    def note_restart(self, scope: str) -> None:
        self._require()
        if scope not in self.restart:
            self.restart = [*self.restart, scope]
