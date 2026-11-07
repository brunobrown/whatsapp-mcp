package main

import (
	"net/http"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

func TestDialControlRefusesWhatIsNotAPlainAllowedIP(t *testing.T) { // HOOK-02 (SSRF)
	remote := webhookDialControl(false, false)
	for _, addr := range []string{"[fe80::1%lo]:80", "[::1%lo]:80", "not-an-address", "127.0.0.1:80", "10.0.0.1:80", "[::ffff:10.0.0.1]:80"} {
		if err := remote("tcp", addr, nil); err == nil {
			t.Errorf("%s allowed for a remote destination, want refused", addr)
		}
	}
	if err := remote("tcp", "8.8.8.8:443", nil); err != nil {
		t.Errorf("a public address refused: %v", err)
	}
	loopback := webhookDialControl(true, false)
	if err := loopback("tcp", "127.0.0.1:9000", nil); err != nil {
		t.Errorf("loopback refused for a loopback destination: %v", err)
	}
	if err := loopback("tcp", "[::1%lo]:9000", nil); err == nil {
		t.Error("a zone-scoped literal must be refused even for a loopback destination")
	}
	if err := loopback("tcp", "8.8.8.8:9000", nil); err == nil {
		t.Error("a loopback destination must not reach a public address")
	}
}

func TestPlainHTTPToAnIPLiteralThatIsNotLoopbackIsRefused(t *testing.T) { // HOOK-02
	for _, u := range []string{"http://8.8.8.8:9000/x", "http://10.0.0.5/x", "http://192.168.1.2:9000/x", "http://[2001:db8::1]:9000/x"} {
		body := `{"endpoints":[{"name":"a","url":"` + u + `","secret":"` + goodSecret + `","events":["message"]}]}`
		if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err == nil {
			t.Errorf("%s accepted, want refused: plain http is only for loopback", u)
		}
	}
	for _, u := range []string{"http://127.0.0.2:9000/x", "http://[::1]:9000/x"} {
		body := `{"endpoints":[{"name":"a","url":"` + u + `","secret":"` + goodSecret + `","events":["message"]}]}`
		if _, err := parseWebhookConfig([]byte(body), defaultRESTPort); err != nil {
			t.Errorf("%s refused: %v", u, err)
		}
	}
}

// otherChatStore has an endpoint that wants only chat@g.us, one that takes any chat, and an own-messages one.
func filteredStore(t *testing.T) (store *MessageStore, filtered, open *receiver) {
	t.Helper()
	store = newTestStore(t)
	filtered, open = newReceiver(t, nil), newReceiver(t, nil)
	store.webhooks = dispatcherFor(t, nil,
		webhookEndpointConfig{URL: filtered.srv.URL, Name: "filtered", Chats: []string{"chat@g.us"}},
		webhookEndpointConfig{URL: open.srv.URL, Name: "open"})
	return store, filtered, open
}

func TestEveryEventTypeIsHeldBackByTheChatsFilter(t *testing.T) { // HOOK-05
	store, filtered, open := filteredStore(t)
	now := time.Now()
	const elsewhere = "other@g.us"
	store.StoreChat(elsewhere, "Other", now)
	// each event of another chat
	handleMessage(nil, store, pollEvent(elsewhere, alice, "TXT", &waE2E.Message{Conversation: proto.String("hi")}, now), waLog.Noop)
	handleMessage(nil, store, pollEvent(elsewhere, alice, "POLL1", creationMessage("Q", []string{"A", "B"}, 0), now), waLog.Noop)
	handleMessageWith(decryptors{vote: decryptTo(sha("A"))}, "", nil, store,
		pollEvent(elsewhere, alice, "V1", voteUpdate("POLL1", now.Add(time.Second).UnixMilli()), now), waLog.Noop)
	handleMessageWith(decryptors{}, "", nil, store, pollEvent(elsewhere, alice, "R1", reactionMessage("TXT", "👍", now.Add(time.Second).UnixMilli()), now), waLog.Noop)
	edit := &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{Type: waE2E.ProtocolMessage_MESSAGE_EDIT.Enum(),
		Key: &waCommon.MessageKey{ID: proto.String("TXT")}, EditedMessage: &waE2E.Message{Conversation: proto.String("new")}}}
	handleMessage(nil, store, pollEvent(elsewhere, alice, "E1", edit, now.Add(2*time.Second)), waLog.Noop)

	for _, typ := range []string{"message", "poll_created", "poll_vote", "reaction", "message_edit"} {
		oneOf(t, open, typ) // the open endpoint sees all five, so they were really emitted
	}
	time.Sleep(60 * time.Millisecond)
	if n := len(filtered.got()); n != 0 {
		t.Errorf("the endpoint that wants only chat@g.us got %d events of another chat, want none", n)
	}
}

func TestOwnEditIsNotDeliveredByDefault(t *testing.T) { // HOOK-05
	store := newTestStore(t)
	def := newReceiver(t, nil)
	store.webhooks = dispatcherFor(t, nil, webhookEndpointConfig{URL: def.srv.URL})
	me := "5511900000000"
	seedText(t, store, "MINE", me, "old", true)
	edit := &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{Type: waE2E.ProtocolMessage_MESSAGE_EDIT.Enum(),
		Key: &waCommon.MessageKey{ID: proto.String("MINE")}, EditedMessage: &waE2E.Message{Conversation: proto.String("new")}}}
	evt := pollEvent("chat@g.us", me+"@s.whatsapp.net", "E1", edit, time.Now())
	evt.Info.IsFromMe = true
	handleMessage(nil, store, evt, waLog.Noop)
	if row := readRow(t, store, "MINE"); row.content != "new" {
		t.Fatalf("the edit was not applied (%q): the test would prove nothing", row.content)
	}
	time.Sleep(60 * time.Millisecond)
	if n := len(def.got()); n != 0 {
		t.Errorf("the account's own edit reached an endpoint that did not ask: %d events", n)
	}
}

