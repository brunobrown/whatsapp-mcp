package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

func samplePoll() Poll {
	return Poll{
		ID: "POLL1", ChatJID: "chat@g.us", Author: "alice", Question: "Almoço?", Options: []string{"Sim", "Não"},
		SelectableCount: 1, AllowAddOption: true, HideVoterNames: true,
	}
}

func votesOf(t *testing.T, store *MessageStore, pollID string) map[string]PollVote {
	t.Helper()
	votes, err := store.PollVotes(pollID, "chat@g.us")
	if err != nil {
		t.Fatal(err)
	}
	byVoter := map[string]PollVote{}
	for _, v := range votes {
		byVoter[v.Voter] = v
	}
	return byVoter
}

func TestStorePollRoundTrip(t *testing.T) { // RX-01 (armazenamento)
	store := newTestStore(t)
	want := samplePoll()
	if _, err := store.StorePoll(want); err != nil {
		t.Fatal(err)
	}
	got, found, err := store.GetPoll("POLL1", "chat@g.us")
	if err != nil || !found {
		t.Fatalf("GetPoll = (%v, %v), want found", found, err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("GetPoll = %+v, want %+v", got, want)
	}
	if _, found, _ := store.GetPoll("OTHER", "chat@g.us"); found {
		t.Error("an unknown poll must not be found")
	}
}

func TestStorePollTwiceKeepsOneRowAndVotes(t *testing.T) { // RX-05
	store := newTestStore(t)
	if _, err := store.StorePoll(samplePoll()); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	if _, err := store.StorePollVote("POLL1", "chat@g.us", "alice", []string{"aa"}, now); err != nil {
		t.Fatal(err)
	}

	if _, err := store.StorePoll(samplePoll()); err != nil {
		t.Fatal(err)
	}

	var rows int
	if err := store.db.QueryRow("SELECT COUNT(*) FROM polls").Scan(&rows); err != nil || rows != 1 {
		t.Fatalf("polls rows = %d (%v), want 1", rows, err)
	}
	if len(votesOf(t, store, "POLL1")) != 1 {
		t.Error("storing the poll again must keep its votes")
	}
}

func TestPollWithTheIDOfSomeoneElsesPollIsRefused(t *testing.T) { // RX-05 (autoria)
	store := newTestStore(t)
	if _, err := store.StorePoll(samplePoll()); err != nil {
		t.Fatal(err)
	}
	forged := samplePoll()
	forged.Author = "mallory"
	forged.Question = "Pergunta trocada"
	forged.Options = []string{"X", "Y"}

	stored, err := store.StorePoll(forged)
	if err != nil || stored {
		t.Fatalf("StorePoll by another author = (%v, %v), want refused", stored, err)
	}
	got, _, _ := store.GetPoll("POLL1", "chat@g.us")
	if got.Question != "Almoço?" || !reflect.DeepEqual(got.Options, []string{"Sim", "Não"}) {
		t.Errorf("poll = %+v, want the original untouched", got)
	}

	own := samplePoll()
	own.Question = "Almoço hoje?"
	if stored, err := store.StorePoll(own); err != nil || !stored {
		t.Errorf("StorePoll by the author = (%v, %v), want updated", stored, err)
	}
}

func TestReceivedPollOfAnotherAuthorIsNotAcceptedByRecordPoll(t *testing.T) { // RX-05 (autoria)
	store := newTestStore(t)
	now := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	first := pollEvent("chat@g.us", "5511999990000@s.whatsapp.net", "POLL1", creationMessage("Original", []string{"A", "B"}, 0), now)
	forged := pollEvent("chat@g.us", "5511888880000@s.whatsapp.net", "POLL1", creationMessage("Falsa", []string{"X", "Y"}, 0), now)

	if !recordPoll(store, nil, first, waLog.Noop) {
		t.Fatal("the first poll must be accepted")
	}
	if recordPoll(store, nil, forged, waLog.Noop) {
		t.Error("a poll reusing another author's ID must be refused, so the message is not stored either")
	}
	if got, _, _ := store.GetPoll("POLL1", "chat@g.us"); got.Question != "Original" {
		t.Errorf("question = %q, want Original", got.Question)
	}
}

func TestPollVoteNewerReplacesOlderIgnored(t *testing.T) { // RX-03
	store := newTestStore(t)
	t0 := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)

	if stored, err := store.StorePollVote("POLL1", "chat@g.us", "alice", []string{"aa"}, t0); err != nil || !stored {
		t.Fatalf("first vote = (%v, %v), want stored", stored, err)
	}
	if stored, err := store.StorePollVote("POLL1", "chat@g.us", "alice", []string{"bb"}, t0.Add(time.Minute)); err != nil || !stored {
		t.Fatalf("newer vote = (%v, %v), want stored", stored, err)
	}
	if got := votesOf(t, store, "POLL1")["alice"].Hashes; !reflect.DeepEqual(got, []string{"bb"}) {
		t.Errorf("after a newer vote hashes = %v, want [bb]", got)
	}

	if stored, err := store.StorePollVote("POLL1", "chat@g.us", "alice", []string{"cc"}, t0.Add(30*time.Second)); err != nil || stored {
		t.Fatalf("older vote = (%v, %v), want ignored", stored, err)
	}
	if stored, _ := store.StorePollVote("POLL1", "chat@g.us", "alice", []string{"dd"}, t0.Add(time.Minute)); stored {
		t.Error("a vote with the same timestamp must be ignored")
	}
	if got := votesOf(t, store, "POLL1")["alice"].Hashes; !reflect.DeepEqual(got, []string{"bb"}) {
		t.Errorf("after older and equal votes hashes = %v, want [bb]", got)
	}
}

