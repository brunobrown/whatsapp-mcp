package main

import (
	"encoding/json"
	"net/http"
	"runtime/debug"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types/events"
)

// connectionSource is what the status endpoint asks of the WhatsApp client.
type connectionSource interface {
	IsConnected() bool
	IsLoggedIn() bool
	Phone() string
}

type clientSource struct{ client *whatsmeow.Client }

func (c clientSource) IsConnected() bool { return c.client.IsConnected() }
func (c clientSource) IsLoggedIn() bool  { return c.client.IsLoggedIn() }
func (c clientSource) Phone() string {
	if c.client.Store == nil || c.client.Store.ID == nil {
		return ""
	}
	return c.client.Store.ID.User
}

// bridgeState remembers when things last happened, from the events the client delivers.
type bridgeState struct {
	mu               sync.Mutex
	now              func() time.Time
	startedAt        time.Time
	lastEvent        time.Time
	lastMessage      time.Time
	lastConnected    time.Time
	lastDisconnected time.Time
}

func newBridgeState(now func() time.Time) *bridgeState {
	return &bridgeState{now: now, startedAt: now()}
}

// record notes an event of the client. Call it first for every event.
func (s *bridgeState) record(evt interface{}) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	s.lastEvent = now
	switch evt.(type) {
	case *events.Message:
		s.lastMessage = now
	case *events.Connected:
		s.lastConnected = now
	case *events.Disconnected, *events.LoggedOut:
		s.lastDisconnected = now
	}
}

// pendingMediaRetries is how many media retry requests wait for an answer from WhatsApp.
func pendingMediaRetries() int {
	mediaRetryMu.Lock()
	defer mediaRetryMu.Unlock()
	return len(mediaRetryPending)
}

// bridgeVersion is the revision the binary was built from, or "dev" when the build has none.
func bridgeVersion() string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "dev"
	}
	for _, setting := range info.Settings {
		if setting.Key == "vcs.revision" && setting.Value != "" {
			return setting.Value
		}
	}
	return "dev"
}

type statusResponse struct {
	Connected           bool       `json:"connected"`
	LoggedIn            bool       `json:"logged_in"`
	Phone               string     `json:"phone"`
	Version             string     `json:"version"`
	StartedAt           time.Time  `json:"started_at"`
	UptimeSeconds       int64      `json:"uptime_seconds"`
	LastEventAt         *time.Time `json:"last_event_at"`
	LastMessageAt       *time.Time `json:"last_message_at"`
	LastConnectedAt     *time.Time `json:"last_connected_at"`
	LastDisconnectedAt  *time.Time `json:"last_disconnected_at"`
	PendingMediaRetries int        `json:"pending_media_retries"`
}

func orNil(t time.Time) *time.Time {
	if t.IsZero() {
		return nil
	}
	return &t
}

func (s *bridgeState) snapshot(src connectionSource, pending int, version string) statusResponse {
	s.mu.Lock()
	defer s.mu.Unlock()
	return statusResponse{
		Connected:           src.IsConnected(),
		LoggedIn:            src.IsLoggedIn(),
		Phone:               src.Phone(),
		Version:             version,
		StartedAt:           s.startedAt,
		UptimeSeconds:       int64(s.now().Sub(s.startedAt).Seconds()),
		LastEventAt:         orNil(s.lastEvent),
		LastMessageAt:       orNil(s.lastMessage),
		LastConnectedAt:     orNil(s.lastConnected),
		LastDisconnectedAt:  orNil(s.lastDisconnected),
		PendingMediaRetries: pending,
	}
}

// handleStatus answers GET /api/status.
func handleStatus(src connectionSource, state *bridgeState, version string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(state.snapshot(src, pendingMediaRetries(), version))
	}
}
