package main

import (
	"database/sql"
	"encoding/json"
	"time"
)

// pollSchema holds the polls we know and the votes we saw. A vote stores the hashes of the options
// (SHA-256 of the option name, as WhatsApp sends them) because it can arrive before its poll, and the
// reader resolves them to names.
const pollSchema = `
	CREATE TABLE IF NOT EXISTS polls (
		id TEXT,
		chat_jid TEXT,
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

// StorePoll records a poll. Storing it again (echo, history sync) updates the poll and keeps its votes.
func (store *MessageStore) StorePoll(p Poll) error {
	options, err := json.Marshal(p.Options)
	if err != nil {
		return err
	}
	_, err = store.db.Exec(
		`INSERT INTO polls (id, chat_jid, question, options_json, selectable_count, allow_add_option, hide_voter_names)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id, chat_jid) DO UPDATE SET
			question = excluded.question, options_json = excluded.options_json,
			selectable_count = excluded.selectable_count, allow_add_option = excluded.allow_add_option,
			hide_voter_names = excluded.hide_voter_names`,
		p.ID, p.ChatJID, p.Question, string(options), p.SelectableCount, p.AllowAddOption, p.HideVoterNames,
	)
	return err
}

// GetPoll returns the poll, and whether it is known.
func (store *MessageStore) GetPoll(id, chatJID string) (Poll, bool, error) {
	p := Poll{ID: id, ChatJID: chatJID}
	var options string
	err := store.db.QueryRow(
		"SELECT question, options_json, selectable_count, allow_add_option, hide_voter_names FROM polls WHERE id = ? AND chat_jid = ?",
		id, chatJID,
	).Scan(&p.Question, &options, &p.SelectableCount, &p.AllowAddOption, &p.HideVoterNames)
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
