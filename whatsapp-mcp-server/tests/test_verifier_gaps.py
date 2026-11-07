"""Tests added after the independent verification of the general improvements: export, drafts, metrics (EXPORT, DRAFT, METRIC)."""
import json
import os
import stat
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import drafts  # noqa: E402
import export  # noqa: E402
import metrics  # noqa: E402
import monitor  # noqa: E402
import whatsapp  # noqa: E402
from test_polls_read import A, POLL_TABLES, h, make  # noqa: E402
from tests.test_monitor_reply import NOW, TOKEN, config, reply, seed  # noqa: E402
from tests.test_monitor_reply import conn as reply_conn  # noqa: E402,F401
from tests.test_monitor_scan import ANA, GROUP  # noqa: E402

TABLES = POLL_TABLES.replace("hide_voter_names BOOLEAN,", "hide_voter_names BOOLEAN, ends_at_ms INTEGER, quiz BOOLEAN, "
                             "correct_answer TEXT, closed_notified BOOLEAN,") + """
    CREATE TABLE reactions (target_id TEXT, chat_jid TEXT, reactor TEXT, emoji TEXT, timestamp_ms INTEGER,
        PRIMARY KEY (target_id, chat_jid, reactor));"""
PHONE = "+5511987654321"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    return make(tmp_path, monkeypatch, TABLES)


def msg(conn, id, sender, content, minute, from_me=0):
    conn.execute("INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?,?,?,?,?,?)",
                 (id, A, sender, content, f"2026-11-06 10:{minute:02d}:00+00:00", from_me))
    conn.commit()


