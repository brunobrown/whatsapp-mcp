package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
)

// webhookConfigMaxBytes bounds the file the interface sends to be checked.
const webhookConfigMaxBytes = 128 << 10

// handleValidateWebhooks checks the text of a webhooks.json with the same rules the bridge applies when it
// reads the file, so the web interface does not carry a copy of them. It writes nothing, enables nothing and
// never repeats a secret: the answer is {"ok":true} or {"ok":false,"error":"..."}.
func handleValidateWebhooks() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, webhookConfigMaxBytes))
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			http.Error(w, "The file is too large", http.StatusRequestEntityTooLarge)
			return
		}
		if err != nil {
			http.Error(w, "Could not read the request", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if _, err := parseWebhookConfig(data, defaultRESTPort); err != nil {
			json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": err.Error()})
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"ok": true})
	}
}
