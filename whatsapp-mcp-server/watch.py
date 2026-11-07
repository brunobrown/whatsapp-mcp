"""watch: print one short line per event of a monitor profile (or per vote change of a poll), for a tool that wakes
an agent on each line, such as Claude Code's Monitor.

    uv run watch.py --profile support            # new events only; the position is kept between runs
    uv run watch.py --profile support --since saved
    uv run watch.py --poll <message_id>

It reads with the same engine as monitor_messages / watch_poll, so triggers, chats, senders, topics and the maximum
age come from the profile. It never sends, votes or reacts, and it prints no message text, name, question or option:
the agent reads those with the read tools, which mark them untrusted. Diagnostics are lines that start with `state=`
and also go to stdout.
"""
import argparse
import asyncio
import os
import signal
import sys
from typing import Callable, Optional, TextIO

import localconfig
import monitor
import pollwatch
from eventline import line

WAIT_SECONDS = 20
RETRY_START = 2.0
RETRY_MAX = 30.0
FATAL = {"INVALID_ARGUMENT", monitor.CONFIG_INVALID, monitor.PROFILE_NOT_FOUND, monitor.PROFILE_DISABLED}


class CursorFileError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def default_cursor_file(kind: str, name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]
    return os.path.join(localconfig.config_dir(), f"watch_{kind}_{safe}.cursor")


