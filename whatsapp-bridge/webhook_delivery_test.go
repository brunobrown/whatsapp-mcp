package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	waLog "go.mau.fi/whatsmeow/util/log"
)

// receiver is a local webhook receiver that records what it gets.
type receiver struct {
	srv      *httptest.Server
	mu       sync.Mutex
	requests []receivedHook
	respond  func(n int, w http.ResponseWriter, r *http.Request)
}

type receivedHook struct {
	header http.Header
	raw    []byte
	body   webhookBody
}

func newReceiver(t *testing.T, respond func(n int, w http.ResponseWriter, r *http.Request)) *receiver {
	t.Helper()
	rc := &receiver{respond: respond}
	rc.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var b webhookBody
		json.Unmarshal(raw, &b)
		rc.mu.Lock()
		rc.requests = append(rc.requests, receivedHook{header: r.Header.Clone(), raw: raw, body: b})
		n := len(rc.requests)
		rc.mu.Unlock()
		if rc.respond != nil {
			rc.respond(n, w, r)
		}
	}))
	t.Cleanup(rc.srv.Close)
	return rc
}

func (rc *receiver) got() []receivedHook {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	return append([]receivedHook(nil), rc.requests...)
}

func (rc *receiver) waitFor(t *testing.T, n int) []receivedHook {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if got := rc.got(); len(got) >= n {
			return got
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("receiver got %d requests, want %d", len(rc.got()), n)
	return nil
}

func fastBackoff(t *testing.T) {
	t.Helper()
	saved := webhookBackoff
	webhookBackoff = []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond, time.Millisecond, time.Millisecond}
	t.Cleanup(func() { webhookBackoff = saved })
}

func dispatcherFor(t *testing.T, logger waLog.Logger, endpoints ...webhookEndpointConfig) *webhookDispatcher {
	t.Helper()
	for i := range endpoints {
		if endpoints[i].Secret == "" {
			endpoints[i].Secret = goodSecret
		}
		if endpoints[i].Name == "" {
			endpoints[i].Name = fmt.Sprintf("ep%d", i)
		}
		if len(endpoints[i].Events) == 0 {
			endpoints[i].Events = []string{"*"}
		}
	}
	d := newWebhookDispatcher(&webhookConfig{Endpoints: endpoints}, logger, time.Now)
	t.Cleanup(func() { d.Close(time.Second) })
	return d
}

func waitStatus(t *testing.T, d *webhookDispatcher, ok func(webhookStatus) bool) webhookStatus {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if st := d.Status(); ok(st) {
			return st
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("status never reached the wanted state: %+v", d.Status())
	return webhookStatus{}
}

func msgEvent(id string) webhookEvent {
	return webhookEvent{Type: eventMessage, Key: id, OccurredAt: time.Now(), ChatJID: "chat@g.us",
		Meta:    map[string]any{"message_id": id, "chat_jid": "chat@g.us", "sender": "5511999990000", "content_length": 11},
		Content: map[string]any{"text": "hello world"}}
}

func TestEventIsPostedWithTheFieldsAndAValidSignature(t *testing.T) { // HOOK-03, HOOK-04
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})

	d.Emit(msgEvent("M1"))

	h := rc.waitFor(t, 1)[0]
	if h.body.EventID == "" || h.body.Type != "message" || h.body.DeliveryAttempt != 1 || h.body.OccurredAt == "" {
		t.Errorf("body = %+v, want event_id, type, occurred_at and delivery_attempt 1", h.body)
	}
	if h.body.Data["message_id"] != "M1" {
		t.Errorf("data = %v, want the message id", h.body.Data)
	}
	if h.header.Get("Content-Type") != "application/json" {
		t.Errorf("Content-Type = %q", h.header.Get("Content-Type"))
	}
	ts := h.header.Get("X-Webhook-Timestamp")
	if ts == "" {
		t.Fatal("no X-Webhook-Timestamp")
	}
	want := webhookSign(goodSecret, ts, h.raw) // independent of the sender's body: the receiver's own bytes
	if got := h.header.Get("X-Webhook-Signature"); got != want || !strings.HasPrefix(got, "sha256=") {
		t.Errorf("signature = %q, want %q (HMAC-SHA256 of timestamp.body)", got, want)
	}
	if webhookSign("other-secret-other-secret-other-secret!", ts, h.raw) == want {
		t.Error("a different secret gives the same signature")
	}
	if webhookSign(goodSecret, ts, append(h.raw, ' ')) == want {
		t.Error("a different body gives the same signature")
	}
}

