"""Testes do P1-6: a última mensagem de cada chat vem de `messages`, não de igualdade de horário."""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import whatsapp  # noqa: E402

SCHEMA = """
CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
    is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB,
    file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER, PRIMARY KEY (id, chat_jid));
"""
BRT = timezone(timedelta(hours=-3))


class Db:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.n = 0

    def chat(self, jid, name, last_time):
        self.conn.execute("INSERT INTO chats VALUES (?, ?, ?)", (jid, name, last_time))
        self.conn.commit()

    def message(self, chat, ts, content="", sender="alice", media_type="", is_from_me=0):
        self.n += 1
        self.conn.execute(
            "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me, media_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"m{self.n}", chat, sender, content, ts, is_from_me, media_type),
        )
        self.conn.commit()


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = Db(str(tmp_path / "messages.db"))
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", d.path)
    return d


def test_time_and_text_come_from_the_latest_message(db):  # LAST-01, LAST-02
    # o horário do chat (11:47:34) não coincide com nenhuma mensagem gravada
    db.chat("g@g.us", "Projeto Elbratec", "2026-10-21 11:47:34-03:00")
    db.message("g@g.us", "2026-10-21 11:30:49-03:00", "mais antiga", sender="191134835990646")
    db.message("g@g.us", "2026-10-21 11:41:05-03:00", "a última gravada", sender="170166738510077")

    [chat] = whatsapp.list_chats(query="Projeto")

    assert chat.last_message_time == datetime(2026, 10, 21, 11, 41, 5, tzinfo=BRT)
    assert chat.last_message == "a última gravada"
    assert chat.last_sender == "170166738510077"
    assert not chat.last_is_from_me


def test_stale_chat_time_still_shows_real_last_message(db):  # LAST-01 (o caso de 31/08)
    db.chat("g@g.us", "Software Elbratec", "2026-08-31 21:43:56-03:00")
    db.message("g@g.us", "2026-10-20 18:00:00-03:00", "mensagem de outubro")

    [chat] = whatsapp.list_chats(query="Software")

    assert chat.last_message_time == datetime(2026, 10, 20, 18, 0, tzinfo=BRT)
    assert chat.last_message == "mensagem de outubro"


@pytest.mark.parametrize("media,caption,expected", [
    ("image", "FICOU BOM", "[image] FICOU BOM"),
    ("image", "", "[image]"),
    ("document", "", "[document]"),
    ("audio", "", "[audio]"),
])
def test_media_last_message_shows_type_and_caption(db, media, caption, expected):  # LAST-03
    db.chat("g@g.us", "G", "2026-10-21 10:00:00-03:00")
    db.message("g@g.us", "2026-10-21 09:00:00-03:00", "texto antigo")
    db.message("g@g.us", "2026-10-21 10:00:00-03:00", caption, media_type=media)
    assert whatsapp.list_chats()[0].last_message == expected


def test_text_last_message_has_no_type_prefix(db):  # LAST-03
    db.chat("g@g.us", "G", "2026-10-21 10:00:00-03:00")
    db.message("g@g.us", "2026-10-21 10:00:00-03:00", "só texto")
    assert whatsapp.list_chats()[0].last_message == "só texto"


def test_last_active_orders_by_latest_message_not_by_chat_time(db):  # LAST-04
    # A: horário do chat antigo (defasado), mas a mensagem mais nova de todas
    db.chat("a@g.us", "A", "2026-08-31 21:43:56-03:00")
    db.message("a@g.us", "2026-10-21 12:00:00-03:00", "nova")
    # B: horário do chat recente (atividade fantasma), mensagem mais antiga
    db.chat("b@g.us", "B", "2026-10-21 12:30:00-03:00")
    db.message("b@g.us", "2026-10-21 08:00:00-03:00", "velha")

    assert [c.jid for c in whatsapp.list_chats(sort_by="last_active")] == ["a@g.us", "b@g.us"]


def test_ordering_compares_instants_across_time_zones(db):  # LAST-04
    # 11:00-03:00 = 14:00Z; 13:00+00:00 = 13:00Z. Como texto, "13:00:00+00:00" > "11:00:00-03:00".
    db.chat("local@g.us", "local", "2026-10-21 11:00:00-03:00")
    db.message("local@g.us", "2026-10-21 11:00:00-03:00", "x")
    db.chat("utc@g.us", "utc", "2026-10-21 13:00:00+00:00")
    db.message("utc@g.us", "2026-10-21 13:00:00+00:00", "y")

    assert [c.jid for c in whatsapp.list_chats()] == ["local@g.us", "utc@g.us"]


def test_latest_message_of_a_chat_compares_instants(db):  # LAST-04
    db.chat("g@g.us", "G", "2026-10-21 11:00:00-03:00")
    db.message("g@g.us", "2026-10-21 11:00:00-03:00", "14:00Z, a mais nova")
    db.message("g@g.us", "2026-10-21 13:00:00+00:00", "13:00Z, parece maior como texto")
    assert whatsapp.list_chats()[0].last_message == "14:00Z, a mais nova"


