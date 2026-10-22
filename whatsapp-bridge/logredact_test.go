package main

import (
	"strings"
	"testing"
)

const secretText = "o cliente João deve R$ 5.000 até sexta"

func TestMessageLogLineHidesTheTextByDefault(t *testing.T) { // LOGPII-01
	line := messageLogLine("2026-10-21 10:00:00", "←", "5511999990000", "", "", secretText, false)

	if strings.Contains(line, secretText) || strings.Contains(line, "João") || strings.Contains(line, "5.000") {
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
	hidden := messageLogLine("t", "←", "55", "document", "contrato-cliente-joao.pdf", "legenda secreta", false)
	shown := messageLogLine("t", "←", "55", "document", "contrato-cliente-joao.pdf", "legenda secreta", true)

	for _, leak := range []string{"contrato", "joao", "legenda", "secreta"} {
		if strings.Contains(hidden, leak) {
			t.Errorf("hidden line leaks %q: %q", leak, hidden)
		}
	}
	if !strings.Contains(hidden, "document") {
		t.Errorf("the media type should still show: %q", hidden)
	}
	if !strings.Contains(shown, "contrato-cliente-joao.pdf") || !strings.Contains(shown, "legenda secreta") {
		t.Errorf("shown line lacks the details: %q", shown)
	}
}

func TestMessageLogLineIsEmptyWithNothingToLog(t *testing.T) { // LOGPII-01
	if line := messageLogLine("t", "←", "55", "", "", "", false); line != "" {
		t.Errorf("line = %q, want none", line)
	}
}

func TestLoggedTextCountsCharactersNotBytes(t *testing.T) { // LOGPII-01
	if got := loggedText("ação", false); got != "<4 chars>" {
		t.Errorf("loggedText = %q, want <4 chars>", got)
	}
	if got := loggedText("ação", true); got != "ação" {
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
