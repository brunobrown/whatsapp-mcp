"""Routes of the session and the account: state, setup code, first password, sign-in, sign-out, password change.

A refused attempt is a 200 with `ok: false` and a plain message (the page shows it); an attempt made during a wait is a
429 with `wait` seconds and a `Retry-After` header. The setup code goes only to the log and never into a response.
"""
from typing import Any, Dict, List, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ui.api import security
from ui.api.services import Services, Settings
from ui.api.sessions import Session, SessionStore
from ui.security.signin import BROKEN, Result

PUBLIC = frozenset({("GET", "/api/session"), ("POST", "/api/session/code"), ("POST", "/api/session/setup"),
                    ("POST", "/api/session/login")})
IDLE_MESSAGE = "Your session ended because of inactivity."
BAD_REQUEST = "The request could not be understood."


async def read_object(request: Request) -> Optional[Dict[str, Any]]:
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def bad_request() -> JSONResponse:
    return security.refusal(400, "bad_request", BAD_REQUEST)


def _origin(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _answer(result: Result, **extra: Any) -> JSONResponse:
    body: Dict[str, Any] = {"ok": result.ok}
    if result.message:
        body["message"] = result.message
    if result.wait:
        body["wait"] = result.wait
        return JSONResponse(body, status_code=429, headers={"Retry-After": str(result.wait)})
    body.update(extra)
    return JSONResponse(body)


def session_routes(services: Services, sessions: SessionStore, settings: Settings) -> List[Route]:
    flow = services.signin

    def new_session(request: Request) -> Session:
        return sessions.create(_origin(request))

    async def state(request: Request) -> Response:
        session = security.session_of(request)
        cookie = request.cookies.get(security.COOKIE_NAME)
        message = IDLE_MESSAGE if session is None and sessions.was_expired(cookie) else None
        fresh = session is None
        if fresh:
            session = new_session(request)
        if session.authenticated:
            body: Dict[str, Any] = {"state": "authenticated", "csrf": session.csrf, "idle_remaining": session.idle.remaining()}
        else:
            current = flow.state()
            body = {"state": current, "csrf": session.csrf}
            if current == "broken":
                message = BROKEN
        if message:
            body["message"] = message
        response = JSONResponse(body)
        if fresh:
            security.set_session_cookie(response, request, settings, session)
        return response

    async def code(request: Request) -> Response:
        asked = flow.request_code(_origin(request))
        body: Dict[str, Any] = {"announced": asked.announced}
        if asked.message:
            body["message"] = asked.message
        return JSONResponse(body)

    def signed_in(request: Request, session: Session, result: Result) -> Response:
        response = _answer(result, csrf=session.csrf) if result.ok else _answer(result)
        if result.ok:
            security.set_session_cookie(response, request, settings, session)     # the id changed on sign-in
        return response

    async def setup(request: Request) -> Response:
        body = await read_object(request)
        if body is None:
            return bad_request()
        session = security.session_of(request)
        result = flow.setup(session, _origin(request), body.get("code"), body.get("password"), body.get("confirm"))
        return signed_in(request, session, result)

    async def login(request: Request) -> Response:
        body = await read_object(request)
        if body is None:
            return bad_request()
        session = security.session_of(request)
        result = flow.sign_in(session, _origin(request), body.get("password"))
        return signed_in(request, session, result)

    async def sign_out(request: Request) -> Response:
        flow.sign_out(security.session_of(request))
        response = JSONResponse({"ok": True})
        security.forget_session_cookie(response, request, settings)
        return response

    async def password(request: Request) -> Response:
        body = await read_object(request)
        if body is None:
            return bad_request()
        result = flow.change_password(_origin(request), body.get("current"), body.get("new"), body.get("confirm"))
        return _answer(result)

    return [Route("/api/session", state, methods=["GET"]),
            Route("/api/session", sign_out, methods=["DELETE"]),
            Route("/api/session/code", code, methods=["POST"]),
            Route("/api/session/setup", setup, methods=["POST"]),
            Route("/api/session/login", login, methods=["POST"]),
            Route("/api/account/password", password, methods=["POST"])]
