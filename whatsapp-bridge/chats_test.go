package main

import (
	"database/sql"
	"testing"
	"time"
)

func chatState(t *testing.T, store *MessageStore, jid string) (name string, last sql.NullTime) {
	t.Helper()
	if err := store.db.QueryRow("SELECT name, last_message_time FROM chats WHERE jid = ?", jid).Scan(&name, &last); err != nil {
		t.Fatal(err)
	}
	return
}

var brt = time.FixedZone("BRT", -3*3600)

func TestStoreChatNeverMovesTimeBackwards(t *testing.T) { // CHT-01, CHT-02, CHT-03
	store := newTestStore(t)
	t0 := time.Date(2026, 10, 21, 12, 0, 0, 0, brt)

	if err := store.StoreChat("c@g.us", "Nome 1", t0); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreChat("c@g.us", "Nome 2", t0.Add(-48*time.Hour)); err != nil { // history sync antigo
		t.Fatal(err)
	}
	name, last := chatState(t, store, "c@g.us")
	if !last.Time.Equal(t0) {
		t.Errorf("an older time overwrote the stored one: %v, want %v", last.Time, t0)
	}
	if name != "Nome 2" {
		t.Errorf("name = %q, want it updated even when the time is kept", name)
	}

	newer := t0.Add(5 * time.Minute)
	store.StoreChat("c@g.us", "Nome 2", newer)
	if _, last := chatState(t, store, "c@g.us"); !last.Time.Equal(newer) {
		t.Errorf("a newer time must advance: %v, want %v", last.Time, newer)
	}
}

func TestStoreChatComparesInstantsAcrossTimeZones(t *testing.T) { // CHT-01
	store := newTestStore(t)
	local := time.Date(2026, 10, 21, 11, 0, 0, 0, brt) // 14:00 UTC
	store.StoreChat("c@g.us", "N", local)

	// 13:00 UTC é mais antigo, mas como texto "13:00:00+00:00" é maior que "11:00:00-03:00"
	store.StoreChat("c@g.us", "N", time.Date(2026, 10, 21, 13, 0, 0, 0, time.UTC))
	if _, last := chatState(t, store, "c@g.us"); !last.Time.Equal(local) {
		t.Errorf("time regressed across zones: %v, want %v", last.Time, local)
	}

	later := time.Date(2026, 10, 21, 15, 0, 0, 0, time.UTC) // mais novo que 14:00 UTC
	store.StoreChat("c@g.us", "N", later)
	if _, last := chatState(t, store, "c@g.us"); !last.Time.Equal(later) {
		t.Errorf("a later instant in another zone must advance: %v, want %v", last.Time, later)
	}
}

func TestStoreChatHandlesFractionalSeconds(t *testing.T) { // CHT-01
	store := newTestStore(t)
	a := time.Date(2026, 10, 21, 11, 0, 0, 900_000_000, brt)
	store.StoreChat("c@g.us", "N", a)
	store.StoreChat("c@g.us", "N", a.Add(-500*time.Millisecond))
	if _, last := chatState(t, store, "c@g.us"); !last.Time.Equal(a) {
		t.Errorf("time = %v, want %v", last.Time, a)
	}
	b := a.Add(2 * time.Second)
	store.StoreChat("c@g.us", "N", b)
	if _, last := chatState(t, store, "c@g.us"); !last.Time.Equal(b) {
		t.Errorf("time = %v, want %v", last.Time, b)
	}
}

func TestTouchChatNeverMovesTimeBackwards(t *testing.T) { // CHT-01 (envio pela API)
	store := newTestStore(t)
	t0 := time.Date(2026, 10, 21, 12, 0, 0, 0, brt)
	store.StoreChat("c@g.us", "Nome", t0)
	store.TouchChat("c@g.us", t0.Add(-time.Hour))
	if _, last := chatState(t, store, "c@g.us"); !last.Time.Equal(t0) {
		t.Errorf("TouchChat moved the time back: %v", last.Time)
	}
}

func seedChatWithMessage(t *testing.T, store *MessageStore, jid string, chatTime, msgTime time.Time) {
	t.Helper()
	if _, err := store.db.Exec("INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)", jid, jid, chatTime); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreMessage("m-"+jid, jid, "alice", "oi", msgTime, false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}
}

func TestRepairChatTimes(t *testing.T) { // CHT-04, CHT-05, CHT-06
	store := newTestStore(t)
	base := time.Date(2026, 10, 21, 12, 0, 0, 0, brt)

	seedChatWithMessage(t, store, "stale@g.us", base.Add(-60*24*time.Hour), base)                     // defasado
	seedChatWithMessage(t, store, "phantom@g.us", base.Add(time.Hour), base)                          // horário além da última mensagem
	seedChatWithMessage(t, store, "equal@g.us", base, base)                                           // igual
	seedChatWithMessage(t, store, "zone@g.us", time.Date(2026, 10, 21, 14, 30, 0, 0, time.UTC), base) // 14:30Z = 11:30 BRT, mensagem 15:00Z
	store.db.Exec("INSERT INTO chats (jid, name, last_message_time) VALUES ('empty@g.us', 'x', ?)", base.Add(-24*time.Hour))
	store.db.Exec("INSERT INTO chats (jid, name, last_message_time) VALUES ('nulltime@g.us', 'x', NULL)")
	store.StoreMessage("m-null", "nulltime@g.us", "bob", "oi", base, false, "", "", "", nil, nil, nil, 0)

	n, err := store.RepairChatTimes()
	if err != nil {
		t.Fatal(err)
	}
	if n != 3 {
		t.Errorf("repaired %d chats, want 3 (stale, zone, nulltime)", n)
	}

	for jid, want := range map[string]time.Time{
		"stale@g.us":    base,
		"zone@g.us":     base,
		"nulltime@g.us": base,
		"phantom@g.us":  base.Add(time.Hour), // nunca recua
		"equal@g.us":    base,
		"empty@g.us":    base.Add(-24 * time.Hour), // sem mensagens: intacto
	} {
		if _, last := chatState(t, store, jid); !last.Time.Equal(want) {
			t.Errorf("%s time = %v, want %v", jid, last.Time, want)
		}
	}

	if n, err := store.RepairChatTimes(); err != nil || n != 0 {
		t.Errorf("second run = (%d, %v), want (0, nil): the repair must be idempotent", n, err)
	}
}

func TestMessagesIndexIsCreatedIdempotently(t *testing.T) { // CHT-06
	t.Chdir(t.TempDir())
	for i := 0; i < 2; i++ {
		store, err := NewMessageStore()
		if err != nil {
			t.Fatalf("open #%d: %v", i+1, err)
		}
		var n int
		store.db.QueryRow("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='idx_messages_chat_ts'").Scan(&n)
		store.Close()
		if n != 1 {
			t.Fatalf("open #%d: idx_messages_chat_ts count = %d, want 1", i+1, n)
		}
	}
}
