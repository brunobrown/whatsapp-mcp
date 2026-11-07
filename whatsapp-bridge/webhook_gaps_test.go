package main

import (
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

func TestTheBridgePortCannotBeSpelledAround(t *testing.T) { // HOOK-22
	for _, u := range []string{"http://127.0.0.1:08080/x", "http://localhost:008080/x", "http://[::1]:08080/x", "http://127.0.0.1:8080/x",
		"http://127.0.0.1:99999/x", "http://127.0.0.1:0/x", "http://127.0.0.1:abc/x"} {
		body := `{"endpoints":[{"name":"a","url":"` + u + `","secret":"` + goodSecret + `","events":["message"]}]}`
		if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err == nil {
			t.Errorf("%s accepted, want refused", u)
		}
	}
	body := `{"endpoints":[{"name":"a","url":"http://127.0.0.1:08081/x","secret":"` + goodSecret + `","events":["message"]}]}`
	if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err != nil {
		t.Errorf("another port on loopback must be fine: %v", err)
	}
}

func TestSecretLengthBoundaryAndUnknownFieldWithValidEndpoints(t *testing.T) { // HOOK-02
	mk := func(secret string) string {
		return `{"endpoints":[{"name":"a","url":"http://127.0.0.1:9000/x","secret":"` + secret + `","events":["message"]}]}`
	}
	if _, err := parseWebhookConfig([]byte(mk(strings.Repeat("s", 31))), defaultRESTPort); err == nil {
		t.Error("31-character secret accepted, want refused")
	}
	if _, err := parseWebhookConfig([]byte(mk(strings.Repeat("s", 32))), defaultRESTPort); err != nil {
		t.Errorf("32-character secret refused: %v", err)
	}
	typo := `{"enable":false,"endpoints":[{"name":"a","url":"http://127.0.0.1:9000/x","secret":"` + goodSecret + `","events":["message"]}]}`
	if _, err := parseWebhookConfig([]byte(typo), defaultRESTPort); err == nil {
		t.Error(`a typo like "enable" must be refused, or it silently leaves delivery on`)
	}
}

func TestFileWithGroupOrOtherAccessIsRefusedWhateverTheBit(t *testing.T) { // HOOK-02
	for _, mode := range []os.FileMode{0o640, 0o604, 0o660, 0o666, 0o644} {
		dir := t.TempDir()
		writeConfig(t, dir, goodConfig(""), mode)
		if st := loadWebhooks(dir, nil).Status(); st.Enabled || st.Error == "" {
			t.Errorf("mode %o: status %+v, want refused", mode, st)
		}
	}
}

func TestTimestampHeaderIsSecondsNow(t *testing.T) { // HOOK-04, HOOK-24
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("M1"))
	h := rc.waitFor(t, 1)[0]
	ts, err := strconv.ParseInt(h.header.Get("X-Webhook-Timestamp"), 10, 64)
	if err != nil {
		t.Fatalf("timestamp %q is not an integer", h.header.Get("X-Webhook-Timestamp"))
	}
	if diff := time.Now().Unix() - ts; diff < -5 || diff > 5 {
		t.Errorf("timestamp is %d s off, want seconds since the epoch, now", diff)
	}
}

func TestOverflowDropsTheOldestAndKeepsTheNewest(t *testing.T) { // HOOK-11
	if webhookQueueLimit != 1000 {
		t.Fatalf("queue limit = %d, want 1000", webhookQueueLimit)
	}
	release := make(chan struct{})
	rc := newReceiver(t, func(n int, w http.ResponseWriter, _ *http.Request) {
		if n == 1 {
			<-release
		}
	})
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("FIRST"))
	rc.waitFor(t, 1)
	total := webhookQueueLimit + 3
	for i := 0; i < total; i++ {
		d.Emit(msgEvent(fmt.Sprintf("E%d", i)))
	}
	close(release)
	got := rc.waitFor(t, 1+webhookQueueLimit)
	if id := got[1].body.Data["message_id"]; id != "E3" {
		t.Errorf("first queued event delivered = %v, want E3 (E0, E1, E2 were the oldest and are dropped)", id)
	}
	if id := got[len(got)-1].body.Data["message_id"]; id != fmt.Sprintf("E%d", total-1) {
		t.Errorf("last = %v, want the newest", id)
	}
	if st := d.Status().Endpoints[0]; st.Dropped != 3 {
		t.Errorf("dropped = %d, want 3", st.Dropped)
	}
}

