# Runs the bridge from a binary of its own, not from the go run cache.
# The bridge keeps its files in ./store, so it always runs from this directory.

BIN := bin/whatsapp-bridge
SERVICE := whatsapp-bridge.service
SERVICE_DIR ?= $(HOME)/.config/systemd/user

.PHONY: build run test install-service

build:
	go build -o $(BIN) .

run: build
	exec ./$(BIN)

test:
	go test ./...

# Writes the systemd --user unit for this directory. Then: systemctl --user enable --now whatsapp-bridge
install-service: build
	mkdir -p $(SERVICE_DIR)
	sed 's|@DIR@|$(CURDIR)|g' $(SERVICE).in > $(SERVICE_DIR)/$(SERVICE)
	@echo "Wrote $(SERVICE_DIR)/$(SERVICE)"
	@echo "Next: systemctl --user daemon-reload && systemctl --user enable --now whatsapp-bridge"
