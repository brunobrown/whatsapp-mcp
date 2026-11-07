package main

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waSyncAction"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

type fakeSource struct {
	contacts map[types.JID]types.ContactInfo
	pnToLID  map[string]string
	lidToPN  map[string]string
	failPN   map[string]bool // LIDForPN fails for these phones
}

func (f *fakeSource) AllContacts(context.Context) (map[types.JID]types.ContactInfo, error) {
	return f.contacts, nil
}

func (f *fakeSource) LIDForPN(_ context.Context, pn types.JID) (types.JID, error) {
	if f.failPN[pn.User] {
		return types.JID{}, errors.New("boom")
	}
	if lid, ok := f.pnToLID[pn.User]; ok {
		return types.JID{User: lid, Server: types.HiddenUserServer}, nil
	}
	return types.JID{}, nil
}

func (f *fakeSource) PNForLID(_ context.Context, lid types.JID) (types.JID, error) {
	if pn, ok := f.lidToPN[lid.User]; ok {
		return types.JID{User: pn, Server: types.DefaultUserServer}, nil
	}
	return types.JID{}, nil
}

func pnJID(u string) types.JID  { return types.JID{User: u, Server: types.DefaultUserServer} }
func lidJID(u string) types.JID { return types.JID{User: u, Server: types.HiddenUserServer} }

func getID(t *testing.T, store *MessageStore, id string) Identity {
	t.Helper()
	i, ok, err := store.GetIdentity(id)
	if err != nil || !ok {
		t.Fatalf("identity %s not found (%v)", id, err)
	}
	return i
}

func TestSyncRecordsAddressBook(t *testing.T) { // SYNC-01
	store := newTestStore(t)
	src := &fakeSource{
		contacts: map[types.JID]types.ContactInfo{
			pnJID("551155550404"): {FullName: "Chris Taylor - Acme", FirstName: "Chris", PushName: "Chris"},
			pnJID("551155550505"): {FirstName: "Sam", PushName: "Samantha"}, // no full name: uses the first name
			lidJID("999"):         {PushName: "Only Lid"},
		},
		pnToLID: map[string]string{"551155550404": "123"},
	}

	if _, err := syncIdentities(context.Background(), store, src, nil); err != nil {
		t.Fatal(err)
	}

	got := getID(t, store, "551155550404")
	if got != (Identity{LID: "123", Phone: "551155550404", FullName: "Chris Taylor - Acme", PushName: "Chris"}) {
		t.Errorf("identity = %+v", got)
	}
	if got := getID(t, store, "123"); got.Phone != "551155550404" {
		t.Errorf("lookup by LID = %+v", got)
	}
	if got := getID(t, store, "551155550505"); got.FullName != "Sam" || got.PushName != "Samantha" {
		t.Errorf("first-name fallback = %+v", got)
	}
	if got := getID(t, store, "999"); got.LID != "999" || got.PushName != "Only Lid" {
		t.Errorf("lid-only contact = %+v", got)
	}
}

func TestSyncLinksLIDSendersFromMessages(t *testing.T) { // SYNC-02
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "f.jpg") // creates the chat
	for _, sender := range []string{"100000000000005", "777", "unknown-lid-1"} {
		if err := store.StoreMessage("m-"+sender, "chat@g.us", sender, "hi", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatal(err)
		}
	}
	src := &fakeSource{lidToPN: map[string]string{"100000000000005": "551188880101"}}

	if _, err := syncIdentities(context.Background(), store, src, nil); err != nil {
		t.Fatal(err)
	}

	got := getID(t, store, "100000000000005")
	if got.Phone != "551188880101" {
		t.Errorf("identity = %+v, want phone linked", got)
	}
	if _, ok, _ := store.GetIdentity("unknown-lid-1"); ok {
		t.Error("a sender WhatsApp knows nothing about must not get an empty row")
	}
}

