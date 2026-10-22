package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

const guardPort = 8080

func guarded(t *testing.T) (http.Handler, *int, string) {
	t.Helper()
	dir := t.TempDir()
	token, err := loadOrCreateToken(dir)
	if err != nil {
		t.Fatal(err)
	}
	reached := 0
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached++
		w.WriteHeader(http.StatusOK)
	})
	return newAPIGuard(token, guardPort).wrap(next), &reached, token
}

func call(h http.Handler, method, host string, headers map[string]string, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, "http://127.0.0.1:8080/api/send", strings.NewReader(body))
	req.Host = host // set directly: some of the hosts tried are not valid in a URL
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func goodHeaders(token string) map[string]string {
	return map[string]string{"Authorization": "Bearer " + token, "Content-Type": "application/json"}
}

func TestTokenIsCreatedPrivateAndReused(t *testing.T) { // API-05
	skipOnWindows(t)
	dir := filepath.Join(t.TempDir(), "config", "whatsapp-mcp")

	first, err := loadOrCreateToken(dir)
	if err != nil {
		t.Fatal(err)
	}
	second, _ := loadOrCreateToken(dir)

	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(first) || first != second {
		t.Errorf("token = %q then %q, want the same 64 hex characters", first, second)
	}
	if got := mode(t, dir); got != 0o700 {
		t.Errorf("config dir mode = %v, want 0700", got)
	}
	if got := mode(t, filepath.Join(dir, "api_token")); got != 0o600 {
		t.Errorf("token file mode = %v, want 0600", got)
	}
}

func TestTokenFileWithWideModeIsTightened(t *testing.T) { // API-05
	skipOnWindows(t)
	dir := t.TempDir()
	loadOrCreateToken(dir)
	os.Chmod(filepath.Join(dir, "api_token"), 0o644)
	os.Chmod(dir, 0o755)

	loadOrCreateToken(dir)

	if mode(t, filepath.Join(dir, "api_token")) != 0o600 || mode(t, dir) != 0o700 {
		t.Error("an existing token file and directory must be made private")
	}
}

func TestBrokenTokenFileIsReplaced(t *testing.T) { // API-05
	for _, content := range []string{"", "curto", "não é hexadecimal " + strings.Repeat("z", 64)} {
		dir := t.TempDir()
		os.WriteFile(filepath.Join(dir, "api_token"), []byte(content), 0o600)

		token, err := loadOrCreateToken(dir)

		if err != nil || !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(token) {
			t.Errorf("content %q: token %q err %v", content, token, err)
		}
	}
}

func TestConfigDirComesFromTheEnvironmentFirst(t *testing.T) { // API-05
	t.Setenv("WHATSAPP_CONFIG_DIR", "/somewhere/else")
	if dir, err := configDir(); err != nil || dir != "/somewhere/else" {
		t.Errorf("configDir = %q, %v", dir, err)
	}
	t.Setenv("WHATSAPP_CONFIG_DIR", "")
	dir, err := configDir()
	if err != nil || filepath.Base(dir) != "whatsapp-mcp" {
		t.Errorf("default configDir = %q, %v", dir, err)
	}
}

func TestValidRequestReachesTheHandler(t *testing.T) { // API-01
	h, reached, token := guarded(t)

	for _, host := range []string{"127.0.0.1:8080", "localhost:8080", "LocalHost:8080"} {
		if rec := call(h, http.MethodPost, host, goodHeaders(token), "{}"); rec.Code != http.StatusOK {
			t.Errorf("host %s -> %d", host, rec.Code)
		}
	}
	if *reached != 3 {
		t.Errorf("handler reached %d times, want 3", *reached)
	}
}

func TestRequestsWithoutAValidTokenAreRefused(t *testing.T) { // API-01
	h, reached, token := guarded(t)
	cases := map[string]map[string]string{
		"no header":      {"Content-Type": "application/json"},
		"wrong token":    {"Authorization": "Bearer " + strings.Repeat("0", 64), "Content-Type": "application/json"},
		"truncated":      {"Authorization": "Bearer " + token[:63], "Content-Type": "application/json"},
		"longer":         {"Authorization": "Bearer " + token + "0", "Content-Type": "application/json"},
		"empty bearer":   {"Authorization": "Bearer ", "Content-Type": "application/json"},
		"wrong scheme":   {"Authorization": "Basic " + token, "Content-Type": "application/json"},
		"token only":     {"Authorization": token, "Content-Type": "application/json"},
		"token in query": {"Content-Type": "application/json"},
	}
	for name, headers := range cases {
		path := "127.0.0.1:8080"
		rec := call(h, http.MethodPost, path, headers, "{}")
		if name == "token in query" {
			req := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8080/api/send?token="+token, strings.NewReader("{}"))
			req.Header.Set("Content-Type", "application/json")
			rec = httptest.NewRecorder()
			h.ServeHTTP(rec, req)
		}
		body, _ := io.ReadAll(rec.Body)
		if rec.Code != http.StatusUnauthorized || !strings.Contains(string(body), `"error_code":"UNAUTHORIZED"`) {
			t.Errorf("%s -> %d %s, want 401 UNAUTHORIZED", name, rec.Code, body)
		}
		if strings.Contains(string(body), token) {
			t.Errorf("%s: the answer leaks the token", name)
		}
	}
	if *reached != 0 {
		t.Errorf("handler reached %d times without a valid token", *reached)
	}
}

