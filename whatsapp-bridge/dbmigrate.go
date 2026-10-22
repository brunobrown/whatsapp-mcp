package main

import (
	"context"
	"database/sql"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	migrationTempSuffix = ".enc-tmp"
	plainBackupMarker   = ".plain-"
)

// migrationStep is called at the stages of a migration ("exported", "verified", "backed-up"); a test
// returns an error from it to stand for the process dying there.
var migrationStep = func(stage string) error { return nil }

func init() { migratePlaintext = migrateToEncrypted }

// removeStaleSidecars deletes the journal files of a database that is about to be replaced; a leftover
// journal of the old file must not be applied to the new one.
func removeStaleSidecars(path string) {
	for _, suffix := range []string{"-journal", "-wal", "-shm"} {
		os.Remove(path + suffix)
	}
}

func quoteIdent(name string) string { return `"` + strings.ReplaceAll(name, `"`, `""`) + `"` }

// tableCounts is {table: rows} for the user tables of a database.
func tableCounts(db *sql.DB) (map[string]int, error) {
	rows, err := db.Query("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
	if err != nil {
		return nil, err
	}
	var names []string
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			rows.Close()
			return nil, err
		}
		names = append(names, name)
	}
	rows.Close()
	counts := map[string]int{}
	for _, name := range names {
		var n int
		if err := db.QueryRow("SELECT count(*) FROM " + quoteIdent(name)).Scan(&n); err != nil {
			return nil, err
		}
		counts[name] = n
	}
	return counts, nil
}

// objectCounts is {type: count} of the tables, indexes, triggers and views a database defines.
func objectCounts(db *sql.DB) (map[string]int, error) {
	rows, err := db.Query("SELECT type, count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' GROUP BY type")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	counts := map[string]int{}
	for rows.Next() {
		var kind string
		var n int
		if err := rows.Scan(&kind, &n); err != nil {
			return nil, err
		}
		counts[kind] = n
	}
	return counts, rows.Err()
}

func sameCounts(label string, plain, encrypted map[string]int) error {
	if len(plain) != len(encrypted) {
		return fmt.Errorf("%s differ: %v in the original, %v in the copy", label, plain, encrypted)
	}
	for key, n := range plain {
		if encrypted[key] != n {
			return fmt.Errorf("%s differ for %q: %d in the original, %d in the copy", label, key, n, encrypted[key])
		}
	}
	return nil
}

// verifyEncryptedCopy checks that the encrypted copy at tmp holds what the plaintext database at path holds.
var verifyEncryptedCopy = func(path, tmp, name string, cfg dbConfig) error {
	plain, err := sql.Open("sqlite3", "file:"+path+"?mode=ro")
	if err != nil {
		return err
	}
	defer plain.Close()
	encrypted, err := sql.Open("sqlite3", cfg.dsn(tmp, name, "mode=ro", true))
	if err != nil {
		return redactKey(err, cfg.keyFor(name))
	}
	defer encrypted.Close()

	plainTables, err := tableCounts(plain)
	if err != nil {
		return fmt.Errorf("cannot read the original: %v", err)
	}
	encryptedTables, err := tableCounts(encrypted)
	if err != nil {
		return fmt.Errorf("cannot read the encrypted copy with the key: %v", redactKey(err, cfg.keyFor(name)))
	}
	if err := sameCounts("tables", plainTables, encryptedTables); err != nil {
		return err
	}
	plainObjects, _ := objectCounts(plain)
	encryptedObjects, _ := objectCounts(encrypted)
	if err := sameCounts("schema objects", plainObjects, encryptedObjects); err != nil {
		return err
	}
	var plainVersion, encryptedVersion int
	plain.QueryRow("PRAGMA user_version").Scan(&plainVersion)
	encrypted.QueryRow("PRAGMA user_version").Scan(&encryptedVersion)
	if plainVersion != encryptedVersion {
		return fmt.Errorf("user_version differs: %d in the original, %d in the copy", plainVersion, encryptedVersion)
	}
	var integrity string
	if err := encrypted.QueryRow("PRAGMA integrity_check").Scan(&integrity); err != nil || integrity != "ok" {
		return fmt.Errorf("the integrity check of the encrypted copy says %q (%v)", integrity, err)
	}
	return nil
}

