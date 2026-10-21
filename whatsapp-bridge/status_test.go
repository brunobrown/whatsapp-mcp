package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/types/events"
)

type fakeConn struct {
	connected, loggedIn bool
	phone               string
}

func (f fakeConn) IsConnected() bool { return f.connected }
func (f fakeConn) IsLoggedIn() bool  { return f.loggedIn }
func (f fakeConn) Phone() string     { return f.phone }

type clock struct{ t time.Time }

func (c *clock) now() time.Time          { return c.t }
func (c *clock) advance(d time.Duration) { c.t = c.t.Add(d) }

func getStatus(t *testing.T, src connectionSource, state *bridgeState) map[string]any {
	t.Helper()
	rec := httptest.NewRecorder()
	handleStatus(src, state, "abc123")(rec, httptest.NewRequest(http.MethodGet, "/api/status", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("body is not JSON: %v", err)
	}
	return out
}

func TestStatusReportsEveryField(t *testing.T) { // STAT-01
	c := &clock{time.Date(2026, 10, 21, 10, 0, 0, 0, time.UTC)}
	state := newBridgeState(c.now)
	c.advance(90 * time.Second)

	out := getStatus(t, fakeConn{connected: true, loggedIn: true, phone: "5581999990000"}, state)

	want := map[string]any{
		"connected": true, "logged_in": true, "phone": "5581999990000", "version": "abc123",
		"uptime_seconds": float64(90), "pending_media_retries": float64(0),
		"started_at": "2026-10-21T10:00:00Z",
	}
	for key, value := range want {
		if out[key] != value {
			t.Errorf("%s = %v, want %v", key, out[key], value)
		}
	}
	for _, key := range []string{"last_event_at", "last_message_at", "last_connected_at", "last_disconnected_at"} {
		if v, present := out[key]; !present || v != nil {
			t.Errorf("%s = %v (present=%v), want null before any such event", key, v, present)
		}
	}
}

func TestStateRecordsEachKindOfEvent(t *testing.T) { // STAT-02
	c := &clock{time.Date(2026, 10, 21, 10, 0, 0, 0, time.UTC)}
	state := newBridgeState(c.now)
	src := fakeConn{connected: true, loggedIn: true}

	c.advance(time.Minute)
	state.record(&events.Connected{})
	c.advance(time.Minute)
	state.record(&events.Message{})
	c.advance(time.Minute)
	state.record(&events.PushName{}) // um evento qualquer só move last_event_at
	out := getStatus(t, src, state)
	if out["last_connected_at"] != "2026-10-21T10:01:00Z" {
		t.Errorf("last_connected_at = %v", out["last_connected_at"])
	}
	if out["last_message_at"] != "2026-10-21T10:02:00Z" {
		t.Errorf("last_message_at = %v", out["last_message_at"])
	}
	if out["last_event_at"] != "2026-10-21T10:03:00Z" {
		t.Errorf("last_event_at = %v", out["last_event_at"])
	}
	if out["last_disconnected_at"] != nil {
		t.Errorf("last_disconnected_at = %v, want null", out["last_disconnected_at"])
	}

	c.advance(time.Minute)
	state.record(&events.Disconnected{})
	c.advance(time.Minute)
	state.record(&events.LoggedOut{})
	out = getStatus(t, src, state)
	if out["last_disconnected_at"] != "2026-10-21T10:05:00Z" {
		t.Errorf("last_disconnected_at = %v", out["last_disconnected_at"])
	}
	if out["last_message_at"] != "2026-10-21T10:02:00Z" {
		t.Errorf("a disconnect must not move last_message_at: %v", out["last_message_at"])
	}
}

func TestStatusCountsPendingMediaRetries(t *testing.T) { // STAT-03
	mediaRetryMu.Lock()
	mediaRetryPending["A"] = &mediaRetryPendingEntry{}
	mediaRetryPending["B"] = &mediaRetryPendingEntry{}
	mediaRetryMu.Unlock()
	t.Cleanup(func() {
		mediaRetryMu.Lock()
		delete(mediaRetryPending, "A")
		delete(mediaRetryPending, "B")
		mediaRetryMu.Unlock()
	})

	out := getStatus(t, fakeConn{}, newBridgeState(time.Now))

	if out["pending_media_retries"] != float64(2) {
		t.Errorf("pending_media_retries = %v, want 2", out["pending_media_retries"])
	}
}

func TestStatusRejectsOtherMethods(t *testing.T) { // STAT-04
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodDelete} {
		rec := httptest.NewRecorder()
		handleStatus(fakeConn{}, newBridgeState(time.Now), "v")(rec, httptest.NewRequest(method, "/api/status", nil))
		if rec.Code != http.StatusMethodNotAllowed {
			t.Errorf("%s -> %d, want 405", method, rec.Code)
		}
	}
}

func TestStatusWhenDisconnectedKeepsTheTimes(t *testing.T) { // STAT-05
	c := &clock{time.Date(2026, 10, 21, 10, 0, 0, 0, time.UTC)}
	state := newBridgeState(c.now)
	c.advance(time.Minute)
	state.record(&events.Message{})
	c.advance(time.Minute)
	state.record(&events.Disconnected{})

	out := getStatus(t, fakeConn{connected: false, loggedIn: true}, state)

	if out["connected"] != false {
		t.Errorf("connected = %v, want false", out["connected"])
	}
	if out["last_message_at"] != "2026-10-21T10:01:00Z" || out["last_disconnected_at"] != "2026-10-21T10:02:00Z" ||
		out["last_event_at"] != "2026-10-21T10:02:00Z" {
		t.Errorf("times lost: %v", out)
	}
}
