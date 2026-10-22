package main

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

const (
	apiTokenFile  = "api_token"
	apiTokenBytes = 32
	configDirName = "whatsapp-mcp"
)

// configDir is where the bridge keeps what must live outside store/ and the repository: the API
// token and the database key. WHATSAPP_CONFIG_DIR overrides the per-user configuration directory.
func configDir() (string, error) {
	if dir := os.Getenv("WHATSAPP_CONFIG_DIR"); dir != "" {
		return dir, nil
	}
	base, err := os.UserConfigDir()
	if err != nil {
		return "", fmt.Errorf("no per-user configuration directory (set WHATSAPP_CONFIG_DIR): %v", err)
	}
	return filepath.Join(base, configDirName), nil
}

func isHexOfLength(text string, length int) bool {
	if len(text) != length {
		return false
	}
	_, err := hex.DecodeString(text)
	return err == nil && text == strings.ToLower(text)
}

// writePrivateFile writes content to path (0600) inside a directory that is 0700, replacing it
// atomically, and tightens both if they already existed with wider permissions.
func writePrivateFile(dir, name, content string) error {
	if err := os.MkdirAll(dir, privateDirMode); err != nil {
		return err
	}
	if err := chmodPath(dir, privateDirMode); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".tmp-*") // 0600
	if err != nil {
		return err
	}
	if _, err = tmp.WriteString(content); err == nil {
		err = tmp.Sync() // on disk before it takes its name
	}
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(tmp.Name(), filepath.Join(dir, name))
	}
	if err != nil {
		os.Remove(tmp.Name())
		return err
	}
	syncDir(dir)
	return nil
}

// syncDir flushes a directory so that a new name survives a crash. Best effort (not available everywhere).
func syncDir(dir string) {
	if d, err := os.Open(dir); err == nil {
		d.Sync()
		d.Close()
	}
}

// createPrivateFile creates dir/name (0600) with content only if it does not exist, flushed to disk before it
// appears. It reports whether it created the file: two starts at once cannot both win.
func createPrivateFile(dir, name, content string) (bool, error) {
	if err := os.MkdirAll(dir, privateDirMode); err != nil {
		return false, err
	}
	if err := chmodPath(dir, privateDirMode); err != nil {
		return false, err
	}
	tmp, err := os.CreateTemp(dir, ".tmp-*") // 0600
	if err != nil {
		return false, err
	}
	defer os.Remove(tmp.Name())
	if _, err = tmp.WriteString(content); err == nil {
		err = tmp.Sync()
	}
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return false, err
	}
	if err := os.Link(tmp.Name(), filepath.Join(dir, name)); err != nil {
		if os.IsExist(err) {
			return false, nil
		}
		return false, err
	}
	syncDir(dir)
	return true, nil
}

// loadOrCreateToken returns the token of the local API: the one in dir/api_token if it is valid,
// else a new random one. The file is kept private to the owner. The token is never printed.
func loadOrCreateToken(dir string) (string, error) {
	path := filepath.Join(dir, apiTokenFile)
	if data, err := os.ReadFile(path); err == nil {
		if token := strings.TrimSpace(string(data)); isHexOfLength(token, apiTokenBytes*2) {
			if err := chmodPath(path, privateFileMode); err != nil {
				return "", err
			}
			if err := chmodPath(dir, privateDirMode); err != nil {
				return "", err
			}
			return token, nil
		}
	}
	raw := make([]byte, apiTokenBytes)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	token := hex.EncodeToString(raw)
	if err := writePrivateFile(dir, apiTokenFile, token+"\n"); err != nil {
		return "", fmt.Errorf("could not save the API token: %v", err)
	}
	return token, nil
}

// apiGuard puts the local API behind a token and refuses what a browser or a rebinding attack
// would send: a foreign Host, any Origin, and a POST that is not JSON.
type apiGuard struct {
	token string
	hosts map[string]bool
}

func newAPIGuard(token string, port int) *apiGuard {
	return &apiGuard{token: token, hosts: map[string]bool{
		fmt.Sprintf("127.0.0.1:%d", port): true,
		fmt.Sprintf("localhost:%d", port): true,
	}}
}

func refuse(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]any{"success": false, "error_code": code, "message": message})
}

func (g *apiGuard) authorized(r *http.Request) bool {
	scheme, value, found := strings.Cut(r.Header.Get("Authorization"), " ")
	if !found || !strings.EqualFold(scheme, "Bearer") {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(strings.TrimSpace(value)), []byte(g.token)) == 1
}

func (g *apiGuard) wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !g.hosts[strings.ToLower(r.Host)] {
			refuse(w, http.StatusForbidden, "FORBIDDEN", "The Host of the request is not this bridge")
			return
		}
		if _, present := r.Header["Origin"]; present {
			refuse(w, http.StatusForbidden, "FORBIDDEN", "Requests from a web page are refused")
			return
		}
		if !g.authorized(r) {
			w.Header().Set("WWW-Authenticate", "Bearer")
			refuse(w, http.StatusUnauthorized, "UNAUTHORIZED", "Missing or wrong API token")
			return
		}
		if r.Method == http.MethodPost {
			if mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type")); err != nil || mediaType != "application/json" {
				refuse(w, http.StatusUnsupportedMediaType, "UNSUPPORTED_MEDIA_TYPE", "POST bodies must be application/json")
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}
