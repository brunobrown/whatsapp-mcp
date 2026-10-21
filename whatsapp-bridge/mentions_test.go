package main

import (
	"reflect"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/types"
)

var (
	mPhone = types.JID{User: "555181993271", Server: "s.whatsapp.net"}
	mLID   = types.JID{User: "160889625940144", Server: "lid"}
)

func TestBuildTextMessagePlainStaysConversation(t *testing.T) { // MENT-04
	msg := buildTextMessage("*oi* _tudo_ bem", nil, nil)

	if msg.GetConversation() != "*oi* _tudo_ bem" || msg.GetExtendedTextMessage() != nil {
		t.Errorf("plain text must stay a Conversation, got %+v", msg)
	}
}

func TestBuildTextMessageWithMentionsUsesContextInfo(t *testing.T) { // MENT-04, MENT-07
	text := "*aviso* @555181993271\n> citação"
	msg := buildTextMessage(text, []types.JID{mPhone, mLID}, nil)

	ext := msg.GetExtendedTextMessage()
	if ext == nil || ext.GetText() != text {
		t.Fatalf("want ExtendedTextMessage with the text untouched, got %+v", msg)
	}
	want := []string{"555181993271@s.whatsapp.net", "160889625940144@lid"}
	if got := ext.GetContextInfo().GetMentionedJID(); !reflect.DeepEqual(got, want) {
		t.Errorf("MentionedJID = %v, want %v", got, want)
	}
	if ext.GetContextInfo().GetStanzaID() != "" {
		t.Error("no quote requested: StanzaID must be empty")
	}
}

func TestBuildTextMessageQuote(t *testing.T) { // REPLY-01
	quote := &QuoteInfo{ID: "ORIG1", Participant: mLID, Text: "texto original"}

	msg := buildTextMessage("de acordo", nil, quote)

	ctx := msg.GetExtendedTextMessage().GetContextInfo()
	if ctx.GetStanzaID() != "ORIG1" || ctx.GetParticipant() != "160889625940144@lid" {
		t.Errorf("StanzaID/Participant = %q/%q", ctx.GetStanzaID(), ctx.GetParticipant())
	}
	if ctx.GetQuotedMessage().GetConversation() != "texto original" {
		t.Errorf("QuotedMessage = %q, want the stored text", ctx.GetQuotedMessage().GetConversation())
	}
	if len(ctx.GetMentionedJID()) != 0 {
		t.Errorf("no mentions requested, got %v", ctx.GetMentionedJID())
	}
}

func TestSentReplyStoresQuotedID(t *testing.T) { // REPLY-03
	store := newTestStore(t)
	msg := buildTextMessage("de acordo", nil, &QuoteInfo{ID: "ORIG1", Participant: mLID, Text: "x"})

	if err := recordSentMessage(store, "chat@g.us", "me", "SENT1", time.Now(), msg); err != nil {
		t.Fatal(err)
	}

	if _, quoted, _ := readMeta(t, store, "SENT1"); quoted.String != "ORIG1" {
		t.Errorf("quoted_id = %q, want ORIG1", quoted.String)
	}
	var content string
	store.db.QueryRow("SELECT content FROM messages WHERE id='SENT1'").Scan(&content)
	if content != "de acordo" {
		t.Errorf("stored content = %q", content)
	}
}

func participant(jid, phone, lid types.JID) types.GroupParticipant {
	return types.GroupParticipant{JID: jid, PhoneNumber: phone, LID: lid}
}

func TestResolveGroupMentionsSwapsToTheGroupAddressing(t *testing.T) { // MENT-05
	// the group addresses Teilor by LID; the text and mention came as the phone number
	parts := []types.GroupParticipant{participant(mLID, mPhone, mLID)}

	text, jids := resolveGroupMentions("oi @555181993271, tudo bem? @555181993271", []types.JID{mPhone}, parts)

	if text != "oi @160889625940144, tudo bem? @160889625940144" {
		t.Errorf("text = %q", text)
	}
	if !reflect.DeepEqual(jids, []types.JID{mLID}) {
		t.Errorf("jids = %v, want [%v]", jids, mLID)
	}
}

func TestResolveGroupMentionsFromLidToPhoneGroup(t *testing.T) { // MENT-05
	parts := []types.GroupParticipant{participant(mPhone, mPhone, mLID)}

	text, jids := resolveGroupMentions("@160889625940144 oi", []types.JID{mLID}, parts)

	if text != "@555181993271 oi" || !reflect.DeepEqual(jids, []types.JID{mPhone}) {
		t.Errorf("got %q %v", text, jids)
	}
}

