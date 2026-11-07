"""The one ASGI app: the API under /api and the SPA on the same port.

Every request first passes the security layer (Host, Origin, CSRF, session); every /api path except session state, the
setup code, the first password and sign-in answers 401 without a signed-in session. What no route claims goes to the
static handler, which serves the SPA and answers a JSON 404 under /api.
"""
from pathlib import Path
from typing import Any, Callable, Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware

from ui.api import security
from ui.api.forms import FormService
from ui.api.routes_ops import ops_routes
from ui.api.routes_sections import section_routes
from ui.api.routes_session import PUBLIC, session_routes
from ui.api.services import Services, Settings
from ui.api.sessions import SessionStore
from ui.api.static import static_handler

DEFAULT_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(services: Services, settings: Settings, web_dir: Optional[Path] = DEFAULT_WEB_DIR,
               sessions: Optional[SessionStore] = None, notice: Optional[Callable[[str], Any]] = None) -> Starlette:
    sessions = sessions or SessionStore(idle_seconds=services.idle_seconds)
    routes = [*session_routes(services, sessions, settings), *section_routes(FormService(services)), *ops_routes()]
    handler = static_handler(web_dir, notice) if notice else static_handler(web_dir)
    return Starlette(
        routes=routes,
        middleware=[Middleware(security.SecurityMiddleware, sessions=sessions, settings=settings, public=PUBLIC)],
        exception_handlers={404: handler})
