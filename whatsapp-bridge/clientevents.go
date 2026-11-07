package main

import (
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
)

// setupWebhooks reads webhooks.json, hands the dispatcher to the store and to the status, and returns the
// function that closes it (up to 5 s to empty the queues). Without the file nothing is delivered.
func setupWebhooks(store *MessageStore, state *bridgeState, logger waLog.Logger) (closeFn func()) {
	if cfgDir, err := configDir(); err != nil {
		store.webhooks = &webhookDispatcher{now: time.Now, err: "no configuration directory for webhooks.json"}
	} else {
		store.webhooks = loadWebhooks(cfgDir, logger)
	}
	state.webhooks = store.webhooks.Status
	return func() { store.webhooks.Close(5 * time.Second) }
}

// clientEventHandler handles what the WhatsApp client reports. startIdentitySync runs in the background
// when the client connects.
func clientEventHandler(client *whatsmeow.Client, store *MessageStore, startIdentitySync func(), logger waLog.Logger) func(interface{}) {
	return func(evt interface{}) {
		switch v := evt.(type) {
		case *events.Message:
			// Learn who the sender is (LID, phone, push name) before storing the message
			if err := recordMessageIdentity(store, v.Info); err != nil {
				logger.Warnf("Failed to record sender identity: %v", err)
			}
			// Process regular messages
			handleMessage(client, store, v, logger)

		case *events.PushName:
			if err := recordPushNameEvent(store, v); err != nil {
				logger.Warnf("Failed to record push name: %v", err)
			}

		case *events.Contact:
			if err := recordContactEvent(store, v); err != nil {
				logger.Warnf("Failed to record contact: %v", err)
			}

		case *events.HistorySync:
			// Process history sync events
			handleHistorySync(client, store, v, logger)

		case *events.MediaRetry:
			// Response to a SendMediaRetryReceipt request (expired media link recovery)
			handleMediaRetryNotification(v)

		case *events.Connected:
			logger.Infof("Connected to WhatsApp")
			store.webhooks.Emit(connectionWebhookEvent("connected", time.Now()))
			go startIdentitySync()

		case *events.Disconnected:
			store.webhooks.Emit(connectionWebhookEvent("disconnected", time.Now()))

		case *events.LoggedOut:
			logger.Warnf("Device logged out, please scan QR code to log in again")
			store.webhooks.Emit(connectionWebhookEvent("logged_out", time.Now()))
		}
	}
}