// exportEncrypted writes an encrypted copy of the plaintext database at path into tmp (private, empty before).
func exportEncrypted(path, tmp, name string, cfg dbConfig) error {
	if f, err := os.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, privateFileMode); err != nil {
		return err
	} else {
		f.Close()
	}
	mode := "?mode=ro"
	if strings.HasSuffix(path, "-src") {
		mode = "" // our private copy: recovery of a journal or WAL has to write to it
	}
	db, err := sql.Open("sqlite3", "file:"+path+mode)
	if err != nil {
		return err
	}
	defer db.Close()
	ctx := context.Background()
	conn, err := db.Conn(ctx) // ATTACH belongs to one connection
	if err != nil {
		return err
	}
	defer conn.Close()

	var version int
	if err := conn.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil {
		return fmt.Errorf("cannot read the original: %v", err)
	}
	key := "x'" + cfg.keyFor(name) + "'"
	steps := []string{
		fmt.Sprintf("ATTACH DATABASE '%s' AS encrypted KEY \"%s\"", strings.ReplaceAll(tmp, "'", "''"), key),
		"PRAGMA encrypted.cipher_page_size = 4096",
		"SELECT sqlcipher_export('encrypted')",
		fmt.Sprintf("PRAGMA encrypted.user_version = %d", version),
		"DETACH DATABASE encrypted",
	}
	for _, step := range steps {
		if _, err := conn.ExecContext(ctx, step); err != nil {
			return fmt.Errorf("export failed: %v", redactKey(err, cfg.keyFor(name)))
		}
	}
	return nil
}

func hasSidecars(path string) bool {
	for _, suffix := range []string{"-journal", "-wal"} {
		if info, err := os.Stat(path + suffix); err == nil && info.Size() > 0 {
			return true
		}
	}
	return false
}

// copyWithSidecars copies a database and its journal or WAL to dst (private files).
func copyWithSidecars(src, dst string) error {
	for _, suffix := range []string{"", "-journal", "-wal"} {
		if _, err := os.Stat(src + suffix); suffix != "" && err != nil {
			continue
		}
		if err := copyPrivate(src+suffix, dst+suffix); err != nil {
			return err
		}
	}
	return nil
}

func copyPrivate(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, privateFileMode)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

// linkOrCopy gives the file at src a second name, or a private copy where hard links are not available.
func linkOrCopy(src, dst string) error {
	if err := os.Link(src, dst); err == nil {
		return chmodPath(dst, privateFileMode)
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_EXCL|os.O_WRONLY, privateFileMode)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		os.Remove(dst)
		return err
	}
	return out.Close()
}

// plainBackupName is the name for the plaintext copy of path. A name already taken by another file gets
// a number; one that is already another name of this very file (left by a start that was interrupted
// after making it) is reused, and existing says so.
func plainBackupName(path string, now time.Time) (name string, existing bool, err error) {
	base := fmt.Sprintf("%s%s%s", path, plainBackupMarker, now.Format("20060102T150405Z"))
	original, err := os.Stat(path)
	if err != nil {
		return "", false, err
	}
	for n := 1; n < 1000; n++ {
		name = base + ".bak"
		if n > 1 {
			name = fmt.Sprintf("%s-%d.bak", base, n)
		}
		taken, err := os.Stat(name)
		switch {
		case os.IsNotExist(err):
			return name, false, nil
		case err != nil:
			return "", false, err
		case os.SameFile(original, taken):
			return name, true, nil
		}
	}
	return "", false, fmt.Errorf("no free name for the backup of %s", path)
}

