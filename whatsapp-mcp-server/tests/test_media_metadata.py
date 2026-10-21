"""Testes do P1-7: metadados de mídia (arquivo, MIME, tamanho, encaminhada, resposta, caminho local) na leitura."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import whatsapp  # noqa: E402

BASE_COLUMNS = """
    id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN,
    media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB,
    file_length INTEGER, local_path TEXT"""
META_COLUMNS = ", mimetype TEXT, forwarded BOOLEAN, quoted_id TEXT"
CHATS = "CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);"
CHAT = "g@g.us"


def make_db(path, with_meta=True):
    conn = sqlite3.connect(path)
    conn.executescript(
        CHATS + f"CREATE TABLE messages ({BASE_COLUMNS}{META_COLUMNS if with_meta else ''}, PRIMARY KEY (id, chat_jid));"
    )
    conn.execute("INSERT INTO chats VALUES (?, 'Projeto', '2026-10-21 12:00:00+00:00')", (CHAT,))
    conn.commit()
    return conn


def add(conn, id, minute, content="", media_type=None, **cols):
    cols.update(id=id, chat_jid=CHAT, sender="alice", content=content, media_type=media_type,
                timestamp=f"2026-10-21 10:{minute:02d}:00+00:00", is_from_me=0)
    conn.execute(f"INSERT INTO messages ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
    conn.commit()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    c = make_db(path)
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    return c


def read_one(**kwargs):
    return whatsapp.list_messages(chat_jid=CHAT, include_context=False, **kwargs)


def test_document_shows_name_mime_and_size(conn):  # MEDIA-06
    add(conn, "DOC1", 1, media_type="document", filename="proposta.pdf",
        mimetype="application/pdf", file_length=2048)

    out = read_one()

    assert "[document - Message ID: DOC1 - Chat JID: g@g.us" in out
    assert "File: proposta.pdf (application/pdf, 2.0 KB)" in out


@pytest.mark.parametrize("size, shown", [(512, "512 B"), (1536, "1.5 KB"), (5 * 1024 * 1024, "5.0 MB")])
def test_size_is_human_readable(conn, size, shown):  # MEDIA-06
    add(conn, "D", 1, media_type="document", filename="a.bin", file_length=size)

    assert f"a.bin ({shown})" in read_one()


def test_unknown_size_and_mime_are_left_out(conn):  # MEDIA-06 (file_length 0 ou ausente)
    add(conn, "D", 1, media_type="document", filename="a.bin", file_length=0)

    out = read_one()

    assert "File: a.bin" in out
    assert "0 B" not in out and "None" not in out


def test_forwarded_and_reply_are_marked(conn):  # MEDIA-07
    add(conn, "DOC1", 1, media_type="document", filename="p.pdf", forwarded=1, quoted_id="Q123")

    out = read_one()

    assert "Forwarded" in out
    assert "Reply to: Q123" in out


def test_plain_media_is_not_marked(conn):  # MEDIA-07
    add(conn, "IMG", 1, media_type="image", filename="image_IMG.jpg", forwarded=0, quoted_id="")

    out = read_one()

    assert "Forwarded" not in out and "Reply to" not in out


def test_text_message_forwarded_or_reply_is_marked(conn):  # MEDIA-11
    add(conn, "T1", 1, content="vejam isso", forwarded=1)
    add(conn, "T2", 2, content="concordo", quoted_id="T1")
    add(conn, "T3", 3, content="normal")

    lines = {("vejam isso" in l and "T1") or ("concordo" in l and "T2") or ("normal" in l and "T3"): l
             for l in read_one(limit=10).splitlines()}

    assert "Forwarded" in lines["T1"] and "Reply to" not in lines["T1"]
    assert "Reply to: T1" in lines["T2"] and "Forwarded" not in lines["T2"]
    assert "Forwarded" not in lines["T3"] and "Reply to" not in lines["T3"]
    assert "vejam isso" in lines["T1"]  # o texto continua na linha


def test_local_path_shown_only_when_file_exists(conn, tmp_path):  # MEDIA-08
    real = tmp_path / "proposta.pdf"
    real.write_bytes(b"x")
    add(conn, "HAS", 1, media_type="document", filename="proposta.pdf", local_path=str(real))
    add(conn, "GONE", 2, media_type="document", filename="velho.pdf", local_path=str(tmp_path / "apagado.pdf"))

    lines = {("HAS" in l and "HAS") or ("GONE" in l and "GONE"): l for l in read_one(limit=10).splitlines()}

    assert f"Local path: {real}" in lines["HAS"]
    assert "Local path" not in lines["GONE"]


def test_context_messages_show_the_same_details(conn):  # MEDIA-10
    add(conn, "BEFORE", 1, media_type="document", filename="antes.pdf", mimetype="application/pdf",
        file_length=1024, forwarded=1)
    add(conn, "HIT", 2, content="palavra-chave")
    add(conn, "AFTER", 3, media_type="document", filename="depois.docx", quoted_id="HIT")

    listed = whatsapp.list_messages(chat_jid=CHAT, query="palavra-chave", include_context=True,
                                    context_before=1, context_after=1)
    assert "File: antes.pdf (application/pdf, 1.0 KB)" in listed
    assert "Forwarded" in listed
    assert "File: depois.docx" in listed and "Reply to: HIT" in listed

    ctx = whatsapp.get_message_context("HIT", before=1, after=1)
    assert ctx.before[0].filename == "antes.pdf"
    assert ctx.before[0].mimetype == "application/pdf"
    assert ctx.before[0].file_length == 1024
    assert ctx.before[0].forwarded is True
    assert ctx.after[0].quoted_message_id == "HIT"
    assert ctx.message.filename is None and ctx.message.forwarded is False


def test_database_without_new_columns_still_lists(tmp_path, monkeypatch):  # MEDIA-09
    path = str(tmp_path / "old.db")
    c = make_db(path, with_meta=False)
    add(c, "DOC1", 1, content="legenda", media_type="document", filename="p.pdf", file_length=2048)
    add(c, "T", 2, content="oi")
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)

    out = read_one()
    ctx = whatsapp.get_message_context("DOC1", before=0, after=1)

    assert "[document - Message ID: DOC1 - Chat JID: g@g.us" in out and "legenda" in out
    assert "File: p.pdf (2.0 KB)" in out
    assert "Forwarded" not in out and "Reply to" not in out
    assert ctx.message.mimetype is None and ctx.after[0].content == "oi"


def test_message_id_and_chat_jid_prefix_is_unchanged(conn):  # compatibilidade do prefixo (Assumptions)
    add(conn, "IMG", 1, content="legenda", media_type="image", filename="image_IMG.jpg")

    assert "[image - Message ID: IMG - Chat JID: g@g.us" in read_one()
    assert "legenda" in read_one()
