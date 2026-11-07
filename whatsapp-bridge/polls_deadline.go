package main

import (
	"database/sql"
	"time"

	waLog "go.mau.fi/whatsmeow/util/log"
)

// pollColumns are the columns added to polls after its first version: the deadline, whether it is a quiz
// and its right answer, and whether the end was already announced.
var pollColumns = []struct{ name, ddl string }{
	{"ends_at_ms", "INTEGER"},
	{"quiz", "BOOLEAN"},
	{"correct_answer", "TEXT"},
	{"closed_notified", "BOOLEAN"},
}

// ensurePollColumns adds the columns an older database lacks. It never recreates or drops anything.
func ensurePollColumns(db *sql.DB) error {
	rows, err := db.Query("PRAGMA table_info(polls)")
	if err != nil {
		return err
	}
	have := map[string]bool{}
	for rows.Next() {
		var cid, notnull, pk int
		var name, ctype string
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			rows.Close()
			return err
		}
		have[name] = true
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	for _, col := range pollColumns {
		if have[col.name] {
			continue
		}
		if _, err := db.Exec("ALTER TABLE polls ADD COLUMN " + col.name + " " + col.ddl); err != nil {
			return err
		}
	}
	return nil
}

// pollClosedWebhookEvent says that a poll's deadline passed. Ids and a count only: no question, no options.
// It is never "from me": the end of a poll is not something this account did, so include_own does not hide it.
func pollClosedWebhookEvent(pollID, chatJID string, endsAt time.Time, totalVoters int) webhookEvent {
	return webhookEvent{Type: eventPollClosed, Key: chatJID + "|" + pollID, OccurredAt: endsAt, ChatJID: chatJID,
		Meta: map[string]any{"poll_id": pollID, "chat_jid": chatJID, "closed_at": endsAt.UTC().Format(time.RFC3339),
			"total_voters": totalVoters}}
}

// announceClosedPolls emits poll_closed once for each poll whose deadline has passed, and remembers it did.
// A poll that closed long ago (the bridge was off) is marked but, being stale, the dispatcher drops its event.
// It returns how many polls it announced.
func announceClosedPolls(store *MessageStore, now time.Time) (int, error) {
	rows, err := store.db.Query(
		`SELECT id, chat_jid, ends_at_ms FROM polls
		WHERE ends_at_ms > 0 AND ends_at_ms <= ? AND COALESCE(closed_notified, 0) = 0`, now.UnixMilli())
	if err != nil {
		return 0, err
	}
	type closed struct {
		id, chat string
		endsMs   int64
	}
	var due []closed
	for rows.Next() {
		var c closed
		if err := rows.Scan(&c.id, &c.chat, &c.endsMs); err != nil {
			rows.Close()
			return 0, err
		}
		due = append(due, c)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}
	for _, c := range due {
		var voters int
		if err := store.db.QueryRow("SELECT COUNT(*) FROM poll_votes WHERE poll_id = ? AND chat_jid = ? AND hashes_json != '[]'", c.id, c.chat).Scan(&voters); err != nil {
			return 0, err
		}
		if _, err := store.db.Exec("UPDATE polls SET closed_notified = 1 WHERE id = ? AND chat_jid = ?", c.id, c.chat); err != nil {
			return 0, err
		}
		store.webhooks.Emit(pollClosedWebhookEvent(c.id, c.chat, time.UnixMilli(c.endsMs), voters))
	}
	return len(due), nil
}

// startPollCloseWatcher checks for polls that reached their deadline every pollCloseInterval.
func startPollCloseWatcher(store *MessageStore, logger waLog.Logger) {
	go func() {
		for {
			if _, err := announceClosedPolls(store, time.Now()); err != nil {
				logger.Warnf("Checking poll deadlines failed: %v", err)
			}
			time.Sleep(pollCloseInterval)
		}
	}()
}

const pollCloseInterval = 15 * time.Second
