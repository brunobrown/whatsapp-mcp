package main

import (
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

func TestNonTextMessagesBecomeShortStableText(t *testing.T) { // NT-01..NT-04, NT-06
	for name, c := range map[string]struct {
		msg  *waE2E.Message
		want string
	}{
		"location with name": {&waE2E.Message{LocationMessage: &waE2E.LocationMessage{
			Name: proto.String("Casa"), DegreesLatitude: proto.Float64(-8.05), DegreesLongitude: proto.Float64(-34.88)}},
			"[location: Casa (-8.050000, -34.880000)]"},
		"location without name": {&waE2E.Message{LocationMessage: &waE2E.LocationMessage{
			DegreesLatitude: proto.Float64(-8.05), DegreesLongitude: proto.Float64(-34.88)}},
			"[location: -8.050000, -34.880000]"},
		"live location with caption": {&waE2E.Message{LiveLocationMessage: &waE2E.LiveLocationMessage{
			Caption: proto.String("a caminho"), DegreesLatitude: proto.Float64(1.5), DegreesLongitude: proto.Float64(2.25)}},
			"[live location: a caminho (1.500000, 2.250000)]"},
		"live location without caption": {&waE2E.Message{LiveLocationMessage: &waE2E.LiveLocationMessage{
			DegreesLatitude: proto.Float64(1.5), DegreesLongitude: proto.Float64(2.25)}},
			"[live location: 1.500000, 2.250000]"},
		"contact": {&waE2E.Message{ContactMessage: &waE2E.ContactMessage{DisplayName: proto.String("Maria")}},
			"[contact: Maria]"},
		"contacts keep only the names that exist": {&waE2E.Message{ContactsArrayMessage: &waE2E.ContactsArrayMessage{
			Contacts: []*waE2E.ContactMessage{{DisplayName: proto.String("Ana")}, {}, {DisplayName: proto.String("Beto")}}}},
			"[contacts: Ana, Beto]"},
		"sticker":                   {&waE2E.Message{StickerMessage: &waE2E.StickerMessage{}}, "[sticker]"},
		"plain text is unchanged":   {&waE2E.Message{Conversation: proto.String("oi")}, "oi"},
		"caption is unchanged":      {&waE2E.Message{ImageMessage: &waE2E.ImageMessage{Caption: proto.String("legenda")}}, "legenda"},
		"extended text unchanged":   {&waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String("citado")}}, "citado"},
		"empty message stays empty": {&waE2E.Message{}, ""},
	} {
		if got := extractTextContent(c.msg); got != c.want {
			t.Errorf("%s: extractTextContent = %q, want %q", name, got, c.want)
		}
	}
}

func TestNonTextMessageIsStoredByHandleMessage(t *testing.T) { // NT-01, NT-04
	store := newTestStore(t)
	now := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	store.StoreChat("chat@g.us", "Chat", now)
	loc := &waE2E.Message{LocationMessage: &waE2E.LocationMessage{
		Name: proto.String("Casa"), DegreesLatitude: proto.Float64(-8.05), DegreesLongitude: proto.Float64(-34.88)}}

	handleMessage(nil, store, pollEvent("chat@g.us", "5511999990000@s.whatsapp.net", "LOC", loc, now), waLog.Noop)
	handleMessage(nil, store, pollEvent("chat@g.us", "5511999990000@s.whatsapp.net", "STK", &waE2E.Message{StickerMessage: &waE2E.StickerMessage{}}, now), waLog.Noop)

	if row := readRow(t, store, "LOC"); row.content != "[location: Casa (-8.050000, -34.880000)]" {
		t.Errorf("location content = %q", row.content)
	}
	if row := readRow(t, store, "STK"); row.content != "[sticker]" {
		t.Errorf("sticker content = %q", row.content)
	}
}
