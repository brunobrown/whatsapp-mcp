package main

import (
	"strings"
	"testing"
)

const secretText = "the client John owes $5,000 by Friday."

func TestMessageLogLineHidesTheTextByDefault(t *testing.T) { // LOGPII-01
	line := messageLogLine("2026-10-21 10:00:00", "←", "5511999990000", "", "", secretText, false)

	if strings.Contains(line, secretText) || strings.Contains(line, "John") || strings.Contains(line, "5,000") {
		t.Errorf("the text leaked into the log line: %q", line)
	}
	for _, want := range []string{"2026-10-21 10:00:00", "←", "5511999990000", "38 chars"} {
		if !strings.Contains(line, want) {
			t.Errorf("line %q lacks %q", line, want)
		}
	}
}

func TestMessageLogLineShowsTheTextWhenAsked(t *testing.T) { // LOGPII-01
	line := messageLogLine("2026-10-21 10:00:00", "→", "5511999990000", "", "", secretText, true)

	if !strings.Contains(line, secretText) {
		t.Errorf("line %q should include the text", line)
	}
}

func TestMessageLogLineForMediaHidesCaptionAndFilename(t *testing.T) { // LOGPII-01
	hidden := messageLogLine("t", "←", "55", "document", "contract-customer-john.pdf", "secret caption", false)
	shown := messageLogLine("t", "←", "55", "document", "contract-customer-john.pdf", "secret caption", true)

	for _, leak := range []string{"contract", "john", "caption", "secret"} {
		if strings.Contains(hidden, leak) {
			t.Errorf("hidden line leaks %q: %q", leak, hidden)
		}
	}
	if !strings.Contains(hidden, "document") {
		t.Errorf("the media type should still show: %q", hidden)
	}
	if !strings.Contains(shown, "contract-customer-john.pdf") || !strings.Contains(shown, "secret caption") {
		t.Errorf("shown line lacks the details: %q", shown)
	}
}

func TestMessageLogLineIsEmptyWithNothingToLog(t *testing.T) { // LOGPII-01
	if line := messageLogLine("t", "←", "55", "", "", "", false); line != "" {
		t.Errorf("line = %q, want none", line)
	}
}

func TestLoggedTextCountsCharactersNotBytes(t *testing.T) { // LOGPII-01
	if got := loggedText("café", false); got != "<4 chars>" {
		t.Errorf("loggedText = %q, want <4 chars>", got)
	}
	if got := loggedText("café", true); got != "café" {
		t.Errorf("loggedText = %q, want the text", got)
	}
}

func TestLogMessagesSwitchReadsTheEnvironment(t *testing.T) { // LOGPII-01
	for value, want := range map[string]bool{"true": true, "TRUE": true, "1": true, "false": false, "": false, "yes": false} {
		t.Setenv("WHATSAPP_LOG_MESSAGES", value)
		if got := logMessagesEnabled(); got != want {
			t.Errorf("WHATSAPP_LOG_MESSAGES=%q -> %v, want %v", value, got, want)
		}
	}
}
