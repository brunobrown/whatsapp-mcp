package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"


	"bytes"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/proto/waMmsRetry"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

// Message represents a chat message for our client
type Message struct {
	Time      time.Time
	Sender    string
	Content   string
	IsFromMe  bool
	MediaType string
	Filename  string
}

// Database handler for storing message history
type MessageStore struct {
	db *sql.DB
}

// Initialize message store
func NewMessageStore() (*MessageStore, error) {
	cfg, err := loadDBConfig()
	if err != nil {
		return nil, err
	}
	return newMessageStore(cfg)
}

func newMessageStore(cfg dbConfig) (*MessageStore, error) {
	// Create directory for database if it doesn't exist
	if err := os.MkdirAll("store", privateDirMode); err != nil {
		return nil, fmt.Errorf("failed to create store directory: %v", err)
	}

	// Open SQLite database for messages
	db, err := openDatabase("store/"+messagesDBName, messagesDBName, "_foreign_keys=on&_busy_timeout=5000&_txlock=immediate", cfg)
	if err != nil {
		return nil, fmt.Errorf("failed to open message database: %v", err)
	}

	// Create tables if they don't exist
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS chats (
			jid TEXT PRIMARY KEY,
			name TEXT,
			last_message_time TIMESTAMP
		);
		
		CREATE TABLE IF NOT EXISTS messages (
			id TEXT,
			chat_jid TEXT,
			sender TEXT,
			content TEXT,
			timestamp TIMESTAMP,
			is_from_me BOOLEAN,
			media_type TEXT,
			filename TEXT,
			url TEXT,
			media_key BLOB,
			file_sha256 BLOB,
			file_enc_sha256 BLOB,
			file_length INTEGER,
			local_path TEXT,
			mimetype TEXT,
			forwarded BOOLEAN,
			quoted_id TEXT,
			PRIMARY KEY (id, chat_jid),
			FOREIGN KEY (chat_jid) REFERENCES chats(jid)
		);
	`)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to create tables: %v", err)
	}

	if _, err := db.Exec(identitySchema); err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to create contacts_identity: %v", err)
	}

	if _, err := db.Exec("CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_jid, timestamp)"); err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to create messages index: %v", err)
	}

	if err := ensureMessageColumns(db); err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to migrate messages table: %v", err)
	}

	// The database file now exists: keep it, and everything else in the store, private to the owner.
	reportHardening(hardenStore("store"))

	return &MessageStore{db: db}, nil
}

// reportHardening prints what hardenStore could not do. Tightening what it can is enough to carry on.
func reportHardening(result hardenResult, err error) {
	if err != nil {
		fmt.Printf("Could not restrict the permissions of store/: %v\n", err)
	}
	for _, path := range result.Failed {
		fmt.Printf("Could not restrict the permissions of %s\n", path)
	}
	if result.Changed > 0 {
		fmt.Printf("Restricted the permissions of %d path(s) in store/ to the owner\n", result.Changed)
	}
}

// Close the database connection
func (store *MessageStore) Close() error {
	return store.db.Close()
}

// Store a chat in the database
func (store *MessageStore) StoreChat(jid, name string, lastMessageTime time.Time) error {
	// An upsert that only moves last_message_time forward: INSERT OR REPLACE let a history sync
	// with an older time overwrite a newer one. Instants are compared with datetime() because
	// the stored times can carry different UTC offsets, and as text they would not order right.
	_, err := store.db.Exec(
		`INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)
		ON CONFLICT(jid) DO UPDATE SET
			name = excluded.name,
			last_message_time = CASE
				WHEN chats.last_message_time IS NULL
					OR datetime(excluded.last_message_time) > datetime(chats.last_message_time)
				THEN excluded.last_message_time ELSE chats.last_message_time END`,
		jid, name, lastMessageTime,
	)
	return err
}

// RepairChatTimes moves chats.last_message_time forward to the latest stored message of chats
// whose time is missing or older than it, and returns how many chats it changed. It never moves
// a time back (a later time is only activity) and leaves chats without messages alone.
func (store *MessageStore) RepairChatTimes() (int, error) {
	res, err := store.db.Exec(`
		UPDATE chats SET last_message_time = (
			SELECT m.timestamp FROM messages m WHERE m.chat_jid = chats.jid
			ORDER BY datetime(m.timestamp) DESC, m.rowid DESC LIMIT 1
		)
		WHERE EXISTS (
			SELECT 1 FROM messages m WHERE m.chat_jid = chats.jid
			AND (chats.last_message_time IS NULL OR datetime(m.timestamp) > datetime(chats.last_message_time))
		)`)
	if err != nil {
		return 0, err
	}
	n, err := res.RowsAffected()
	return int(n), err
}


// TouchChat records activity in a chat, creating it (named after its JID) if unknown and
// keeping the name of a known chat, which StoreChat would overwrite.
func (store *MessageStore) TouchChat(jid string, lastMessageTime time.Time) error {
	_, err := store.db.Exec(
		`INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)
		ON CONFLICT(jid) DO UPDATE SET last_message_time = CASE
			WHEN chats.last_message_time IS NULL
				OR datetime(excluded.last_message_time) > datetime(chats.last_message_time)
			THEN excluded.last_message_time ELSE chats.last_message_time END`,
		jid, jid, lastMessageTime,
	)
	return err
}

// Store a message in the database
func (store *MessageStore) StoreMessage(id, chatJID, sender, content string, timestamp time.Time, isFromMe bool,
	mediaType, filename, url string, mediaKey, fileSHA256, fileEncSHA256 []byte, fileLength uint64) error {
	// Only store if there's actual content or media
	if content == "" && mediaType == "" {
		return nil
	}

	// An upsert instead of INSERT OR REPLACE: storing a message again (echo, history sync)
	// must not wipe its local_path.
	_, err := store.db.Exec(
		`INSERT INTO messages 
		(id, chat_jid, sender, content, timestamp, is_from_me, media_type, filename, url, media_key, file_sha256, file_enc_sha256, file_length) 
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id, chat_jid) DO UPDATE SET
			sender = excluded.sender, content = excluded.content, timestamp = excluded.timestamp,
			is_from_me = excluded.is_from_me, media_type = excluded.media_type, filename = excluded.filename,
			url = excluded.url, media_key = excluded.media_key, file_sha256 = excluded.file_sha256,
			file_enc_sha256 = excluded.file_enc_sha256, file_length = excluded.file_length`,
		id, chatJID, sender, content, timestamp, isFromMe, mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength,
	)
	return err
}

// UpdateMessageContent replaces the content of a stored message and reports
// whether it was updated. Only the original sender may edit a message, so an
// edit naming someone else's message ID leaves it untouched.
func (store *MessageStore) UpdateMessageContent(id, chatJID, sender, content string) (bool, error) {
	res, err := store.db.Exec("UPDATE messages SET content = ? WHERE id = ? AND chat_jid = ? AND sender = ?", content, id, chatJID, sender)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// SetLocalPath records where the media of a message was saved.
func (store *MessageStore) SetLocalPath(id, chatJID, path string) error {
	_, err := store.db.Exec("UPDATE messages SET local_path = ? WHERE id = ? AND chat_jid = ?", path, id, chatJID)
	return err
}

// messageColumns are the columns added to messages after its first version.
var messageColumns = []struct{ name, ddl string }{
	{"local_path", "TEXT"},
	{"mimetype", "TEXT"},
	{"forwarded", "BOOLEAN"},
	{"quoted_id", "TEXT"},
}

// ensureMessageColumns adds the columns of messageColumns that a database created by an older
// version lacks. It never recreates or drops anything, and is a no-op when they are all there.
func ensureMessageColumns(db *sql.DB) error {
	rows, err := db.Query("PRAGMA table_info(messages)")
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
	for _, col := range messageColumns {
		if have[col.name] {
			continue
		}
		if _, err := db.Exec("ALTER TABLE messages ADD COLUMN " + col.name + " " + col.ddl); err != nil {
			return err
		}
	}
	return nil
}

// MessageMeta is what a message says about itself beyond its text and media bytes.
type MessageMeta struct {
	Mimetype  string
	Forwarded bool
	QuotedID  string // ID of the message this one replies to
}

// extractMessageMeta reads the MIME type, the forwarded flag and the quoted message ID of msg.
func extractMessageMeta(msg *waProto.Message) MessageMeta {
	if msg == nil {
		return MessageMeta{}
	}
	msg = unwrapDocumentWithCaption(msg)

	var meta MessageMeta
	var ctx *waProto.ContextInfo
	switch {
	case msg.GetExtendedTextMessage() != nil:
		ctx = msg.GetExtendedTextMessage().GetContextInfo()
	case msg.GetImageMessage() != nil:
		meta.Mimetype = msg.GetImageMessage().GetMimetype()
		ctx = msg.GetImageMessage().GetContextInfo()
	case msg.GetVideoMessage() != nil:
		meta.Mimetype = msg.GetVideoMessage().GetMimetype()
		ctx = msg.GetVideoMessage().GetContextInfo()
	case msg.GetAudioMessage() != nil:
		meta.Mimetype = msg.GetAudioMessage().GetMimetype()
		ctx = msg.GetAudioMessage().GetContextInfo()
	case msg.GetDocumentMessage() != nil:
		meta.Mimetype = msg.GetDocumentMessage().GetMimetype()
		ctx = msg.GetDocumentMessage().GetContextInfo()
	}
	meta.Forwarded = ctx.GetIsForwarded()
	meta.QuotedID = ctx.GetStanzaID()
	return meta
}

// SetMessageMeta records the metadata of a stored message. Empty values never replace stored
// ones, so a message that arrives again without metadata keeps what it had.
func (store *MessageStore) SetMessageMeta(id, chatJID string, meta MessageMeta) error {
	_, err := store.db.Exec(
		`UPDATE messages SET
			mimetype = COALESCE(NULLIF(?, ''), mimetype),
			quoted_id = COALESCE(NULLIF(?, ''), quoted_id),
			forwarded = CASE WHEN ? THEN 1 ELSE forwarded END
		WHERE id = ? AND chat_jid = ?`,
		meta.Mimetype, meta.QuotedID, meta.Forwarded, id, chatJID,
	)
	return err
}

// Get messages from a chat
func (store *MessageStore) GetMessages(chatJID string, limit int) ([]Message, error) {
	rows, err := store.db.Query(
		"SELECT sender, content, timestamp, is_from_me, media_type, filename FROM messages WHERE chat_jid = ? ORDER BY timestamp DESC LIMIT ?",
		chatJID, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var messages []Message
	for rows.Next() {
		var msg Message
		var timestamp time.Time
		err := rows.Scan(&msg.Sender, &msg.Content, &timestamp, &msg.IsFromMe, &msg.MediaType, &msg.Filename)
		if err != nil {
			return nil, err
		}
		msg.Time = timestamp
		messages = append(messages, msg)
	}

	return messages, nil
}

// Get all chats
func (store *MessageStore) GetChats() (map[string]time.Time, error) {
	rows, err := store.db.Query("SELECT jid, last_message_time FROM chats ORDER BY last_message_time DESC")
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	chats := make(map[string]time.Time)
	for rows.Next() {
		var jid string
		var lastMessageTime time.Time
		err := rows.Scan(&jid, &lastMessageTime)
		if err != nil {
			return nil, err
		}
		chats[jid] = lastMessageTime
	}

	return chats, nil
}

// unwrapDocumentWithCaption returns the inner message of a DocumentWithCaptionMessage.
// whatsmeow unwraps it for live events, but history sync delivers it still wrapped.
func unwrapDocumentWithCaption(msg *waProto.Message) *waProto.Message {
	if inner := msg.GetDocumentWithCaptionMessage().GetMessage(); inner != nil {
		return inner
	}
	return msg
}

// editTarget returns the ID of the message being edited when msg is an edit.
func editTarget(msg *waProto.Message) (string, bool) {
	pm := msg.GetProtocolMessage()
	if pm == nil || pm.GetType() != waProto.ProtocolMessage_MESSAGE_EDIT || pm.GetKey().GetID() == "" {
		return "", false
	}
	return pm.GetKey().GetID(), true
}

// applyEdit applies msg to the stored message it edits. isEdit reports whether
// msg is an edit at all, so the caller must not store it as a new message.
func applyEdit(store *MessageStore, chatJID, sender string, msg *waProto.Message) (isEdit, updated bool, err error) {
	targetID, ok := editTarget(msg)
	if !ok {
		return false, false, nil
	}
	updated, err = store.UpdateMessageContent(targetID, chatJID, sender, extractTextContent(msg))
	return true, updated, err
}

// historyMessageContent extracts the content of a history sync message with
// the same rules as live messages.
func historyMessageContent(msg *waProto.HistorySyncMsg) string {
	return extractTextContent(msg.GetMessage().GetMessage())
}

// Extract text content from a message: the text of plain and extended text
// messages, the caption of image, video and document messages, or the new
// text of an edit.
func extractTextContent(msg *waProto.Message) string {
	if msg == nil {
		return ""
	}
	msg = unwrapDocumentWithCaption(msg)

	if _, ok := editTarget(msg); ok {
		return extractTextContent(msg.GetProtocolMessage().GetEditedMessage())
	}

	if text := msg.GetConversation(); text != "" {
		return text
	}
	if extendedText := msg.GetExtendedTextMessage(); extendedText != nil {
		return extendedText.GetText()
	}
	if img := msg.GetImageMessage(); img != nil {
		return img.GetCaption()
	}
	if vid := msg.GetVideoMessage(); vid != nil {
		return vid.GetCaption()
	}
	if doc := msg.GetDocumentMessage(); doc != nil {
		return doc.GetCaption()
	}

	return ""
}

// SendMessageResponse represents the response for the send message API
type SendMessageResponse struct {
	Success   bool   `json:"success"`
	Message   string `json:"message"`
	MessageID string `json:"message_id,omitempty"`
	Timestamp string `json:"timestamp,omitempty"`
	ChatJID   string `json:"chat_jid,omitempty"`
}

// SendResult is the outcome of sending a message.
type SendResult struct {
	Success   bool
	Message   string
	MessageID string
	Timestamp time.Time
	ChatJID   string
}

func newSendResponse(res SendResult) SendMessageResponse {
	resp := SendMessageResponse{Success: res.Success, Message: res.Message, MessageID: res.MessageID, ChatJID: res.ChatJID}
	if !res.Timestamp.IsZero() {
		resp.Timestamp = res.Timestamp.Format(time.RFC3339)
	}
	return resp
}

// completeSend turns the outcome of client.SendMessage into a SendResult and, when the
// send succeeded, stores the message as sent by us. WhatsApp does not echo it back to
// this device, so without this the message would never show up in the database. A failure
// to store is only logged: the message is already out, and reporting an error would
// invite a duplicate send.
func completeSend(store *MessageStore, chatJID, sender string, msg *waProto.Message, resp whatsmeow.SendResponse, sendErr error, logger waLog.Logger) SendResult {
	if sendErr != nil {
		return SendResult{Message: fmt.Sprintf("Error sending message: %v", sendErr)}
	}
	if err := recordSentMessage(store, chatJID, sender, resp.ID, resp.Timestamp, msg); err != nil && logger != nil {
		logger.Warnf("Message %s was sent but could not be stored: %v", resp.ID, err)
	}
	return SendResult{Success: true, MessageID: resp.ID, Timestamp: resp.Timestamp, ChatJID: chatJID}
}

// recordSentMessage stores a message we sent. INSERT OR REPLACE on (id, chat_jid) keeps a
// single row if the echo of the same message is stored later.
func recordSentMessage(store *MessageStore, chatJID, sender, messageID string, ts time.Time, msg *waProto.Message) error {
	if err := store.TouchChat(chatJID, ts); err != nil {
		return err
	}
	content := extractTextContent(msg)
	mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength := extractMediaInfo(msg, messageID)
	if err := store.StoreMessage(messageID, chatJID, sender, content, ts, true,
		mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength); err != nil {
		return err
	}
	return store.SetMessageMeta(messageID, chatJID, extractMessageMeta(msg))
}

// SendMessageRequest represents the request body for the send message API
type SendMessageRequest struct {
	Recipient string `json:"recipient"`
	Message   string `json:"message"`
	MediaPath string `json:"media_path,omitempty"`
	// Mentions are full JIDs of the people to tag; ReplyTo is the ID of a stored message of the chat to quote.
	Mentions []string `json:"mentions,omitempty"`
	ReplyTo  string   `json:"reply_to,omitempty"`
}

// Function to send a WhatsApp message
func sendWhatsAppMessage(client *whatsmeow.Client, store *MessageStore, recipient string, message string, mediaPath string, mentions []string, replyTo string, logger waLog.Logger) SendResult {
	if !client.IsConnected() {
		return SendResult{Message: "Not connected to WhatsApp"}
	}

	// Create JID for recipient
	var recipientJID types.JID
	var err error

	// Check if recipient is a JID
	isJID := strings.Contains(recipient, "@")

	if isJID {
		// Parse the JID string
		recipientJID, err = types.ParseJID(recipient)
		if err != nil {
			return SendResult{Message: fmt.Sprintf("Error parsing JID: %v", err)}
		}
	} else {
		// Create JID from phone number
		recipientJID = types.JID{
			User:   recipient,
			Server: "s.whatsapp.net", // For personal chats
		}
	}

	msg := &waProto.Message{}

	// Check if we have media to send
	if mediaPath != "" {
		// Read media file
		mediaData, err := os.ReadFile(mediaPath)
		if err != nil {
			return SendResult{Message: fmt.Sprintf("Error reading media file: %v", err)}
		}

		// Determine media type and mime type based on file extension
		fileExt := strings.ToLower(mediaPath[strings.LastIndex(mediaPath, ".")+1:])
		var mediaType whatsmeow.MediaType
		var mimeType string

		// Handle different media types
		switch fileExt {
		// Image types
		case "jpg", "jpeg":
			mediaType = whatsmeow.MediaImage
			mimeType = "image/jpeg"
		case "png":
			mediaType = whatsmeow.MediaImage
			mimeType = "image/png"
		case "gif":
			mediaType = whatsmeow.MediaImage
			mimeType = "image/gif"
		case "webp":
			mediaType = whatsmeow.MediaImage
			mimeType = "image/webp"

		// Audio types
		case "ogg":
			mediaType = whatsmeow.MediaAudio
			mimeType = "audio/ogg; codecs=opus"

		// Video types
		case "mp4":
			mediaType = whatsmeow.MediaVideo
			mimeType = "video/mp4"
		case "avi":
			mediaType = whatsmeow.MediaVideo
			mimeType = "video/avi"
		case "mov":
			mediaType = whatsmeow.MediaVideo
			mimeType = "video/quicktime"

		// Document types (for any other file type)
		default:
			mediaType = whatsmeow.MediaDocument
			mimeType = "application/octet-stream"
		}

		// Upload media to WhatsApp servers
		resp, err := client.Upload(context.Background(), mediaData, mediaType)
		if err != nil {
			return SendResult{Message: fmt.Sprintf("Error uploading media: %v", err)}
		}

		fmt.Println("Media uploaded", resp)

		// Create the appropriate message type based on media type
		switch mediaType {
		case whatsmeow.MediaImage:
			msg.ImageMessage = &waProto.ImageMessage{
				Caption:       proto.String(message),
				Mimetype:      proto.String(mimeType),
				URL:           &resp.URL,
				DirectPath:    &resp.DirectPath,
				MediaKey:      resp.MediaKey,
				FileEncSHA256: resp.FileEncSHA256,
				FileSHA256:    resp.FileSHA256,
				FileLength:    &resp.FileLength,
			}
		case whatsmeow.MediaAudio:
			// Handle ogg audio files
			var seconds uint32 = 30 // Default fallback
			var waveform []byte = nil

			// Try to analyze the ogg file
			if strings.Contains(mimeType, "ogg") {
				analyzedSeconds, analyzedWaveform, err := analyzeOggOpus(mediaData)
				if err == nil {
					seconds = analyzedSeconds
					waveform = analyzedWaveform
				} else {
					return SendResult{Message: fmt.Sprintf("Failed to analyze Ogg Opus file: %v", err)}
				}
			} else {
				fmt.Printf("Not an Ogg Opus file: %s\n", mimeType)
			}

			msg.AudioMessage = &waProto.AudioMessage{
				Mimetype:      proto.String(mimeType),
				URL:           &resp.URL,
				DirectPath:    &resp.DirectPath,
				MediaKey:      resp.MediaKey,
				FileEncSHA256: resp.FileEncSHA256,
				FileSHA256:    resp.FileSHA256,
				FileLength:    &resp.FileLength,
				Seconds:       proto.Uint32(seconds),
				PTT:           proto.Bool(true),
				Waveform:      waveform,
			}
		case whatsmeow.MediaVideo:
			msg.VideoMessage = &waProto.VideoMessage{
				Caption:       proto.String(message),
				Mimetype:      proto.String(mimeType),
				URL:           &resp.URL,
				DirectPath:    &resp.DirectPath,
				MediaKey:      resp.MediaKey,
				FileEncSHA256: resp.FileEncSHA256,
				FileSHA256:    resp.FileSHA256,
				FileLength:    &resp.FileLength,
			}
		case whatsmeow.MediaDocument:
			msg.DocumentMessage = &waProto.DocumentMessage{
				Title:         proto.String(mediaPath[strings.LastIndex(mediaPath, "/")+1:]),
				Caption:       proto.String(message),
				Mimetype:      proto.String(mimeType),
				URL:           &resp.URL,
				DirectPath:    &resp.DirectPath,
				MediaKey:      resp.MediaKey,
				FileEncSHA256: resp.FileEncSHA256,
				FileSHA256:    resp.FileSHA256,
				FileLength:    &resp.FileLength,
			}
		}
	} else {
		built, errMsg := buildTextSend(newSendEnv(client), store, recipientJID, message, mentions, replyTo)
		if errMsg != "" {
			return SendResult{Message: errMsg}
		}
		msg = built
	}

	// Send message
	resp, err := client.SendMessage(context.Background(), recipientJID, msg)

	sender := ""
	if client.Store.ID != nil {
		sender = client.Store.ID.User
	}
	res := completeSend(store, recipientJID.String(), sender, msg, resp, err, logger)
	if res.Success {
		res.Message = fmt.Sprintf("Message sent to %s", recipient)
	}
	return res
}

// filenameUnsafeChars matches anything but a conservative filename character set, so path
// separators, ".." traversal sequences, and other filesystem-meaningful characters get stripped.
var filenameUnsafeChars = regexp.MustCompile(`[^A-Za-z0-9._-]`)

// sanitizeFilename neutralizes a filename (or filename component) that ultimately comes from a
// message sender - and is therefore untrusted - so it can never be used to escape the intended
// media directory (e.g. via "../" sequences) when building a filesystem path from it.
func sanitizeFilename(name string) string {
	name = filepath.Base(name)
	name = filenameUnsafeChars.ReplaceAllString(name, "_")
	if name == "" || name == "." || name == ".." {
		return "_"
	}
	return name
}

// Extract media info from a message. messageID is embedded in the generated filename (when
// WhatsApp doesn't supply one, e.g. images/videos/audio) so that two media messages processed
// within the same second - common in a bulk history sync - don't collide on the same filename.
func extractMediaInfo(msg *waProto.Message, messageID string) (mediaType string, filename string, url string, mediaKey []byte, fileSHA256 []byte, fileEncSHA256 []byte, fileLength uint64) {
	if msg == nil {
		return "", "", "", nil, nil, nil, 0
	}
	safeID := sanitizeFilename(messageID)
	msg = unwrapDocumentWithCaption(msg)

	// Check for image message
	if img := msg.GetImageMessage(); img != nil {
		return "image", fmt.Sprintf("image_%s.jpg", safeID),
			img.GetURL(), img.GetMediaKey(), img.GetFileSHA256(), img.GetFileEncSHA256(), img.GetFileLength()
	}

	// Check for video message
	if vid := msg.GetVideoMessage(); vid != nil {
		return "video", fmt.Sprintf("video_%s.mp4", safeID),
			vid.GetURL(), vid.GetMediaKey(), vid.GetFileSHA256(), vid.GetFileEncSHA256(), vid.GetFileLength()
	}

	// Check for audio message
	if aud := msg.GetAudioMessage(); aud != nil {
		return "audio", fmt.Sprintf("audio_%s.ogg", safeID),
			aud.GetURL(), aud.GetMediaKey(), aud.GetFileSHA256(), aud.GetFileEncSHA256(), aud.GetFileLength()
	}

	// Check for document message
	if doc := msg.GetDocumentMessage(); doc != nil {
		// The document's filename is attacker-controlled (set by whoever sent it), so it must be
		// sanitized too - not just the fallback branch.
		filename := sanitizeFilename(doc.GetFileName())
		if filename == "_" {
			filename = fmt.Sprintf("document_%s", safeID)
		}
		return "document", filename,
			doc.GetURL(), doc.GetMediaKey(), doc.GetFileSHA256(), doc.GetFileEncSHA256(), doc.GetFileLength()
	}

	return "", "", "", nil, nil, nil, 0
}

// Handle regular incoming messages with media support
func handleMessage(client *whatsmeow.Client, messageStore *MessageStore, msg *events.Message, logger waLog.Logger) {
	// Save message to database
	chatJID := msg.Info.Chat.String()
	sender := msg.Info.Sender.User

	// Get appropriate chat name (pass nil for conversation since we don't have one for regular messages)
	name := GetChatName(client, messageStore, msg.Info.Chat, chatJID, nil, sender, logger)

	// Update chat in database with the message timestamp (keeps last message time updated)
	err := messageStore.StoreChat(chatJID, name, msg.Info.Timestamp)
	if err != nil {
		logger.Warnf("Failed to store chat: %v", err)
	}

	// An edit updates the original message instead of creating a new one
	if isEdit, updated, err := applyEdit(messageStore, chatJID, sender, msg.Message); isEdit {
		if err != nil {
			logger.Warnf("Failed to apply edit: %v", err)
		} else if !updated {
			logger.Infof("Ignoring edit of unknown message, or by a sender other than its author")
		}
		return
	}

	// Extract text content
	content := extractTextContent(msg.Message)

	// Extract media info
	mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength := extractMediaInfo(msg.Message, msg.Info.ID)

	// Skip if there's no content and no media
	if content == "" && mediaType == "" {
		return
	}

	// Store message in database
	err = messageStore.StoreMessage(
		msg.Info.ID,
		chatJID,
		sender,
		content,
		msg.Info.Timestamp,
		msg.Info.IsFromMe,
		mediaType,
		filename,
		url,
		mediaKey,
		fileSHA256,
		fileEncSHA256,
		fileLength,
	)
	if err == nil {
		if metaErr := messageStore.SetMessageMeta(msg.Info.ID, chatJID, extractMessageMeta(msg.Message)); metaErr != nil {
			logger.Warnf("Failed to store metadata of %s: %v", msg.Info.ID, metaErr)
		}
	}

	if err == nil && autoDownloadCfg.allows(mediaType, fileLength) {
		waType, _ := waMediaTypeFor(mediaType)
		downloader := &MediaDownloader{
			URL:           url,
			DirectPath:    extractDirectPathFromURL(url),
			MediaKey:      mediaKey,
			FileLength:    fileLength,
			FileSHA256:    fileSHA256,
			FileEncSHA256: fileEncSHA256,
			MediaType:     waType,
		}
		download := func(f whatsmeow.File) error { return client.DownloadToFile(context.Background(), downloader, f) }
		maybeAutoDownload(messageStore, autoDownloadCfg, autoDownloadLimiter, download, err, msg.Info.IsFromMe,
			msg.Info.ID, chatJID, mediaType, filename, fileLength, logger)
	}

	if err != nil {
		logger.Warnf("Failed to store message: %v", err)
	} else {
		// Log message reception
		timestamp := msg.Info.Timestamp.Format("2006-01-02 15:04:05")
		direction := "←"
		if msg.Info.IsFromMe {
			direction = "→"
		}

		if line := messageLogLine(timestamp, direction, sender, mediaType, filename, content, includeTextInLog); line != "" {
			fmt.Println(line)
		}
	}
}

// DownloadMediaRequest represents the request body for the download media API
type DownloadMediaRequest struct {
	MessageID string `json:"message_id"`
	ChatJID   string `json:"chat_jid"`
}

// DownloadMediaResponse represents the response for the download media API
type DownloadMediaResponse struct {
	Success  bool   `json:"success"`
	Message  string `json:"message"`
	Filename string `json:"filename,omitempty"`
	Path     string `json:"path,omitempty"`
}

// Store additional media info in the database
func (store *MessageStore) StoreMediaInfo(id, chatJID, url string, mediaKey, fileSHA256, fileEncSHA256 []byte, fileLength uint64) error {
	_, err := store.db.Exec(
		"UPDATE messages SET url = ?, media_key = ?, file_sha256 = ?, file_enc_sha256 = ?, file_length = ? WHERE id = ? AND chat_jid = ?",
		url, mediaKey, fileSHA256, fileEncSHA256, fileLength, id, chatJID,
	)
	return err
}

// Get media info from the database
func (store *MessageStore) GetMediaInfo(id, chatJID string) (string, string, string, []byte, []byte, []byte, uint64, error) {
	var mediaType, filename, url string
	var mediaKey, fileSHA256, fileEncSHA256 []byte
	var fileLength uint64

	err := store.db.QueryRow(
		"SELECT media_type, filename, url, media_key, file_sha256, file_enc_sha256, file_length FROM messages WHERE id = ? AND chat_jid = ?",
		id, chatJID,
	).Scan(&mediaType, &filename, &url, &mediaKey, &fileSHA256, &fileEncSHA256, &fileLength)

	return mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength, err
}

// GetMessageSenderInfo returns who sent a message and whether it was sent by us,
// needed to build the MessageInfo required for a media retry request.
func (store *MessageStore) GetMessageSenderInfo(id, chatJID string) (sender string, isFromMe bool, err error) {
	err = store.db.QueryRow(
		"SELECT sender, is_from_me FROM messages WHERE id = ? AND chat_jid = ?",
		id, chatJID,
	).Scan(&sender, &isFromMe)
	return sender, isFromMe, err
}

// MediaDownloader implements the whatsmeow.DownloadableMessage interface
type MediaDownloader struct {
	URL           string
	DirectPath    string
	MediaKey      []byte
	FileLength    uint64
	FileSHA256    []byte
	FileEncSHA256 []byte
	MediaType     whatsmeow.MediaType
}

// GetDirectPath implements the DownloadableMessage interface
func (d *MediaDownloader) GetDirectPath() string {
	return d.DirectPath
}

// GetURL implements the DownloadableMessage interface
func (d *MediaDownloader) GetURL() string {
	return d.URL
}

// GetMediaKey implements the DownloadableMessage interface
func (d *MediaDownloader) GetMediaKey() []byte {
	return d.MediaKey
}

// GetFileLength implements the DownloadableMessage interface
func (d *MediaDownloader) GetFileLength() uint64 {
	return d.FileLength
}

// GetFileSHA256 implements the DownloadableMessage interface
func (d *MediaDownloader) GetFileSHA256() []byte {
	return d.FileSHA256
}

// GetFileEncSHA256 implements the DownloadableMessage interface
func (d *MediaDownloader) GetFileEncSHA256() []byte {
	return d.FileEncSHA256
}

// GetMediaType implements the DownloadableMessage interface
func (d *MediaDownloader) GetMediaType() whatsmeow.MediaType {
	return d.MediaType
}

// mediaRetryResult carries either the decrypted notification or the error that occurred
// while waiting for / decrypting the media retry response.
type mediaRetryResult struct {
	notif *waMmsRetry.MediaRetryNotification
	err   error
}

// mediaRetryPendingEntry tracks a media retry request awaiting a response from WhatsApp
type mediaRetryPendingEntry struct {
	mediaKey   []byte
	resultChan chan mediaRetryResult
}

var (
	mediaRetryMu      sync.Mutex
	mediaRetryPending = make(map[string]*mediaRetryPendingEntry)
)

// handleMediaRetryNotification decrypts an incoming media retry response and delivers it
// (or the error from trying to) to whichever goroutine is waiting for that message ID, if any.
func handleMediaRetryNotification(evt *events.MediaRetry) {
	mediaRetryMu.Lock()
	entry, ok := mediaRetryPending[evt.MessageID]
	mediaRetryMu.Unlock()
	if !ok {
		// Not something we requested (or we already gave up waiting for it)
		return
	}

	notif, err := whatsmeow.DecryptMediaRetryNotification(evt, entry.mediaKey)
	entry.resultChan <- mediaRetryResult{notif: notif, err: err}
}

// resolveSenderJID builds the JID to use as a message's participant/sender. The stored sender
// value only ever keeps the bare numeric ID (see jidUserPart), so the original addressing mode
// (phone number vs. WhatsApp's newer privacy-preserving LID) has to be reconstructed:
//   - A real phone number JID (with country code) is at most 13 digits.
//   - A LID (as WhatsApp currently issues them) is longer than that.
// A request built with the wrong addressing mode goes unanswered rather than erroring, which is
// otherwise indistinguishable from the phone genuinely being unreachable.
func resolveSenderJID(client *whatsmeow.Client, sender string) types.JID {
	if len(sender) > 13 {
		return types.JID{User: sender, Server: "lid"}
	}

	phoneJID := types.JID{User: sender, Server: "s.whatsapp.net"}
	if client == nil || client.Store == nil || client.Store.LIDs == nil {
		return phoneJID
	}
	if lidJID, err := client.Store.LIDs.GetLIDForPN(context.Background(), phoneJID); err == nil && !lidJID.IsEmpty() {
		return lidJID
	}
	return phoneJID
}

// retryDownloadMedia asks WhatsApp to re-upload media whose original download link has expired
// (this is the same "media retry" mechanism the official WhatsApp clients use), then retries
// the download using the fresh direct path returned by the server.
func retryDownloadMedia(client *whatsmeow.Client, chatJID, sender string, isFromMe bool, messageID string, downloader *MediaDownloader) ([]byte, error) {
	chatParsedJID, err := types.ParseJID(chatJID)
	if err != nil {
		return nil, fmt.Errorf("failed to parse chat JID for media retry: %v", err)
	}

	msgInfo := &types.MessageInfo{
		ID: messageID,
		MessageSource: types.MessageSource{
			Chat:     chatParsedJID,
			Sender:   resolveSenderJID(client, sender),
			IsFromMe: isFromMe,
			IsGroup:  chatParsedJID.Server == "g.us",
		},
	}

	resultChan := make(chan mediaRetryResult, 1)
	mediaRetryMu.Lock()
	mediaRetryPending[messageID] = &mediaRetryPendingEntry{mediaKey: downloader.MediaKey, resultChan: resultChan}
	mediaRetryMu.Unlock()
	defer func() {
		mediaRetryMu.Lock()
		delete(mediaRetryPending, messageID)
		mediaRetryMu.Unlock()
	}()

	ctx := context.Background()
	if err := client.SendMediaRetryReceipt(ctx, msgInfo, downloader.MediaKey); err != nil {
		return nil, fmt.Errorf("failed to send media retry receipt: %v", err)
	}

	select {
	case result := <-resultChan:
		if result.err != nil {
			return nil, fmt.Errorf("media retry request failed: %v", result.err)
		}
		if result.notif.GetResult() != waMmsRetry.MediaRetryNotification_SUCCESS {
			return nil, fmt.Errorf("media is no longer available (retry result: %v) - your phone may have already deleted it", result.notif.GetResult())
		}
		downloader.DirectPath = result.notif.GetDirectPath()
		return client.Download(ctx, downloader)
	case <-time.After(90 * time.Second):
		// A phone waking up from battery-saver/background restrictions to process the push and
		// answer can genuinely take a while, so this is deliberately generous.
		return nil, fmt.Errorf("timed out waiting for media retry response from WhatsApp (make sure your phone is online, unlocked, and WhatsApp is not force-stopped)")
	}
}

// Function to download media from a message
// mediaFilePath returns where the media of a message is stored: store/<chat>/<message id>/<filename>,
// creating the directory. The message ID keeps two messages with the same filename apart; the
// filename is chosen by the sender, so without it one sender could plant a file that a later
// message of someone else would be served. Both components ultimately come from other parties
// and are sanitized here (defense in depth on top of extractMediaInfo), and the result is
// confirmed to land inside the chat directory.
func mediaFilePath(chatJID, messageID, filename string) (localPath, absPath string, err error) {
	chatDir := fmt.Sprintf("store/%s", strings.ReplaceAll(chatJID, ":", "_"))
	msgDir := fmt.Sprintf("%s/%s", chatDir, mediaDirName(messageID))
	if err := os.MkdirAll(msgDir, privateDirMode); err != nil {
		return "", "", fmt.Errorf("failed to create media directory: %v", err)
	}
	localPath = fmt.Sprintf("%s/%s", msgDir, sanitizeFilename(filename))
	absPath, err = filepath.Abs(localPath)
	if err != nil {
		return "", "", fmt.Errorf("failed to get absolute path: %v", err)
	}
	absChatDir, err := filepath.Abs(chatDir)
	if err != nil {
		return "", "", fmt.Errorf("failed to get absolute chat directory path: %v", err)
	}
	if !strings.HasPrefix(absPath, absChatDir+string(os.PathSeparator)) {
		return "", "", fmt.Errorf("refusing to use media path outside chat directory: %s", absPath)
	}
	return localPath, absPath, nil
}

// mediaDirName names the directory of a message. WhatsApp IDs are already safe and used as is;
// any other ID gets a hash suffix so that two different IDs never sanitize to the same name.
func mediaDirName(messageID string) string {
	safe := sanitizeFilename(messageID)
	if safe == messageID {
		return safe
	}
	sum := sha256.Sum256([]byte(messageID))
	return fmt.Sprintf("%s-%x", safe, sum[:4])
}

// legacyFileMatches reports whether a file saved by an older version is the media of a message,
// by comparing its SHA-256 with the one in the message. Older versions named files by filename
// alone, so a file of the same name may belong to another message.
func legacyFileMatches(path string, fileSHA256 []byte) bool {
	if len(fileSHA256) != sha256.Size {
		return false
	}
	f, err := os.Open(path)
	if err != nil {
		return false
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return false
	}
	return bytes.Equal(h.Sum(nil), fileSHA256)
}

// legacyMediaFilePath is where versions before the per-message directory saved media. It is only
// read, for files the user already downloaded; nothing new is ever written there.
func legacyMediaFilePath(chatJID, filename string) string {
	return fmt.Sprintf("store/%s/%s", strings.ReplaceAll(chatJID, ":", "_"), sanitizeFilename(filename))
}

// saveAtomic writes a file through a unique temporary file in the same directory and renames
// it into place, so the final path never holds a truncated file (a crash would otherwise leave
// one that later existence checks take for a finished download).
func saveAtomic(localPath string, fill func(f *os.File) error) error {
	tmp, err := os.CreateTemp(filepath.Dir(localPath), ".part-*")
	if err != nil {
		return fmt.Errorf("failed to save media file: %v", err)
	}
	tmpName := tmp.Name()
	err = fill(tmp)
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(tmpName, localPath)
	}
	if err != nil {
		os.Remove(tmpName)
		return fmt.Errorf("failed to save media file: %v", err)
	}
	return nil
}

// storeDownloadedMedia saves downloaded media and records its path.
func storeDownloadedMedia(store *MessageStore, messageID, chatJID, absPath string, data []byte) error {
	if err := saveAtomic(absPath, func(f *os.File) error { _, err := f.Write(data); return err }); err != nil {
		return err
	}
	return store.SetLocalPath(messageID, chatJID, absPath)
}

// cappedFile refuses to grow past max bytes, so a download whose real size is larger than
// the size the sender declared is aborted instead of filling the disk.
type cappedFile struct {
	*os.File
	max int64
}

var errMediaTooLarge = fmt.Errorf("media is larger than the auto download limit")

func (c *cappedFile) Write(p []byte) (int, error) {
	pos, err := c.File.Seek(0, io.SeekCurrent)
	if err != nil {
		return 0, err
	}
	if pos+int64(len(p)) > c.max {
		return 0, errMediaTooLarge
	}
	return c.File.Write(p)
}

// ReadFrom must go through the capped Write: otherwise io.Copy would use the ReadFrom promoted
// from *os.File, which writes straight to the file and ignores the limit.
func (c *cappedFile) ReadFrom(r io.Reader) (int64, error) {
	return io.Copy(struct{ io.Writer }{c}, r)
}

func (c *cappedFile) WriteAt(p []byte, off int64) (int, error) {
	if off+int64(len(p)) > c.max {
		return 0, errMediaTooLarge
	}
	return c.File.WriteAt(p, off)
}

func (c *cappedFile) Truncate(size int64) error {
	if size > c.max {
		return errMediaTooLarge
	}
	return c.File.Truncate(size)
}

func waMediaTypeFor(mediaType string) (whatsmeow.MediaType, bool) {
	switch mediaType {
	case "image":
		return whatsmeow.MediaImage, true
	case "video":
		return whatsmeow.MediaVideo, true
	case "audio":
		return whatsmeow.MediaAudio, true
	case "document":
		return whatsmeow.MediaDocument, true
	}
	return "", false
}

func downloadMedia(client *whatsmeow.Client, messageStore *MessageStore, messageID, chatJID string) (bool, string, string, string, error) {
	// Query the database for the message
	var mediaType, filename, url string
	var mediaKey, fileSHA256, fileEncSHA256 []byte
	var fileLength uint64
	var err error

	// Get media info from the database
	mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength, err = messageStore.GetMediaInfo(messageID, chatJID)

	if err != nil {
		// Try to get basic info if extended info isn't available
		err = messageStore.db.QueryRow(
			"SELECT media_type, filename FROM messages WHERE id = ? AND chat_jid = ?",
			messageID, chatJID,
		).Scan(&mediaType, &filename)

		if err != nil {
			return false, "", "", "", fmt.Errorf("failed to find message: %v", err)
		}
	}

	// Check if this is a media message
	if mediaType == "" {
		return false, "", "", "", fmt.Errorf("not a media message")
	}

	localPath, absPath, err := mediaFilePath(chatJID, messageID, filename)
	if err != nil {
		return false, "", "", "", err
	}

	// Already downloaded (automatically or before): return the file without contacting WhatsApp.
	// A file saved by an older version sits at the legacy path, named by filename alone, so it
	// is only trusted when its content matches this message.
	existing := ""
	if _, err := os.Stat(localPath); err == nil {
		existing = localPath
	} else if legacy := legacyMediaFilePath(chatJID, filename); legacyFileMatches(legacy, fileSHA256) {
		existing = legacy
	}
	if existing != "" {
		existingAbs, _ := filepath.Abs(existing)
		if err := messageStore.SetLocalPath(messageID, chatJID, existingAbs); err != nil {
			fmt.Printf("Failed to record local_path for %s: %v\n", messageID, err)
		}
		return true, mediaType, filename, existingAbs, nil
	}

	// If we don't have all the media info we need, we can't download
	if url == "" || len(mediaKey) == 0 || len(fileSHA256) == 0 || len(fileEncSHA256) == 0 || fileLength == 0 {
		return false, "", "", "", fmt.Errorf("incomplete media information for download")
	}

	fmt.Printf("Attempting to download media for message %s in chat %s...\n", messageID, chatJID)

	// Extract direct path from URL
	directPath := extractDirectPathFromURL(url)

	// Create a downloader that implements DownloadableMessage
	waMediaType, ok := waMediaTypeFor(mediaType)
	if !ok {
		return false, "", "", "", fmt.Errorf("unsupported media type: %s", mediaType)
	}

	downloader := &MediaDownloader{
		URL:           url,
		DirectPath:    directPath,
		MediaKey:      mediaKey,
		FileLength:    fileLength,
		FileSHA256:    fileSHA256,
		FileEncSHA256: fileEncSHA256,
		MediaType:     waMediaType,
	}

	// Download the media using whatsmeow client
	mediaData, err := client.Download(context.Background(), downloader)
	if err != nil {
		// The original link is likely expired (common for old media). Ask WhatsApp to
		// re-upload it and retry, the same recovery mechanism the official clients use.
		fmt.Printf("Initial download failed for message %s (%v), requesting a fresh link via media retry...\n", messageID, err)

		sender, isFromMe, senderErr := messageStore.GetMessageSenderInfo(messageID, chatJID)
		if senderErr != nil {
			return false, "", "", "", fmt.Errorf("failed to download media: %v (could not look up sender for retry: %v)", err, senderErr)
		}

		retriedData, retryErr := retryDownloadMedia(client, chatJID, sender, isFromMe, messageID, downloader)
		if retryErr != nil {
			return false, "", "", "", fmt.Errorf("failed to download media: %v (retry also failed: %v)", err, retryErr)
		}
		mediaData = retriedData
	}

	// Save the downloaded media to file
	if err := storeDownloadedMedia(messageStore, messageID, chatJID, absPath, mediaData); err != nil {
		return false, "", "", "", err
	}
	fmt.Printf("Successfully downloaded %s media to %s (%d bytes)\n", mediaType, loggedText(absPath, includeTextInLog), len(mediaData))
	return true, mediaType, filename, absPath, nil
}

// AutoDownloadConfig controls which incoming media is saved on arrival.
type AutoDownloadConfig struct {
	Enabled  bool
	MaxBytes uint64
	Types    map[string]bool
}

const defaultAutoDownloadMaxMB = 25

// loadAutoDownloadConfig reads WHATSAPP_AUTO_DOWNLOAD (default on), WHATSAPP_AUTO_DOWNLOAD_MAX_MB
// (default 25) and WHATSAPP_AUTO_DOWNLOAD_TYPES (default image,document,audio). Invalid values
// fall back to the defaults.
func loadAutoDownloadConfig(getenv func(string) string) AutoDownloadConfig {
	cfg := AutoDownloadConfig{Enabled: true, MaxBytes: defaultAutoDownloadMaxMB << 20, Types: map[string]bool{}}

	switch strings.ToLower(strings.TrimSpace(getenv("WHATSAPP_AUTO_DOWNLOAD"))) {
	case "false", "0":
		cfg.Enabled = false
	}
	if mb, err := strconv.ParseFloat(strings.TrimSpace(getenv("WHATSAPP_AUTO_DOWNLOAD_MAX_MB")), 64); err == nil && mb > 0 {
		cfg.MaxBytes = uint64(mb * (1 << 20))
	}
	types := "image,document,audio"
	if v := strings.TrimSpace(getenv("WHATSAPP_AUTO_DOWNLOAD_TYPES")); v != "" {
		types = v
	}
	for _, t := range strings.Split(types, ",") {
		if t = strings.ToLower(strings.TrimSpace(t)); t != "" {
			cfg.Types[t] = true
		}
	}
	return cfg
}

// allows reports whether a media of this type and size should be downloaded on arrival.
// An unknown size (0) is refused because the limit cannot be checked.
func (cfg AutoDownloadConfig) allows(mediaType string, fileLength uint64) bool {
	return cfg.Enabled && cfg.Types[mediaType] && fileLength > 0 && fileLength <= cfg.MaxBytes
}

// mediaCryptoOverhead is the slack allowed over the size limit for the padding and MAC of the
// encrypted file, which is a little larger than its plaintext.
const mediaCryptoOverhead = 64

// autoDownload saves the media of a message when the config allows it and records its path.
// It returns an empty path and no error when the media is skipped. download writes into the
// file it is given; the file refuses to grow past the size limit whatever the sender declared.
func autoDownload(store *MessageStore, cfg AutoDownloadConfig, download func(whatsmeow.File) error, messageID, chatJID, mediaType, filename string, fileLength uint64) (string, error) {
	if !cfg.allows(mediaType, fileLength) {
		return "", nil
	}
	localPath, absPath, err := mediaFilePath(chatJID, messageID, filename)
	if err != nil {
		return "", err
	}
	if _, err := os.Stat(localPath); err != nil {
		err := saveAtomic(localPath, func(f *os.File) error {
			if err := download(&cappedFile{File: f, max: int64(cfg.MaxBytes) + mediaCryptoOverhead}); err != nil {
				return err
			}
			info, err := f.Stat()
			if err != nil {
				return err
			}
			if info.Size() > int64(cfg.MaxBytes) {
				return errMediaTooLarge
			}
			return nil
		})
		if err != nil {
			return "", err
		}
	}
	if err := store.SetLocalPath(messageID, chatJID, absPath); err != nil {
		return absPath, fmt.Errorf("saved media but failed to record local_path: %v", err)
	}
	return absPath, nil
}

// maybeAutoDownload starts the background download of a message that was just stored. It does
// nothing when the message could not be stored, was sent by the user (who already has the
// file) or the config skips the media. The download
// must not hold up message handling, and the limiter bounds how many run at once.
func maybeAutoDownload(store *MessageStore, cfg AutoDownloadConfig, limiter downloadLimiter, download func(whatsmeow.File) error, storeErr error, isFromMe bool,
	messageID, chatJID, mediaType, filename string, fileLength uint64, logger waLog.Logger) {
	if storeErr != nil || isFromMe || !cfg.allows(mediaType, fileLength) {
		return
	}
	go limiter.run(func() {
		if _, err := autoDownload(store, cfg, download, messageID, chatJID, mediaType, filename, fileLength); err != nil && logger != nil {
			logger.Warnf("Auto download of %s in %s failed (use download_media to retry): %v", messageID, chatJID, err)
		}
	})
}

// loadRetentionDays reads WHATSAPP_MEDIA_RETENTION_DAYS. Anything but a positive whole number
// disables the cleanup, since deleting files has to be an explicit choice.
func loadRetentionDays(getenv func(string) string) int {
	days, err := strconv.Atoi(strings.TrimSpace(getenv("WHATSAPP_MEDIA_RETENTION_DAYS")))
	if err != nil || days <= 0 {
		return 0
	}
	return days
}

// CleanupExpiredMedia deletes the downloaded file of every message older than days and clears
// its local_path, keeping the message itself. The age is the message's, not the file's, since
// the download can happen long after the message. Only paths inside store/ are ever deleted.
func (store *MessageStore) CleanupExpiredMedia(now time.Time, days int) (int, error) {
	storeRoot, err := filepath.Abs("store")
	if err != nil {
		return 0, err
	}
	cutoff := now.Add(-time.Duration(days) * 24 * time.Hour)

	type expired struct{ id, chatJID, path string }
	rows, err := store.db.Query("SELECT id, chat_jid, local_path, timestamp FROM messages WHERE local_path IS NOT NULL AND local_path != ''")
	if err != nil {
		return 0, err
	}
	var candidates []expired
	for rows.Next() {
		var e expired
		var ts time.Time
		if err := rows.Scan(&e.id, &e.chatJID, &e.path, &ts); err != nil {
			rows.Close()
			return 0, err
		}
		if ts.Before(cutoff) {
			candidates = append(candidates, e)
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}

	deleted := 0
	for _, e := range candidates {
		abs, err := filepath.Abs(e.path)
		if err != nil || !strings.HasPrefix(abs, storeRoot+string(os.PathSeparator)) {
			continue // never touch anything outside store/
		}
		if err := os.Remove(abs); err != nil && !os.IsNotExist(err) {
			continue
		} else if err == nil {
			deleted++
		}
		os.Remove(filepath.Dir(abs)) // only succeeds when the directory is empty
		if err := store.SetLocalPath(e.id, e.chatJID, ""); err != nil {
			return deleted, err
		}
	}
	return deleted, nil
}

// startMediaRetention runs the cleanup at startup and every 6 hours.
func startMediaRetention(store *MessageStore, days int, logger waLog.Logger) {
	if days <= 0 {
		return
	}
	go func() {
		for {
			if n, err := store.CleanupExpiredMedia(time.Now(), days); err != nil {
				logger.Warnf("Media retention cleanup failed: %v", err)
			} else if n > 0 {
				logger.Infof("Media retention removed %d files older than %d days", n, days)
			}
			time.Sleep(6 * time.Hour)
		}
	}()
}

// downloadLimiter bounds how many automatic downloads run at once.
type downloadLimiter chan struct{}

func newDownloadLimiter(n int) downloadLimiter { return make(downloadLimiter, n) }

func (l downloadLimiter) run(fn func()) {
	l <- struct{}{}
	defer func() { <-l }()
	fn()
}

const autoDownloadConcurrency = 4

var (
	autoDownloadCfg     = loadAutoDownloadConfig(os.Getenv)
	autoDownloadLimiter = newDownloadLimiter(autoDownloadConcurrency)
)

// Extract direct path from a WhatsApp media URL
func extractDirectPathFromURL(url string) string {
	// The direct path is typically in the URL, we need to extract it
	// Example URL: https://mmg.whatsapp.net/v/t62.7118-24/13812002_698058036224062_3424455886509161511_n.enc?ccb=11-4&oh=...

	// Find the path part after the domain
	parts := strings.SplitN(url, ".net/", 2)
	if len(parts) < 2 {
		return url // Return original URL if parsing fails
	}

	pathPart := parts[1]

	// Remove query parameters
	pathPart = strings.SplitN(pathPart, "?", 2)[0]

	// Create proper direct path format
	return "/" + pathPart
}

// Start a REST API server to expose the WhatsApp client functionality
// newRESTMux registers the REST routes of the bridge.
func newRESTMux(client *whatsmeow.Client, messageStore *MessageStore, status connectionSource, state *bridgeState, logger waLog.Logger) *http.ServeMux {
	mux := http.NewServeMux()

	// Handler for the bridge status
	mux.HandleFunc("/api/status", handleStatus(status, state, bridgeVersion()))

	// Handler for sending messages
	mux.HandleFunc("/api/send", func(w http.ResponseWriter, r *http.Request) {
		// Only allow POST requests
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		// Parse the request body
		var req SendMessageRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "Invalid request format", http.StatusBadRequest)
			return
		}

		// Validate request
		if req.Recipient == "" {
			http.Error(w, "Recipient is required", http.StatusBadRequest)
			return
		}

		if req.Message == "" && req.MediaPath == "" {
			http.Error(w, "Message or media path is required", http.StatusBadRequest)
			return
		}

		fmt.Println("Received request to send a message", loggedText(req.Message, includeTextInLog), loggedText(req.MediaPath, includeTextInLog))

		// Send the message
		result := sendWhatsAppMessage(client, messageStore, req.Recipient, req.Message, req.MediaPath, req.Mentions, req.ReplyTo, logger)
		fmt.Println("Message sent", result.Success, result.Message)
		// Set response headers
		w.Header().Set("Content-Type", "application/json")

		// Set appropriate status code
		if !result.Success {
			w.WriteHeader(http.StatusInternalServerError)
		}

		// Send response
		json.NewEncoder(w).Encode(newSendResponse(result))
	})

	// Handler for listing the participants of a group
	mux.HandleFunc("/api/group_participants", handleGroupParticipants(client, messageStore))

	// Handler for downloading media
	mux.HandleFunc("/api/download", func(w http.ResponseWriter, r *http.Request) {
		// Only allow POST requests
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		// Parse the request body
		var req DownloadMediaRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "Invalid request format", http.StatusBadRequest)
			return
		}

		// Validate request
		if req.MessageID == "" || req.ChatJID == "" {
			http.Error(w, "Message ID and Chat JID are required", http.StatusBadRequest)
			return
		}

		// Download the media
		success, mediaType, filename, path, err := downloadMedia(client, messageStore, req.MessageID, req.ChatJID)

		// Set response headers
		w.Header().Set("Content-Type", "application/json")

		// Handle download result
		if !success || err != nil {
			errMsg := "Unknown error"
			if err != nil {
				errMsg = err.Error()
			}

			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(DownloadMediaResponse{
				Success: false,
				Message: fmt.Sprintf("Failed to download media: %s", errMsg),
			})
			return
		}

		// Send successful response
		json.NewEncoder(w).Encode(DownloadMediaResponse{
			Success:  true,
			Message:  fmt.Sprintf("Successfully downloaded %s media", mediaType),
			Filename: filename,
			Path:     path,
		})
	})

	return mux
}

func startRESTServer(client *whatsmeow.Client, messageStore *MessageStore, state *bridgeState, port int, token string, logger waLog.Logger) {
	mux := newRESTMux(client, messageStore, clientSource{client}, state, logger)
	handler := newAPIGuard(token, port).wrap(mux)

	// Start the server (bound to localhost only, to avoid exposing the API on the network)
	serverAddr := fmt.Sprintf("127.0.0.1:%d", port)
	fmt.Printf("Starting REST API server on %s...\n", serverAddr)

	// Run server in a goroutine so it doesn't block
	go func() {
		if err := http.ListenAndServe(serverAddr, handler); err != nil {
			fmt.Printf("REST API server error: %v\n", err)
		}
	}()
}

func main() {
	// Nothing the bridge creates (databases, media, logs, backups) may be readable by other users.
	restrictUmask()

	if handled, code := runCommand(os.Args[1:], os.Stdout); handled {
		os.Exit(code)
	}

	// Everything printed to stdout also goes to store/bridge.log (rotating); the pairing QR code is
	// printed to the terminal only.
	terminal := os.Stdout
	if logFile, err := newRotatingFile(filepath.Join(logStoreDir, logFileName), logMaxBytes, logKeepOlder); err != nil {
		fmt.Printf("Could not open %s, logging to the terminal only: %v\n", logFileName, err)
	} else if tee, stop, err := teeStdout(logFile); err != nil {
		fmt.Printf("Could not capture the log: %v\n", err)
	} else {
		terminal = tee
		if err := logFile.ModeErr(); err != nil {
			fmt.Printf("Could not restrict %s to its owner (%v); logging anyway\n", logFileName, err)
		}
		defer logFile.Close()
		defer stop()
	}
	state := newBridgeState(time.Now)

	// Set up logger
	logger := waLog.Stdout("Client", "INFO", true)
	logger.Infof("Starting WhatsApp client...")

	// Create database connection for storing session data
	dbLog := waLog.Stdout("Database", "INFO", true)

	// Create directory for database if it doesn't exist
	if err := os.MkdirAll("store", privateDirMode); err != nil {
		logger.Errorf("Failed to create store directory: %v", err)
		return
	}

	dbCfg, err := loadDBConfig()
	if err != nil {
		logger.Errorf("Cannot set up the database encryption: %v", err)
		return
	}
	if dbCfg.Encrypted {
		logger.Infof("The databases are encrypted; the key comes from %s", dbCfg.Source)
	} else {
		logger.Warnf("%s=off: the databases are NOT encrypted", dbEncryptionEnv)
	}
	for _, warning := range dbCfg.Warnings {
		logger.Warnf("%s", warning)
	}

	sessionDB, err := openDatabase("store/"+sessionDBName, sessionDBName, "_foreign_keys=on", dbCfg)
	if err != nil {
		logger.Errorf("Failed to connect to database: %v", err)
		return
	}
	container := sqlstore.NewWithDB(sessionDB, "sqlite3", dbLog)
	if err := container.Upgrade(context.Background()); err != nil {
		logger.Errorf("Failed to connect to database: failed to upgrade database: %v", err)
		return
	}
	reportHardening(hardenStore("store")) // whatsapp.db holds the keys of the linked device

	// Get device store - This contains session information
	deviceStore, err := container.GetFirstDevice(context.Background())
	if err != nil {
		if err == sql.ErrNoRows {
			// No device exists, create one
			deviceStore = container.NewDevice()
			logger.Infof("Created new device")
		} else {
			logger.Errorf("Failed to get device: %v", err)
			return
		}
	}

	// Create client instance
	client := whatsmeow.NewClient(deviceStore, logger)
	if client == nil {
		logger.Errorf("Failed to create WhatsApp client")
		return
	}

	// Initialize message store
	messageStore, err := newMessageStore(dbCfg)
	if err != nil {
		logger.Errorf("Failed to initialize message store: %v", err)
		return
	}
	defer messageStore.Close()

	if n, err := messageStore.RepairChatTimes(); err != nil {
		logger.Warnf("Failed to repair chat times: %v", err)
	} else if n > 0 {
		logger.Infof("Repaired last_message_time of %d chats", n)
	}

	// Setup event handling for messages and history sync
	client.AddEventHandler(recordingHandler(state, func(evt interface{}) {
		switch v := evt.(type) {
		case *events.Message:
			// Learn who the sender is (LID, phone, push name) before storing the message
			if err := recordMessageIdentity(messageStore, v.Info); err != nil {
				logger.Warnf("Failed to record sender identity: %v", err)
			}
			// Process regular messages
			handleMessage(client, messageStore, v, logger)

		case *events.PushName:
			if err := recordPushNameEvent(messageStore, v); err != nil {
				logger.Warnf("Failed to record push name: %v", err)
			}

		case *events.Contact:
			if err := recordContactEvent(messageStore, v); err != nil {
				logger.Warnf("Failed to record contact: %v", err)
			}

		case *events.HistorySync:
			// Process history sync events
			handleHistorySync(client, messageStore, v, logger)

		case *events.MediaRetry:
			// Response to a SendMediaRetryReceipt request (expired media link recovery)
			handleMediaRetryNotification(v)

		case *events.Connected:
			logger.Infof("Connected to WhatsApp")
			// In the background: it reads every contact and can take a while
			go func() {
				n, err := syncIdentities(context.Background(), messageStore, whatsmeowIdentitySource{client}, logger)
				if err != nil {
					logger.Warnf("Identity sync failed: %v", err)
					return
				}
				logger.Infof("Identity sync recorded %d identities", n)
			}()

		case *events.LoggedOut:
			logger.Warnf("Device logged out, please scan QR code to log in again")
		}
	}))

	// Create channel to track connection success
	connected := make(chan bool, 1)

	// Connect to WhatsApp
	if client.Store.ID == nil {
		// No ID stored, this is a new client, need to pair with phone
		qrChan, _ := client.GetQRChannel(context.Background())
		err = client.Connect()
		if err != nil {
			logger.Errorf("Failed to connect: %v", err)
			return
		}

		// Print QR code for pairing with phone
		for evt := range qrChan {
			if evt.Event == "code" {
				showPairingQR(terminal, evt.Code)
			} else if evt.Event == "success" {
				connected <- true
				break
			}
		}

		// Wait for connection
		select {
		case <-connected:
			fmt.Println("\nSuccessfully connected and authenticated!")
		case <-time.After(3 * time.Minute):
			logger.Errorf("Timeout waiting for QR code scan")
			return
		}
	} else {
		// Already logged in, just connect
		err = client.Connect()
		if err != nil {
			logger.Errorf("Failed to connect: %v", err)
			return
		}
		connected <- true
	}

	// Wait a moment for connection to stabilize
	time.Sleep(2 * time.Second)

	if !client.IsConnected() {
		logger.Errorf("Failed to establish stable connection")
		return
	}

	fmt.Println("\n✓ Connected to WhatsApp! Type 'help' for commands.")

	// Start REST API server
	startMediaRetention(messageStore, loadRetentionDays(os.Getenv), logger)
	// The API is never started without its token: it can send messages as the account.
	apiConfigDir, err := configDir()
	if err != nil {
		logger.Errorf("Cannot start the REST API: %v", err)
		return
	}
	apiToken, err := loadOrCreateToken(apiConfigDir)
	if err != nil {
		logger.Errorf("Cannot start the REST API: %v", err)
		return
	}
	logger.Infof("The REST API token is in %s", filepath.Join(apiConfigDir, apiTokenFile))
	startRESTServer(client, messageStore, state, 8080, apiToken, logger)

	// Create a channel to keep the main goroutine alive
	exitChan := make(chan os.Signal, 1)
	signal.Notify(exitChan, syscall.SIGINT, syscall.SIGTERM)

	fmt.Println("REST server is running. Press Ctrl+C to disconnect and exit.")

	// Wait for termination signal
	<-exitChan

	fmt.Println("Disconnecting...")
	// Disconnect client
	client.Disconnect()
}

// GetChatName determines the appropriate name for a chat based on JID and other info
func GetChatName(client *whatsmeow.Client, messageStore *MessageStore, jid types.JID, chatJID string, conversation interface{}, sender string, logger waLog.Logger) string {
	// First, check if chat already exists in database with a name
	var existingName string
	err := messageStore.db.QueryRow("SELECT name FROM chats WHERE jid = ?", chatJID).Scan(&existingName)
	if err == nil && existingName != "" {
		// Chat exists with a name, use that
		logger.Infof("Using existing chat name for %s: %s", chatJID, existingName)
		return existingName
	}

	// Need to determine chat name
	var name string

	if jid.Server == "g.us" {
		// This is a group chat
		logger.Infof("Getting name for group: %s", chatJID)

		// Use conversation data if provided (from history sync)
		if conversation != nil {
			// Extract name from conversation if available
			// This uses type assertions to handle different possible types
			var displayName, convName *string
			// Try to extract the fields we care about regardless of the exact type
			v := reflect.ValueOf(conversation)
			if v.Kind() == reflect.Ptr && !v.IsNil() {
				v = v.Elem()

				// Try to find DisplayName field
				if displayNameField := v.FieldByName("DisplayName"); displayNameField.IsValid() && displayNameField.Kind() == reflect.Ptr && !displayNameField.IsNil() {
					dn := displayNameField.Elem().String()
					displayName = &dn
				}

				// Try to find Name field
				if nameField := v.FieldByName("Name"); nameField.IsValid() && nameField.Kind() == reflect.Ptr && !nameField.IsNil() {
					n := nameField.Elem().String()
					convName = &n
				}
			}

			// Use the name we found
			if displayName != nil && *displayName != "" {
				name = *displayName
			} else if convName != nil && *convName != "" {
				name = *convName
			}
		}

		// If we didn't get a name, try group info
		if name == "" {
			groupInfo, err := client.GetGroupInfo(context.Background(), jid)
			if err == nil && groupInfo.Name != "" {
				name = groupInfo.Name
			} else {
				// Fallback name for groups
				name = fmt.Sprintf("Group %s", jid.User)
			}
		}

		logger.Infof("Using group name: %s", name)
	} else {
		// This is an individual contact
		logger.Infof("Getting name for contact: %s", chatJID)

		// Just use contact info (full name)
		contact, err := client.Store.Contacts.GetContact(context.Background(), jid)
		if err == nil && contact.FullName != "" {
			name = contact.FullName
		} else if sender != "" {
			// Fallback to sender
			name = sender
		} else {
			// Last fallback to JID
			name = jid.User
		}

		logger.Infof("Using contact name: %s", name)
	}

	return name
}

// jidUserPart extracts just the user portion (e.g. phone number or LID) from a raw JID string,
// stripping the "@server" suffix so it matches the convention used for stored sender values.
func jidUserPart(rawJID string) string {
	parsed, err := types.ParseJID(rawJID)
	if err != nil {
		return rawJID
	}
	return parsed.User
}

// Handle history sync events
func handleHistorySync(client *whatsmeow.Client, messageStore *MessageStore, historySync *events.HistorySync, logger waLog.Logger) {
	fmt.Printf("Received history sync event with %d conversations\n", len(historySync.Data.Conversations))

	syncedCount := 0
	for _, conversation := range historySync.Data.Conversations {
		// Parse JID from the conversation
		if conversation.ID == nil {
			continue
		}

		chatJID := *conversation.ID

		// Try to parse the JID
		jid, err := types.ParseJID(chatJID)
		if err != nil {
			logger.Warnf("Failed to parse JID %s: %v", chatJID, err)
			continue
		}

		// Get appropriate chat name by passing the history sync conversation directly
		name := GetChatName(client, messageStore, jid, chatJID, conversation, "", logger)

		// Process messages
		messages := conversation.Messages
		if len(messages) > 0 {
			// Update chat with latest message timestamp
			latestMsg := messages[0]
			if latestMsg == nil || latestMsg.Message == nil {
				continue
			}

			// Get timestamp from message info
			timestamp := time.Time{}
			if ts := latestMsg.Message.GetMessageTimestamp(); ts != 0 {
				timestamp = time.Unix(int64(ts), 0)
			} else {
				continue
			}

			messageStore.StoreChat(chatJID, name, timestamp)

			// Store messages
			for _, msg := range messages {
				if msg == nil || msg.Message == nil {
					continue
				}

				// Extract text content (including media captions)
				content := historyMessageContent(msg)

				// Message ID (needed up front so extractMediaInfo can build a unique filename)
				msgID := ""
				if msg.Message.Key != nil && msg.Message.Key.ID != nil {
					msgID = *msg.Message.Key.ID
				}

				// Extract media info
				var mediaType, filename, url string
				var mediaKey, fileSHA256, fileEncSHA256 []byte
				var fileLength uint64

				if msg.Message.Message != nil {
					mediaType, filename, url, mediaKey, fileSHA256, fileEncSHA256, fileLength = extractMediaInfo(msg.Message.Message, msgID)
				}

				// Log the message content for debugging
				logger.Infof("Message content: %v, Media Type: %v", loggedText(content, includeTextInLog), mediaType)

				// Skip messages with no content and no media
				if content == "" && mediaType == "" {
					continue
				}

				// Determine sender
				var sender string
				isFromMe := false
				if msg.Message.Key != nil && msg.Message.Key.FromMe != nil {
					isFromMe = *msg.Message.Key.FromMe
				}

				switch {
				case isFromMe:
					sender = client.Store.ID.User
				case msg.Message.Key != nil && msg.Message.Key.Participant != nil && *msg.Message.Key.Participant != "":
					// Present on real-time/group messages
					sender = jidUserPart(*msg.Message.Key.Participant)
				case msg.Message.Participant != nil && *msg.Message.Participant != "":
					// History-synced group messages carry the sender here instead of in Key
					sender = jidUserPart(*msg.Message.Participant)
				default:
					// Not a group message (or truly no sender info available)
					sender = jid.User
				}

				// Get message timestamp
				timestamp := time.Time{}
				if ts := msg.Message.GetMessageTimestamp(); ts != 0 {
					timestamp = time.Unix(int64(ts), 0)
				} else {
					continue
				}

				err = messageStore.StoreMessage(
					msgID,
					chatJID,
					sender,
					content,
					timestamp,
					isFromMe,
					mediaType,
					filename,
					url,
					mediaKey,
					fileSHA256,
					fileEncSHA256,
					fileLength,
				)
				if err != nil {
					logger.Warnf("Failed to store history message: %v", err)
				} else {
					if metaErr := messageStore.SetMessageMeta(msgID, chatJID, extractMessageMeta(msg.Message.Message)); metaErr != nil {
						logger.Warnf("Failed to store metadata of %s: %v", msgID, metaErr)
					}
					syncedCount++
					// Log successful message storage
					logger.Infof("Stored message: %s -> %s", messageLogLine(
						timestamp.Format("2006-01-02 15:04:05"), "", sender, mediaType, filename, content, includeTextInLog), chatJID)
				}
			}
		}
	}

	fmt.Printf("History sync complete. Stored %d messages.\n", syncedCount)
}

// Request history sync from the server
func requestHistorySync(client *whatsmeow.Client) {
	if client == nil {
		fmt.Println("Client is not initialized. Cannot request history sync.")
		return
	}

	if !client.IsConnected() {
		fmt.Println("Client is not connected. Please ensure you are connected to WhatsApp first.")
		return
	}

	if client.Store.ID == nil {
		fmt.Println("Client is not logged in. Please scan the QR code first.")
		return
	}

	// Build and send a history sync request
	historyMsg := client.BuildHistorySyncRequest(nil, 100)
	if historyMsg == nil {
		fmt.Println("Failed to build history sync request.")
		return
	}

	_, err := client.SendMessage(context.Background(), types.JID{
		Server: "s.whatsapp.net",
		User:   "status",
	}, historyMsg)

	if err != nil {
		fmt.Printf("Failed to request history sync: %v\n", err)
	} else {
		fmt.Println("History sync requested. Waiting for server response...")
	}
}

// analyzeOggOpus tries to extract duration and generate a simple waveform from an Ogg Opus file
func analyzeOggOpus(data []byte) (duration uint32, waveform []byte, err error) {
	// Try to detect if this is a valid Ogg file by checking for the "OggS" signature
	// at the beginning of the file
	if len(data) < 4 || string(data[0:4]) != "OggS" {
		return 0, nil, fmt.Errorf("not a valid Ogg file (missing OggS signature)")
	}

	// Parse Ogg pages to find the last page with a valid granule position
	var lastGranule uint64
	var sampleRate uint32 = 48000 // Default Opus sample rate
	var preSkip uint16 = 0
	var foundOpusHead bool

	// Scan through the file looking for Ogg pages
	for i := 0; i < len(data); {
		// Check if we have enough data to read Ogg page header
		if i+27 >= len(data) {
			break
		}

		// Verify Ogg page signature
		if string(data[i:i+4]) != "OggS" {
			// Skip until next potential page
			i++
			continue
		}

		// Extract header fields
		granulePos := binary.LittleEndian.Uint64(data[i+6 : i+14])
		pageSeqNum := binary.LittleEndian.Uint32(data[i+18 : i+22])
		numSegments := int(data[i+26])

		// Extract segment table
		if i+27+numSegments >= len(data) {
			break
		}
		segmentTable := data[i+27 : i+27+numSegments]

		// Calculate page size
		pageSize := 27 + numSegments
		for _, segLen := range segmentTable {
			pageSize += int(segLen)
		}

		// Check if we're looking at an OpusHead packet (should be in first few pages)
		if !foundOpusHead && pageSeqNum <= 1 {
			// Look for "OpusHead" marker in this page
			pageData := data[i : i+pageSize]
			headPos := bytes.Index(pageData, []byte("OpusHead"))
			if headPos >= 0 && headPos+12 < len(pageData) {
				// Found OpusHead, extract sample rate and pre-skip
				// OpusHead format: Magic(8) + Version(1) + Channels(1) + PreSkip(2) + SampleRate(4) + ...
				headPos += 8 // Skip "OpusHead" marker
				// PreSkip is 2 bytes at offset 10
				if headPos+12 <= len(pageData) {
					preSkip = binary.LittleEndian.Uint16(pageData[headPos+10 : headPos+12])
					sampleRate = binary.LittleEndian.Uint32(pageData[headPos+12 : headPos+16])
					foundOpusHead = true
					fmt.Printf("Found OpusHead: sampleRate=%d, preSkip=%d\n", sampleRate, preSkip)
				}
			}
		}

		// Keep track of last valid granule position
		if granulePos != 0 {
			lastGranule = granulePos
		}

		// Move to next page
		i += pageSize
	}

	if !foundOpusHead {
		fmt.Println("Warning: OpusHead not found, using default values")
	}

	// Calculate duration based on granule position
	if lastGranule > 0 {
		// Formula for duration: (lastGranule - preSkip) / sampleRate
		durationSeconds := float64(lastGranule-uint64(preSkip)) / float64(sampleRate)
		duration = uint32(math.Ceil(durationSeconds))
		fmt.Printf("Calculated Opus duration from granule: %f seconds (lastGranule=%d)\n",
			durationSeconds, lastGranule)
	} else {
		// Fallback to rough estimation if granule position not found
		fmt.Println("Warning: No valid granule position found, using estimation")
		durationEstimate := float64(len(data)) / 2000.0 // Very rough approximation
		duration = uint32(durationEstimate)
	}

	// Make sure we have a reasonable duration (at least 1 second, at most 300 seconds)
	if duration < 1 {
		duration = 1
	} else if duration > 300 {
		duration = 300
	}

	// Generate waveform
	waveform = placeholderWaveform(duration)

	fmt.Printf("Ogg Opus analysis: size=%d bytes, calculated duration=%d sec, waveform=%d bytes\n",
		len(data), duration, len(waveform))

	return duration, waveform, nil
}

// min returns the smaller of x or y
func min(x, y int) int {
	if x < y {
		return x
	}
	return y
}

// placeholderWaveform generates a synthetic waveform for WhatsApp voice messages
// that appears natural with some variability based on the duration
func placeholderWaveform(duration uint32) []byte {
	// WhatsApp expects a 64-byte waveform for voice messages
	const waveformLength = 64
	waveform := make([]byte, waveformLength)

	// Seed the random number generator for consistent results with the same duration
	rand.Seed(int64(duration))

	// Create a more natural looking waveform with some patterns and variability
	// rather than completely random values

	// Base amplitude and frequency - longer messages get faster frequency
	baseAmplitude := 35.0
	frequencyFactor := float64(min(int(duration), 120)) / 30.0

	for i := range waveform {
		// Position in the waveform (normalized 0-1)
		pos := float64(i) / float64(waveformLength)

		// Create a wave pattern with some randomness
		// Use multiple sine waves of different frequencies for more natural look
		val := baseAmplitude * math.Sin(pos*math.Pi*frequencyFactor*8)
		val += (baseAmplitude / 2) * math.Sin(pos*math.Pi*frequencyFactor*16)

		// Add some randomness to make it look more natural
		val += (rand.Float64() - 0.5) * 15

		// Add some fade-in and fade-out effects
		fadeInOut := math.Sin(pos * math.Pi)
		val = val * (0.7 + 0.3*fadeInOut)

		// Center around 50 (typical voice baseline)
		val = val + 50

		// Ensure values stay within WhatsApp's expected range (0-100)
		if val < 0 {
			val = 0
		} else if val > 100 {
			val = 100
		}

		waveform[i] = byte(val)
	}

	return waveform
}
