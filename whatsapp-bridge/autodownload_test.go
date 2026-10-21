package main

import (
	"database/sql"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func env(vars map[string]string) func(string) string {
	return func(k string) string { return vars[k] }
}

const MiB = 1024 * 1024

func TestAutoDownloadConfigDefaults(t *testing.T) { // AUTODL-03
	cfg := loadAutoDownloadConfig(env(nil))
	for _, c := range []struct {
		mediaType string
		length    uint64
		want      bool
	}{
		{"image", 1000, true},
		{"document", 1000, true},
		{"audio", 1000, true},
		{"video", 1000, false},
		{"image", 25 * MiB, true},
		{"image", 25*MiB + 1, false},
	} {
		if got := cfg.allows(c.mediaType, c.length); got != c.want {
			t.Errorf("allows(%s, %d) = %v, want %v", c.mediaType, c.length, got, c.want)
		}
	}
}

func TestAutoDownloadConfigDisabled(t *testing.T) { // AUTODL-04
	for _, v := range []string{"false", "FALSE", "0"} {
		cfg := loadAutoDownloadConfig(env(map[string]string{"WHATSAPP_AUTO_DOWNLOAD": v}))
		if cfg.allows("image", 1000) {
			t.Errorf("WHATSAPP_AUTO_DOWNLOAD=%s still allows downloads", v)
		}
	}
	for _, v := range []string{"true", "1", "banana", ""} {
		cfg := loadAutoDownloadConfig(env(map[string]string{"WHATSAPP_AUTO_DOWNLOAD": v}))
		if !cfg.allows("image", 1000) {
			t.Errorf("WHATSAPP_AUTO_DOWNLOAD=%q should keep downloads on", v)
		}
	}
}

func TestAutoDownloadConfigMaxMB(t *testing.T) { // AUTODL-05
	cfg := loadAutoDownloadConfig(env(map[string]string{"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": "2"}))
	if !cfg.allows("image", 2*MiB) || cfg.allows("image", 2*MiB+1) {
		t.Error("limit of 2 MiB not applied")
	}
	for _, bad := range []string{"abc", "0", "-3"} {
		cfg := loadAutoDownloadConfig(env(map[string]string{"WHATSAPP_AUTO_DOWNLOAD_MAX_MB": bad}))
		if !cfg.allows("image", 25*MiB) || cfg.allows("image", 25*MiB+1) {
			t.Errorf("invalid value %q must fall back to 25 MiB", bad)
		}
	}
}

func TestAutoDownloadConfigTypes(t *testing.T) { // AUTODL-06
	cfg := loadAutoDownloadConfig(env(map[string]string{"WHATSAPP_AUTO_DOWNLOAD_TYPES": "video, Image"}))
	if !cfg.allows("video", 10) || !cfg.allows("image", 10) {
		t.Error("listed types must be allowed (case and spaces ignored)")
	}
	if cfg.allows("document", 10) || cfg.allows("audio", 10) {
		t.Error("unlisted types must not be allowed")
	}
}

func seedMedia(t *testing.T, store *MessageStore, id, mediaType, filename string) {
	t.Helper()
	if err := store.StoreChat("chat@g.us", "Chat", time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreMessage(id, "chat@g.us", "alice", "", time.Now(), false, mediaType, filename, "https://x", []byte("k"), []byte("s"), []byte("e"), 10); err != nil {
		t.Fatal(err)
	}
}

func localPathOf(t *testing.T, store *MessageStore, id string) string {
	t.Helper()
	var p sql.NullString
	if err := store.db.QueryRow("SELECT local_path FROM messages WHERE id = ? AND chat_jid = 'chat@g.us'", id).Scan(&p); err != nil {
		t.Fatal(err)
	}
	return p.String
}

func okDownload(data string) func() ([]byte, error) {
	return func() ([]byte, error) { return []byte(data), nil }
}

func TestAutoDownloadSavesFileAndRecordsPath(t *testing.T) { // AUTODL-07
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "image_M1.jpg")

	path, err := autoDownload(store, loadAutoDownloadConfig(env(nil)), okDownload("PIXELS"), "M1", "chat@g.us", "image", "image_M1.jpg", 6)
	if err != nil {
		t.Fatal(err)
	}
	want, _ := filepath.Abs("store/chat@g.us/image_M1.jpg")
	if path != want {
		t.Errorf("path = %q, want %q", path, want)
	}
	if data, _ := os.ReadFile(path); string(data) != "PIXELS" {
		t.Errorf("file content = %q", data)
	}
	if got := localPathOf(t, store, "M1"); got != want {
		t.Errorf("local_path = %q, want %q", got, want)
	}
}

func TestAutoDownloadSanitizesFilename(t *testing.T) { // AUTODL-07 (anti path traversal)
	store := newTestStore(t)
	seedMedia(t, store, "M1", "document", "../../evil.sh")

	path, err := autoDownload(store, loadAutoDownloadConfig(env(nil)), okDownload("x"), "M1", "chat@g.us", "document", "../../evil.sh", 1)
	if err != nil {
		t.Fatal(err)
	}
	dir, _ := filepath.Abs("store/chat@g.us")
	if filepath.Dir(path) != dir {
		t.Errorf("file written outside chat dir: %s", path)
	}
}

func TestAutoDownloadSkips(t *testing.T) { // AUTODL-08
	cases := []struct {
		name      string
		mediaType string
		length    uint64
		env       map[string]string
	}{
		{"disabled type", "video", 100, nil},
		{"over limit", "image", 26 * MiB, nil},
		{"unknown length", "image", 0, nil},
		{"globally off", "image", 100, map[string]string{"WHATSAPP_AUTO_DOWNLOAD": "false"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			store := newTestStore(t)
			seedMedia(t, store, "M1", c.mediaType, "f.bin")
			called := false
			dl := func() ([]byte, error) { called = true; return []byte("x"), nil }

			path, err := autoDownload(store, loadAutoDownloadConfig(env(c.env)), dl, "M1", "chat@g.us", c.mediaType, "f.bin", c.length)

			if path != "" || err != nil || called {
				t.Errorf("path=%q err=%v called=%v, want skip", path, err, called)
			}
			if _, statErr := os.Stat("store/chat@g.us/f.bin"); statErr == nil {
				t.Error("file was saved")
			}
			if countMessages(t, store) != 1 {
				t.Error("message must stay stored")
			}
		})
	}
}

func TestAutoDownloadFailureKeepsMessage(t *testing.T) { // AUTODL-09
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "image_M1.jpg")
	fail := func() ([]byte, error) { return nil, errors.New("403") }

	path, err := autoDownload(store, loadAutoDownloadConfig(env(nil)), fail, "M1", "chat@g.us", "image", "image_M1.jpg", 10)

	if err == nil || path != "" {
		t.Errorf("path=%q err=%v, want error", path, err)
	}
	if localPathOf(t, store, "M1") != "" || countMessages(t, store) != 1 {
		t.Error("message must stay with empty local_path")
	}
	if _, statErr := os.Stat("store/chat@g.us/image_M1.jpg"); statErr == nil {
		t.Error("no file may be left behind on failure")
	}
}

