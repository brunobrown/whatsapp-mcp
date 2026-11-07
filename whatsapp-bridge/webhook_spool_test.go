package main

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func durableDispatcher(t *testing.T, dir, url string, extra ...func(*webhookEndpointConfig)) *webhookDispatcher {
	t.Helper()
	cfg := webhookEndpointConfig{Name: "agent", URL: url, Secret: goodSecret, Events: []string{"*"}, IncludeContent: true, PersistQueue: true}
	for _, f := range extra {
		f(&cfg)
	}
	d := newWebhookDispatcherIn(&webhookConfig{Endpoints: []webhookEndpointConfig{cfg}}, nil, time.Now, dir)
	t.Cleanup(func() { d.Close(time.Second) })
	return d
}

func messageEvent(id string, at time.Time) webhookEvent {
	return webhookEvent{Type: eventMessage, Key: "chat@g.us|" + id, OccurredAt: at, ChatJID: "chat@g.us",
		Meta:    map[string]any{"message_id": id, "chat_jid": "chat@g.us", "content_length": 11},
		Content: map[string]any{"text": "SECRET-TEXT"}}
}

func spoolText(t *testing.T, dir string) string {
	t.Helper()
	files, _ := filepath.Glob(filepath.Join(dir, "webhook_queue_*.jsonl"))
	var all strings.Builder
	for _, f := range files {
		b, _ := os.ReadFile(f)
		all.Write(b)
	}
	return all.String()
}

func TestEventsWaitingAtShutdownAreDeliveredAfterARestart(t *testing.T) { // DURQ-01
	fastBackoff(t)
	dir := t.TempDir()
	down := newReceiver(t, func(n int, w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) })
	d1 := durableDispatcher(t, dir, down.srv.URL)
	now := time.Now()
	d1.Emit(messageEvent("M1", now))
	d1.Emit(messageEvent("M2", now))
	down.waitFor(t, 1)
	d1.Close(50 * time.Millisecond) // stops while the endpoint is failing

	up := newReceiver(t, nil)
	d2 := durableDispatcher(t, dir, up.srv.URL)
	got := up.waitFor(t, 2)

	ids := map[any]bool{}
	for _, h := range got {
		ids[h.body.Data["message_id"]] = true
	}
	if !ids["M1"] || !ids["M2"] {
		t.Errorf("recovered %v, want M1 and M2", ids)
	}
	if st := d2.Status().Endpoints[0]; !st.Persistent || st.Recovered != 2 {
		t.Errorf("status = %+v, want persistent with 2 recovered", st)
	}
}

func TestADeliveredEventIsNotDeliveredAgain(t *testing.T) { // DURQ-02
	dir := t.TempDir()
	rc := newReceiver(t, nil)
	d1 := durableDispatcher(t, dir, rc.srv.URL)
	d1.Emit(messageEvent("M1", time.Now()))
	waitStatus(t, d1, func(s webhookStatus) bool { return s.Endpoints[0].Delivered == 1 })
	d1.Close(time.Second)

	again := newReceiver(t, nil)
	d2 := durableDispatcher(t, dir, again.srv.URL)
	time.Sleep(150 * time.Millisecond)

	if n := len(again.got()); n != 0 {
		t.Errorf("%d events delivered again after a restart, want none", n)
	}
	if st := d2.Status().Endpoints[0]; st.Recovered != 0 {
		t.Errorf("recovered = %d, want 0", st.Recovered)
	}
}

func TestAGivenUpEventIsNotRetriedAfterARestart(t *testing.T) { // DURQ-02
	dir := t.TempDir()
	rejecting := newReceiver(t, func(n int, w http.ResponseWriter, r *http.Request) { w.WriteHeader(400) })
	d1 := durableDispatcher(t, dir, rejecting.srv.URL)
	d1.Emit(messageEvent("M1", time.Now()))
	waitStatus(t, d1, func(s webhookStatus) bool { return s.Endpoints[0].Failed == 1 })
	d1.Close(time.Second)

	d2 := durableDispatcher(t, dir, rejecting.srv.URL)
	if st := d2.Status().Endpoints[0]; st.Recovered != 0 || st.Queued != 0 {
		t.Errorf("status = %+v, a 4xx is final and must not come back", st)
	}
}