def poll(conn, id="P1", options=("Yes", "No"), hide=0, quiz=0, correct=None, from_me=1, author="me-id", minute=30):
    msg(conn, id, author, "[poll] q", minute, from_me)
    conn.execute("INSERT INTO polls (id, chat_jid, author, question, options_json, selectable_count, allow_add_option, "
                 "hide_voter_names, quiz, correct_answer) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (id, A, author, "Which?", json.dumps(list(options)), 0, 0, hide, quiz, correct))
    conn.commit()


def vote(conn, voter, options, at, poll_id="P1"):
    conn.execute("INSERT OR REPLACE INTO poll_votes VALUES (?,?,?,?,?)", (poll_id, A, voter, json.dumps([h(o) for o in options]), at))
    conn.commit()


# ---- export ------------------------------------------------------------------------------------------------
def test_anonymizing_masks_option_texts_and_the_right_answer(conn):  # EXPORT-04
    options = (f"Call {PHONE}", "Rio")
    poll(conn, options=options, quiz=1, correct=f"Call {PHONE}")
    vote(conn, "alice", [options[0]], 1000)

    out = export.export_conversation(A, anonymize=True)

    (p,) = out["polls"]
    assert [o["text"] for o in p["options"]] == ["Call [number]", "Rio"]
    assert p["correct_answer"] == "Call [number]" and p["votes"][0]["options"] == ["Call [number]"]
    assert "5511987654321" not in json.dumps(out)


def test_the_voters_of_an_anonymous_poll_are_not_exported(conn):  # EXPORT-04
    poll(conn, hide=1, from_me=0, author="alice")
    vote(conn, "bob-voter-id", ["Yes"], 1000)

    for anonymize in (False, True):
        out = export.export_conversation(A, anonymize=anonymize)
        (p,) = out["polls"]
        assert p["anonymous"] is True and p["votes"] == [] and all(o["voters"] == [] for o in p["options"])
        assert "bob-voter-id" not in json.dumps(out)
        assert p["total_voters"] == 1                 # the count stays: only the names are hidden


def test_has_more_is_true_only_when_there_is_one_more_message(conn):  # EXPORT-02
    for n in range(3):
        msg(conn, f"M{n}", "alice", "x", n)

    exact = export.export_conversation(A, limit=3)
    short = export.export_conversation(A, limit=2)

    assert exact["count"] == 3 and exact["has_more"] is False and exact["next_after"] is None
    assert short["count"] == 2 and short["has_more"] is True and short["next_after"] == short["messages"][-1]["timestamp"]


def test_removed_reactions_are_not_exported_and_retracted_votes_are_flagged(conn):  # EXPORT-01
    msg(conn, "M1", "alice", "x", 1)
    conn.execute("INSERT INTO reactions VALUES ('M1', ?, 'bob', '👍', 5000)", (A,))
    conn.execute("INSERT INTO reactions VALUES ('M1', ?, 'carol', '', 6000)", (A,))        # taken back
    poll(conn)
    vote(conn, "alice", ["Yes"], 1000)
    conn.execute("INSERT OR REPLACE INTO poll_votes VALUES ('P1', ?, 'bob', '[]', 2000)", (A,))   # retracted
    conn.commit()

    out = export.export_conversation(A)

    assert [(r["reactor"], r["emoji"]) for r in out["reactions"]] == [("bob", "👍")]
    votes = {v["voter"]: v for v in out["polls"][0]["votes"]}
    assert votes["bob"]["retracted"] is True and votes["bob"]["options"] == []
    assert votes["alice"]["retracted"] is False and votes["alice"]["options"] == ["Yes"]


def test_a_leftover_temporary_file_does_not_block_a_new_export(conn, tmp_path):  # EXPORT-06
    msg(conn, "M1", "alice", "x", 1)
    directory = tmp_path / "config" / "exports"
    directory.mkdir(parents=True)
    (directory / f"alpha.json.{os.getpid()}.tmp").write_text("left by a crash")

    assert export.save_conversation_export(A, "alpha")["success"] is True


# ---- drafts ------------------------------------------------------------------------------------------------
@pytest.fixture
def sends(monkeypatch):
    """send_message whose real send can be made to fail, and whose preview can be made to fail."""
    state = {"preview_ok": True, "send_ok": True, "real": 0}

    def fake(recipient, message, override_token=None, mentions=None, reply_to=None, dry_run=False):
        if dry_run:
            return whatsapp.SendResult(state["preview_ok"], "preview", preview={"text": message} if state["preview_ok"] else None)
        state["real"] += 1
        if not state["send_ok"]:
            return whatsapp.SendResult(False, "bridge down")
        return whatsapp.SendResult(True, "sent", message_id="S1", timestamp="2026-10-21T12:00:00-03:00", chat_jid=recipient)
    monkeypatch.setattr(whatsapp, "send_message", fake)
    return state


def test_a_failed_send_keeps_the_draft(reply_conn, sends):  # DRAFT-03
    seed(reply_conn)
    config()
    reply("HIT", "pending", approval_token=None)
    sends["send_ok"] = False

    out = reply("HIT", "pending")

    assert out["success"] is False and sends["real"] == 1
    assert [d["message_id"] for d in drafts.listing()["drafts"]] == ["HIT"]


def test_no_draft_is_kept_when_the_preview_failed(reply_conn, sends):  # DRAFT-01
    seed(reply_conn)
    config()
    sends["preview_ok"] = False

    out = reply("HIT", "text", approval_token=None)

    assert out["error_code"] == "APPROVAL_REQUIRED" and out["draft_id"] is None
    assert drafts.listing()["drafts"] == []


def test_two_profiles_answering_one_message_keep_two_drafts():  # DRAFT-02
    first = drafts.save("a", GROUP, "M1", ANA, "from a")
    second = drafts.save("b", GROUP, "M1", ANA, "from b")

    assert first != second and {d["text"] for d in drafts.listing()["drafts"]} == {"from a", "from b"}
    assert drafts.remove(first) and [d["text"] for d in drafts.listing()["drafts"]] == ["from b"]


def test_a_drafts_file_other_users_can_read_is_ignored(caplog):  # DRAFT-08
    drafts.save("a", GROUP, "M1", ANA, "x")
    os.chmod(drafts.path(), 0o644)

    assert drafts.listing()["drafts"] == []
    os.chmod(drafts.path(), 0o600)
    assert drafts.listing()["count"] == 1


def test_the_lock_file_is_private():  # DRAFT-08
    drafts.save("a", GROUP, "M1", ANA, "x")
    assert stat.S_IMODE(os.stat(drafts.path() + ".lock").st_mode) == 0o600


# ---- metrics -----------------------------------------------------------------------------------------------
def test_received_and_from_this_account_are_counted_apart(conn):  # METRIC-03
    ref = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    for n in range(3):
        msg(conn, f"IN{n}", "alice", "x", n)
    msg(conn, "OUT", "me-id", "x", 5, from_me=1)

    out = metrics.collect(24, now=ref)

    assert out["messages"] == {"received": 3, "from_this_account": 1}


def test_an_unreachable_bridge_reports_no_webhooks_even_if_the_status_has_stale_fields():  # METRIC-04
    stale = {"bridge_reachable": False, "webhooks": {"enabled": True, "endpoints": [{"delivered": 9}]}}
    assert metrics.collect(24, now=NOW, bridge_status=stale)["webhooks"] is None
