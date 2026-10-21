"""Testes do P2-10: saída estruturada, paginação, contexto cronológico por chat e filtros com fuso."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import whatsapp  # noqa: E402

A, B = "a@g.us", "b@g.us"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
        CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
            is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB,
            file_enc_sha256 BLOB, file_length INTEGER, local_path TEXT, mimetype TEXT, forwarded BOOLEAN,
            quoted_id TEXT, PRIMARY KEY (id, chat_jid));
        INSERT INTO chats VALUES ('a@g.us', 'Projeto Alfa', NULL), ('b@g.us', 'Família', NULL);
    """)
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    monkeypatch.delenv("WHATSAPP_TIMEZONE", raising=False)
    return c


def add(conn, chat, id, ts, content="x", sender="alice", **cols):
    cols = {"is_from_me": 0, **cols, "id": id, "chat_jid": chat, "sender": sender, "content": content, "timestamp": ts}
    conn.execute(f"INSERT INTO messages ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    conn.commit()


def ids(result):
    return [m["id"] for m in result["messages"]]


def structured(**kwargs):
    kwargs.setdefault("include_context", False)
    return whatsapp.list_messages(format="structured", **kwargs)


def five(conn):
    for n in range(5):
        add(conn, A, f"M{n}", f"2026-10-21 10:0{n}:00+00:00", content=f"msg {n}")


def test_structured_message_has_the_planned_fields(conn):  # QUERY-01, QUERY-07
    add(conn, A, "M1", "2026-10-21 10:00:00-03:00", content="olá", sender="5511999990000")

    out = structured(chat_jid=A)

    assert (out["total"], out["page"], out["limit"], out["has_more"], out["next_page"]) == (1, 0, 20, False, None)
    msg = out["messages"][0]
    assert msg["id"] == "M1" and msg["chat_jid"] == A and msg["chat_name"] == "Projeto Alfa"
    assert msg["sender"] == "5511999990000" and msg["content"] == "olá"
    assert msg["is_from_me"] is False and msg["is_match"] is True and msg["media"] is None
    assert "sender_name" in msg
    assert msg["timestamp"] == "2026-10-21T10:00:00-03:00"


def test_structured_timestamp_without_stored_offset_is_utc(conn):  # QUERY-07 (SQLite lê hora sem fuso como UTC)
    add(conn, A, "M1", "2026-10-21 10:00:00")

    assert structured(chat_jid=A)["messages"][0]["timestamp"] == "2026-10-21T10:00:00+00:00"


def test_structured_media_block(conn, tmp_path):  # QUERY-02
    present = tmp_path / "doc.pdf"
    present.write_bytes(b"x")
    add(conn, A, "D1", "2026-10-21 10:00:00+00:00", media_type="document", filename="doc.pdf",
        mimetype="application/pdf", file_length=2048, forwarded=1, quoted_id="Q1", local_path=str(present))
    add(conn, A, "D2", "2026-10-21 10:01:00+00:00", media_type="image", local_path=str(tmp_path / "gone.jpg"))

    by_id = {m["id"]: m["media"] for m in structured(chat_jid=A)["messages"]}

    assert by_id["D1"] == {"type": "document", "filename": "doc.pdf", "mimetype": "application/pdf", "size": 2048,
                           "forwarded": True, "quoted_message_id": "Q1", "local_path": str(present)}
    assert by_id["D2"]["local_path"] is None  # arquivo apagado não é oferecido


def test_text_is_still_the_default(conn):  # QUERY-03
    add(conn, A, "M1", "2026-10-21 10:00:00+00:00", content="texto simples")

    out = whatsapp.list_messages(chat_jid=A, include_context=False)

    assert isinstance(out, str)
    assert out == whatsapp.list_messages(chat_jid=A, include_context=False, format="text")
    assert "Chat: Projeto Alfa" in out and "texto simples" in out and ">>" not in out


def test_pagination_walks_every_message_once(conn):  # QUERY-04
    five(conn)

    pages = [structured(chat_jid=A, limit=2, page=p, order="asc") for p in range(3)]

    assert [p["total"] for p in pages] == [5, 5, 5]
    assert [p["has_more"] for p in pages] == [True, True, False]
    assert [p["next_page"] for p in pages] == [1, 2, None]
    assert [ids(p) for p in pages] == [["M0", "M1"], ["M2", "M3"], ["M4"]]


def test_page_past_the_end_is_empty(conn):  # QUERY-04
    five(conn)

    out = structured(chat_jid=A, limit=2, page=9)

    assert out["messages"] == [] and out["total"] == 5 and out["has_more"] is False and out["next_page"] is None


def test_total_ignores_context_messages(conn):  # QUERY-04
    five(conn)

    out = structured(chat_jid=A, query="msg 2", include_context=True, context_before=2, context_after=2)

    assert out["total"] == 1 and len(out["messages"]) == 5


def test_order_desc_is_default_and_asc_reverses_it(conn):  # QUERY-05
    five(conn)

    assert ids(structured(chat_jid=A)) == ["M4", "M3", "M2", "M1", "M0"]
    assert ids(structured(chat_jid=A, order="asc")) == ["M0", "M1", "M2", "M3", "M4"]


def test_order_uses_the_instant_not_the_text(conn):  # QUERY-05
    add(conn, A, "LATE", "2026-10-21 10:00:00-03:00")   # 13:00 UTC
    add(conn, A, "EARLY", "2026-10-21 11:00:00+00:00")  # 11:00 UTC: antes, embora o texto "11" venha depois de "10"

    assert ids(structured(chat_jid=A, order="asc")) == ["EARLY", "LATE"]


@pytest.mark.parametrize("kwargs", [{"format": "xml"}, {"order": "up"}])
def test_invalid_format_or_order_raises(conn, kwargs):  # QUERY-06
    with pytest.raises(ValueError):
        whatsapp.list_messages(include_context=False, **kwargs)


def test_context_comes_from_the_matching_chat_even_with_a_repeated_id(conn):  # CTX-01
    add(conn, A, "SAME", "2026-10-21 10:05:00+00:00", content="alvo A")
    add(conn, A, "A1", "2026-10-21 10:04:00+00:00", content="antes em A")
    add(conn, B, "SAME", "2026-10-21 09:00:00+00:00", content="outro B")
    add(conn, B, "B1", "2026-10-21 08:59:00+00:00", content="antes em B")

    out = structured(chat_jid=A, query="alvo A", include_context=True, context_before=1, context_after=0)

    assert [(m["chat_jid"], m["id"]) for m in out["messages"]] == [(A, "A1"), (A, "SAME")]


def test_context_is_grouped_by_chat_and_chronological(conn):  # CTX-02
    add(conn, A, "A0", "2026-10-21 10:00:00+00:00", content="a antes")
    add(conn, A, "A1", "2026-10-21 10:01:00+00:00", content="FICOU BOM em A")
    add(conn, A, "A2", "2026-10-21 10:02:00+00:00", content="a depois")
    add(conn, B, "B0", "2026-10-21 12:00:00+00:00", content="b antes")
    add(conn, B, "B1", "2026-10-21 12:01:00+00:00", content="FICOU BOM em B")
    add(conn, B, "B2", "2026-10-21 12:02:00+00:00", content="b depois")

    out = structured(query="ficou bom", include_context=True, order="desc")

    # B tem o resultado mais recente, então o grupo dele vem primeiro; cada grupo em ordem cronológica
    assert [(m["chat_jid"], m["id"]) for m in out["messages"]] == [
        (B, "B0"), (B, "B1"), (B, "B2"), (A, "A0"), (A, "A1"), (A, "A2")]
    out = structured(query="ficou bom", include_context=True, order="asc")
    assert [m["chat_jid"] for m in out["messages"]] == [A, A, A, B, B, B]


def test_overlapping_contexts_show_each_message_once(conn):  # CTX-03
    for n in range(4):
        add(conn, A, f"M{n}", f"2026-10-21 10:0{n}:00+00:00", content=f"alvo {n}" if n in (1, 2) else "nada")

    out = structured(chat_jid=A, query="alvo", include_context=True, context_before=1, context_after=1, order="asc")

    assert ids(out) == ["M0", "M1", "M2", "M3"]
    assert [m["is_match"] for m in out["messages"]] == [False, True, True, False]


def test_found_message_is_marked_in_text(conn):  # CTX-04
    add(conn, A, "M0", "2026-10-21 10:00:00+00:00", content="antes")
    add(conn, A, "M1", "2026-10-21 10:01:00+00:00", content="achei")
    add(conn, A, "M2", "2026-10-21 10:02:00+00:00", content="depois")

    lines = whatsapp.list_messages(chat_jid=A, query="achei", include_context=True).splitlines()

    marked = [l for l in lines if l.startswith(">>")]
    assert len(marked) == 1 and "achei" in marked[0]
    messages = [l for l in lines if "antes" in l or "achei" in l or "depois" in l]
    assert [("antes" in l, "achei" in l, "depois" in l) for l in messages] == [
        (True, False, False), (False, True, False), (False, False, True)]
    assert [l.startswith(">>") for l in messages] == [False, True, False]


def test_context_boundary_uses_instant_and_write_order_on_ties(conn):  # CTX-05
    add(conn, A, "T1", "2026-10-21 10:00:00-03:00", content="primeira")   # 13:00 UTC
    add(conn, A, "T2", "2026-10-21 13:00:00+00:00", content="segunda")    # mesmo instante, gravada depois
    add(conn, A, "T3", "2026-10-21 12:30:00+00:00", content="antes de ambas")  # 12:30 UTC, texto maior em '12' > '10'

    out = structured(chat_jid=A, query="segunda", include_context=True, context_before=5, context_after=5)

    assert ids(out) == ["T3", "T1", "T2"]


def test_chat_name_filter_is_a_case_insensitive_fragment(conn):  # FILT-01
    add(conn, A, "A1", "2026-10-21 10:00:00+00:00")
    add(conn, B, "B1", "2026-10-21 10:01:00+00:00")

    assert ids(structured(chat_name="alfa")) == ["A1"]
    assert ids(structured(chat_name="FAMÍLIA")) == ["B1"]
    assert structured(chat_name="não existe")["messages"] == []
    assert structured(chat_name="alfa", chat_jid=B)["messages"] == []  # os dois filtros valem juntos
    assert ids(structured(chat_name="alfa", chat_jid=A)) == ["A1"]


def test_after_and_before_compare_instants_across_offsets(conn):  # FILT-02
    add(conn, A, "UTC", "2026-10-21 13:30:00+00:00")   # 10:30 em -03:00
    add(conn, A, "BR", "2026-10-21 09:59:00-03:00")    # 12:59 UTC

    after = structured(chat_jid=A, after="2026-10-21T10:00:00-03:00", order="asc")
    before = structured(chat_jid=A, before="2026-10-21T10:00:00-03:00", order="asc")

    assert ids(after) == ["UTC"]
    assert ids(before) == ["BR"]


def test_date_filter_keeps_sub_second_precision(conn):  # FILT-02
    add(conn, A, "FRAC", "2026-10-21 10:00:00.500000+00:00")

    assert ids(structured(chat_jid=A, after="2026-10-21T10:00:00+00:00")) == ["FRAC"]
    assert structured(chat_jid=A, after="2026-10-21T10:00:01+00:00")["messages"] == []


def test_date_without_zone_uses_whatsapp_timezone(conn, monkeypatch):  # FILT-03
    monkeypatch.setenv("WHATSAPP_TIMEZONE", "America/Recife")
    add(conn, A, "IN", "2026-10-21 12:30:00+00:00")  # 09:30 em Recife
    add(conn, A, "OUT", "2026-10-21 11:30:00+00:00")  # 08:30 em Recife

    assert ids(structured(chat_jid=A, after="2026-10-21T09:00:00")) == ["IN"]
    monkeypatch.setenv("WHATSAPP_TIMEZONE", "UTC")
    assert ids(structured(chat_jid=A, after="2026-10-21T09:00:00", order="asc")) == ["OUT", "IN"]


def test_date_without_zone_falls_back_to_system_zone(conn, monkeypatch):  # FILT-03
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    import time
    time.tzset()
    try:
        add(conn, A, "IN", "2026-10-21 01:30:00+00:00")   # 10:30 em Tóquio
        add(conn, A, "OUT", "2026-10-21 00:30:00+00:00")  # 09:30 em Tóquio

        assert ids(structured(chat_jid=A, after="2026-10-21T10:00:00")) == ["IN"]
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_unknown_timezone_names_the_variable(conn, monkeypatch):  # FILT-04
    monkeypatch.setenv("WHATSAPP_TIMEZONE", "Marte/Olympus")

    with pytest.raises(ValueError, match="WHATSAPP_TIMEZONE"):
        structured(after="2026-10-21T09:00:00")


def test_tool_description_documents_dates_and_options():  # FILT-05
    import main
    doc = main.list_messages.__doc__

    for expected in ("ISO-8601", "WHATSAPP_TIMEZONE", "structured", "has_more", "next_page", "chat_name", "order"):
        assert expected in doc


def test_context_neighbours_never_come_from_another_chat(conn):  # CTX-01
    add(conn, A, "A1", "2026-10-21 10:00:00+00:00", content="antes em A")
    add(conn, A, "SAME", "2026-10-21 10:10:00+00:00", content="alvo A")
    add(conn, A, "A3", "2026-10-21 10:20:00+00:00", content="depois em A")
    add(conn, B, "B1", "2026-10-21 10:05:00+00:00", content="intruso antes")
    add(conn, B, "B2", "2026-10-21 10:15:00+00:00", content="intruso depois")

    out = structured(chat_jid=A, query="alvo A", include_context=True, context_before=1, context_after=1)

    assert [(m["chat_jid"], m["id"]) for m in out["messages"]] == [(A, "A1"), (A, "SAME"), (A, "A3")]
    ctx = whatsapp.get_message_context("SAME", before=1, after=1, chat_jid=A)
    assert (ctx.before[0].id, ctx.after[0].id) == ("A1", "A3")


def test_pagination_with_equal_instants_neither_repeats_nor_skips(conn):  # QUERY-04
    for n in range(5):
        add(conn, A, f"T{n}", "2026-10-21 10:00:00+00:00")  # mesmo instante: desempata a ordem de gravação

    seen = []
    for p in range(3):
        seen += ids(structured(chat_jid=A, limit=2, page=p, order="asc"))

    assert seen == ["T0", "T1", "T2", "T3", "T4"]


@pytest.mark.parametrize("name, wanted", [("100%", "P"), ("a_b", "U"), ("a\\b", "S"), ("ÁLFA", "C")])
def test_chat_name_treats_wildcards_literally_and_ignores_case(conn, name, wanted):  # FILT-01
    for jid, chat in (("p@g.us", "Meta 100% batida"), ("u@g.us", "Grupo a_b"), ("s@g.us", "Pasta a\\b"),
                      ("c@g.us", "Álfa"), ("x@g.us", "Meta 1000 batida"), ("y@g.us", "Grupo axb")):
        conn.execute("INSERT INTO chats VALUES (?, ?, NULL)", (jid, chat))
        add(conn, jid, jid[0].upper(), "2026-10-21 10:00:00+00:00")

    assert ids(structured(chat_name=name)) == [wanted]


def test_date_boundaries_are_exclusive_and_keep_milliseconds(conn):  # FILT-02
    add(conn, A, "EXACT", "2026-10-21 10:00:00+00:00")
    add(conn, A, "MS", "2026-10-21 10:00:00.250000+00:00")

    assert ids(structured(chat_jid=A, after="2026-10-21T10:00:00+00:00")) == ["MS"]
    assert ids(structured(chat_jid=A, before="2026-10-21T10:00:00.250+00:00")) == ["EXACT"]
    assert ids(structured(chat_jid=A, after="2026-10-21T10:00:00.250+00:00")) == []


def test_get_message_context_is_chronological_on_both_sides(conn):
    for n in range(5):
        add(conn, A, f"M{n}", f"2026-10-21 10:0{n}:00+00:00")

    ctx = whatsapp.get_message_context("M2", before=2, after=2, chat_jid=A)

    assert [m.id for m in ctx.before] == ["M0", "M1"] and [m.id for m in ctx.after] == ["M3", "M4"]
    with pytest.raises(ValueError):
        whatsapp.get_message_context("M2", chat_jid=B)


def test_sender_name_is_the_known_name_or_me(conn):  # QUERY-01
    conn.executescript("""
        CREATE TABLE contacts_identity (id INTEGER PRIMARY KEY, lid TEXT UNIQUE, phone TEXT UNIQUE,
            full_name TEXT, push_name TEXT);
        INSERT INTO contacts_identity (phone, full_name) VALUES ('5511999990000', 'Ana Souza');""")
    add(conn, A, "M1", "2026-10-21 10:00:00+00:00", sender="5511999990000")
    add(conn, A, "M2", "2026-10-21 10:01:00+00:00", sender="me", is_from_me=1)

    by_id = {m["id"]: m["sender_name"] for m in structured(chat_jid=A)["messages"]}

    assert "Ana Souza" in by_id["M1"] and by_id["M2"] == "Me"


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": -1}, {"page": -1}])
def test_limit_below_one_or_negative_page_raises(conn, kwargs):  # QUERY-08
    with pytest.raises(ValueError):
        whatsapp.list_messages(include_context=False, **kwargs)
