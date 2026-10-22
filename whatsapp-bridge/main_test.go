package main

import (
	"os"
	"testing"
)

// TestMain keeps every test away from the real configuration directory and gives them a database key,
// so the whole suite runs against encrypted databases (or against plaintext ones when it is started
// with WHATSAPP_DB_ENCRYPTION=off).
func TestMain(m *testing.M) {
	dir, err := os.MkdirTemp("", "whatsapp-bridge-test-config")
	if err != nil {
		panic(err)
	}
	os.Setenv("WHATSAPP_CONFIG_DIR", dir)
	os.Setenv("WHATSAPP_DB_KEY", "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
	os.Setenv("WHATSAPP_DB_KEY_FILE", "")
	code := m.Run()
	os.RemoveAll(dir)
	os.Exit(code)
}
