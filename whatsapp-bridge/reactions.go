package main

import (
	"database/sql"
	"fmt"
	"net/http"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

// reactionSchema holds the current reaction of each person to a message. A reaction that was taken back
// stays as a row with an empty emoji, so an older reaction that arrives late cannot bring it back.
const reactionSchema = `
	CREATE TABLE IF NOT EXISTS reactions (
		target_id TEXT,
		chat_jid TEXT,
		reactor TEXT,
		emoji TEXT,
		timestamp_ms INTEGER,
		PRIMARY KEY (target_id, chat_jid, reactor)
	);
`

// Reaction is the current reaction of one person to a message.
type Reaction struct {
	Reactor string
	Emoji   string
	At      time.Time
}

// StoreReaction records the reaction of reactor, which replaces the one before it only when it is newer.
// An empty emoji takes the reaction back. It reports whether anything was stored.
func (store *MessageStore) StoreReaction(targetID, chatJID, reactor, emoji string, at time.Time) (bool, error) {
	if deleted, err := store.isDeleted(targetID, chatJID); err != nil || deleted {
		return false, err
	}
	res, err := store.db.Exec(
		`INSERT INTO reactions (target_id, chat_jid, reactor, emoji, timestamp_ms) VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(target_id, chat_jid, reactor) DO UPDATE SET
			emoji = excluded.emoji, timestamp_ms = excluded.timestamp_ms
		WHERE excluded.timestamp_ms > reactions.timestamp_ms`,
		targetID, chatJID, reactor, emoji, at.UnixMilli(),
	)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// Reactions returns the reactions to a message that still stand, oldest first.
func (store *MessageStore) Reactions(targetID, chatJID string) ([]Reaction, error) {
	rows, err := store.db.Query(
		"SELECT reactor, emoji, timestamp_ms FROM reactions WHERE target_id = ? AND chat_jid = ? AND emoji != '' ORDER BY timestamp_ms, reactor",
		targetID, chatJID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var list []Reaction
	for rows.Next() {
		var r Reaction
		var ms int64
		if err := rows.Scan(&r.Reactor, &r.Emoji, &ms); err != nil {
			return nil, err
		}
		r.At = time.UnixMilli(ms).UTC()
		list = append(list, r)
	}
	return list, rows.Err()
}

// recordReaction stores the reaction of a reaction message, under this account's own user when it is ours.
// It reports whether the message was a reaction, which is never stored as a message of the chat.
func recordReaction(store *MessageStore, own string, evt *events.Message, logger waLog.Logger) bool {
	reaction := evt.Message.GetReactionMessage()
	if reaction == nil {
		return false
	}
	target := reaction.GetKey().GetID()
	who := evt.Info.Sender.User
	if evt.Info.IsFromMe && own != "" {
		who = own
	}
	at := evt.Info.Timestamp
	if ms := reaction.GetSenderTimestampMS(); ms > 0 {
		at = time.UnixMilli(ms)
	}
	if target == "" {
		logger.Warnf("A reaction in %s names no message", evt.Info.Chat)
		return true
	}
	if stored, err := store.StoreReaction(target, evt.Info.Chat.String(), who, reaction.GetText(), at); err != nil {
		logger.Warnf("Failed to store a reaction to %s: %v", target, err)
	} else if stored { // an older reaction than the one we have is not news
		if hook, ok := reactionWebhookEvent(evt, who); ok {
			store.webhooks.Emit(hook)
		}
	}
	return true
}

const maxEmojiRunes = 16

// validEmoji accepts an empty emoji (taking the reaction back) or up to 16 characters without spaces,
// which is enough for the longest emoji sequences.
func validEmoji(emoji string) bool {
	if utf8.RuneCountInString(emoji) > maxEmojiRunes {
		return false
	}
	return !strings.ContainsFunc(emoji, unicode.IsSpace)
}

// ReactRequest is the body of /api/react. An empty Emoji takes the reaction back.
type ReactRequest struct {
	ChatJID   string `json:"chat_jid"`
	MessageID string `json:"message_id"`
	Emoji     string `json:"emoji"`
}

// reactToMessage reacts to a stored message and records our reaction. In a group the reaction names the
// sender of the message; for our own message the sender is left empty.
func reactToMessage(env pollEnv, store *MessageStore, req ReactRequest, logger waLog.Logger) (int, SendMessageResponse) {
	chat, err := types.ParseJID(req.ChatJID)
	if err != nil || !strings.Contains(req.ChatJID, "@") || req.MessageID == "" {
		return failure(http.StatusBadRequest, "chat_jid (a JID) and message_id are required")
	}
	if !validEmoji(req.Emoji) {
		return failure(http.StatusBadRequest, "Invalid emoji: use up to %d characters without spaces, or an empty one to take the reaction back", maxEmojiRunes)
	}
	user, fromMe, err := store.GetMessageSenderInfo(req.MessageID, chat.String())
	if err == sql.ErrNoRows {
		return failure(http.StatusNotFound, "Message %s was not found in %s", req.MessageID, req.ChatJID)
	}
	if err != nil {
		return failure(http.StatusInternalServerError, "Error reading the message: %v", err)
	}
	if !env.connected() {
		return failure(http.StatusServiceUnavailable, "Not connected to WhatsApp")
	}

	sender := types.EmptyJID
	if !fromMe {
		sender = env.resolveSender(user)
	}
	resp, err := env.send(chat, env.buildReaction(chat, sender, req.MessageID, req.Emoji))
	if err != nil {
		return failure(http.StatusInternalServerError, "Error sending the reaction: %v", err)
	}
	if _, err := store.StoreReaction(req.MessageID, chat.String(), env.own.User, req.Emoji, resp.Timestamp); err != nil {
		logger.Warnf("The reaction to %s was sent but could not be stored: %v", req.MessageID, err)
	}
	return http.StatusOK, SendMessageResponse{Success: true, Message: fmt.Sprintf("Reaction sent to %s", req.MessageID),
		MessageID: resp.ID, ChatJID: chat.String()}
}