func TestEventIDsDifferWhenAnyPartOfTheEventDiffers(t *testing.T) { // HOOK-21
	at := time.Now()
	mk := func(chat, id string, ts time.Time) *events.Message {
		e := pollEvent(chat, "5511999990000@s.whatsapp.net", id, nil, ts)
		return e
	}
	reaction := func(chat, target, reactor string, ts time.Time) webhookEvent {
		m := pollEvent(chat, "5511999990000@s.whatsapp.net", "R", reactionMessage(target, "👍", ts.UnixMilli()), ts)
		ev, _ := reactionWebhookEvent(m, reactor)
		return ev
	}
	poll := func(chat, id string) Poll { return Poll{ID: id, ChatJID: chat, Options: []string{"A", "B"}} }
	cases := map[string][2]webhookEvent{
		"message: chat":       {messageWebhookEvent(mk("a@g.us", "M", at), "x", "", ""), messageWebhookEvent(mk("b@g.us", "M", at), "x", "", "")},
		"message: id":         {messageWebhookEvent(mk("a@g.us", "M1", at), "x", "", ""), messageWebhookEvent(mk("a@g.us", "M2", at), "x", "", "")},
		"edit: chat":          {messageEditWebhookEvent(mk("a@g.us", "E", at), "M", "x"), messageEditWebhookEvent(mk("b@g.us", "E", at), "M", "x")},
		"edit: target":        {messageEditWebhookEvent(mk("a@g.us", "E", at), "M1", "x"), messageEditWebhookEvent(mk("a@g.us", "E", at), "M2", "x")},
		"edit: time":          {messageEditWebhookEvent(mk("a@g.us", "E", at), "M", "x"), messageEditWebhookEvent(mk("a@g.us", "E", at.Add(time.Second)), "M", "x")},
		"reaction: chat":      {reaction("a@g.us", "M", "u", at), reaction("b@g.us", "M", "u", at)},
		"reaction: target":    {reaction("a@g.us", "M1", "u", at), reaction("a@g.us", "M2", "u", at)},
		"reaction: reactor":   {reaction("a@g.us", "M", "u1", at), reaction("a@g.us", "M", "u2", at)},
		"reaction: time":      {reaction("a@g.us", "M", "u", at), reaction("a@g.us", "M", "u", at.Add(time.Second))},
		"poll_created: chat":  {pollCreatedWebhookEvent(poll("a@g.us", "P"), at, false), pollCreatedWebhookEvent(poll("b@g.us", "P"), at, false)},
		"poll_created: id":    {pollCreatedWebhookEvent(poll("a@g.us", "P1"), at, false), pollCreatedWebhookEvent(poll("a@g.us", "P2"), at, false)},
		"vote: chat":          {pollVoteWebhookEvent(poll("a@g.us", "P"), "u", nil, at, false, "V"), pollVoteWebhookEvent(poll("b@g.us", "P"), "u", nil, at, false, "V")},
		"vote: poll":          {pollVoteWebhookEvent(poll("a@g.us", "P1"), "u", nil, at, false, "V"), pollVoteWebhookEvent(poll("a@g.us", "P2"), "u", nil, at, false, "V")},
		"connection: state":   {connectionWebhookEvent("connected", at), connectionWebhookEvent("disconnected", at)},
		"connection: instant": {connectionWebhookEvent("connected", at), connectionWebhookEvent("connected", at.Add(time.Second))},
	}
	for name, pair := range cases {
		if pair[0].id() == pair[1].id() {
			t.Errorf("%s: two different events share the event id %s, so a receiver would drop the second as a duplicate", name, pair[0].id())
		}
	}
}

func TestRepetitionsWaitTheScheduledTimes(t *testing.T) { // HOOK-08
	saved := webhookBackoff
	webhookBackoff = []time.Duration{20 * time.Millisecond, 60 * time.Millisecond, 120 * time.Millisecond, 200 * time.Millisecond, 280 * time.Millisecond}
	t.Cleanup(func() { webhookBackoff = saved })
	var arrivals []time.Time
	rc := newReceiver(t, func(_ int, w http.ResponseWriter, _ *http.Request) {
		arrivals = append(arrivals, time.Now())
		w.WriteHeader(http.StatusInternalServerError)
	})
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL})
	d.Emit(msgEvent("M1"))
	waitStatus(t, d, func(s webhookStatus) bool { return s.Endpoints[0].Failed == 1 })
	if len(arrivals) != 6 {
		t.Fatalf("%d attempts, want 6", len(arrivals))
	}
	for i, want := range webhookBackoff {
		if gap := arrivals[i+1].Sub(arrivals[i]); gap < want {
			t.Errorf("wait before attempt %d was %v, want at least %v", i+2, gap, want)
		}
	}
	if webhookCloseWait != 5*time.Second {
		t.Errorf("close wait = %v, want 5s", webhookCloseWait)
	}
}
