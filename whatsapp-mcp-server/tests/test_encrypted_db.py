"""Testes do T10: o servidor MCP lê os bancos cifrados pelo bridge (KEY-03, PYENC-01..05)."""
import asyncio
import os
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dbcrypto  # noqa: E402
import main  # noqa: E402
import monitor  # noqa: E402
import send_policy  # noqa: E402
import whatsapp  # noqa: E402

MASTER = bytes(range(32)).hex()
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "encrypted_messages.db")   # escrito pelo bridge em Go
GROUP = "fixture@g.us"
# respostas conhecidas calculadas com um HKDF independente e conferidas no teste do bridge em Go (dbkey_test.go)
MESSAGES_KEY = "0f3cd7db53fb5087a9624f5c1f1f30c3e1f99a1e474be5f04df6e8535b1f6d86"
SESSION_KEY = "b82e2676fe8259e623b594a53d488cd4dff20fa468da22bd16f5dd904e4bb416"


@pytest.fixture
def encrypted(tmp_path, monkeypatch):
    path = str(tmp_path / "messages.db")
    shutil.copy(FIXTURE, path)
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    monkeypatch.setenv("WHATSAPP_DB_KEY", MASTER)
    return path


def test_derived_keys_match_the_bridge():  # KEY-03
    master = bytes(range(32))

    assert dbcrypto.derive_key(master, "messages.db").hex() == MESSAGES_KEY
    assert dbcrypto.derive_key(master, "whatsapp.db").hex() == SESSION_KEY


def test_hkdf_matches_rfc_5869_test_case_1():  # KEY-03
    okm = dbcrypto.hkdf_sha256(bytes([0x0b] * 22), bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"), 42,
                               bytes.fromhex("000102030405060708090a0b0c"))

    assert okm.hex() == "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"


def test_key_sources_in_order(tmp_path, monkeypatch):  # KEY-01 (lado Python)
    other = "ab" * 32
    keyfile = tmp_path / "my.key"
    keyfile.write_text(other + "\n")
    monkeypatch.setenv("WHATSAPP_DB_KEY", MASTER.upper())
    monkeypatch.setenv("WHATSAPP_DB_KEY_FILE", str(keyfile))
    assert dbcrypto.master_key().hex() == MASTER
    monkeypatch.setenv("WHATSAPP_DB_KEY", "")
    assert dbcrypto.master_key().hex() == other
    monkeypatch.setenv("WHATSAPP_DB_KEY_FILE", "")
    default = os.path.join(os.environ["WHATSAPP_CONFIG_DIR"], "db.key")
    os.makedirs(os.path.dirname(default))
    with open(default, "w") as f:
        f.write(MASTER + "\n")
    assert dbcrypto.master_key().hex() == MASTER


@pytest.mark.parametrize("bad", ["zz" + "0" * 62, "0" * 63, "0" * 65, "texto-secreto"])
def test_a_bad_key_source_is_an_error_without_fallback(tmp_path, monkeypatch, bad):  # KEY-02, PYENC-02
    good = tmp_path / "good.key"
    good.write_text(MASTER)
    monkeypatch.setenv("WHATSAPP_DB_KEY_FILE", str(good))
    monkeypatch.setenv("WHATSAPP_DB_KEY", bad)

    with pytest.raises(dbcrypto.DatabaseKeyError) as error:
        dbcrypto.master_key()

    assert error.value.error_code == "DATABASE_KEY_UNAVAILABLE" and bad not in str(error.value)


def test_no_key_anywhere_is_a_clear_error_and_nothing_is_created(monkeypatch):  # PYENC-02
    monkeypatch.setenv("WHATSAPP_DB_KEY", "")

    with pytest.raises(dbcrypto.DatabaseKeyError) as error:
        dbcrypto.master_key()

    assert "db.key" in str(error.value) and error.value.error_code == "DATABASE_KEY_UNAVAILABLE"
    assert not os.path.exists(os.environ["WHATSAPP_CONFIG_DIR"])      # o servidor nunca cria chave


def test_the_server_reads_a_database_the_bridge_encrypted(encrypted):  # PYENC-01, PYENC-04
    out = whatsapp.list_messages(chat_jid=GROUP, format="structured", include_context=False, order="asc")

    assert [m["id"] for m in out["messages"]] == ["FX1", "FX2"]
    assert out["messages"][0]["content"] == "primeira mensagem do fixture"


def test_every_reading_tool_works_on_the_encrypted_database(encrypted):  # PYENC-03
    thread = main.get_message_thread(message_id="FX2", chat_jid=GROUP)
    assert [m["id"] for m in thread["messages"]] == ["FX1", "FX2"]
    assert thread["messages"][1]["reply_to"]["content"] == "primeira mensagem do fixture"
    assert "Grupo Fixture" in whatsapp.list_messages(chat_jid=GROUP, include_context=False)
    assert [c.name for c in whatsapp.list_chats(query="Fixture")] == ["Grupo Fixture"]
    assert whatsapp.get_chat(GROUP).name == "Grupo Fixture"
    assert whatsapp.get_message_context("FX1", before=0, after=1).after[0].id == "FX2"
    assert any(c.name == "Ana Fixture" for c in whatsapp.search_contacts("Ana"))
    assert "Ana Fixture" in whatsapp.sender_label("207571524796601")        # contacts_identity também é lido
    first = asyncio.run(monitor.monitor_messages(targets=["5511999990000"], triggers=["from"]))
    assert first["success"] is True and first["cursor"] == "2"
    assert monitor.current_cursor() == 2


def test_the_monitor_scan_reads_the_encrypted_database(encrypted):  # PYENC-03
    from datetime import datetime, timezone
    profile = monitor.Profile(name="p", targets=["5511999990000"], triggers=["from"], max_event_age_minutes=10 ** 6)

    result = monitor.scan(profile, 0, 10, now=datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc))

    assert [e["message"]["id"] for e in result.events] == ["FX1"]


