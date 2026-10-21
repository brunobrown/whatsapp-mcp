package main

import (
	"encoding/json"
	"errors"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"google.golang.org/protobuf/proto"
)

type storedRow struct {
	sender, content, mediaType, filename string
	isFromMe                             bool
	ts                                   time.Time
}

func readRow(t *testing.T, store *MessageStore, id string) storedRow {
	t.Helper()
	var r storedRow
	var mediaType, filename *string
	err := store.db.QueryRow(
		"SELECT sender, content, timestamp, is_from_me, media_type, filename FROM messages WHERE id = ? AND chat_jid = 'chat@g.us'", id,
	).Scan(&r.sender, &r.content, &r.ts, &r.isFromMe, &mediaType, &filename)
	if err != nil {
		t.Fatalf("row %s: %v", id, err)
	}
	if mediaType != nil {
		r.mediaType = *mediaType
	}
	if filename != nil {
		r.filename = *filename
	}
	return r
}

func chatRow(t *testing.T, store *MessageStore) (name string, last time.Time) {
	t.Helper()
	if err := store.db.QueryRow("SELECT name, last_message_time FROM chats WHERE jid = 'chat@g.us'").Scan(&name, &last); err != nil {
		t.Fatal(err)
	}
	return
}

func sendResp(id string, ts time.Time) whatsmeow.SendResponse {
	return whatsmeow.SendResponse{ID: id, Timestamp: ts}
}

func TestCompleteSendStoresText(t *testing.T) { // SENT-01
	store := newTestStore(t)
	ts := time.Date(2026, 10, 21, 10, 30, 0, 0, time.UTC)
	msg := &waProto.Message{Conversation: proto.String("olá time")}

	res := completeSend(store, "chat@g.us", "5511999", msg, sendResp("SENT1", ts), nil, nil)

	if !res.Success || res.MessageID != "SENT1" || res.ChatJID != "chat@g.us" || !res.Timestamp.Equal(ts) {
		t.Fatalf("result = %+v", res)
	}
	row := readRow(t, store, "SENT1")
	if !row.isFromMe || row.content != "olá time" || row.sender != "5511999" || !row.ts.Equal(ts) {
		t.Errorf("row = %+v", row)
	}
}

func TestCompleteSendStoresMedia(t *testing.T) { // SENT-02
	store := newTestStore(t)
	msg := &waProto.Message{ImageMessage: &waProto.ImageMessage{Caption: proto.String("foto da tela")}}

	completeSend(store, "chat@g.us", "5511999", msg, sendResp("IMG1", time.Now()), nil, nil)

	row := readRow(t, store, "IMG1")
	if row.mediaType != "image" || row.filename != "image_IMG1.jpg" || row.content != "foto da tela" {
		t.Errorf("row = %+v", row)
	}
}

func TestCompleteSendKeepsChatNameAndAdvancesTime(t *testing.T) { // SENT-03
	store := newTestStore(t)
	old := time.Date(2026, 8, 31, 21, 43, 0, 0, time.UTC)
	if err := store.StoreChat("chat@g.us", "Projeto Elbratec", old); err != nil {
		t.Fatal(err)
	}
	ts := time.Date(2026, 10, 21, 10, 30, 0, 0, time.UTC)

	completeSend(store, "chat@g.us", "me", &waProto.Message{Conversation: proto.String("x")}, sendResp("S", ts), nil, nil)

	name, last := chatRow(t, store)
	if name != "Projeto Elbratec" || !last.Equal(ts) {
		t.Errorf("chat = (%q, %v), want (Projeto Elbratec, %v)", name, last, ts)
	}
}

func TestCompleteSendCreatesUnknownChatWithJIDAsName(t *testing.T) { // SENT-03 (chat novo)
	store := newTestStore(t)
	completeSend(store, "chat@g.us", "me", &waProto.Message{Conversation: proto.String("x")}, sendResp("S", time.Now()), nil, nil)
	if name, _ := chatRow(t, store); name != "chat@g.us" {
		t.Errorf("name = %q, want chat@g.us", name)
	}
}

func TestEchoAfterSendKeepsOneRow(t *testing.T) { // SENT-04
	store := newTestStore(t)
	ts := time.Now()
	completeSend(store, "chat@g.us", "me", &waProto.Message{Conversation: proto.String("oi")}, sendResp("DUP", ts), nil, nil)

	// o eco chega pelo caminho de handleMessage
	if err := store.StoreMessage("DUP", "chat@g.us", "me", "oi", ts, true, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}
	if n := countMessages(t, store); n != 1 {
		t.Errorf("rows = %d, want 1", n)
	}
}

func TestCompleteSendFailureStoresNothing(t *testing.T) { // SENT-05
	store := newTestStore(t)
	res := completeSend(store, "chat@g.us", "me", &waProto.Message{Conversation: proto.String("x")}, sendResp("", time.Time{}), errors.New("boom"), nil)

	if res.Success || res.MessageID != "" {
		t.Errorf("result = %+v, want failure without ID", res)
	}
	if n := countMessages(t, store); n != 0 {
		t.Errorf("rows = %d, want 0", n)
	}
}

func TestCompleteSendStoreFailureStillSucceeds(t *testing.T) { // SENT-06
	store := newTestStore(t)
	store.Close() // força o erro de gravação

	res := completeSend(store, "chat@g.us", "me", &waProto.Message{Conversation: proto.String("x")}, sendResp("S", time.Now()), nil, nil)

	if !res.Success || res.MessageID != "S" {
		t.Errorf("result = %+v, want success", res)
	}
}

func TestSendResponseJSON(t *testing.T) { // SENT-07
	ts := time.Date(2026, 10, 21, 10, 30, 0, 0, time.FixedZone("BRT", -3*3600))
	out, _ := json.Marshal(newSendResponse(SendResult{Success: true, Message: "Message sent to x", MessageID: "ID9", Timestamp: ts, ChatJID: "chat@g.us"}))

	var got map[string]any
	json.Unmarshal(out, &got)
	if got["message_id"] != "ID9" || got["chat_jid"] != "chat@g.us" || got["timestamp"] != "2026-10-21T10:30:00-03:00" {
		t.Errorf("json = %s", out)
	}

	out, _ = json.Marshal(newSendResponse(SendResult{Success: false, Message: "Error"}))
	got = map[string]any{}
	json.Unmarshal(out, &got)
	if _, has := got["message_id"]; has {
		t.Errorf("failure json has message_id: %s", out)
	}
}
