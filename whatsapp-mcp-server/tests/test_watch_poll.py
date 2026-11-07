"""watch.py --poll: one line per vote change, without voter names or option texts (WATCH-05)."""
import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import watch  # noqa: E402
from test_polls_read import A, add_poll, conn, vote  # noqa: E402,F401


def test_each_vote_change_is_a_line_without_voter_or_options(conn, tmp_path):  # WATCH-05
    add_poll(conn)
    vote(conn, "alice", ["Yes"], at=1000)
    out, stop = io.StringIO(), asyncio.Event()

    async def go():
        async def nap(_):
            await asyncio.sleep(0)
        task = asyncio.ensure_future(watch.watch_poll_votes("P1", out, str(tmp_path / "c"), "now", stop,
                                                           sleep=nap, wait_seconds=0.02))
        for _ in range(500):
            if "state=started" in out.getvalue():
                break
            await asyncio.sleep(0.01)
        vote(conn, "bob", ["No", "Maybe"], at=2000)
        vote(conn, "carol", [], at=3000)
        for _ in range(500):
            if out.getvalue().count("event=poll_vote") >= 2:
                break
            await asyncio.sleep(0.01)
        stop.set()
        return await task

    assert asyncio.run(go()) == 0
    votes = [ln for ln in out.getvalue().splitlines() if ln.startswith("event=poll_vote")]
    assert votes == [f"event=poll_vote poll=P1 chat={A} options=2 retracted=false",
                     f"event=poll_vote poll=P1 chat={A} options=0 retracted=true"]
    assert "bob" not in out.getvalue() and "Maybe" not in out.getvalue()
    assert open(tmp_path / "c").read() == "3000"


def test_a_poll_that_reaches_its_deadline_while_watched_prints_one_closing_line(conn, tmp_path):  # POLLX-11
    import time
    add_poll(conn)
    conn.execute("ALTER TABLE polls ADD COLUMN ends_at_ms INTEGER")
    conn.execute("UPDATE polls SET ends_at_ms = ?", (int((time.time() + 0.4) * 1000),))
    conn.commit()
    vote(conn, "alice", ["Yes"], at=1000)
    out, stop = io.StringIO(), asyncio.Event()

    async def go():
        async def nap(_):
            await asyncio.sleep(0)
        task = asyncio.ensure_future(watch.watch_poll_votes("P1", out, str(tmp_path / "c"), "now", stop,
                                                           sleep=nap, wait_seconds=0.05))
        for _ in range(500):
            if "event=poll_closed" in out.getvalue():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)              # a second line would show up here
        stop.set()
        return await task

    assert asyncio.run(go()) == 0
    closing = [ln for ln in out.getvalue().splitlines() if ln.startswith("event=poll_closed")]
    assert closing == [f"event=poll_closed poll=P1 chat={A} voters=1"]


def test_a_poll_already_closed_when_watching_starts_prints_no_closing_line(conn, tmp_path):  # POLLX-11
    add_poll(conn)
    conn.execute("ALTER TABLE polls ADD COLUMN ends_at_ms INTEGER")
    conn.execute("UPDATE polls SET ends_at_ms = 1000")
    conn.commit()
    out, stop = io.StringIO(), asyncio.Event()

    async def go():
        async def nap(_):
            await asyncio.sleep(0)
        task = asyncio.ensure_future(watch.watch_poll_votes("P1", out, str(tmp_path / "c"), "now", stop,
                                                           sleep=nap, wait_seconds=0.05))
        await asyncio.sleep(0.3)
        stop.set()
        return await task

    assert asyncio.run(go()) == 0 and "poll_closed" not in out.getvalue()
