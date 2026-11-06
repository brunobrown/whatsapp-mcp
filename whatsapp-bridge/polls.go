package main

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

// pollSchema holds the polls we know and the votes we saw. A vote stores the hashes of the options
// (SHA-256 of the option name, as WhatsApp sends them) because it can arrive before its poll, and the
// reader resolves them to names.
const pollSchema = `
	CREATE TABLE IF NOT EXISTS polls (
		id TEXT,
		chat_jid TEXT,
		author TEXT,
		question TEXT,
		options_json TEXT,
		selectable_count INTEGER,
		allow_add_option BOOLEAN,
		hide_voter_names BOOLEAN,
		PRIMARY KEY (id, chat_jid)
	);

	CREATE TABLE IF NOT EXISTS poll_votes (
		poll_id TEXT,
		chat_jid TEXT,
		voter TEXT,
		hashes_json TEXT,
		timestamp_ms INTEGER,
		PRIMARY KEY (poll_id, chat_jid, voter)
	);
`

// Poll is a poll message: the question, the options in the order they were created and how it
// accepts answers. SelectableCount is 1 for a single answer and 0 for any number.
type Poll struct {
	ID              string
	ChatJID         string
	Author          string // user part of whoever created it; only they may change it
	Question        string
	Options         []string
	SelectableCount int
	AllowAddOption  bool
	HideVoterNames  bool
}

// PollVote is the current choice of one person: hex hashes of the options, none once retracted.
type PollVote struct {
	Voter  string
	Hashes []string
	At     time.Time
}