func TestSignatureMatchesAnIndependentImplementation(t *testing.T) { // HOOK-04
	// HMAC-SHA256("key", "1.{}") computed by Python's hmac module, the same code the README's receiver uses.
	const want = "sha256=1ba6b8171186efc613e8bcc0cbdab2748f24984d7c5a84faa2637afa0e40d224"
	if got := webhookSign("key", "1", []byte("{}")); got != want {
		t.Errorf("signature = %s, want %s", got, want)
	}
}

func TestOnlyRequestedEventsOwnMessagesChatsAndFreshEventsAreDelivered(t *testing.T) { // HOOK-05
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, Events: []string{"message"}, Chats: []string{"chat@g.us"}})

	other := msgEvent("OTHERCHAT")
	other.ChatJID = "other@g.us"
	own := msgEvent("OWN")
	own.FromMe = true
	old := msgEvent("OLD")
	old.OccurredAt = time.Now().Add(-2 * time.Hour)
	vote := webhookEvent{Type: eventPollVote, Key: "V", OccurredAt: time.Now(), ChatJID: "chat@g.us", Meta: map[string]any{}}
	for _, evt := range []webhookEvent{other, own, old, vote, msgEvent("OK")} {
		d.Emit(evt)
	}

	got := rc.waitFor(t, 1)
	time.Sleep(100 * time.Millisecond)
	got = rc.got()
	if len(got) != 1 || got[0].body.Data["message_id"] != "OK" {
		ids := []any{}
		for _, g := range got {
			ids = append(ids, g.body.Data["message_id"])
		}
		t.Errorf("delivered %v, want only OK (other chat, own, old and not-requested events stay out)", ids)
	}
}

func TestOwnMessagesAreDeliveredOnlyWithIncludeOwn(t *testing.T) { // HOOK-05
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, IncludeOwn: true})
	own := msgEvent("OWN")
	own.FromMe = true
	d.Emit(own)
	rc.waitFor(t, 1)
}

func TestWithoutIncludeContentTheEventHasNoText(t *testing.T) { // HOOK-06
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("M1"))
	h := rc.waitFor(t, 1)[0]
	if strings.Contains(string(h.raw), "hello world") {
		t.Errorf("body %s carries the text", h.raw)
	}
	if h.body.Data["content_length"] != float64(11) || h.body.UntrustedContent {
		t.Errorf("data = %v, untrusted = %v; want content_length and no untrusted flag without text", h.body.Data, h.body.UntrustedContent)
	}
}

func TestWithIncludeContentTheTextGoesOutMarkedUntrusted(t *testing.T) { // HOOK-06
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, IncludeContent: true})
	d.Emit(msgEvent("M1"))
	h := rc.waitFor(t, 1)[0]
	if h.body.Data["text"] != "hello world" || !h.body.UntrustedContent || h.body.Notice == "" {
		t.Errorf("body = %+v, want the text, untrusted_content and the notice", h.body)
	}
}

func TestA2xxAnswerCountsAsDeliveredAndRecordsTheTime(t *testing.T) { // HOOK-07
	rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusAccepted) })
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("M1"))
	st := waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Delivered == 1 })
	if st.Endpoints[0].Failed != 0 || st.Endpoints[0].LastSuccessAt == nil || len(rc.got()) != 1 {
		t.Errorf("status = %+v with %d requests, want one delivery, no failure and a success time", st, len(rc.got()))
	}
}

func TestServerErrorsAreRepeatedWithTheSameEventIDAndAGrowingAttempt(t *testing.T) { // HOOK-08
	fastBackoff(t)
	rc := newReceiver(t, func(n int, w http.ResponseWriter, _ *http.Request) {
		if n < 3 {
			w.WriteHeader(http.StatusServiceUnavailable)
		}
	})
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("M1"))
	waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Delivered == 1 })
	got := rc.got()
	if len(got) != 3 {
		t.Fatalf("%d requests, want 3", len(got))
	}
	for i, g := range got {
		if g.body.DeliveryAttempt != i+1 || g.body.EventID != got[0].body.EventID {
			t.Errorf("request %d: attempt %d, event %s; want attempt %d and the same event id", i, g.body.DeliveryAttempt, g.body.EventID, i+1)
		}
	}
}

func TestRetryableAnswersAreRepeatedAndGiveUpAfterSixAttempts(t *testing.T) { // HOOK-08
	fastBackoff(t)
	for _, code := range []int{500, 502, 408, 429} {
		rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { w.WriteHeader(code) })
		d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
		d.Emit(msgEvent("M1"))
		st := waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Failed == 1 })
		if len(rc.got()) != 6 || st.Endpoints[0].Delivered != 0 || !strings.Contains(st.Endpoints[0].LastError, fmt.Sprint(code)) {
			t.Errorf("code %d: %d requests, status %+v; want 6 attempts and a failure naming the code", code, len(rc.got()), st)
		}
	}
}

