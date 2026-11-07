"""Where the web interface listens, and what it refuses.

It listens on 127.0.0.1 unless WHATSAPP_UI_HOST says otherwise, and a host outside loopback needs
WHATSAPP_UI_ALLOW_REMOTE=true in the environment of the process (the interface cannot set it for itself). A port
outside 1024-65535 is refused rather than replaced, and so is a port that is already taken. On SIGTERM uvicorn stops
accepting and finishes the requests in flight, then raises the signal again for whoever was handling it before; that
handler is ours and ends the process with code 0 instead of letting the signal kill it.
"""
import ipaddress
import os
import re
import signal
import socket
import sys
from dataclasses import replace
from typing import Callable, MutableMapping, Optional

import uisettings
from ui.api.services import Settings

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MIN_PORT, MAX_PORT = 1024, 65535


class LaunchError(Exception):
    """The interface cannot start; the message says why and what to change."""


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
        digits = raw_port.strip(" \t")
        # Plain ASCII digits only: int() would also take 9_000, +9000, fullwidth and Arabic-Indic digits.
        port = int(digits) if re.fullmatch(r"[0-9]+", digits) else 0
        if not MIN_PORT <= port <= MAX_PORT:
            raise LaunchError(f"WHATSAPP_UI_PORT must be a whole number from {MIN_PORT} to {MAX_PORT} (got {raw_port!r})")
    remote = not _is_loopback(host)
    if remote and (environ.get("WHATSAPP_UI_ALLOW_REMOTE") or "").strip().lower() != "true":
        raise LaunchError(
            f"WHATSAPP_UI_HOST={host} is not a loopback address, so the interface would be reachable from the network. "
            "Set WHATSAPP_UI_ALLOW_REMOTE=true in the environment of the bridge to allow it, or use 127.0.0.1.")
    return Settings(host=host, port=port, remote=remote)


def remote_warning(settings: Settings) -> str:
    text = (f"The interface listens on {settings.host}, which other machines can reach. It has a password but no TLS "
            "of its own: put it behind a reverse proxy that does HTTPS, or do not expose it.")
    wildcard = settings.host.strip().lower() in ("0.0.0.0", "::")
    if wildcard and not (settings.startup_environ.get("WHATSAPP_UI_ALLOWED_HOSTS") or "").strip():
        text += (" This address is a wildcard, so only localhost names are served until you list the host names people "
                 "reach it by in WHATSAPP_UI_ALLOWED_HOSTS (comma separated, `name` or `name:port`).")
    return text


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


def _end_cleanly(signum, frame) -> None:
    raise SystemExit(0)


def run_app(settings: Settings) -> None:
    """Build the app and serve it until the process is asked to stop."""
    import uvicorn                                                  # noqa: PLC0415
    from ui.api.app import create_app                                # noqa: PLC0415
    from ui.api.services import announce_setup_code, build_services  # noqa: PLC0415

    services = build_services(settings.startup_environ)
    announce_setup_code(services)
    previous = signal.signal(signal.SIGTERM, _end_cleanly)
    try:
        uvicorn.run(create_app(services, settings), host=settings.host, port=settings.port, log_level="warning",
                    server_header=False, proxy_headers=False, access_log=False)
    finally:
        signal.signal(signal.SIGTERM, previous)


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
    if settings.remote:
        print(f"ui: warning: {remote_warning(settings)}", file=sys.stderr)
    print(f"ui: WhatsApp MCP interface at {settings.url}", flush=True)
    runner(settings)
    return 0
