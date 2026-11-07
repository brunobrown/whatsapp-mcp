package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestDisabledWithEndpointsStaysOff(t *testing.T) { // HOOK-01
	body := `{"enabled":false,"endpoints":[{"name":"a","url":"http://127.0.0.1:9000/x","secret":"` + goodSecret + `","events":["*"]}]}`
	cfg, err := parseWebhookConfig([]byte(body), defaultRESTPort)
	if err != nil || cfg.Enabled() {
		t.Fatalf("cfg, err = %+v, %v; want a disabled config even though it lists endpoints", cfg, err)
	}
	dir := t.TempDir()
	writeConfig(t, dir, body, 0o600)
	d := loadWebhooks(dir, nil)
	defer d.Close(0)
	if st := d.Status(); st.Enabled || len(st.Endpoints) != 0 {
		t.Errorf("status = %+v, want off: \"enabled\": false turns everything off", st)
	}
}

func TestEveryAttemptIsSignedNotJustTheFirst(t *testing.T) { // HOOK-04
	fastBackoff(t)
	rc := newReceiver(t, func(n int, w http.ResponseWriter, _ *http.Request) {
		if n < 3 {
			w.WriteHeader(http.StatusServiceUnavailable)
		}
	})
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("M1"))
	waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Delivered == 1 })
	for i, h := range rc.got() {
		ts := h.header.Get("X-Webhook-Timestamp")
		if got, want := h.header.Get("X-Webhook-Signature"), webhookSign(goodSecret, ts, h.raw); got != want {
			t.Errorf("attempt %d: signature %q does not match its own body (want %q)", i+1, got, want)
		}
	}
}

func TestConfigErrorsNeverRepeatASecretOrAURL(t *testing.T) { // HOOK-02, HOOK-17, HOOK-23
	const shortSecret, urlSecret = "shortsecret", "supersecretpw"
	cases := map[string]string{
		"short secret":       `{"endpoints":[{"name":"a","url":"http://127.0.0.1:9000/x","secret":"` + shortSecret + `","events":["message"]}]}`,
		"placeholder secret": `{"endpoints":[{"name":"a","url":"http://127.0.0.1:9000/x","secret":"` + webhookExampleSecret + `","events":["message"]}]}`,
		"credentials in url": `{"endpoints":[{"name":"a","url":"https://user:` + urlSecret + `@example.com/p?token=abc","secret":"` + goodSecret + `","events":["message"]}]}`,
		"plain http, a path": `{"endpoints":[{"name":"a","url":"http://example.com/private/path?token=abc","secret":"` + goodSecret + `","events":["message"]}]}`,
		"bad port":           `{"endpoints":[{"name":"a","url":"http://127.0.0.1:99999/private/path?token=abc","secret":"` + goodSecret + `","events":["message"]}]}`,
		"unknown event":      `{"endpoints":[{"name":"a","url":"https://example.com/x","secret":"` + goodSecret + `","events":["nope"]}]}`,
		"syntax error":       `{"endpoints":[{"name":"a","secret":"` + goodSecret + `",,]}`,
		"unknown field":      `{"endpoints":[{"name":"a","url":"https://example.com/x","secret":"` + goodSecret + `","events":["message"],"extra":"` + goodSecret + `"}]}`,
	}
	for name, body := range cases {
		_, err := parseWebhookConfig([]byte(body), defaultRESTPort)
		if err == nil {
			t.Errorf("%s: accepted", name)
			continue
		}
		for _, leak := range []string{shortSecret, goodSecret, webhookExampleSecret, urlSecret, "token=abc", "private/path", "example.com"} {
			if strings.Contains(err.Error(), leak) {
				t.Errorf("%s: the error repeats %q: %v", name, leak, err)
			}
		}
		dir := t.TempDir()
		writeConfig(t, dir, body, 0o600)
		st := loadWebhooks(dir, nil).Status()
		for _, leak := range []string{shortSecret, goodSecret, urlSecret, "token=abc", "private/path"} {
			if strings.Contains(st.Error, leak) {
				t.Errorf("%s: the status error repeats %q: %s", name, leak, st.Error)
			}
		}
	}
}

func TestChatsFilterMatchesTheWholeJIDOrTheWholeNumberOnly(t *testing.T) { // HOOK-05
	e := &webhookEndpoint{cfg: webhookEndpointConfig{Chats: []string{"5511", "chat@g", "120363000000000000"}}, events: map[string]bool{"*": true}}
	for _, chat := range []string{"5511999990000@s.whatsapp.net", "chat@g.us", "1203630000000000001@g.us", "x120363000000000000@g.us", "551@s.whatsapp.net"} {
		if e.accepts(webhookEvent{Type: eventMessage, ChatJID: chat}) {
			t.Errorf("%s passes a filter that names only %v: a prefix or substring must not match", chat, e.cfg.Chats)
		}
	}
	for _, chat := range []string{"5511@s.whatsapp.net", "120363000000000000@g.us", "chat@g"} {
		if !e.accepts(webhookEvent{Type: eventMessage, ChatJID: chat}) {
			t.Errorf("%s is named by the filter and must pass", chat)
		}
	}
}

