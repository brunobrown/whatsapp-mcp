package main

import (
	"database/sql"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
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

	only := loadAutoDownloadConfig(env(map[string]string{"WHATSAPP_AUTO_DOWNLOAD_TYPES": "audio"}))
	if only.allows("image", 10) || only.allows("document", 10) || !only.allows("audio", 10) {
		t.Error("TYPES=audio must allow only audio, even though image is a default")
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

func okDownload(data string) func(whatsmeow.File) error {
	return func(f whatsmeow.File) error {
		_, err := f.Write([]byte(data))
		return err
	}
}

func filesUnder(t *testing.T, root string) []string {
	t.Helper()
	var files []string
	filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() && !strings.HasPrefix(p, "store/messages.db") {
			files = append(files, p)
		}
		return nil
	})
	return files
}

func TestAutoDownloadSavesFileAndRecordsPath(t *testing.T) { // AUTODL-07
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "image_M1.jpg")

	path, err := autoDownload(store, loadAutoDownloadConfig(env(nil)), okDownload("PIXELS"), "M1", "chat@g.us", "image", "image_M1.jpg", 6)
	if err != nil {
		t.Fatal(err)
	}
	want, _ := filepath.Abs("store/chat@g.us/M1/image_M1.jpg")
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
	dir, _ := filepath.Abs("store/chat@g.us/M1")
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
			dl := func(whatsmeow.File) error { called = true; return nil }

			path, err := autoDownload(store, loadAutoDownloadConfig(env(c.env)), dl, "M1", "chat@g.us", c.mediaType, "f.bin", c.length)

			if path != "" || err != nil || called {
				t.Errorf("path=%q err=%v called=%v, want skip", path, err, called)
			}
			if files := filesUnder(t, "store"); len(files) != 0 {
				t.Errorf("files saved: %v", files)
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
	fail := func(f whatsmeow.File) error {
		f.Write([]byte("meio arquivo")) // falha no meio do download
		return errors.New("403")
	}

	path, err := autoDownload(store, loadAutoDownloadConfig(env(nil)), fail, "M1", "chat@g.us", "image", "image_M1.jpg", 10)

	if err == nil || path != "" {
		t.Errorf("path=%q err=%v, want error", path, err)
	}
	if localPathOf(t, store, "M1") != "" || countMessages(t, store) != 1 {
		t.Error("message must stay with empty local_path")
	}
	if files := filesUnder(t, "store"); len(files) != 0 {
		t.Errorf("no file may be left behind on failure, found %v", files)
	}
}

func TestDownloadMediaFindsFileDownloadedBeforeThisVersion(t *testing.T) { // AUTODL-10 (caminho antigo)
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

func TestDownloadMediaReturnsAutoDownloadedFileWithoutClient(t *testing.T) { // AUTODL-10, AUTODL-11
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "image_M1.jpg")
	path, err := autoDownload(store, loadAutoDownloadConfig(env(nil)), okDownload("já baixado"), "M1", "chat@g.us", "image", "image_M1.jpg", 10)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SetLocalPath("M1", "chat@g.us", ""); err != nil { // força a busca pelo caminho novo
		t.Fatal(err)
	}

	ok, _, _, got, err := downloadMedia(nil, store, "M1", "chat@g.us")
	if err != nil || !ok || got != path {
		t.Fatalf("downloadMedia = (%v, %q, %v), want %q", ok, got, err, path)
	}
	if localPathOf(t, store, "M1") != path {
		t.Error("local_path must be recorded again")
	}
}

