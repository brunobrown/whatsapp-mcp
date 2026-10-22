"""Testes do P1-5: nomes, LID e telefone na leitura, busca, filtro e participantes de grupo."""
import os
import sqlite3
import sys
from datetime import datetime
from unittest import mock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402
import whatsapp  # noqa: E402

SCHEMA = """
CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
    is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB,
    file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER, PRIMARY KEY (id, chat_jid));
"""
IDENTITY_SCHEMA = """
CREATE TABLE contacts_identity (id INTEGER PRIMARY KEY AUTOINCREMENT, lid TEXT, phone TEXT,
    full_name TEXT, push_name TEXT, updated_at TIMESTAMP);
"""

GROUP = "120363@g.us"
TEILOR_LID = "160889625940144"
TEILOR_PHONE = "555181993271"


def build_db(path, with_identity=True):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    if with_identity:
        conn.executescript(IDENTITY_SCHEMA)
        conn.executemany(
            "INSERT INTO contacts_identity (lid, phone, full_name, push_name) VALUES (?, ?, ?, ?)",
            [
                (TEILOR_LID, TEILOR_PHONE, "Teilor Dall Bello", "Teilor"),
                ("241841085649006", None, None, "Luan Push"),
                (None, "558181282941", "Maria da Agenda", "Maria Push"),
            ],
        )
    conn.execute("INSERT INTO chats VALUES (?, ?, ?)", (GROUP, "Software Elbratec", "2026-10-20 10:00:00"))
    conn.executemany(
        "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?, ?, ?, ?, ?, 0)",
        [
            ("m1", GROUP, TEILOR_LID, "oi pela lid", "2026-10-20 10:00:00"),
            ("m2", GROUP, TEILOR_PHONE, "oi pelo telefone", "2026-10-20 10:01:00"),
            ("m3", GROUP, "241841085649006", "oi do luan", "2026-10-20 10:02:00"),
            ("m4", GROUP, "999000111222", "ninguem conhece", "2026-10-20 10:03:00"),
            ("m5", GROUP, "999000111222", "falei com @160889625940144 e @555000999 e @558181282941", "2026-10-20 10:04:00"),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    build_db(path)
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    return path


def message(sender, content="x"):
    return whatsapp.Message(
        timestamp=datetime(2026, 10, 20, 10, 0), sender=sender, content=content,
        is_from_me=False, chat_jid=GROUP, id="mid", chat_name="Software Elbratec",
    )


def test_sender_with_known_name_shows_name_and_id(db):  # NAME-01
    assert f"From: Teilor Dall Bello ({TEILOR_LID}): oi" in whatsapp.format_message(message(TEILOR_LID, "oi"))
    assert f"From: Teilor Dall Bello ({TEILOR_PHONE}): oi" in whatsapp.format_message(message(TEILOR_PHONE, "oi"))


def test_unknown_sender_shows_only_the_id(db):  # NAME-02
    out = whatsapp.format_message(message("999000111222", "oi"))
    assert "From: 999000111222: oi" in out
    assert "(" not in out.split("From:")[1].split(":")[0]


def test_full_name_wins_over_push_name(db):  # NAME-03
    assert "Maria da Agenda (558181282941)" in whatsapp.format_message(message("558181282941"))
    assert "Luan Push (241841085649006)" in whatsapp.format_message(message("241841085649006"))  # só push name


def test_filter_by_lid_or_phone_returns_both_ids(db):  # NAME-04
    for given in (TEILOR_LID, TEILOR_PHONE, "+55 (51) 8199-3271", f"{TEILOR_PHONE}@s.whatsapp.net"):
        out = whatsapp.list_messages(sender_phone_number=given, include_context=False)
        assert "oi pela lid" in out and "oi pelo telefone" in out, given
        assert "oi do luan" not in out and "ninguem conhece" not in out, given


def test_filter_by_unknown_id_behaves_as_before(db):  # NAME-04 (id sem identidade)
    out = whatsapp.list_messages(sender_phone_number="999000111222", include_context=False)
    assert "ninguem conhece" in out and "falei com" in out
    assert "oi pela lid" not in out


@pytest.mark.parametrize("query", ["teilor dall", "BELLO", TEILOR_LID, TEILOR_PHONE, "81993271", "+55 51 8199"])
def test_search_contacts_finds_person_by_name_phone_or_lid(db, query):  # NAME-05
    found = whatsapp.search_contacts(query)
    assert any(c.name == "Teilor Dall Bello" for c in found), query
    teilor = next(c for c in found if c.name == "Teilor Dall Bello")
    assert teilor.phone_number == TEILOR_PHONE
    assert teilor.jid == f"{TEILOR_PHONE}@s.whatsapp.net"


def test_search_contacts_lid_only_person_uses_lid_jid(db):  # NAME-05
    found = whatsapp.search_contacts("luan push")
    assert [(c.name, c.jid) for c in found] == [("Luan Push", "241841085649006@lid")]


def test_search_contacts_does_not_duplicate_person_also_in_chats(db):  # NAME-05
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO chats VALUES (?, ?, NULL)", (f"{TEILOR_PHONE}@s.whatsapp.net", "Teilor Dall Bello"))
    conn.commit()
    conn.close()
    found = [c for c in whatsapp.search_contacts("Teilor") if c.name == "Teilor Dall Bello"]
    assert len(found) == 1


def test_mentions_resolve_to_names(db):  # NAME-06
    out = whatsapp.format_message(message("999000111222", "falei com @160889625940144 e @555000999 e @558181282941"))
    assert "@Teilor Dall Bello" in out
    assert "@Maria da Agenda" in out
    assert "@555000999" in out  # desconhecido fica igual
    assert "@160889625940144" not in out


def test_mention_text_without_digits_is_untouched(db):  # NAME-06
    assert "email@example.com e @fulano" in whatsapp.format_message(message("999000111222", "email@example.com e @fulano"))


def test_reading_works_without_identity_table(tmp_path, monkeypatch):  # NAME-07
    path = str(tmp_path / "old.db")
    build_db(path, with_identity=False)
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)

    assert "From: 160889625940144: oi" in whatsapp.format_message(message(TEILOR_LID, "oi"))
    out = whatsapp.list_messages(sender_phone_number=TEILOR_LID, include_context=False)
    assert "oi pela lid" in out and "oi pelo telefone" not in out
    assert whatsapp.search_contacts("Software") == []  # grupos não são contatos
    assert "@160889625940144" in whatsapp.format_message(message("x", "@160889625940144"))


def test_list_messages_output_carries_names(db):  # NAME-01 (caminho completo)
    out = whatsapp.list_messages(chat_jid=GROUP, include_context=False)
    assert "Teilor Dall Bello (160889625940144)" in out
    assert "Luan Push (241841085649006)" in out


# --- GRP-04 / GRP-05 ---

def fake_response(status, payload):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


PARTICIPANTS = [
    {"name": "Teilor Dall Bello", "phone": TEILOR_PHONE, "lid": TEILOR_LID, "is_admin": True},
    {"name": "", "phone": "", "lid": "555555", "is_admin": False},
]


def test_group_participants_tool_returns_bridge_list():  # GRP-04
    body = {"success": True, "participants": PARTICIPANTS}
    with mock.patch("whatsapp.requests.post", return_value=fake_response(200, body)) as post:
        out = main.get_group_participants(GROUP)
    assert out == {"success": True, "participants": PARTICIPANTS}
    assert post.call_args.kwargs["json"] == {"chat_jid": GROUP}
    assert post.call_args.args[0].endswith("/group_participants")


def test_group_participants_bridge_error_message_is_returned():  # GRP-05
    body = {"success": False, "message": "Failed to get group info: not in group"}
    with mock.patch("whatsapp.requests.post", return_value=fake_response(500, body)):
        out = main.get_group_participants(GROUP)
    assert out == {"success": False, "message": "Failed to get group info: not in group"}


def test_group_participants_bridge_unreachable():  # GRP-05
    with mock.patch("whatsapp.requests.post", side_effect=requests.ConnectionError("refused")):
        out = main.get_group_participants(GROUP)
    assert out["success"] is False
    assert "refused" in out["message"]
    assert "participants" not in out


def test_group_participants_requires_a_jid():  # GRP-05
    with mock.patch("whatsapp.requests.post") as post:
        out = main.get_group_participants("")
    assert out["success"] is False
    post.assert_not_called()


def test_filter_normalizes_formatted_number_without_identity(db):  # NAME-04 (telefone formatado, pessoa desconhecida)
    out = whatsapp.list_messages(sender_phone_number="+99 9000-111222", include_context=False)
    assert "ninguem conhece" in out
    assert "oi pela lid" not in out


def test_sender_name_falls_back_to_chat_name(db):  # NAME-01 (sem identidade, o nome do chat continua valendo)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO chats VALUES (?, ?, NULL)", ("558100000000@s.whatsapp.net", "Cliente X"))
    conn.commit()
    conn.close()
    assert "From: Cliente X (558100000000): oi" in whatsapp.format_message(message("558100000000", "oi"))


@pytest.mark.parametrize("text", ["user@160889625940144", "@@160889625940144", "a@160889625940144"])
def test_mention_ignores_ids_glued_to_other_text(db, text):  # NAME-06 (e-mail ou arroba colada não é menção)
    assert whatsapp.resolve_mentions(text) == text


def test_legacy_db_still_finds_chat_contacts(tmp_path, monkeypatch):  # NAME-07
    path = str(tmp_path / "old.db")
    build_db(path, with_identity=False)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO chats VALUES (?, ?, NULL)", ("558100000000@s.whatsapp.net", "Cliente X"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)

    found = whatsapp.search_contacts("Cliente")
    assert [(c.name, c.jid) for c in found] == [("Cliente X", "558100000000@s.whatsapp.net")]


def test_short_digit_query_does_not_match_ids(db):  # NAME-05 (consulta curta não vira ruído)
    assert whatsapp.search_contacts("5") == []
    assert whatsapp.search_contacts("55") == []


def test_non_numeric_text_is_not_treated_as_a_phone(db):  # NAME-04
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO contacts_identity (phone, full_name) VALUES ('2024', 'Número Curto')")
    conn.commit()
    conn.close()
    assert whatsapp.lookup_identity("Teilor2024") is None
    assert whatsapp.lookup_identity("2024").full_name == "Número Curto"


def test_last_interaction_and_contact_chats_resolve_both_ids(db):  # consistência com NAME-04
    # a pessoa é consultada pelo telefone, mas a última mensagem dela veio pela LID
    out = whatsapp.get_last_interaction(TEILOR_PHONE)
    assert "oi pelo telefone" in out  # m2 é a mais recente entre m1 (LID) e m2 (telefone)
    out = whatsapp.get_last_interaction(TEILOR_LID)
    assert "oi pelo telefone" in out
    assert {c.jid for c in whatsapp.get_contact_chats(TEILOR_PHONE)} == {GROUP}
    assert {c.jid for c in whatsapp.get_contact_chats(TEILOR_LID)} == {GROUP}
    assert whatsapp.get_contact_chats("999111000") == []  # desconhecido continua sem resultado
