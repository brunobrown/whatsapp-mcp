package main

import (
	"database/sql"
	"os"
	"strings"
	"time"

	waProto "go.mau.fi/whatsmeow/proto/waE2E"
	waLog "go.mau.fi/whatsmeow/util/log"
)

// deletedMarker replaces the text of a message its sender deleted for everyone.
const deletedMarker = "[message deleted]"

// revokeTarget returns the ID of the message being deleted for everyone when msg is such a request.
func revokeTarget(msg *waProto.Message) (string, bool) {
	pm := msg.GetProtocolMessage()
	if pm == nil || pm.GetType() != waProto.ProtocolMessage_REVOKE || pm.GetKey().GetID() == "" {
		return "", false
	}
	return pm.GetKey().GetID(), true
}

// keepDeletedContent reports WHATSAPP_KEEP_DELETED_CONTENT=true: keep the text and the media of a message
// that was deleted for everyone. The default respects the deletion and erases them.
func keepDeletedContent() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("WHATSAPP_KEEP_DELETED_CONTENT")), "true")
}

// MarkDeleted records that sender deleted a stored message for everyone. Only the message's own author can
// delete it: a request from anyone else (a group admin's removal cannot be told from a forged one) changes
// nothing. Unless keep is set the text, the media details, the downloaded file, the poll and its votes and the
// reactions are erased; the row stays, with deleted_at, so the history shows that something was there.
// updated is false when nothing was changed.
func (store *MessageStore) MarkDeleted(id, chatJID, sender string, at time.Time, keep bool) (updated bool, err error) {
	var author string
	var localPath, deletedAt sql.NullString
	err = store.db.QueryRow("SELECT sender, local_path, deleted_at FROM messages WHERE id = ? AND chat_jid = ?", id, chatJID).
		Scan(&author, &localPath, &deletedAt)
	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if author != sender || deletedAt.Valid && deletedAt.String != "" {
		return false, nil
	}
	stamp := at.UTC().Format(time.RFC3339)
	if keep {
		_, err = store.db.Exec("UPDATE messages SET deleted_at = ? WHERE id = ? AND chat_jid = ?", stamp, id, chatJID)
		return err == nil, err
	}

	tx, err := store.db.Begin()
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	steps := []struct {
		query string
		args  []any
	}{
		{`UPDATE messages SET content = ?, media_type = '', filename = '', url = '', media_key = NULL,
			file_sha256 = NULL, file_enc_sha256 = NULL, file_length = 0, local_path = '', mimetype = '', deleted_at = ?
			WHERE id = ? AND chat_jid = ?`, []any{deletedMarker, stamp, id, chatJID}},
		{"DELETE FROM poll_votes WHERE poll_id = ? AND chat_jid = ?", []any{id, chatJID}},
		{"DELETE FROM polls WHERE id = ? AND chat_jid = ?", []any{id, chatJID}},
		{"DELETE FROM reactions WHERE target_id = ? AND chat_jid = ?", []any{id, chatJID}},
	}
	for _, s := range steps {
		if _, err := tx.Exec(s.query, s.args...); err != nil {
			return false, err
		}
	}
	if err := tx.Commit(); err != nil {
		return false, err
	}
	if localPath.Valid && localPath.String != "" {
		if root, rootErr := storeRootDir(); rootErr == nil {
			removeStoreFile(root, localPath.String)
		}
	}
	return true, nil
}

// applyRevoke handles a deletion for everyone. isRevoke tells the caller not to store msg as a message.
func applyRevoke(store *MessageStore, chatJID, sender string, at time.Time, msg *waProto.Message, logger waLog.Logger) (isRevoke bool, target string, updated bool) {
	target, ok := revokeTarget(msg)
	if !ok {
		return false, "", false
	}
	updated, err := store.MarkDeleted(target, chatJID, sender, at, keepDeletedContent())
	if err != nil {
		logger.Warnf("Failed to apply a deletion: %v", err)
	} else if !updated {
		logger.Infof("Ignoring the deletion of an unknown or already deleted message, or by someone other than its author")
	}
	return true, target, updated
}
