package main

import (
	"database/sql"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"strings"

	_ "github.com/mutecomm/go-sqlcipher/v4" // registers "sqlite3": SQLite with SQLCipher
)

const (
	dbEncryptionEnv = "WHATSAPP_DB_ENCRYPTION"
	sqliteMagic     = "SQLite format 3\x00"
	messagesDBName  = "messages.db"
	sessionDBName   = "whatsapp.db"
)

// dbConfig says whether the databases are encrypted and with which master key.
type dbConfig struct {
	Encrypted bool
	master    []byte
	Source    string   // where the master key came from, for the log
	Warnings  []string // problems with the key file that do not stop the start
}

// loadDBConfig reads WHATSAPP_DB_ENCRYPTION (on by default) and, when on, the master key.
func loadDBConfig() (dbConfig, error) {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(dbEncryptionEnv))) {
	case "off":
		return dbConfig{}, nil
	case "", "on":
	default:
		return dbConfig{}, fmt.Errorf("%s must be \"on\" or \"off\"", dbEncryptionEnv)
	}
	key, source, warnings, err := loadMasterKeyWithWarnings()
	if err != nil {
		return dbConfig{}, err
	}
	return dbConfig{Encrypted: true, master: key, Source: source, Warnings: warnings}, nil
}

// keyFor is the hexadecimal key of one database file (name is its file name, e.g. messages.db).
func (c dbConfig) keyFor(name string) string {
	return hex.EncodeToString(deriveKey(c.master, name))
}

// dbState is what a database path holds.
type dbState int

const (
	dbMissing   dbState = iota // no file, or an empty one
	dbPlaintext                // a plain SQLite file
	dbEncrypted                // anything else: SQLCipher writes no recognisable header
)

// detectDBState looks at the first bytes of the file.
func detectDBState(path string) (dbState, error) {
	f, err := os.Open(path)
	if os.IsNotExist(err) {
		return dbMissing, nil
	}
	if err != nil {
		return dbMissing, err
	}
	defer f.Close()
	head := make([]byte, len(sqliteMagic))
	n, err := io.ReadFull(f, head)
	switch {
	case n == 0:
		return dbMissing, nil
	case err == nil && string(head) == sqliteMagic:
		return dbPlaintext, nil
	}
	return dbEncrypted, nil
}

// redactKey removes the key from a message that may carry the connection string.
func redactKey(err error, keys ...string) error {
	if err == nil {
		return nil
	}
	text := err.Error()
	for _, key := range keys {
		if key != "" {
			text = strings.ReplaceAll(text, key, "***")
		}
	}
	return fmt.Errorf("%s", text)
}

// dsn builds the connection string of a database. extra are the usual driver options.
func (c dbConfig) dsn(path, name, extra string, encrypted bool) string {
	options := extra
	if encrypted {
		options += "&_pragma_key=x'" + c.keyFor(name) + "'&_pragma_cipher_page_size=4096"
	}
	return "file:" + path + "?" + strings.TrimPrefix(options, "&")
}

// migratePlaintext moves a plaintext database to an encrypted one. It is set by dbmigrate.go; until
// then a plaintext database is opened as it is.
var migratePlaintext func(path, name string, cfg dbConfig) error

// openDatabase opens (or creates) the database at path under the rules of the configuration:
//   - encryption on: a new database is created encrypted, a plaintext one is migrated, an encrypted one is opened with the key;
//   - encryption off: a plaintext or new database is opened as plain SQLite, an encrypted one is an error.
//
// A database that the key does not open is an error, and the file is never touched or replaced.
func openDatabase(path, name, options string, cfg dbConfig) (*sql.DB, error) {
	state, err := detectDBState(path)
	if err != nil {
		return nil, fmt.Errorf("cannot look at %s: %v", path, err)
	}
	encrypted := cfg.Encrypted
	switch {
	case !cfg.Encrypted && state == dbEncrypted:
		return nil, fmt.Errorf("%s is encrypted but %s=off: turn encryption on, or restore a plaintext copy", path, dbEncryptionEnv)
	case cfg.Encrypted && state == dbPlaintext:
		if migratePlaintext == nil {
			encrypted = false
		} else if err := migratePlaintext(path, name, cfg); err != nil {
			return nil, err
		}
	}
	db, err := sql.Open("sqlite3", cfg.dsn(path, name, options, encrypted))
	if err != nil {
		return nil, redactKey(err, cfg.keyFor(name))
	}
	// the first statement is where a wrong key shows up
	var count int
	if err := db.QueryRow("SELECT count(*) FROM sqlite_master").Scan(&count); err != nil {
		db.Close()
		if encrypted && state == dbEncrypted {
			return nil, fmt.Errorf("cannot open %s with the configured key (the key is wrong or the file is damaged); the file was not changed: %v",
				path, redactKey(err, cfg.keyFor(name)))
		}
		return nil, fmt.Errorf("cannot open %s: %v", path, redactKey(err, cfg.keyFor(name)))
	}
	return db, nil
}
