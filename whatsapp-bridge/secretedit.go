package main

import (
	"context"
	"strings"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

// secretDecryptor decrypts a secret-encrypted envelope (client.DecryptSecretEncryptedMessage).
type secretDecryptor func(*events.Message) (*waE2E.Message, error)

// decryptors are the two ways handleMessage reads what WhatsApp encrypts with a message's secret.
type decryptors struct {
	vote   pollVoteDecryptor
	secret secretDecryptor
}

func clientDecryptors(client *whatsmeow.Client) decryptors {
	return decryptors{
		vote: func(e *events.Message) (*waE2E.PollVoteMessage, error) {
			return client.DecryptPollVote(context.Background(), e)
		},
		secret: func(e *events.Message) (*waE2E.Message, error) {
			return client.DecryptSecretEncryptedMessage(context.Background(), e)
		},
	}
}

// EditMessage replaces the text of a stored message. Only its sender may edit it; for this account
// that is any message we sent, whatever user (phone number or LID) it was stored under.
func (store *MessageStore) EditMessage(id, chatJID, who string, fromMe bool, content string) (bool, error) {
	res, err := store.db.Exec(
		"UPDATE messages SET content = ? WHERE id = ? AND chat_jid = ? AND (sender = ? OR (? AND is_from_me = 1))",
		content, id, chatJID, who, fromMe)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// AddPollOption appends an option a participant added. It reports whether it was added: the poll must
// accept new options, have fewer than the maximum, and not already have the option (case aside).
func (store *MessageStore) AddPollOption(id, chatJID, option string) (bool, error) {
	poll, found, err := store.GetPoll(id, chatJID)
	if err != nil || !found || !poll.AllowAddOption || len(poll.Options) >= maxPollOptions {
		return false, err
	}
	option = strings.TrimSpace(option)
	if option == "" {
		return false, nil
	}
	for _, existing := range poll.Options {
		if strings.EqualFold(strings.TrimSpace(existing), option) {
			return false, nil
		}
	}
	poll.Options = append(poll.Options, option)
	return store.updatePollOptions(poll)
}

func (store *MessageStore) updatePollOptions(poll Poll) (bool, error) {
	encoded, err := jsonOptions(poll.Options)
	if err != nil {
		return false, err
	}
	res, err := store.db.Exec("UPDATE polls SET options_json = ? WHERE id = ? AND chat_jid = ?", encoded, poll.ID, poll.ChatJID)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// EditPollQuestion changes the question of a poll, and the text of its message, only for the author.
func (store *MessageStore) EditPollQuestion(id, chatJID, author, question string) (bool, error) {
	tx, err := store.db.Begin()
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	res, err := tx.Exec("UPDATE polls SET question = ? WHERE id = ? AND chat_jid = ? AND author = ?", question, id, chatJID, author)
	if err != nil {
		return false, err
	}
	if n, err := res.RowsAffected(); err != nil || n == 0 {
		return false, err
	}
	if _, err := tx.Exec("UPDATE messages SET content = ? WHERE id = ? AND chat_jid = ?", pollContentPrefix+question, id, chatJID); err != nil {
		return false, err
	}
	return true, tx.Commit()
}

// recordSecretEdit applies a secret-encrypted envelope: an edit of a message, an option added to a
// poll, or an edited poll question. It reports whether the message was such an envelope, which is never
// stored as a message of its own. What cannot be decrypted or understood is only logged.
func recordSecretEdit(store *MessageStore, own string, decrypt secretDecryptor, evt *events.Message, logger waLog.Logger) bool {
	envelope := evt.Message.GetSecretEncryptedMessage()
	if envelope == nil {
		return false
	}
	chatJID := evt.Info.Chat.String()
	target := envelope.GetTargetMessageKey().GetID()
	who := evt.Info.Sender.User
	if evt.Info.IsFromMe && own != "" {
		who = own
	}

	inner, err := decrypt(evt)
	if err != nil {
		logger.Warnf("Could not read the edit of %s: %v", target, err)
		return true
	}

	switch envelope.GetSecretEncType() {
	case waE2E.SecretEncryptedMessage_MESSAGE_EDIT:
		text := extractTextContent(inner)
		if text == "" {
			logger.Warnf("The edit of %s carries no text", target)
			return true
		}
		if updated, err := store.EditMessage(target, chatJID, who, evt.Info.IsFromMe, text); err != nil {
			logger.Warnf("Failed to apply the edit of %s: %v", target, err)
		} else if !updated {
			logger.Infof("Ignoring the edit of %s: unknown message, or not by its sender", target)
		}
	case waE2E.SecretEncryptedMessage_POLL_ADD_OPTION:
		name := inner.GetPollAddOptionMessage().GetAddOption().GetOptionName()
		if added, err := store.AddPollOption(target, chatJID, name); err != nil {
			logger.Warnf("Failed to add an option to poll %s: %v", target, err)
		} else if !added {
			logger.Infof("Ignoring the option added to poll %s: unknown poll, not accepted, repeated or full", target)
		}
	case waE2E.SecretEncryptedMessage_POLL_EDIT:
		question := pollCreationOf(inner).GetName()
		if question == "" {
			logger.Warnf("The edit of poll %s carries no question", target)
			return true
		}
		if updated, err := store.EditPollQuestion(target, chatJID, who, question); err != nil {
			logger.Warnf("Failed to edit poll %s: %v", target, err)
		} else if !updated {
			logger.Infof("Ignoring the edit of poll %s: unknown poll, or not by its author", target)
		}
	default:
		logger.Infof("Ignoring an encrypted update of kind %s for %s", envelope.GetSecretEncType(), target)
	}
	return true
}