func TestDownloadMediaReturnsLocalFileWithoutClient(t *testing.T) { // AUTODL-10, AUTODL-11
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "image_M1.jpg")
	if err := os.MkdirAll("store/chat@g.us", 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile("store/chat@g.us/image_M1.jpg", []byte("already here"), 0o644); err != nil {
		t.Fatal(err)
	}

	// client nil: qualquer tentativa de falar com o WhatsApp dá panic
	ok, mediaType, _, path, err := downloadMedia(nil, store, "M1", "chat@g.us")
	if err != nil || !ok || mediaType != "image" {
		t.Fatalf("downloadMedia = (%v, %q, %v)", ok, mediaType, err)
	}
	want, _ := filepath.Abs("store/chat@g.us/image_M1.jpg")
	if path != want || localPathOf(t, store, "M1") != want {
		t.Errorf("path=%q local_path=%q, want %q", path, localPathOf(t, store, "M1"), want)
	}
}

func TestStoreMessageKeepsLocalPath(t *testing.T) { // AUTODL-13
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "image_M1.jpg")
	if err := store.SetLocalPath("M1", "chat@g.us", "/abs/image_M1.jpg"); err != nil {
		t.Fatal(err)
	}

	// o eco/history sync grava a mesma mensagem de novo
	if err := store.StoreMessage("M1", "chat@g.us", "alice", "legenda", time.Now(), false, "image", "image_M1.jpg", "https://x", []byte("k"), []byte("s"), []byte("e"), 10); err != nil {
		t.Fatal(err)
	}

	if got := localPathOf(t, store, "M1"); got != "/abs/image_M1.jpg" {
		t.Errorf("local_path = %q after re-store", got)
	}
	var content string
	store.db.QueryRow("SELECT content FROM messages WHERE id='M1'").Scan(&content)
	if content != "legenda" || countMessages(t, store) != 1 {
		t.Errorf("content=%q rows=%d: re-store must still update the row", content, countMessages(t, store))
	}
}

func TestMigrationAddsLocalPathAndKeepsData(t *testing.T) { // AUTODL-01, AUTODL-02
	t.Chdir(t.TempDir())
	if err := os.MkdirAll("store", 0o755); err != nil {
		t.Fatal(err)
	}
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
		INSERT INTO messages (id, chat_jid, sender, content, media_type, filename, file_length)
			VALUES ('OLD', 'chat@g.us', 'alice', 'antiga', 'image', 'image_OLD.jpg', 77);`)
	if err != nil {
		t.Fatal(err)
	}
	legacy.Close()

	for i := 0; i < 2; i++ { // a segunda abertura é o caso idempotente
		store, err := NewMessageStore()
		if err != nil {
			t.Fatalf("open #%d: %v", i+1, err)
		}
		var content, filename string
		var length int
		var lp sql.NullString
		err = store.db.QueryRow("SELECT content, filename, file_length, local_path FROM messages WHERE id='OLD'").Scan(&content, &filename, &length, &lp)
		if err != nil {
			t.Fatalf("open #%d: %v", i+1, err)
		}
		if content != "antiga" || filename != "image_OLD.jpg" || length != 77 || lp.Valid {
			t.Errorf("open #%d: row changed: %q %q %d %v", i+1, content, filename, length, lp)
		}
		store.Close()
	}
}

func TestDownloadLimiterCapsConcurrency(t *testing.T) { // AUTODL-12
	limiter := newDownloadLimiter(4)
	var running, peak int32
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go limiter.run(func() {
			defer wg.Done()
			n := atomic.AddInt32(&running, 1)
			for {
				p := atomic.LoadInt32(&peak)
				if n <= p || atomic.CompareAndSwapInt32(&peak, p, n) {
					break
				}
			}
			time.Sleep(15 * time.Millisecond)
			atomic.AddInt32(&running, -1)
		})
	}
	wg.Wait()
	if peak > 4 || peak < 2 {
		t.Errorf("peak concurrency = %d, want between 2 and 4", peak)
	}
}
