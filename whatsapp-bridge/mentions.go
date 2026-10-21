package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"google.golang.org/protobuf/proto"
)

// QuoteInfo is the message a reply quotes.
type QuoteInfo struct {
	ID          string
	Participant types.JID // author of the quoted message
	Text        string
}

// QuotedRef is a stored message that can be quoted.
type QuotedRef struct {
	ID       string
	Sender   string // user part, as stored
	Content  string
	IsFromMe bool
}

// buildTextMessage makes a plain Conversation, or an ExtendedTextMessage with ContextInfo when the
// message mentions people or quotes another. The text is never altered.
func buildTextMessage(text string, mentions []types.JID, quote *QuoteInfo) *waProto.Message {
	if len(mentions) == 0 && quote == nil {
		return &waProto.Message{Conversation: proto.String(text)}
	}
	ctx := &waProto.ContextInfo{}
	for _, m := range mentions {
		ctx.MentionedJID = append(ctx.MentionedJID, m.String())
	}
	if quote != nil {
		ctx.StanzaID = proto.String(quote.ID)
		ctx.Participant = proto.String(quote.Participant.String())
		ctx.QuotedMessage = &waProto.Message{Conversation: proto.String(quote.Text)}
	}
	return &waProto.Message{ExtendedTextMessage: &waProto.ExtendedTextMessage{
		Text:        proto.String(text),
		ContextInfo: ctx,
	}}
}

// parseMentions reads mention JIDs; a malformed one is an error rather than silently dropped.
func parseMentions(raw []string) ([]types.JID, error) {
	var out []types.JID
	for _, r := range raw {
		jid, err := types.ParseJID(r)
		if err != nil || jid.User == "" {
			return nil, fmt.Errorf("invalid mention %q", r)
		}
		out = append(out, jid)
	}
	return out, nil
}

var mentionToken = regexp.MustCompile(`@(\d+)`)

// resolveGroupMentions aligns mentions with how the group addresses its members: a person given by
// phone number but listed by LID (or the reverse) is mentioned by the group's JID, and the @token of
// the text follows. People who are not in the group are left as they came.
func resolveGroupMentions(text string, mentions []types.JID, participants []types.GroupParticipant) (string, []types.JID) {
	rename := map[string]string{}
	var out []types.JID
	seen := map[string]bool{}
	for _, m := range mentions {
		resolved := m
		for _, p := range participants {
			if m.User != "" && (m.User == p.JID.User || m.User == p.PhoneNumber.User || m.User == p.LID.User) {
				resolved = p.JID
				break
			}
		}
		if resolved.User != m.User {
			rename[m.User] = resolved.User
		}
		if !seen[resolved.String()] {
			seen[resolved.String()] = true
			out = append(out, resolved)
		}
	}
	if len(rename) > 0 {
		text = mentionToken.ReplaceAllStringFunc(text, func(tok string) string {
			if to, ok := rename[tok[1:]]; ok {
				return "@" + to
			}
			return tok
		})
	}
	return text, out
}

// GetQuotedMessage finds a stored message to quote in chatJID. A direct chat may be stored under the
// other addressing (phone or LID), so for those the lookup falls back to the ID alone.
func (store *MessageStore) GetQuotedMessage(id, chatJID string) (QuotedRef, bool, error) {
	ref := QuotedRef{}
	var content *string
	err := store.db.QueryRow(
		"SELECT id, sender, content, is_from_me FROM messages WHERE id = ? AND chat_jid = ?", id, chatJID,
	).Scan(&ref.ID, &ref.Sender, &content, &ref.IsFromMe)
	if err != nil && !strings.HasSuffix(chatJID, "@g.us") {
		err = store.db.QueryRow(
			"SELECT id, sender, content, is_from_me FROM messages WHERE id = ? AND chat_jid NOT LIKE '%@g.us'", id,
		).Scan(&ref.ID, &ref.Sender, &content, &ref.IsFromMe)
	}
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return QuotedRef{}, false, nil
		}
		return QuotedRef{}, false, err
	}
	if content != nil {
		ref.Content = *content
	}
	return ref, true, nil
}

// quoteParticipant is the JID WhatsApp expects as the author of a quoted message.
func quoteParticipant(ref QuotedRef, own types.JID, resolve func(user string) types.JID) types.JID {
	if ref.IsFromMe {
		return own.ToNonAD()
	}
	return resolve(ref.Sender)
}

// sendEnv is what buildTextSend needs from the WhatsApp client, so it can be replaced in tests.
type sendEnv struct {
	groupParticipants func(group types.JID) ([]types.GroupParticipant, error)
	own               types.JID
	resolveSender     func(user string) types.JID
}

func newSendEnv(client *whatsmeow.Client) sendEnv {
	env := sendEnv{
		groupParticipants: func(group types.JID) ([]types.GroupParticipant, error) {
			info, err := client.GetGroupInfo(context.Background(), group)
			if err != nil {
				return nil, err
			}
			return info.Participants, nil
		},
		resolveSender: func(user string) types.JID { return resolveSenderJID(client, user) },
	}
	if client.Store != nil && client.Store.ID != nil {
		env.own = *client.Store.ID
	}
	return env
}

// buildTextSend makes the message for a text send, with mentions aligned to the group and the quote
// resolved from the stored messages. A non-empty error text means the send must not happen.
func buildTextSend(env sendEnv, store *MessageStore, to types.JID, text string, rawMentions []string, replyTo string) (*waProto.Message, string) {
	mentions, err := parseMentions(rawMentions)
	if err != nil {
		return nil, err.Error()
	}
	if len(mentions) > 0 && to.Server == types.GroupServer {
		if participants, err := env.groupParticipants(to); err == nil {
			text, mentions = resolveGroupMentions(text, mentions, participants)
		}
	}
	var quote *QuoteInfo
	if replyTo != "" {
		ref, ok, err := store.GetQuotedMessage(replyTo, to.String())
		if err != nil {
			return nil, fmt.Sprintf("Could not read the message to reply to: %v", err)
		}
		if !ok {
			return nil, fmt.Sprintf("Message to reply to not found in %s: %s", to, replyTo)
		}
		quote = &QuoteInfo{ID: ref.ID, Participant: quoteParticipant(ref, env.own, env.resolveSender), Text: ref.Content}
	}
	return buildTextMessage(text, mentions, quote), ""
}
