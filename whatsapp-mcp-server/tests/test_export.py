"""export_conversation: structured archive of a window, optional anonymization (EXPORT-*)."""
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import export  # noqa: E402
import main  # noqa: E402
import whatsapp  # noqa: E402
from test_polls_read import A, B, add_poll, h, make, vote  # noqa: E402,F401
from test_polls_read import POLL_TABLES  # noqa: E402

TABLES = POLL_TABLES + """
    CREATE TABLE reactions (target_id TEXT, chat_jid TEXT, reactor TEXT, emoji TEXT, timestamp_ms INTEGER,
        PRIMARY KEY (target_id, chat_jid, reactor));"""
PHONE = "5511987654321"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, TABLES)
    return c


def msg(conn, id, sender, content, minute, chat=A, from_me=0, **cols):
    cols = {"id": id, "chat_jid": chat, "sender": sender, "content": content, "is_from_me": from_me,
            "timestamp": f"2026-11-06 10:{minute:02d}:00+00:00", **cols}
    conn.execute(f"INSERT INTO messages ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    conn.commit()


def react(conn, target, reactor, emoji, at=5000, chat=A):
    conn.execute("INSERT INTO reactions VALUES (?,?,?,?,?)", (target, chat, reactor, emoji, at))
    conn.commit()


def seed(conn):
    msg(conn, "M1", "alice", f"call me on +{PHONE} please", 1)
    msg(conn, "M2", "bob", "ok", 2, quoted_id="M1", forwarded=1)
    msg(conn, "M3", "me-id", "done", 3, from_me=1, media_type="document", filename="contract-john.pdf", file_length=10)
    msg(conn, "OTHER", "carol", "other chat", 4, chat=B)
    add_poll(conn, "P1", A, "Lunch at +%s?" % PHONE, author="alice", minute=5)
    vote(conn, "alice", ["Yes"], at=1000)
    vote(conn, "bob", ["No"], at=2000)
    react(conn, "M1", "bob", "👍")
    react(conn, "OTHER", "carol", "🔥", chat=B)


def test_exports_the_window_in_order_with_polls_and_reactions(conn):  # EXPORT-01
    seed(conn)

    out = export.export_conversation(A)

    assert out["success"] and out["chat"] == {"jid": A, "name": "Project Alpha"} and out["anonymized"] is False
    assert [m["id"] for m in out["messages"]] == ["M1", "M2", "M3", "P1"] and out["count"] == 4 and not out["has_more"]
    m2 = out["messages"][1]
    assert (m2["quoted_message_id"], m2["forwarded"], m2["is_from_me"]) == ("M1", True, False)
    assert out["messages"][2]["media"] == {"type": "document", "mimetype": None, "size": 10, "filename": "contract-john.pdf"}
    (poll,) = out["polls"]
    assert poll["message_id"] == "P1" and [(v["voter"], v["options"]) for v in poll["votes"]] == [
        ("alice", ["Yes"]), ("bob", ["No"])]
    assert [(r["message_id"], r["reactor"], r["emoji"]) for r in out["reactions"]] == [("M1", "bob", "👍")]
    assert out["untrusted_content"] is True


def test_window_and_pagination(conn):  # EXPORT-02
    seed(conn)

    first = export.export_conversation(A, limit=2)
    rest = export.export_conversation(A, after=first["next_after"], limit=10)

    assert [m["id"] for m in first["messages"]] == ["M1", "M2"] and first["has_more"] is True
    assert [m["id"] for m in rest["messages"]] == ["M3", "P1"] and rest["has_more"] is False and rest["next_after"] is None
    cut = export.export_conversation(A, after="2026-11-06T10:01:00+00:00", before="2026-11-06T10:03:00+00:00")
    assert [m["id"] for m in cut["messages"]] == ["M2"]
    assert export.export_conversation(A, after="2027-01-01T00:00:00+00:00")["messages"] == []


def test_polls_and_reactions_can_be_left_out(conn):  # EXPORT-03
    seed(conn)
    out = export.export_conversation(A, include_polls=False, include_reactions=False)
    assert out["polls"] == [] and out["reactions"] == []


def test_anonymize_replaces_people_chat_numbers_and_file_names(conn):  # EXPORT-04
    seed(conn)

    out = export.export_conversation(A, anonymize=True)
    text = json.dumps(out)

    assert out["chat"] == {"jid": "[chat]", "name": "[chat]"} and out["anonymized"] is True
    for leaked in ("alice", "bob", PHONE, "Project Alpha", A, "contract-john.pdf", "me-id"):
        assert leaked not in text, leaked
    m1, m2, m3 = out["messages"][:3]
    assert m1["sender"] == "P1" and m2["sender"] == "P2" and m3["sender"] == "Me"
    assert m1["content"] == "call me on [number] please" and m2["content"] == "ok"
    assert m1["sender_name"] is None and m3["media"]["filename"] is None
    (poll,) = out["polls"]
    assert [(v["voter"], v["options"]) for v in poll["votes"]] == [("P1", ["Yes"]), ("P2", ["No"])]
    assert out["reactions"][0]["reactor"] == "P2"
    assert poll["question"] == "Lunch at [number]?"


def test_the_same_person_keeps_the_same_pseudonym_everywhere(conn):  # EXPORT-04
    seed(conn)
    out = export.export_conversation(A, anonymize=True)
    assert out["messages"][1]["sender"] == out["polls"][0]["votes"][1]["voter"] == out["reactions"][0]["reactor"] == "P2"


@pytest.mark.parametrize("kwargs", [{"chat_jid": ""}, {"chat_jid": A, "limit": 0}, {"chat_jid": A, "limit": 2001},
                                    {"chat_jid": A, "limit": True}, {"chat_jid": A, "after": "yesterday"},
                                    ])
def test_bad_arguments(conn, kwargs):  # EXPORT-05
    assert export.export_conversation(**kwargs)["error_code"] == "INVALID_ARGUMENT"


def test_unknown_chat_and_unreadable_database(conn, tmp_path, monkeypatch):  # EXPORT-05
    assert export.export_conversation("nobody@g.us")["error_code"] == "CHAT_NOT_FOUND"
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(tmp_path / "missing" / "m.db"))
    assert export.export_conversation(A)["error_code"] == "DIRECTORY_UNAVAILABLE"


