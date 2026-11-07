"""Sessions of the interface, kept in memory: a restart of the process ends all of them.

A session has a random id (the cookie), a CSRF token and an idle clock. Signing in changes both the id and the token,
so an id seen before signing in is worthless afterwards. At most MAX_SESSIONS exist; making room drops the one that has
been idle the longest (one that is not signed in goes before one that is), and a session that lapsed is gone as soon as it is looked up.
"""
import secrets
import time
from collections import OrderedDict
from typing import Callable, Dict, Optional

from ui.security.throttle import IDLE_LIMIT_SECONDS, IdleClock

MAX_SESSIONS = 20
_REMEMBERED_EXPIRED = 100


class Session:
    def __init__(self, store: "SessionStore", ip: str, idle: IdleClock):
        self._store = store
        self.ip = ip
        self.idle = idle
        self.id = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.authenticated = False

    def sign_in(self) -> None:
        self.authenticated = True
        self._store._rekey(self)

    def sign_out(self) -> None:
        self.authenticated = False
        self._store.drop(self.id)


class SessionStore:
    def __init__(self, idle_seconds: float = IDLE_LIMIT_SECONDS, clock: Callable[[], float] = time.monotonic,
                 limit: int = MAX_SESSIONS):
        self._idle_seconds = idle_seconds
        self._clock = clock
        self._limit = limit
        self._sessions: Dict[str, Session] = {}
        self._expired: "OrderedDict[str, None]" = OrderedDict()

    def create(self, ip: str) -> Session:
        while len(self._sessions) >= self._limit:
            # a session nobody signed in goes first, so a stream of anonymous page loads cannot push the operator out
            oldest = min(self._sessions.values(), key=lambda s: (s.authenticated, s.idle.remaining()))
            self.drop(oldest.id)
        session = Session(self, ip, IdleClock(self._idle_seconds, self._clock))
        self._sessions[session.id] = session
        return session

    def get(self, cookie: Optional[str]) -> Optional[Session]:
        session = self._sessions.get(cookie) if cookie else None
        if session is None:
            return None
        if session.idle.lapsed:
            self.drop(session.id)
            self._expired[cookie] = None
            while len(self._expired) > _REMEMBERED_EXPIRED:
                self._expired.popitem(last=False)
            return None
        return session

    def was_expired(self, cookie: Optional[str]) -> bool:
        return bool(cookie) and cookie in self._expired

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def count(self) -> int:
        return len(self._sessions)

    def _rekey(self, session: Session) -> None:
        self._sessions.pop(session.id, None)
        session.id = secrets.token_urlsafe(32)
        session.csrf = secrets.token_urlsafe(32)
        self._sessions[session.id] = session
