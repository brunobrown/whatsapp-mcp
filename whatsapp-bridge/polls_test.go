package main

import (
	"reflect"
	"testing"
	"time"
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