func TestPollVoteKeptWhenPollIsUnknownYet(t *testing.T) { // EDGE-02
	store := newTestStore(t)
	now := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	if stored, err := store.StorePollVote("LATER", "chat@g.us", "bob", []string{"aa"}, now); err != nil || !stored {
		t.Fatalf("vote before the poll = (%v, %v), want stored", stored, err)
	}
	if _, err := store.StorePoll(Poll{ID: "LATER", ChatJID: "chat@g.us", Author: "alice", Question: "Q", Options: []string{"A", "B"}}); err != nil {
		t.Fatal(err)
	}
	if _, ok := votesOf(t, store, "LATER")["bob"]; !ok {
		t.Error("a vote that arrived before its poll must be there once the poll is known")
	}
}

func TestEmptyVoteIsStoredAsRetraction(t *testing.T) { // RES-04 (base) / VOTE-02
	store := newTestStore(t)
	t0 := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	store.StorePollVote("POLL1", "chat@g.us", "alice", []string{"aa"}, t0)
	if stored, err := store.StorePollVote("POLL1", "chat@g.us", "alice", nil, t0.Add(time.Minute)); err != nil || !stored {
		t.Fatalf("empty vote = (%v, %v), want stored", stored, err)
	}
	vote, ok := votesOf(t, store, "POLL1")["alice"]
	if !ok || len(vote.Hashes) != 0 {
		t.Errorf("retracted vote = %+v (present %v), want present with no hashes", vote, ok)
	}
}

func pollEvent(chat, sender, id string, msg *waE2E.Message, ts time.Time) *events.Message {
	chatJID, _ := types.ParseJID(chat)
	senderJID, _ := types.ParseJID(sender)
	return &events.Message{
		Info:    types.MessageInfo{MessageSource: types.MessageSource{Chat: chatJID, Sender: senderJID}, ID: id, Timestamp: ts},
		Message: msg,
	}
}

func creationMessage(name string, options []string, selectable uint32) *waE2E.Message {
	opts := make([]*waE2E.PollCreationMessage_Option, len(options))
	for i, o := range options {
		opts[i] = &waE2E.PollCreationMessage_Option{OptionName: proto.String(o)}
	}
	return &waE2E.Message{PollCreationMessage: &waE2E.PollCreationMessage{
		Name: proto.String(name), Options: opts, SelectableOptionsCount: proto.Uint32(selectable),
		AllowAddOption: proto.Bool(true), HideParticipantName: proto.Bool(true),
	}}
}

