package main

import (
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

func revokeMessage(id string) *waE2E.Message {
	return &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{
		Type: waE2E.ProtocolMessage_REVOKE.Enum(), Key: &waCommon.MessageKey{ID: proto.String(id)}}}
}

type deletedRow struct {
	content, mediaType, filename, localPath, deletedAt string
}

func readDeleted(t *testing.T, store *MessageStore, id string) deletedRow {
	t.Helper()
	var r deletedRow
	var mt, fn, lp, da *string
	if err := store.db.QueryRow("SELECT content, media_type, filename, local_path, deleted_at FROM messages WHERE id = ? AND chat_jid = 'chat@g.us'", id).
		Scan(&r.content, &mt, &fn, &lp, &da); err != nil {
		t.Fatalf("row %s: %v", id, err)
	}
	for dst, src := range map[*string]*string{&r.mediaType: mt, &r.filename: fn, &r.localPath: lp, &r.deletedAt: da} {
		if src != nil {
			*dst = *src
		}
	}
	return r
}

func TestDeletionByTheAuthorErasesTheMessageButKeepsTheRow(t *testing.T) { // DEL-01
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "private words", false)
	before := countMessages(t, store)

	handleMessage(nil, store, pollEvent("chat@g.us", alice, "R1", revokeMessage("M1"), time.Now()), waLog.Noop)

	got := readDeleted(t, store, "M1")
	if got.content != deletedMarker || got.deletedAt == "" {
		t.Errorf("row = %+v, want the marker and a deleted_at", got)
	}
	if countMessages(t, store) != before {
		t.Error("the row and no other must remain: the revoke is not a message")
	}
}

func TestDeletionErasesMediaDetailsAndTheDownloadedFile(t *testing.T) { // DEL-02
	store := newTestStore(t)
	now := time.Now()
	path := seedDownloaded(t, store, "IMG", time.Hour, now) // sender "alice"
	if !exists(path) {
		t.Fatal("setup: the file is not there")
	}

	updated, err := store.MarkDeleted("IMG", "chat@g.us", "alice", now, false)

	if err != nil || !updated {
		t.Fatalf("MarkDeleted = (%v, %v)", updated, err)
	}
	got := readDeleted(t, store, "IMG")
	if exists(path) || got.mediaType != "" || got.filename != "" || got.localPath != "" || got.content != deletedMarker {
		t.Errorf("media not erased: %+v (file exists: %v)", got, exists(path))
	}
}

func TestDeletionOfAPollRemovesItsQuestionVotesAndReactions(t *testing.T) { // DEL-03
	store := newTestStore(t)
	now := time.Now()
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "POLL1", creationMessage("Secret question", []string{"A", "B"}, 0), now), waLog.Noop)
	handleMessageWith(decryptors{vote: decryptTo(sha("A"))}, "", nil, store,
		pollEvent("chat@g.us", alice, "V1", voteUpdate("POLL1", now.Add(time.Second).UnixMilli()), now), waLog.Noop)
	handleMessageWith(decryptors{}, "", nil, store, pollEvent("chat@g.us", alice, "R1", reactionMessage("POLL1", "👍", now.Add(2*time.Second).UnixMilli()), now), waLog.Noop)
	count := func(table, col string) int {
		var n int
		if err := store.db.QueryRow("SELECT COUNT(*) FROM " + table + " WHERE " + col + " = 'POLL1'").Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	if count("polls", "id") != 1 || count("poll_votes", "poll_id") != 1 || count("reactions", "target_id") != 1 {
		t.Fatal("setup: the poll, vote and reaction should exist")
	}

	handleMessage(nil, store, pollEvent("chat@g.us", alice, "D1", revokeMessage("POLL1"), now.Add(3*time.Second)), waLog.Noop)

	if count("polls", "id")+count("poll_votes", "poll_id")+count("reactions", "target_id") != 0 {
		t.Error("the poll question, votes and reactions must be erased")
	}
	if got := readDeleted(t, store, "POLL1"); got.content != deletedMarker {
		t.Errorf("content = %q", got.content)
	}
}

