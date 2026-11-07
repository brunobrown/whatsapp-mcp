package main

import (
	"fmt"
	"strings"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"google.golang.org/protobuf/reflect/protoreflect"
)

// nonTextContent is the short text stored for messages that carry neither text nor media: a
// location, a contact or a sticker. Without it handleMessage drops them, and a chat that was active
// looks empty. The brackets and the prefix are stable so a reader can recognise them.
func nonTextContent(msg *waE2E.Message) string {
	if location := msg.GetLocationMessage(); location != nil {
		if name := location.GetName(); name != "" {
			return fmt.Sprintf("[location: %s (%.6f, %.6f)]", name, location.GetDegreesLatitude(), location.GetDegreesLongitude())
		}
		return fmt.Sprintf("[location: %.6f, %.6f]", location.GetDegreesLatitude(), location.GetDegreesLongitude())
	}
	if live := msg.GetLiveLocationMessage(); live != nil {
		if caption := live.GetCaption(); caption != "" {
			return fmt.Sprintf("[live location: %s (%.6f, %.6f)]", caption, live.GetDegreesLatitude(), live.GetDegreesLongitude())
		}
		return fmt.Sprintf("[live location: %.6f, %.6f]", live.GetDegreesLatitude(), live.GetDegreesLongitude())
	}
	if contact := msg.GetContactMessage(); contact != nil {
		return fmt.Sprintf("[contact: %s]", contact.GetDisplayName())
	}
	if list := msg.GetContactsArrayMessage(); list != nil {
		var names []string
		for _, contact := range list.GetContacts() {
			if name := contact.GetDisplayName(); name != "" {
				names = append(names, name)
			}
		}
		return fmt.Sprintf("[contacts: %s]", strings.Join(names, ", "))
	}
	if msg.GetStickerMessage() != nil {
		return "[sticker]"
	}
	return ""
}

// noTextFields are the fields of messages that never carry text on purpose (votes, reactions, protocol
// and key-distribution messages, encrypted envelopes, and the context info that rides along), so
// their absence from the database is not worth a log line.
var noTextFields = map[string]bool{
	"messageContextInfo": true, "pollUpdateMessage": true, "reactionMessage": true, "encReactionMessage": true,
	"protocolMessage": true, "senderKeyDistributionMessage": true, "secretEncryptedMessage": true,
}

// droppedFields names the populated fields of a message that produced neither text nor media, minus
// the ones known to carry none. It lists names only: the content is never logged.
func droppedFields(msg *waE2E.Message) []string {
	if msg == nil {
		return nil
	}
	var names []string
	msg.ProtoReflect().Range(func(field protoreflect.FieldDescriptor, _ protoreflect.Value) bool {
		if name := string(field.Name()); !noTextFields[name] {
			names = append(names, name)
		}
		return true
	})
	return names
}
