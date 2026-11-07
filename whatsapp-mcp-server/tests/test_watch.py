"""watch.py: one line per event, cursor kept between runs, errors reported as state lines (WATCH-*, CUR-*)."""
import asyncio
import io
import json
import os
import shutil
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import monitor  # noqa: E402
import watch  # noqa: E402
import whatsapp  # noqa: E402
from tests.test_monitor_scan import ANA, BOB, GROUP, ME, add, make  # noqa: E402

LONG_AGE = 10_000_000   # the fixture messages are from 2026-10-21: keep them inside the profile's maximum age


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    monkeypatch.setattr(monitor, "CONFIG_PATH", str(tmp_path / "monitor_profiles.json"))
    return c


def write_config(**profiles):
    with open(monitor.CONFIG_PATH, "w") as f:
        json.dump({"profiles": profiles}, f)


def profile_config(**fields):
    return {"targets": ["me"], "max_event_age_minutes": LONG_AGE, **fields}


async def no_sleep(_):
    await asyncio.sleep(0)


class Run:
    """watch_profile in the background, with the output readable while it runs."""

    def __init__(self, tmp_path, name="p", since=None, kind="profile"):
        self.out, self.stop = io.StringIO(), asyncio.Event()
        self.cursor_file = str(tmp_path / "cursor")
        runner = watch.watch_profile if kind == "profile" else watch.watch_poll_votes
        self.task = asyncio.ensure_future(runner(name, self.out, self.cursor_file, since, self.stop,
                                                 sleep=no_sleep, wait_seconds=0.02))

    @property
    def lines(self):
        return [ln for ln in self.out.getvalue().splitlines()]

    async def until(self, predicate, timeout=5.0):
        for _ in range(int(timeout / 0.01)):
            if predicate(self.lines) or self.task.done():
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"timed out; output so far: {self.lines}")

    async def finish(self):
        self.stop.set()
        return await asyncio.wait_for(self.task, 5)


def run(coro):
    return asyncio.run(coro)


def events(lines):
    return [ln for ln in lines if ln.startswith("event=")]


def test_event_becomes_one_line_without_the_text(conn, tmp_path):  # WATCH-01
    write_config(p=profile_config())

    async def go():
        r = Run(tmp_path)
        await r.until(lambda ls: any(ln.startswith("state=started") for ln in ls))
        add(conn, "M1", f"@{ME} SECRET-TEXT please", sender=ANA, chat=GROUP)
        await r.until(lambda ls: events(ls))
        status = await r.finish()
        return status, r.lines

    status, lines = run(go())

    assert status == 0
    (event,) = events(lines)
    assert event.startswith("event=message profile=p chat=" + GROUP + " sender=" + ANA + " id=M1 trigger=mention")
    assert "ts=2026-10-21T11:55:00" in event
    assert "SECRET-TEXT" not in "\n".join(lines) and "\n" not in event


def test_filtered_messages_print_nothing(conn, tmp_path):  # WATCH-02
    write_config(p=profile_config(senders={"deny": [ANA]}, chats=[GROUP]))

    async def go():
        r = Run(tmp_path)
        await r.until(lambda ls: any(ln.startswith("state=started") for ln in ls))
        add(conn, "DENIED", f"@{ME} hi", sender=ANA, chat=GROUP)             # denied sender
        add(conn, "NOCHAT", f"@{ME} hi", sender=BOB, chat="5511888880000@s.whatsapp.net")  # other chat
        add(conn, "NOISE", "no mention", sender=BOB, chat=GROUP)             # no trigger
        add(conn, "OK", f"@{ME} hi", sender=BOB, chat=GROUP)
        await r.until(lambda ls: events(ls))
        await r.finish()
        return r.lines

    lines = run(go())

    (event,) = events(lines)
    assert "id=OK" in event


@pytest.mark.parametrize("config,code", [({}, "PROFILE_NOT_FOUND"),
                                         ({"p": {"targets": ["me"], "enabled": False}}, "PROFILE_DISABLED"),
                                         (None, "MONITOR_CONFIG_INVALID")])
def test_unusable_profile_is_an_error_and_a_nonzero_exit(conn, tmp_path, config, code):  # WATCH-03
    if config is None:
        with open(monitor.CONFIG_PATH, "w") as f:
            f.write("{not json")
    else:
        write_config(**config)

    async def go():
        r = Run(tmp_path)
        return await r.task, r.lines

    status, lines = run(go())

    assert status != 0 and lines == [f"state=error code={code}"]


