"""A small API assembled from the route lists, with a signed-in client, for tests of one route group."""
import os
import shutil

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient

import localconfig
from ui.api import security
from ui.api.routes_session import PUBLIC, session_routes
from ui.api.services import Settings, build_services
from ui.api.sessions import SessionStore

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "ui")
PASSWORD = "correct horse battery staple"
BASE = "http://127.0.0.1:8765"


def install_fixture(services, section_id, name):
    path = services.registry.path_of(section_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copy(os.path.join(FIXTURES, name), path)
    os.chmod(path, 0o600)
    return path


def install_password():
    os.makedirs(localconfig.config_dir(), exist_ok=True)
    target = os.path.join(localconfig.config_dir(), "ui_auth.json")
    shutil.copy(os.path.join(FIXTURES, "ui_auth.json"), target)
    os.chmod(target, 0o600)


class Rig:
    def __init__(self, extra_routes, startup_environ=None):
        install_password()
        self.settings = Settings(host="127.0.0.1", port=8765)
        self.services = build_services(startup_environ or {})
        self.sessions = SessionStore()
        routes = session_routes(self.services, self.sessions, self.settings) + extra_routes(self)
        self.app = Starlette(routes=routes, middleware=[Middleware(
            security.SecurityMiddleware, sessions=self.sessions, settings=self.settings, public=PUBLIC)])

    def anonymous(self):
        return TestClient(self.app, base_url=BASE)

    def signed_in(self):
        """(client, headers with the CSRF token)"""
        client = self.anonymous()
        token = client.get("/api/session").json()["csrf"]
        out = client.post("/api/session/login", json={"password": PASSWORD}, headers={security.CSRF_HEADER: token})
        assert out.json()["ok"], out.text
        return client, {security.CSRF_HEADER: out.json()["csrf"]}
