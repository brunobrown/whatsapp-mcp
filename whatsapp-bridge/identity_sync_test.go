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
	failPN   map[string]bool // LIDForPN falha para estes telefones
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
			pnJID("558195246671"): {FullName: "Edmilson Santana - Orube", FirstName: "Edmilson", PushName: "Edmilson"},
			pnJID("558191374322"): {FirstName: "Nai", PushName: "Naiara"}, // sem full name: usa o primeiro nome
			lidJID("999"):         {PushName: "Só Lid"},
		},
		pnToLID: map[string]string{"558195246671": "123"},
	}

	if _, err := syncIdentities(context.Background(), store, src, nil); err != nil {
		t.Fatal(err)
	}

	got := getID(t, store, "558195246671")
	if got != (Identity{LID: "123", Phone: "558195246671", FullName: "Edmilson Santana - Orube", PushName: "Edmilson"}) {
		t.Errorf("identity = %+v", got)
	}
	if got := getID(t, store, "123"); got.Phone != "558195246671" {
		t.Errorf("lookup by LID = %+v", got)
	}
	if got := getID(t, store, "558191374322"); got.FullName != "Nai" || got.PushName != "Naiara" {
		t.Errorf("first-name fallback = %+v", got)
	}
	if got := getID(t, store, "999"); got.LID != "999" || got.PushName != "Só Lid" {
		t.Errorf("lid-only contact = %+v", got)
	}
}

func TestSyncLinksLIDSendersFromMessages(t *testing.T) { // SYNC-02
	store := newTestStore(t)
	seedMedia(t, store, "M1", "image", "f.jpg") // cria o chat
	for _, sender := range []string{"262242197061776", "777", "unknown-lid-1"} {
		if err := store.StoreMessage("m-"+sender, "chat@g.us", sender, "oi", time.Now(), false, "", "", "", nil, nil, nil, 0); err != nil {
			t.Fatal(err)
		}
	}
	src := &fakeSource{lidToPN: map[string]string{"262242197061776": "555181993271"}}

	if _, err := syncIdentities(context.Background(), store, src, nil); err != nil {
		t.Fatal(err)
	}

	got := getID(t, store, "262242197061776")
	if got.Phone != "555181993271" {
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
			pnJID("111"): {FullName: "Quebrado"},
			pnJID("222"): {FullName: "Inteiro"},
		},
		pnToLID: map[string]string{"222": "lid222"},
		failPN:  map[string]bool{"111": true},
	}

	if _, err := syncIdentities(context.Background(), store, src, nil); err != nil {
		t.Fatal(err)
	}

	if got := getID(t, store, "111"); got.FullName != "Quebrado" {
		t.Errorf("contact whose LID lookup failed must still be recorded: %+v", got)
	}
	if got := getID(t, store, "222"); got.LID != "lid222" {
		t.Errorf("other contacts must not be affected: %+v", got)
	}
}

func TestMessageIdentityLinksSenderAndAlt(t *testing.T) { // SYNC-03
	store := newTestStore(t)
	info := types.MessageInfo{
		MessageSource: types.MessageSource{Sender: lidJID("160889625940144"), SenderAlt: pnJID("555181993271")},
		PushName:      "Teilor",
	}
	recordMessageIdentity(store, info)
	got := getID(t, store, "160889625940144")
	if got != (Identity{LID: "160889625940144", Phone: "555181993271", PushName: "Teilor"}) {
		t.Errorf("identity = %+v", got)
	}

	// sem SenderAlt: só o id que existe, e a ordem inversa (telefone como Sender) também liga
	recordMessageIdentity(store, types.MessageInfo{
		MessageSource: types.MessageSource{Sender: pnJID("558181282941")}, PushName: "Maria",
	})
	if got := getID(t, store, "558181282941"); got.PushName != "Maria" || got.LID != "" {
		t.Errorf("identity = %+v", got)
	}
	recordMessageIdentity(store, types.MessageInfo{
		MessageSource: types.MessageSource{Sender: pnJID("558181282941"), SenderAlt: lidJID("555")}, PushName: "Maria",
	})
	if got := getID(t, store, "555"); got.Phone != "558181282941" {
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
	recordPushNameEvent(store, &events.PushName{JID: lidJID("111"), JIDAlt: pnJID("5581"), NewPushName: "Novo Push"})
	if got := getID(t, store, "111"); got != (Identity{LID: "111", Phone: "5581", PushName: "Novo Push"}) {
		t.Errorf("push name event: %+v", got)
	}

	recordContactEvent(store, &events.Contact{JID: pnJID("5581"), Action: &waSyncAction.ContactAction{FullName: proto.String("Nome da Agenda")}})
	if got := getID(t, store, "5581"); got.FullName != "Nome da Agenda" || got.LID != "111" || got.PushName != "Novo Push" {
		t.Errorf("contact event: %+v", got)
	}

	recordContactEvent(store, &events.Contact{JID: pnJID("5581"), Action: &waSyncAction.ContactAction{FirstName: proto.String("Só Primeiro")}})
	if got := getID(t, store, "5581"); got.FullName != "Só Primeiro" {
		t.Errorf("first name fallback: %+v", got)
	}
	recordContactEvent(store, &events.Contact{JID: pnJID("5581")}) // sem Action: não pode derrubar
}

func groupInfo(parts ...types.GroupParticipant) *types.GroupInfo {
	return &types.GroupInfo{JID: types.JID{User: "120363", Server: types.GroupServer}, Participants: parts}
}

func TestGroupParticipantsListedAndRecorded(t *testing.T) { // GRP-01, GRP-02
	store := newTestStore(t)
	store.UpsertIdentity("160889625940144", "555181993271", "Teilor Dall Bello", "")
	info := groupInfo(
		types.GroupParticipant{JID: lidJID("160889625940144"), LID: lidJID("160889625940144"), PhoneNumber: pnJID("555181993271")},
		types.GroupParticipant{JID: lidJID("241841085649006"), LID: lidJID("241841085649006"), PhoneNumber: pnJID("558181282941"), IsAdmin: true},
		types.GroupParticipant{JID: pnJID("558100000000"), IsSuperAdmin: true},
		types.GroupParticipant{JID: lidJID("555555")}, // desconhecido, sem telefone
	)

	got, err := recordAndListParticipants(store, info)
	if err != nil {
		t.Fatal(err)
	}

	want := []ParticipantInfo{
		{Name: "Teilor Dall Bello", Phone: "555181993271", LID: "160889625940144"},
		{Name: "", Phone: "558181282941", LID: "241841085649006", IsAdmin: true},
		{Name: "", Phone: "558100000000", LID: "", IsAdmin: true},
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
	if recorded := getID(t, store, "241841085649006"); recorded.Phone != "558181282941" {
		t.Errorf("participant link not recorded: %+v", recorded)
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
	store.UpsertIdentity("", "558100000000", "Só Telefone", "")
	got, err := recordAndListParticipants(store, groupInfo(types.GroupParticipant{JID: pnJID("558100000000")}))
	if err != nil || len(got) != 1 || got[0].Name != "Só Telefone" {
		t.Errorf("participants = %+v, err = %v", got, err)
	}
}

func TestGroupParticipantsRejectsNonGroupJID(t *testing.T) { // GRP-03
	store := newTestStore(t)
	status, resp := groupParticipantsResponse(func(types.JID) (*types.GroupInfo, error) {
		t.Error("fetch must not run for a JID that is not a group")
		return nil, nil
	}, store, "558100000000@s.whatsapp.net")
	if status != http.StatusBadRequest || resp.Success {
		t.Errorf("status=%d resp=%+v", status, resp)
	}
}
