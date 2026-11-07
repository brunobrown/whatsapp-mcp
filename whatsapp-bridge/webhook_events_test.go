package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

// hookedStore is a store with two endpoints on the same receiver kind: one with the text, one without.
func hookedStore(t *testing.T) (store *MessageStore, withText, withoutText *receiver) {
	t.Helper()
	store = newTestStore(t)
	withText, withoutText = newReceiver(t, nil), newReceiver(t, nil)
	store.webhooks = dispatcherFor(t, nil,
		webhookEndpointConfig{URL: withText.srv.URL, Name: "text", IncludeContent: true},
		webhookEndpointConfig{URL: withoutText.srv.URL, Name: "meta"})
	return store, withText, withoutText
}

func oneOf(t *testing.T, rc *receiver, typ string) receivedHook {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		for _, h := range rc.got() {
			if h.body.Type == typ {
				return h
			}
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("no %s event arrived; got %d requests", typ, len(rc.got()))
	return receivedHook{}
}

func TestAReceivedMessageIsDeliveredWithoutItsTextUnlessAsked(t *testing.T) { // HOOK-03, HOOK-06
	store, withText, without := hookedStore(t)

	handleMessage(nil, store, pollEvent("chat@g.us", alice, "M1", &waE2E.Message{Conversation: proto.String("hello")}, time.Now()), waLog.Noop)

	a := oneOf(t, withText, "message")
	if a.body.Data["message_id"] != "M1" || a.body.Data["text"] != "hello" || a.body.Data["content_length"] != float64(5) {
		t.Errorf("with content: data = %v", a.body.Data)
	}
	b := oneOf(t, without, "message")
	if strings.Contains(string(b.raw), "hello") || b.body.Data["content_length"] != float64(5) || b.body.Data["chat_jid"] != "chat@g.us" {
		t.Errorf("without content: %s", b.raw)
	}
}

func TestOwnMessageIsNotDeliveredByDefault(t *testing.T) { // HOOK-05
	store, withText, _ := hookedStore(t)
	evt := pollEvent("chat@g.us", "5511900000000@s.whatsapp.net", "MINE", &waE2E.Message{Conversation: proto.String("mine")}, time.Now())
	evt.Info.IsFromMe = true
	handleMessage(nil, store, evt, waLog.Noop)
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "THEIRS", &waE2E.Message{Conversation: proto.String("theirs")}, time.Now()), waLog.Noop)

	withText.waitFor(t, 1)
	time.Sleep(50 * time.Millisecond)
	got := withText.got()
	if len(got) != 1 || got[0].body.Data["message_id"] != "THEIRS" {
		t.Errorf("delivered %d events, want only THEIRS (own messages stay out)", len(got))
	}
}

func TestAPollCreatedAndAVoteAreDeliveredWithTheOptionsOnlyWhenAsked(t *testing.T) { // HOOK-14, HOOK-15
	store, withText, without := hookedStore(t)
	now := time.Now()
	create := &waE2E.Message{PollCreationMessage: &waE2E.PollCreationMessage{
		Name: proto.String("Lunch?"), Options: []*waE2E.PollCreationMessage_Option{{OptionName: proto.String("Yes")}, {OptionName: proto.String("No")}}}}

	handleMessage(nil, store, pollEvent("chat@g.us", alice, "POLL1", create, now), waLog.Noop)
	handleMessageWith(decryptors{vote: decryptTo(sha("Yes"))}, "", nil, store,
		pollEvent("chat@g.us", "5511888880000@s.whatsapp.net", "V1", voteUpdate("POLL1", now.Add(time.Second).UnixMilli()), now), waLog.Noop)

	created := oneOf(t, withText, "poll_created")
	if created.body.Data["poll_id"] != "POLL1" || created.body.Data["option_count"] != float64(2) || created.body.Data["question"] != "Lunch?" {
		t.Errorf("poll_created = %v", created.body.Data)
	}
	vote := oneOf(t, withText, "poll_vote")
	opts, _ := vote.body.Data["options"].([]any)
	if vote.body.Data["voter"] != "5511888880000" || vote.body.Data["retracted"] != false || len(opts) != 1 || opts[0] != "Yes" {
		t.Errorf("poll_vote = %v, want the voter, retracted=false and the option Yes", vote.body.Data)
	}
	bare := oneOf(t, without, "poll_vote")
	if strings.Contains(string(bare.raw), "Yes") || strings.Contains(string(oneOf(t, without, "poll_created").raw), "Lunch") {
		t.Error("the question and the option names must stay out without include_content")
	}
}

