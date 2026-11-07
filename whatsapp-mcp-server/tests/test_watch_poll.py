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
