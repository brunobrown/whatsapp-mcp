package main

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

func TestAnonymousVoteIDDoesNotDependOnTheVoterThroughTheRealPath(t *testing.T) { // HOOK-23 (wiring)
	store, withText, _ := hookedStore(t)
	now := time.Now()
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "POLL1", creationMessage("Q", []string{"A", "B"}, 0), now), waLog.Noop) // anonymous
	vote := func(voter, msgID string, ms int64) {
		handleMessageWith(decryptors{vote: decryptTo(sha("A"))}, "", nil, store,
			pollEvent("chat@g.us", voter, msgID, voteUpdate("POLL1", ms), now), waLog.Noop)
	}
	// two voters, the same vote message id and instant: only the voter differs
	vote("5511111111111@s.whatsapp.net", "VM1", now.Add(time.Second).UnixMilli())
	vote("5522222222222@s.whatsapp.net", "VM1", now.Add(time.Second).UnixMilli())
	// the first voter again, another vote message
	vote("5511111111111@s.whatsapp.net", "VM2", now.Add(2*time.Second).UnixMilli())

	var ids []string
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && len(ids) < 3 {
		ids = ids[:0]
		for _, h := range withText.got() {
			if h.body.Type == "poll_vote" {
				ids = append(ids, h.body.EventID)
			}
		}
		time.Sleep(5 * time.Millisecond)
	}
	if len(ids) != 3 {
		t.Fatalf("got %d poll_vote events, want 3", len(ids))
	}
	if ids[0] != ids[1] {
		t.Error("the event id changes with the voter: a receiver could hash candidate numbers and unmask an anonymous vote")
	}
	if ids[0] == ids[2] {
		t.Error("two different vote messages share an event id")
	}
}

func TestTheClientRefusesPrivateAddressesByDefaultAndTheFlagLiftsOnlyThat(t *testing.T) { // HOOK-02 (SSRF, client level)
	try := func(allow bool, addr string) string {
		client := newWebhookClient(webhookEndpointConfig{URL: "https://example.test/hook", AllowPrivateNetwork: allow})
		ctx, cancel := context.WithTimeout(context.Background(), 400*time.Millisecond)
		defer cancel()
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "http://"+addr+"/", nil)
		_, err := client.Do(req)
		if err == nil {
			return ""
		}
		return err.Error()
	}
	if got := try(false, "10.255.255.1:81"); !strings.Contains(got, "not allowed") {
		t.Errorf("default: %q, want the private address refused by the destination policy", got)
	}
	if got := try(true, "10.255.255.1:81"); strings.Contains(got, "not allowed") {
		t.Errorf("allow_private_network: %q, want the private address no longer refused by the policy", got)
	}
	for _, addr := range []string{"239.1.1.1:81", "127.0.0.1:81", "169.254.169.254:81"} {
		for _, allow := range []bool{false, true} {
			if got := try(allow, addr); !strings.Contains(got, "not allowed") {
				t.Errorf("%s with allow_private_network=%v: %q, want refused", addr, allow, got)
			}
		}
	}
}

func TestMoreAddressesAreRefusedWhateverTheFlag(t *testing.T) { // HOOK-02 (SSRF)
	for _, s := range []string{"239.1.1.1", "0.1.2.3", "0.255.255.255", "64:ff9b::a00:1", "64:ff9b:1::1", "2002:a00:1::1"} {
		for _, allow := range []bool{false, true} {
			if webhookIPAllowed(net.ParseIP(s), false, allow) {
				t.Errorf("%s allowed with allow_private_network=%v, want refused", s, allow)
			}
		}
	}
	if !webhookIPAllowed(net.ParseIP("2001:4860:4860::8888"), false, false) {
		t.Error("an ordinary public IPv6 address must stay allowed")
	}
}

func TestHTTPSToTheBridgePortAndTheLoadedFileAreRefusedToo(t *testing.T) { // HOOK-22
	for _, u := range []string{"https://127.0.0.1:8080/x", "https://localhost:8080/x", "https://[::1]:08080/x"} {
		body := `{"endpoints":[{"name":"a","url":"` + u + `","secret":"` + goodSecret + `","events":["message"]}]}`
		if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err == nil {
			t.Errorf("%s accepted, want refused", u)
		}
	}
	dir := t.TempDir()
	writeConfig(t, dir, `{"endpoints":[{"name":"a","url":"http://127.0.0.1:8080/x","secret":"`+goodSecret+`","events":["message"]}]}`, 0o600)
	if st := loadWebhooks(dir, nil).Status(); st.Enabled || st.Error == "" {
		t.Errorf("status = %+v, want a file that points at the bridge's own API refused when loaded", st)
	}
}

