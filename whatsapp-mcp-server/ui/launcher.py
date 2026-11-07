"""Where the web interface listens, and what it refuses.

It listens on 127.0.0.1 unless WHATSAPP_UI_HOST says otherwise, and a host outside loopback needs
WHATSAPP_UI_ALLOW_REMOTE=true in the environment of the process (the interface cannot set it for itself).
Flet itself would let FLET_SERVER_IP and FLET_SERVER_PORT override what is passed to it, so they are removed
before it starts. Flet is imported only when the interface actually runs.
"""
import ipaddress
import os
import socket
import sys
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, MutableMapping, Optional

import uisettings

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MIN_PORT, MAX_PORT = 1024, 65535
_FLET_SERVER_VARIABLES = ("FLET_SERVER_IP", "FLET_SERVER_PORT", "FLET_SERVER_UDS_PATH")


class LaunchError(Exception):
    """The interface cannot start; the message says why and what to change."""


ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    remote: bool = False
    # The environment as the process was started, before ui_settings.json filled in what it lacked: the interface needs
    # it to tell a variable of the environment from a value of the file.
    startup_environ: Dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolve(environ: MutableMapping[str, str]) -> Settings:
    host = (environ.get("WHATSAPP_UI_HOST") or "").strip() or DEFAULT_HOST
    raw_port = environ.get("WHATSAPP_UI_PORT")
    port = DEFAULT_PORT
    if raw_port is not None and raw_port.strip() != "":
        try:
            port = int(raw_port.strip())
        except ValueError:
            port = 0
        if not MIN_PORT <= port <= MAX_PORT:
            raise LaunchError(f"WHATSAPP_UI_PORT must be a whole number from {MIN_PORT} to {MAX_PORT} (got {raw_port!r})")
    remote = not _is_loopback(host)
    if remote and (environ.get("WHATSAPP_UI_ALLOW_REMOTE") or "").strip().lower() != "true":
        raise LaunchError(
            f"WHATSAPP_UI_HOST={host} is not a loopback address, so the interface would be reachable from the network. "
            "Set WHATSAPP_UI_ALLOW_REMOTE=true in the environment of the bridge to allow it, or use 127.0.0.1.")
    return Settings(host=host, port=port, remote=remote)


def remote_warning(settings: Settings) -> str:
    return (f"The interface listens on {settings.host}, which other machines can reach. It has a password but no TLS "
            "of its own: put it behind a reverse proxy that does HTTPS, or do not expose it.")


def prepare_environment(environ: MutableMapping[str, str]) -> None:
    for name in _FLET_SERVER_VARIABLES:
        environ.pop(name, None)
    environ["FLET_FORCE_WEB_SERVER"] = "true"      # serve the page; never open a browser on the machine


def check_port_free(host: str, port: int) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            if sys.platform != "win32":           # as the server will bind it; on Windows this flag would allow stealing
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except OSError as e:
        raise LaunchError(f"port {port} on {host} is already in use or cannot be opened ({e.strerror}); "
                          "change WHATSAPP_UI_PORT") from e


def run_app(settings: Settings) -> None:
    """Start the interface. Flet is imported here and nowhere earlier."""
    from ui.app.entry import serve      # noqa: PLC0415
    serve(settings)


def main(environ: Optional[MutableMapping[str, str]] = None, runner: Callable[[Settings], None] = run_app,
         probe: Callable[[str, int], None] = check_port_free) -> int:
    environ = os.environ if environ is None else environ
    startup = dict(environ)
    uisettings.apply(environ)            # the port and host saved by the interface; the environment still wins
    try:
        settings = replace(resolve(environ), startup_environ=startup)
        probe(settings.host, settings.port)
    except LaunchError as e:
        print(f"ui: {e}", file=sys.stderr)
        return 1
    prepare_environment(environ)
    if settings.remote:
        print(f"ui: warning: {remote_warning(settings)}", file=sys.stderr)
    print(f"ui: WhatsApp MCP interface at {settings.url}", flush=True)
    runner(settings)
    return 0
