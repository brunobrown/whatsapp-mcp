package main

import (
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const (
	dbKeyEnv       = "WHATSAPP_DB_KEY"
	dbKeyFileEnv   = "WHATSAPP_DB_KEY_FILE"
	dbKeyFileName  = "db.key"
	dbKeyBytes     = 32
	hkdfInfoPrefix = "whatsapp-mcp/v1/"
)

// parseKey reads a master key written as 64 hexadecimal characters. The error never contains the text.
func parseKey(text string) ([]byte, error) {
	text = strings.TrimSpace(text)
	if len(text) != dbKeyBytes*2 {
		return nil, fmt.Errorf("must be %d hexadecimal characters", dbKeyBytes*2)
	}
	key, err := hex.DecodeString(text)
	if err != nil {
		return nil, fmt.Errorf("must be %d hexadecimal characters", dbKeyBytes*2)
	}
	return key, nil
}

// loadMasterKey returns the master key of the databases and where it came from. The first source
// that is set decides, in this order: WHATSAPP_DB_KEY, the file in WHATSAPP_DB_KEY_FILE, then
// db.key in the configuration directory (created on the first run). A source that is set but
// unusable is an error: it never falls back to another key, which would silently open (or not
// open) the wrong data. The key is never part of an error.
func loadMasterKey() ([]byte, string, error) {
	key, source, _, err := loadMasterKeyWithWarnings()
	return key, source, err
}

func loadMasterKeyWithWarnings() (key []byte, source string, warnings []string, err error) {
	if value := os.Getenv(dbKeyEnv); value != "" { // set to blanks is a mistake, not "unset"
		key, err = parseKey(value)
		if err != nil {
			return nil, dbKeyEnv, nil, fmt.Errorf("%s %v", dbKeyEnv, err)
		}
		return key, dbKeyEnv, nil, nil
	}
	if path := os.Getenv(dbKeyFileEnv); path != "" {
		data, readErr := os.ReadFile(path)
		if readErr != nil {
			return nil, path, nil, fmt.Errorf("cannot read the key file %s (from %s): %v", path, dbKeyFileEnv, readErr)
		}
		if key, err = parseKey(string(data)); err != nil {
			return nil, path, nil, fmt.Errorf("the key file %s %v", path, err)
		}
		if info, statErr := os.Stat(path); statErr == nil && info.Mode().Perm()&0o077 != 0 {
			warnings = append(warnings, fmt.Sprintf("the key file %s can be read by other users (mode %04o): run chmod 600 on it", path, info.Mode().Perm()))
		}
		return key, path, warnings, nil
	}

	dir, dirErr := configDir()
	if dirErr != nil {
		return nil, "", nil, dirErr
	}
	path := filepath.Join(dir, dbKeyFileName)
	data, readErr := os.ReadFile(path)
	switch {
	case readErr == nil:
		if key, err = parseKey(string(data)); err != nil {
			return nil, path, nil, fmt.Errorf("the key file %s %v; it is not replaced, because the encrypted databases need exactly that key", path, err)
		}
		if err := chmodPath(path, privateFileMode); err != nil {
			warnings = append(warnings, fmt.Sprintf("could not restrict %s to its owner: %v", path, err))
		}
		if err := chmodPath(dir, privateDirMode); err != nil {
			warnings = append(warnings, fmt.Sprintf("could not restrict %s to its owner: %v", dir, err))
		}
		return key, path, warnings, nil
	case !os.IsNotExist(readErr):
		return nil, path, nil, fmt.Errorf("cannot read the key file %s: %v", path, readErr)
	}
	// No key yet. A new one is only right for a new installation: with encrypted databases already there it
	// could never open them, and it would sit where the real key belongs.
	if existing := encryptedDatabasesIn("store"); len(existing) > 0 {
		return nil, path, nil, fmt.Errorf("the key file %s is missing, but %s already encrypted: restore db.key (from your backup or vault), or point %s at it. A new key was NOT created, because it could not open them",
			path, strings.Join(existing, " and "), dbKeyFileEnv)
	}
	key = make([]byte, dbKeyBytes)
	if _, err := rand.Read(key); err != nil {
		return nil, path, nil, err
	}
	created, err := createPrivateFile(dir, dbKeyFileName, hex.EncodeToString(key)+"\n")
	if err != nil {
		return nil, path, nil, fmt.Errorf("could not save the new key %s: %v", path, err)
	}
	if !created { // another start made it first: that one is the key
		data, readErr := os.ReadFile(path)
		if readErr != nil {
			return nil, path, nil, fmt.Errorf("cannot read the key file %s: %v", path, readErr)
		}
		if key, err = parseKey(string(data)); err != nil {
			return nil, path, nil, fmt.Errorf("the key file %s %v", path, err)
		}
	}
	return key, path, nil, nil
}

// deriveKey gives each database its own 32-byte key from the master key (HKDF-SHA256), so that the
// two files never share a key. The MCP server derives the same bytes (see dbcrypto.py).
func deriveKey(master []byte, name string) []byte {
	key, err := hkdf.Key(sha256.New, master, nil, hkdfInfoPrefix+name, dbKeyBytes)
	if err != nil { // only for a length the function cannot produce
		panic(err)
	}
	return key
}

// encryptedDatabasesIn names the databases in dir (messages.db, whatsapp.db) that are already encrypted.
func encryptedDatabasesIn(dir string) []string {
	var names []string
	for _, name := range []string{messagesDBName, sessionDBName} {
		if state, err := detectDBState(filepath.Join(dir, name)); err == nil && state == dbEncrypted {
			names = append(names, name)
		}
	}
	return names
}