func TestForeignHostIsRefused(t *testing.T) { // API-02
	h, reached, token := guarded(t)

	for _, host := range []string{"evil.example:8080", "127.0.0.1:9090", "127.0.0.1", "localhost", "127.0.0.1.evil.example:8080",
		"evil.example", "[::1]:8080", "0.0.0.0:8080", "localhost:8080.evil.example", ""} {
		rec := call(h, http.MethodPost, host, goodHeaders(token), "{}")
		if rec.Code != http.StatusForbidden {
			t.Errorf("host %q -> %d, want 403", host, rec.Code)
		}
	}
	if *reached != 0 {
		t.Error("a foreign Host must never reach the handler, even with a valid token")
	}
}

func TestAnyOriginIsRefused(t *testing.T) { // API-03
	h, reached, token := guarded(t)

	for _, origin := range []string{"https://evil.example", "http://127.0.0.1:8080", "http://localhost:8080", "null"} {
		headers := goodHeaders(token)
		headers["Origin"] = origin
		if rec := call(h, http.MethodPost, "127.0.0.1:8080", headers, "{}"); rec.Code != http.StatusForbidden {
			t.Errorf("Origin %q -> %d, want 403", origin, rec.Code)
		}
	}
	if *reached != 0 {
		t.Error("a request with an Origin header must never reach the handler")
	}
}

func TestPostNeedsJSONContentType(t *testing.T) { // API-04
	h, reached, token := guarded(t)

	for _, ct := range []string{"", "text/plain", "text/plain;charset=UTF-8", "application/x-www-form-urlencoded",
		"multipart/form-data; boundary=x", "application/jsonx", "application/json5", "application/xml"} {
		headers := map[string]string{"Authorization": "Bearer " + token}
		if ct != "" {
			headers["Content-Type"] = ct
		}
		if rec := call(h, http.MethodPost, "127.0.0.1:8080", headers, "{}"); rec.Code != http.StatusUnsupportedMediaType {
			t.Errorf("Content-Type %q -> %d, want 415", ct, rec.Code)
		}
	}
	if *reached != 0 {
		t.Errorf("handler reached %d times with a bad Content-Type", *reached)
	}
	for _, ct := range []string{"application/json", "application/json; charset=utf-8", "Application/JSON"} {
		headers := map[string]string{"Authorization": "Bearer " + token, "Content-Type": ct}
		if rec := call(h, http.MethodPost, "127.0.0.1:8080", headers, "{}"); rec.Code != http.StatusOK {
			t.Errorf("Content-Type %q -> %d, want 200", ct, rec.Code)
		}
	}
}

func TestGetDoesNotNeedAContentType(t *testing.T) { // API-04
	h, reached, token := guarded(t)

	rec := call(h, http.MethodGet, "127.0.0.1:8080", map[string]string{"Authorization": "Bearer " + token}, "")

	if rec.Code != http.StatusOK || *reached != 1 {
		t.Errorf("GET -> %d reached=%d", rec.Code, *reached)
	}
}

func TestTheRealRoutesAreAllBehindTheGuard(t *testing.T) { // API-01, API-03
	token, _ := loadOrCreateToken(t.TempDir())
	mux := newRESTMux(nil, nil, fakeConn{connected: true}, newBridgeState(time.Now), nil) // nil client: reaching send would panic
	h := newAPIGuard(token, guardPort).wrap(mux)

	for _, route := range []string{"/api/send", "/api/download", "/api/group_participants", "/api/status", "/api/anything"} {
		for _, method := range []string{http.MethodGet, http.MethodPost} {
			req := httptest.NewRequest(method, "http://127.0.0.1:8080"+route, strings.NewReader("{}"))
			req.Header.Set("Origin", "https://evil.example")
			req.Header.Set("Content-Type", "text/plain")
			rec := httptest.NewRecorder()
			h.ServeHTTP(rec, req)
			if rec.Code != http.StatusForbidden {
				t.Errorf("%s %s from another origin -> %d, want 403", method, route, rec.Code)
			}
			req = httptest.NewRequest(method, "http://127.0.0.1:8080"+route, strings.NewReader("{}"))
			rec = httptest.NewRecorder()
			h.ServeHTTP(rec, req)
			if rec.Code != http.StatusUnauthorized {
				t.Errorf("%s %s without a token -> %d, want 401", method, route, rec.Code)
			}
		}
	}
	req := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8080/api/status", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Errorf("status with the token -> %d, want 200", rec.Code)
	}
	req = httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8080/api/send", strings.NewReader(`{"message":"x"}`))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Errorf("send with the token and no recipient -> %d, want 400 from the handler", rec.Code)
	}
}

func TestTheTokenIsNeverPrinted(t *testing.T) { // API-07
	realStdout := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w
	dir := t.TempDir()
	token, _ := loadOrCreateToken(dir)
	loadOrCreateToken(dir)
	h := newAPIGuard(token, guardPort).wrap(http.NotFoundHandler())
	call(h, http.MethodPost, "127.0.0.1:8080", map[string]string{"Authorization": "Bearer wrong"}, "{}")
	call(h, http.MethodPost, "evil.example", goodHeaders(token), "{}")
	w.Close()
	os.Stdout = realStdout
	printed, _ := io.ReadAll(r)

	if strings.Contains(string(printed), token) {
		t.Error("the token was printed")
	}
}