func TestNetworkErrorsAreRepeated(t *testing.T) { // HOOK-08
	fastBackoff(t)
	l, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := l.Addr().String()
	l.Close() // nothing listens: connection refused
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: "http://" + addr + "/hook"})
	d.Emit(msgEvent("M1"))
	st := waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Failed == 1 })
	if st.Endpoints[0].LastError == "" || strings.Contains(st.Endpoints[0].LastError, addr) {
		t.Errorf("last error = %q, want a reason that does not repeat the address", st.Endpoints[0].LastError)
	}
}

func TestOtherClientErrorsAreNotRepeated(t *testing.T) { // HOOK-09
	fastBackoff(t)
	for _, code := range []int{400, 401, 403, 404, 410, 422} {
		rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { w.WriteHeader(code) })
		d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
		d.Emit(msgEvent("M1"))
		st := waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Failed == 1 })
		time.Sleep(30 * time.Millisecond)
		if len(rc.got()) != 1 || !strings.Contains(st.Endpoints[0].LastError, fmt.Sprint(code)) {
			t.Errorf("code %d: %d requests, error %q; want one attempt and the code recorded", code, len(rc.got()), st.Endpoints[0].LastError)
		}
	}
}

func TestRedirectsAreNotFollowedNorRepeated(t *testing.T) { // HOOK-10
	fastBackoff(t)
	target := newReceiver(t, nil)
	rc := newReceiver(t, func(_ int, w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.srv.URL, http.StatusTemporaryRedirect)
	})
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("M1"))
	st := waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Failed == 1 })
	time.Sleep(30 * time.Millisecond)
	if len(rc.got()) != 1 || len(target.got()) != 0 {
		t.Errorf("origin got %d, redirect target got %d; want 1 and 0", len(rc.got()), len(target.got()))
	}
	if !strings.Contains(st.Endpoints[0].LastError, "redirect") {
		t.Errorf("last error = %q, want it to say redirect", st.Endpoints[0].LastError)
	}
}

func TestFullQueueDropsTheOldestAndASlowEndpointDoesNotHoldUpAnother(t *testing.T) { // HOOK-11
	release := make(chan struct{})
	slow := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { <-release })
	fast := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: slow.srv.URL, Name: "slow"}, webhookEndpointConfig{URL: fast.srv.URL, Name: "fast"})
	t.Cleanup(func() { close(release) })

	d.Emit(msgEvent("FIRST"))
	slow.waitFor(t, 1) // the worker holds FIRST; the queue is empty
	for i := 0; i < webhookQueueLimit+3; i++ {
		d.Emit(msgEvent(fmt.Sprintf("E%d", i)))
	}

	st := d.Status()
	var slowSt webhookEndpointStatus
	for _, e := range st.Endpoints {
		if e.Name == "slow" {
			slowSt = e
		}
	}
	if slowSt.Queued != webhookQueueLimit || slowSt.Dropped != 3 {
		t.Errorf("slow endpoint: queued %d, dropped %d; want %d and 3", slowSt.Queued, slowSt.Dropped, webhookQueueLimit)
	}
	if got := len(fast.waitFor(t, webhookQueueLimit+4)); got < webhookQueueLimit+4 {
		t.Errorf("fast endpoint got %d events while the other was stuck, want all %d", got, webhookQueueLimit+4)
	}
}

func TestHugeContentIsTruncatedAndStaysWithin64KiB(t *testing.T) { // HOOK-12
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, IncludeContent: true})
	big := msgEvent("BIG")
	big.Content = map[string]any{"text": strings.Repeat("x", 200<<10)}
	d.Emit(big)
	h := rc.waitFor(t, 1)[0]
	if !h.body.Truncated || len(h.raw) > webhookMaxBody {
		t.Errorf("truncated = %v, size = %d; want truncated and at most %d bytes", h.body.Truncated, len(h.raw), webhookMaxBody)
	}
	if _, has := h.body.Data["text"]; has || h.body.Data["message_id"] != "BIG" {
		t.Errorf("data = %v, want the ids kept and the text gone", h.body.Data)
	}
}

func TestCloseGivesTheQueueTimeToEmptyAndLaterEventsAreIgnored(t *testing.T) { // HOOK-13
	rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { time.Sleep(20 * time.Millisecond) })
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	for i := 0; i < 5; i++ {
		d.Emit(msgEvent(fmt.Sprintf("E%d", i)))
	}
	d.Close(5 * time.Second)
	if got := len(rc.got()); got != 5 {
		t.Errorf("receiver got %d events at close, want the 5 that were queued", got)
	}
	d.Emit(msgEvent("LATE"))
	time.Sleep(50 * time.Millisecond)
	if got := len(rc.got()); got != 5 {
		t.Errorf("receiver got %d events, want an event after Close to be ignored", got)
	}
}