def test_same_timestamp_is_broken_by_insertion_order(db):  # LAST-01 (empate)
    db.chat("g@g.us", "G", "2026-10-21 10:00:00-03:00")
    db.message("g@g.us", "2026-10-21 10:00:00-03:00", "primeira")
    db.message("g@g.us", "2026-10-21 10:00:00-03:00", "segunda")
    assert whatsapp.list_chats()[0].last_message == "segunda"


def test_chat_without_messages_keeps_its_own_time(db):  # LAST-05
    db.chat("e@g.us", "Vazio", "2026-10-21 09:00:00-03:00")
    [chat] = whatsapp.list_chats()
    assert chat.last_message_time == datetime(2026, 10, 21, 9, 0, tzinfo=BRT)
    assert not chat.last_message and chat.last_sender is None


def test_sort_by_name_still_works(db):  # LAST-04 (sort_by=name)
    db.chat("z@g.us", "Zeta", "2026-10-21 12:00:00-03:00")
    db.chat("a@g.us", "Alfa", "2026-01-01 12:00:00-03:00")
    assert [c.name for c in whatsapp.list_chats(sort_by="name")] == ["Alfa", "Zeta"]


def test_include_last_message_false_leaves_fields_empty(db):  # LAST-06
    db.chat("g@g.us", "G", "2026-10-21 10:00:00-03:00")
    db.message("g@g.us", "2026-10-21 10:00:00-03:00", "oi", sender="alice", is_from_me=1)
    [chat] = whatsapp.list_chats(include_last_message=False)
    assert chat.last_message is None and chat.last_sender is None and chat.last_is_from_me is None
    assert chat.last_message_time == datetime(2026, 10, 21, 10, 0, tzinfo=BRT)


def test_get_chat_applies_the_same_rule(db):  # LAST-07
    db.chat("g@g.us", "G", "2026-10-21 11:47:34-03:00")
    db.message("g@g.us", "2026-10-21 11:41:05-03:00", "legenda", media_type="image")

    chat = whatsapp.get_chat("g@g.us")
    assert chat.last_message == "[image] legenda"
    assert chat.last_message_time == datetime(2026, 10, 21, 11, 41, 5, tzinfo=BRT)
    bare = whatsapp.get_chat("g@g.us", include_last_message=False)
    assert bare.last_message is None
    assert whatsapp.get_chat("nao@existe") is None


def test_get_direct_chat_by_contact_applies_the_same_rule(db):  # LAST-07
    db.chat("5581999@s.whatsapp.net", "Maria", "2026-10-21 11:47:34-03:00")
    db.message("5581999@s.whatsapp.net", "2026-10-21 11:41:05-03:00", "oi maria", sender="5581999")

    chat = whatsapp.get_direct_chat_by_contact("5581999")
    assert chat.last_message == "oi maria"
    assert chat.last_message_time == datetime(2026, 10, 21, 11, 41, 5, tzinfo=BRT)
    assert whatsapp.get_direct_chat_by_contact("000") is None


def test_get_contact_chats_returns_each_chat_once(db):  # LAST-08
    db.chat("g1@g.us", "G1", "2026-10-21 12:00:00-03:00")
    db.chat("g2@g.us", "G2", "2026-10-20 12:00:00-03:00")
    db.chat("g3@g.us", "G3", "2026-10-19 12:00:00-03:00")
    for i in range(5):  # várias mensagens da mesma pessoa no mesmo chat
        db.message("g1@g.us", f"2026-10-21 10:0{i}:00-03:00", f"msg {i}", sender="alice")
    db.message("g2@g.us", "2026-10-20 11:00:00-03:00", "outra", sender="alice")
    db.message("g3@g.us", "2026-10-19 11:00:00-03:00", "de outra pessoa", sender="bob")

    chats = whatsapp.get_contact_chats("alice")

    assert [c.jid for c in chats] == ["g1@g.us", "g2@g.us"]
    assert chats[0].last_message == "msg 4"  # a última do chat, não uma das da pessoa
    assert whatsapp.get_contact_chats("ninguem") == []


def test_get_contact_chats_includes_the_direct_chat_and_paginates(db):  # LAST-08
    db.chat("alice@s.whatsapp.net", "Alice", "2026-10-21 12:00:00-03:00")
    db.message("alice@s.whatsapp.net", "2026-10-21 12:00:00-03:00", "oi", sender="alice")
    for i in range(3):
        db.chat(f"g{i}@g.us", f"G{i}", f"2026-10-1{i}T12:00:00-03:00".replace("T", " "))
        db.message(f"g{i}@g.us", f"2026-10-1{i} 12:00:00-03:00", "x", sender="alice")

    first = whatsapp.get_contact_chats("alice@s.whatsapp.net", limit=2, page=0)
    second = whatsapp.get_contact_chats("alice@s.whatsapp.net", limit=2, page=1)

    assert [c.jid for c in first] == ["alice@s.whatsapp.net", "g2@g.us"]
    assert [c.jid for c in second] == ["g1@g.us", "g0@g.us"]