func TestSameFilenameInTwoMessagesStaysSeparate(t *testing.T) { // AUTODL-14
	store := newTestStore(t)
	seedMedia(t, store, "REAL", "document", "proposta.pdf")
	seedMedia(t, store, "FAKE", "document", "proposta.pdf")
	cfg := loadAutoDownloadConfig(env(nil))

	fakePath, err := autoDownload(store, cfg, okDownload("CONTEUDO DO ATACANTE"), "FAKE", "chat@g.us", "document", "proposta.pdf", 20)
	if err != nil {
		t.Fatal(err)
	}
	realPath, err := autoDownload(store, cfg, okDownload("CONTEUDO REAL"), "REAL", "chat@g.us", "document", "proposta.pdf", 13)
	if err != nil {
		t.Fatal(err)
	}

	if fakePath == realPath {
		t.Fatal("two messages share one file")
	}
	if data, _ := os.ReadFile(realPath); string(data) != "CONTEUDO REAL" {
		t.Errorf("real message file = %q", data)
	}
	if localPathOf(t, store, "REAL") != realPath || localPathOf(t, store, "FAKE") != fakePath {
		t.Error("each message must point at its own file")
	}
}

func TestAutoDownloadAbortsWhenBytesExceedLimit(t *testing.T) { // AUTODL-15
	store := newTestStore(t)
	seedMedia(t, store, "M1", "document", "big.bin")
	cfg := AutoDownloadConfig{Enabled: true, MaxBytes: 1000, Types: map[string]bool{"document": true}}
	var wrote int
	flood := func(f whatsmeow.File) error {
		chunk := make([]byte, 500)
		for i := 0; i < 100; i++ { // 50 KB contra um limite de 1000 bytes
			if _, err := f.Write(chunk); err != nil {
				return err
			}
			wrote += len(chunk)
		}
		return nil
	}

	// o remetente declara 10 bytes, bem abaixo do limite, mas o arquivo real é grande
	path, err := autoDownload(store, cfg, flood, "M1", "chat@g.us", "document", "big.bin", 10)

	if err == nil || path != "" {
		t.Fatalf("path=%q err=%v, want abort", path, err)
	}
	if wrote > 1000+64 {
		t.Errorf("download kept writing past the limit: %d bytes", wrote)
	}
	if files := filesUnder(t, "store"); len(files) != 0 {
		t.Errorf("files left behind: %v", files)
	}
	if localPathOf(t, store, "M1") != "" {
		t.Error("local_path must stay empty")
	}
}

func TestAutoDownloadRejectsFinalFileOverLimit(t *testing.T) { // AUTODL-15 (arquivo que cresce na decifragem)
	store := newTestStore(t)
	seedMedia(t, store, "M1", "document", "big.bin")
	cfg := AutoDownloadConfig{Enabled: true, MaxBytes: 1000, Types: map[string]bool{"document": true}}
	justOver := func(f whatsmeow.File) error { // dentro da folga de 64 bytes, mas acima do limite
		_, err := f.Write(make([]byte, 1020))
		return err
	}
	if path, err := autoDownload(store, cfg, justOver, "M1", "chat@g.us", "document", "big.bin", 10); err == nil || path != "" {
		t.Errorf("path=%q err=%v, want rejection", path, err)
	}
	if files := filesUnder(t, "store"); len(files) != 0 {
		t.Errorf("files left behind: %v", files)
	}
}

func TestAutoDownloadNeverLeavesFinalFileOnCrash(t *testing.T) { // AUTODL-16
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "image_M1.jpg")
	_, finalAbs, err := mediaFilePath("chat@g.us", "M1", "image_M1.jpg")
	if err != nil {
		t.Fatal(err)
	}
	observed := false
	inspect := func(f whatsmeow.File) error {
		f.Write([]byte("metade"))
		// no meio da escrita o caminho final ainda não pode existir
		if _, statErr := os.Stat(finalAbs); statErr == nil {
			t.Error("final path exists while the download is still in flight")
		}
		observed = true
		return nil
	}
	if _, err := autoDownload(store, loadAutoDownloadConfig(env(nil)), inspect, "M1", "chat@g.us", "image", "image_M1.jpg", 6); err != nil {
		t.Fatal(err)
	}
	if !observed {
		t.Fatal("download function not called")
	}
	if _, err := os.Stat(finalAbs); err != nil {
		t.Errorf("final file missing after success: %v", err)
	}
}

