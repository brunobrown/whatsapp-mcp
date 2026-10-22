package main

import (
	"encoding/hex"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

const (
	testMasterHex = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
	// known answers, computed with an independent HKDF-SHA256 (RFC 5869, checked against its test case 1) in Python
	messagesKeyHex = "0f3cd7db53fb5087a9624f5c1f1f30c3e1f99a1e474be5f04df6e8535b1f6d86"
	sessionKeyHex  = "b82e2676fe8259e623b594a53d488cd4dff20fa468da22bd16f5dd904e4bb416"
)

func clearKeyEnv(t *testing.T) string {
	t.Helper()
	t.Chdir(t.TempDir()) // the key check looks at ./store: never at the real one
	dir := filepath.Join(t.TempDir(), "config")
	t.Setenv("WHATSAPP_CONFIG_DIR", dir)
	t.Setenv("WHATSAPP_DB_KEY", "")
	t.Setenv("WHATSAPP_DB_KEY_FILE", "")
	return dir
}

func TestDerivedKeysMatchTheKnownAnswers(t *testing.T) { // KEY-03
	master, _ := hex.DecodeString(testMasterHex)

	if got := hex.EncodeToString(deriveKey(master, "messages.db")); got != messagesKeyHex {
		t.Errorf("messages.db key = %s, want %s", got, messagesKeyHex)
	}
	if got := hex.EncodeToString(deriveKey(master, "whatsapp.db")); got != sessionKeyHex {
		t.Errorf("whatsapp.db key = %s, want %s", got, sessionKeyHex)
	}
}

func TestEachDatabaseGetsItsOwnKey(t *testing.T) { // KEY-03
	master, _ := hex.DecodeString(testMasterHex)
	other := append([]byte{}, master...)
	other[0] ^= 1

	a, b := deriveKey(master, "messages.db"), deriveKey(master, "whatsapp.db")

	if len(a) != 32 || hex.EncodeToString(a) == hex.EncodeToString(b) {
		t.Error("the keys of the two databases must be 32 bytes and differ")
	}
	if hex.EncodeToString(deriveKey(other, "messages.db")) == hex.EncodeToString(a) {
		t.Error("a different master key must give a different key")
	}
	if hex.EncodeToString(deriveKey(master, "messages.db")) != hex.EncodeToString(a) {
		t.Error("derivation must be deterministic")
	}
}

func TestKeyFromTheEnvironment(t *testing.T) { // KEY-01
	clearKeyEnv(t)

	for _, value := range []string{testMasterHex, strings.ToUpper(testMasterHex), "  " + testMasterHex + "\n"} {
		t.Setenv("WHATSAPP_DB_KEY", value)
		key, source, err := loadMasterKey()
		if err != nil || hex.EncodeToString(key) != testMasterHex || source != "WHATSAPP_DB_KEY" {
			t.Errorf("value %q -> %x %q %v", value, key, source, err)
		}
	}
}

func TestKeyFromAFile(t *testing.T) { // KEY-01
	clearKeyEnv(t)
	path := filepath.Join(t.TempDir(), "my.key")
	os.WriteFile(path, []byte(testMasterHex+"\n"), 0o600)
	t.Setenv("WHATSAPP_DB_KEY_FILE", path)

	key, source, err := loadMasterKey()

	if err != nil || hex.EncodeToString(key) != testMasterHex || source != path {
		t.Errorf("-> %x %q %v", key, source, err)
	}
}

func TestSourcesAreTriedInOrderWithoutFallingBack(t *testing.T) { // KEY-01, KEY-02
	dir := clearKeyEnv(t)
	filePath := filepath.Join(t.TempDir(), "my.key")
	otherHex := strings.Repeat("ab", 32)
	os.WriteFile(filePath, []byte(otherHex), 0o600)
	t.Setenv("WHATSAPP_DB_KEY_FILE", filePath)
	t.Setenv("WHATSAPP_DB_KEY", testMasterHex)

	key, source, _ := loadMasterKey()
	if hex.EncodeToString(key) != testMasterHex || source != "WHATSAPP_DB_KEY" {
		t.Errorf("the environment key must win over the file, got %q", source)
	}

	t.Setenv("WHATSAPP_DB_KEY", "")
	key, _, _ = loadMasterKey()
	if hex.EncodeToString(key) != otherHex {
		t.Error("with no environment key the file must be used")
	}
	if _, err := os.Stat(dir); err == nil {
		t.Error("the default key must not be created when another source is chosen")
	}
}

func TestAnInvalidKeyIsAnErrorNotAFallback(t *testing.T) { // KEY-02, KEY-04
	clearKeyEnv(t)
	good := filepath.Join(t.TempDir(), "good.key")
	os.WriteFile(good, []byte(testMasterHex), 0o600)
	t.Setenv("WHATSAPP_DB_KEY_FILE", good)

	for _, bad := range []string{"zz" + testMasterHex[2:], testMasterHex[:62], testMasterHex + "00", "chave-secreta-em-texto"} {
		t.Setenv("WHATSAPP_DB_KEY", bad)
		_, _, err := loadMasterKey()
		if err == nil {
			t.Errorf("key %q accepted", bad)
			continue
		}
		if !strings.Contains(err.Error(), "WHATSAPP_DB_KEY") {
			t.Errorf("the error must name the source: %v", err)
		}
		if strings.Contains(err.Error(), bad) {
			t.Errorf("the error leaks the key: %v", err)
		}
	}
}

func TestAKeyFileThatIsMissingOrInvalidIsAnError(t *testing.T) { // KEY-02, KEY-04
	clearKeyEnv(t)
	missing := filepath.Join(t.TempDir(), "absent.key")
	t.Setenv("WHATSAPP_DB_KEY_FILE", missing)
	if _, _, err := loadMasterKey(); err == nil || !strings.Contains(err.Error(), missing) {
		t.Errorf("a missing key file must be an error naming it: %v", err)
	}
	if _, err := os.Stat(missing); err == nil {
		t.Error("an explicit key file must not be created")
	}

	bad := filepath.Join(t.TempDir(), "bad.key")
	os.WriteFile(bad, []byte("isto-nao-e-uma-chave-hexadecimal"), 0o600)
	t.Setenv("WHATSAPP_DB_KEY_FILE", bad)
	_, _, err := loadMasterKey()
	if err == nil || strings.Contains(err.Error(), "isto-nao-e-uma-chave") || !strings.Contains(err.Error(), bad) {
		t.Errorf("an invalid key file must be an error naming the file and not its content: %v", err)
	}
}

func TestTheDefaultKeyIsCreatedPrivateAndReused(t *testing.T) { // KEY-01
	skipOnWindows(t)
	dir := clearKeyEnv(t)

	first, source, err := loadMasterKey()
	if err != nil {
		t.Fatal(err)
	}
	second, _, _ := loadMasterKey()

	path := filepath.Join(dir, "db.key")
	data, _ := os.ReadFile(path)
	if source != path || !regexp.MustCompile(`^[0-9a-f]{64}\n$`).Match(data) || hex.EncodeToString(first) != strings.TrimSpace(string(data)) {
		t.Errorf("source %q file %q", source, data)
	}
	if hex.EncodeToString(first) != hex.EncodeToString(second) {
		t.Error("the default key must be reused")
	}
	if mode(t, path) != 0o600 || mode(t, dir) != 0o700 {
		t.Errorf("modes: file %v dir %v, want 0600 and 0700", mode(t, path), mode(t, dir))
	}
}

func TestTwoInstallationsDoNotShareAKey(t *testing.T) { // KEY-01
	a, _, _ := func() ([]byte, string, error) { clearKeyEnv(t); return loadMasterKey() }()
	b, _, _ := func() ([]byte, string, error) { clearKeyEnv(t); return loadMasterKey() }()

	if hex.EncodeToString(a) == hex.EncodeToString(b) {
		t.Error("two new installations must not get the same key")
	}
}

func TestAnExistingBadDefaultKeyIsNeverReplaced(t *testing.T) { // KEY-02
	dir := clearKeyEnv(t)
	os.MkdirAll(dir, 0o700)
	path := filepath.Join(dir, "db.key")
	os.WriteFile(path, []byte("corrompida"), 0o600)

	_, _, err := loadMasterKey()

	if err == nil {
		t.Fatal("a corrupted default key must be an error: replacing it would make the encrypted databases unreadable")
	}
	if data, _ := os.ReadFile(path); string(data) != "corrompida" {
		t.Errorf("the file was changed to %q", data)
	}
}

func TestADefaultKeyWithWideModeIsTightened(t *testing.T) { // KEY-01
	skipOnWindows(t)
	dir := clearKeyEnv(t)
	loadMasterKey()
	path := filepath.Join(dir, "db.key")
	os.Chmod(path, 0o644)
	os.Chmod(dir, 0o755)

	loadMasterKey()

	if mode(t, path) != 0o600 || mode(t, dir) != 0o700 {
		t.Errorf("modes: file %v dir %v", mode(t, path), mode(t, dir))
	}
}

func TestAnExplicitKeyFileWithWideModeIsReportedNotChanged(t *testing.T) { // KEY-01
	skipOnWindows(t)
	clearKeyEnv(t)
	path := filepath.Join(t.TempDir(), "my.key")
	os.WriteFile(path, []byte(testMasterHex), 0o644)
	os.Chmod(path, 0o644)
	t.Setenv("WHATSAPP_DB_KEY_FILE", path)

	_, _, warnings, err := loadMasterKeyWithWarnings()

	if err != nil || len(warnings) != 1 || !strings.Contains(warnings[0], path) {
		t.Errorf("warnings = %v err = %v", warnings, err)
	}
	if mode(t, path) != 0o644 {
		t.Error("a key file the user chose must not be changed")
	}
}

func TestAKeySetToBlanksIsAnErrorNotUnset(t *testing.T) { // KEY-02
	clearKeyEnv(t)
	t.Setenv("WHATSAPP_DB_KEY", "   ")

	_, _, err := loadMasterKey()

	if err == nil || !strings.Contains(err.Error(), "WHATSAPP_DB_KEY") {
		t.Errorf("a blank key must be an error, got %v", err)
	}
}

func TestTwoFirstStartsAtOnceShareTheSameKey(t *testing.T) { // KEY-01
	dir := clearKeyEnv(t)
	const starts = 16
	keys := make(chan string, starts)
	for i := 0; i < starts; i++ {
		go func() {
			key, _, err := loadMasterKey()
			if err != nil {
				keys <- "error: " + err.Error()
				return
			}
			keys <- hex.EncodeToString(key)
		}()
	}
	seen := map[string]bool{}
	for i := 0; i < starts; i++ {
		seen[<-keys] = true
	}
	data, _ := os.ReadFile(filepath.Join(dir, "db.key"))

	if len(seen) != 1 || !seen[strings.TrimSpace(string(data))] {
		t.Errorf("starts got %d different results, file holds %q", len(seen), data)
	}
}

func TestCreatePrivateFileNeverReplacesAnExistingOne(t *testing.T) { // KEY-01
	dir := t.TempDir()
	if created, err := createPrivateFile(dir, "k", "first"); !created || err != nil {
		t.Fatalf("created=%v err=%v", created, err)
	}

	created, err := createPrivateFile(dir, "k", "second")

	if created || err != nil || string(mustRead(t, filepath.Join(dir, "k"))) != "first" {
		t.Errorf("created=%v err=%v content=%q", created, err, mustRead(t, filepath.Join(dir, "k")))
	}
	if entries, _ := os.ReadDir(dir); len(entries) != 1 {
		t.Errorf("temporary files left behind: %v", entries)
	}
}

func TestAMissingDefaultKeyIsNotReplacedWhenEncryptedDatabasesExist(t *testing.T) { // KEY-02
	dir := clearKeyEnv(t)
	t.Chdir(t.TempDir())
	os.MkdirAll("store", 0o700)
	os.WriteFile("store/messages.db", []byte("\x62\x98\x72\xb6 encrypted"), 0o600)

	_, _, err := loadMasterKey()

	if err == nil || !strings.Contains(err.Error(), "restore db.key") || !strings.Contains(err.Error(), "messages.db") {
		t.Fatalf("err = %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(dir, "db.key")); statErr == nil {
		t.Error("a new key was created next to databases it cannot open")
	}
}

func TestAMissingDefaultKeyIsCreatedForANewInstallationOrPlaintextDatabases(t *testing.T) { // KEY-01
	dir := clearKeyEnv(t)
	t.Chdir(t.TempDir())
	os.MkdirAll("store", 0o700)
	os.WriteFile("store/messages.db", append([]byte(sqliteMagic), make([]byte, 50)...), 0o600) // plaintext: it will be migrated

	if _, _, err := loadMasterKey(); err != nil {
		t.Fatalf("err = %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "db.key")); err != nil {
		t.Error("the key was not created")
	}
}
