"""watch_poll: waits for votes newer than a cursor and reports who changed what."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import whatsapp  # noqa: E402
import pollwatch  # noqa: E402
from test_polls_read import A, B, add_poll, conn, vote  # noqa: E402,F401


def watch(*args, **kwargs):
    return asyncio.run(pollwatch.watch_poll(*args, **kwargs))


def test_first_call_returns_current_result_and_cursor_without_changes(conn):  # WATCH-01
    add_poll(conn)
    vote(conn, "alice", ["Yes"], at=1000)
    vote(conn, "bob", ["No"], at=2000)
    r = watch("P1", A)
    assert r["success"] and r["changes"] == [] and r["timed_out"] is False
    assert r["cursor"] == "2000"
    assert r["results"]["total_voters"] == 2
    assert {o["text"]: o["votes"] for o in r["results"]["options"]} == {"Yes": 1, "No": 1, "Maybe": 0}


def test_changes_are_the_votes_above_the_cursor_in_order(conn):  # WATCH-02
    add_poll(conn)
    vote(conn, "alice", ["Yes"], at=1000)
    vote(conn, "bob", ["No", "Maybe"], at=3000)
    vote(conn, "carol", ["Yes"], at=2000)
    r = watch("P1", A, cursor="1000")
    assert [(c["voter"], c["options"]) for c in r["changes"]] == [("carol", ["Yes"]), ("bob", ["No", "Maybe"])]
    assert [c["retracted"] for c in r["changes"]] == [False, False]
    assert r["cursor"] == "3000" and r["timed_out"] is False
    assert watch("P1", A, cursor=r["cursor"])["changes"] == []  # nothing is repeated


def test_a_vote_equal_to_the_cursor_is_not_repeated(conn):  # WATCH-02
    add_poll(conn)
    vote(conn, "alice", ["Yes"], at=1000)
    assert watch("P1", A, cursor=1000)["changes"] == []


def test_no_vote_waits_the_whole_time_then_times_out(conn):  # WATCH-03
    add_poll(conn)
    now = [0.0]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    r = watch("P1", A, cursor="0", wait_seconds=5, sleep=sleep, clock=lambda: now[0])
    assert r["changes"] == [] and r["timed_out"] is True and r["cursor"] == "0"
    assert sum(slept) == pytest.approx(5)


def test_a_vote_arriving_during_the_wait_ends_it_early(conn):  # WATCH-03
    add_poll(conn)
    now = [0.0]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds
        if len(slept) == 2:
            vote(conn, "alice", ["No"], at=500)

    r = watch("P1", A, cursor="0", wait_seconds=30, sleep=sleep, clock=lambda: now[0])
    assert [c["voter"] for c in r["changes"]] == ["alice"] and r["timed_out"] is False
    assert len(slept) == 2 and now[0] < 30


def test_no_wait_without_a_wait_time_is_not_a_timeout(conn):  # WATCH-03
    add_poll(conn)
    assert watch("P1", A, cursor="0")["timed_out"] is False


def test_a_retracted_vote_is_reported(conn):  # WATCH-04
    add_poll(conn)
    vote(conn, "alice", [], at=1500)
    c = watch("P1", A, cursor="1000")["changes"][0]
    assert c["retracted"] is True and c["options"] == [] and c["voter"] == "alice"


def test_anonymous_poll_hides_the_voter(conn):  # WATCH-05
    add_poll(conn, hide=1, author="zed")
    vote(conn, "alice", ["Yes"], at=1500)
    r = watch("P1", A, cursor="1000")
    assert r["changes"][0]["voter"] is None and r["changes"][0]["options"] == ["Yes"]
    assert all(o["voters"] == [] for o in r["results"]["options"])
    assert "alice" not in str(r)


def test_vote_for_an_unknown_option_is_counted_apart(conn):  # unknown options
    add_poll(conn)
    vote(conn, "alice", ["Yes"], raw=["f" * 64], at=1500)
    c = watch("P1", A, cursor="1000")["changes"][0]
    assert c["options"] == [] and c["unknown_options"] == 1 and c["retracted"] is False


def test_errors_for_unknown_and_ambiguous_polls(conn):  # WATCH-06
    add_poll(conn)
    add_poll(conn, id="P1", chat=B)
    assert watch("NOPE", A)["error_code"] == "POLL_NOT_FOUND"
    assert watch("P1")["error_code"] == "AMBIGUOUS_MESSAGE"


@pytest.mark.parametrize("kwargs", [{"message_id": ""}, {"cursor": "abc"}, {"cursor": "-1"}, {"cursor": True},
                                    {"wait_seconds": -1}, {"wait_seconds": 56}, {"wait_seconds": True},
                                    {"wait_seconds": "5"}])
def test_invalid_arguments(conn, kwargs):  # WATCH-07
    add_poll(conn)
    args = {"message_id": "P1", "chat_jid": A, **kwargs}
    assert watch(**args)["error_code"] == "INVALID_ARGUMENT"


def test_tool_is_registered_read_only_and_untrusted(conn):  # WATCH-08
    add_poll(conn)
    vote(conn, "alice", ["Yes"], at=1500)
    tool = {t.name: t for t in asyncio.run(main.mcp.list_tools())}["watch_poll"]
    assert tool.annotations.read_only_hint is True
    assert set(tool.input_schema["properties"]) == {"message_id", "chat_jid", "cursor", "wait_seconds"}
    assert tool.input_schema["required"] == ["message_id"]
    r = asyncio.run(main.watch_poll("P1", A, "1000"))
    assert r["untrusted_content"] is True and r["changes"][0]["voter"] == "alice"


def test_first_call_with_a_wait_returns_at_once(conn):  # WATCH-01, WATCH-03
    add_poll(conn)
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        raise AssertionError("the first call must not wait")

    r = watch("P1", A, wait_seconds=30, sleep=sleep, clock=lambda: 0.0)
    assert slept == [] and r["timed_out"] is False and r["changes"] == []


def test_a_wait_shorter_than_the_poll_interval_is_not_overshot(conn):  # WATCH-03
    add_poll(conn)
    now = [0.0]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds
        assert len(slept) < 10, "the wait never ends"

    r = watch("P1", A, cursor="0", wait_seconds=0.4, sleep=sleep, clock=lambda: now[0])
    assert r["timed_out"] is True and sum(slept) == pytest.approx(0.4)


def test_the_longest_wait_is_accepted_and_a_negative_integer_cursor_is_not(conn):  # WATCH-07
    add_poll(conn)
    assert watch("P1", A, cursor="0", wait_seconds=55, sleep=lambda s: asyncio.sleep(0),
                 clock=iter([0.0, 56.0]).__next__)["success"] is True
    assert watch("P1", A, cursor=-1)["error_code"] == "INVALID_ARGUMENT"


PHONE_CHAT, LID_CHAT = "5511999990000@s.whatsapp.net", "5511999990000@lid"


def test_direct_chat_poll_is_watched_and_listed_by_either_addressing(conn):  # DM-06
    conn.execute("INSERT INTO chats VALUES (?, 'Carol', NULL)", (PHONE_CHAT,))
    add_poll(conn, chat=PHONE_CHAT, author="carol")
    vote(conn, "carol", ["Yes"], chat=PHONE_CHAT, at=1500)
    for chat in (PHONE_CHAT, LID_CHAT):
        r = watch("P1", chat, cursor="1000")
        assert r["success"] is True
        assert [(c["voter"], c["options"]) for c in r["changes"]] == [("carol", ["Yes"])]
        assert r["results"]["chat_jid"] == PHONE_CHAT
        listed = whatsapp.list_polls(chat)
        assert listed["total"] == 1 and listed["polls"][0]["options"][0] == {"text": "Yes", "votes": 1}