def read_cursor(path: str) -> Optional[int]:
    """The saved cursor, None when there is no file. Raises CursorFileError for an open or broken file."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise CursorFileError("CURSOR_FILE_UNREADABLE", f"cannot read {path}: {e.strerror}")
    if sys.platform != "win32" and st.st_mode & 0o077:
        raise CursorFileError("CURSOR_FILE_OPEN", f"{path} can be read by other users; run chmod 600 on it")
    try:
        with open(path, encoding="ascii") as f:
            text = f.read().strip()
    except (OSError, UnicodeDecodeError) as e:
        raise CursorFileError("CURSOR_FILE_UNREADABLE", f"cannot read {path}: {e}")
    if not (text.isascii() and text.isdigit()):
        raise CursorFileError("CURSOR_FILE_INVALID", f"{path} does not hold a cursor")
    return int(text)


def write_cursor(path: str, value: int) -> None:
    """Replace the file atomically, with mode 0600 from the start."""
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(str(value))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def message_line(profile: str, event: dict) -> str:
    message = event["message"]
    triggers = sorted({h["trigger"] for h in event["matched"]})
    return line([("event", "message"), ("profile", profile), ("chat", message["chat_jid"]),
                 ("sender", message["sender"]), ("id", message["id"]), ("trigger", ",".join(triggers)),
                 ("mode", event.get("reply_mode")), ("ts", message["timestamp"])])


def vote_line(poll_id: str, chat_jid: str, change: dict) -> str:
    return line([("event", "poll_vote"), ("poll", poll_id), ("chat", chat_jid),
                 ("options", len(change["options"]) + change["unknown_options"]),
                 ("retracted", str(change["retracted"]).lower())])


class Watcher:
    def __init__(self, out: TextIO, cursor_file: str, stop: asyncio.Event, sleep: Callable = asyncio.sleep,
                 wait_seconds: float = WAIT_SECONDS):
        self.out, self.cursor_file, self.stop, self.sleep, self.wait_seconds = out, cursor_file, stop, sleep, wait_seconds
        self.failing = False

    def emit(self, text: str) -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def state(self, name: str, **extra) -> None:
        self.emit(line([("state", name)] + list(extra.items())))

    def start_position(self, since: Optional[str]) -> Optional[int]:
        """The cursor to start from: None means the end of the stored messages (only new events)."""
        if since is None or since == "saved":
            return read_cursor(self.cursor_file)
        if since == "now":
            return None
        if since.isascii() and since.isdigit():
            return int(since)
        raise CursorFileError("INVALID_ARGUMENT", "--since must be now, saved or a cursor number")

    async def _call(self, coro):
        """The call, abandoned if a stop signal arrives first. None when stopped."""
        task = asyncio.ensure_future(coro)
        stopper = asyncio.ensure_future(self.stop.wait())
        done, _ = await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        if task in done:
            return task.result()
        task.cancel()
        return None

    async def _retry_wait(self, delay: float) -> float:
        await self._call(self.sleep(delay))
        return min(delay * 2, RETRY_MAX)

    async def run(self, fetch: Callable, render: Callable, position: Optional[int]) -> int:
        """Loop: fetch(position) returns the tool answer; render(answer) gives the lines. Returns the exit status."""
        delay = RETRY_START
        while not self.stop.is_set():
            answer = await self._call(fetch(position))
            if answer is None:
                break
            if not answer.get("success"):
                code = answer.get("error_code", "UNKNOWN")
                self.state("error", code=code)
                if code in FATAL:
                    return 2
                self.failing = True
                delay = await self._retry_wait(delay)
                continue
            if self.failing:
                self.state("recovered")
                self.failing, delay = False, RETRY_START
            for text in render(answer):
                self.emit(text)
            position = int(answer["cursor"])
            write_cursor(self.cursor_file, position)
        if position is not None:
            write_cursor(self.cursor_file, position)
        self.state("stopped")
        return 0


async def watch_profile(name: str, out: TextIO, cursor_file: str, since: Optional[str], stop: asyncio.Event,
                        sleep: Callable = asyncio.sleep, wait_seconds: float = WAIT_SECONDS) -> int:
    w = Watcher(out, cursor_file, stop, sleep, wait_seconds)
    profile, problem = monitor.get_profile(name)
    if problem:
        w.state("error", code=problem["error_code"])
        return 2
    try:
        position = w.start_position(since)
    except CursorFileError as e:
        w.state("error", code=e.code)
        return 2

    async def fetch(pos):
        return await monitor.monitor_messages(profile=name, cursor=pos, wait_seconds=0 if pos is None else wait_seconds)

    def render(answer):
        return [message_line(name, e) for e in answer["events"]]

    w.state("started", profile=name)
    return await w.run(fetch, render, position)


async def watch_poll_votes(poll_id: str, out: TextIO, cursor_file: str, since: Optional[str], stop: asyncio.Event,
                           sleep: Callable = asyncio.sleep, wait_seconds: float = WAIT_SECONDS) -> int:
    w = Watcher(out, cursor_file, stop, sleep, wait_seconds)
    try:
        position = w.start_position(since)
    except CursorFileError as e:
        w.state("error", code=e.code)
        return 2

    async def fetch(pos):
        return await pollwatch.watch_poll(poll_id, cursor=pos, wait_seconds=0 if pos is None else wait_seconds)

    def render(answer):
        chat = answer["results"].get("chat_jid")
        return [vote_line(poll_id, chat, c) for c in answer["changes"]]

    w.state("started", poll=poll_id)
    return await w.run(fetch, render, position)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--profile", help="monitor profile to follow")
    target.add_argument("--poll", metavar="MESSAGE_ID", help="poll whose vote changes to follow")
    parser.add_argument("--since", help="now, saved or a cursor number (default: saved if there is a file, else now)")
    parser.add_argument("--cursor-file", help="where the position is kept (mode 0600)")
    parser.add_argument("--wait", type=float, default=WAIT_SECONDS, help="seconds each look waits for news (max 55)")
    args = parser.parse_args(argv)
    if not 0 <= args.wait <= 55:
        parser.error("--wait must be between 0 and 55")
    kind, name = ("profile", args.profile) if args.profile else ("poll", args.poll)
    cursor_file = args.cursor_file or default_cursor_file(kind, name)

    async def go() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        runner = watch_profile if args.profile else watch_poll_votes
        return await runner(name, sys.stdout, cursor_file, args.since, stop, wait_seconds=args.wait)

    return asyncio.run(go())


if __name__ == "__main__":
    sys.exit(main())
