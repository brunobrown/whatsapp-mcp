package main

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

func reactionsOf(t *testing.T, store *MessageStore, target string) map[string]string {
	t.Helper()
	list, err := store.Reactions(target, "chat@g.us")
	if err != nil {
		t.Fatal(err)
	}
	byReactor := map[string]string{}
	for _, r := range list {
		byReactor[r.Reactor] = r.Emoji
	}
	return byReactor
}

func TestNewestReactionOfEachPersonWins(t *testing.T) { // REACT-06
	store := newTestStore(t)
	t0 := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)

	if stored, err := store.StoreReaction("M1", "chat@g.us", "alice", "👍", t0); err != nil || !stored {
		t.Fatalf("first = (%v, %v), want stored", stored, err)
	}
	store.StoreReaction("M1", "chat@g.us", "bob", "❤️", t0)
	if stored, _ := store.StoreReaction("M1", "chat@g.us", "alice", "😂", t0.Add(time.Minute)); !stored {
		t.Error("a newer reaction must replace the older one")
	}
	if stored, _ := store.StoreReaction("M1", "chat@g.us", "alice", "😮", t0.Add(30*time.Second)); stored {
		t.Error("an older reaction must be ignored")
	}
	if stored, _ := store.StoreReaction("M1", "chat@g.us", "alice", "😮", t0.Add(time.Minute)); stored {
		t.Error("a reaction with the same timestamp must be ignored")
	}

	if got := reactionsOf(t, store, "M1"); !reflect.DeepEqual(got, map[string]string{"alice": "😂", "bob": "❤️"}) {
		t.Errorf("reactions = %v, want alice 😂 and bob ❤️", got)
	}
}

func TestRetractedReactionLeavesTheListAndBlocksOlderOnes(t *testing.T) { // REACT-06
	store := newTestStore(t)
	t0 := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	store.StoreReaction("M1", "chat@g.us", "alice", "👍", t0)

	store.StoreReaction("M1", "chat@g.us", "alice", "", t0.Add(time.Minute))
	if _, present := reactionsOf(t, store, "M1")["alice"]; present {
		t.Error("a retracted reaction must not be listed")
	}
	if stored, _ := store.StoreReaction("M1", "chat@g.us", "alice", "😂", t0.Add(30*time.Second)); stored {
		t.Error("a reaction older than the retraction must not bring it back")
	}
	if _, present := reactionsOf(t, store, "M1")["alice"]; present {
		t.Error("the retraction must still stand")
	}
}

func reactionMessage(target, emoji string, tsMS int64) *waE2E.Message {
	return &waE2E.Message{ReactionMessage: &waE2E.ReactionMessage{
		Key:               &waCommon.MessageKey{ID: proto.String(target)},
		Text:              proto.String(emoji),
		SenderTimestampMS: proto.Int64(tsMS),
	}}
}

func TestReceivedReactionIsStoredNotAsAMessage(t *testing.T) { // REACT-06
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "oi", false)
	votedAt := time.Date(2026, 11, 6, 12, 5, 0, 0, time.UTC)
	evt := pollEvent("chat@g.us", "5511888880000@s.whatsapp.net", "RCT1", reactionMessage("M1", "👍", votedAt.UnixMilli()), votedAt.Add(time.Hour))

	logger := &infoLogger{}
	handleMessageWith(decryptors{}, "", nil, store, evt, logger)

	if got := reactionsOf(t, store, "M1"); !reflect.DeepEqual(got, map[string]string{"5511888880000": "👍"}) {
		t.Errorf("reactions = %v, want 5511888880000 👍", got)
	}
	list, _ := store.Reactions("M1", "chat@g.us")
	if !list[0].At.Equal(votedAt) {
		t.Errorf("reaction time = %v, want the reaction's own timestamp %v", list[0].At, votedAt)
	}
	if messageCount(t, store, "RCT1") != 0 {
		t.Error("a reaction is not a message of the chat and must not be stored as one")
	}
	for _, line := range logger.lines {
		if strings.Contains(line, "Not stored") {
			t.Errorf("a handled reaction must not be logged as dropped: %q", line)
		}
	}
}

