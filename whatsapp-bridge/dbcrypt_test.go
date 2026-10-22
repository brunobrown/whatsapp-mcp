package main

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/store/sqlstore"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const secretMessage = "mensagem secreta do bruno sobre o contrato"

func encryptedEnv(t *testing.T) {
	t.Helper()
	t.Setenv("WHATSAPP_DB_ENCRYPTION", "")
	t.Setenv("WHATSAPP_DB_KEY", testMasterHex)
	t.Setenv("WHATSAPP_DB_KEY_FILE", "")
	t.Setenv("WHATSAPP_CONFIG_DIR", filepath.Join(t.TempDir(), "config"))
}

func fileHash(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func startsWithSQLiteMagic(t *testing.T, path string) bool {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return strings.HasPrefix(string(data), sqliteMagic)
}

// openWithoutKey opens a file the way a plain SQLite reader would: no key.
func openWithoutKey(t *testing.T, path string) error {
	t.Helper()
	db, err := sql.Open("sqlite3", "file:"+path)
	if err != nil {
		return err
	}
	defer db.Close()
	var n int
	return db.QueryRow("SELECT count(*) FROM sqlite_master").Scan(&n)
}

func storeASecret(t *testing.T, store *MessageStore) {
	t.Helper()
	if err := store.StoreChat("chat@g.us", "Grupo", time.Date(2026, 10, 21, 10, 0, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreMessage("M1", "chat@g.us", "5511999990000", secretMessage, time.Date(2026, 10, 21, 10, 0, 0, 0, time.UTC), false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}
}

func TestANewMessageDatabaseIsEncrypted(t *testing.T) { // ENC-01
	encryptedEnv(t)
	t.Chdir(t.TempDir())

	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	storeASecret(t, store)
	store.Close()

	path := "store/messages.db"
	if startsWithSQLiteMagic(t, path) {
		t.Error("the file still has the SQLite header")
	}
	if data, _ := os.ReadFile(path); strings.Contains(string(data), "secreta") || strings.Contains(string(data), "contrato") || strings.Contains(string(data), "chats") {
		t.Error("readable text found inside the encrypted file")
	}
	if err := openWithoutKey(t, path); err == nil {
		t.Error("a reader without the key opened the database")
	}
}

func TestTheEncryptedMessageDatabaseReopensWithTheKey(t *testing.T) { // ENC-01, ENC-05
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	store, _ := NewMessageStore()
	storeASecret(t, store)
	store.Close()

	again, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer again.Close()

	var content string
	if err := again.db.QueryRow("SELECT content FROM messages WHERE id = 'M1'").Scan(&content); err != nil || content != secretMessage {
		t.Errorf("content = %q err = %v", content, err)
	}
}

func TestTheSessionDatabaseIsEncrypted(t *testing.T) { // ENC-01, ENC-05
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	os.MkdirAll("store", 0o700)
	cfg, err := loadDBConfig()
	if err != nil {
		t.Fatal(err)
	}

	db, err := openDatabase("store/whatsapp.db", sessionDBName, "_foreign_keys=on", cfg)
	if err != nil {
		t.Fatal(err)
	}
	container := sqlstore.NewWithDB(db, "sqlite3", waLog.Noop)
	if err := container.Upgrade(t.Context()); err != nil {
		t.Fatal(err)
	}
	db.Exec("CREATE TABLE session_note (secret TEXT)")
	db.Exec("INSERT INTO session_note VALUES ('chave-de-sessao-secreta')")
	container.Close()

	if startsWithSQLiteMagic(t, "store/whatsapp.db") || openWithoutKey(t, "store/whatsapp.db") == nil {
		t.Error("the session database is readable without the key")
	}
	again, err := openDatabase("store/whatsapp.db", sessionDBName, "_foreign_keys=on", cfg)
	if err != nil {
		t.Fatalf("reopen with the key: %v", err)
	}
	var secret string
	again.QueryRow("SELECT secret FROM session_note").Scan(&secret)
	var tables int
	again.QueryRow("SELECT count(*) FROM sqlite_master WHERE name = 'whatsmeow_device'").Scan(&tables)
	again.Close()
	if secret != "chave-de-sessao-secreta" || tables != 1 {
		t.Errorf("after reopening: secret %q, device table %d", secret, tables)
	}
	if data, _ := os.ReadFile("store/whatsapp.db"); strings.Contains(string(data), "secreta") || strings.Contains(string(data), "whatsmeow_device") {
		t.Error("readable text found inside the encrypted session database")
	}
}

func TestEachDatabaseHasItsOwnKey(t *testing.T) { // KEY-03, ENC-01
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	os.MkdirAll("store", 0o700)
	cfg, _ := loadDBConfig()
	db, err := openDatabase("store/messages.db", messagesDBName, "", cfg)
	if err != nil {
		t.Fatal(err)
	}
	db.Exec("CREATE TABLE t (x)")
	db.Close()

	// the key of the other database does not open this one
	if _, err := openDatabase("store/messages.db", sessionDBName, "", cfg); err == nil {
		t.Error("the whatsapp.db key opened messages.db")
	}
}

func TestInvalidEncryptionSettingFailsTheStart(t *testing.T) { // ENC-02
	encryptedEnv(t)

	for _, value := range []string{"yes", "true", "1", "ON!", "disabled", "of"} {
		t.Setenv("WHATSAPP_DB_ENCRYPTION", value)
		if _, err := loadDBConfig(); err == nil || !strings.Contains(err.Error(), "WHATSAPP_DB_ENCRYPTION") {
			t.Errorf("value %q: err = %v", value, err)
		}
		t.Chdir(t.TempDir())
		if _, err := NewMessageStore(); err == nil {
			t.Errorf("value %q: the store started", value)
		}
	}
	for _, value := range []string{"on", "ON", " on ", "off", "Off", ""} {
		t.Setenv("WHATSAPP_DB_ENCRYPTION", value)
		if _, err := loadDBConfig(); err != nil {
			t.Errorf("value %q refused: %v", value, err)
		}
	}
}

func TestAWrongKeyFailsWithoutTouchingTheFile(t *testing.T) { // ENC-03, KEY-04
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	store, _ := NewMessageStore()
	storeASecret(t, store)
	store.Close()
	before := fileHash(t, "store/messages.db")
	wrong := strings.Repeat("ab", 32)
	t.Setenv("WHATSAPP_DB_KEY", wrong)

	_, err := NewMessageStore()

	if err == nil {
		t.Fatal("a wrong key opened the database")
	}
	for _, want := range []string{"messages.db", "key"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the error lacks %q: %v", want, err)
		}
	}
	for _, leak := range []string{wrong, testMasterHex, messagesKeyHex} {
		if strings.Contains(err.Error(), leak) {
			t.Errorf("the error leaks a key: %v", err)
		}
	}
	if fileHash(t, "store/messages.db") != before {
		t.Error("the encrypted file was changed")
	}
	entries, _ := os.ReadDir("store")
	if len(entries) != 1 {
		t.Errorf("unexpected files next to the database: %v", entries)
	}
}

func TestOffOpensPlaintextAsBefore(t *testing.T) { // ENC-04
	encryptedEnv(t)
	t.Setenv("WHATSAPP_DB_ENCRYPTION", "off")
	t.Setenv("WHATSAPP_DB_KEY", "")
	t.Chdir(t.TempDir())

	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	storeASecret(t, store)
	store.Close()

	if !startsWithSQLiteMagic(t, "store/messages.db") {
		t.Error("with encryption off the database must be plain SQLite")
	}
	if err := openWithoutKey(t, "store/messages.db"); err != nil {
		t.Errorf("a plain reader cannot open it: %v", err)
	}
	if _, err := os.Stat(os.Getenv("WHATSAPP_CONFIG_DIR")); err == nil {
		t.Error("with encryption off no key is created")
	}
}

func TestOffWithAnEncryptedDatabaseIsAnError(t *testing.T) { // ENC-04
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	store, _ := NewMessageStore()
	store.Close()
	before := fileHash(t, "store/messages.db")
	t.Setenv("WHATSAPP_DB_ENCRYPTION", "off")

	_, err := NewMessageStore()

	if err == nil || !strings.Contains(err.Error(), "encrypted") || !strings.Contains(err.Error(), "WHATSAPP_DB_ENCRYPTION") {
		t.Errorf("err = %v", err)
	}
	if fileHash(t, "store/messages.db") != before {
		t.Error("the encrypted file was changed")
	}
}

func TestDetectDBState(t *testing.T) { // ENC-01
	dir := t.TempDir()
	write := func(name string, data []byte) string {
		p := filepath.Join(dir, name)
		os.WriteFile(p, data, 0o600)
		return p
	}
	cases := map[string]struct {
		path string
		want dbState
	}{
		"missing":   {filepath.Join(dir, "absent.db"), dbMissing},
		"empty":     {write("empty.db", nil), dbMissing},
		"plain":     {write("plain.db", append([]byte(sqliteMagic), make([]byte, 100)...)), dbPlaintext},
		"encrypted": {write("enc.db", []byte("\x62\x98\x72\xb6 random bytes that are not the header")), dbEncrypted},
		"short":     {write("short.db", []byte("SQLite")), dbEncrypted},
	}
	for name, c := range cases {
		if got, err := detectDBState(c.path); err != nil || got != c.want {
			t.Errorf("%s: state %v err %v, want %v", name, got, err, c.want)
		}
	}
}

func TestErrorsNeverCarryTheKey(t *testing.T) { // KEY-04
	key := "feedface" + strings.Repeat("0", 56)

	err := redactKey(os.ErrInvalid, key)
	if err == nil || strings.Contains(err.Error(), key) {
		t.Errorf("err = %v", err)
	}
	leaky := redactKey(&os.PathError{Op: "open", Path: "file:x?_pragma_key=x'" + key + "'", Err: os.ErrInvalid}, key)
	if strings.Contains(leaky.Error(), key) || !strings.Contains(leaky.Error(), "***") {
		t.Errorf("redacted = %v", leaky)
	}
	if redactKey(nil, key) != nil {
		t.Error("nil must stay nil")
	}
}

func TestDSNCarriesTheKeyOnlyWhenEncrypted(t *testing.T) { // ENC-01
	cfg := dbConfig{Encrypted: true, master: mustHex(testMasterHex)}

	enc := cfg.dsn("store/messages.db", messagesDBName, "_foreign_keys=on", true)
	plain := cfg.dsn("store/messages.db", messagesDBName, "_foreign_keys=on", false)

	if !strings.Contains(enc, "_pragma_key=x'"+messagesKeyHex+"'") || !strings.Contains(enc, "_pragma_cipher_page_size=4096") {
		t.Errorf("encrypted DSN = %s", enc)
	}
	if strings.Contains(plain, "_pragma") || plain != "file:store/messages.db?_foreign_keys=on" {
		t.Errorf("plain DSN = %s", plain)
	}
	if got := cfg.dsn("a.db", "a", "", true); strings.Contains(got, "?&") {
		t.Errorf("DSN with no extra options = %s", got)
	}
}

func mustHex(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		panic(err)
	}
	return b
}

// TestWriteCrossLanguageFixture regenerates whatsapp-mcp-server/tests/fixtures/encrypted_messages.db, the
// file the Python tests read to prove that the server opens what the bridge writes. Run it with
// WRITE_CROSSLANG_FIXTURE=<path>; the key is the test master key.
func TestWriteCrossLanguageFixture(t *testing.T) {
	target := os.Getenv("WRITE_CROSSLANG_FIXTURE")
	if target == "" {
		t.Skip("set WRITE_CROSSLANG_FIXTURE to regenerate the fixture")
	}
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	when := time.Date(2026, 10, 21, 10, 0, 0, 0, time.UTC)
	store.StoreChat("fixture@g.us", "Grupo Fixture", when)
	store.StoreMessage("FX1", "fixture@g.us", "5511999990000", "primeira mensagem do fixture", when, false, "", "", "", nil, nil, nil, 0)
	store.StoreMessage("FX2", "fixture@g.us", "5511888880000", "segunda, em resposta", when.Add(time.Minute), false, "", "", "", nil, nil, nil, 0)
	store.SetMessageMeta("FX2", "fixture@g.us", MessageMeta{QuotedID: "FX1"})
	store.UpsertIdentity("207571524796601", "5511999990000", "Ana Fixture", "")
	store.Close()
	data, err := os.ReadFile("store/messages.db")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, data, 0o644); err != nil {
		t.Fatal(err)
	}
}
