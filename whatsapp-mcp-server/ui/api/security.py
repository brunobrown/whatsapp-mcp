"""What every request passes through: Host, Origin, session, CSRF, JSON writes, the cookie and the response headers.

Refusals are short JSON bodies `{"code", "message"}` that never carry configuration, the Host that was refused or a
secret. A write needs, in this order: an Origin that matches the Host (when it sends one), a session, the session's
CSRF token in `X-CSRF-Token` and, when it has a body, `Content-Type: application/json`.
"""
import hmac
from typing import Collection, Optional, Tuple

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ui.api.sessions import Session, SessionStore
from ui.api.services import Settings

COOKIE_NAME = "wmcp_session"
CSRF_HEADER = "X-CSRF-Token"
BACKGROUND_HEADER = "X-Background"
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
_LOOPBACK_NAMES = ("localhost", "127.0.0.1", "::1")
_WILDCARDS = ("0.0.0.0", "::")


def allows_remote(settings: Settings) -> bool:
    return settings.remote or (settings.startup_environ.get("WHATSAPP_UI_ALLOW_REMOTE") or "").strip().lower() == "true"


def _split_host(value: str) -> Tuple[str, Optional[str]]:
    """(name, port) of a Host header; the name is lower case and an IPv6 address has no brackets."""
    value = value.strip().lower()
    if value.startswith("["):
        name, _, rest = value[1:].partition("]")
        return name, rest[1:] if rest.startswith(":") else None
    name, sep, port = value.rpartition(":")
    return (name, port) if sep else (value, None)


def _listed_hosts(settings: Settings) -> Tuple[Tuple[str, Optional[str]], ...]:
    """The names the operator allows besides the bind address: WHATSAPP_UI_ALLOWED_HOSTS, comma separated, each `name` or
    `name:port`. Read from the environment the process started with, never from what the interface saves."""
    raw = settings.startup_environ.get("WHATSAPP_UI_ALLOWED_HOSTS") or ""
    return tuple(_split_host(item) for item in raw.split(",") if item.strip())


def host_allowed(host_header: str, settings: Settings) -> bool:
    """Whether the Host header names this server. Loopback names are served on the bind port. Anything else needs the
    remote opt-in and must be the configured bind address on the bind port, or be listed in WHATSAPP_UI_ALLOWED_HOSTS
    (a wildcard bind is not a licence to serve every name that resolves here: that is DNS rebinding)."""
    name, port = _split_host(host_header)
    if not name:
        return False
    bind_port = str(settings.port)
    if name in _LOOPBACK_NAMES and port == bind_port:
        return True
    if not allows_remote(settings):
        return False
    configured = settings.host.strip().lower()
    if configured not in _WILDCARDS and name == configured and port == bind_port:
        return True
    for listed_name, listed_port in _listed_hosts(settings):
        if name == listed_name and (port == listed_port if listed_port is not None else port in (None, bind_port)):
            return True
    return False


def _is_secure(request: Request, settings: Settings) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return allows_remote(settings) and proto == "https"


def set_session_cookie(response: Response, request: Request, settings: Settings, session: Session) -> None:
    response.set_cookie(COOKIE_NAME, session.id, httponly=True, samesite="strict", path="/",
                        secure=_is_secure(request, settings))


def forget_session_cookie(response: Response, request: Request, settings: Settings) -> None:
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="strict", path="/", secure=_is_secure(request, settings))


def session_of(request: Request) -> Optional[Session]:
    return request.scope.get("state", {}).get("session")


def refusal(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status)


class SecurityMiddleware:
    """`public` lists the (method, path) pairs of /api that need no signed-in session; when it is None nothing is
    guarded and only the checks above run."""

    def __init__(self, app: ASGIApp, sessions: SessionStore, settings: Settings,
                 public: Optional[Collection[Tuple[str, str]]] = None):
        self.app = app
        self.sessions = sessions
        self.settings = settings
        self.public = None if public is None else frozenset(public)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        is_api = scope["path"] == "/api" or scope["path"].startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                extra = [(b"content-security-policy", CSP.encode()), (b"x-content-type-options", b"nosniff"),
                         (b"referrer-policy", b"no-referrer")]
                if is_api:
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers + [(k, v) for k, v in extra if k not in names or is_api]}
            await send(message)

        refused = self._check(request, is_api)
        if refused is not None:
            await refused(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)

    def _check(self, request: Request, is_api: bool) -> Optional[Response]:
        if not host_allowed(request.headers.get("host", ""), self.settings):
            return refusal(421, "bad_host", "This address is not served here.")
        cookie = request.cookies.get(COOKIE_NAME)
        session = self.sessions.get(cookie)
        request.scope.setdefault("state", {})["session"] = session
        if session is not None and request.headers.get(BACKGROUND_HEADER) != "1":
            session.idle.touch()
        write = request.method not in _SAFE_METHODS
        if write and not self._origin_matches(request):
            return refusal(403, "origin", "Request refused; reload the page.")
        key = (request.method, request.scope["path"])
        if is_api and self.public is not None and key not in self.public and not (session and session.authenticated):
            if self.sessions.was_expired(cookie):
                return refusal(401, "session_expired", "Your session ended because of inactivity.")
            return refusal(401, "unauthenticated", "Sign in to continue.")
        if write:
            return self._write_refusal(request, session)
        return None

    @staticmethod
    def _origin_matches(request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is None:
            return True
        host = request.headers.get("host", "").strip().lower()
        scheme, sep, netloc = origin.partition("://")
        return bool(sep) and netloc.lower() == host

    @staticmethod
    def _write_refusal(request: Request, session: Optional[Session]) -> Optional[Response]:
        token = request.headers.get(CSRF_HEADER, "")
        if session is None or not hmac.compare_digest(token.encode(), session.csrf.encode()):
            return refusal(403, "csrf", "Request refused; reload the page.")
        has_body = request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers
        kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if has_body and kind != "application/json":
            return refusal(403, "content_type", "Request refused; the body must be JSON.")
        return None
