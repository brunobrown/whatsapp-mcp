package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

const validateWebhooksRoute = "/api/config/validate-webhooks"

var configAPISecret = strings.Repeat("s3cret-", 8) // 56 characters, over the minimum

func validateCall(t *testing.T, method, token, body string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	real, _ := loadOrCreateToken(t.TempDir())
	mux := newRESTMux(nil, nil, fakeConn{connected: true}, newBridgeState(time.Now), nil)
	h := newAPIGuard(real, defaultRESTPort).wrap(mux)
	req := httptest.NewRequest(method, "http://127.0.0.1:8080"+validateWebhooksRoute, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	switch token {
	case "real":
		req.Header.Set("Authorization", "Bearer "+real)
	case "wrong":
		req.Header.Set("Authorization", "Bearer "+strings.Repeat("0", 64))
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	var out map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	return rec, out
}

func endpointBody(url string) string {
	return `{"endpoints":[{"name":"n8n","url":"` + url + `","secret":"` + configAPISecret + `","events":["message"]}]}`
}

func TestValidateWebhooksAcceptsAValidFile(t *testing.T) {
	rec, out := validateCall(t, http.MethodPost, "real", endpointBody("https://hooks.example.com/in"))

	if rec.Code != http.StatusOK || out["ok"] != true {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
}

func TestValidateWebhooksAcceptsADisabledFile(t *testing.T) {
	rec, out := validateCall(t, http.MethodPost, "real", `{"enabled":false}`)

	if rec.Code != http.StatusOK || out["ok"] != true {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
}

func TestValidateWebhooksSaysWhyAFileIsRefused(t *testing.T) {
	for body, want := range map[string]string{
		`{"endpoints":[]}`:                                                             "webhooks.json has no endpoints",
		endpointBody("ftp://hooks.example.com/in"):                                     "url must be http (loopback only) or https",
		endpointBody("http://127.0.0.1:8080/api/send"):                                 "url points at the bridge's own API",
		strings.Replace(endpointBody("https://a.example/x"), `"message"`, `"nope"`, 1): `unknown event "nope"`,
	} {
		rec, out := validateCall(t, http.MethodPost, "real", body)

		got, _ := out["error"].(string)
		if rec.Code != http.StatusOK || out["ok"] != false || !strings.Contains(got, want) {
			t.Errorf("%.40s -> %d %s (want %q)", body, rec.Code, rec.Body, want)
		}
	}
}

func TestValidateWebhooksNeverEchoesASecret(t *testing.T) {
	for _, body := range []string{
		endpointBody("ftp://hooks.example.com/in"),                                 // refused for another reason
		`{"endpoints":[{"name":"n","secret":"` + configAPISecret + `"`,             // broken JSON that holds the secret
		strings.Replace(endpointBody("https://a.example/x"), `"name"`, `"nom"`, 1), // unknown field next to the secret
	} {
		rec, _ := validateCall(t, http.MethodPost, "real", body)

		if strings.Contains(rec.Body.String(), configAPISecret) || strings.Contains(rec.Body.String(), "s3cret") {
			t.Errorf("the answer repeats the secret: %s", rec.Body)
		}
	}
}

func TestValidateWebhooksNeedsTheAPIToken(t *testing.T) {
	for _, token := range []string{"none", "wrong"} {
		rec, _ := validateCall(t, http.MethodPost, token, endpointBody("https://hooks.example.com/in"))

		if rec.Code != http.StatusUnauthorized {
			t.Errorf("token %q -> %d", token, rec.Code)
		}
	}
}

func TestValidateWebhooksRefusesABodyOverTheLimit(t *testing.T) {
	rec, _ := validateCall(t, http.MethodPost, "real", `{"endpoints":[],"pad":"`+strings.Repeat("x", webhookConfigMaxBytes)+`"}`)

	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("-> %d", rec.Code)
	}
}

func TestValidateWebhooksOnlyAcceptsPost(t *testing.T) {
	rec, _ := validateCall(t, http.MethodGet, "real", "")

	if rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("-> %d", rec.Code)
	}
}