func TestTheSpoolNeverHoldsTheTextAndARecoveredEventHasNone(t *testing.T) { // DURQ-03
	fastBackoff(t)
	dir := t.TempDir()
	down := newReceiver(t, func(n int, w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) })
	d1 := durableDispatcher(t, dir, down.srv.URL)
	d1.Emit(messageEvent("M1", time.Now()))
	down.waitFor(t, 1)
	d1.Close(50 * time.Millisecond)

	if strings.Contains(spoolText(t, dir), "SECRET-TEXT") {
		t.Fatal("the text of a message reached the spool file")
	}
	up := newReceiver(t, nil)
	durableDispatcher(t, dir, up.srv.URL)
	h := up.waitFor(t, 1)[0]
	if _, has := h.body.Data["text"]; has || strings.Contains(string(h.raw), "SECRET-TEXT") {
		t.Errorf("a recovered event carries text: %s", h.raw)
	}
}

func TestTheSpoolFileIsPrivateAndNamedWithoutTheEndpointName(t *testing.T) { // DURQ-04
	dir := t.TempDir()
	rc := newReceiver(t, nil)
	durableDispatcher(t, dir, rc.srv.URL, func(c *webhookEndpointConfig) { c.Name = "my-private-endpoint" })

	files, _ := filepath.Glob(filepath.Join(dir, "webhook_queue_*.jsonl"))
	if len(files) != 1 || strings.Contains(files[0], "my-private-endpoint") {
		t.Fatalf("spool files = %v", files)
	}
	if info, _ := os.Stat(files[0]); info.Mode().Perm() != 0o600 {
		t.Errorf("mode = %o, want 600", info.Mode().Perm())
	}
}

func TestOldPendingEventsAndBrokenLinesAreForgotten(t *testing.T) { // DURQ-05
	dir := t.TempDir()
	path := spoolPath(dir, "agent")
	now := time.Now()
	fresh, stale := messageEvent("FRESH", now.Add(-time.Hour)), messageEvent("STALE", now.Add(-webhookSpoolMaxAge-time.Hour))
	s, _, err := openWebhookSpool(path, now)
	if err != nil {
		t.Fatal(err)
	}
	s.append(recordOf(stale))
	s.append(recordOf(fresh))
	s.close()
	f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	f.WriteString("{\"type\":\"message\",\"key\":\"cut off by a cra") // a crash in the middle of a write
	f.Close()

	_, pending, err := openWebhookSpool(path, now)

	if err != nil || len(pending) != 1 || pending[0].Key != fresh.Key {
		t.Errorf("pending = %v, err = %v; want only FRESH", pending, err)
	}
}

func TestTheSpoolIsCompactedSoItCannotGrowWithoutLimit(t *testing.T) { // DURQ-06
	dir := t.TempDir()
	rc := newReceiver(t, nil)
	d := durableDispatcher(t, dir, rc.srv.URL)
	n := webhookQueueLimit + webhookSpoolSlack + 50
	for i := 0; i < n; i++ {
		d.Emit(messageEvent(strings.Repeat("x", 3)+string(rune('a'+i%26))+time.Duration(i).String(), time.Now()))
	}
	waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Delivered+s.Endpoints[0].Dropped >= n })

	lines := strings.Count(spoolText(t, dir), "\n")
	if lines > webhookQueueLimit+webhookSpoolSlack+webhookQueueLimit {
		t.Errorf("spool has %d lines after %d events: it was never compacted", lines, n)
	}
}

func TestWithoutPersistQueueNothingIsWritten(t *testing.T) { // DURQ-07
	dir := t.TempDir()
	rc := newReceiver(t, nil)
	d := durableDispatcher(t, dir, rc.srv.URL, func(c *webhookEndpointConfig) { c.PersistQueue = false })
	d.Emit(messageEvent("M1", time.Now()))
	rc.waitFor(t, 1)
	if files, _ := filepath.Glob(filepath.Join(dir, "*")); len(files) != 0 {
		t.Errorf("files = %v, want none", files)
	}
	if d.Status().Endpoints[0].Persistent {
		t.Error("persistent must be false")
	}
}

