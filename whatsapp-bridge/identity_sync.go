package main

import (
	"context"
	"encoding/json"
	"net/http"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

// classifyJID splits a JID into the LID or the phone number it carries.
func classifyJID(jid types.JID) (lid, phone string) {
	if jid.IsEmpty() {
		return "", ""
	}
	switch jid.Server {
	case types.HiddenUserServer:
		return jid.User, ""
	case types.DefaultUserServer:
		return "", jid.User
	}
	return "", ""
}

// pairIdentity reads the LID and the phone from two addresses of the same account, in any order.
func pairIdentity(a, b types.JID) (lid, phone string) {
	lidA, phoneA := classifyJID(a)
	lidB, phoneB := classifyJID(b)
	return firstNonEmpty(lidA, lidB), firstNonEmpty(phoneA, phoneB)
}

// recordMessageIdentity records the push name of a message's sender and links the sender's LID
// and phone when the message carries both.
func recordMessageIdentity(store *MessageStore, info types.MessageInfo) error {
	lid, phone := pairIdentity(info.Sender, info.SenderAlt)
	return store.UpsertIdentity(lid, phone, "", info.PushName)
}

func recordPushNameEvent(store *MessageStore, ev *events.PushName) error {
	lid, phone := pairIdentity(ev.JID, ev.JIDAlt)
	return store.UpsertIdentity(lid, phone, "", ev.NewPushName)
}

func recordContactEvent(store *MessageStore, ev *events.Contact) error {
	if ev.Action == nil {
		return nil
	}
	lid, phone := classifyJID(ev.JID)
	return store.UpsertIdentity(lid, phone, firstNonEmpty(ev.Action.GetFullName(), ev.Action.GetFirstName()), "")
}

// identitySource is where the identity sync reads what WhatsApp knows.
type identitySource interface {
	AllContacts(ctx context.Context) (map[types.JID]types.ContactInfo, error)
	LIDForPN(ctx context.Context, pn types.JID) (types.JID, error)
	PNForLID(ctx context.Context, lid types.JID) (types.JID, error)
}

type whatsmeowIdentitySource struct{ client *whatsmeow.Client }

func (s whatsmeowIdentitySource) AllContacts(ctx context.Context) (map[types.JID]types.ContactInfo, error) {
	return s.client.Store.Contacts.GetAllContacts(ctx)
}

func (s whatsmeowIdentitySource) LIDForPN(ctx context.Context, pn types.JID) (types.JID, error) {
	return s.client.Store.LIDs.GetLIDForPN(ctx, pn)
}

func (s whatsmeowIdentitySource) PNForLID(ctx context.Context, lid types.JID) (types.JID, error) {
	return s.client.Store.LIDs.GetPNForLID(ctx, lid)
}

// syncIdentities fills contacts_identity from the WhatsApp contact store and links the LID
// senders found in stored messages. A lookup that fails only skips that contact. It returns
// how many identities were recorded.
func syncIdentities(ctx context.Context, store *MessageStore, src identitySource, logger waLog.Logger) (int, error) {
	warn := func(format string, args ...interface{}) {
		if logger != nil {
			logger.Warnf(format, args...)
		}
	}

	contacts, err := src.AllContacts(ctx)
	if err != nil {
		return 0, err
	}
	recorded := 0
	for jid, info := range contacts {
		lid, phone := classifyJID(jid)
		if phone != "" && lid == "" {
			if mapped, err := src.LIDForPN(ctx, jid); err != nil {
				warn("Identity sync: LID lookup for %s failed: %v", jid.User, err)
			} else if !mapped.IsEmpty() {
				lid = mapped.User
			}
		} else if lid != "" && phone == "" {
			if mapped, err := src.PNForLID(ctx, jid); err != nil {
				warn("Identity sync: phone lookup for %s failed: %v", jid.User, err)
			} else if !mapped.IsEmpty() {
				phone = mapped.User
			}
		}
		fullName := firstNonEmpty(info.FullName, info.FirstName)
		pushName := firstNonEmpty(info.PushName, info.BusinessName)
		if err := store.UpsertIdentity(lid, phone, fullName, pushName); err != nil {
			warn("Identity sync: could not record %s: %v", jid.User, err)
			continue
		}
		recorded++
	}

	rows, err := store.db.Query("SELECT DISTINCT sender FROM messages WHERE sender IS NOT NULL AND sender != ''")
	if err != nil {
		return recorded, err
	}
	var senders []string
	for rows.Next() {
		var sender string
		if err := rows.Scan(&sender); err != nil {
			rows.Close()
			return recorded, err
		}
		senders = append(senders, sender)
	}
	rows.Close()
	for _, sender := range senders {
		if _, known, err := store.GetIdentity(sender); err != nil || known {
			continue
		}
		mapped, err := src.PNForLID(ctx, types.JID{User: sender, Server: types.HiddenUserServer})
		if err != nil {
			warn("Identity sync: phone lookup for sender %s failed: %v", sender, err)
			continue
		}
		if mapped.IsEmpty() {
			continue
		}
		if err := store.UpsertIdentity(sender, mapped.User, "", ""); err != nil {
			warn("Identity sync: could not record sender %s: %v", sender, err)
			continue
		}
		recorded++
	}
	return recorded, nil
}

// ParticipantInfo is one member of a group as the API reports it.
type ParticipantInfo struct {
	Name    string `json:"name"`
	Phone   string `json:"phone"`
	LID     string `json:"lid"`
	IsAdmin bool   `json:"is_admin"`
}

// GroupParticipantsResponse is the answer of /api/group_participants.
type GroupParticipantsResponse struct {
	Success      bool              `json:"success"`
	Message      string            `json:"message,omitempty"`
	Participants []ParticipantInfo `json:"participants,omitempty"`
}

// recordAndListParticipants records each participant's LID and phone and lists them with the
// names known for them. Admins and super admins are both reported as admins.
func recordAndListParticipants(store *MessageStore, info *types.GroupInfo) ([]ParticipantInfo, error) {
	out := make([]ParticipantInfo, 0, len(info.Participants))
	for _, p := range info.Participants {
		lid, phone := pairIdentity(p.LID, p.PhoneNumber)
		jidLID, jidPhone := classifyJID(p.JID)
		lid, phone = firstNonEmpty(lid, jidLID), firstNonEmpty(phone, jidPhone)

		if err := store.UpsertIdentity(lid, phone, "", ""); err != nil {
			return nil, err
		}
		name := ""
		for _, id := range []string{lid, phone} {
			if known, ok, err := store.GetIdentity(id); err == nil && ok && known.DisplayName() != "" {
				name = known.DisplayName()
				break
			}
		}
		out = append(out, ParticipantInfo{Name: name, Phone: phone, LID: lid, IsAdmin: p.IsAdmin || p.IsSuperAdmin})
	}
	return out, nil
}

// groupParticipantsResponse answers a participants request. Nothing is recorded when the group
// information cannot be fetched.
func groupParticipantsResponse(fetch func(types.JID) (*types.GroupInfo, error), store *MessageStore, chatJID string) (int, GroupParticipantsResponse) {
	jid, err := types.ParseJID(chatJID)
	if chatJID == "" || err != nil || jid.Server != types.GroupServer {
		return http.StatusBadRequest, GroupParticipantsResponse{Message: "chat_jid must be a group JID (…@g.us)"}
	}
	info, err := fetch(jid)
	if err != nil {
		return http.StatusInternalServerError, GroupParticipantsResponse{Message: "Failed to get group info: " + err.Error()}
	}
	participants, err := recordAndListParticipants(store, info)
	if err != nil {
		return http.StatusInternalServerError, GroupParticipantsResponse{Message: "Failed to record participants: " + err.Error()}
	}
	return http.StatusOK, GroupParticipantsResponse{Success: true, Participants: participants}
}

func handleGroupParticipants(client *whatsmeow.Client, store *MessageStore) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			ChatJID string `json:"chat_jid"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "Invalid request format", http.StatusBadRequest)
			return
		}
		fetch := func(jid types.JID) (*types.GroupInfo, error) { return client.GetGroupInfo(r.Context(), jid) }
		status, resp := groupParticipantsResponse(fetch, store, req.ChatJID)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		json.NewEncoder(w).Encode(resp)
	}
}