// migrateToEncrypted turns the plaintext database at path into an encrypted one:
//  1. export into a private temporary file next to it (a leftover of an interrupted run is removed first);
//  2. verify the copy (tables, row counts, schema objects, user_version, integrity);
//  3. keep the plaintext as <name>.plain-<UTC>.bak (a second name for the same file) and move the copy into place.
//
// At every moment the path holds a valid database, and a failure leaves the original untouched.
func migrateToEncrypted(path, name string, cfg dbConfig) error {
	tmp := path + migrationTempSuffix
	os.Remove(tmp)
	removeStaleSidecars(tmp)

	var madeBackup string
	source := path
	fail := func(err error) error {
		os.Remove(tmp)
		removeStaleSidecars(tmp)
		if source != path {
			os.Remove(source)
			removeStaleSidecars(source)
		}
		if madeBackup != "" { // a link we made to the live file: leaving it would let a purge reach the live database
			os.Remove(madeBackup)
		}
		return fmt.Errorf("could not encrypt %s; it was left as it was: %v", path, err)
	}
	// A database with a journal or a WAL (the bridge was killed mid-write) needs recovery, which writes. That is
	// done on a private copy, so that the original stays untouched.
	if hasSidecars(path) {
		source = path + migrationTempSuffix + "-src"
		if err := copyWithSidecars(path, source); err != nil {
			return fail(fmt.Errorf("could not copy the database to recover it: %v", err))
		}
	}
	if err := exportEncrypted(source, tmp, name, cfg); err != nil {
		return fail(err)
	}
	if err := migrationStep("exported"); err != nil {
		return fail(err)
	}
	if err := verifyEncryptedCopy(source, tmp, name, cfg); err != nil {
		return fail(err)
	}
	if err := migrationStep("verified"); err != nil {
		return fail(err)
	}

	backup, existing, err := plainBackupName(path, time.Now().UTC())
	if err != nil {
		return fail(fmt.Errorf("could not keep a plaintext backup: %v", err))
	}
	if !existing {
		if err := linkOrCopy(path, backup); err != nil {
			return fail(fmt.Errorf("could not keep a plaintext backup: %v", err))
		}
		madeBackup = backup
	}
	if err := migrationStep("backed-up"); err != nil {
		return fail(err)
	}
	// the journal or WAL belongs to the old file: it goes with the backup, never against the new database
	for _, suffix := range []string{"-journal", "-wal", "-shm"} {
		if _, err := os.Stat(path + suffix); err == nil {
			os.Rename(path+suffix, backup+suffix)
		}
	}
	if err := os.Rename(tmp, path); err != nil {
		return fail(err)
	}
	if source != path {
		os.Remove(source)
		removeStaleSidecars(source)
	}
	fmt.Printf("Encrypted %s. The plaintext copy is %s: once everything works, delete it with `make purge-plaintext`\n", path, backup)
	return nil
}

// plaintextCopies lists the backups in dir that are plain SQLite files: names with ".bak" in them, never the
// live databases, never links.
func plaintextCopies(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var live []os.FileInfo // the databases in use, whatever their names: a backup link may be another name for one
	for _, name := range []string{messagesDBName, sessionDBName} {
		if info, err := os.Stat(filepath.Join(dir, name)); err == nil {
			live = append(live, info)
		}
	}
	isLive := func(info os.FileInfo) bool {
		for _, db := range live {
			if os.SameFile(db, info) {
				return true
			}
		}
		return false
	}
	var found []string
	for _, entry := range entries {
		name := entry.Name()
		if name == messagesDBName || name == sessionDBName || !strings.Contains(name, ".bak") {
			continue
		}
		path := filepath.Join(dir, name)
		info, err := os.Lstat(path)
		if err != nil || !info.Mode().IsRegular() || isLive(info) {
			continue
		}
		if state, err := detectDBState(path); err == nil && state == dbPlaintext {
			found = append(found, path)
		}
	}
	sort.Strings(found)
	return found, nil
}

// overwriteAndRemove fills the file with zeros, flushes it and deletes it. On a solid-state disk or a
// journaling file system this is a best effort: the old blocks may survive elsewhere.
func overwriteAndRemove(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_WRONLY, 0)
	if err != nil {
		return err
	}
	zeros := make([]byte, 64*1024)
	for left := info.Size(); left > 0; {
		n := int64(len(zeros))
		if left < n {
			n = left
		}
		if _, err := f.Write(zeros[:n]); err != nil {
			f.Close()
			return err
		}
		left -= n
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return os.Remove(path)
}

// purgePlaintext removes the plaintext copies of the databases in dir and returns what it removed.
func purgePlaintext(dir string) ([]string, error) {
	copies, err := plaintextCopies(dir)
	if err != nil {
		return nil, err
	}
	var removed []string
	for _, path := range copies {
		if err := overwriteAndRemove(path); err != nil {
			return removed, fmt.Errorf("could not remove %s: %v", path, err)
		}
		removed = append(removed, path)
	}
	return removed, nil
}

// runCommand handles the subcommands of the binary. handled is false when there is none, so that the
// bridge starts as usual.
func runCommand(args []string, out io.Writer) (handled bool, code int) {
	if len(args) == 0 {
		return false, 0
	}
	switch args[0] {
	case "purge-plaintext":
		removed, err := purgePlaintext("store")
		for _, path := range removed {
			fmt.Fprintf(out, "Removed %s\n", path)
		}
		if err != nil {
			fmt.Fprintf(out, "%v\n", err)
			return true, 1
		}
		if len(removed) == 0 {
			fmt.Fprintln(out, "No plaintext copies of the databases found in store/")
		}
		return true, 0
	}
	fmt.Fprintf(out, "Unknown command %q. Usage: whatsapp-bridge [purge-plaintext]\n", args[0])
	return true, 2
}