func TestResolveGroupMentionsKeepsCorrectAddressingAndStrangers(t *testing.T) { // MENT-05, MENT-06
	stranger := types.JID{User: "5511000000000", Server: "s.whatsapp.net"}
	parts := []types.GroupParticipant{participant(mPhone, mPhone, mLID)}

	text, jids := resolveGroupMentions("@555181993271 e @5511000000000", []types.JID{mPhone, stranger}, parts)

	if text != "@555181993271 e @5511000000000" {
		t.Errorf("text changed: %q", text)
	}
	if !reflect.DeepEqual(jids, []types.JID{mPhone, stranger}) {
		t.Errorf("jids = %v", jids)
	}
}

func TestResolveGroupMentionsOnlyRewritesWholeTokens(t *testing.T) { // MENT-05
	parts := []types.GroupParticipant{participant(mLID, mPhone, mLID)}

	text, _ := resolveGroupMentions("@5551819932719 não é @555181993271", []types.JID{mPhone}, parts)

	if text != "@5551819932719 não é @160889625940144" {
		t.Errorf("text = %q (a longer number must not be rewritten)", text)
	}
}

func TestResolveGroupMentionsDeduplicates(t *testing.T) { // MENT-05
	parts := []types.GroupParticipant{participant(mLID, mPhone, mLID)}

	_, jids := resolveGroupMentions("x", []types.JID{mPhone, mLID}, parts)

	if !reflect.DeepEqual(jids, []types.JID{mLID}) {
		t.Errorf("same person twice must be one mention: %v", jids)
	}
}

func TestParseMentions(t *testing.T) { // MENT-04
	got, err := parseMentions([]string{"555181993271@s.whatsapp.net", "160889625940144@lid"})
	if err != nil || !reflect.DeepEqual(got, []types.JID{mPhone, mLID}) {
		t.Fatalf("parseMentions = %v, %v", got, err)
	}
	if _, err := parseMentions([]string{"não é jid"}); err == nil {
		t.Error("a malformed mention must be an error, not silently dropped")
	}
	if got, err := parseMentions(nil); err != nil || len(got) != 0 {
		t.Errorf("no mentions = %v, %v", got, err)
	}
}

func seedQuotable(t *testing.T, store *MessageStore) {
	t.Helper()
	ts := time.Now()
	store.StoreChat("g@g.us", "G", ts)
	store.StoreChat("5551@s.whatsapp.net", "DM", ts)
	store.StoreMessage("THEIRS", "g@g.us", "160889625940144", "pergunta", ts, false, "", "", "", nil, nil, nil, 0)
	store.StoreMessage("MINE", "g@g.us", "555100000000", "resposta minha", ts, true, "", "", "", nil, nil, nil, 0)
	store.StoreMessage("DMMSG", "5551@s.whatsapp.net", "5551", "oi", ts, false, "", "", "", nil, nil, nil, 0)
}

func TestGetQuotedMessage(t *testing.T) { // REPLY-01, REPLY-02
	store := newTestStore(t)
	seedQuotable(t, store)

	ref, ok, err := store.GetQuotedMessage("THEIRS", "g@g.us")
	if err != nil || !ok || ref.Sender != "160889625940144" || ref.Content != "pergunta" || ref.IsFromMe {
		t.Fatalf("THEIRS = %+v %v %v", ref, ok, err)
	}
	if ref, ok, _ := store.GetQuotedMessage("MINE", "g@g.us"); !ok || !ref.IsFromMe {
		t.Errorf("MINE = %+v %v", ref, ok)
	}
	if _, ok, _ := store.GetQuotedMessage("NOPE", "g@g.us"); ok {
		t.Error("unknown id must not be found")
	}
	if _, ok, _ := store.GetQuotedMessage("DMMSG", "g@g.us"); ok {
		t.Error("a message of another group must not be quotable here")
	}
}

func TestGetQuotedMessageDirectChatFallsBackToTheOtherAddressing(t *testing.T) { // REPLY-02
	store := newTestStore(t)
	seedQuotable(t, store)

	// the DM is stored under the phone JID, the send targets the LID variant of the same chat
	if _, ok, _ := store.GetQuotedMessage("DMMSG", "999@lid"); !ok {
		t.Error("a direct chat may be stored under the other addressing")
	}
}

func TestQuoteParticipant(t *testing.T) { // REPLY-01, REPLY-04
	own := types.JID{User: "555100000000", Server: "s.whatsapp.net", Device: 5}
	resolve := func(user string) types.JID { return types.JID{User: user, Server: "lid"} }

	if got := quoteParticipant(QuotedRef{Sender: "160889625940144"}, own, resolve); got != mLID {
		t.Errorf("other author = %v, want %v", got, mLID)
	}
	got := quoteParticipant(QuotedRef{Sender: "555100000000", IsFromMe: true}, own, resolve)
	if got.String() != "555100000000@s.whatsapp.net" {
		t.Errorf("own message participant = %v, want our own JID without device", got)
	}
}
