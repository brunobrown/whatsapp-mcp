package main

import (
	"database/sql"
	"os"
	"testing"
	"time"

	waProto "go.mau.fi/whatsmeow/binary/proto"
	"google.golang.org/protobuf/proto"
)

func TestExtractMessageMetaMimetype(t *testing.T) { // MEDIA-01
	cases := []struct {
		name string
		msg  *waProto.Message
		want string
	}{
		{"image", &waProto.Message{ImageMessage: &waProto.ImageMessage{Mimetype: proto.String("image/jpeg")}}, "image/jpeg"},
		{"video", &waProto.Message{VideoMessage: &waProto.VideoMessage{Mimetype: proto.String("video/mp4")}}, "video/mp4"},
		{"audio", &waProto.Message{AudioMessage: &waProto.AudioMessage{Mimetype: proto.String("audio/ogg; codecs=opus")}}, "audio/ogg; codecs=opus"},
		{"document", &waProto.Message{DocumentMessage: &waProto.DocumentMessage{Mimetype: proto.String("application/pdf")}}, "application/pdf"},
		{"document in caption wrapper", &waProto.Message{DocumentWithCaptionMessage: &waProto.FutureProofMessage{Message: &waProto.Message{
			DocumentMessage: &waProto.DocumentMessage{Mimetype: proto.String("application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
		}}}, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
		{"text has none", &waProto.Message{Conversation: proto.String("oi")}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := extractMessageMeta(c.msg).Mimetype; got != c.want {
				t.Errorf("Mimetype = %q, want %q", got, c.want)
			}
		})
	}
	if got := extractMessageMeta(nil); got != (MessageMeta{}) {
		t.Errorf("nil message meta = %+v, want zero", got)
	}
}

func TestExtractMessageMetaForwardedAndQuoted(t *testing.T) { // MEDIA-02, MEDIA-03, MEDIA-11
	ctx := &waProto.ContextInfo{IsForwarded: proto.Bool(true), StanzaID: proto.String("QUOTED1")}
	cases := []struct {
		name string
		msg  *waProto.Message
	}{
		{"extended text", &waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{Text: proto.String("x"), ContextInfo: ctx}}},
		{"image", &waProto.Message{ImageMessage: &waProto.ImageMessage{ContextInfo: ctx}}},
		{"video", &waProto.Message{VideoMessage: &waProto.VideoMessage{ContextInfo: ctx}}},
		{"audio", &waProto.Message{AudioMessage: &waProto.AudioMessage{ContextInfo: ctx}}},
		{"document", &waProto.Message{DocumentMessage: &waProto.DocumentMessage{ContextInfo: ctx}}},
		{"document in caption wrapper", &waProto.Message{DocumentWithCaptionMessage: &waProto.FutureProofMessage{Message: &waProto.Message{
			DocumentMessage: &waProto.DocumentMessage{ContextInfo: ctx},
		}}}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := extractMessageMeta(c.msg)
			if !got.Forwarded || got.QuotedID != "QUOTED1" {
				t.Errorf("meta = %+v, want Forwarded and QuotedID=QUOTED1", got)
			}
		})
	}

	plain := extractMessageMeta(&waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{
		Text: proto.String("x"), ContextInfo: &waProto.ContextInfo{IsForwarded: proto.Bool(false)},
	}})
	if plain.Forwarded || plain.QuotedID != "" {
		t.Errorf("not forwarded, no quote: meta = %+v, want zero", plain)
	}
	if got := extractMessageMeta(&waProto.Message{Conversation: proto.String("oi")}); got != (MessageMeta{}) {
		t.Errorf("conversation meta = %+v, want zero", got)
	}
}

func readMeta(t *testing.T, store *MessageStore, id string) (mimetype, quoted sql.NullString, forwarded sql.NullBool) {
	t.Helper()
	err := store.db.QueryRow("SELECT mimetype, quoted_id, forwarded FROM messages WHERE id = ?", id).Scan(&mimetype, &quoted, &forwarded)
	if err != nil {
		t.Fatal(err)
	}
	return
}

func TestSetMessageMetaPersistsAndSurvivesRestore(t *testing.T) { // MEDIA-01..03, MEDIA-05
	store := newTestStore(t)
	ts := time.Now()
	if err := store.StoreChat("chat@g.us", "Chat", ts); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreMessage("DOC", "chat@g.us", "alice", "", ts, false, "document", "proposta.pdf", "", nil, nil, nil, 2048); err != nil {
		t.Fatal(err)
	}
	if err := store.SetMessageMeta("DOC", "chat@g.us", MessageMeta{Mimetype: "application/pdf", Forwarded: true, QuotedID: "Q1"}); err != nil {
		t.Fatal(err)
	}
	mime, quoted, fwd := readMeta(t, store, "DOC")
	if mime.String != "application/pdf" || quoted.String != "Q1" || !fwd.Bool {
		t.Fatalf("stored meta = %v %v %v", mime, quoted, fwd)
	}

	// The same message arrives again without metadata (echo, history sync).
	if err := store.StoreMessage("DOC", "chat@g.us", "alice", "", ts, false, "document", "proposta.pdf", "", nil, nil, nil, 2048); err != nil {
		t.Fatal(err)
	}
	mime, quoted, fwd = readMeta(t, store, "DOC")
	if mime.String != "application/pdf" || quoted.String != "Q1" || !fwd.Bool {
		t.Errorf("re-storing the message wiped its metadata: %v %v %v", mime, quoted, fwd)
	}
}

