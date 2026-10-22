"""Testes do get_message_thread: cadeia de respostas, janela do chat e mídia citada."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import whatsapp  # noqa: E402

A, B = "a@g.us", "b@g.us"
SCHEMA = """
    CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
    CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
        is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB,
        file_enc_sha256 BLOB, file_length INTEGER, local_path TEXT{extra}, PRIMARY KEY (id, chat_jid));
    INSERT INTO chats VALUES ('a@g.us', 'Projeto Alfa', NULL), ('b@g.us', 'Família', NULL);"""
EXTRA = ", mimetype TEXT, forwarded BOOLEAN, quoted_id TEXT"


def make(tmp_path, monkeypatch, extra=EXTRA):
    path = str(tmp_path / "messages.db")
    c = sqlite3.connect(path)
    c.executescript(SCHEMA.format(extra=extra))
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    monkeypatch.delenv("WHATSAPP_TIMEZONE", raising=False)
    return c


@pytest.fixture
def conn(tmp_path, monkeypatch):
    return make(tmp_path, monkeypatch)


def add(conn, id, minute, chat=A, quoted=None, content="x", sender="alice", hour=10, **cols):
    cols = {"is_from_me": 0, **cols, "id": id, "chat_jid": chat, "sender": sender, "content": content,
            "timestamp": f"2026-10-21 {hour:02d}:{minute:02d}:00+00:00", "quoted_id": quoted}
    conn.execute(f"INSERT INTO messages ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    conn.commit()


def thread(**kwargs):
    return main.get_message_thread(**kwargs)


def ids(out):
    return [m["id"] for m in out["messages"]]


def by_id(out):
    return {m["id"]: m for m in out["messages"]}


def chain(conn):
    add(conn, "R", 0, content="raiz")
    add(conn, "A1", 1, quoted="R", content="resposta 1")
    add(conn, "A2", 2, quoted="R", content="resposta 2")
    add(conn, "B1", 3, quoted="A1", content="resposta da resposta")
    add(conn, "OTHER", 4, content="fora da cadeia")


def test_thread_from_any_message_returns_the_whole_chain_in_order(conn):  # THREAD-01
    chain(conn)

    for start in ("R", "A1", "B1", "A2"):
        out = thread(message_id=start, chat_jid=A)
        assert out["success"] is True and out["root_id"] == "R" and out["target_id"] == start
        assert ids(out) == ["R", "A1", "A2", "B1"] and out["total"] == 4 and out["truncated"] is False


def test_thread_message_has_every_field(conn):  # THREAD-02, REPL-01, REPL-06
    chain(conn)

    out = thread(message_id="B1", chat_jid=A)
    msgs = by_id(out)

    b1 = msgs["B1"]
    assert set(b1) == {"id", "timestamp", "chat_jid", "chat_name", "sender", "sender_name", "content", "is_from_me",
                       "forwarded", "media", "reply_to", "replies", "thread_root_id", "is_target"}
    assert b1["timestamp"] == "2026-10-21T10:03:00+00:00" and b1["chat_name"] == "Projeto Alfa"
    assert b1["is_target"] is True and msgs["R"]["is_target"] is False
    assert b1["thread_root_id"] == "R" and msgs["A1"]["thread_root_id"] == "R" and msgs["R"]["thread_root_id"] == "R"
    assert b1["reply_to"]["id"] == "A1" and b1["reply_to"]["found"] is True
    assert b1["reply_to"]["content"] == "resposta 1" and b1["reply_to"]["sender"] == "alice"
    assert b1["reply_to"]["timestamp"] == "2026-10-21T10:01:00+00:00" and b1["reply_to"]["is_from_me"] is False
    assert "sender_name" in b1["reply_to"]
    assert msgs["R"]["reply_to"] is None
    assert msgs["R"]["replies"] == ["A1", "A2"] and msgs["A1"]["replies"] == ["B1"] and b1["replies"] == []


def test_ambiguous_or_missing_message(conn):  # THREAD-03
    add(conn, "SAME", 1, chat=A)
    add(conn, "SAME", 2, chat=B)

    assert thread(message_id="SAME")["error_code"] == "AMBIGUOUS_MESSAGE"
    assert thread(message_id="SAME")["success"] is False
    assert ids(thread(message_id="SAME", chat_jid=B)) == ["SAME"]
    assert thread(message_id="SAME", chat_jid=B)["messages"][0]["chat_jid"] == B
    assert thread(message_id="NOPE")["error_code"] == "MESSAGE_NOT_FOUND"
    assert thread(message_id="SAME", chat_jid="c@g.us")["error_code"] == "MESSAGE_NOT_FOUND"


def test_the_chain_never_crosses_chats(conn):  # THREAD-01
    add(conn, "R", 0, chat=A)
    add(conn, "X", 1, chat=B, quoted="R")  # mesmo ID citado em outro chat: não é resposta
    add(conn, "A1", 2, chat=A, quoted="R")

    out = thread(message_id="R", chat_jid=A)

    assert ids(out) == ["R", "A1"] and by_id(out)["R"]["replies"] == ["A1"]


def test_long_chain_is_truncated_to_500_oldest(conn):  # THREAD-04
    add(conn, "M0", 0, hour=0)
    for n in range(1, 520):
        add(conn, f"M{n}", n % 60, hour=n // 60, quoted=f"M{n - 1}")

    out = thread(message_id="M0", chat_jid=A)

    assert out["truncated"] is True and out["total"] == 500 and len(out["messages"]) == 500
    assert ids(out)[0] == "M0" and ids(out)[-1] == "M499"


def test_chain_of_exactly_500_is_not_truncated(conn):  # THREAD-04
    add(conn, "M0", 0, hour=0)
    for n in range(1, 500):
        add(conn, f"M{n}", n % 60, hour=n // 60, quoted=f"M{n - 1}")

    out = thread(message_id="M0", chat_jid=A)

    assert out["truncated"] is False and out["total"] == 500


def test_citation_loop_terminates_without_repeats(conn):  # THREAD-05
    add(conn, "X", 1, quoted="Y")
    add(conn, "Y", 2, quoted="X")
    add(conn, "SELF", 3, quoted="SELF")

    loop = thread(message_id="X", chat_jid=A)
    selfref = thread(message_id="SELF", chat_jid=A)

    assert sorted(ids(loop)) == ["X", "Y"] and len(ids(loop)) == 2
    assert ids(selfref) == ["SELF"]


def test_window_returns_the_chat_with_pages(conn):  # WIN-01, WIN-02
    for n in range(5):
        add(conn, f"M{n}", n)
    add(conn, "ELSEWHERE", 2, chat=B)

    first = thread(chat_jid=A, limit=2)
    last = thread(chat_jid=A, limit=2, page=2)
    desc = thread(chat_jid=A, limit=2, order="desc")

    assert first["success"] is True and ids(first) == ["M0", "M1"]
    assert (first["total"], first["page"], first["limit"], first["has_more"], first["next_page"]) == (5, 0, 2, True, 1)
    assert ids(last) == ["M4"] and last["has_more"] is False and last["next_page"] is None
    assert ids(desc) == ["M4", "M3"]


def test_window_limits_are_exclusive_instants(conn):  # WIN-01
    add(conn, "EDGE", 0)
    add(conn, "IN", 1)
    add(conn, "OUT", 2)

    out = thread(chat_jid=A, after="2026-10-21T10:00:00+00:00", before="2026-10-21T10:02:00+00:00")
    zone = thread(chat_jid=A, after="2026-10-21T07:00:30-03:00", before="2026-10-21T07:02:00-03:00")

    assert ids(out) == ["IN"] and ids(zone) == ["IN"]


def test_window_links_replies_outside_the_window(conn):  # WIN-03
    add(conn, "OLD", 0, content="antes da janela")
    add(conn, "MID", 10, quoted="OLD", content="dentro")
    add(conn, "LATE", 50, quoted="MID", content="depois da janela")

    out = thread(chat_jid=A, after="2026-10-21T10:05:00+00:00", before="2026-10-21T10:20:00+00:00")

    assert ids(out) == ["MID"]
    mid = out["messages"][0]
    assert mid["reply_to"]["id"] == "OLD" and mid["reply_to"]["content"] == "antes da janela"
    assert mid["replies"] == ["LATE"] and mid["thread_root_id"] == "OLD"


def test_window_has_no_target(conn):  # WIN-01
    add(conn, "M", 1)

    assert thread(chat_jid=A)["messages"][0]["is_target"] is False


@pytest.mark.parametrize("kwargs", [
    {}, {"message_id": "M", "after": "2026-10-21T00:00:00+00:00"}, {"message_id": "M", "before": "2026-10-21T00:00:00+00:00"},
    {"message_id": "M", "limit": 5}, {"message_id": "M", "page": 1}, {"message_id": "M", "order": "desc"},
    {"chat_jid": A, "limit": 0}, {"chat_jid": A, "limit": -1}, {"chat_jid": A, "page": -1}, {"chat_jid": A, "order": "up"},
    {"chat_jid": A, "after": "ontem"}, {"chat_jid": A, "before": "amanhã"},
])
def test_invalid_arguments(conn, kwargs):  # WIN-04
    add(conn, "M", 1)

    out = thread(**kwargs)

    assert out["success"] is False and out["error_code"] == "INVALID_ARGUMENT" and out["message"]


def test_missing_parent_is_not_found_and_becomes_its_own_root(conn):  # REPL-02
    add(conn, "ORPHAN", 1, quoted="GONE", content="resposta")

    out = thread(message_id="ORPHAN", chat_jid=A)

    msg = out["messages"][0]
    assert msg["reply_to"] == {"id": "GONE", "found": False}
    assert msg["thread_root_id"] == "ORPHAN" and out["root_id"] == "ORPHAN"


def test_reply_to_a_media_shows_its_kind_and_caption(conn):  # REPL-03
    add(conn, "DOC", 1, content="legenda do documento", media_type="document", filename="proposta.pdf",
        mimetype="application/pdf", file_length=2048)
    add(conn, "IMG", 2, content="", media_type="image", filename="foto.jpg", mimetype="image/jpeg", file_length=10)
    add(conn, "R1", 3, quoted="DOC", content="li o documento")
    add(conn, "R2", 4, quoted="IMG", content="linda foto")

    msgs = by_id(thread(chat_jid=A))

    assert msgs["R1"]["reply_to"]["media"] == {"type": "document", "filename": "proposta.pdf",
                                               "mimetype": "application/pdf", "size": 2048}
    assert msgs["R1"]["reply_to"]["content"] == "legenda do documento"
    assert msgs["R2"]["reply_to"]["media"]["type"] == "image" and msgs["R2"]["reply_to"]["content"] == ""
    assert msgs["R1"]["media"] is None


def test_message_media_block_and_local_path(conn, tmp_path):  # REPL-04
    present = tmp_path / "a.mp4"
    present.write_bytes(b"x")
    add(conn, "V", 1, media_type="video", filename="a.mp4", mimetype="video/mp4", file_length=500,
        local_path=str(present), forwarded=1)
    add(conn, "V2", 2, media_type="video", filename="b.mp4", local_path=str(tmp_path / "gone.mp4"))

    msgs = by_id(thread(chat_jid=A))

    assert msgs["V"]["media"] == {"type": "video", "filename": "a.mp4", "mimetype": "video/mp4", "size": 500,
                                  "forwarded": True, "quoted_message_id": None, "local_path": str(present)}
    assert msgs["V2"]["media"]["local_path"] is None


def test_forwarded_is_on_the_message_with_or_without_media(conn):  # REPL-05
    add(conn, "TXT", 1, content="encaminhada", forwarded=1)
    add(conn, "PLAIN", 2)

    msgs = by_id(thread(chat_jid=A))

    assert msgs["TXT"]["forwarded"] is True and msgs["TXT"]["media"] is None
    assert msgs["PLAIN"]["forwarded"] is False


def test_old_database_without_quoted_id_still_works(tmp_path, monkeypatch):  # banco de um bridge antigo
    c = make(tmp_path, monkeypatch, extra="")
    c.execute("INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) "
              "VALUES ('M', 'a@g.us', 's', 'oi', '2026-10-21 10:00:00+00:00', 0)")
    c.commit()

    window = thread(chat_jid=A)
    chain_ = thread(message_id="M", chat_jid=A)

    assert ids(window) == ["M"] and window["messages"][0]["reply_to"] is None and window["messages"][0]["replies"] == []
    assert ids(chain_) == ["M"]


def test_missing_database_is_reported_and_not_created(tmp_path, monkeypatch):
    missing = tmp_path / "none.db"
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(missing))

    out = thread(chat_jid=A)

    assert out["success"] is False and out["error_code"] == "DIRECTORY_UNAVAILABLE" and not missing.exists()


def test_mentions_in_content_show_names(conn):
    add(conn, "M", 1, content="oi @5511999990000")
    conn.executescript("""
        CREATE TABLE contacts_identity (id INTEGER PRIMARY KEY, lid TEXT UNIQUE, phone TEXT UNIQUE,
            full_name TEXT, push_name TEXT);
        INSERT INTO contacts_identity (phone, full_name) VALUES ('5511999990000', 'Ana Souza');""")

    assert thread(chat_jid=A)["messages"][0]["content"] == "oi @Ana Souza"


def test_mention_of_a_name_that_already_starts_with_at_is_not_doubled(conn):
    add(conn, "M", 1, content="oi @5511999990000")
    conn.executescript("""
        CREATE TABLE contacts_identity (id INTEGER PRIMARY KEY, lid TEXT UNIQUE, phone TEXT UNIQUE,
            full_name TEXT, push_name TEXT);
        INSERT INTO contacts_identity (phone, push_name) VALUES ('5511999990000', '@usuario');""")

    assert thread(chat_jid=A)["messages"][0]["content"] == "oi @usuario"


def test_tool_documents_both_uses():
    doc = main.get_message_thread.__doc__

    for expected in ("message_id", "chat_jid", "reply_to", "replies", "thread_root_id", "forwarded", "media", "AMBIGUOUS_MESSAGE"):
        assert expected in doc
