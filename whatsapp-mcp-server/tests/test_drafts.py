"""Pending drafts: a reply that needs the approval token is kept for review (DRAFT-*)."""
import json
import os
import stat
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import drafts  # noqa: E402
import main  # noqa: E402
import monitor  # noqa: E402
from tests.test_monitor_reply import NOW, TOKEN, conn, config, reply, seed, sent  # noqa: E402,F401
from tests.test_monitor_scan import ANA, GROUP  # noqa: E402


def stored():
    with open(drafts.path()) as f:
        return json.load(f)


def test_a_reply_without_the_token_is_kept_as_a_draft_0600_without_the_token(conn, sent):  # DRAFT-01
    seed(conn)
    config()

    out = reply("HIT", "Please check", approval_token=None)

    assert out["error_code"] == "APPROVAL_REQUIRED" and out["draft_id"].startswith("d_")
    (entry,) = stored()
    assert (entry["id"], entry["profile"], entry["chat_jid"], entry["message_id"], entry["sender"], entry["text"]) == \
           (out["draft_id"], "p", GROUP, "HIT", ANA, "Please check")
    assert TOKEN not in json.dumps(stored())
    assert stat.S_IMODE(os.stat(drafts.path()).st_mode) == 0o600
    assert [c for c in sent if not c["dry_run"]] == []


def test_a_wrong_token_also_keeps_the_draft_but_dry_run_does_not(conn, sent):  # DRAFT-01
    seed(conn)
    config()

    reply("HIT", "dry", approval_token=None, dry_run=True)
    assert not os.path.exists(drafts.path())
    out = reply("HIT", "wrong", approval_token="not-the-token")

    assert out["error_code"] == "APPROVAL_REQUIRED" and [e["text"] for e in stored()] == ["wrong"]


def test_a_new_draft_for_the_same_message_replaces_the_old(conn, sent):  # DRAFT-02
    seed(conn)
    config()

    reply("HIT", "first", approval_token=None)
    reply("HIT", "second", approval_token=None)

    assert [e["text"] for e in stored()] == ["second"]


def test_sending_the_reply_removes_its_draft_and_keeps_the_others(conn, sent):  # DRAFT-03
    seed(conn)
    config()
    reply("HIT", "pending", approval_token=None)
    drafts.save("p", GROUP, "OTHER", ANA, "other draft")

    out = reply("HIT", "pending")

    assert out["success"] is True
    assert [e["message_id"] for e in stored()] == ["OTHER"]


def test_list_shows_drafts_filters_by_profile_and_marks_them_untrusted(conn, sent):  # DRAFT-04
    drafts.save("a", GROUP, "M1", ANA, "for a")
    drafts.save("b", GROUP, "M2", ANA, "for b")

    out = main.list_pending_drafts()
    only_b = main.list_pending_drafts("b")

    assert out["success"] and out["count"] == 2 and out["untrusted_content"] is True and "review" in out["notice"]
    assert [d["text"] for d in only_b["drafts"]] == ["for b"] and only_b["count"] == 1
    assert main.list_pending_drafts("nobody")["drafts"] == []


def test_drafts_expire(conn):  # DRAFT-05
    drafts.save("p", GROUP, "M1", ANA, "old", now=NOW)

    assert drafts.listing(now=NOW + timedelta(hours=23, minutes=59))["count"] == 1
    assert drafts.listing(now=NOW + timedelta(hours=24, minutes=1))["count"] == 0


def test_ttl_comes_from_the_environment(monkeypatch):  # DRAFT-05
    monkeypatch.setenv("WHATSAPP_MONITOR_DRAFT_TTL_HOURS", "1")
    drafts.save("p", GROUP, "M1", ANA, "short", now=NOW)

    assert drafts.listing(now=NOW + timedelta(minutes=59))["count"] == 1
    assert drafts.listing(now=NOW + timedelta(minutes=61))["count"] == 0


def test_only_the_newest_hundred_are_kept(monkeypatch):  # DRAFT-06
    for n in range(drafts.MAX_DRAFTS + 5):
        drafts.save("p", GROUP, f"M{n}", ANA, f"t{n}")

    ids = [e["message_id"] for e in stored()]
    assert len(ids) == drafts.MAX_DRAFTS and ids[0] == "M5" and ids[-1] == f"M{drafts.MAX_DRAFTS + 4}"


def test_discard_removes_one_draft(conn):  # DRAFT-07
    ident = drafts.save("p", GROUP, "M1", ANA, "x")

    assert main.discard_pending_draft(ident)["success"] is True
    assert drafts.listing()["count"] == 0
    again = main.discard_pending_draft(ident)
    assert again["success"] is False and again["error_code"] == "DRAFT_NOT_FOUND"


def test_a_damaged_file_reads_as_empty_and_is_replaced(conn):  # DRAFT-08
    with open(drafts.path(), "w") as f:
        f.write("{not json")
    assert drafts.listing()["drafts"] == []

    drafts.save("p", GROUP, "M1", ANA, "x")
    assert [e["message_id"] for e in stored()] == ["M1"]


def test_ttl_is_capped_and_bad_values_fall_back(monkeypatch):  # DRAFT-05
    for value, hours in (("100000", drafts.MAX_TTL_HOURS), ("0", 24), ("-3", 24), ("abc", 24), ("nan", 24), ("48", 48)):
        monkeypatch.setenv("WHATSAPP_MONITOR_DRAFT_TTL_HOURS", value)
        assert drafts.ttl() == timedelta(hours=hours), value


def test_persistence_can_be_turned_off(monkeypatch, conn, sent):  # DRAFT-09
    seed(conn)
    config()
    monkeypatch.setenv("WHATSAPP_MONITOR_DRAFTS", "off")

    out = reply("HIT", "kept?", approval_token=None)

    assert out["error_code"] == "APPROVAL_REQUIRED" and out["draft_id"] is None
    assert drafts.listing()["drafts"] == []
