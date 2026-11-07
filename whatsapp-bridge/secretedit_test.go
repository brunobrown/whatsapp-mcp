package main

import (
	"errors"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

func envelope(kind waE2E.SecretEncryptedMessage_SecretEncType, target string) *waE2E.Message {
	return &waE2E.Message{SecretEncryptedMessage: &waE2E.SecretEncryptedMessage{
		SecretEncType:    kind.Enum(),
		TargetMessageKey: &waCommon.MessageKey{ID: proto.String(target)},
	}}
}

func decryptedAs(inner *waE2E.Message) secretDecryptor {
	return func(*events.Message) (*waE2E.Message, error) { return inner, nil }
}

func secretEvent(sender, id string, msg *waE2E.Message) *events.Message {
	return pollEvent("chat@g.us", sender, id, msg, time.Date(2026, 11, 6, 12, 5, 0, 0, time.UTC))
}

const alice = "5511999990000@s.whatsapp.net"

func seedText(t *testing.T, store *MessageStore, id, sender, content string, fromMe bool) {
	t.Helper()
	ts := time.Date(2026, 11, 6, 12, 0, 0, 0, time.UTC)
	store.StoreChat("chat@g.us", "Chat", ts)
	if err := store.StoreMessage(id, "chat@g.us", sender, content, ts, fromMe, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}
}

func TestEncryptedEditReplacesTheTextOfTheOriginalSendersMessage(t *testing.T) { // ED-01
	store := newTestStore(t)
	seedText(t, store, "ORIG", "5511999990000", "texto velho", false)
	inner := &waE2E.Message{Conversation: proto.String("texto novo")}

	handled := recordSecretEdit(store, "", decryptedAs(inner), secretEvent(alice, "ENV1", envelope(waE2E.SecretEncryptedMessage_MESSAGE_EDIT, "ORIG")), waLog.Noop)

	if !handled {
		t.Error("an encrypted envelope must be reported as handled so it is not stored as a message")
	}
	if got := readRow(t, store, "ORIG").content; got != "texto novo" {
		t.Errorf("content = %q, want the edited text", got)
	}
}

func TestEncryptedEditByAnotherPersonChangesNothing(t *testing.T) { // ED-01
	store := newTestStore(t)
	seedText(t, store, "ORIG", "5511999990000", "texto velho", false)
	inner := &waE2E.Message{Conversation: proto.String("texto trocado")}

	recordSecretEdit(store, "", decryptedAs(inner), secretEvent("5511888880000@s.whatsapp.net", "ENV1", envelope(waE2E.SecretEncryptedMessage_MESSAGE_EDIT, "ORIG")), waLog.Noop)

	if got := readRow(t, store, "ORIG").content; got != "texto velho" {
		t.Errorf("content = %q, want the original untouched", got)
	}
}

func TestEncryptedEditFromThisAccountFindsOurMessageWhateverTheStoredSender(t *testing.T) { // ED-01
	store := newTestStore(t)
	seedText(t, store, "MINE", "99887766", "meu velho", true) // stored under a LID user
	inner := &waE2E.Message{Conversation: proto.String("meu novo")}
	evt := secretEvent("99887766@lid", "ENV1", envelope(waE2E.SecretEncryptedMessage_MESSAGE_EDIT, "MINE"))
	evt.Info.IsFromMe = true

	recordSecretEdit(store, "5511900000000", decryptedAs(inner), evt, waLog.Noop)

	if got := readRow(t, store, "MINE").content; got != "meu novo" {
		t.Errorf("content = %q, want our edit applied", got)
	}
}

func TestUndecryptableOrEmptyEditOnlyWarns(t *testing.T) { // ED-04
	for name, decrypt := range map[string]secretDecryptor{
		"decrypt fails": func(*events.Message) (*waE2E.Message, error) { return nil, errors.New("no message secret") },
		"empty payload": decryptedAs(&waE2E.Message{}),
		"nil payload":   decryptedAs(nil),
	} {
		store := newTestStore(t)
		seedText(t, store, "ORIG", "5511999990000", "texto velho", false)
		logger := &recordingLogger{}

		handled := recordSecretEdit(store, "", decrypt, secretEvent(alice, "ENV1", envelope(waE2E.SecretEncryptedMessage_MESSAGE_EDIT, "ORIG")), logger)

		if !handled || readRow(t, store, "ORIG").content != "texto velho" {
			t.Errorf("%s: handled=%v content=%q, want handled and unchanged", name, handled, readRow(t, store, "ORIG").content)
		}
		if len(logger.warnings) != 1 || !strings.Contains(logger.warnings[0], "ORIG") {
			t.Errorf("%s: warnings = %q, want one naming the target", name, logger.warnings)
		}
	}
}

func TestAMessageThatIsNotAnEnvelopeIsNotHandled(t *testing.T) { // ED-01
	store := newTestStore(t)
	if recordSecretEdit(store, "", nil, secretEvent(alice, "T", &waE2E.Message{Conversation: proto.String("oi")}), waLog.Noop) {
		t.Error("an ordinary message must fall through to the normal handling")
	}
}

func addOption(name string) *waE2E.Message {
	return &waE2E.Message{PollAddOptionMessage: &waE2E.PollAddOptionMessage{
		AddOption: &waE2E.PollCreationMessage_Option{OptionName: proto.String(name)}}}
}

func pollWithOptions(t *testing.T, store *MessageStore, allowAdd bool, options ...string) {
	t.Helper()
	seedText(t, store, "POLL1", "5511999990000", "[poll] Almoço?", false)
	if _, err := store.StorePoll(Poll{ID: "POLL1", ChatJID: "chat@g.us", Author: "5511999990000", Question: "Almoço?",
		Options: options, AllowAddOption: allowAdd}); err != nil {
		t.Fatal(err)
	}
}

func optionsOf(t *testing.T, store *MessageStore) []string {
	t.Helper()
	poll, found, err := store.GetPoll("POLL1", "chat@g.us")
	if err != nil || !found {
		t.Fatalf("GetPoll = (%v, %v)", found, err)
	}
	return poll.Options
}

func TestAddedOptionIsAppendedToThePoll(t *testing.T) { // ED-02
	store := newTestStore(t)
	pollWithOptions(t, store, true, "Sim", "Não")

	recordSecretEdit(store, "", decryptedAs(addOption("Talvez")), secretEvent("5511777770000@s.whatsapp.net", "ENV1", envelope(waE2E.SecretEncryptedMessage_POLL_ADD_OPTION, "POLL1")), waLog.Noop)

	if got := strings.Join(optionsOf(t, store), "|"); got != "Sim|Não|Talvez" {
		t.Errorf("options = %q, want Sim|Não|Talvez (anyone may add, at the end)", got)
	}
}

func TestAddedOptionIsRefusedWhenItShouldNotBe(t *testing.T) { // ED-02
	twelve := []string{"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"}
	for name, c := range map[string]struct {
		allow   bool
		options []string
		added   string
	}{
		"the poll does not accept new options": {false, []string{"Sim", "Não"}, "Talvez"},
		"a repeated option":                    {true, []string{"Sim", "Não"}, "sim"},
		"a repeated option with spaces":        {true, []string{"Sim", "Não"}, " NÃO "},
		"the poll already has 12":              {true, twelve, "13"},
		"an empty option":                      {true, []string{"Sim", "Não"}, "  "},
	} {
		store := newTestStore(t)
		pollWithOptions(t, store, c.allow, c.options...)

		recordSecretEdit(store, "", decryptedAs(addOption(c.added)), secretEvent(alice, "ENV1", envelope(waE2E.SecretEncryptedMessage_POLL_ADD_OPTION, "POLL1")), waLog.Noop)

		if got := optionsOf(t, store); len(got) != len(c.options) {
			t.Errorf("%s: options = %v, want them unchanged", name, got)
		}
	}
}

func TestAddedOptionToAnUnknownPollChangesNothing(t *testing.T) { // ED-02, ED-04
	store := newTestStore(t)
	logger := &recordingLogger{}

	handled := recordSecretEdit(store, "", decryptedAs(addOption("Talvez")), secretEvent(alice, "ENV1", envelope(waE2E.SecretEncryptedMessage_POLL_ADD_OPTION, "SEM-ENQUETE")), logger)

	if !handled {
		t.Error("the envelope must still be reported as handled")
	}
	if _, found, _ := store.GetPoll("SEM-ENQUETE", "chat@g.us"); found {
		t.Error("an option for an unknown poll must not create one")
	}
}

func editedPoll(question string) *waE2E.Message {
	return creationMessage(question, []string{"Sim", "Não"}, 0)
}

func TestPollQuestionEditedByItsAuthor(t *testing.T) { // ED-03
	store := newTestStore(t)
	pollWithOptions(t, store, false, "Sim", "Não")

	recordSecretEdit(store, "", decryptedAs(editedPoll("Almoço hoje?")), secretEvent(alice, "ENV1", envelope(waE2E.SecretEncryptedMessage_POLL_EDIT, "POLL1")), waLog.Noop)

	poll, _, _ := store.GetPoll("POLL1", "chat@g.us")
	if poll.Question != "Almoço hoje?" {
		t.Errorf("question = %q, want the edited one", poll.Question)
	}
	if got := readRow(t, store, "POLL1").content; got != "[poll] Almoço hoje?" {
		t.Errorf("message content = %q, want [poll] Almoço hoje?", got)
	}
	if len(poll.Options) != 2 {
		t.Errorf("options = %v, want them untouched", poll.Options)
	}
}

func TestPollQuestionEditedBySomeoneElseOrWithoutOneChangesNothing(t *testing.T) { // ED-03, ED-04
	for name, c := range map[string]struct {
		sender string
		inner  *waE2E.Message
	}{
		"someone else": {"5511888880000@s.whatsapp.net", editedPoll("Pergunta trocada")},
		"no question":  {alice, &waE2E.Message{Conversation: proto.String("x")}},
	} {
		store := newTestStore(t)
		pollWithOptions(t, store, false, "Sim", "Não")

		recordSecretEdit(store, "", decryptedAs(c.inner), secretEvent(c.sender, "ENV1", envelope(waE2E.SecretEncryptedMessage_POLL_EDIT, "POLL1")), waLog.Noop)

		poll, _, _ := store.GetPoll("POLL1", "chat@g.us")
		if poll.Question != "Almoço?" || readRow(t, store, "POLL1").content != "[poll] Almoço?" {
			t.Errorf("%s: question %q, content %q, want the original", name, poll.Question, readRow(t, store, "POLL1").content)
		}
	}
}

func TestEnvelopeKindsWeDoNotApplyAreHandledAndIgnored(t *testing.T) { // ED-04
	store := newTestStore(t)
	logger := &recordingLogger{}
	handled := recordSecretEdit(store, "", decryptedAs(&waE2E.Message{}), secretEvent(alice, "ENV1", envelope(waE2E.SecretEncryptedMessage_EVENT_EDIT, "X")), logger)
	if !handled {
		t.Error("an envelope of a kind we do not apply must still not be stored as a message")
	}
}

func TestHandleMessageAppliesAnEncryptedEditAndDoesNotStoreTheEnvelope(t *testing.T) { // ED-01 (fiação)
	store := newTestStore(t)
	seedText(t, store, "ORIG", "5511999990000", "texto velho", false)
	evt := secretEvent(alice, "ENV1", envelope(waE2E.SecretEncryptedMessage_MESSAGE_EDIT, "ORIG"))
	inner := &waE2E.Message{Conversation: proto.String("texto novo")}

	handleMessageWith(decryptors{secret: decryptedAs(inner)}, "", nil, store, evt, waLog.Noop)

	if got := readRow(t, store, "ORIG").content; got != "texto novo" {
		t.Errorf("content = %q, want the edited text", got)
	}
	if messageCount(t, store, "ENV1") != 0 {
		t.Error("the envelope itself must not be stored as a message")
	}
}
