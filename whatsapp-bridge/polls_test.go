package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"reflect"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

func samplePoll() Poll {
	return Poll{
		ID: "POLL1", ChatJID: "chat@g.us", Question: "Almoço?", Options: []string{"Sim", "Não"},
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
	if err := store.StorePoll(want); err != nil {
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
	if err := store.StorePoll(samplePoll()); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	if _, err := store.StorePollVote("POLL1", "chat@g.us", "alice", []string{"aa"}, now); err != nil {
		t.Fatal(err)
	}

	if err := store.StorePoll(samplePoll()); err != nil {
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
	if err := store.StorePoll(Poll{ID: "LATER", ChatJID: "chat@g.us", Question: "Q", Options: []string{"A", "B"}}); err != nil {
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

func TestDeletePollRemovesVotesAndOtherPollsStay(t *testing.T) { // DEL-01 (armazenamento)
	store := newTestStore(t)
	now := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	other := samplePoll()
	other.ID = "KEEP"
	for _, p := range []Poll{samplePoll(), other} {
		if err := store.StorePoll(p); err != nil {
			t.Fatal(err)
		}
		store.StorePollVote(p.ID, p.ChatJID, "alice", []string{"aa"}, now)
	}

	if err := store.DeletePoll("POLL1", "chat@g.us"); err != nil {
		t.Fatal(err)
	}

	if _, found, _ := store.GetPoll("POLL1", "chat@g.us"); found {
		t.Error("the deleted poll must be gone")
	}
	if len(votesOf(t, store, "POLL1")) != 0 {
		t.Error("the votes of the deleted poll must be gone")
	}
	if _, found, _ := store.GetPoll("KEEP", "chat@g.us"); !found || len(votesOf(t, store, "KEEP")) != 1 {
		t.Error("another poll and its votes must stay")
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
	want := Poll{ID: "POLL1", ChatJID: "chat@g.us", Question: "Almoço?", Options: []string{"Sim", "Não", "Talvez"},
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
	if _, ok := pollFromMessage("chat@g.us", "P", wrapped); !ok {
		t.Error("a poll inside the V4 wrapper must be recognised")
	}
	if _, ok := pollFromMessage("chat@g.us", "P", &waE2E.Message{Conversation: proto.String("oi")}); ok {
		t.Error("a text message is not a poll")
	}
}
