package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// A durable queue is opt-in per endpoint ("persist_queue": true). Every accepted event is appended to a
// spool file before it is queued and a "done" line is appended once it is delivered, dropped or given up on,
// so an event that was waiting (or in flight) when the bridge stopped is delivered again at the next start:
// at least once, with the same event_id, which the receiver deduplicates.
//
// The spool holds what the event says about itself (type, chat, ids, times), NEVER the text: an event
// recovered after a restart has no Content, even for an endpoint with include_content. The file is 0600.
const (
	webhookSpoolMaxAge = 24 * time.Hour
	// compacted when it holds this many more lines than the queue could, so it cannot grow without limit
	webhookSpoolSlack = 4 * webhookQueueLimit
)

type spoolRecord struct {
	Done       string         `json:"done,omitempty"`
	Type       string         `json:"type,omitempty"`
	Key        string         `json:"key,omitempty"`
	OccurredAt time.Time      `json:"occurred_at,omitempty"`
	ChatJID    string         `json:"chat_jid,omitempty"`
	FromMe     bool           `json:"from_me,omitempty"`
	Meta       map[string]any `json:"meta,omitempty"`
}

func recordOf(evt webhookEvent) spoolRecord {
	return spoolRecord{Type: evt.Type, Key: evt.Key, OccurredAt: evt.OccurredAt, ChatJID: evt.ChatJID, FromMe: evt.FromMe, Meta: evt.Meta}
}

func (r spoolRecord) event() webhookEvent {
	return webhookEvent{Type: r.Type, Key: r.Key, OccurredAt: r.OccurredAt, ChatJID: r.ChatJID, FromMe: r.FromMe, Meta: r.Meta}
}

type webhookSpool struct {
	path  string
	file  *os.File
	lines int
}

// spoolPath names the file after a hash of the endpoint name, so the name never reaches the file system.
func spoolPath(dir, endpoint string) string {
	sum := sha256.Sum256([]byte(endpoint))
	return filepath.Join(dir, "webhook_queue_"+hex.EncodeToString(sum[:6])+".jsonl")
}

// openWebhookSpool reads what is still pending in the file, rewrites the file with only that and returns it
// ready for appending. Events older than webhookSpoolMaxAge are forgotten, and so are all but the newest
// webhookQueueLimit, like a full in-memory queue.
func openWebhookSpool(path string, now time.Time) (*webhookSpool, []webhookEvent, error) {
	var pending []webhookEvent
	if f, err := os.Open(path); err == nil {
		done := map[string]bool{}
		var all []spoolRecord
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
		for sc.Scan() {
			var r spoolRecord
			if json.Unmarshal(sc.Bytes(), &r) != nil {
				continue // a line cut off by a crash
			}
			if r.Done != "" {
				done[r.Done] = true
			} else if r.Type != "" {
				all = append(all, r)
			}
		}
		f.Close()
		for _, r := range all {
			evt := r.event()
			if !done[evt.id()] && now.Sub(evt.OccurredAt) <= webhookSpoolMaxAge {
				pending = append(pending, evt)
			}
		}
		if len(pending) > webhookQueueLimit {
			pending = pending[len(pending)-webhookQueueLimit:]
		}
	} else if !os.IsNotExist(err) {
		return nil, nil, err
	}
	s := &webhookSpool{path: path}
	if err := s.rewrite(pending); err != nil {
		return nil, nil, err
	}
	return s, pending, nil
}

// rewrite replaces the file with the pending events, atomically, and reopens it for appending.
func (s *webhookSpool) rewrite(pending []webhookEvent) error {
	if s.file != nil {
		s.file.Close()
		s.file = nil
	}
	tmp := s.path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(f)
	for _, evt := range pending {
		if err := enc.Encode(recordOf(evt)); err != nil {
			f.Close()
			os.Remove(tmp)
			return err
		}
	}
	if err := f.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, s.path); err != nil {
		os.Remove(tmp)
		return err
	}
	file, err := os.OpenFile(s.path, os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	s.file, s.lines = file, len(pending)
	return nil
}

func (s *webhookSpool) append(r spoolRecord) error {
	if s == nil || s.file == nil {
		return nil
	}
	b, err := json.Marshal(r)
	if err != nil {
		return err
	}
	if _, err := s.file.Write(append(b, '\n')); err != nil {
		return err
	}
	s.lines++
	return s.file.Sync()
}

func (s *webhookSpool) needsCompaction() bool {
	return s != nil && s.lines > webhookQueueLimit+webhookSpoolSlack
}

func (s *webhookSpool) close() {
	if s != nil && s.file != nil {
		s.file.Close()
		s.file = nil
	}
}