// StorePoll records a poll and reports whether it was stored. Storing it again (echo, history sync)
// updates the poll and keeps its votes, but only for its author: anyone else sending a poll with
// the ID of an existing one must not rewrite its question or options.
func (store *MessageStore) StorePoll(p Poll) (bool, error) {
	options, err := json.Marshal(p.Options)
	if err != nil {
		return false, err
	}
	res, err := store.db.Exec(
		`INSERT INTO polls (id, chat_jid, author, question, options_json, selectable_count, allow_add_option, hide_voter_names)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id, chat_jid) DO UPDATE SET
			question = excluded.question, options_json = excluded.options_json,
			selectable_count = excluded.selectable_count, allow_add_option = excluded.allow_add_option,
			hide_voter_names = excluded.hide_voter_names
		WHERE polls.author = excluded.author`,
		p.ID, p.ChatJID, p.Author, p.Question, string(options), p.SelectableCount, p.AllowAddOption, p.HideVoterNames,
	)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// GetPoll returns the poll, and whether it is known.
func (store *MessageStore) GetPoll(id, chatJID string) (Poll, bool, error) {
	p := Poll{ID: id, ChatJID: chatJID}
	var options string
	err := store.db.QueryRow(
		"SELECT author, question, options_json, selectable_count, allow_add_option, hide_voter_names FROM polls WHERE id = ? AND chat_jid = ?",
		id, chatJID,
	).Scan(&p.Author, &p.Question, &options, &p.SelectableCount, &p.AllowAddOption, &p.HideVoterNames)
	if err == sql.ErrNoRows {
		return Poll{}, false, nil
	}
	if err != nil {
		return Poll{}, false, err
	}
	if err := json.Unmarshal([]byte(options), &p.Options); err != nil {
		return Poll{}, false, err
	}
	return p, true, nil
}

// StorePollVote records the choice of voter, which replaces the one before it only when it is newer.
// It reports whether the vote was stored. An empty hashes list retracts the vote.
func (store *MessageStore) StorePollVote(pollID, chatJID, voter string, hashes []string, at time.Time) (bool, error) {
	if hashes == nil {
		hashes = []string{}
	}
	encoded, err := json.Marshal(hashes)
	if err != nil {
		return false, err
	}
	res, err := store.db.Exec(
		`INSERT INTO poll_votes (poll_id, chat_jid, voter, hashes_json, timestamp_ms) VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(poll_id, chat_jid, voter) DO UPDATE SET
			hashes_json = excluded.hashes_json, timestamp_ms = excluded.timestamp_ms
		WHERE excluded.timestamp_ms > poll_votes.timestamp_ms`,
		pollID, chatJID, voter, string(encoded), at.UnixMilli(),
	)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// PollVotes returns the current choice of every person who voted in the poll.
func (store *MessageStore) PollVotes(pollID, chatJID string) ([]PollVote, error) {
	rows, err := store.db.Query(
		"SELECT voter, hashes_json, timestamp_ms FROM poll_votes WHERE poll_id = ? AND chat_jid = ? ORDER BY timestamp_ms, voter",
		pollID, chatJID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var votes []PollVote
	for rows.Next() {
		var v PollVote
		var hashes string
		var ms int64
		if err := rows.Scan(&v.Voter, &hashes, &ms); err != nil {
			return nil, err
		}
		if err := json.Unmarshal([]byte(hashes), &v.Hashes); err != nil {
			return nil, err
		}
		v.At = time.UnixMilli(ms).UTC()
		votes = append(votes, v)
	}
	return votes, rows.Err()
}

// DeletePoll removes the poll and its votes.
func (store *MessageStore) DeletePoll(id, chatJID string) error {
	tx, err := store.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.Exec("DELETE FROM poll_votes WHERE poll_id = ? AND chat_jid = ?", id, chatJID); err != nil {
		return err
	}
	if _, err := tx.Exec("DELETE FROM polls WHERE id = ? AND chat_jid = ?", id, chatJID); err != nil {
		return err
	}
	return tx.Commit()
}

// pollContentPrefix marks the stored text of a poll message, so list_messages and the monitor
// show the poll as what it is.
const pollContentPrefix = "[enquete] "

// pollCreationOf returns the poll creation inside msg. WhatsApp sends it in different fields
// depending on the client and on how the poll accepts answers.
func pollCreationOf(msg *waE2E.Message) *waE2E.PollCreationMessage {
	if msg == nil {
		return nil
	}
	for _, poll := range []*waE2E.PollCreationMessage{
		msg.GetPollCreationMessage(), msg.GetPollCreationMessageV2(), msg.GetPollCreationMessageV3(),
		msg.GetPollCreationMessageV5(), msg.GetPollCreationMessageV6(),
	} {
		if poll != nil {
			return poll
		}
	}
	return pollCreationOf(msg.GetPollCreationMessageV4().GetMessage())
}

// pollFromMessage reads the poll of a message, and reports whether it holds one.
func pollFromMessage(chatJID, id, author string, msg *waE2E.Message) (Poll, bool) {
	creation := pollCreationOf(msg)
	if creation == nil {
		return Poll{}, false
	}
	options := make([]string, 0, len(creation.GetOptions()))
	for _, option := range creation.GetOptions() {
		options = append(options, option.GetOptionName())
	}
	return Poll{
		ID: id, ChatJID: chatJID, Author: author, Question: creation.GetName(), Options: options,
		SelectableCount: int(creation.GetSelectableOptionsCount()),
		AllowAddOption:  creation.GetAllowAddOption(), HideVoterNames: creation.GetHideParticipantName(),
	}, true
}

// pollVoteDecryptor decrypts the vote of a poll update message (client.DecryptPollVote).
type pollVoteDecryptor func(*events.Message) (*waE2E.PollVoteMessage, error)

// recordPoll stores the poll of a poll message, or the decrypted vote of a poll update. A vote that
// cannot be decrypted (the bridge never saw the poll) is only logged. It reports false when the
// message is a poll that was refused because it reuses the ID of another person's poll.
func recordPoll(store *MessageStore, decrypt pollVoteDecryptor, evt *events.Message, logger waLog.Logger) bool {
	chatJID := evt.Info.Chat.String()
	if poll, ok := pollFromMessage(chatJID, evt.Info.ID, evt.Info.Sender.User, evt.Message); ok {
		stored, err := store.StorePoll(poll)
		if err != nil {
			logger.Warnf("Failed to store poll %s: %v", evt.Info.ID, err)
		} else if !stored {
			logger.Warnf("Ignoring a poll that reuses the ID of a poll by someone else (%s)", evt.Info.ID)
			return false
		}
		return true
	}

	update := evt.Message.GetPollUpdateMessage()
	pollID := update.GetPollCreationMessageKey().GetID()
	if update == nil || pollID == "" {
		return true
	}
	vote, err := decrypt(evt)
	if err != nil {
		logger.Warnf("Could not read a vote on poll %s: %v", pollID, err)
		return true
	}
	hashes := make([]string, 0, len(vote.GetSelectedOptions()))
	for _, hash := range vote.GetSelectedOptions() {
		hashes = append(hashes, hex.EncodeToString(hash))
	}
	at := evt.Info.Timestamp
	if ms := update.GetSenderTimestampMS(); ms > 0 {
		at = time.UnixMilli(ms)
	}
	if _, err := store.StorePollVote(pollID, chatJID, evt.Info.Sender.User, hashes, at); err != nil {
		logger.Warnf("Failed to store a vote on poll %s: %v", pollID, err)
	}
	return true
}

// WhatsApp's limits for a poll.
const (
	maxPollQuestion = 255
	maxPollOptions  = 12
	minPollOptions  = 2
	maxPollOption   = 100
)

// validatePoll checks the question and options against WhatsApp's limits. An option is the same as
// another when only the case differs, since a vote could not tell them apart in the app.
func validatePoll(question string, options []string) error {
	if strings.TrimSpace(question) == "" || utf8.RuneCountInString(question) > maxPollQuestion {
		return fmt.Errorf("the question must have 1 to %d characters", maxPollQuestion)
	}
	if len(options) < minPollOptions || len(options) > maxPollOptions {
		return fmt.Errorf("a poll needs %d to %d options, got %d", minPollOptions, maxPollOptions, len(options))
	}
	seen := map[string]bool{}
	for _, option := range options {
		if strings.TrimSpace(option) == "" || utf8.RuneCountInString(option) > maxPollOption {
			return fmt.Errorf("each option must have 1 to %d characters", maxPollOption)
		}
		key := strings.ToLower(strings.TrimSpace(option))
		if seen[key] {
			return fmt.Errorf("the option %q is repeated", option)
		}
		seen[key] = true
	}
	return nil
}

// pollEnv is what the poll endpoints need from the WhatsApp client, so it can be replaced in tests.
type pollEnv struct {
	own       types.JID
	connected func() bool
	send      func(to types.JID, msg *waE2E.Message) (whatsmeow.SendResponse, error)
	// buildVote makes the vote message for a poll (client.BuildPollVote); resolveSender gives the
	// JID of a stored sender user (phone number or LID).
	buildVote     func(poll *types.MessageInfo, options []string) (*waE2E.Message, error)
	resolveSender func(user string) types.JID
}

func newPollEnv(client *whatsmeow.Client) pollEnv {
	env := pollEnv{
		connected: func() bool { return client.IsConnected() },
		send: func(to types.JID, msg *waE2E.Message) (whatsmeow.SendResponse, error) {
			return client.SendMessage(context.Background(), to, msg)
		},
		buildVote: func(poll *types.MessageInfo, options []string) (*waE2E.Message, error) {
			return client.BuildPollVote(context.Background(), poll, options)
		},
		resolveSender: func(user string) types.JID { return resolveSenderJID(client, user) },
	}
	if client != nil && client.Store != nil && client.Store.ID != nil {
		env.own = *client.Store.ID
	}
	return env
}

// pollHandler answers a POST with the status and JSON body that run returns.
func pollHandler(run func(r *http.Request) (int, any)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		status, body := run(r)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		json.NewEncoder(w).Encode(body)
	}
}

// pollEndpoint decodes the request body into T and hands it to run.
func pollEndpoint[T any](run func(T) (int, SendMessageResponse)) http.HandlerFunc {
	return pollHandler(func(r *http.Request) (int, any) {
		var req T
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			return http.StatusBadRequest, SendMessageResponse{Message: "Invalid request format"}
		}
		return run(req)
	})
}

// parseRecipient turns a JID or a phone number into the JID to send to.
func parseRecipient(recipient string) (types.JID, error) {
	if strings.Contains(recipient, "@") {
		return types.ParseJID(recipient)
	}
	return types.JID{User: recipient, Server: types.DefaultUserServer}, nil
}

// SendPollRequest is the body of /api/send_poll. AllowMultiple defaults to true, like the app.
type SendPollRequest struct {
	Recipient      string   `json:"recipient"`
	Question       string   `json:"question"`
	Options        []string `json:"options"`
	AllowMultiple  *bool    `json:"allow_multiple,omitempty"`
	AllowAddOption bool     `json:"allow_add_option,omitempty"`
	HideVoterNames bool     `json:"hide_voter_names,omitempty"`
}

// buildPollMessage makes the poll creation message. The message secret is what lets the votes be
// decrypted later; WhatsApp does not send the poll back to the device that created it.
func buildPollMessage(req SendPollRequest) *waE2E.Message {
	selectable := uint32(0)
	if req.AllowMultiple != nil && !*req.AllowMultiple {
		selectable = 1
	}
	options := make([]*waE2E.PollCreationMessage_Option, len(req.Options))
	for i, option := range req.Options {
		options[i] = &waE2E.PollCreationMessage_Option{OptionName: proto.String(option)}
	}
	creation := &waE2E.PollCreationMessage{
		Name: proto.String(req.Question), Options: options, SelectableOptionsCount: proto.Uint32(selectable),
	}
	if req.AllowAddOption {
		creation.AllowAddOption = proto.Bool(true)
	}
	if req.HideVoterNames {
		creation.HideParticipantName = proto.Bool(true)
	}
	secret := make([]byte, 32)
	rand.Read(secret)
	return &waE2E.Message{
		PollCreationMessage: creation,
		MessageContextInfo:  &waE2E.MessageContextInfo{MessageSecret: secret},
	}
}

func failure(status int, format string, args ...any) (int, SendMessageResponse) {
	return status, SendMessageResponse{Message: fmt.Sprintf(format, args...)}
}

// sendPoll sends a poll and records it as ours.
func sendPoll(env pollEnv, store *MessageStore, req SendPollRequest, logger waLog.Logger) (int, SendMessageResponse) {
	if req.Recipient == "" {
		return failure(http.StatusBadRequest, "Recipient is required")
	}
	if err := validatePoll(req.Question, req.Options); err != nil {
		return failure(http.StatusBadRequest, "Invalid poll: %v", err)
	}
	if !env.connected() {
		return failure(http.StatusServiceUnavailable, "Not connected to WhatsApp")
	}
	to, err := parseRecipient(req.Recipient)
	if err != nil {
		return failure(http.StatusBadRequest, "Error parsing JID: %v", err)
	}

	msg := buildPollMessage(req)
	resp, sendErr := env.send(to, msg)
	res := completeSend(store, to.String(), env.own.User, msg, resp, sendErr, logger)
	if !res.Success {
		return http.StatusInternalServerError, newSendResponse(res)
	}
	if poll, ok := pollFromMessage(to.String(), res.MessageID, env.own.User, msg); ok {
		if _, err := store.StorePoll(poll); err != nil {
			logger.Warnf("Poll %s was sent but could not be stored: %v", res.MessageID, err)
		}
	}
	res.Message = fmt.Sprintf("Poll sent to %s", req.Recipient)
	return http.StatusOK, newSendResponse(res)
}

// VotePollRequest is the body of /api/vote_poll. An empty Options retracts the vote.
type VotePollRequest struct {
	ChatJID   string   `json:"chat_jid"`
	MessageID string   `json:"message_id"`
	Options   []string `json:"options"`
}

// chosenOptions maps the names asked for (any case) to the poll's own spelling, which is what the
// vote is hashed from.
func chosenOptions(poll Poll, asked []string) ([]string, error) {
	if poll.SelectableCount == 1 && len(asked) > 1 {
		return nil, errors.New("this poll accepts a single answer")
	}
	chosen := make([]string, 0, len(asked))
	picked := map[string]bool{}
	for _, name := range asked {
		var match string
		for _, option := range poll.Options {
			if strings.EqualFold(strings.TrimSpace(option), strings.TrimSpace(name)) {
				match = option
				break
			}
		}
		if match == "" {
			return nil, fmt.Errorf("the poll has no option %q", name)
		}
		if picked[match] {
			return nil, fmt.Errorf("the option %q was chosen twice", name)
		}
		picked[match] = true
		chosen = append(chosen, match)
	}
	return chosen, nil
}

// votePoll votes in a poll we know, and records our vote.
func votePoll(env pollEnv, store *MessageStore, req VotePollRequest, logger waLog.Logger) (int, SendMessageResponse) {
	chat, err := types.ParseJID(req.ChatJID)
	if err != nil || req.MessageID == "" {
		return failure(http.StatusBadRequest, "chat_jid and message_id are required")
	}
	poll, found, err := store.GetPoll(req.MessageID, chat.String())
	if err != nil {
		return failure(http.StatusInternalServerError, "Error reading the poll: %v", err)
	}
	if !found {
		return failure(http.StatusNotFound, "Poll %s was not found in %s", req.MessageID, req.ChatJID)
	}
	chosen, err := chosenOptions(poll, req.Options)
	if err != nil {
		return failure(http.StatusBadRequest, "Invalid vote: %v", err)
	}
	if !env.connected() {
		return failure(http.StatusServiceUnavailable, "Not connected to WhatsApp")
	}

	author := env.resolveSender(poll.Author)
	fromMe := poll.Author == env.own.User
	if fromMe {
		author = env.own
	}
	info := &types.MessageInfo{
		MessageSource: types.MessageSource{Chat: chat, Sender: author, IsFromMe: fromMe, IsGroup: chat.Server == types.GroupServer},
		ID:            poll.ID,
	}
	vote, err := env.buildVote(info, chosen)
	if err != nil {
		return failure(http.StatusInternalServerError, "Error building the vote: %v", err)
	}
	resp, err := env.send(chat, vote)
	if err != nil {
		return failure(http.StatusInternalServerError, "Error sending the vote: %v", err)
	}

	hashes := make([]string, 0, len(chosen))
	for _, hash := range whatsmeow.HashPollOptions(chosen) {
		hashes = append(hashes, hex.EncodeToString(hash))
	}
	if _, err := store.StorePollVote(poll.ID, chat.String(), env.own.User, hashes, resp.Timestamp); err != nil {
		logger.Warnf("The vote on poll %s was sent but could not be stored: %v", poll.ID, err)
	}
	return http.StatusOK, SendMessageResponse{Success: true, Message: "Vote sent", MessageID: resp.ID, ChatJID: chat.String()}
}
