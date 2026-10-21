package main

import (
	"testing"
	"time"

	waProto "go.mau.fi/whatsmeow/binary/proto"
	"google.golang.org/protobuf/proto"
)

func TestExtractTextContentCaptions(t *testing.T) {
	cases := []struct {
		name string
		msg  *waProto.Message
		want string
	}{
		{"image caption (CAP-01)", &waProto.Message{ImageMessage: &waProto.ImageMessage{Caption: proto.String("FICOU BOM")}}, "FICOU BOM"},
		{"video caption (CAP-01)", &waProto.Message{VideoMessage: &waProto.VideoMessage{Caption: proto.String("demo")}}, "demo"},
		{"document caption (CAP-01)", &waProto.Message{DocumentMessage: &waProto.DocumentMessage{Caption: proto.String("proposta")}}, "proposta"},
		{"document with caption wrapper (CAP-02)", &waProto.Message{
			DocumentWithCaptionMessage: &waProto.FutureProofMessage{Message: &waProto.Message{
				DocumentMessage: &waProto.DocumentMessage{Caption: proto.String("revisar")},
			}},
		}, "revisar"},
		{"conversation unchanged (CAP-03)", &waProto.Message{Conversation: proto.String("oi")}, "oi"},
		{"extended text unchanged (CAP-03)", &waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{Text: proto.String("link")}}, "link"},
		{"image without caption (CAP-04)", &waProto.Message{ImageMessage: &waProto.ImageMessage{}}, ""},
		{"nil message", nil, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := extractTextContent(c.msg); got != c.want {
				t.Errorf("extractTextContent = %q, want %q", got, c.want)
			}
		})
	}
}

func editMessage(targetID, text string) *waProto.Message {
	typ := waProto.ProtocolMessage_MESSAGE_EDIT
	return &waProto.Message{ProtocolMessage: &waProto.ProtocolMessage{
		Type:          &typ,
		Key:           &waProto.MessageKey{ID: proto.String(targetID)},
		EditedMessage: &waProto.Message{Conversation: proto.String(text)},
	}}
}

func TestEditTarget(t *testing.T) {
	id, ok := editTarget(editMessage("ORIG", "novo"))
	if !ok || id != "ORIG" {
		t.Errorf("editTarget = (%q, %v), want (ORIG, true)", id, ok)
	}
	if _, ok := editTarget(&waProto.Message{Conversation: proto.String("oi")}); ok {
		t.Error("plain text must not be an edit")
	}
	if got := extractTextContent(editMessage("ORIG", "novo")); got != "novo" {
		t.Errorf("edit content = %q, want novo", got)
	}
}

func newTestStore(t *testing.T) *MessageStore {
	t.Helper()
	t.Chdir(t.TempDir())
	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

func countMessages(t *testing.T, store *MessageStore) int {
	t.Helper()
	var n int
	if err := store.db.QueryRow("SELECT COUNT(*) FROM messages").Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestUpdateMessageContent(t *testing.T) {
	store := newTestStore(t)
	now := time.Now()
	if err := store.StoreChat("chat@g.us", "Chat", now); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreMessage("ORIG", "chat@g.us", "me", "texto velho", now, true, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}

	// CAP-05: edição troca o content e não cria linha
	updated, err := store.UpdateMessageContent("ORIG", "chat@g.us", "me", "texto novo")
	if err != nil || !updated {
		t.Fatalf("UpdateMessageContent = (%v, %v), want (true, nil)", updated, err)
	}
	var content string
	if err := store.db.QueryRow("SELECT content FROM messages WHERE id='ORIG'").Scan(&content); err != nil {
		t.Fatal(err)
	}
	if content != "texto novo" || countMessages(t, store) != 1 {
		t.Errorf("content=%q rows=%d, want 'texto novo' and 1 row", content, countMessages(t, store))
	}

	// CAP-06: edição de mensagem ausente não grava nada
	updated, err = store.UpdateMessageContent("GHOST", "chat@g.us", "me", "x")
	if err != nil || updated {
		t.Fatalf("UpdateMessageContent(ghost) = (%v, %v), want (false, nil)", updated, err)
	}
	if countMessages(t, store) != 1 {
		t.Errorf("rows = %d, want 1", countMessages(t, store))
	}
}

func TestUpdateMessageContentRejectsOtherSender(t *testing.T) {
	store := newTestStore(t)
	now := time.Now()
	if err := store.StoreChat("chat@g.us", "Chat", now); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreMessage("ORIG", "chat@g.us", "alice", "original", now, false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}

	updated, err := store.UpdateMessageContent("ORIG", "chat@g.us", "mallory", "adulterado")
	if err != nil || updated {
		t.Fatalf("edit by another sender = (%v, %v), want (false, nil)", updated, err)
	}
	var content string
	if err := store.db.QueryRow("SELECT content FROM messages WHERE id='ORIG'").Scan(&content); err != nil {
		t.Fatal(err)
	}
	if content != "original" {
		t.Errorf("content = %q, want original", content)
	}
}