def test_without_a_key_the_error_is_clear_not_an_empty_result(encrypted, monkeypatch):  # PYENC-02
    monkeypatch.setenv("WHATSAPP_DB_KEY", "")

    with pytest.raises(dbcrypto.DatabaseKeyError) as error:
        whatsapp.list_messages(chat_jid=GROUP, include_context=False)

    assert error.value.error_code == "DATABASE_KEY_UNAVAILABLE"


def test_a_wrong_key_is_unreadable_not_empty(encrypted, monkeypatch):  # PYENC-02
    monkeypatch.setenv("WHATSAPP_DB_KEY", "ab" * 32)

    with pytest.raises(dbcrypto.DatabaseKeyError) as error:
        whatsapp.list_messages(chat_jid=GROUP, include_context=False)

    assert error.value.error_code == "DATABASE_UNREADABLE" and "ab" * 32 not in str(error.value)


def test_tools_that_return_dicts_report_the_key_problem(encrypted, monkeypatch):  # PYENC-02
    monkeypatch.setenv("WHATSAPP_DB_KEY", "")
    monkeypatch.setattr(monitor, "own_number", lambda: "5511999990000")

    out = asyncio.run(monitor.monitor_messages(targets=["me"], cursor=0))
    batch = main.download_media_batch(GROUP)

    assert out["success"] is False and out["error_code"] == "DIRECTORY_UNAVAILABLE" and "DATABASE_KEY_UNAVAILABLE" in out["message"]
    assert batch["success"] is False and batch["error_code"] == "DIRECTORY_UNAVAILABLE"
    with pytest.raises(send_policy.DirectoryError):
        whatsapp._open_chats()


def test_the_connection_is_read_only(encrypted):  # PYENC-01
    conn = whatsapp._connect()
    try:
        with pytest.raises(dbcrypto.DB_ERRORS):
            conn.execute("CREATE TABLE intruso (x)")
    finally:
        conn.close()


def test_a_plaintext_database_still_opens_with_the_standard_module(tmp_path, monkeypatch):  # PYENC-01
    path = str(tmp_path / "messages.db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    c.execute("INSERT INTO chats VALUES ('a@g.us', 'Texto Puro', NULL)")
    c.commit()
    c.close()
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", path)
    monkeypatch.setenv("WHATSAPP_DB_KEY", "")           # sem chave: o banco em texto puro não precisa

    conn = whatsapp._connect()

    assert isinstance(conn, sqlite3.Connection) and conn.execute("SELECT name FROM chats").fetchone() == ("Texto Puro",)
    conn.close()


def test_a_missing_file_is_an_error_and_is_not_created(tmp_path, monkeypatch):  # PYENC-05
    missing = tmp_path / "none.db"
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(missing))

    with pytest.raises(sqlite3.Error):
        whatsapp._connect()
    with pytest.raises(send_policy.DirectoryError):
        whatsapp._open_chats()

    assert not missing.exists()


def test_a_file_that_is_neither_plain_nor_encrypted_is_unreadable(tmp_path, monkeypatch):  # PYENC-02
    path = tmp_path / "messages.db"
    path.write_bytes(os.urandom(8192))
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", str(path))
    monkeypatch.setenv("WHATSAPP_DB_KEY", MASTER)

    with pytest.raises(dbcrypto.DatabaseKeyError) as error:
        whatsapp._connect()

    assert error.value.error_code == "DATABASE_UNREADABLE"


def test_without_the_sqlcipher_package_an_encrypted_database_is_a_clear_error(encrypted, monkeypatch):  # PYENC-02
    monkeypatch.setattr(dbcrypto, "sqlcipher", None)

    with pytest.raises(dbcrypto.DatabaseKeyError) as error:
        whatsapp._connect()

    assert "sqlcipher3" in str(error.value) and error.value.error_code == "DATABASE_UNREADABLE"


def test_the_database_error_tuple_catches_both_libraries():  # PYENC-03
    import sqlcipher3.dbapi2 as sqlcipher
    assert sqlite3.Error in dbcrypto.DB_ERRORS and sqlcipher.Error in dbcrypto.DB_ERRORS


def test_encrypted_files_have_no_plain_header_and_no_readable_text():  # PYENC-04
    data = open(FIXTURE, "rb").read()

    assert not data.startswith(dbcrypto.SQLITE_MAGIC)
    assert b"primeira mensagem" not in data and b"Grupo Fixture" not in data and b"messages" not in data


def test_reply_target_lookup_reads_the_encrypted_database(encrypted):  # PYENC-03
    found = whatsapp._find_reply_target("FX1", GROUP)

    assert found and found["id"] == "FX1" and "primeira mensagem" in found["text"]


def test_a_key_of_only_blanks_is_an_error_not_unset(tmp_path, monkeypatch):  # KEY-02
    keyfile = tmp_path / "good.key"
    keyfile.write_text(MASTER)
    monkeypatch.setenv("WHATSAPP_DB_KEY_FILE", str(keyfile))
    monkeypatch.setenv("WHATSAPP_DB_KEY", "   ")

    with pytest.raises(dbcrypto.DatabaseKeyError) as error:
        dbcrypto.master_key()

    assert "WHATSAPP_DB_KEY" in str(error.value)


def test_no_module_opens_the_database_without_going_through_the_key(tmp_path):  # PYENC-01
    import pathlib
    offenders = [p.name for p in pathlib.Path(__file__).parent.parent.glob("*.py")
                 if p.name != "dbcrypto.py" and "sqlite3.connect" in p.read_text()]

    assert offenders == [], f"these open the database directly and would ignore the encryption: {offenders}"
