package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func mode(t *testing.T, path string) os.FileMode {
	t.Helper()
	info, err := os.Lstat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode().Perm()
}

func skipOnWindows(t *testing.T) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permissions")
	}
}

func openTree(t *testing.T) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "store")
	for _, dir := range []string{"", "chat@g.us", "chat@g.us/MSG1"} {
		if err := os.MkdirAll(filepath.Join(root, dir), 0o755); err != nil {
			t.Fatal(err)
		}
		os.Chmod(filepath.Join(root, dir), 0o755)
	}
	for _, file := range []string{"messages.db", "whatsapp.db", "messages.db.bak-1", "chat@g.us/MSG1/photo.jpg"} {
		if err := os.WriteFile(filepath.Join(root, file), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
		os.Chmod(filepath.Join(root, file), 0o644)
	}
	return root
}

func TestHardenStoreMakesEverythingPrivate(t *testing.T) { // HARD-02
	skipOnWindows(t)
	root := openTree(t)

	result, err := hardenStore(root)

	if err != nil || len(result.Failed) != 0 {
		t.Fatalf("err=%v failed=%v", err, result.Failed)
	}
	if result.Changed != 7 { // 3 diretórios (raiz inclusa) + 4 arquivos
		t.Errorf("Changed = %d, want 7", result.Changed)
	}
	for _, dir := range []string{"", "chat@g.us", "chat@g.us/MSG1"} {
		if got := mode(t, filepath.Join(root, dir)); got != 0o700 {
			t.Errorf("dir %q mode = %v, want 0700", dir, got)
		}
	}
	for _, file := range []string{"messages.db", "whatsapp.db", "messages.db.bak-1", "chat@g.us/MSG1/photo.jpg"} {
		if got := mode(t, filepath.Join(root, file)); got != 0o600 {
			t.Errorf("file %q mode = %v, want 0600", file, got)
		}
	}
}

func TestHardenStoreIsIdempotent(t *testing.T) { // HARD-02
	skipOnWindows(t)
	root := openTree(t)
	hardenStore(root)

	result, err := hardenStore(root)

	if err != nil || result.Changed != 0 || len(result.Failed) != 0 {
		t.Errorf("second run: %+v err=%v", result, err)
	}
}

func TestHardenStoreCreatesAMissingRootPrivate(t *testing.T) { // HARD-02
	skipOnWindows(t)
	root := filepath.Join(t.TempDir(), "a", "store")

	if _, err := hardenStore(root); err != nil {
		t.Fatal(err)
	}

	if got := mode(t, root); got != 0o700 {
		t.Errorf("mode = %v, want 0700", got)
	}
}

func TestHardenStoreNeverFollowsSymlinks(t *testing.T) { // HARD-02
	skipOnWindows(t)
	root := openTree(t)
	outside := filepath.Join(t.TempDir(), "outside.txt")
	os.WriteFile(outside, []byte("x"), 0o644)
	outsideDir := t.TempDir()
	os.Chmod(outsideDir, 0o755)
	os.Symlink(outside, filepath.Join(root, "link-to-file"))
	os.Symlink(outsideDir, filepath.Join(root, "link-to-dir"))

	if _, err := hardenStore(root); err != nil {
		t.Fatal(err)
	}

	if got := mode(t, outside); got != 0o644 {
		t.Errorf("a file outside the store was changed to %v", got)
	}
	if got := mode(t, outsideDir); got != 0o755 {
		t.Errorf("a directory outside the store was changed to %v", got)
	}
}

func TestHardenStoreReportsWhatItCannotChange(t *testing.T) { // HARD-03
	skipOnWindows(t)
	root := openTree(t)
	stuck := filepath.Join(root, "whatsapp.db")
	chmodPath = func(path string, m os.FileMode) error {
		if path == stuck {
			return os.ErrPermission
		}
		return os.Chmod(path, m)
	}
	defer func() { chmodPath = os.Chmod }()

	result, err := hardenStore(root)

	if err != nil {
		t.Fatalf("a path that cannot be changed must not be fatal: %v", err)
	}
	if len(result.Failed) != 1 || result.Failed[0] != stuck {
		t.Errorf("Failed = %v, want [%s]", result.Failed, stuck)
	}
	if got := mode(t, filepath.Join(root, "messages.db")); got != 0o600 {
		t.Errorf("the other files must still be tightened, got %v", got)
	}
}

func TestRestrictUmaskMakesNewFilesPrivate(t *testing.T) { // HARD-01
	skipOnWindows(t)
	previous := restrictUmask()
	defer restoreUmask(previous)
	path := filepath.Join(t.TempDir(), "new.txt")

	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY, 0o666)
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	dir := filepath.Join(t.TempDir(), "d")
	os.Mkdir(dir, 0o777)

	if got := mode(t, path); got != 0o600 {
		t.Errorf("file mode = %v, want 0600", got)
	}
	if got := mode(t, dir); got != 0o700 {
		t.Errorf("dir mode = %v, want 0700", got)
	}
}

func TestStoreAndDatabaseAreCreatedPrivateEvenWithAnOpenUmask(t *testing.T) { // HARD-04
	skipOnWindows(t)
	previous := restoreAndSetUmask(0o022)
	defer restoreUmask(previous)
	t.Chdir(t.TempDir())

	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	if got := mode(t, "store"); got != 0o700 {
		t.Errorf("store mode = %v, want 0700", got)
	}
	if got := mode(t, "store/messages.db"); got != 0o600 {
		t.Errorf("messages.db mode = %v, want 0600", got)
	}
}

func TestMediaDirectoriesAreCreatedPrivate(t *testing.T) { // HARD-04
	skipOnWindows(t)
	previous := restoreAndSetUmask(0o022)
	defer restoreUmask(previous)
	t.Chdir(t.TempDir())

	if _, _, err := mediaFilePath("chat@g.us", "MSG1", "photo.jpg"); err != nil {
		t.Fatal(err)
	}

	for _, dir := range []string{"store/chat@g.us", "store/chat@g.us/MSG1"} {
		if got := mode(t, dir); got != 0o700 {
			t.Errorf("%s mode = %v, want 0700", dir, got)
		}
	}
}

func TestLogFileDirectoryIsPrivate(t *testing.T) { // HARD-04
	skipOnWindows(t)
	previous := restoreAndSetUmask(0o022)
	defer restoreUmask(previous)
	path := filepath.Join(t.TempDir(), "newstore", "bridge.log")

	log, err := newRotatingFile(path, 1<<20, 3)
	if err != nil {
		t.Fatal(err)
	}
	log.Close()

	if got := mode(t, filepath.Dir(path)); got != 0o700 {
		t.Errorf("log directory mode = %v, want 0700", got)
	}
}
