package main

import (
	"fmt"
	"strings"

	"go.mau.fi/whatsmeow/proto/waE2E"
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