func TestARetractedVoteIsDeliveredAsRetracted(t *testing.T) { // HOOK-14
	store, withText, _ := hookedStore(t)
	now := time.Now()
	create := &waE2E.Message{PollCreationMessage: &waE2E.PollCreationMessage{
		Name: proto.String("Q"), Options: []*waE2E.PollCreationMessage_Option{{OptionName: proto.String("A")}, {OptionName: proto.String("B")}}}}
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "POLL1", create, now), waLog.Noop)

	handleMessageWith(decryptors{vote: decryptTo()}, "", nil, store,
		pollEvent("chat@g.us", alice, "V1", voteUpdate("POLL1", now.Add(time.Second).UnixMilli()), now), waLog.Noop)

	v := oneOf(t, withText, "poll_vote")
	if v.body.Data["retracted"] != true || v.body.Data["option_count"] != float64(0) {
		t.Errorf("poll_vote = %v, want retracted=true and no options", v.body.Data)
	}
}

func TestTheVoterOfAnAnonymousPollIsNull(t *testing.T) { // HOOK-14
	store, withText, _ := hookedStore(t)
	now := time.Now()
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "POLL1", creationMessage("Q", []string{"A", "B"}, 0), now), waLog.Noop) // anonymous
	handleMessageWith(decryptors{vote: decryptTo(sha("A"))}, "", nil, store,
		pollEvent("chat@g.us", "5511888880000@s.whatsapp.net", "V1", voteUpdate("POLL1", now.Add(time.Second).UnixMilli()), now), waLog.Noop)

	v := oneOf(t, withText, "poll_vote")
	if voter, present := v.body.Data["voter"]; !present || voter != nil {
		t.Errorf("voter = %v (present %v), want null", voter, present)
	}
	if strings.Contains(string(v.raw), "5511888880000") {
		t.Errorf("the voter's number leaks in an anonymous poll: %s", v.raw)
	}
}

func TestAStaleVoteIsNotDeliveredTwice(t *testing.T) { // HOOK-14 (only a stored vote is news)
	store, withText, _ := hookedStore(t)
	now := time.Now()
	create := &waE2E.Message{PollCreationMessage: &waE2E.PollCreationMessage{
		Name: proto.String("Q"), Options: []*waE2E.PollCreationMessage_Option{{OptionName: proto.String("A")}, {OptionName: proto.String("B")}}}}
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "POLL1", create, now), waLog.Noop)
	newer := pollEvent("chat@g.us", alice, "V2", voteUpdate("POLL1", now.Add(2*time.Second).UnixMilli()), now)
	older := pollEvent("chat@g.us", alice, "V1", voteUpdate("POLL1", now.Add(time.Second).UnixMilli()), now)

	handleMessageWith(decryptors{vote: decryptTo(sha("A"))}, "", nil, store, newer, waLog.Noop)
	handleMessageWith(decryptors{vote: decryptTo(sha("B"))}, "", nil, store, older, waLog.Noop) // the app ignores it: older

	oneOf(t, withText, "poll_vote")
	time.Sleep(60 * time.Millisecond)
	votes := 0
	for _, h := range withText.got() {
		if h.body.Type == "poll_vote" {
			votes++
		}
	}
	if votes != 1 {
		t.Errorf("%d poll_vote events, want 1: the older vote was not stored, so it is not a change", votes)
	}
}

func TestAReactionIsDeliveredWithTheEmojiOnlyWhenAsked(t *testing.T) { // HOOK-15
	store, withText, without := hookedStore(t)
	seedText(t, store, "M1", "5511999990000", "hi", false)

	handleMessageWith(decryptors{}, "", nil, store, pollEvent("chat@g.us", alice, "R1", reactionMessage("M1", "👍", time.Now().UnixMilli()), time.Now()), waLog.Noop)
	handleMessageWith(decryptors{}, "", nil, store, pollEvent("chat@g.us", alice, "R2", reactionMessage("M1", "", time.Now().Add(time.Second).UnixMilli()), time.Now()), waLog.Noop)

	withText.waitFor(t, 2)
	got := withText.got()
	if got[0].body.Type != "reaction" || got[0].body.Data["target_id"] != "M1" || got[0].body.Data["emoji"] != "👍" || got[0].body.Data["retracted"] != false {
		t.Errorf("first = %v", got[0].body.Data)
	}
	if got[1].body.Data["retracted"] != true {
		t.Errorf("second = %v, want retracted=true", got[1].body.Data)
	}
	if strings.Contains(string(oneOf(t, without, "reaction").raw), "👍") {
		t.Error("the emoji must stay out without include_content")
	}
}