func voteUpdate(pollID string, tsMS int64) *waE2E.Message {
	return &waE2E.Message{PollUpdateMessage: &waE2E.PollUpdateMessage{
		PollCreationMessageKey: &waCommon.MessageKey{ID: proto.String(pollID)},
		SenderTimestampMS:      proto.Int64(tsMS),
	}}
}

func decryptTo(hashes ...[]byte) pollVoteDecryptor {
	return func(*events.Message) (*waE2E.PollVoteMessage, error) {
		return &waE2E.PollVoteMessage{SelectedOptions: hashes}, nil
	}
}

func sha(name string) []byte {
	sum := sha256.Sum256([]byte(name))
	return sum[:]
}

func TestReceivedPollIsStoredWithOptionsInOrder(t *testing.T) { // RX-01
	store := newTestStore(t)
	now := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	evt := pollEvent("chat@g.us", "5511999990000@s.whatsapp.net", "POLL1", creationMessage("Almoço?", []string{"Sim", "Não", "Talvez"}, 1), now)

	recordPoll(store, nil, evt, waLog.Noop)

	got, found, err := store.GetPoll("POLL1", "chat@g.us")
	if err != nil || !found {
		t.Fatalf("GetPoll = (%v, %v), want found", found, err)
	}
	want := Poll{ID: "POLL1", ChatJID: "chat@g.us", Author: "5511999990000", Question: "Almoço?", Options: []string{"Sim", "Não", "Talvez"},
		SelectableCount: 1, AllowAddOption: true, HideVoterNames: true}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("poll = %+v, want %+v", got, want)
	}
}

func TestPollMessageTextIsTheQuestionWithMarker(t *testing.T) { // RX-01, SEND-07
	if got := extractTextContent(creationMessage("Almoço?", []string{"Sim", "Não"}, 0)); got != "[enquete] Almoço?" {
		t.Errorf("content = %q, want %q", got, "[enquete] Almoço?")
	}
}

func TestReceivedVoteStoresHashesVoterAndVoteTime(t *testing.T) { // RX-02
	store := newTestStore(t)
	votedAt := time.Date(2026, 11, 6, 12, 5, 0, 0, time.UTC)
	evt := pollEvent("chat@g.us", "5511999990000@s.whatsapp.net", "VOTEMSG", voteUpdate("POLL1", votedAt.UnixMilli()),
		votedAt.Add(time.Hour)) // the envelope arrives later than the vote was cast

	recordPoll(store, decryptTo(sha("Sim"), sha("Talvez")), evt, waLog.Noop)

	vote, ok := votesOf(t, store, "POLL1")["5511999990000"]
	if !ok {
		t.Fatal("vote not stored under the voter's user part")
	}
	want := []string{hex.EncodeToString(sha("Sim")), hex.EncodeToString(sha("Talvez"))}
	if !reflect.DeepEqual(vote.Hashes, want) {
		t.Errorf("hashes = %v, want %v", vote.Hashes, want)
	}
	if !vote.At.Equal(votedAt) {
		t.Errorf("vote time = %v, want the vote's own timestamp %v", vote.At, votedAt)
	}
}

func TestVoteWithoutTimestampUsesTheEnvelopeTime(t *testing.T) { // RX-02
	store := newTestStore(t)
	arrived := time.Date(2026, 11, 6, 12, 5, 0, 0, time.UTC)
	evt := pollEvent("chat@g.us", "5511999990000@s.whatsapp.net", "VOTEMSG", voteUpdate("POLL1", 0), arrived)

	recordPoll(store, decryptTo(sha("Sim")), evt, waLog.Noop)

	if got := votesOf(t, store, "POLL1")["5511999990000"].At; !got.Equal(arrived) {
		t.Errorf("vote time = %v, want %v", got, arrived)
	}
}