func TestSyncContinuesAfterFailedLookup(t *testing.T) { // SYNC-05
	store := newTestStore(t)
	src := &fakeSource{
		contacts: map[types.JID]types.ContactInfo{
			pnJID("111"): {FullName: "Broken"},
			pnJID("222"): {FullName: "Whole"},
		},
		pnToLID: map[string]string{"222": "lid222"},
		failPN:  map[string]bool{"111": true},
	}

	if _, err := syncIdentities(context.Background(), store, src, nil); err != nil {
		t.Fatal(err)
	}

	if got := getID(t, store, "111"); got.FullName != "Broken" {
		t.Errorf("contact whose LID lookup failed must still be recorded: %+v", got)
	}
	if got := getID(t, store, "222"); got.LID != "lid222" {
		t.Errorf("other contacts must not be affected: %+v", got)
	}
}

func TestMessageIdentityLinksSenderAndAlt(t *testing.T) { // SYNC-03
	store := newTestStore(t)
	info := types.MessageInfo{
		MessageSource: types.MessageSource{Sender: lidJID("100000000000001"), SenderAlt: pnJID("551188880101")},
		PushName:      "Alex",
	}
	recordMessageIdentity(store, info)
	got := getID(t, store, "100000000000001")
	if got != (Identity{LID: "100000000000001", Phone: "551188880101", PushName: "Alex"}) {
		t.Errorf("identity = %+v", got)
	}

	// no SenderAlt: only the id that exists, and the reverse order (phone as Sender) also links
	recordMessageIdentity(store, types.MessageInfo{
		MessageSource: types.MessageSource{Sender: pnJID("551166660303")}, PushName: "Maria",
	})
	if got := getID(t, store, "551166660303"); got.PushName != "Maria" || got.LID != "" {
		t.Errorf("identity = %+v", got)
	}
	recordMessageIdentity(store, types.MessageInfo{
		MessageSource: types.MessageSource{Sender: pnJID("551166660303"), SenderAlt: lidJID("555")}, PushName: "Maria",
	})
	if got := getID(t, store, "555"); got.Phone != "551166660303" {
		t.Errorf("identity = %+v", got)
	}
}

func TestMessageIdentityWithoutPushNameStillLinks(t *testing.T) { // SYNC-03
	store := newTestStore(t)
	recordMessageIdentity(store, types.MessageInfo{
		MessageSource: types.MessageSource{Sender: lidJID("1"), SenderAlt: pnJID("2")},
	})
	if got := getID(t, store, "1"); got.Phone != "2" {
		t.Errorf("identity = %+v", got)
	}
}

func TestPushNameAndContactEvents(t *testing.T) { // SYNC-04
	store := newTestStore(t)
	recordPushNameEvent(store, &events.PushName{JID: lidJID("111"), JIDAlt: pnJID("5581"), NewPushName: "New Push"})
	if got := getID(t, store, "111"); got != (Identity{LID: "111", Phone: "5581", PushName: "New Push"}) {
		t.Errorf("push name event: %+v", got)
	}

	recordContactEvent(store, &events.Contact{JID: pnJID("5581"), Action: &waSyncAction.ContactAction{FullName: proto.String("Address Book Name")}})
	if got := getID(t, store, "5581"); got.FullName != "Address Book Name" || got.LID != "111" || got.PushName != "New Push" {
		t.Errorf("contact event: %+v", got)
	}

	recordContactEvent(store, &events.Contact{JID: pnJID("5581"), Action: &waSyncAction.ContactAction{FirstName: proto.String("Only First")}})
	if got := getID(t, store, "5581"); got.FullName != "Only First" {
		t.Errorf("first name fallback: %+v", got)
	}
	recordContactEvent(store, &events.Contact{JID: pnJID("5581")}) // no Action: must not crash
}

func groupInfo(parts ...types.GroupParticipant) *types.GroupInfo {
	return &types.GroupInfo{JID: types.JID{User: "120363", Server: types.GroupServer}, Participants: parts}
}

