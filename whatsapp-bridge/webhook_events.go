package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"time"
	"unicode/utf8"

	"go.mau.fi/whatsmeow/types/events"
)

// Builders of the webhook events. Meta is what always goes out; Content only to endpoints that asked
// for include_content. The text of people never goes in Meta.

func messageWebhookEvent(msg *events.Message, content, mediaType, filename string) webhookEvent {
	chat := msg.Info.Chat.String()
	meta := map[string]any{
		"message_id": msg.Info.ID, "chat_jid": chat, "sender": msg.Info.Sender.User,
		"is_from_me": msg.Info.IsFromMe, "timestamp": msg.Info.Timestamp.UTC().Format(time.RFC3339Nano),
		"content_length": utf8.RuneCountInString(content),
	}
	if mediaType != "" {
		meta["media_type"] = mediaType
	}
	body := map[string]any{"text": content}
	if filename != "" {
		body["filename"] = filename
	}
	return webhookEvent{Type: eventMessage, Key: chat + "|" + msg.Info.ID, OccurredAt: msg.Info.Timestamp, ChatJID: chat,
		FromMe: msg.Info.IsFromMe, Meta: meta, Content: body}
}

func messageEditWebhookEvent(msg *events.Message, targetID, text string) webhookEvent {
	chat := msg.Info.Chat.String()
	return webhookEvent{Type: eventMessageEdit, Key: fmt.Sprintf("%s|%s|%d", chat, targetID, msg.Info.Timestamp.UnixMilli()),
		OccurredAt: msg.Info.Timestamp, ChatJID: chat, FromMe: msg.Info.IsFromMe,
		Meta: map[string]any{"message_id": targetID, "chat_jid": chat, "sender": msg.Info.Sender.User,
			"content_length": utf8.RuneCountInString(text)},
		Content: map[string]any{"text": text}}
}

func reactionWebhookEvent(msg *events.Message, reactor string) (webhookEvent, bool) {
	reaction := msg.Message.GetReactionMessage()
	target := reaction.GetKey().GetID()
	if reaction == nil || target == "" {
		return webhookEvent{}, false
	}
	at := msg.Info.Timestamp
	if ms := reaction.GetSenderTimestampMS(); ms > 0 {
		at = time.UnixMilli(ms)
	}
	chat := msg.Info.Chat.String()
	return webhookEvent{Type: eventReaction, Key: fmt.Sprintf("%s|%s|%s|%d", chat, target, reactor, at.UnixMilli()),
		OccurredAt: at, ChatJID: chat, FromMe: msg.Info.IsFromMe,
		Meta: map[string]any{"target_id": target, "chat_jid": chat, "reactor": reactor,
			"retracted": reaction.GetText() == "", "timestamp": at.UTC().Format(time.RFC3339Nano)},
		Content: map[string]any{"emoji": reaction.GetText()}}, true
}

func pollCreatedWebhookEvent(poll Poll, at time.Time, fromMe bool) webhookEvent {
	return webhookEvent{Type: eventPollCreated, Key: poll.ChatJID + "|" + poll.ID, OccurredAt: at, ChatJID: poll.ChatJID, FromMe: fromMe,
		Meta: map[string]any{"poll_id": poll.ID, "chat_jid": poll.ChatJID, "author": poll.Author,
			"option_count": len(poll.Options), "anonymous": poll.HideVoterNames},
		Content: map[string]any{"question": poll.Question, "options": poll.Options}}
}

// pollVoteWebhookEvent: the voter is null in an anonymous poll; the option names go only with content.
func pollVoteWebhookEvent(poll Poll, voter string, hashes []string, at time.Time, fromMe bool) webhookEvent {
	byHash := map[string]string{}
	for _, option := range poll.Options {
		byHash[hex.EncodeToString(sha256Of(option))] = option
	}
	chosen := []string{}
	for _, h := range hashes {
		if name, ok := byHash[h]; ok {
			chosen = append(chosen, name)
		}
	}
	var voterID any = voter
	if poll.HideVoterNames {
		voterID = nil
	}
	return webhookEvent{Type: eventPollVote, Key: fmt.Sprintf("%s|%s|%s|%d", poll.ChatJID, poll.ID, voter, at.UnixMilli()),
		OccurredAt: at, ChatJID: poll.ChatJID, FromMe: fromMe,
		Meta: map[string]any{"poll_id": poll.ID, "chat_jid": poll.ChatJID, "voter": voterID, "retracted": len(hashes) == 0,
			"option_count": len(hashes), "timestamp": at.UTC().Format(time.RFC3339Nano)},
		Content: map[string]any{"options": chosen}}
}

func connectionWebhookEvent(state string, at time.Time) webhookEvent {
	return webhookEvent{Type: eventConnection, Key: state + "|" + strconv.FormatInt(at.UnixMilli(), 10), OccurredAt: at,
		Meta: map[string]any{"state": state}}
}

// handleWebhookTest answers POST /api/webhooks/test with {"name": "<endpoint>"}.
func handleWebhookTest(store *MessageStore) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			Name string `json:"name"`
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&req); err != nil || req.Name == "" {
			http.Error(w, "Invalid request body: name is required", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		res, err := store.webhooks.Test(req.Name)
		if err == errWebhookEndpointNotFound {
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]any{"success": false, "error_code": "ENDPOINT_NOT_FOUND",
				"message": "No webhook endpoint is named " + strconv.Quote(req.Name)})
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"success": res.Error == "" && res.Status >= 200 && res.Status < 300,
			"name": res.Name, "status": res.Status, "latency_ms": res.LatencyMs, "error": res.Error})
	}
}

func sha256Of(text string) []byte {
	sum := sha256.Sum256([]byte(text))
	return sum[:]
}