func TestAnEditIsDeliveredAsAMessageEdit(t *testing.T) { // HOOK-15
	store, withText, without := hookedStore(t)
	seedText(t, store, "M1", "5511999990000", "old text", false)
	edit := &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{
		Type: waE2E.ProtocolMessage_MESSAGE_EDIT.Enum(), Key: &waCommon.MessageKey{ID: proto.String("M1")},
		EditedMessage: &waE2E.Message{Conversation: proto.String("new text")}}}

	handleMessage(nil, store, pollEvent("chat@g.us", alice, "E1", edit, time.Now()), waLog.Noop)

	e := oneOf(t, withText, "message_edit")
	if e.body.Data["message_id"] != "M1" || e.body.Data["text"] != "new text" {
		t.Errorf("message_edit = %v", e.body.Data)
	}
	if strings.Contains(string(oneOf(t, without, "message_edit").raw), "new text") {
		t.Error("the edited text must stay out without include_content")
	}
}

func TestAnEditOfAnUnknownMessageIsNotDelivered(t *testing.T) { // HOOK-15
	store, withText, _ := hookedStore(t)
	edit := &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{
		Type: waE2E.ProtocolMessage_MESSAGE_EDIT.Enum(), Key: &waCommon.MessageKey{ID: proto.String("NOPE")},
		EditedMessage: &waE2E.Message{Conversation: proto.String("x")}}}
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "E1", edit, time.Now()), waLog.Noop)
	time.Sleep(60 * time.Millisecond)
	if n := len(withText.got()); n != 0 {
		t.Errorf("%d events for an edit that changed nothing, want none", n)
	}
}

func TestConnectionEventsAreDelivered(t *testing.T) { // HOOK-16
	rc := newReceiver(t, nil)
	d := dispatcherFor(t, nil, webhookEndpointConfig{URL: rc.srv.URL, Events: []string{"connection"}})
	now := time.Now()
	for _, state := range []string{"connected", "disconnected", "logged_out"} {
		d.Emit(connectionWebhookEvent(state, now))
		now = now.Add(time.Millisecond)
	}
	got := rc.waitFor(t, 3)
	states := []any{got[0].body.Data["state"], got[1].body.Data["state"], got[2].body.Data["state"]}
	if states[0] != "connected" || states[1] != "disconnected" || states[2] != "logged_out" {
		t.Errorf("states = %v", states)
	}
	if got[0].body.Type != "connection" || got[0].body.Data["chat_jid"] != nil {
		t.Errorf("event = %+v", got[0].body)
	}
}