func TestVoteThatCannotBeDecryptedStoresNothing(t *testing.T) { // RX-04
	store := newTestStore(t)
	failing := func(*events.Message) (*waE2E.PollVoteMessage, error) { return nil, errors.New("no message secret") }
	evt := pollEvent("chat@g.us", "5511999990000@s.whatsapp.net", "VOTEMSG", voteUpdate("POLL1", 1), time.Now())

	recordPoll(store, failing, evt, waLog.Noop)

	if n := len(votesOf(t, store, "POLL1")); n != 0 {
		t.Errorf("%d votes stored, want none", n)
	}
}

func TestOlderReceivedVoteDoesNotReplaceNewer(t *testing.T) { // RX-03
	store := newTestStore(t)
	t0 := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	newer := pollEvent("chat@g.us", "5511999990000@s.whatsapp.net", "V2", voteUpdate("POLL1", t0.Add(time.Minute).UnixMilli()), t0)
	older := pollEvent("chat@g.us", "5511999990000@s.whatsapp.net", "V1", voteUpdate("POLL1", t0.UnixMilli()), t0)

	recordPoll(store, decryptTo(sha("Não")), newer, waLog.Noop)
	recordPoll(store, decryptTo(sha("Sim")), older, waLog.Noop)

	want := []string{hex.EncodeToString(sha("Não"))}
	if got := votesOf(t, store, "POLL1")["5511999990000"].Hashes; !reflect.DeepEqual(got, want) {
		t.Errorf("hashes = %v, want %v (the newer vote)", got, want)
	}
}

func TestPollInsideFutureProofWrapperIsRecognised(t *testing.T) { // RX-01
	inner := creationMessage("Q", []string{"A", "B"}, 0)
	wrapped := &waE2E.Message{PollCreationMessageV4: &waE2E.FutureProofMessage{Message: inner}}
	if _, ok := pollFromMessage("chat@g.us", "P", "alice", wrapped); !ok {
		t.Error("a poll inside the V4 wrapper must be recognised")
	}
	if _, ok := pollFromMessage("chat@g.us", "P", "alice", &waE2E.Message{Conversation: proto.String("oi")}); ok {
		t.Error("a text message is not a poll")
	}
}

// fakePollClient is a connected WhatsApp client that records what it was asked to send.
type fakePollClient struct {
	env  pollEnv
	sent []*waE2E.Message
	to   []types.JID
	err  error
}

func newFakePollClient() *fakePollClient {
	f := &fakePollClient{}
	own, _ := types.ParseJID("5511900000000@s.whatsapp.net")
	f.env = pollEnv{
		own:       own,
		connected: func() bool { return true },
		send: func(to types.JID, msg *waE2E.Message) (whatsmeow.SendResponse, error) {
			if f.err != nil {
				return whatsmeow.SendResponse{}, f.err
			}
			f.sent = append(f.sent, msg)
			f.to = append(f.to, to)
			return whatsmeow.SendResponse{ID: "SENT1", Timestamp: time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)}, nil
		},
	}
	return f
}

func boolPtr(b bool) *bool { return &b }

func TestSendPollBuildsTheMessageAndRecordsIt(t *testing.T) { // SEND-02, SEND-03, SEND-07
	store := newTestStore(t)
	f := newFakePollClient()

	status, resp := sendPoll(f.env, store, SendPollRequest{
		Recipient: "chat@g.us", Question: "Almoço?", Options: []string{"Sim", "Não"},
		AllowMultiple: boolPtr(false), AllowAddOption: true, HideVoterNames: true,
	}, waLog.Noop)

	if status != http.StatusOK || !resp.Success || resp.MessageID != "SENT1" || resp.ChatJID != "chat@g.us" {
		t.Fatalf("sendPoll = %d %+v, want 200 success SENT1 chat@g.us", status, resp)
	}
	creation := f.sent[0].GetPollCreationMessage()
	if creation.GetName() != "Almoço?" || creation.GetSelectableOptionsCount() != 1 ||
		!creation.GetAllowAddOption() || !creation.GetHideParticipantName() {
		t.Errorf("message = %+v, want question, selectable 1 and both flags", creation)
	}
	if got := []string{creation.GetOptions()[0].GetOptionName(), creation.GetOptions()[1].GetOptionName()}; !reflect.DeepEqual(got, []string{"Sim", "Não"}) {
		t.Errorf("options = %v, want [Sim Não]", got)
	}
	if len(f.sent[0].GetMessageContextInfo().GetMessageSecret()) != 32 {
		t.Error("the poll needs a 32-byte message secret, or votes cannot be read")
	}

	poll, found, _ := store.GetPoll("SENT1", "chat@g.us")
	if !found || poll.Question != "Almoço?" || poll.SelectableCount != 1 || !poll.AllowAddOption || !poll.HideVoterNames {
		t.Errorf("stored poll = %+v (found %v)", poll, found)
	}
	row := readRow(t, store, "SENT1")
	if row.content != "[enquete] Almoço?" || !row.isFromMe || row.sender != "5511900000000" {
		t.Errorf("stored message = %+v, want [enquete] Almoço?, ours", row)
	}
}