func TestOlderReactionIsNotDelivered(t *testing.T) { // HOOK-15
	store, withText, _ := hookedStore(t)
	seedText(t, store, "M1", "5511999990000", "hi", false)
	newer := time.Now().Add(2 * time.Second)
	handleMessageWith(decryptors{}, "", nil, store, pollEvent("chat@g.us", alice, "R2", reactionMessage("M1", "👍", newer.UnixMilli()), time.Now()), waLog.Noop)
	handleMessageWith(decryptors{}, "", nil, store, pollEvent("chat@g.us", alice, "R1", reactionMessage("M1", "❤️", time.Now().UnixMilli()), time.Now()), waLog.Noop)

	withText.waitFor(t, 1)
	time.Sleep(60 * time.Millisecond)
	if got := withText.got(); len(got) != 1 || got[0].body.Data["emoji"] != "👍" {
		t.Errorf("delivered %d reactions, want only the newest (the older one was not stored)", len(got))
	}
}

func TestWorkersEndOnCloseAndLateEventsAreNotQueued(t *testing.T) { // HOOK-13
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Close(time.Second)
	done := make(chan struct{})
	go func() { d.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("the workers did not end after Close")
	}
	d.Emit(msgEvent("LATE"))
	if q := d.Status().Endpoints[0].Queued; q != 0 {
		t.Errorf("queued = %d after Close, want 0", q)
	}
}

func TestProductionTimingAndTransport(t *testing.T) { // HOOK-08
	want := []time.Duration{time.Second, 4 * time.Second, 16 * time.Second, time.Minute, 5 * time.Minute}
	if fmt.Sprint(webhookBackoff) != fmt.Sprint(want) {
		t.Errorf("backoff = %v, want %v", webhookBackoff, want)
	}
	if webhookTimeout != 5*time.Second || webhookMaxBody != 64<<10 || webhookMinSecret != 32 {
		t.Errorf("timeout %v, max body %d, min secret %d", webhookTimeout, webhookMaxBody, webhookMinSecret)
	}
	client := newWebhookClient(webhookEndpointConfig{URL: "https://example.com/x"})
	if client.Timeout != 5*time.Second {
		t.Errorf("client timeout = %v, want 5s", client.Timeout)
	}
	if tr, ok := client.Transport.(*http.Transport); !ok || tr.Proxy != nil {
		t.Error("the client must ignore the proxy of the environment: the address check would see the proxy, not the destination")
	}
}

func TestFieldFormatsAndChatFilterByNumber(t *testing.T) { // HOOK-03, HOOK-05, HOOK-06
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, Chats: []string{"120363000000000000"}})
	in := pollEvent("120363000000000000@g.us", "5511999990000@s.whatsapp.net", "M1", &waE2E.Message{Conversation: proto.String("héllo")}, time.Now())
	out := pollEvent("120363999999999999@g.us", "5511999990000@s.whatsapp.net", "M2", &waE2E.Message{Conversation: proto.String("x")}, time.Now())
	d.Emit(messageWebhookEvent(in, "héllo", "", ""))
	d.Emit(messageWebhookEvent(out, "x", "", ""))

	got := rc.waitFor(t, 1)
	time.Sleep(60 * time.Millisecond)
	got = rc.got()
	if len(got) != 1 {
		t.Fatalf("delivered %d, want only the chat named by its number", len(got))
	}
	h := got[0].body
	if h.Data["sender"] != "5511999990000" || h.Data["content_length"] != float64(5) {
		t.Errorf("data = %v, want sender as the user part and content_length 5 (runes, not the 6 bytes)", h.Data)
	}
	if _, err := time.Parse(time.RFC3339Nano, h.OccurredAt); err != nil || !strings.HasSuffix(h.OccurredAt, "Z") {
		t.Errorf("occurred_at = %q, want RFC 3339 in UTC", h.OccurredAt)
	}
}

