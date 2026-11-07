package main

import (
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	waLog "go.mau.fi/whatsmeow/util/log"
)

const (
	dmPhoneChat = "5511999990000@s.whatsapp.net"
	dmLIDChat   = "100000000000003@lid"
)

// directPollStore has a person known by both addresses and a poll stored under pollChat.
func directPollStore(t *testing.T, pollChat string) *MessageStore {
	t.Helper()
	store := newTestStore(t)
	if err := store.UpsertIdentity("100000000000003", "5511999990000", "Ana", ""); err != nil {
		t.Fatal(err)
	}
	if _, err := store.StorePoll(Poll{ID: "POLL1", ChatJID: pollChat, Author: "5511900000000", Question: "Lunch?",
		Options: []string{"Yes", "No"}, AllowAddOption: true}); err != nil {
		t.Fatal(err)
	}
	return store
}

func votersIn(t *testing.T, store *MessageStore, chat string) []string {
	t.Helper()
	votes, err := store.PollVotes("POLL1", chat)
	if err != nil {
		t.Fatal(err)
	}
	var who []string
	for _, v := range votes {
		who = append(who, v.Voter)
	}
	return who
}

func castVote(store *MessageStore, chat, sender string) {
	recordPoll(store, "", decryptTo(sha("Yes")), pollEvent(chat, sender, "V1", voteUpdate("POLL1", time.Now().UnixMilli()), time.Now()), waLog.Noop)
}

func TestVoteArrivingByTheLIDChatCountsForThePollStoredUnderThePhone(t *testing.T) { // DM-01
	store := directPollStore(t, dmPhoneChat)

	castVote(store, dmLIDChat, "100000000000003@lid")

	if got := votersIn(t, store, dmPhoneChat); len(got) != 1 {
		t.Errorf("votes under the poll's chat = %v, want the vote", got)
	}
	if got := votersIn(t, store, dmLIDChat); len(got) != 0 {
		t.Errorf("votes under the other address = %v, want none", got)
	}
}

func TestVoteArrivingByThePhoneChatCountsForThePollStoredUnderTheLID(t *testing.T) { // DM-01
	store := directPollStore(t, dmLIDChat)

	castVote(store, dmPhoneChat, "5511999990000@s.whatsapp.net")

	if got := votersIn(t, store, dmLIDChat); len(got) != 1 {
		t.Errorf("votes under the poll's chat = %v, want the vote", got)
	}
	if got := votersIn(t, store, dmPhoneChat); len(got) != 0 {
		t.Errorf("votes under the other address = %v, want none", got)
	}
}

func TestVoteInTheChatOfThePollStaysThere(t *testing.T) { // DM-02
	store := directPollStore(t, dmPhoneChat)

	castVote(store, dmPhoneChat, "5511999990000@s.whatsapp.net")

	if got := votersIn(t, store, dmPhoneChat); len(got) != 1 {
		t.Errorf("votes = %v, want the vote under the event's chat", got)
	}
}

func TestVoteForAnUnknownPollStaysUnderTheEventChat(t *testing.T) { // DM-03
	store := directPollStore(t, dmPhoneChat)

	recordPoll(store, "", decryptTo(sha("Yes")),
		pollEvent(dmLIDChat, "100000000000003@lid", "V1", voteUpdate("OTHER", time.Now().UnixMilli()), time.Now()), waLog.Noop)

	votes, _ := store.PollVotes("OTHER", dmLIDChat)
	if len(votes) != 1 {
		t.Errorf("votes under the event chat = %d, want 1 (the poll may arrive later)", len(votes))
	}
}

func TestVoteFromAPersonWithoutIdentityStaysUnderTheEventChat(t *testing.T) { // DM-03
	store := directPollStore(t, dmPhoneChat)

	castVote(store, "999@lid", "999@lid")

	if got := votersIn(t, store, "999@lid"); len(got) != 1 {
		t.Errorf("votes = %v, want the vote under the event chat", got)
	}
	if got := votersIn(t, store, dmPhoneChat); len(got) != 0 {
		t.Errorf("votes under the poll's chat = %v, want none: that person is not known to be the same", got)
	}
}

func TestAddedOptionFollowsThePollToTheOtherAddress(t *testing.T) { // DM-04
	store := directPollStore(t, dmPhoneChat)
	evt := pollEvent(dmLIDChat, "100000000000003@lid", "ENV1", envelope(waE2E.SecretEncryptedMessage_POLL_ADD_OPTION, "POLL1"), time.Now())

	recordSecretEdit(store, "", decryptedAs(addOption("Maybe")), evt, waLog.Noop)

	poll, found, _ := store.GetPoll("POLL1", dmPhoneChat)
	if !found || strings.Join(poll.Options, "|") != "Yes|No|Maybe" {
		t.Errorf("options = %v (found %v), want Yes|No|Maybe on the poll's own chat", poll.Options, found)
	}
	if _, stray, _ := store.GetPoll("POLL1", dmLIDChat); stray {
		t.Error("the edit must not create a poll under the other address")
	}
}

func TestQuestionEditFollowsThePollToTheOtherAddress(t *testing.T) { // DM-04
	store := newTestStore(t)
	store.UpsertIdentity("100000000000003", "5511999990000", "Ana", "")
	store.StoreChat(dmPhoneChat, "Ana", time.Now())
	if err := store.StoreMessage("POLL1", dmPhoneChat, "100000000000003", "[poll] Lunch?", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}
	store.StorePoll(Poll{ID: "POLL1", ChatJID: dmPhoneChat, Author: "100000000000003", Question: "Lunch?", Options: []string{"Yes", "No"}})
	evt := pollEvent(dmLIDChat, "100000000000003@lid", "ENV1", envelope(waE2E.SecretEncryptedMessage_POLL_EDIT, "POLL1"), time.Now())

	recordSecretEdit(store, "", decryptedAs(editedPoll("Lunch today?")), evt, waLog.Noop)

	if poll, _, _ := store.GetPoll("POLL1", dmPhoneChat); poll.Question != "Lunch today?" {
		t.Errorf("question = %q, want the edited one", poll.Question)
	}
}

func TestGroupVoteIsNeverMovedToAPersonsChat(t *testing.T) { // DM-05
	store := directPollStore(t, dmPhoneChat)

	castVote(store, "group@g.us", "100000000000003@lid")

	if got := votersIn(t, store, "group@g.us"); len(got) != 1 {
		t.Errorf("votes under the group = %v, want the vote", got)
	}
	if got := votersIn(t, store, dmPhoneChat); len(got) != 0 {
		t.Errorf("votes under the person's chat = %v, want none", got)
	}
}