func TestSetMessageMetaEmptyKeepsStoredValues(t *testing.T) { // MEDIA-05
	store := newTestStore(t)
	ts := time.Now()
	store.StoreChat("chat@g.us", "Chat", ts)
	store.StoreMessage("M", "chat@g.us", "alice", "oi", ts, false, "", "", "", nil, nil, nil, 0)
	if err := store.SetMessageMeta("M", "chat@g.us", MessageMeta{QuotedID: "Q1"}); err != nil {
		t.Fatal(err)
	}
	if err := store.SetMessageMeta("M", "chat@g.us", MessageMeta{}); err != nil {
		t.Fatal(err)
	}
	if _, quoted, _ := readMeta(t, store, "M"); quoted.String != "Q1" {
		t.Errorf("an empty meta replaced the stored quoted_id: %q", quoted.String)
	}
}

func TestMetaColumnsMigrateLegacyDatabaseIdempotently(t *testing.T) { // MEDIA-04
	t.Chdir(t.TempDir())
	if err := os.MkdirAll("store", 0o755); err != nil {
		t.Fatal(err)
	}
	legacy, err := sql.Open("sqlite3", "file:store/messages.db")
	if err != nil {
		t.Fatal(err)
	}
	_, err = legacy.Exec(`
		CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
		CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN,
			media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB, file_enc_sha256 BLOB,
			file_length INTEGER, PRIMARY KEY (id, chat_jid));
		INSERT INTO chats VALUES ('chat@g.us', 'Chat', '2026-10-20 10:00:00');
		INSERT INTO messages (id, chat_jid, sender, content) VALUES ('OLD', 'chat@g.us', 'alice', 'antiga');`)
	if err != nil {
		t.Fatal(err)
	}
	legacy.Close()

	for i := 0; i < 2; i++ { // second open must be a no-op
		store, err := NewMessageStore()
		if err != nil {
			t.Fatalf("open %d: %v", i+1, err)
		}
		var content string
		if err := store.db.QueryRow("SELECT content FROM messages WHERE id='OLD'").Scan(&content); err != nil || content != "antiga" {
			t.Fatalf("open %d: existing message lost: %q %v", i+1, content, err)
		}
		if mime, quoted, fwd := readMeta(t, store, "OLD"); mime.Valid || quoted.Valid || fwd.Bool {
			t.Errorf("open %d: legacy row should have empty metadata, got %v %v %v", i+1, mime, quoted, fwd)
		}
		store.Close()
	}
}

func TestRecordSentMessageStoresMetadata(t *testing.T) { // MEDIA-01
	store := newTestStore(t)
	msg := &waProto.Message{DocumentMessage: &waProto.DocumentMessage{
		Mimetype: proto.String("application/pdf"), FileName: proto.String("proposta.pdf"),
	}}
	if err := recordSentMessage(store, "chat@g.us", "me", "SENT1", time.Now(), msg); err != nil {
		t.Fatal(err)
	}
	if mime, _, _ := readMeta(t, store, "SENT1"); mime.String != "application/pdf" {
		t.Errorf("sent document mimetype = %q, want application/pdf", mime.String)
	}
}

func TestSetMessageMetaEmptyKeepsEveryStoredField(t *testing.T) { // MEDIA-05
	store := newTestStore(t)
	ts := time.Now()
	store.StoreChat("chat@g.us", "Chat", ts)
	store.StoreMessage("M", "chat@g.us", "alice", "", ts, false, "document", "a.pdf", "", nil, nil, nil, 1)
	if err := store.SetMessageMeta("M", "chat@g.us", MessageMeta{Mimetype: "application/pdf", Forwarded: true, QuotedID: "Q1"}); err != nil {
		t.Fatal(err)
	}
	if err := store.SetMessageMeta("M", "chat@g.us", MessageMeta{}); err != nil {
		t.Fatal(err)
	}
	mime, quoted, fwd := readMeta(t, store, "M")
	if mime.String != "application/pdf" || quoted.String != "Q1" || !fwd.Bool {
		t.Errorf("an empty meta replaced stored values: %v %v %v", mime, quoted, fwd)
	}
}
