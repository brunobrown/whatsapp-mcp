package main

import (
	"database/sql"
	"os"
	"testing"
)

func identityRows(t *testing.T, store *MessageStore) []Identity {
	t.Helper()
	rows, err := store.db.Query("SELECT COALESCE(lid,''), COALESCE(phone,''), COALESCE(full_name,''), COALESCE(push_name,'') FROM contacts_identity ORDER BY id")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out []Identity
	for rows.Next() {
		var i Identity
		if err := rows.Scan(&i.LID, &i.Phone, &i.FullName, &i.PushName); err != nil {
			t.Fatal(err)
		}
		out = append(out, i)
	}
	return out
}

func TestMigrationCreatesIdentityTableAndKeepsData(t *testing.T) { // IDN-01, IDN-02
	t.Chdir(t.TempDir())
	os.MkdirAll("store", 0o755)
	legacy, err := sql.Open("sqlite3", "file:store/messages.db")
	if err != nil {
		t.Fatal(err)
	}
	_, err = legacy.Exec(`
		CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
		CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP,
			is_from_me BOOLEAN, media_type TEXT, filename TEXT, url TEXT, media_key BLOB,
			file_sha256 BLOB, file_enc_sha256 BLOB, file_length INTEGER, PRIMARY KEY (id, chat_jid));
		INSERT INTO chats VALUES ('chat@g.us', 'Chat', '2026-10-20 10:00:00');
		INSERT INTO messages (id, chat_jid, sender, content) VALUES ('OLD', 'chat@g.us', 'alice', 'antiga');`)
	if err != nil {
		t.Fatal(err)
	}
	legacy.Close()

	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	var content string
	if err := store.db.QueryRow("SELECT content FROM messages WHERE id='OLD'").Scan(&content); err != nil || content != "antiga" {
		t.Fatalf("existing message lost: %q %v", content, err)
	}
	if err := store.UpsertIdentity("111", "5581", "Ana", "ana p"); err != nil {
		t.Fatal(err)
	}
	store.Close()

	store, err = NewMessageStore() // segunda abertura: idempotente
	if err != nil {
		t.Fatalf("second open: %v", err)
	}
	defer store.Close()
	got := identityRows(t, store)
	if len(got) != 1 || got[0] != (Identity{LID: "111", Phone: "5581", FullName: "Ana", PushName: "ana p"}) {
		t.Errorf("rows after reopen = %+v", got)
	}
}

func TestUpsertIdentityStoresEverythingInOneRow(t *testing.T) { // IDN-03, IDN-07
	store := newTestStore(t)
	if err := store.UpsertIdentity("160889625940144", "555181993271", "Teilor Dall Bello", "Teilor"); err != nil {
		t.Fatal(err)
	}
	got := identityRows(t, store)
	want := Identity{LID: "160889625940144", Phone: "555181993271", FullName: "Teilor Dall Bello", PushName: "Teilor"}
	if len(got) != 1 || got[0] != want {
		t.Errorf("rows = %+v, want [%+v]", got, want)
	}
}

func TestUpsertIdentityUpdatesKnownPersonByEitherId(t *testing.T) { // IDN-04
	store := newTestStore(t)
	store.UpsertIdentity("111", "", "", "só lid")
	store.UpsertIdentity("", "5581", "", "") // só telefone, outra pessoa por enquanto
	store.UpsertIdentity("111", "", "Nome Novo", "")
	store.UpsertIdentity("", "5581", "", "push do telefone")

	got := identityRows(t, store)
	if len(got) != 2 {
		t.Fatalf("rows = %+v, want 2 distinct people", got)
	}
	if got[0].LID != "111" || got[0].FullName != "Nome Novo" || got[0].PushName != "só lid" {
		t.Errorf("lid row = %+v", got[0])
	}
	if got[1].Phone != "5581" || got[1].PushName != "push do telefone" {
		t.Errorf("phone row = %+v", got[1])
	}
}

func TestUpsertIdentityMergesSeparateRows(t *testing.T) { // IDN-05
	store := newTestStore(t)
	store.UpsertIdentity("111", "", "", "push da lid")
	store.UpsertIdentity("", "5581", "Nome da agenda", "")

	if err := store.UpsertIdentity("111", "5581", "", ""); err != nil { // agora sabemos que são a mesma pessoa
		t.Fatal(err)
	}

	got := identityRows(t, store)
	want := Identity{LID: "111", Phone: "5581", FullName: "Nome da agenda", PushName: "push da lid"}
	if len(got) != 1 || got[0] != want {
		t.Errorf("rows = %+v, want [%+v]", got, want)
	}
}

func TestUpsertIdentityKeepsNameWhenNewOneIsEmpty(t *testing.T) { // IDN-06
	store := newTestStore(t)
	store.UpsertIdentity("111", "5581", "Ana", "ana p")
	store.UpsertIdentity("111", "5581", "", "")

	got := identityRows(t, store)
	if len(got) != 1 || got[0].FullName != "Ana" || got[0].PushName != "ana p" {
		t.Errorf("rows = %+v", got)
	}
}

func TestUpsertIdentityIgnoresRecordsWithoutIds(t *testing.T) {
	store := newTestStore(t)
	if err := store.UpsertIdentity("", "", "Ninguém", "x"); err != nil {
		t.Fatal(err)
	}
	if got := identityRows(t, store); len(got) != 0 {
		t.Errorf("rows = %+v", got)
	}
}

func TestIdentityDisplayNamePrefersAddressBook(t *testing.T) { // IDN-07
	for _, c := range []struct {
		id   Identity
		want string
	}{
		{Identity{FullName: "Agenda", PushName: "Push"}, "Agenda"},
		{Identity{PushName: "Push"}, "Push"},
		{Identity{}, ""},
	} {
		if got := c.id.DisplayName(); got != c.want {
			t.Errorf("DisplayName(%+v) = %q, want %q", c.id, got, c.want)
		}
	}
}

func TestUpsertIdentityMergeTakesNamesFromBothRows(t *testing.T) { // IDN-05 (nomes vindos da outra linha)
	store := newTestStore(t)
	store.UpsertIdentity("111", "", "Nome da agenda", "") // linha da LID só tem o nome da agenda
	store.UpsertIdentity("", "5581", "", "push do telefone")

	store.UpsertIdentity("111", "5581", "", "")

	got := identityRows(t, store)
	want := Identity{LID: "111", Phone: "5581", FullName: "Nome da agenda", PushName: "push do telefone"}
	if len(got) != 1 || got[0] != want {
		t.Errorf("rows = %+v, want [%+v]", got, want)
	}
}

func TestIdentityTableRejectsDuplicateIds(t *testing.T) { // IDN-03, IDN-04 (rede de segurança no banco)
	store := newTestStore(t)
	store.UpsertIdentity("111", "5581", "Ana", "")
	if _, err := store.db.Exec("INSERT INTO contacts_identity (lid) VALUES ('111')"); err == nil {
		t.Error("a second row with the same LID must be rejected")
	}
	if _, err := store.db.Exec("INSERT INTO contacts_identity (phone) VALUES ('5581')"); err == nil {
		t.Error("a second row with the same phone must be rejected")
	}
	if _, err := store.db.Exec("INSERT INTO contacts_identity (lid) VALUES (NULL), (NULL)"); err != nil {
		t.Errorf("rows without LID must not collide with each other: %v", err)
	}
}