func TestPersistQueueIsAcceptedByTheConfigFile(t *testing.T) { // DURQ-07
	cfg, err := parseWebhookConfig([]byte(`{"endpoints":[{"name":"a","url":"http://127.0.0.1:9000/h","secret":"`+goodSecret+`","events":["*"],"persist_queue":true}]}`), defaultRESTPort)
	if err != nil || !cfg.Endpoints[0].PersistQueue {
		t.Errorf("cfg = %+v, err = %v", cfg, err)
	}
}

func TestABrokenLineInTheMiddleDoesNotHideTheLinesAfterIt(t *testing.T) { // DURQ-05
	dir := t.TempDir()
	path := spoolPath(dir, "agent")
	now := time.Now()
	s, _, err := openWebhookSpool(path, now)
	if err != nil {
		t.Fatal(err)
	}
	s.append(recordOf(messageEvent("BEFORE", now)))
	s.file.WriteString("{\"type\":\"message\",\"key\":\"cut off\n") // a damaged line with its own newline
	s.append(recordOf(messageEvent("AFTER", now)))
	s.close()

	_, pending, err := openWebhookSpool(path, now)

	if err != nil || len(pending) != 2 || pending[0].Key != "chat@g.us|BEFORE" || pending[1].Key != "chat@g.us|AFTER" {
		t.Errorf("pending = %v (%v), want BEFORE and AFTER", pending, err)
	}
}

func TestMoreThanTheQueueLimitAtStartKeepsTheNewest(t *testing.T) { // DURQ-05
	dir := t.TempDir()
	path := spoolPath(dir, "agent")
	now := time.Now()
	s, _, _ := openWebhookSpool(path, now)
	total := webhookQueueLimit + 25
	for i := 0; i < total; i++ {
		s.lines = 0 // keep the test about reading, not compaction
		s.append(recordOf(messageEvent(time.Duration(i).String(), now)))
	}
	s.close()

	_, pending, _ := openWebhookSpool(path, now)

	want := webhookQueueLimit + 1 // the queue plus the event that may have been in flight
	if len(pending) != want || pending[0].Key != "chat@g.us|"+time.Duration(total-want).String() ||
		pending[len(pending)-1].Key != "chat@g.us|"+time.Duration(total-1).String() {
		t.Errorf("kept %d, first %q, last %q; want the newest %d", len(pending), pending[0].Key, pending[len(pending)-1].Key, want)
	}
}

func TestAnOverflowDropAndACompactionNeverLoseTheEventInFlight(t *testing.T) { // DURQ-02, DURQ-06
	dir := t.TempDir()
	release := make(chan struct{})
	stuck := newReceiver(t, func(n int, w http.ResponseWriter, r *http.Request) { <-release; w.WriteHeader(204) })
	d := durableDispatcher(t, dir, stuck.srv.URL)
	defer close(release)
	now := time.Now()
	total := webhookQueueLimit + webhookSpoolSlack + 200 // enough to overflow the queue and to force a compaction
	d.Emit(messageEvent(time.Duration(0).String(), now))
	stuck.waitFor(t, 1) // the first event is in flight and stays there
	for i := 1; i < total; i++ {
		d.Emit(messageEvent(time.Duration(i).String(), now))
	}
	d.Close(10 * time.Millisecond)

	_, pending, err := openWebhookSpool(spoolPath(dir, "agent"), time.Now())

	if err != nil {
		t.Fatal(err)
	}
	keys := map[string]bool{}
	for _, p := range pending {
		keys[p.Key] = true
	}
	if !keys["chat@g.us|"+time.Duration(0).String()] {
		t.Error("the event in flight when the spool was compacted was lost")
	}
	if keys["chat@g.us|"+time.Duration(1).String()] {
		t.Error("an event dropped because the queue was full came back after a restart")
	}
	if !keys["chat@g.us|"+time.Duration(total-1).String()] {
		t.Error("the newest event was lost")
	}
	if len(pending) > webhookQueueLimit+1 {
		t.Errorf("recovered %d events, more than the queue holds", len(pending))
	}
}
