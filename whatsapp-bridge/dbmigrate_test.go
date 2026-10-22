package main

import (
	"bytes"
	"errors"
	"fmt"
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

func TestPurgeNeverReachesALiveDatabaseThroughABackupLink(t *testing.T) { // MIG-06 (perda de dados)
	skipOnWindows(t)
	plaintextStore(t) // a live plaintext messages.db
	live := "store/messages.db"
	before := fileHash(t, live)
	size := len(mustRead(t, live))
	link := "store/messages.db.plain-20261022T000000Z.bak" // the state an interrupted swap used to leave behind
	if err := os.Link(live, link); err != nil {
		t.Fatal(err)
	}

	removed, err := purgePlaintext("store")

	if err != nil || len(removed) != 0 {
		t.Fatalf("removed %v err %v: a name of the live database must not be purged", removed, err)
	}
	if fileHash(t, live) != before || len(mustRead(t, live)) != size {
		t.Error("the live database was overwritten")
	}
}

func TestAFailedSwapLeavesNoLinkToTheLiveDatabase(t *testing.T) { // MIG-03, MIG-06
	plaintextStore(t)
	before := fileHash(t, "store/messages.db")
	real := migrationStep
	migrationStep = func(stage string) error {
		if stage == "backed-up" {
			return errors.New("o processo morreu aqui")
		}
		return nil
	}
	_, err := NewMessageStore()
	migrationStep = real
	if err == nil {
		t.Fatal("the failure was not reported")
	}

	if len(backups(t)) != 0 {
		t.Errorf("a link to the live database was left behind: %v", backups(t))
	}
	if removed, _ := purgePlaintext("store"); len(removed) != 0 || fileHash(t, "store/messages.db") != before {
		t.Error("a purge after the failed swap touched the live database")
	}
}

func TestADatabaseWithAWALIsRecoveredOnACopyAndLosesNothing(t *testing.T) { // MIG-01, MIG-03 (bridge morto no meio da escrita)
	skipOnWindows(t)
	plaintextStore(t)
	t.Setenv("WHATSAPP_DB_ENCRYPTION", "off")
	live, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer live.Close()
	live.db.Exec("PRAGMA journal_mode=WAL")
	live.db.Exec("PRAGMA wal_autocheckpoint=0")
	for _, id := range []string{"W1", "W2"} {
		if err := live.StoreMessage(id, "chat@g.us", "x", "só no WAL "+id, migrationTime, false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatal(err)
		}
	}
	// the image a crash leaves: the database file and its WAL, with the two rows only in the WAL
	crash := t.TempDir()
	os.MkdirAll(filepath.Join(crash, "store"), 0o700)
	for _, suffix := range []string{"", "-wal"} {
		if err := copyPrivate("store/messages.db"+suffix, filepath.Join(crash, "store", "messages.db"+suffix)); err != nil {
			t.Fatal(err)
		}
	}
	t.Chdir(crash)
	t.Setenv("WHATSAPP_DB_ENCRYPTION", "")
	original := fileHash(t, "store/messages.db")

	store, err := NewMessageStore()
	if err != nil {
		t.Fatalf("a database with a WAL must migrate: %v", err)
	}
	defer store.Close()

	if got := countMessages(t, store); got != 5 {
		t.Errorf("messages after the migration = %d, want 5 (3 + the 2 that were only in the WAL)", got)
	}
	copies := backups(t)
	if len(copies) != 1 || fileHash(t, copies[0]) != original {
		t.Fatalf("the plaintext copy must be the untouched original: %v", copies)
	}
	if _, err := os.Stat(copies[0] + "-wal"); err != nil {
		t.Error("the WAL must go with the plaintext copy")
	}
	for _, leftover := range []string{"store/messages.db-wal", "store/messages.db-shm", "store/messages.db-journal"} {
		if _, err := os.Stat(leftover); err == nil {
			t.Errorf("%s was left against the new database", leftover)
		}
	}
	entries, _ := os.ReadDir("store")
	for _, entry := range entries {
		if strings.Contains(entry.Name(), "enc-tmp") {
			t.Errorf("temporary file left: %s", entry.Name())
		}
	}
}

func TestAnEmptyStaleJournalDoesNotStayAgainstTheNewDatabase(t *testing.T) { // MIG-03
	plaintextStore(t)
	os.WriteFile("store/messages.db-journal", nil, 0o600)

	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	if _, err := os.Stat("store/messages.db-journal"); err == nil {
		t.Error("a journal of the replaced file is still next to the new database")
	}
	if countMessages(t, store) != 3 {
		t.Error("rows lost")
	}
}

func TestADatabaseWithAHotJournalIsRecoveredOnACopyAndTheOriginalStaysUntouched(t *testing.T) { // MIG-01, MIG-03
	skipOnWindows(t)
	plaintextStore(t)
	t.Setenv("WHATSAPP_DB_ENCRYPTION", "off")
	live, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer live.Close()
	conn, err := live.db.Conn(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.ExecContext(t.Context(), "PRAGMA cache_size = 1") // dirty pages spill to the file, which forces a journal
	conn.ExecContext(t.Context(), "BEGIN")
	big := strings.Repeat("x", 3000)
	for i := 0; i < 60; i++ {
		if _, err := conn.ExecContext(t.Context(), "INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES (?, 'chat@g.us', 'x', ?, '2026-10-21 10:00:00', 0)",
			fmt.Sprintf("HOT%d", i), big); err != nil {
			t.Fatal(err)
		}
	}
	crash := t.TempDir()
	os.MkdirAll(filepath.Join(crash, "store"), 0o700)
	if err := copyWithSidecars("store/messages.db", filepath.Join(crash, "store", "messages.db")); err != nil {
		t.Fatal(err)
	}
	conn.ExecContext(t.Context(), "ROLLBACK")
	if _, err := os.Stat(filepath.Join(crash, "store", "messages.db-journal")); err != nil {
		t.Skip("this SQLite did not leave a hot journal for the test to use")
	}
	t.Chdir(crash)
	t.Setenv("WHATSAPP_DB_ENCRYPTION", "")
	original := fileHash(t, "store/messages.db")

	store, err := NewMessageStore()
	if err != nil {
		t.Fatalf("a database with a hot journal must migrate: %v", err)
	}
	defer store.Close()

	if got := countMessages(t, store); got != 3 { // the interrupted transaction is rolled back, as SQLite always does
		t.Errorf("messages = %d, want the 3 that were committed", got)
	}
	copies := backups(t)
	if len(copies) != 1 || fileHash(t, copies[0]) != original {
		t.Fatalf("the plaintext copy must be the untouched original: %v", copies)
	}
	if _, err := os.Stat(copies[0] + "-journal"); err != nil {
		t.Error("the journal must go with the plaintext copy")
	}
	if _, err := os.Stat("store/messages.db-journal"); err == nil {
		t.Error("the old journal was left against the new database")
	}
}

func TestAStaleRecoveryCopyIsWipedOnTheNextStart(t *testing.T) { // MIG-04 (revisão de segurança)
	skipOnWindows(t)
	plaintextStore(t)
	stale := "store/messages.db" + migrationTempSuffix + "-src"
	os.WriteFile(stale, []byte(secretMessage), 0o600)
	os.WriteFile(stale+"-wal", []byte(secretMessage), 0o600)
	witness := filepath.Join(t.TempDir(), "witness")
	os.Link(stale, witness) // a second name: shows whether the content was overwritten before the unlink

	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	for _, leftover := range []string{stale, stale + "-wal"} {
		if _, err := os.Stat(leftover); err == nil {
			t.Errorf("%s was left behind", leftover)
		}
	}
	if !allZero(mustRead(t, witness)) {
		t.Error("the plaintext of the stale copy was not overwritten")
	}
}

func TestPurgeAlsoRemovesRecoveryCopiesAndTheSidecarsOfPlaintextCopies(t *testing.T) { // MIG-06
	skipOnWindows(t)
	t.Chdir(t.TempDir())
	os.MkdirAll("store", 0o700)
	plain := append([]byte(sqliteMagic), make([]byte, 100)...)
	files := map[string][]byte{
		"store/messages.db.plain-X.bak":         plain,
		"store/messages.db.plain-X.bak-wal":     []byte(secretMessage),
		"store/messages.db.plain-X.bak-journal": []byte(secretMessage),
		"store/messages.db.enc-tmp-src":         plain,
		"store/messages.db.enc-tmp-src-journal": []byte(secretMessage),
		"store/messages.db":                     []byte("\x62\x98\x72\xb6 encrypted live"),
		"store/messages.db-wal":                 []byte("sidecar of a live database"),
		"store/other.bak":                       []byte("my notes"),
		"store/other.bak-wal":                   []byte("not a sidecar of a plaintext copy"),
		"store/encrypted.bak":                   []byte("\x62\x98\x72\xb6 encrypted"),
		"store/encrypted.bak-journal":           []byte("sidecar of an encrypted copy"),
	}
	for name, data := range files {
		os.WriteFile(name, data, 0o600)
	}

	removed, err := purgePlaintext("store")

	if err != nil || len(removed) != 5 {
		t.Fatalf("removed %v err %v, want the 5 plaintext files", removed, err)
	}
	for _, kept := range []string{"store/messages.db", "store/messages.db-wal", "store/other.bak", "store/other.bak-wal", "store/encrypted.bak", "store/encrypted.bak-journal"} {
		if _, err := os.Stat(kept); err != nil {
			t.Errorf("%s must not be touched", kept)
		}
	}
}


func TestPurgeKeepsTheJournalsOfALivePlaintextDatabase(t *testing.T) { // MIG-06
	skipOnWindows(t)
	t.Chdir(t.TempDir())
	os.MkdirAll("store", 0o700)
	plain := append([]byte(sqliteMagic), make([]byte, 100)...)
	os.WriteFile("store/messages.db", plain, 0o600)
	os.WriteFile("store/messages.db-journal", []byte("hot journal of the live database"), 0o600)
	os.WriteFile("store/messages.db-wal", []byte("wal of the live database"), 0o600)
	// a swap that moved the journal to the backup name and then failed leaves it next to a link to the live file
	os.Link("store/messages.db", "store/messages.db.plain-X.bak")
	os.WriteFile("store/messages.db.plain-X.bak-journal", []byte("hot journal now under the backup name"), 0o600)

	removed, err := purgePlaintext("store")

	if err != nil || len(removed) != 0 {
		t.Fatalf("removed %v err %v, want nothing", removed, err)
	}
	for _, kept := range []string{"store/messages.db", "store/messages.db-journal", "store/messages.db-wal",
		"store/messages.db.plain-X.bak", "store/messages.db.plain-X.bak-journal"} {
		if _, err := os.Stat(kept); err != nil {
			t.Errorf("%s must not be removed: %v", kept, err)
		}
	}
}
