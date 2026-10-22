package main

import (
	"fmt"
	"os"
	"strings"
	"unicode/utf8"
)

// includeTextInLog is read once at start: WHATSAPP_LOG_MESSAGES=true puts the text of messages in the log,
// which is otherwise kept free of what people write.
var includeTextInLog = logMessagesEnabled()

func logMessagesEnabled() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("WHATSAPP_LOG_MESSAGES"))) {
	case "true", "1":
		return true
	}
	return false
}

// loggedText is the text itself when include is set, else only how long it is.
func loggedText(text string, include bool) string {
	if include || text == "" {
		return text
	}
	return fmt.Sprintf("<%d chars>", utf8.RuneCountInString(text))
}

// messageLogLine is the line the bridge prints for a message, or "" when there is nothing to say.
// Without include it names who, when, which direction and what kind, never what was written
// (text, caption or file name).
func messageLogLine(timestamp, direction, sender, mediaType, filename, content string, include bool) string {
	head := fmt.Sprintf("[%s] %s %s:", timestamp, direction, sender)
	if direction == "" {
		head = fmt.Sprintf("[%s] %s:", timestamp, sender)
	}
	switch {
	case mediaType != "" && include:
		return fmt.Sprintf("%s [%s: %s] %s", head, mediaType, filename, content)
	case mediaType != "":
		return fmt.Sprintf("%s [%s] %s", head, mediaType, loggedText(content, false))
	case content != "":
		return fmt.Sprintf("%s %s", head, loggedText(content, include))
	}
	return ""
}
