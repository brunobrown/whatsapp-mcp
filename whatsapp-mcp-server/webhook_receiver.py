"""Reference receiver for the bridge's webhooks (standard library only).

    WEBHOOK_SECRET=<the endpoint's secret> python3 webhook_receiver.py --port 9000
    python3 webhook_receiver.py --secret-file ~/.config/whatsapp-mcp/hook.secret

It checks the HMAC-SHA256 signature in constant time, refuses a timestamp more than 5 minutes off, deduplicates by
`event_id` and prints one line per event, in the same form as watch.py and without any text: not the message, the
question, the options or the secret. It listens on loopback only.
"""
import argparse
import hashlib
import hmac
import ipaddress
import socket
import json
import os
import stat
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, TextIO

from eventline import line

MAX_BODY = 64 * 1024 + 1
TOLERANCE_SECONDS = 300
MIN_SECRET_BYTES = 32
SEEN_LIMIT = 10000
READ_TIMEOUT_SECONDS = 10
PLACEHOLDER_SECRETS = {"replace-with-your-own-value", "<at least 32 random bytes, e.g. openssl rand -hex 32>"}


class ConfigError(Exception):
    pass


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_secret(secret: Optional[bytes]) -> bytes:
    if not secret or len(secret) < MIN_SECRET_BYTES:
        raise ConfigError(f"the secret needs at least {MIN_SECRET_BYTES} bytes")
    if secret.decode("utf-8", "replace").strip() in PLACEHOLDER_SECRETS or b"replace-with" in secret:
        raise ConfigError("the secret is the example value; use your own (openssl rand -hex 32)")
    return secret


def load_secret(secret_file: Optional[str], env=os.environ) -> bytes:
    if secret_file:
        mode = stat.S_IMODE(os.stat(secret_file).st_mode)
        if sys.platform != "win32" and mode & 0o077:
            raise ConfigError(f"{secret_file} can be read by other users; run chmod 600 on it")
        with open(secret_file, "rb") as f:
            return check_secret(f.read().strip())
    value = env.get("WEBHOOK_SECRET")
    return check_secret(value.encode() if value else None)


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class Receiver:
    """The decision for one request, apart from the HTTP plumbing."""

    def __init__(self, secret: bytes, out: TextIO, clock: Callable[[], float] = time.time):
        self.secret, self.out, self.clock = check_secret(secret), out, clock
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self._lock = threading.Lock()

    def handle(self, headers, body: bytes) -> int:
        if len(body) >= MAX_BODY:
            return 413
        stamp = headers.get("X-Webhook-Timestamp", "")
        if not (stamp.isascii() and stamp.isdigit()) or abs(self.clock() - int(stamp)) > TOLERANCE_SECONDS:
            return 401
        if not hmac.compare_digest(sign(self.secret, stamp, body), headers.get("X-Webhook-Signature", "")):
            return 401
        try:
            event = json.loads(body)
        except (ValueError, RecursionError):     # deeply nested JSON raises RecursionError, not ValueError
            return 400
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str) or not event["event_id"] \
                or not isinstance(event.get("type"), str) or not event["type"]:
            return 400
        with self._lock:
            if event["event_id"] in self._seen:
                return 204
            self._seen[event["event_id"]] = None
            if len(self._seen) > SEEN_LIMIT:
                self._seen.popitem(last=False)
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            self.out.write(event_line(event, data) + "\n")
            self.out.flush()
        return 204


def event_line(event: dict, data: dict) -> str:
    """Ids, type and times only. Which data keys are copied is fixed here, so no text can slip in."""
    return line([("event", event["type"]), ("id", event["event_id"]), ("chat", data.get("chat_jid")),
                 ("sender", data.get("sender") or data.get("voter") or data.get("reactor") or data.get("author")),
                 ("message", data.get("message_id") or data.get("poll_id") or data.get("target_id")),
                 ("state", data.get("state")), ("ts", event.get("occurred_at"))])


def make_server(host: str, port: int, receiver: Receiver) -> ThreadingHTTPServer:
    if not is_loopback(host):
        raise ConfigError("the receiver listens on loopback only (127.0.0.1, ::1 or localhost)")

    class Hook(BaseHTTPRequestHandler):
        timeout = READ_TIMEOUT_SECONDS      # a client that stops sending cannot hold a thread for ever

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0:
                status = 400
            elif length >= MAX_BODY:
                status = 413
            else:
                try:
                    status = receiver.handle(self.headers, self.rfile.read(length))
                except OSError:             # timeout or reset while reading the body
                    self.close_connection = True
                    return
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):   # the default log line would print the client address for each request
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

    return Server((host, port), Hook)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--secret-file", help="file with the secret (mode 0600); else WEBHOOK_SECRET")
    args = parser.parse_args(argv)
    try:
        server = make_server(args.host, args.port, Receiver(load_secret(args.secret_file), sys.stdout))
    except (ConfigError, OSError) as e:
        print(line([("state", "error"), ("code", "CONFIG")]) + f" # {e}", flush=True)
        return 2
    print(line([("state", "listening"), ("host", args.host), ("port", args.port)]), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