func TestNetworkErrorsAreRepeatedAndThenDelivered(t *testing.T) { // HOOK-08
	fastBackoff(t)
	rc := newReceiver(t, func(n int, w http.ResponseWriter, _ *http.Request) {
		if n < 3 {
			conn, _, _ := w.(http.Hijacker).Hijack()
			conn.Close() // the connection drops without an answer: a network error for the sender
		}
	})
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("M1"))
	waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Delivered == 1 })
	got := rc.got()
	if len(got) != 3 || got[2].body.DeliveryAttempt != 3 {
		t.Errorf("%d requests, want 3 (a network error is repeated)", len(got))
	}
}

func TestAnUntrustedCertificateIsRefused(t *testing.T) { // HOOK-02 (transport)
	fastBackoff(t)
	hits := 0
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { hits++ })) // self-signed
	defer srv.Close()
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: srv.URL})
	d.Emit(msgEvent("M1"))
	st := waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Failed == 1 })
	if hits != 0 || st.Endpoints[0].Delivered != 0 {
		t.Errorf("hits = %d, delivered = %d; want the self-signed certificate refused before any request", hits, st.Endpoints[0].Delivered)
	}
	if tr, ok := newWebhookClient(webhookEndpointConfig{URL: srv.URL}).Transport.(*http.Transport); !ok || (tr.TLSClientConfig != nil && tr.TLSClientConfig.InsecureSkipVerify) {
		t.Error("the client must verify certificates")
	}
}

func TestHostsThatOnlyLookLikeLocalhostAndBadPortsAreRefused(t *testing.T) { // HOOK-02
	for _, u := range []string{"http://evillocalhost/x", "http://localhost.evil.com/x", "http://notlocalhost:9000/x", "https://:443/x",
		"http://127.0.0.1:65536/x", "http://127.0.0.1:-1/x"} {
		body := `{"endpoints":[{"name":"a","url":"` + u + `","secret":"` + goodSecret + `","events":["message"]}]}`
		if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err == nil {
			t.Errorf("%s accepted, want refused", u)
		}
	}
	for _, u := range []string{"http://LOCALHOST:9000/x", "http://127.0.0.1:65535/x"} {
		body := `{"endpoints":[{"name":"a","url":"` + u + `","secret":"` + goodSecret + `","events":["message"]}]}`
		if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err != nil {
			t.Errorf("%s refused: %v", u, err)
		}
	}
}

func TestEveryRedirectCodeIsAFailureNotADelivery(t *testing.T) { // HOOK-10
	fastBackoff(t)
	for _, code := range []int{300, 301, 302, 303, 304, 307, 308} {
		rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Location", "http://127.0.0.1:1/elsewhere")
			w.WriteHeader(code)
		})
		d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
		d.Emit(msgEvent("M1"))
		st := waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Failed+s.Endpoints[0].Delivered == 1 })
		if st.Endpoints[0].Delivered != 0 || st.Endpoints[0].Failed != 1 {
			t.Errorf("HTTP %d: status %+v, want a failure and no delivery", code, st.Endpoints[0])
		}
	}
}

func TestTheAgeLimitIsAnHourExactlyAndIdIsWellFormed(t *testing.T) { // HOOK-05, HOOK-21
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	fresh, stale := msgEvent("FRESH"), msgEvent("STALE")
	fresh.OccurredAt = time.Now().Add(-59 * time.Minute)
	stale.OccurredAt = time.Now().Add(-61 * time.Minute)
	zero := msgEvent("ZERO")
	zero.OccurredAt = time.Time{} // an event without a time is treated as now
	d.Emit(stale)
	d.Emit(fresh)
	d.Emit(zero)
	got := rc.waitFor(t, 2)
	time.Sleep(60 * time.Millisecond)
	got = rc.got()
	ids := map[any]bool{}
	for _, g := range got {
		ids[g.body.Data["message_id"]] = true
		if len(g.body.EventID) != len("evt_")+32 || !strings.HasPrefix(g.body.EventID, "evt_") {
			t.Errorf("event id %q is not evt_ plus 32 hex digits", g.body.EventID)
		}
		at, err := time.Parse(time.RFC3339Nano, g.body.OccurredAt)
		if age := time.Since(at); err != nil || age < -time.Minute || age > 61*time.Minute {
			t.Errorf("occurred_at %q is not a time within the last hour (an event without a time is stamped now)", g.body.OccurredAt)
		}
	}
	if len(got) != 2 || !ids["FRESH"] || !ids["ZERO"] || ids["STALE"] {
		t.Errorf("delivered %v, want FRESH (59 min) and ZERO (no time) but not STALE (61 min)", ids)
	}
}