def test_saving_writes_a_private_json_file_in_a_private_directory(conn, tmp_path):  # EXPORT-06
    seed(conn)

    out = export.save_conversation_export(A, "alpha")

    path = out["saved_to"]
    assert path == os.path.join(str(tmp_path / "config"), "exports", "alpha.json")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    saved = json.load(open(path))
    assert saved["count"] == 4 and "saved_to" not in saved


def test_an_existing_directory_with_wide_permissions_is_tightened(conn, tmp_path):  # EXPORT-06
    seed(conn)
    directory = tmp_path / "config" / "exports"
    directory.mkdir(parents=True)
    os.chmod(directory, 0o755)

    export.save_conversation_export(A, "alpha")

    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700


def test_an_existing_export_is_not_replaced_unless_asked(conn, tmp_path):  # EXPORT-06
    seed(conn)
    first = export.save_conversation_export(A, "alpha")
    with open(first["saved_to"], "w") as f:
        f.write("mine")

    again = export.save_conversation_export(A, "alpha")
    assert again["error_code"] == "EXPORT_EXISTS" and open(first["saved_to"]).read() == "mine"
    assert export.save_conversation_export(A, "alpha", overwrite=True)["success"] is True
    assert json.load(open(first["saved_to"]))["count"] == 4


def test_a_planted_link_is_never_followed(conn, tmp_path):  # EXPORT-06
    seed(conn)
    directory = tmp_path / "config" / "exports"
    directory.mkdir(parents=True)
    target = tmp_path / "victim.txt"
    target.write_text("keep")
    os.symlink(target, directory / "alpha.json")

    assert export.save_conversation_export(A, "alpha")["error_code"] == "EXPORT_EXISTS"
    assert export.save_conversation_export(A, "alpha", overwrite=True)["success"] is True
    assert target.read_text() == "keep"          # the link was replaced, not written through


@pytest.mark.parametrize("name", ["../x", "a/b", "", ".", "..", "a b", "x" * 81])
def test_unsafe_file_names_are_refused(conn, name):  # EXPORT-06
    seed(conn)
    assert export.save_conversation_export(A, name)["error_code"] == "INVALID_ARGUMENT"


def test_a_failed_export_writes_nothing(conn, tmp_path):  # EXPORT-06
    assert export.save_conversation_export("nobody@g.us", "alpha")["error_code"] == "CHAT_NOT_FOUND"
    assert not os.path.exists(tmp_path / "config" / "exports")


def test_the_read_tool_cannot_write_and_the_writing_one_is_not_read_only():  # EXPORT-07
    import asyncio
    tools = {t.name: t for t in asyncio.run(main.mcp.list_tools())}
    assert "save_as" not in tools["export_conversation"].input_schema["properties"]
    assert tools["export_conversation"].annotations.read_only_hint is True
    assert tools["save_conversation_export"].annotations.read_only_hint is False


def test_mentions_by_id_follow_the_pseudonym_of_that_person(conn):  # EXPORT-04
    msg(conn, "X1", "100000000000011", "hello", 1)
    msg(conn, "X2", "100000000000012", "thanks @100000000000011 and @100000000000099", 2)

    out = export.export_conversation(A, anonymize=True)

    assert [m["sender"] for m in out["messages"]] == ["P1", "P2"]
    assert out["messages"][1]["content"] == "thanks @P1 and @P3"
    assert "1000000000000" not in json.dumps(out)