func TestSendPollDefaultsToMultipleAnswersWithoutFlags(t *testing.T) { // SEND-02, SEND-03
	store := newTestStore(t)
	f := newFakePollClient()

	sendPoll(f.env, store, SendPollRequest{Recipient: "chat@g.us", Question: "Q", Options: []string{"A", "B"}}, waLog.Noop)

	creation := f.sent[0].GetPollCreationMessage()
	if creation.GetSelectableOptionsCount() != 0 {
		t.Errorf("selectable = %d, want 0 (any number of answers)", creation.GetSelectableOptionsCount())
	}
	if creation.AllowAddOption != nil || creation.HideParticipantName != nil {
		t.Error("the flags must be absent from the message unless requested")
	}
}

func TestSendPollRejectsInvalidPollsBeforeSending(t *testing.T) { // SEND-04 (defesa do bridge)
	store := newTestStore(t)
	for name, req := range map[string]SendPollRequest{
		"empty question": {Recipient: "chat@g.us", Question: "", Options: []string{"A", "B"}},
		"one option":     {Recipient: "chat@g.us", Question: "Q", Options: []string{"A"}},
		"no recipient":   {Question: "Q", Options: []string{"A", "B"}},
		"repeated":       {Recipient: "chat@g.us", Question: "Q", Options: []string{"Sim", "sim"}},
	} {
		f := newFakePollClient()
		status, resp := sendPoll(f.env, store, req, waLog.Noop)
		if status != http.StatusBadRequest || resp.Success || len(f.sent) != 0 {
			t.Errorf("%s: sendPoll = %d %+v (sent %d), want 400 and nothing sent", name, status, resp, len(f.sent))
		}
	}
}

func TestSendPollFailureStoresNothing(t *testing.T) { // SEND-07
	store := newTestStore(t)
	f := newFakePollClient()
	f.err = errors.New("network down")

	status, resp := sendPoll(f.env, store, SendPollRequest{Recipient: "chat@g.us", Question: "Q", Options: []string{"A", "B"}}, waLog.Noop)

	if status != http.StatusInternalServerError || resp.Success {
		t.Errorf("sendPoll = %d %+v, want 500 failure", status, resp)
	}
	var rows int
	store.db.QueryRow("SELECT (SELECT COUNT(*) FROM polls) + (SELECT COUNT(*) FROM messages)").Scan(&rows)
	if rows != 0 {
		t.Errorf("%d rows stored after a failed send, want 0", rows)
	}
}

func TestSendPollNotConnected(t *testing.T) { // SEND-01 (erro)
	f := newFakePollClient()
	f.env.connected = func() bool { return false }
	status, resp := sendPoll(f.env, newTestStore(t), SendPollRequest{Recipient: "chat@g.us", Question: "Q", Options: []string{"A", "B"}}, waLog.Noop)
	if status != http.StatusServiceUnavailable || resp.Success || len(f.sent) != 0 {
		t.Errorf("sendPoll = %d %+v, want 503 and nothing sent", status, resp)
	}
}