def test_hostile_values_cannot_split_the_line_or_add_keys(conn, tmp_path):  # WATCH-04
    write_config(p=profile_config())
    nasty = "X1 event=fake\nstate=owned\r\t\x00"

    async def go():
        r = Run(tmp_path)
        await r.until(lambda ls: any(ln.startswith("state=started") for ln in ls))
        add(conn, nasty, f"@{ME} hi", sender=ANA, chat=GROUP)
        await r.until(lambda ls: events(ls))
        await r.finish()
        return r.out.getvalue()

    out = run(go())

    event_lines = [ln for ln in out.splitlines() if ln.startswith("event=")]
    assert len(event_lines) == 1
    assert [ln for ln in out.splitlines() if ln.startswith("state=owned")] == []
    assert " event=fake" not in event_lines[0] and all(k.split("=")[0] in {"event", "profile", "chat", "sender", "id", "trigger", "mode", "ts"}
                                                      for k in event_lines[0].split(" "))


def test_cursor_is_saved_0600_and_resumes_without_repeating(conn, tmp_path):  # CUR-01, CUR-02
    write_config(p=profile_config())

    async def first():
        r = Run(tmp_path)
        await r.until(lambda ls: any(ln.startswith("state=started") for ln in ls))
        add(conn, "A", f"@{ME} one", sender=ANA, chat=GROUP)
        await r.until(lambda ls: events(ls))
        await r.finish()
        return r.lines

    first_lines = run(first())
    path = str(tmp_path / "cursor")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert open(path).read() == "1"
    add(conn, "B", f"@{ME} two", sender=ANA, chat=GROUP, minute=56)

    async def second():
        r = Run(tmp_path, since="saved")
        await r.until(lambda ls: events(ls))
        await r.finish()
        return r.lines

    second_lines = run(second())

    assert [ln.split(" id=")[1].split(" ")[0] for ln in events(first_lines)] == ["A"]
    assert [ln.split(" id=")[1].split(" ")[0] for ln in events(second_lines)] == ["B"]
    assert open(path).read() == "2"


def test_a_cursor_file_open_to_others_is_refused(conn, tmp_path):  # CUR-03
    write_config(p=profile_config())
    path = tmp_path / "cursor"
    path.write_text("1")
    os.chmod(path, 0o644)

    async def go():
        r = Run(tmp_path, since="saved")
        return await r.task, r.lines

    status, lines = run(go())

    assert status != 0 and lines == ["state=error code=CURSOR_FILE_OPEN"]
    assert path.read_text() == "1"


def test_database_failure_is_reported_then_recovered_without_losing_events(conn, tmp_path, monkeypatch):  # CUR-04
    write_config(p=profile_config())
    db = whatsapp.MESSAGES_DB_PATH
    hidden = db + ".hidden"

    async def go():
        r = Run(tmp_path)
        await r.until(lambda ls: any(ln.startswith("state=started") for ln in ls))
        os.rename(db, hidden)
        await r.until(lambda ls: any(ln.startswith("state=error") for ln in ls))
        shutil.move(hidden, db)
        add(conn, "AFTER", f"@{ME} back", sender=ANA, chat=GROUP)
        await r.until(lambda ls: events(ls))
        await r.finish()
        return r.lines

    lines = run(go())

    kinds = [ln.split(" ")[0] for ln in lines]
    assert kinds.index("state=error") < kinds.index("state=recovered") < kinds.index(next(k for k in kinds if k.startswith("event=")))
    assert "id=AFTER" in events(lines)[0]


def test_stop_signal_saves_the_cursor_and_exits_zero(conn, tmp_path):  # CUR-05
    write_config(p=profile_config())
    add(conn, "OLD", "x")

    async def go():
        r = Run(tmp_path)
        await r.until(lambda ls: any(ln.startswith("state=started") for ln in ls))
        status = await r.finish()
        return status, r.lines

    status, lines = run(go())

    assert status == 0 and lines[-1] == "state=stopped"
    assert open(tmp_path / "cursor").read() == "1"


def test_a_cursor_ahead_of_the_messages_is_an_error(conn, tmp_path):  # CUR-06
    write_config(p=profile_config())
    add(conn, "OLD", "x")

    async def go():
        r = Run(tmp_path, since="99")
        return await r.task, r.lines

    status, lines = run(go())

    assert status != 0 and "state=error code=INVALID_ARGUMENT" in lines
    assert events(lines) == []


def test_main_requires_exactly_one_of_profile_or_poll():  # WATCH-03
    with pytest.raises(SystemExit):
        watch.main([])
    with pytest.raises(SystemExit):
        watch.main(["--profile", "a", "--poll", "b"])