func TestGroupParticipantsListedAndRecorded(t *testing.T) { // GRP-01, GRP-02
	store := newTestStore(t)
	store.UpsertIdentity("100000000000001", "551188880101", "Alex Morgan", "")
	info := groupInfo(
		types.GroupParticipant{JID: lidJID("100000000000001"), LID: lidJID("100000000000001"), PhoneNumber: pnJID("551188880101")},
		types.GroupParticipant{JID: lidJID("100000000000004"), LID: lidJID("100000000000004"), PhoneNumber: pnJID("551166660303"), IsAdmin: true},
		types.GroupParticipant{JID: pnJID("551100000000"), IsSuperAdmin: true},
		types.GroupParticipant{JID: lidJID("555555")}, // unknown, no phone
	)

	got, err := recordAndListParticipants(store, info)
	if err != nil {
		t.Fatal(err)
	}

	want := []ParticipantInfo{
		{Name: "Alex Morgan", Phone: "551188880101", LID: "100000000000001"},
		{Name: "", Phone: "551166660303", LID: "100000000000004", IsAdmin: true},
		{Name: "", Phone: "551100000000", LID: "", IsAdmin: true},
		{Name: "", Phone: "", LID: "555555"},
	}
	if len(got) != len(want) {
		t.Fatalf("participants = %+v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("participant %d = %+v, want %+v", i, got[i], want[i])
		}
	}
	for lid, phone := range map[string]string{"100000000000001": "551188880101", "100000000000004": "551166660303"} {
		if recorded := getID(t, store, lid); recorded.Phone != phone {
			t.Errorf("link %s -> %s not recorded: %+v", lid, phone, recorded)
		}
	}
	if recorded := getID(t, store, "551100000000"); recorded.LID != "" {
		t.Errorf("phone-only participant recorded with a LID: %+v", recorded)
	}
	if _, known, _ := store.GetIdentity("555555"); !known {
		t.Error("LID-only participant not recorded")
	}
}

func TestGroupParticipantsFetchFailure(t *testing.T) { // GRP-03
	store := newTestStore(t)
	fetch := func(types.JID) (*types.GroupInfo, error) { return nil, errors.New("not in group") }

	status, resp := groupParticipantsResponse(fetch, store, "120363@g.us")

	if status != http.StatusInternalServerError || resp.Success || resp.Message == "" || len(resp.Participants) != 0 {
		t.Errorf("status=%d resp=%+v", status, resp)
	}
	if n := len(identityRows(t, store)); n != 0 {
		t.Errorf("recorded %d identities after a failed fetch", n)
	}
}

func TestGroupParticipantsRejectsBadJID(t *testing.T) { // GRP-03
	store := newTestStore(t)
	status, resp := groupParticipantsResponse(func(types.JID) (*types.GroupInfo, error) {
		t.Error("fetch must not run for an invalid JID")
		return nil, nil
	}, store, "")
	if status != http.StatusBadRequest || resp.Success {
		t.Errorf("status=%d resp=%+v", status, resp)
	}
}

func TestGroupParticipantsSuccessResponse(t *testing.T) { // GRP-01
	store := newTestStore(t)
	fetch := func(jid types.JID) (*types.GroupInfo, error) {
		return groupInfo(types.GroupParticipant{JID: lidJID("1"), LID: lidJID("1"), PhoneNumber: pnJID("2")}), nil
	}
	status, resp := groupParticipantsResponse(fetch, store, "120363@g.us")
	if status != http.StatusOK || !resp.Success || len(resp.Participants) != 1 || resp.Participants[0].Phone != "2" {
		t.Errorf("status=%d resp=%+v", status, resp)
	}
}

func TestGroupParticipantNameFoundByPhoneWhenLIDUnknown(t *testing.T) { // GRP-01
	store := newTestStore(t)
	store.UpsertIdentity("", "551100000000", "Only Phone", "")
	got, err := recordAndListParticipants(store, groupInfo(types.GroupParticipant{JID: pnJID("551100000000")}))
	if err != nil || len(got) != 1 || got[0].Name != "Only Phone" {
		t.Errorf("participants = %+v, err = %v", got, err)
	}
}

func TestGroupParticipantsRejectsNonGroupJID(t *testing.T) { // GRP-03
	store := newTestStore(t)
	status, resp := groupParticipantsResponse(func(types.JID) (*types.GroupInfo, error) {
		t.Error("fetch must not run for a JID that is not a group")
		return nil, nil
	}, store, "551100000000@s.whatsapp.net")
	if status != http.StatusBadRequest || resp.Success {
		t.Errorf("status=%d resp=%+v", status, resp)
	}
}