func TestNoTextLeavesInMetaWhateverTheEvent(t *testing.T) { // HOOK-06
	store, withText, without := hookedStore(t)
	now := time.Now()
	doc := pollEvent("chat@g.us", alice, "D1", &waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{
		FileName: proto.String("secret-report.pdf"), Caption: proto.String("see attached"), Mimetype: proto.String("application/pdf"),
		URL: proto.String("https://mmg.whatsapp.net/x"), MediaKey: []byte{1}, FileSHA256: []byte{1}, FileEncSHA256: []byte{1}, FileLength: proto.Uint64(10)}}, now)
	handleMessage(nil, store, doc, waLog.Noop)
	create := &waE2E.Message{PollCreationMessage: &waE2E.PollCreationMessage{Name: proto.String("Which?"),
		Options: []*waE2E.PollCreationMessage_Option{{OptionName: proto.String("OptionAlpha")}, {OptionName: proto.String("OptionBeta")}}}}
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "P1", create, now), waLog.Noop)

	plain := oneOf(t, without, "message")
	pollPlain := oneOf(t, without, "poll_created")
	for _, leak := range []string{"secret-report.pdf", "see attached", "OptionAlpha", "OptionBeta", "Which?"} {
		if strings.Contains(string(plain.raw), leak) || strings.Contains(string(pollPlain.raw), leak) {
			t.Errorf("%q leaves without include_content", leak)
		}
	}
	rich := oneOf(t, withText, "message")
	richPoll := oneOf(t, withText, "poll_created")
	if !strings.Contains(string(rich.raw), "secret-report.pdf") || !strings.Contains(string(richPoll.raw), "OptionAlpha") {
		t.Errorf("with include_content the file name and the options must be there: %s | %s", rich.raw, richPoll.raw)
	}
}

func TestOwnPollsVotesAndReactionsNeedIncludeOwn(t *testing.T) { // HOOK-05
	store := newTestStore(t)
	def, own := newReceiver(t, nil), newReceiver(t, nil)
	store.webhooks = dispatcherFor(t, nil,
		webhookEndpointConfig{URL: def.srv.URL, Name: "def"},
		webhookEndpointConfig{URL: own.srv.URL, Name: "own", IncludeOwn: true})
	me := "5511900000000"
	now := time.Now()
	mine := func(id string, msg *waE2E.Message) {
		evt := pollEvent("chat@g.us", me+"@s.whatsapp.net", id, msg, now)
		evt.Info.IsFromMe = true
		handleMessageWith(decryptors{vote: decryptTo(sha("A"))}, me, nil, store, evt, waLog.Noop)
	}
	mine("POLL1", creationMessage("Q", []string{"A", "B"}, 0))
	mine("V1", voteUpdate("POLL1", now.Add(time.Second).UnixMilli()))
	seedText(t, store, "M1", "5511999990000", "hi", false)
	mine("R1", reactionMessage("M1", "👍", now.Add(time.Second).UnixMilli()))

	for _, typ := range []string{"poll_created", "poll_vote", "reaction"} {
		oneOf(t, own, typ)
	}
	time.Sleep(60 * time.Millisecond)
	if n := len(def.got()); n != 0 {
		t.Errorf("the default endpoint got %d of this account's own events, want none", n)
	}
}

func TestEventsWithoutAChatPassTheChatsFilter(t *testing.T) { // HOOK-05, README
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, Chats: []string{"120363000000000000@g.us"}})
	d.Emit(connectionWebhookEvent("connected", time.Now()))
	if rc.waitFor(t, 1)[0].body.Type != "connection" {
		t.Error("a connection event must pass a chats filter")
	}
}

func TestThePingReportsTheAnswerAndTheFailureKind(t *testing.T) { // HOOK-18
	rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusInternalServerError) })
	store := newTestStore(t)
	store.webhooks = dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, Name: "local"})
	rec := httptest.NewRecorder()
	handleWebhookTest(store).ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/", strings.NewReader(`{"name":"local"}`)))
	if !strings.Contains(rec.Body.String(), `"success":false`) || !strings.Contains(rec.Body.String(), `"status":500`) {
		t.Errorf("ping of a failing endpoint = %s, want success=false and status 500", rec.Body.String())
	}

	l, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := l.Addr().String()
	l.Close()
	down := newTestStore(t)
	down.webhooks = dispatcherFor(t, nil, webhookEndpointConfig{URL: "http://" + addr + "/", Name: "down"})
	res, err := down.webhooks.Test("down")
	if err != nil || res.Status != 0 || res.Error == "" || strings.Contains(res.Error, addr) {
		t.Errorf("res = %+v, %v; want status 0 and a reason that does not repeat the address", res, err)
	}
}