func TestPollEndpointRejectsWrongMethodAndBadBody(t *testing.T) { // SEND-01 (HTTP)
	handler := pollHandler(func(r *http.Request) (int, any) {
		var req SendPollRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			return http.StatusBadRequest, nil
		}
		return http.StatusOK, SendMessageResponse{Success: true}
	})
	get := httptest.NewRecorder()
	handler(get, httptest.NewRequest(http.MethodGet, "/api/send_poll", nil))
	if get.Code != http.StatusMethodNotAllowed {
		t.Errorf("GET = %d, want 405", get.Code)
	}
	bad := httptest.NewRecorder()
	handler(bad, httptest.NewRequest(http.MethodPost, "/api/send_poll", strings.NewReader("{")))
	if bad.Code != http.StatusBadRequest {
		t.Errorf("bad body = %d, want 400", bad.Code)
	}
	ok := httptest.NewRecorder()
	handler(ok, httptest.NewRequest(http.MethodPost, "/api/send_poll", strings.NewReader(`{"recipient":"x"}`)))
	if ok.Code != http.StatusOK || !strings.Contains(ok.Body.String(), `"success":true`) {
		t.Errorf("good body = %d %s, want 200 success", ok.Code, ok.Body.String())
	}
}

// votingFake adds the vote builder to the fake client and records how it was called.
func votingFake() (*fakePollClient, *types.MessageInfo, *[]string) {
	f := newFakePollClient()
	var info types.MessageInfo
	var names []string
	f.env.resolveSender = func(user string) types.JID { return types.JID{User: user, Server: types.DefaultUserServer} }
	f.env.buildVote = func(i *types.MessageInfo, options []string) (*waE2E.Message, error) {
		info, names = *i, options
		return &waE2E.Message{PollUpdateMessage: &waE2E.PollUpdateMessage{}}, nil
	}
	return f, &info, &names
}

func storeSamplePollBy(t *testing.T, store *MessageStore, author string, selectable int) {
	t.Helper()
	if _, err := store.StorePoll(Poll{ID: "POLL1", ChatJID: "chat@g.us", Author: author, Question: "Almoço?",
		Options: []string{"Sim", "Não", "Talvez"}, SelectableCount: selectable}); err != nil {
		t.Fatal(err)
	}
}

func TestVotePollStoresOurVoteWithTheHashesOfTheChosenOptions(t *testing.T) { // VOTE-01, VOTE-05
	store := newTestStore(t)
	storeSamplePollBy(t, store, "5511999990000", 0)
	f, info, names := votingFake()

	status, resp := votePoll(f.env, store, VotePollRequest{ChatJID: "chat@g.us", MessageID: "POLL1", Options: []string{"sim", "TALVEZ"}}, waLog.Noop)

	if status != http.StatusOK || !resp.Success {
		t.Fatalf("votePoll = %d %+v, want 200 success", status, resp)
	}
	if !reflect.DeepEqual(*names, []string{"Sim", "Talvez"}) {
		t.Errorf("voted names = %v, want the poll's own spelling [Sim Talvez]", *names)
	}
	if info.ID != "POLL1" || info.Chat.String() != "chat@g.us" || info.Sender.User != "5511999990000" || info.IsFromMe || !info.IsGroup {
		t.Errorf("poll info = %+v, want POLL1 in the group, by 5511999990000, not ours", info)
	}
	want := []string{hex.EncodeToString(sha("Sim")), hex.EncodeToString(sha("Talvez"))}
	if got := votesOf(t, store, "POLL1")["5511900000000"].Hashes; !reflect.DeepEqual(got, want) {
		t.Errorf("our stored vote = %v, want %v", got, want)
	}
	if len(f.sent) != 1 {
		t.Errorf("%d messages sent, want 1", len(f.sent))
	}
}

func TestVotePollInOurOwnPollMarksItAsFromMe(t *testing.T) { // VOTE-01
	store := newTestStore(t)
	storeSamplePollBy(t, store, "5511900000000", 0)
	f, info, _ := votingFake()

	votePoll(f.env, store, VotePollRequest{ChatJID: "chat@g.us", MessageID: "POLL1", Options: []string{"Sim"}}, waLog.Noop)

	if !info.IsFromMe || info.Sender.User != "5511900000000" {
		t.Errorf("poll info = %+v, want IsFromMe by us", info)
	}
}