func TestCloseDoesNotWaitForeverOnAStuckReceiver(t *testing.T) { // HOOK-13
	release := make(chan struct{})
	rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { <-release })
	t.Cleanup(func() { close(release) })
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("M1"))
	rc.waitFor(t, 1)
	started := time.Now()
	d.Close(200 * time.Millisecond)
	if took := time.Since(started); took > 2*time.Second {
		t.Errorf("Close took %v, want it to return after the wait", took)
	}
}

func TestEventIDIsStableForTheSameEventAndDiffersOtherwise(t *testing.T) { // HOOK-21
	a, b := msgEvent("M1"), msgEvent("M1")
	b.OccurredAt = a.OccurredAt.Add(time.Hour)
	if a.id() != b.id() {
		t.Error("the same message gives different event ids")
	}
	c := msgEvent("M2")
	p := msgEvent("M1")
	p.Type = eventMessageEdit
	if a.id() == c.id() || a.id() == p.id() {
		t.Error("different messages or types share an event id")
	}
	if !strings.HasPrefix(a.id(), "evt_") {
		t.Errorf("id = %q", a.id())
	}
}

func TestDestinationPolicyChecksTheAddressReallyConnected(t *testing.T) { // HOOK-02 (SSRF)
	refused := []string{"127.0.0.1", "10.1.2.3", "192.168.0.10", "172.16.5.5", "169.254.169.254", "fe80::1", "::1",
		"::ffff:10.0.0.1", "::ffff:127.0.0.1", "100.64.0.1", "0.0.0.0", "::", "224.0.0.1", "fd00::1"}
	for _, s := range refused {
		if webhookIPAllowed(net.ParseIP(s), false, false) {
			t.Errorf("%s allowed for an https host, want refused", s)
		}
	}
	for _, s := range []string{"8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"} {
		if !webhookIPAllowed(net.ParseIP(s), false, false) {
			t.Errorf("%s refused, want allowed", s)
		}
	}
	if !webhookIPAllowed(net.ParseIP("10.1.2.3"), false, true) || !webhookIPAllowed(net.ParseIP("192.168.0.1"), false, true) {
		t.Error("private networks must be allowed with allow_private_network")
	}
	for _, s := range []string{"0.0.0.0", "::", "224.0.0.1", "127.0.0.1", "::1", "169.254.169.254", "fe80::1", "::ffff:127.0.0.1"} {
		if webhookIPAllowed(net.ParseIP(s), false, true) {
			t.Errorf("%s allowed even with allow_private_network, want refused (loopback, link-local and the like never)", s)
		}
	}
	for _, s := range []string{"8.8.8.8", "100.64.0.1", "fd00::1", "172.16.0.9"} {
		if !webhookIPAllowed(net.ParseIP(s), false, true) {
			t.Errorf("%s refused with allow_private_network, want allowed", s)
		}
	}
	if !webhookIPAllowed(net.ParseIP("127.0.0.1"), true, false) || !webhookIPAllowed(net.ParseIP("::1"), true, false) {
		t.Error("a loopback destination must reach loopback")
	}
	if webhookIPAllowed(net.ParseIP("10.0.0.1"), true, false) || webhookIPAllowed(net.ParseIP("8.8.8.8"), true, true) {
		t.Error("a loopback destination must not reach anything else (a name that resolves elsewhere)")
	}
}

func TestClientRefusesToConnectToAForbiddenAddress(t *testing.T) { // HOOK-02 (SSRF)
	hit := newReceiver(t, nil) // listens on 127.0.0.1
	client := newWebhookClient(webhookEndpointConfig{URL: "https://example.test/hook"})
	_, err := client.Get(hit.srv.URL)
	if err == nil || len(hit.got()) != 0 {
		t.Errorf("err = %v, requests = %d; want the connection refused before any request", err, len(hit.got()))
	}
	allowed := newWebhookClient(webhookEndpointConfig{URL: "https://example.test/hook", AllowPrivateNetwork: true})
	if _, err := allowed.Get(hit.srv.URL); err == nil {
		t.Error("loopback must stay refused for a non-loopback destination even with allow_private_network")
	}
}

type hookLogger struct {
	mu    sync.Mutex
	lines []string
}