func TestReactionFromThisAccountIsStoredUnderItsOwnUser(t *testing.T) { // REACT-06
	store := newTestStore(t)
	evt := pollEvent("chat@g.us", "99887766@lid", "RCT1", reactionMessage("M1", "🙏", time.Now().UnixMilli()), time.Now())
	evt.Info.IsFromMe = true

	handleMessageWith(decryptors{}, "5511900000000", nil, store, evt, waLog.Noop)

	if got := reactionsOf(t, store, "M1"); !reflect.DeepEqual(got, map[string]string{"5511900000000": "🙏"}) {
		t.Errorf("reactions = %v, want it under our own user", got)
	}
}

func TestReactionRemovedByTheSenderIsStoredAsRetraction(t *testing.T) { // REACT-06
	store := newTestStore(t)
	t0 := time.Now().Add(-time.Hour)
	handleMessageWith(decryptors{}, "", nil, store, pollEvent("chat@g.us", alice, "R1", reactionMessage("M1", "👍", t0.UnixMilli()), t0), waLog.Noop)
	handleMessageWith(decryptors{}, "", nil, store, pollEvent("chat@g.us", alice, "R2", reactionMessage("M1", "", time.Now().UnixMilli()), time.Now()), waLog.Noop)

	if got := reactionsOf(t, store, "M1"); len(got) != 0 {
		t.Errorf("reactions = %v, want none after the removal", got)
	}
}

var errNetwork = errors.New("network down")

// reactingFake adds the reaction builder to the fake client and records how it was called.
type reaction struct {
	chat, sender types.JID
	id, emoji    string
}

func reactingFake() (*fakePollClient, *reaction) {
	f := newFakePollClient()
	var got reaction
	f.env.resolveSender = func(user string) types.JID { return types.JID{User: user, Server: types.DefaultUserServer} }
	f.env.buildReaction = func(chat, sender types.JID, id, emoji string) *waE2E.Message {
		got = reaction{chat, sender, id, emoji}
		return &waE2E.Message{ReactionMessage: &waE2E.ReactionMessage{}}
	}
	return f, &got
}

func TestReactToAGroupMessageNamesTheSenderOfTheTarget(t *testing.T) { // REACT-01, REACT-07
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "oi", false)
	f, got := reactingFake()

	status, resp := reactToMessage(f.env, store, ReactRequest{ChatJID: "chat@g.us", MessageID: "M1", Emoji: "👍"}, waLog.Noop)

	if status != http.StatusOK || !resp.Success || len(f.sent) != 1 {
		t.Fatalf("reactToMessage = %d %+v (sent %d), want 200 success", status, resp, len(f.sent))
	}
	if got.chat.String() != "chat@g.us" || got.id != "M1" || got.emoji != "👍" || got.sender.User != "5511999990000" {
		t.Errorf("builder got %+v, want M1 in chat@g.us, 👍, by 5511999990000", *got)
	}
	if mine := reactionsOf(t, store, "M1")["5511900000000"]; mine != "👍" {
		t.Errorf("our reaction = %q, want 👍 stored under our user", mine)
	}
}

func TestReactToOurOwnMessageUsesAnEmptySender(t *testing.T) { // REACT-07
	store := newTestStore(t)
	seedText(t, store, "MINE", "5511900000000", "meu", true)
	f, got := reactingFake()

	reactToMessage(f.env, store, ReactRequest{ChatJID: "chat@g.us", MessageID: "MINE", Emoji: "👍"}, waLog.Noop)

	if !got.sender.IsEmpty() {
		t.Errorf("sender = %v, want empty for our own message", got.sender)
	}
}

