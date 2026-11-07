package main

import (
	"errors"
	"testing"
	"time"

	waProto "go.mau.fi/whatsmeow/proto/waE2E"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

// seedMedia stores a media message with all the material that could decrypt it, and deletes it as its author.
func seedDeletedMedia(t *testing.T, store *MessageStore) {
	t.Helper()
	ts := time.Now()
	store.StoreChat("chat@g.us", "Chat", ts)
	if err := store.StoreMessage("IMG", "chat@g.us", "5511999990000", "a caption", ts, false, "image", "photo.jpg",
		"https://cdn.example/x", []byte{1, 2, 3}, []byte{4, 5}, []byte{6, 7}, 99); err != nil {
		t.Fatal(err)
	}
	if err := store.SetMessageMeta("IMG", "chat@g.us", MessageMeta{Mimetype: "image/jpeg", Forwarded: true, QuotedID: "Q1"}); err != nil {
		t.Fatal(err)
	}
	if ok, err := store.MarkDeleted("IMG", "chat@g.us", "5511999990000", ts, false); !ok || err != nil {
		t.Fatalf("MarkDeleted = (%v, %v)", ok, err)
	}
}

func TestDeletionErasesEveryPieceOfMediaMaterial(t *testing.T) { // DEL-02
	store := newTestStore(t)
	seedDeletedMedia(t, store)

	var url, mimetype, quoted *string
	var key, sha, encSha []byte
	var length uint64
	var forwarded bool
	if err := store.db.QueryRow("SELECT url, media_key, file_sha256, file_enc_sha256, file_length, mimetype, quoted_id, forwarded FROM messages WHERE id = 'IMG'").
		Scan(&url, &key, &sha, &encSha, &length, &mimetype, &quoted, &forwarded); err != nil {
		t.Fatal(err)
	}
	if (url != nil && *url != "") || len(key) != 0 || len(sha) != 0 || len(encSha) != 0 || length != 0 ||
		(mimetype != nil && *mimetype != "") || (quoted != nil && *quoted != "") || forwarded {
		t.Errorf("material left after the deletion: url=%v key=%v sha=%v enc=%v length=%d mime=%v quoted=%v forwarded=%v",
			url, key, sha, encSha, length, mimetype, quoted, forwarded)
	}
}

func TestADeletedMessageDoesNotComeBackWithAnotherDelivery(t *testing.T) { // DEL-09
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "private words", false)
	store.MarkDeleted("M1", "chat@g.us", "5511999990000", time.Now(), false)

	err := store.StoreMessage("M1", "chat@g.us", "5511999990000", "private words", time.Now(), false, "", "", "", nil, nil, nil, 0)

	if !errors.Is(err, errMessageDeleted) {
		t.Errorf("StoreMessage = %v, want errMessageDeleted", err)
	}
	if got := readDeleted(t, store, "M1"); got.content != deletedMarker || got.deletedAt == "" {
		t.Errorf("row = %+v, the deletion was undone", got)
	}
}

func TestAHistorySyncOrReplayOfADeletedMessageChangesNothing(t *testing.T) { // DEL-09
	store := newTestStore(t)
	seedDeletedMedia(t, store)

	// the same message arrives again through the normal handler
	redelivered := pollEvent("chat@g.us", alice, "IMG", &waProto.Message{ImageMessage: &waProto.ImageMessage{Caption: proto.String("a caption again")}}, time.Now())
	handleMessage(nil, store, redelivered, waLog.Noop)

	got := readDeleted(t, store, "IMG")
	if got.content != deletedMarker || got.mediaType != "" || got.filename != "" || got.localPath != "" {
		t.Errorf("row = %+v, a redelivery brought the message back", got)
	}
}

func TestNoEditCanChangeADeletedMessage(t *testing.T) { // DEL-09
	store := newTestStore(t)
	seedText(t, store, "M1", "5511999990000", "x", false)
	store.MarkDeleted("M1", "chat@g.us", "5511999990000", time.Now(), false)

	plain, _ := store.UpdateMessageContent("M1", "chat@g.us", "5511999990000", "new text after delete")
	secret, _ := store.EditMessage("M1", "chat@g.us", "5511999990000", false, "new text after delete")

	if plain || secret {
		t.Errorf("an edit was applied to a deleted message (plain=%v, encrypted=%v)", plain, secret)
	}
	if got := readDeleted(t, store, "M1"); got.content != deletedMarker {
		t.Errorf("content = %q", got.content)
	}
}

func TestADeletedPollAndItsVotesAndReactionsDoNotComeBack(t *testing.T) { // DEL-09
	store := newTestStore(t)
	now := time.Now()
	handleMessage(nil, store, pollEvent("chat@g.us", alice, "POLL1", creationMessage("Secret question", []string{"A", "B"}, 0), now), waLog.Noop)
	store.MarkDeleted("POLL1", "chat@g.us", "5511999990000", now, false)

	stored, err := store.StorePoll(Poll{ID: "POLL1", ChatJID: "chat@g.us", Author: "5511999990000", Question: "Secret question", Options: []string{"A", "B"}})
	vote, _ := store.StorePollVote("POLL1", "chat@g.us", "5511888880000", []string{"x"}, now)
	react, _ := store.StoreReaction("POLL1", "chat@g.us", "5511888880000", "👍", now)

	if stored || vote || react || err != nil {
		t.Errorf("stored poll=%v vote=%v reaction=%v err=%v, want none", stored, vote, react, err)
	}
	var n int
	for _, q := range []string{"SELECT COUNT(*) FROM polls", "SELECT COUNT(*) FROM poll_votes", "SELECT COUNT(*) FROM reactions"} {
		store.db.QueryRow(q).Scan(&n)
		if n != 0 {
			t.Errorf("%s = %d, want 0", q, n)
		}
	}
}

func TestMetadataAndLocalPathAreNotWrittenToADeletedMessage(t *testing.T) { // DEL-09
	store := newTestStore(t)
	seedDeletedMedia(t, store)

	store.SetMessageMeta("IMG", "chat@g.us", MessageMeta{Mimetype: "image/jpeg", Forwarded: true, QuotedID: "Q1"})
	store.SetLocalPath("IMG", "chat@g.us", "/tmp/somewhere")

	var mime, quoted, path *string
	var forwarded bool
	store.db.QueryRow("SELECT mimetype, quoted_id, local_path, forwarded FROM messages WHERE id = 'IMG'").Scan(&mime, &quoted, &path, &forwarded)
	if (mime != nil && *mime != "") || (quoted != nil && *quoted != "") || (path != nil && *path != "") || forwarded {
		t.Errorf("a deleted message got new data: mime=%v quoted=%v path=%v forwarded=%v", mime, quoted, path, forwarded)
	}
}

func TestDeletedTextIsOverwrittenInTheDatabaseFile(t *testing.T) { // DEL-10
	store := newTestStore(t)
	var on int
	if err := store.db.QueryRow("PRAGMA secure_delete").Scan(&on); err != nil || on == 0 {
		t.Errorf("secure_delete = %d (%v), want it on so erased text does not stay in free pages", on, err)
	}
}
