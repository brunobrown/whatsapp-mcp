package main

import (
	"net/http"
	"strings"
	"testing"
	"time"

	waLog "go.mau.fi/whatsmeow/util/log"
)

func TestNothingMayFollowTheJSONObject(t *testing.T) { // HOOK-02
	good := goodConfig("")
	for _, tail := range []string{`{"x":1}`, `garbage`, `,`, `]`, good} {
		if _, err := parseWebhookConfig([]byte(good+tail), defaultRESTPort); err == nil {
			t.Errorf("a file with %q after the object was accepted, want refused", tail)
		} else if strings.Contains(err.Error(), "garbage") || strings.Contains(err.Error(), goodSecret) {
			t.Errorf("the error repeats the file: %v", err)
		}
	}
	for _, tail := range []string{"", "\n", "  \n\t\n"} {
		if _, err := parseWebhookConfig([]byte(good+tail), defaultRESTPort); err != nil {
			t.Errorf("trailing whitespace %q refused: %v", tail, err)
		}
	}
}

func TestASyntaxErrorTellsWhereAndNothingElse(t *testing.T) { // HOOK-02, HOOK-23
	_, err := parseWebhookConfig([]byte(`{,}`), defaultRESTPort)
	if err == nil || err.Error() != "webhooks.json is not valid: syntax error at byte 2" {
		t.Errorf("error = %v, want the byte position only", err)
	}
	_, err = parseWebhookConfig([]byte(`{"endpoints":[{"name":"a","secret":"`+goodSecret+`","bogus":1}]}`), defaultRESTPort)
	if err == nil || err.Error() != "webhooks.json is not valid: unknown field" {
		t.Errorf("error = %v, want just \"unknown field\" (the decoder names the field and it could be anything)", err)
	}
}

func TestThePingBodyNeverCarriesTheSecret(t *testing.T) { // HOOK-23
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, Name: "local", IncludeContent: true})
	if _, err := d.Test("local"); err != nil {
		t.Fatal(err)
	}
	h := rc.waitFor(t, 1)[0]
	if strings.Contains(string(h.raw), goodSecret) {
		t.Errorf("the ping body carries the secret: %s", h.raw)
	}
	for k, v := range h.header {
		if strings.Contains(strings.Join(v, ","), goodSecret) {
			t.Errorf("header %s carries the secret", k)
		}
	}
}

func TestThePingReportsItsLatency(t *testing.T) { // HOOK-18
	rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { time.Sleep(60 * time.Millisecond) })
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, Name: "local"})
	res, err := d.Test("local")
	if err != nil || res.LatencyMs < 60 || res.LatencyMs > 3000 {
		t.Errorf("res = %+v, %v; want a latency of at least 60 ms (the receiver took that long)", res, err)
	}
}

func TestTheClosersWaitLetsTheQueueEmpty(t *testing.T) { // HOOK-13
	t.Setenv("WHATSAPP_CONFIG_DIR", t.TempDir())
	rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { time.Sleep(80 * time.Millisecond) })
	dir, _ := configDir()
	writeConfig(t, dir, `{"endpoints":[{"name":"local","url":"`+rc.srv.URL+`","secret":"`+goodSecret+`","events":["*"]}]}`, 0o600)
	store, state := newTestStore(t), newBridgeState(time.Now)
	closeFn := setupWebhooks(store, state, waLog.Noop)
	for _, id := range []string{"A", "B", "C"} {
		store.webhooks.Emit(msgEvent(id))
	}
	closeFn()
	if got := len(rc.got()); got != 3 {
		t.Errorf("%d events delivered when the closer returned, want all 3: it must wait for the queue", got)
	}
}

func TestNoRetentionLoopStartsWithoutRetentionDays(t *testing.T) { // RET-01
	store := newTestStore(t)
	now := time.Now()
	old := seedDownloaded(t, store, "OLD", 400*24*time.Hour, now)
	logger := &recordingLogger{}
	for _, days := range []int{0, -3} {
		startMediaRetention(store, days, logger)
	}
	time.Sleep(150 * time.Millisecond)
	if !exists(old) {
		t.Error("a file was deleted with retention off")
	}
	if len(logger.warnings) != 0 {
		t.Errorf("warnings = %q: a cleanup ran even though retention is off", logger.warnings)
	}
}
