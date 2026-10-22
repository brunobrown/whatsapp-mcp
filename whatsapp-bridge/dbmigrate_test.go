package main

import (
	"bytes"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/store/sqlstore"
	waLog "go.mau.fi/whatsmeow/util/log"
)

var migrationTime = time.Date(2026, 10, 21, 10, 0, 0, 0, time.UTC)

// plaintextStore builds, in a fresh directory, the plaintext messages.db an older bridge left behind.
func plaintextStore(t *testing.T) {
	t.Helper()
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	t.Setenv("WHATSAPP_DB_ENCRYPTION", "off")
	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	if err := store.StoreChat("chat@g.us", "Grupo", migrationTime); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"M1", "M2", "M3"} {
		if err := store.StoreMessage(id, "chat@g.us", "5511999990000", secretMessage+" "+id, migrationTime, false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.UpsertIdentity("207571524796601", "5511999990000", "Ana", "ana p"); err != nil {
		t.Fatal(err)
	}
	for _, stmt := range []string{
		"CREATE VIEW recent AS SELECT id FROM messages",
		"CREATE TRIGGER touch AFTER INSERT ON messages BEGIN UPDATE chats SET name = name WHERE jid = NEW.chat_jid; END",
		"PRAGMA user_version = 7",
	} {
		if _, err := store.db.Exec(stmt); err != nil {
			t.Fatal(err)
		}
	}
	store.Close()
	t.Setenv("WHATSAPP_DB_ENCRYPTION", "")
}

func captureStdout(t *testing.T, fn func()) string {
	t.Helper()
	real := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w
	fn()
	w.Close()
	os.Stdout = real
	out, _ := io.ReadAll(r)
	return string(out)
}

func backups(t *testing.T) []string {
	t.Helper()
	found, _ := filepath.Glob("store/messages.db.plain-*.bak")
	return found
}

func TestPlaintextDatabaseIsMigratedWithEverythingInIt(t *testing.T) { // MIG-01, MIG-02
	plaintextStore(t)
	before := fileHash(t, "store/messages.db")

	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	if startsWithSQLiteMagic(t, "store/messages.db") || openWithoutKey(t, "store/messages.db") == nil {
		t.Fatal("the database is not encrypted after the migration")
	}
	if got := countMessages(t, store); got != 3 {
		t.Errorf("messages = %d, want 3", got)
	}
	var chats, identities, view, trigger, index, version int
	store.db.QueryRow("SELECT count(*) FROM chats").Scan(&chats)
	store.db.QueryRow("SELECT count(*) FROM contacts_identity WHERE lid = '207571524796601'").Scan(&identities)
	store.db.QueryRow("SELECT count(*) FROM sqlite_master WHERE type = 'view' AND name = 'recent'").Scan(&view)
	store.db.QueryRow("SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND name = 'touch'").Scan(&trigger)
	store.db.QueryRow("SELECT count(*) FROM sqlite_master WHERE type = 'index' AND name = 'idx_messages_chat_ts'").Scan(&index)
	store.db.QueryRow("PRAGMA user_version").Scan(&version)
	if chats != 1 || identities != 1 || view != 1 || trigger != 1 || index != 1 || version != 7 {
		t.Errorf("chats=%d identities=%d view=%d trigger=%d index=%d user_version=%d", chats, identities, view, trigger, index, version)
	}
	if err := store.StoreMessage("M4", "chat@g.us", "x", "depois da migração", migrationTime, false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Errorf("the migrated database must accept writes: %v", err)
	}

	copies := backups(t)
	if len(copies) != 1 || fileHash(t, copies[0]) != before {
		t.Fatalf("plaintext copies = %v, want exactly the original", copies)
	}
	if !startsWithSQLiteMagic(t, copies[0]) {
		t.Error("the backup must be the plaintext original")
	}
}

func TestMigrationLeavesNoTemporaryFileAndPrivateModes(t *testing.T) { // MIG-01, MIG-03
	skipOnWindows(t)
	plaintextStore(t)
	store, _ := NewMessageStore()
	store.Close()

	entries, _ := os.ReadDir("store")
	for _, entry := range entries {
		if strings.HasSuffix(entry.Name(), migrationTempSuffix) {
			t.Errorf("temporary file left: %s", entry.Name())
		}
		if got := mode(t, filepath.Join("store", entry.Name())); got != 0o600 {
			t.Errorf("%s mode = %v, want 0600", entry.Name(), got)
		}
	}
}

func TestMigrationTellsWhichPlaintextCopyToDelete(t *testing.T) { // MIG-05
	plaintextStore(t)

	out := captureStdout(t, func() {
		store, _ := NewMessageStore()
		store.Close()
	})

	copies := backups(t)
	if len(copies) != 1 || !strings.Contains(out, copies[0]) || !strings.Contains(out, "purge-plaintext") {
		t.Errorf("output %q should name %v and the purge command", out, copies)
	}
}

func TestAnEncryptedDatabaseIsNotMigratedAgain(t *testing.T) { // MIG-07
	plaintextStore(t)
	store, _ := NewMessageStore()
	store.Close()

	again, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	again.Close()

	if len(backups(t)) != 1 {
		t.Errorf("plaintext copies = %v, want still one", backups(t))
	}
}

func TestAFailedVerificationLeavesTheOriginalAlone(t *testing.T) { // MIG-02
	plaintextStore(t)
	before := fileHash(t, "store/messages.db")
	real := verifyEncryptedCopy
	verifyEncryptedCopy = func(path, tmp, name string, cfg dbConfig) error { return errors.New("contagens diferentes") }
	defer func() { verifyEncryptedCopy = real }()

	_, err := NewMessageStore()

	if err == nil || !strings.Contains(err.Error(), "contagens diferentes") || !strings.Contains(err.Error(), "left as it was") {
		t.Errorf("err = %v", err)
	}
	if fileHash(t, "store/messages.db") != before || !startsWithSQLiteMagic(t, "store/messages.db") {
		t.Error("the original was changed")
	}
	entries, _ := os.ReadDir("store")
	if len(entries) != 1 {
		t.Errorf("files left next to the original: %v", entries)
	}
}

func TestTheRealVerificationCatchesAMissingRow(t *testing.T) { // MIG-02
	plaintextStore(t)
	cfg, _ := loadDBConfig()
	if err := exportEncrypted("store/messages.db", "store/copy.tmp", messagesDBName, cfg); err != nil {
		t.Fatal(err)
	}
	if err := verifyEncryptedCopy("store/messages.db", "store/copy.tmp", messagesDBName, cfg); err != nil {
		t.Fatalf("a faithful copy must verify: %v", err)
	}
	db, err := openDatabase("store/copy.tmp", messagesDBName, "", cfg)
	if err != nil {
		t.Fatal(err)
	}
	db.Exec("DELETE FROM messages WHERE id = 'M3'")
	db.Close()

	err = verifyEncryptedCopy("store/messages.db", "store/copy.tmp", messagesDBName, cfg)

	if err == nil || !strings.Contains(err.Error(), "messages") {
		t.Errorf("verification err = %v, want a difference in the messages table", err)
	}
}

func TestTheRealVerificationCatchesAWrongKeyOrUserVersionOrSchema(t *testing.T) { // MIG-02
	plaintextStore(t)
	cfg, _ := loadDBConfig()
	exportEncrypted("store/messages.db", "store/copy.tmp", messagesDBName, cfg)

	other := dbConfig{Encrypted: true, master: mustHex(strings.Repeat("11", 32))}
	if err := verifyEncryptedCopy("store/messages.db", "store/copy.tmp", messagesDBName, other); err == nil {
		t.Error("a copy that the key does not open must fail verification")
	}
	db, _ := openDatabase("store/copy.tmp", messagesDBName, "", cfg)
	db.Exec("PRAGMA user_version = 99")
	db.Close()
	if err := verifyEncryptedCopy("store/messages.db", "store/copy.tmp", messagesDBName, cfg); err == nil || !strings.Contains(err.Error(), "user_version") {
		t.Errorf("user_version mismatch err = %v", err)
	}
	db, _ = openDatabase("store/copy.tmp", messagesDBName, "", cfg)
	db.Exec("PRAGMA user_version = 7")
	db.Exec("DROP VIEW recent")
	db.Close()
	if err := verifyEncryptedCopy("store/messages.db", "store/copy.tmp", messagesDBName, cfg); err == nil || !strings.Contains(err.Error(), "schema") {
		t.Errorf("schema mismatch err = %v", err)
	}
}

func TestADamagedPlaintextDatabaseIsNotReplaced(t *testing.T) { // MIG-02
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	os.MkdirAll("store", 0o700)
	damaged := append([]byte(sqliteMagic), bytes.Repeat([]byte{0xAB}, 5000)...)
	os.WriteFile("store/messages.db", damaged, 0o600)

	_, err := NewMessageStore()

	if err == nil {
		t.Fatal("a damaged database was accepted")
	}
	if data, _ := os.ReadFile("store/messages.db"); !bytes.Equal(data, damaged) {
		t.Error("the damaged file was changed")
	}
	if len(backups(t)) != 0 {
		t.Error("no backup is made for a failed migration")
	}
}

func TestInterruptionAtAnyStageKeepsAValidDatabase(t *testing.T) { // MIG-03, MIG-04
	for _, stage := range []string{"exported", "verified", "backed-up"} {
		t.Run(stage, func(t *testing.T) {
			plaintextStore(t)
			before := fileHash(t, "store/messages.db")
			real := migrationStep
			migrationStep = func(s string) error {
				if s == stage {
					return errors.New("o processo morreu aqui")
				}
				return nil
			}
			_, err := NewMessageStore()
			migrationStep = real
			if err == nil {
				t.Fatal("the interruption was not reported")
			}
			if fileHash(t, "store/messages.db") != before {
				t.Fatal("the database at the original path is no longer the valid original")
			}

			store, err := NewMessageStore() // the next start finishes the job
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			if countMessages(t, store) != 3 || startsWithSQLiteMagic(t, "store/messages.db") {
				t.Error("after the next start the database must be the encrypted one with all its rows")
			}
		})
	}
}

func TestALeftoverTemporaryFileIsRemovedAndTheMigrationRuns(t *testing.T) { // MIG-04
	plaintextStore(t)
	os.WriteFile("store/messages.db"+migrationTempSuffix, []byte("lixo de uma execução interrompida"), 0o600)
	os.WriteFile("store/messages.db"+migrationTempSuffix+"-journal", []byte("lixo"), 0o600)

	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	if countMessages(t, store) != 3 || startsWithSQLiteMagic(t, "store/messages.db") {
		t.Error("the migration did not complete")
	}
	if _, err := os.Stat("store/messages.db" + migrationTempSuffix); err == nil {
		t.Error("the leftover temporary file is still there")
	}
}

func TestStaleSidecarsOfTheReplacedFileAreRemoved(t *testing.T) { // MIG-03
	dir := t.TempDir()
	path := filepath.Join(dir, "messages.db")
	for _, suffix := range []string{"-journal", "-wal", "-shm"} {
		os.WriteFile(path+suffix, []byte("x"), 0o600)
	}

	removeStaleSidecars(path)

	for _, suffix := range []string{"-journal", "-wal", "-shm"} {
		if _, err := os.Stat(path + suffix); err == nil {
			t.Errorf("%s left behind", suffix)
		}
	}
}

func TestTheSessionDatabaseIsMigratedToo(t *testing.T) { // MIG-01
	encryptedEnv(t)
	t.Chdir(t.TempDir())
	os.MkdirAll("store", 0o700)
	off := dbConfig{}
	db, err := openDatabase("store/whatsapp.db", sessionDBName, "_foreign_keys=on", off)
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
	cfg, _ := loadDBConfig()

	migrated, err := openDatabase("store/whatsapp.db", sessionDBName, "_foreign_keys=on", cfg)
	if err != nil {
		t.Fatal(err)
	}
	container = sqlstore.NewWithDB(migrated, "sqlite3", waLog.Noop)
	defer container.Close()
	if err := container.Upgrade(t.Context()); err != nil {
		t.Fatalf("whatsmeow cannot use the migrated session database: %v", err)
	}

	var secret string
	migrated.QueryRow("SELECT secret FROM session_note").Scan(&secret)
	copies, _ := filepath.Glob("store/whatsapp.db.plain-*.bak")
	if secret != "chave-de-sessao-secreta" || len(copies) != 1 || startsWithSQLiteMagic(t, "store/whatsapp.db") {
		t.Errorf("secret=%q copies=%v", secret, copies)
	}
}

func TestPurgeRemovesOnlyPlaintextCopies(t *testing.T) { // MIG-06
	skipOnWindows(t)
	plaintextStore(t)
	store, _ := NewMessageStore() // encrypted messages.db and one plaintext copy
	store.Close()
	copies := backups(t)
	// a second name for the copy, to see afterwards that its content was overwritten
	witness := filepath.Join(t.TempDir(), "witness")
	if err := os.Link(copies[0], witness); err != nil {
		t.Fatal(err)
	}
	size := int64(len(mustRead(t, copies[0])))
	oldPlain := "store/messages.db.bak-202610210000"
	os.WriteFile(oldPlain, append([]byte(sqliteMagic), make([]byte, 100)...), 0o600)
	encryptedBackup := "store/messages.db.bak-encrypted"
	os.WriteFile(encryptedBackup, []byte("\x62\x98\x72\xb6 not a plain sqlite file"), 0o600)
	notes := "store/notes.bak"
	os.WriteFile(notes, []byte("my notes"), 0o600)
	livePlain := "store/whatsapp.db" // a live plaintext database (encryption off) must never be touched
	os.WriteFile(livePlain, append([]byte(sqliteMagic), make([]byte, 100)...), 0o600)
	outside := filepath.Join(t.TempDir(), "outside.db")
	os.WriteFile(outside, append([]byte(sqliteMagic), []byte("keep me")...), 0o600)
	os.Symlink(outside, "store/link.bak")
	os.Mkdir("store/dir.bak", 0o700)
	liveHash := fileHash(t, "store/messages.db")

	removed, err := purgePlaintext("store")

	if err != nil || len(removed) != 2 {
		t.Fatalf("removed %v err %v, want the two plaintext copies", removed, err)
	}
	for _, gone := range []string{copies[0], oldPlain} {
		if _, err := os.Stat(gone); err == nil {
			t.Errorf("%s is still there", gone)
		}
	}
	if data := mustRead(t, witness); int64(len(data)) != size || !allZero(data) {
		t.Error("the content of the removed copy was not overwritten with zeros")
	}
	for _, kept := range []string{encryptedBackup, notes, livePlain, "store/link.bak", "store/dir.bak", "store/messages.db"} {
		if _, err := os.Lstat(kept); err != nil {
			t.Errorf("%s must not be touched: %v", kept, err)
		}
	}
	if fileHash(t, "store/messages.db") != liveHash {
		t.Error("the live database was changed")
	}
	if got := string(mustRead(t, outside)); !strings.Contains(got, "keep me") {
		t.Error("a file outside the store, behind a link, was overwritten")
	}
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func allZero(data []byte) bool {
	for _, b := range data {
		if b != 0 {
			return false
		}
	}
	return true
}

func TestPurgeSubcommand(t *testing.T) { // MIG-06
	plaintextStore(t)
	store, _ := NewMessageStore()
	store.Close()
	copies := backups(t)

	var out bytes.Buffer
	handled, code := runCommand([]string{"purge-plaintext"}, &out)

	if !handled || code != 0 || !strings.Contains(out.String(), "Removed "+copies[0]) {
		t.Errorf("handled=%v code=%d out=%q", handled, code, out.String())
	}
	out.Reset()
	handled, code = runCommand([]string{"purge-plaintext"}, &out)
	if !handled || code != 0 || !strings.Contains(out.String(), "No plaintext copies") {
		t.Errorf("second run: handled=%v code=%d out=%q", handled, code, out.String())
	}
}

func TestOtherSubcommandsAndNoArguments(t *testing.T) { // MIG-06
	var out bytes.Buffer

	if handled, _ := runCommand(nil, &out); handled {
		t.Error("no arguments must start the bridge")
	}
	handled, code := runCommand([]string{"desconhecido"}, &out)
	if !handled || code != 2 || !strings.Contains(out.String(), "Usage") {
		t.Errorf("handled=%v code=%d out=%q", handled, code, out.String())
	}
}

func TestPurgeInAMissingStoreIsAnError(t *testing.T) { // MIG-06
	t.Chdir(t.TempDir())

	var out bytes.Buffer
	handled, code := runCommand([]string{"purge-plaintext"}, &out)

	if !handled || code != 1 {
		t.Errorf("handled=%v code=%d out=%q", handled, code, out.String())
	}
}

func TestBackupNamesNeverCollideAndAreReusedForTheSameFile(t *testing.T) { // MIG-03
	dir := t.TempDir()
	path := filepath.Join(dir, "messages.db")
	os.WriteFile(path, []byte(sqliteMagic+"x"), 0o600)
	now := time.Date(2026, 10, 21, 10, 0, 0, 0, time.UTC)

	first, existing, err := plainBackupName(path, now)
	if err != nil || existing || !strings.HasSuffix(first, ".plain-20261021T100000Z.bak") {
		t.Fatalf("first = %q existing=%v err=%v", first, existing, err)
	}
	os.Link(path, first)
	again, existing, _ := plainBackupName(path, now)
	if again != first || !existing {
		t.Errorf("a name that is already this file must be reused: %q existing=%v", again, existing)
	}

	other := filepath.Join(dir, "other.db")
	os.WriteFile(other, []byte("different"), 0o600)
	taken := first
	os.Remove(taken)
	os.Rename(other, taken)
	next, existing, _ := plainBackupName(path, now)
	if next == taken || existing || !strings.HasSuffix(next, "-2.bak") {
		t.Errorf("a name taken by another file must not be reused: %q existing=%v", next, existing)
	}
}