func TestVotePollWithNoOptionsRetractsTheVote(t *testing.T) { // VOTE-02
	store := newTestStore(t)
	storeSamplePollBy(t, store, "5511999990000", 0)
	f, _, names := votingFake()
	store.StorePollVote("POLL1", "chat@g.us", "5511900000000", []string{"aa"}, time.Date(2026, 11, 6, 11, 0, 0, 0, time.UTC))

	status, resp := votePoll(f.env, store, VotePollRequest{ChatJID: "chat@g.us", MessageID: "POLL1", Options: []string{}}, waLog.Noop)

	if status != http.StatusOK || !resp.Success || len(*names) != 0 {
		t.Fatalf("votePoll = %d %+v names %v, want an empty vote sent", status, resp, *names)
	}
	vote, ok := votesOf(t, store, "POLL1")["5511900000000"]
	if !ok || len(vote.Hashes) != 0 {
		t.Errorf("our vote = %+v (present %v), want retracted", vote, ok)
	}
}

func TestVotePollRefusesWhatItCannotVote(t *testing.T) { // VOTE-03, VOTE-04
	for name, c := range map[string]struct {
		selectable int
		poll       string
		options    []string
		status     int
	}{
		"unknown option":             {0, "POLL1", []string{"Nunca"}, http.StatusBadRequest},
		"two on a single answer":     {1, "POLL1", []string{"Sim", "Não"}, http.StatusBadRequest},
		"the same option twice":      {0, "POLL1", []string{"Sim", "sim"}, http.StatusBadRequest},
		"poll that is not in the db": {0, "OTHER", []string{"Sim"}, http.StatusNotFound},
	} {
		store := newTestStore(t)
		storeSamplePollBy(t, store, "5511999990000", c.selectable)
		f, _, _ := votingFake()

		status, resp := votePoll(f.env, store, VotePollRequest{ChatJID: "chat@g.us", MessageID: c.poll, Options: c.options}, waLog.Noop)

		if status != c.status || resp.Success || len(f.sent) != 0 {
			t.Errorf("%s: votePoll = %d %+v (sent %d), want %d and nothing sent", name, status, resp, len(f.sent), c.status)
		}
		if n := len(votesOf(t, store, "POLL1")); n != 0 {
			t.Errorf("%s: %d votes stored, want none", name, n)
		}
	}
}

func TestVotePollSendFailureStoresNoVote(t *testing.T) { // VOTE-05
	store := newTestStore(t)
	storeSamplePollBy(t, store, "5511999990000", 0)
	f, _, _ := votingFake()
	f.err = errors.New("network down")

	status, resp := votePoll(f.env, store, VotePollRequest{ChatJID: "chat@g.us", MessageID: "POLL1", Options: []string{"Sim"}}, waLog.Noop)

	if status != http.StatusInternalServerError || resp.Success {
		t.Errorf("votePoll = %d %+v, want 500 failure", status, resp)
	}
	if n := len(votesOf(t, store, "POLL1")); n != 0 {
		t.Errorf("%d votes stored after a failed send, want none", n)
	}
}

func seedMessage(t *testing.T, store *MessageStore, id string, fromMe bool) {
	t.Helper()
	ts := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	if err := store.StoreChat("chat@g.us", "Chat", ts); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreMessage(id, "chat@g.us", "5511900000000", "texto "+id, ts, fromMe, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}
}

func messageCount(t *testing.T, store *MessageStore, id string) int {
	t.Helper()
	var n int
	if err := store.db.QueryRow("SELECT COUNT(*) FROM messages WHERE id = ? AND chat_jid = 'chat@g.us'", id).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func revokingFake() (*fakePollClient, *types.JID, *string) {
	f := newFakePollClient()
	var chat types.JID
	var id string
	f.env.buildRevoke = func(c types.JID, messageID string) *waE2E.Message {
		chat, id = c, messageID
		return &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{Type: waE2E.ProtocolMessage_REVOKE.Enum()}}
	}
	return f, &chat, &id
}

