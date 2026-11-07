package main

import (
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	waLog "go.mau.fi/whatsmeow/util/log"
)

func ptrInt64(v int64) *int64 { return &v }

// withEnd sets the deadline of a poll creation message.
func withEnd(msg *waE2E.Message, at time.Time) *waE2E.Message {
	msg.PollCreationMessage.EndTime = ptrInt64(at.UnixMilli())
	return msg
}

type pollExtras struct {
	endsMs  int64
	quiz    bool
	correct string
	notif   bool
}

func readExtras(t *testing.T, store *MessageStore, id string) pollExtras {
	t.Helper()
	var r pollExtras
	var ends *int64
	var quiz, notified *bool
	var correct *string
	if err := store.db.QueryRow("SELECT ends_at_ms, quiz, correct_answer, closed_notified FROM polls WHERE id = ?", id).
		Scan(&ends, &quiz, &correct, &notified); err != nil {
		t.Fatalf("poll %s: %v", id, err)
	}
	if ends != nil {
		r.endsMs = *ends
	}
	if quiz != nil {
		r.quiz = *quiz
	}
	if correct != nil {
		r.correct = *correct
	}
	if notified != nil {
		r.notif = *notified
	}
	return r
}

func TestASentQuizKeepsItsDeadlineAndRightAnswer(t *testing.T) { // POLLX-01
	store := newTestStore(t)
	f := newFakePollClient()
	before := time.Now()

	_, resp := sendPoll(f.env, store, SendPollRequest{Recipient: "chat@g.us", Question: "Capital?",
		Options: []string{"Brasília", "Rio"}, QuizAnswer: "brasília", ClosesInMinutes: 30}, waLog.Noop)
	if !resp.Success {
		t.Fatalf("send failed: %+v", resp)
	}

	got := readExtras(t, store, "SENT1")
	if !got.quiz || got.correct != "Brasília" {
		t.Errorf("extras = %+v, want a quiz with the answer in the poll's spelling", got)
	}
	if low := before.Add(30 * time.Minute).UnixMilli(); got.endsMs < low || got.endsMs > time.Now().Add(30*time.Minute).UnixMilli() {
		t.Errorf("ends_at_ms = %d, want about now + 30 minutes", got.endsMs)
	}
}

func TestAReceivedPollKeepsItsDeadline(t *testing.T) { // POLLX-02
	store := newTestStore(t)
	msg := creationMessage("Q?", []string{"A", "B"}, 0)
	msg.PollCreationMessage.EndTime = ptrInt64(1900000000000)

	handleMessage(nil, store, pollEvent("chat@g.us", alice, "P9", msg, time.Now()), waLog.Noop)

	if got := readExtras(t, store, "P9"); got.endsMs != 1900000000000 || got.quiz {
		t.Errorf("extras = %+v", got)
	}
}

func TestADeadlineThatPassedIsAnnouncedOnceWithIdsAndACount(t *testing.T) { // POLLX-03
	store, withText, without := hookedStore(t)
	now := time.Now()
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "P1", withEnd(creationMessage("Secret question", []string{"A", "B"}, 0), now.Add(-time.Minute)), now.Add(-time.Hour)), waLog.Noop)
	handleMessageWith(decryptors{vote: decryptTo(sha("A"))}, "", nil, store,
		pollEvent("chat@g.us", alice, "V1", voteUpdate("P1", now.Add(-2*time.Minute).UnixMilli()), now), waLog.Noop)
	store.StorePollVote("P1", "chat@g.us", "5511888880000", nil, now.Add(-90*time.Second)) // a retracted vote does not count

	n, err := announceClosedPolls(store, now)
	if err != nil || n != 1 {
		t.Fatalf("announce = (%d, %v), want (1, nil)", n, err)
	}
	again, _ := announceClosedPolls(store, now.Add(time.Hour))

	e := oneOf(t, withText, "poll_closed")
	if e.body.Data["poll_id"] != "P1" || e.body.Data["total_voters"] != float64(1) || e.body.Data["chat_jid"] != "chat@g.us" {
		t.Errorf("poll_closed = %v", e.body.Data)
	}
	if again != 0 {
		t.Error("a poll must be announced once")
	}
	for _, h := range append(withText.got(), without.got()...) {
		if h.body.Type == "poll_closed" && (h.body.Data["question"] != nil || h.body.Data["options"] != nil) {
			t.Errorf("the question or options reached poll_closed: %s", h.raw)
		}
	}
	if !readExtras(t, store, "P1").notif {
		t.Error("the poll must be marked as announced")
	}
}