func TestAutoDownloadReusesExistingFileWithoutDownloading(t *testing.T) { // AUTODL-07 (idempotência)
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "image_M1.jpg")
	cfg := loadAutoDownloadConfig(env(nil))
	first, err := autoDownload(store, cfg, okDownload("v1"), "M1", "chat@g.us", "image", "image_M1.jpg", 2)
	if err != nil {
		t.Fatal(err)
	}
	store.SetLocalPath("M1", "chat@g.us", "")

	neverCalled := func(whatsmeow.File) error { t.Error("downloaded again"); return nil }
	second, err := autoDownload(store, cfg, neverCalled, "M1", "chat@g.us", "image", "image_M1.jpg", 2)
	if err != nil || second != first {
		t.Fatalf("second = (%q, %v), want %q", second, err, first)
	}
	if localPathOf(t, store, "M1") != first {
		t.Error("local_path must be recorded again")
	}
}

func TestStoreDownloadedMediaRecordsLocalPath(t *testing.T) { // AUTODL-11 (download manual)
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "image_M1.jpg")
	_, abs, err := mediaFilePath("chat@g.us", "M1", "image_M1.jpg")
	if err != nil {
		t.Fatal(err)
	}
	if err := storeDownloadedMedia(store, "M1", "chat@g.us", abs, []byte("dados")); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(abs); string(data) != "dados" {
		t.Errorf("file = %q", data)
	}
	if localPathOf(t, store, "M1") != abs {
		t.Error("local_path not recorded")
	}
	if files := filesUnder(t, "store"); len(files) != 1 {
		t.Errorf("files = %v, want only the final one", files)
	}
}

func TestMaybeAutoDownloadSkipsWhenStoreFailed(t *testing.T) { // hook de handleMessage
	store := newTestStore(t)
	called := make(chan struct{}, 1)
	dl := func(whatsmeow.File) error { called <- struct{}{}; return nil }
	maybeAutoDownload(store, loadAutoDownloadConfig(env(nil)), newDownloadLimiter(1), dl, errors.New("db down"), false,
		"M1", "chat@g.us", "image", "f.jpg", 10, nil)
	select {
	case <-called:
		t.Error("download started although the message was not stored")
	case <-time.After(100 * time.Millisecond):
	}
}

func TestMaybeAutoDownloadUsesLimiterAndRuns(t *testing.T) { // hook de handleMessage, AUTODL-12
	store := newTestStore(t)
	for _, id := range []string{"A", "B", "C"} {
		seedMedia(t, store, id, "image", "f_"+id+".jpg")
	}
	var running, peak int32
	var wg sync.WaitGroup
	dl := func(f whatsmeow.File) error {
		defer wg.Done()
		n := atomic.AddInt32(&running, 1)
		if n > atomic.LoadInt32(&peak) {
			atomic.StoreInt32(&peak, n)
		}
		time.Sleep(30 * time.Millisecond)
		atomic.AddInt32(&running, -1)
		_, err := f.Write([]byte("x"))
		return err
	}
	limiter := newDownloadLimiter(1)
	for _, id := range []string{"A", "B", "C"} {
		wg.Add(1)
		maybeAutoDownload(store, loadAutoDownloadConfig(env(nil)), limiter, dl, nil, false, id, "chat@g.us", "image", "f_"+id+".jpg", 1, nil)
	}
	wg.Wait()
	if peak != 1 {
		t.Errorf("peak = %d with a limiter of 1: the hook bypasses the limiter", peak)
	}
	time.Sleep(50 * time.Millisecond)
	for _, id := range []string{"A", "B", "C"} {
		if localPathOf(t, store, id) == "" {
			t.Errorf("message %s has no local_path", id)
		}
	}
}

func TestProductionLimiterAllowsFourDownloads(t *testing.T) { // AUTODL-12
	if got := cap(autoDownloadLimiter); got != 4 {
		t.Errorf("production limiter size = %d, want 4", got)
	}
}