func TestStatusEndpointShowsTheWebhooksWithoutTheURL(t *testing.T) { // HOOK-17, HOOK-01
	store, _, _ := hookedStore(t)
	state := newBridgeState(time.Now)
	state.webhooks = store.webhooks.Status
	rec := httptest.NewRecorder()
	handleStatus(fakeConn{}, state, "test").ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/status", nil))

	var out struct {
		Webhooks webhookStatus `json:"webhooks"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	if !out.Webhooks.Enabled || len(out.Webhooks.Endpoints) != 2 || out.Webhooks.Endpoints[0].Name != "text" {
		t.Errorf("webhooks = %+v", out.Webhooks)
	}
	if strings.Contains(rec.Body.String(), "127.0.0.1") || strings.Contains(rec.Body.String(), goodSecret) {
		t.Errorf("status leaks the address or the secret: %s", rec.Body.String())
	}
}

func TestStatusWithoutWebhooksSaysDisabled(t *testing.T) { // HOOK-01
	state := newBridgeState(time.Now)
	rec := httptest.NewRecorder()
	handleStatus(fakeConn{}, state, "test").ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/status", nil))
	var out struct {
		Webhooks webhookStatus `json:"webhooks"`
	}
	json.Unmarshal(rec.Body.Bytes(), &out)
	if out.Webhooks.Enabled || out.Webhooks.Endpoints == nil || !strings.Contains(rec.Body.String(), `"webhooks":{"enabled":false`) {
		t.Errorf("body = %s, want webhooks.enabled=false and an empty list", rec.Body.String())
	}
}

func TestWebhookTestEndpoint(t *testing.T) { // HOOK-18
	store, withText, _ := hookedStore(t)
	call := func(method, body string) *httptest.ResponseRecorder {
		rec := httptest.NewRecorder()
		handleWebhookTest(store).ServeHTTP(rec, httptest.NewRequest(method, "/api/webhooks/test", strings.NewReader(body)))
		return rec
	}

	ok := call(http.MethodPost, `{"name":"text"}`)
	var res map[string]any
	json.Unmarshal(ok.Body.Bytes(), &res)
	if ok.Code != 200 || res["success"] != true || res["status"] != float64(200) {
		t.Errorf("ok = %d %s", ok.Code, ok.Body.String())
	}
	if oneOf(t, withText, "ping").body.Type != "ping" {
		t.Error("no ping arrived")
	}
	if rec := call(http.MethodPost, `{"name":"nope"}`); rec.Code != 404 || !strings.Contains(rec.Body.String(), "ENDPOINT_NOT_FOUND") {
		t.Errorf("unknown = %d %s", rec.Code, rec.Body.String())
	}
	if rec := call(http.MethodPost, `{}`); rec.Code != 400 {
		t.Errorf("no name = %d, want 400", rec.Code)
	}
	if rec := call(http.MethodGet, ``); rec.Code != 405 {
		t.Errorf("GET = %d, want 405", rec.Code)
	}
}

func TestWebhookTestEndpointWhenOffIsNotFound(t *testing.T) { // HOOK-18, HOOK-01
	store := newTestStore(t) // no dispatcher at all
	rec := httptest.NewRecorder()
	handleWebhookTest(store).ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/", strings.NewReader(`{"name":"a"}`)))
	if rec.Code != 404 {
		t.Errorf("code = %d, want 404", rec.Code)
	}
}

func TestMessageHandlingWorksWithoutAnyWebhooks(t *testing.T) { // HOOK-01
	store := newTestStore(t) // webhooks is nil
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "M1", &waE2E.Message{Conversation: proto.String("hello")}, time.Now()), waLog.Noop)
	if row := readRow(t, store, "M1"); row.content != "hello" {
		t.Errorf("content = %q", row.content)
	}
}

func TestTheEventIDOfAVoteCarriesNothingAboutTheVoter(t *testing.T) { // HOOK-23 (anonymous polls)
	poll := Poll{ID: "POLL1", ChatJID: "chat@g.us", Question: "Q", Options: []string{"A", "B"}, HideVoterNames: true}
	at := time.Now()
	alice := pollVoteWebhookEvent(poll, "5511999990000", []string{"x"}, at, false, "VOTEMSG1")
	bob := pollVoteWebhookEvent(poll, "5511888880000", []string{"x"}, at, false, "VOTEMSG1")
	if alice.id() != bob.id() {
		t.Error("the event id changes with the voter: a receiver could hash candidate numbers and unmask an anonymous vote")
	}
	again := pollVoteWebhookEvent(poll, "5511999990000", []string{"x"}, at, false, "VOTEMSG2")
	if alice.id() == again.id() {
		t.Error("two different votes share an event id")
	}
	if voter, present := alice.Meta["voter"]; !present || voter != nil {
		t.Errorf("voter = %v, want null in an anonymous poll", voter)
	}
}

func TestAVoteRedeliveredByWhatsAppKeepsItsEventID(t *testing.T) { // HOOK-21
	poll := Poll{ID: "POLL1", ChatJID: "chat@g.us", Options: []string{"A", "B"}}
	at := time.Now()
	a := pollVoteWebhookEvent(poll, "5511999990000", nil, at, false, "VOTEMSG1")
	b := pollVoteWebhookEvent(poll, "5511999990000", nil, at.Add(time.Second), false, "VOTEMSG1")
	if a.id() != b.id() {
		t.Error("the same vote message gives different event ids")
	}
}