func TestEmptyEmojiRetractsTheReaction(t *testing.T) { // REACT-02
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "oi", false)
	store.StoreReaction("M1", "chat@g.us", "5511900000000", "👍", time.Date(2026, 11, 6, 11, 0, 0, 0, time.UTC))
	f, got := reactingFake()

	status, resp := reactToMessage(f.env, store, ReactRequest{ChatJID: "chat@g.us", MessageID: "M1", Emoji: ""}, waLog.Noop)

	if status != http.StatusOK || !resp.Success || got.emoji != "" {
		t.Fatalf("reactToMessage = %d %+v emoji %q, want a retraction sent", status, resp, got.emoji)
	}
	if _, present := reactionsOf(t, store, "M1")["5511900000000"]; present {
		t.Error("our retracted reaction must not be listed")
	}
}

func TestReactionRefusals(t *testing.T) { // REACT-03, REACT-04
	for name, c := range map[string]struct {
		req    ReactRequest
		status int
	}{
		"unknown message":    {ReactRequest{ChatJID: "chat@g.us", MessageID: "NOPE", Emoji: "👍"}, http.StatusNotFound},
		"too long":           {ReactRequest{ChatJID: "chat@g.us", MessageID: "M1", Emoji: strings.Repeat("😀", 17)}, http.StatusBadRequest},
		"with a space":       {ReactRequest{ChatJID: "chat@g.us", MessageID: "M1", Emoji: "👍 👍"}, http.StatusBadRequest},
		"missing message id": {ReactRequest{ChatJID: "chat@g.us", Emoji: "👍"}, http.StatusBadRequest},
		"bad chat":           {ReactRequest{ChatJID: "::", MessageID: "M1", Emoji: "👍"}, http.StatusBadRequest},
	} {
		store := newTestStore(t)
		seedText(t, store, "M1", "5511999990000", "oi", false)
		f, _ := reactingFake()

		status, resp := reactToMessage(f.env, store, c.req, waLog.Noop)

		if status != c.status || resp.Success || len(f.sent) != 0 {
			t.Errorf("%s: reactToMessage = %d %+v (sent %d), want %d and nothing sent", name, status, resp, len(f.sent), c.status)
		}
	}
}

func TestSixteenCharactersAreAccepted(t *testing.T) { // REACT-03
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "oi", false)
	f, _ := reactingFake()
	if status, _ := reactToMessage(f.env, store, ReactRequest{ChatJID: "chat@g.us", MessageID: "M1", Emoji: strings.Repeat("😀", 16)}, waLog.Noop); status != http.StatusOK {
		t.Errorf("16 characters = %d, want 200", status)
	}
}

func TestFailedReactionStoresNothing(t *testing.T) { // REACT-06
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "oi", false)
	f, _ := reactingFake()
	f.err = errNetwork

	status, resp := reactToMessage(f.env, store, ReactRequest{ChatJID: "chat@g.us", MessageID: "M1", Emoji: "👍"}, waLog.Noop)

	if status != http.StatusInternalServerError || resp.Success {
		t.Errorf("reactToMessage = %d %+v, want 500 failure", status, resp)
	}
	if got := reactionsOf(t, store, "M1"); len(got) != 0 {
		t.Errorf("reactions = %v, want none after a failed send", got)
	}
}

func TestReactRouteIsRegisteredInTheRESTMux(t *testing.T) { // REACT-01 (fiação)
	mux := newRESTMux(nil, newTestStore(t), fakeConn{connected: true}, newBridgeState(time.Now), nil)
	bad := httptest.NewRecorder()
	mux.ServeHTTP(bad, httptest.NewRequest(http.MethodPost, "/api/react", strings.NewReader("{")))
	if bad.Code != http.StatusBadRequest || !strings.Contains(bad.Body.String(), "Invalid request format") {
		t.Errorf("POST /api/react with a bad body = %d %q, want 400 Invalid request format", bad.Code, bad.Body.String())
	}
	get := httptest.NewRecorder()
	mux.ServeHTTP(get, httptest.NewRequest(http.MethodGet, "/api/react", nil))
	if get.Code != http.StatusMethodNotAllowed {
		t.Errorf("GET /api/react = %d, want 405", get.Code)
	}
}
