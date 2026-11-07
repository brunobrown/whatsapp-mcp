package main

import (
	"database/sql"
	"fmt"
	"os"
	"sync"
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
		INSERT INTO messages (id, chat_jid, sender, content) VALUES ('OLD', 'chat@g.us', 'alice', 'old');`)
	if err != nil {
		t.Fatal(err)
	}
	legacy.Close()

	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	var content string
	if err := store.db.QueryRow("SELECT content FROM messages WHERE id='OLD'").Scan(&content); err != nil || content != "old" {
		t.Fatalf("existing message lost: %q %v", content, err)
	}
	if err := store.UpsertIdentity("111", "5581", "Ana", "ana p"); err != nil {
		t.Fatal(err)
	}
	store.Close()

	store, err = NewMessageStore() // second open: idempotent
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
	if err := store.UpsertIdentity("100000000000001", "551188880101", "Alex Morgan", "Alex"); err != nil {
		t.Fatal(err)
	}
	got := identityRows(t, store)
	want := Identity{LID: "100000000000001", Phone: "551188880101", FullName: "Alex Morgan", PushName: "Alex"}
	if len(got) != 1 || got[0] != want {
		t.Errorf("rows = %+v, want [%+v]", got, want)
	}
}

func TestUpsertIdentityUpdatesKnownPersonByEitherId(t *testing.T) { // IDN-04
	store := newTestStore(t)
	store.UpsertIdentity("111", "", "", "lid only")
	store.UpsertIdentity("", "5581", "", "") // phone only, a different person for now
	store.UpsertIdentity("111", "", "New Name", "")
	store.UpsertIdentity("", "5581", "", "phone push")

	got := identityRows(t, store)
	if len(got) != 2 {
		t.Fatalf("rows = %+v, want 2 distinct people", got)
	}
	if got[0].LID != "111" || got[0].FullName != "New Name" || got[0].PushName != "lid only" {
		t.Errorf("lid row = %+v", got[0])
	}
	if got[1].Phone != "5581" || got[1].PushName != "phone push" {
		t.Errorf("phone row = %+v", got[1])
	}
}

func TestUpsertIdentityMergesSeparateRows(t *testing.T) { // IDN-05
	store := newTestStore(t)
	store.UpsertIdentity("111", "", "", "lid push")
	store.UpsertIdentity("", "5581", "Address book name", "")

	if err := store.UpsertIdentity("111", "5581", "", ""); err != nil { // now we know they are the same person
		t.Fatal(err)
	}

	got := identityRows(t, store)
	want := Identity{LID: "111", Phone: "5581", FullName: "Address book name", PushName: "lid push"}
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
	if err := store.UpsertIdentity("", "", "Nobody", "x"); err != nil {
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
		{Identity{FullName: "Contacts", PushName: "Push"}, "Contacts"},
		{Identity{PushName: "Push"}, "Push"},
		{Identity{}, ""},
	} {
		if got := c.id.DisplayName(); got != c.want {
			t.Errorf("DisplayName(%+v) = %q, want %q", c.id, got, c.want)
		}
	}
}

func TestUpsertIdentityMergeTakesNamesFromBothRows(t *testing.T) { // IDN-05 (names coming from the other row)
	store := newTestStore(t)
	store.UpsertIdentity("111", "", "Address book name", "") // the LID row only has the address book name
	store.UpsertIdentity("", "5581", "", "phone push")

	store.UpsertIdentity("111", "5581", "", "")

	got := identityRows(t, store)
	want := Identity{LID: "111", Phone: "5581", FullName: "Address book name", PushName: "phone push"}
	if len(got) != 1 || got[0] != want {
		t.Errorf("rows = %+v, want [%+v]", got, want)
	}
}

func TestIdentityTableRejectsDuplicateIds(t *testing.T) { // IDN-03, IDN-04 (safety net in the database)
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

func TestConcurrentUpsertsDoNotFail(t *testing.T) { // events, sync and REST write at the same time
	store := newTestStore(t)
	const workers, perWorker = 8, 40
	errs := make(chan error, workers*perWorker)
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < perWorker; i++ {
				lid := fmt.Sprintf("lid%d", i) // the workers compete for the same people
				phone := fmt.Sprintf("55%d", i)
				if err := store.UpsertIdentity(lid, "", "", fmt.Sprintf("push%d", w)); err != nil {
					errs <- err
				}
				if err := store.UpsertIdentity("", phone, fmt.Sprintf("Name%d", i), ""); err != nil {
					errs <- err
				}
				if err := store.UpsertIdentity(lid, phone, "", ""); err != nil {
					errs <- err
				}
			}
		}(w)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Errorf("concurrent upsert failed: %v", err)
	}
	// each person ends up in a single row, with LID, phone and name
	rows := identityRows(t, store)
	if len(rows) != perWorker {
		t.Fatalf("rows = %d, want %d (one per person)", len(rows), perWorker)
	}
	for _, r := range rows {
		if r.LID == "" || r.Phone == "" || r.FullName == "" {
			t.Errorf("incomplete row after concurrent merge: %+v", r)
		}
	}
}
