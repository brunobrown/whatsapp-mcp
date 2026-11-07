package main

import (
	"os"
	"strings"
	"testing"
	"time"
)

func pragma(t *testing.T, store *MessageStore, name string) string {
	t.Helper()
	var value string
	if err := store.db.QueryRow("PRAGMA " + name).Scan(&value); err != nil {
		t.Fatal(err)
	}
	return value
}

func TestMessagesDatabaseUsesWALWithNormalSynchronous(t *testing.T) { // WAL-01
	store := newTestStore(t)

	if got := pragma(t, store, "journal_mode"); got != "wal" {
		t.Errorf("journal_mode = %q, want wal", got)
	}
	if got := pragma(t, store, "synchronous"); got != "1" { // 1 = NORMAL
		t.Errorf("synchronous = %q, want 1 (NORMAL)", got)
	}
	if got := pragma(t, store, "foreign_keys"); got != "1" {
		t.Errorf("foreign_keys = %q, want 1: the other options of the database must still apply", got)
	}
}

func TestMainFileIsNotEmptyOnceTheStoreIsOpen(t *testing.T) { // WAL-02
	// With WAL the pages can stay in the -wal file while the main file is empty, and an empty file is read as
	// "no database yet" (the server would open an encrypted database as plain text and fail).
	newTestStore(t)

	info, err := os.Stat("store/messages.db")
	if err != nil || info.Size() == 0 {
		t.Fatalf("store/messages.db: %v, size %d: the schema must be checkpointed into the main file at start", err, sizeOf(info))
	}
}

func sizeOf(info os.FileInfo) int64 {
	if info == nil {
		return -1
	}
	return info.Size()
}

func TestWALSidecarsAreAsPrivateAsTheDatabase(t *testing.T) { // WAL-03
	store := newTestStore(t)
	if err := store.StoreChat("chat@g.us", "Chat", time.Now()); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"store/messages.db-wal", "store/messages.db-shm"} {
		if _, err := os.Stat(name); err != nil {
			t.Fatalf("%s should exist while the database is open: %v", name, err)
		}
	}

	if _, err := hardenStore("store"); err != nil {
		t.Fatal(err)
	}

	for _, name := range []string{"store/messages.db", "store/messages.db-wal", "store/messages.db-shm"} {
		if got := mode(t, name); got != privateFileMode {
			t.Errorf("%s mode = %v, want %v", name, got, privateFileMode)
		}
	}
}

func TestEncryptedWALFileHoldsNoReadableText(t *testing.T) { // WAL-04
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	store.StoreChat("chat@g.us", "Secret Chat", time.Now())
	if err := store.StoreMessage("M1", "chat@g.us", "5511999990000", secretMessage, time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}

	// the new message is still in the WAL (not checkpointed): that file is where the text would leak
	wal, err := os.ReadFile("store/messages.db-wal")
	if err != nil || len(wal) == 0 {
		t.Fatalf("expected the new message to sit in the WAL: %v, %d bytes", err, len(wal))
	}
	for _, needle := range []string{"secret message", "contract", "Secret Chat", "5511999990000"} {
		if strings.Contains(string(wal), needle) {
			t.Errorf("the WAL file holds %q in plain text", needle)
		}
	}
}

func TestACommittedWriteSurvivesAReopenWithoutAFullClose(t *testing.T) { // WAL-05
	// synchronous=NORMAL may lose the last transactions on a power cut, never an acknowledged one on a plain
	// process exit; the WAL is replayed when the database is opened again
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	store.StoreChat("chat@g.us", "Chat", time.Now())
	if err := store.StoreMessage("M1", "chat@g.us", "alice", "kept", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}
	// no store.Close(): the connection is dropped as a killed process would

	again, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer again.Close()
	var content string
	if err := again.db.QueryRow("SELECT content FROM messages WHERE id = 'M1'").Scan(&content); err != nil || content != "kept" {
		t.Errorf("after reopening: content %q, err %v, want kept", content, err)
	}
}
