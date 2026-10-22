package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
)

func TestLoadRetentionDays(t *testing.T) { // RET-01
	for v, want := range map[string]int{"": 0, "abc": 0, "0": 0, "-5": 0, "1.5": 0, "30": 30, " 7 ": 7} {
		if got := loadRetentionDays(env(map[string]string{"WHATSAPP_MEDIA_RETENTION_DAYS": v})); got != want {
			t.Errorf("RETENTION_DAYS=%q -> %d, want %d", v, got, want)
		}
	}
}

// seedDownloaded stores a media message of the given age with its file saved on disk.
func seedDownloaded(t *testing.T, store *MessageStore, id string, age time.Duration, now time.Time) string {
	t.Helper()
	ts := now.Add(-age)
	if err := store.StoreChat("chat@g.us", "Chat", ts); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreMessage(id, "chat@g.us", "alice", "legenda", ts, false, "image", id+".jpg", "", nil, nil, nil, 5); err != nil {
		t.Fatal(err)
	}
	_, abs, err := mediaFilePath("chat@g.us", id, id+".jpg")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(abs, []byte("dados"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := store.SetLocalPath(id, "chat@g.us", abs); err != nil {
		t.Fatal(err)
	}
	return abs
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

func TestCleanupDeletesOnlyExpiredMedia(t *testing.T) { // RET-02, RET-03, RET-06
	store := newTestStore(t)
	now := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	old := seedDownloaded(t, store, "OLD", 31*24*time.Hour, now)
	edge := seedDownloaded(t, store, "EDGE", 30*24*time.Hour, now)
	fresh := seedDownloaded(t, store, "FRESH", 2*24*time.Hour, now)

	n, err := store.CleanupExpiredMedia(now, 30)
	if err != nil || n != 1 {
		t.Fatalf("CleanupExpiredMedia = (%d, %v), want (1, nil)", n, err)
	}

	if exists(old) || exists(filepath.Dir(old)) {
		t.Error("expired file (and its now empty directory) must be removed")
	}
	if !exists(edge) || !exists(fresh) {
		t.Error("messages that are 30 days old or newer must keep their files")
	}
	if localPathOf(t, store, "OLD") != "" {
		t.Error("expired message must have an empty local_path")
	}
	if localPathOf(t, store, "FRESH") != fresh || localPathOf(t, store, "EDGE") != edge {
		t.Error("kept messages must keep local_path")
	}
	if countMessages(t, store) != 3 {
		t.Error("message rows must be kept")
	}
}

func TestCleanupNeverDeletesOutsideStore(t *testing.T) { // RET-04
	store := newTestStore(t)
	now := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	victim := filepath.Join(t.TempDir(), "importante.txt")
	if err := os.WriteFile(victim, []byte("não apague"), 0o644); err != nil {
		t.Fatal(err)
	}
	seedDownloaded(t, store, "OLD", 90*24*time.Hour, now)
	if err := store.SetLocalPath("OLD", "chat@g.us", victim); err != nil {
		t.Fatal(err)
	}

	if _, err := store.CleanupExpiredMedia(now, 30); err != nil {
		t.Fatal(err)
	}

	if !exists(victim) {
		t.Fatal("file outside store/ was deleted")
	}
	if localPathOf(t, store, "OLD") != victim {
		t.Error("local_path of a path outside store/ must stay unchanged")
	}
}

func TestCleanupClearsLocalPathOfMissingFile(t *testing.T) { // RET-05
	store := newTestStore(t)
	now := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	gone := seedDownloaded(t, store, "OLD", 90*24*time.Hour, now)
	os.Remove(gone)

	if _, err := store.CleanupExpiredMedia(now, 30); err != nil {
		t.Fatal(err)
	}
	if localPathOf(t, store, "OLD") != "" {
		t.Error("local_path of a missing expired file must be cleared")
	}
}

func TestCleanupKeepsDirectoryWithOtherFiles(t *testing.T) { // RET-06 (diretório não vazio)
	store := newTestStore(t)
	now := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	old := seedDownloaded(t, store, "OLD", 90*24*time.Hour, now)
	other := filepath.Join(filepath.Dir(old), "outro.txt")
	os.WriteFile(other, []byte("x"), 0o644)

	if _, err := store.CleanupExpiredMedia(now, 30); err != nil {
		t.Fatal(err)
	}
	if !exists(other) {
		t.Error("a non-empty directory must be kept with its other files")
	}
}

func TestMaybeAutoDownloadSkipsOwnMessages(t *testing.T) { // RET-07
	store := newTestStore(t)
	called := make(chan struct{}, 1)
	dl := func(whatsmeow.File) error { called <- struct{}{}; return nil }

	maybeAutoDownload(store, loadAutoDownloadConfig(env(nil)), newDownloadLimiter(1), dl, nil, true,
		"M1", "chat@g.us", "image", "f.jpg", 10, nil)

	select {
	case <-called:
		t.Error("own media must not be auto downloaded")
	case <-time.After(100 * time.Millisecond):
	}
}
