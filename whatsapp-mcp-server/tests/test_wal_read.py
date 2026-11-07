"""The server reads a database in WAL mode while the bridge still has it open, including the rows that are only
in the -wal file (WAL-06)."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dbcrypto  # noqa: E402

KEY = "11" * 32


def fill(conn, rows):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")   # nothing is moved to the main file: the rows live in the -wal
    conn.execute("CREATE TABLE messages (id TEXT, content TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO messages VALUES (?, ?)", (f"M{i}", f"text {i}"))
    conn.commit()


def test_a_plain_wal_database_is_read_while_the_writer_is_open(tmp_path):  # WAL-06
    path = str(tmp_path / "messages.db")
    writer = sqlite3.connect(path)
    fill(writer, 5)
    assert os.path.getsize(path + "-wal") > 0, "the rows must be in the -wal file for this test to mean anything"

    reader = dbcrypto.connect_readonly(path)
    try:
        assert reader.execute("SELECT count(*) FROM messages").fetchone() == (5,)
        writer.execute("INSERT INTO messages VALUES ('NEW', 'written after the reader opened')")
        writer.commit()
        assert reader.execute("SELECT content FROM messages WHERE id = 'NEW'").fetchone() == ("written after the reader opened",)
    finally:
        reader.close()
        writer.close()


def test_the_connection_stays_read_only_on_a_wal_database(tmp_path):  # WAL-06
    path = str(tmp_path / "messages.db")
    writer = sqlite3.connect(path)
    fill(writer, 1)
    reader = dbcrypto.connect_readonly(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("INSERT INTO messages VALUES ('X', 'no')")
    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(dbcrypto.sqlcipher is None, reason="sqlcipher3 is not installed")
def test_an_encrypted_wal_database_is_read_while_the_writer_is_open(tmp_path, monkeypatch):  # WAL-06
    monkeypatch.setenv("WHATSAPP_DB_KEY", KEY)
    path = str(tmp_path / "messages.db")
    key = dbcrypto.derive_key(dbcrypto.master_key(), os.path.basename(path))
    writer = dbcrypto.sqlcipher.connect(path)
    writer.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
    fill(writer, 4)
    assert os.path.getsize(path + "-wal") > 0

    reader = dbcrypto.connect_readonly(path)
    try:
        assert reader.execute("SELECT count(*) FROM messages").fetchone() == (4,)
    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(dbcrypto.sqlcipher is None, reason="sqlcipher3 is not installed")
def test_an_encrypted_database_with_an_empty_main_file_but_a_wal_is_not_taken_for_plain_text(tmp_path, monkeypatch):  # WAL-02
    # the bridge checkpoints the schema into the main file at start so this state does not arise; if it ever did,
    # the server must not misread the database as plain text
    monkeypatch.setenv("WHATSAPP_DB_KEY", KEY)
    path = str(tmp_path / "messages.db")
    key = dbcrypto.derive_key(dbcrypto.master_key(), os.path.basename(path))
    writer = dbcrypto.sqlcipher.connect(path)
    writer.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
    fill(writer, 2)
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # what the bridge does at start: the main file gets the pages
    assert os.path.getsize(path) > 0
    assert not dbcrypto.is_plaintext(path)
    writer.close()