func (l *hookLogger) add(msg string, args ...any) {
	l.mu.Lock()
	l.lines = append(l.lines, fmt.Sprintf(msg, args...))
	l.mu.Unlock()
}
func (l *hookLogger) Warnf(m string, a ...any)  { l.add(m, a...) }
func (l *hookLogger) Errorf(m string, a ...any) { l.add(m, a...) }
func (l *hookLogger) Infof(m string, a ...any)  { l.add(m, a...) }
func (l *hookLogger) Debugf(m string, a ...any) { l.add(m, a...) }
func (l *hookLogger) Sub(string) waLog.Logger   { return l }
func (l *hookLogger) joined() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return strings.Join(l.lines, "\n")
}

func TestDeliveryIsLoggedWithoutBodySecretOrFullURL(t *testing.T) { // HOOK-19, HOOK-23
	logger := &hookLogger{}
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, logger, webhookEndpointConfig{URL: rc.srv.URL + "/private/path?token=abc", Name: "local", IncludeContent: true})
	d.Emit(msgEvent("M1"))
	waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Delivered == 1 })
	out := logger.joined()
	for _, want := range []string{"endpoint=local", "type=message", "attempts=1", "http=200", "event=evt_"} {
		if !strings.Contains(out, want) {
			t.Errorf("log %q lacks %q", out, want)
		}
	}
	for _, leak := range []string{goodSecret, "hello world", "/private/path", "token=abc"} {
		if strings.Contains(out, leak) {
			t.Errorf("log leaks %q: %s", leak, out)
		}
	}
	st, _ := json.Marshal(d.Status())
	if strings.Contains(string(st), goodSecret) || strings.Contains(string(st), "private/path") {
		t.Errorf("status leaks the secret or the path: %s", st)
	}
}

func TestStatusReportsCountersWithoutTheURL(t *testing.T) { // HOOK-17
	fastBackoff(t)
	rc := newReceiver(t, func(n int, w http.ResponseWriter, _ *http.Request) {
		if n == 2 {
			w.WriteHeader(http.StatusBadRequest)
		}
	})
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL + "/p", Name: "local"})
	d.Emit(msgEvent("M1"))
	d.Emit(msgEvent("M2"))
	st := waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Delivered == 1 && s.Endpoints[0].Failed == 1 })
	e := st.Endpoints[0]
	if !st.Enabled || e.Name != "local" || e.Queued != 0 || e.LastError != "http 400" || e.LastSuccessAt == nil {
		t.Errorf("status = %+v", st)
	}
}

func TestTestSendsOneSignedPingAndReportsTheAnswerWithoutRepeating(t *testing.T) { // HOOK-18
	fastBackoff(t)
	rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusTeapot) })
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, Name: "local", Events: []string{"message"}})
	res, err := d.Test("local")
	if err != nil || res.Status != http.StatusTeapot || res.Name != "local" {
		t.Fatalf("res, err = %+v, %v; want status 418 for local", res, err)
	}
	time.Sleep(30 * time.Millisecond)
	got := rc.got()
	if len(got) != 1 || got[0].body.Type != "ping" || got[0].body.DeliveryAttempt != 1 {
		t.Errorf("requests = %d; want exactly one ping, not repeated", len(got))
	}
	ts := got[0].header.Get("X-Webhook-Timestamp")
	if got[0].header.Get("X-Webhook-Signature") != webhookSign(goodSecret, ts, got[0].raw) {
		t.Error("the ping is not signed like any other event")
	}
	if st := d.Status().Endpoints[0]; st.Delivered != 0 || st.Failed != 0 {
		t.Errorf("a test must not change the counters: %+v", st)
	}
}

func TestTestOfAnUnknownEndpointIsAnError(t *testing.T) { // HOOK-18
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: newReceiver(t, nil).srv.URL, Name: "local"})
	if _, err := d.Test("nope"); err != errWebhookEndpointNotFound {
		t.Errorf("err = %v, want errWebhookEndpointNotFound", err)
	}
	var none *webhookDispatcher
	if _, err := none.Test("local"); err != errWebhookEndpointNotFound {
		t.Errorf("nil dispatcher err = %v", err)
	}
}

func TestNilAndEmptyDispatchersDoNothing(t *testing.T) { // HOOK-01
	var none *webhookDispatcher
	none.Emit(msgEvent("M1"))
	none.Close(0)
	if st := none.Status(); st.Enabled || st.Endpoints == nil {
		t.Errorf("nil status = %+v, want disabled with an empty list", st)
	}
	off := newWebhookDispatcher(nil, nil, time.Now)
	off.Emit(msgEvent("M1"))
	if st := off.Status(); st.Enabled {
		t.Errorf("status = %+v, want disabled", st)
	}
}