func TestMoreAddressesTheDestinationPolicyRefuses(t *testing.T) { // HOOK-02 (SSRF)
	for _, s := range []string{"0.1.2.3", "0.255.255.255"} {
		if webhookIPAllowed(net.ParseIP(s), false, false) {
			t.Errorf("%s allowed, want refused (0.0.0.0/8)", s)
		}
	}
}

func TestClientEventsReachTheWebhooks(t *testing.T) { // HOOK-16, HOOK-03
	store := newTestStore(t)
	rc := newReceiver(t, nil)
	store.webhooks = dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	synced := make(chan struct{}, 1)
	handle := clientEventHandler(nil, store, func() { synced <- struct{}{} }, waLog.Noop)

	handle(&events.Connected{})
	handle(&events.Disconnected{})
	handle(&events.LoggedOut{})
	handle(pollEvent("chat@g.us", alice, "M1", &waE2E.Message{Conversation: proto.String("hi")}, time.Now()))

	got := rc.waitFor(t, 4)
	states := []string{}
	for _, g := range got {
		if g.body.Type == "connection" {
			states = append(states, fmt.Sprint(g.body.Data["state"]))
		}
	}
	if strings.Join(states, ",") != "connected,disconnected,logged_out" {
		t.Errorf("connection states = %v", states)
	}
	select {
	case <-synced:
	case <-time.After(2 * time.Second):
		t.Error("the identity sync was not started on connect")
	}
	if row := readRow(t, store, "M1"); row.content != "hi" {
		t.Errorf("the message was not stored: %+v", row)
	}
}

func TestSetupWebhooksWiresTheStatusAndTheCloser(t *testing.T) { // HOOK-13, HOOK-17
	dir := t.TempDir()
	t.Setenv("WHATSAPP_CONFIG_DIR", dir)
	rc := newReceiver(t, nil)
	writeConfig(t, dir, `{"endpoints":[{"name":"local","url":"`+rc.srv.URL+`","secret":"`+goodSecret+`","events":["*"]}]}`, 0o600)
	store, state := newTestStore(t), newBridgeState(time.Now)

	closeFn := setupWebhooks(store, state, waLog.Noop)

	if st := state.webhooks(); !st.Enabled || len(st.Endpoints) != 1 {
		t.Fatalf("status = %+v, want the endpoint wired into the bridge status", st)
	}
	store.webhooks.Emit(msgEvent("M1"))
	rc.waitFor(t, 1)
	closeFn()
	done := make(chan struct{})
	go func() { store.webhooks.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Error("the closer returned by setupWebhooks did not close the dispatcher")
	}
}

func TestSetupWebhooksWithoutAFileIsOff(t *testing.T) { // HOOK-01
	t.Setenv("WHATSAPP_CONFIG_DIR", t.TempDir())
	store, state := newTestStore(t), newBridgeState(time.Now)
	closeFn := setupWebhooks(store, state, waLog.Noop)
	defer closeFn()
	if st := state.webhooks(); st.Enabled || st.Error != "" {
		t.Errorf("status = %+v, want off without error", st)
	}
}

func TestTheRESTMuxServesTheWebhookTestAndTheStatus(t *testing.T) { // HOOK-17, HOOK-18
	store, withText, _ := hookedStore(t)
	state := newBridgeState(time.Now)
	state.webhooks = store.webhooks.Status
	mux := newRESTMux(nil, store, fakeConn{}, state, waLog.Noop)

	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/webhooks/test", strings.NewReader(`{"name":"text"}`)))
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"status":200`) {
		t.Errorf("test route = %d %s", rec.Code, rec.Body.String())
	}
	oneOf(t, withText, "ping")

	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/status", nil))
	if !strings.Contains(rec.Body.String(), `"webhooks":{"enabled":true`) {
		t.Errorf("status = %s, want the webhooks of the dispatcher", rec.Body.String())
	}
}