func TestOnlyTheAuthorCanDeleteAndOnlyOnce(t *testing.T) { // DEL-04
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "keep me", false)

	handleMessage(nil, store, pollEvent("chat@g.us", "5511888880000@s.whatsapp.net", "R1", revokeMessage("M1"), time.Now()), waLog.Noop)
	if got := readDeleted(t, store, "M1"); got.content != "keep me" || got.deletedAt != "" {
		t.Errorf("someone else's request changed the message: %+v", got)
	}

	if ok, _ := store.MarkDeleted("M1", "chat@g.us", "5511999990000", time.Now(), false); !ok {
		t.Fatal("the author's request must work")
	}
	if ok, _ := store.MarkDeleted("M1", "chat@g.us", "5511999990000", time.Now(), false); ok {
		t.Error("a second deletion must change nothing")
	}
	if ok, err := store.MarkDeleted("NOPE", "chat@g.us", "5511999990000", time.Now(), false); ok || err != nil {
		t.Errorf("unknown message = (%v, %v), want (false, nil)", ok, err)
	}
}

func TestKeepDeletedContentIsAnExplicitChoice(t *testing.T) { // DEL-05
	for value, want := range map[string]bool{"": false, "false": false, "1": false, "yes": false, "true": true, " TRUE ": true} {
		t.Setenv("WHATSAPP_KEEP_DELETED_CONTENT", value)
		if got := keepDeletedContent(); got != want {
			t.Errorf("%q -> %v, want %v", value, got, want)
		}
	}
	t.Setenv("WHATSAPP_KEEP_DELETED_CONTENT", "true")
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "kept text", false)

	handleMessage(nil, store, pollEvent("chat@g.us", alice, "R1", revokeMessage("M1"), time.Now()), waLog.Noop)

	if got := readDeleted(t, store, "M1"); got.content != "kept text" || got.deletedAt == "" {
		t.Errorf("row = %+v, want the text kept and deleted_at set", got)
	}
}

func TestADeletionDoesNotMoveTheChatsLastMessageTime(t *testing.T) { // DEL-06
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "x", false)
	var before string
	store.db.QueryRow("SELECT last_message_time FROM chats WHERE jid = 'chat@g.us'").Scan(&before)

	handleMessage(nil, store, pollEvent("chat@g.us", alice, "R1", revokeMessage("M1"), time.Now().Add(48*time.Hour)), waLog.Noop)

	var after string
	store.db.QueryRow("SELECT last_message_time FROM chats WHERE jid = 'chat@g.us'").Scan(&after)
	if before != after {
		t.Errorf("last_message_time moved from %q to %q", before, after)
	}
}

func TestADeletionIsDeliveredWithoutAnyText(t *testing.T) { // DEL-07
	store, withText, without := hookedStore(t)
	seedText(t, store, "M1", "5511999990000", "SECRET-WORDS", false)
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "R1", revokeMessage("M1"), time.Now()), waLog.Noop)

	e := oneOf(t, withText, "message_deleted")
	if e.body.Data["message_id"] != "M1" || e.body.Data["chat_jid"] != "chat@g.us" || e.body.Data["sender"] != "5511999990000" {
		t.Errorf("message_deleted = %v", e.body.Data)
	}
	for _, rc := range []*receiver{withText, without} {
		for _, h := range rc.got() {
			if strings.Contains(string(h.raw), "SECRET-WORDS") {
				t.Error("the deleted text must never be sent")
			}
		}
	}
	oneOf(t, without, "message_deleted")
}

func TestAnIgnoredDeletionIsNotDelivered(t *testing.T) { // DEL-07
	store, withText, _ := hookedStore(t)
	seedText(t, store, "M1", "5511999990000", "x", false)
	handleMessage(nil, store, pollEvent("chat@g.us", "5511888880000@s.whatsapp.net", "R1", revokeMessage("M1"), time.Now()), waLog.Noop)
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "R2", revokeMessage("UNKNOWN"), time.Now()), waLog.Noop)
	time.Sleep(60 * time.Millisecond)
	if n := len(withText.got()); n != 0 {
		t.Errorf("%d events for deletions that changed nothing", n)
	}
}

func TestMigrationAddsDeletedAtWithoutTouchingRows(t *testing.T) { // DEL-08
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "x", false)
	if err := ensureMessageColumns(store.db); err != nil { // idempotent: a second run is a no-op
		t.Fatal(err)
	}
	if got := readDeleted(t, store, "M1"); got.content != "x" || got.deletedAt != "" {
		t.Errorf("row = %+v", got)
	}
}
