# Runs the bridge from a binary of its own, not from the go run cache.
# The bridge keeps its files in ./store, so it always runs from this directory.

BIN := bin/whatsapp-bridge
SERVICE := whatsapp-bridge.service
SERVICE_DIR ?= $(HOME)/.config/systemd/user
CONFIG_DIR ?= $(if $(WHATSAPP_CONFIG_DIR),$(WHATSAPP_CONFIG_DIR),$(HOME)/.config/whatsapp-mcp)
SERVER_DIR ?= $(abspath $(CURDIR)/../whatsapp-mcp-server)

.PHONY: build run test ui install-service purge-plaintext

build:
	go build -o $(BIN) .

run: build
	exec ./$(BIN)

test:
	go test ./...

# The web interface alone, without the bridge (the bridge starts it by itself; this is for working on it).
ui:
	cd $(SERVER_DIR) && uv run python -m ui

# Writes the systemd --user unit for this directory. Then: systemctl --user enable --now whatsapp-bridge
install-service: build
	@case "$(CURDIR)$(CONFIG_DIR)$(SERVER_DIR)" in *[!A-Za-z0-9_./-]*) echo "install-service: the directories $(CURDIR) and $(CONFIG_DIR) have characters (space, & | % and others) that break the unit file; use paths of letters, digits and _ . / -" >&2; exit 1;; esac
	mkdir -p -m 700 $(CONFIG_DIR) $(CURDIR)/store
	mkdir -p $(SERVICE_DIR)
	sed -e 's|@DIR@|$(CURDIR)|g' -e 's|@CONFIG@|$(CONFIG_DIR)|g' -e 's|@SERVER@|$(SERVER_DIR)|g' $(SERVICE).in > $(SERVICE_DIR)/$(SERVICE)
	@echo "Wrote $(SERVICE_DIR)/$(SERVICE)"
	@echo "For the web interface under this unit, run once: (cd $(SERVER_DIR) && uv sync)"
	@echo "Next: systemctl --user daemon-reload && systemctl --user enable --now whatsapp-bridge"

# Overwrites with zeros and removes the plaintext copies left behind when the databases were encrypted.
purge-plaintext: build
	./$(BIN) purge-plaintext