func TestDeleteMessageRevokesOurMessageAndRemovesItLocally(t *testing.T) { // DEL-01
	store := newTestStore(t)
	seedMessage(t, store, "MINE", true)
	seedMessage(t, store, "OTHER", true)
	f, chat, id := revokingFake()

	status, resp := deleteMessage(f.env, store, DeleteMessageRequest{ChatJID: "chat@g.us", MessageID: "MINE"}, waLog.Noop)

	if status != http.StatusOK || !resp.Success {
		t.Fatalf("deleteMessage = %d %+v, want 200 success", status, resp)
	}
	if chat.String() != "chat@g.us" || *id != "MINE" || len(f.sent) != 1 || f.to[0].String() != "chat@g.us" {
		t.Errorf("revoke for %s/%s sent to %v (%d sent), want MINE in chat@g.us", chat, *id, f.to, len(f.sent))
	}
	if messageCount(t, store, "MINE") != 0 {
		t.Error("the deleted message must be removed locally")
	}
	if messageCount(t, store, "OTHER") != 1 {
		t.Error("another message must stay")
	}
}

func TestDeleteMessageOfAPollRemovesPollAndVotes(t *testing.T) { // DEL-01
	store := newTestStore(t)
	seedMessage(t, store, "POLL1", true)
	storeSamplePollBy(t, store, "5511900000000", 0)
	store.StorePollVote("POLL1", "chat@g.us", "alice", []string{"aa"}, time.Now())
	f, _, _ := revokingFake()

	if status, resp := deleteMessage(f.env, store, DeleteMessageRequest{ChatJID: "chat@g.us", MessageID: "POLL1"}, waLog.Noop); status != http.StatusOK {
		t.Fatalf("deleteMessage = %d %+v, want 200", status, resp)
	}

	if _, found, _ := store.GetPoll("POLL1", "chat@g.us"); found || len(votesOf(t, store, "POLL1")) != 0 || messageCount(t, store, "POLL1") != 0 {
		t.Error("the message, the poll and its votes must all be removed")
	}
}

func TestDeleteMessageRefusesWhatIsNotOurs(t *testing.T) { // DEL-02
	store := newTestStore(t)
	seedMessage(t, store, "THEIRS", false)
	for name, c := range map[string]struct {
		id     string
		status int
	}{
		"someone else's message": {"THEIRS", http.StatusForbidden},
		"unknown message":        {"NOPE", http.StatusNotFound},
	} {
		f, _, _ := revokingFake()
		status, resp := deleteMessage(f.env, store, DeleteMessageRequest{ChatJID: "chat@g.us", MessageID: c.id}, waLog.Noop)
		if status != c.status || resp.Success || len(f.sent) != 0 {
			t.Errorf("%s: deleteMessage = %d %+v (sent %d), want %d and nothing sent", name, status, resp, len(f.sent), c.status)
		}
	}
	if messageCount(t, store, "THEIRS") != 1 {
		t.Error("someone else's message must stay")
	}
}

func TestDeleteMessageFailureKeepsEverythingLocal(t *testing.T) { // DEL-03
	store := newTestStore(t)
	seedMessage(t, store, "POLL1", true)
	storeSamplePollBy(t, store, "5511900000000", 0)
	store.StorePollVote("POLL1", "chat@g.us", "alice", []string{"aa"}, time.Now())
	f, _, _ := revokingFake()
	f.err = errors.New("network down")

	status, resp := deleteMessage(f.env, store, DeleteMessageRequest{ChatJID: "chat@g.us", MessageID: "POLL1"}, waLog.Noop)

	if status != http.StatusInternalServerError || resp.Success {
		t.Errorf("deleteMessage = %d %+v, want 500 failure", status, resp)
	}
	if _, found, _ := store.GetPoll("POLL1", "chat@g.us"); !found || len(votesOf(t, store, "POLL1")) != 1 || messageCount(t, store, "POLL1") != 1 {
		t.Error("nothing local may be removed when the revoke fails")
	}
}