func TestAPollNotYetDueIsNotAnnounced(t *testing.T) { // POLLX-03
	store, withText, _ := hookedStore(t)
	now := time.Now()
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "P1", withEnd(creationMessage("Q", []string{"A", "B"}, 0), now.Add(time.Hour)), now), waLog.Noop)
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "P2", creationMessage("No deadline", []string{"A", "B"}, 0), now), waLog.Noop)

	n, _ := announceClosedPolls(store, now)
	time.Sleep(60 * time.Millisecond)

	if n != 0 {
		t.Errorf("announced %d polls, want 0", n)
	}
	for _, h := range withText.got() {
		if h.body.Type == "poll_closed" {
			t.Errorf("announced a poll that is not due: %s", h.raw)
		}
	}
	if readExtras(t, store, "P1").notif || readExtras(t, store, "P2").notif {
		t.Error("nothing may be marked")
	}
}

func TestAPollThatClosedLongAgoIsMarkedButNotDelivered(t *testing.T) { // POLLX-04
	store, withText, _ := hookedStore(t)
	now := time.Now()
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "OLD", withEnd(creationMessage("Q", []string{"A", "B"}, 0), now.Add(-3*time.Hour)), now.Add(-4*time.Hour)), waLog.Noop)

	n, _ := announceClosedPolls(store, now)
	time.Sleep(60 * time.Millisecond)

	if n != 1 || !readExtras(t, store, "OLD").notif {
		t.Errorf("announced = %d, marked = %v", n, readExtras(t, store, "OLD").notif)
	}
	for _, h := range withText.got() {
		if h.body.Type == "poll_closed" {
			t.Errorf("a stale close was delivered: %s", h.raw)
		}
	}
}

func TestAnOwnPollsClosingIsDeliveredWithoutIncludeOwn(t *testing.T) { // POLLX-05
	store, withText, _ := hookedStore(t)
	f := newFakePollClient()
	sendPoll(f.env, store, SendPollRequest{Recipient: "chat@g.us", Question: "Q", Options: []string{"A", "B"}, ClosesInMinutes: 1}, waLog.Noop)

	announceClosedPolls(store, time.Now().Add(2*time.Minute))

	oneOf(t, withText, "poll_closed")
}

func TestPollColumnsMigrationIsIdempotent(t *testing.T) { // POLLX-06
	store := newTestStore(t)
	if err := ensurePollColumns(store.db); err != nil {
		t.Fatal(err)
	}
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "P1", creationMessage("Q", []string{"A", "B"}, 0), time.Now()), waLog.Noop)
	if err := ensurePollColumns(store.db); err != nil {
		t.Fatal(err)
	}
	if p, ok, _ := store.GetPoll("P1", "chat@g.us"); !ok || p.Question != "Q" {
		t.Errorf("the poll changed: %+v %v", p, ok)
	}
}

func TestAnAbsurdDeadlineInAReceivedPollIsDropped(t *testing.T) { // POLLX-12
	now := time.Now()
	for name, ms := range map[string]int64{"negative": -5, "tiny": 1000, "huge": 9223372036854775807,
		"far future": now.Add(11 * 365 * 24 * time.Hour).UnixMilli()} {
		if got := sanePollEnd(ms, now); got != 0 {
			t.Errorf("%s (%d) -> %d, want 0", name, ms, got)
		}
	}
	for name, ms := range map[string]int64{"in a minute": now.Add(time.Minute).UnixMilli(), "yesterday": now.Add(-24 * time.Hour).UnixMilli(),
		"in nine years": now.Add(9 * 365 * 24 * time.Hour).UnixMilli()} {
		if got := sanePollEnd(ms, now); got != ms {
			t.Errorf("%s (%d) -> %d, want it kept", name, ms, got)
		}
	}
	store := newTestStore(t)
	msg := creationMessage("Q?", []string{"A", "B"}, 0)
	msg.PollCreationMessage.EndTime = ptrInt64(9223372036854775807)
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "P9", msg, now), waLog.Noop)
	if got := readExtras(t, store, "P9"); got.endsMs != 0 {
		t.Errorf("ends_at_ms = %d, want 0 for an absurd deadline", got.endsMs)
	}
}
