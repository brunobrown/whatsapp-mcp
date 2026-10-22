# WhatsApp MCP Server

This is a Model Context Protocol (MCP) server for WhatsApp.

With this you can search and read your personal Whatsapp messages (including images, videos, documents, and audio messages), search your contacts and send messages to either individuals or groups. You can also send media files including images, videos, documents, and audio messages.

It connects to your **personal WhatsApp account** directly via the Whatsapp web multidevice API (using the [whatsmeow](https://github.com/tulir/whatsmeow) library). All your messages are stored locally in a SQLite database and only sent to an LLM (such as Claude) when the agent accesses them through tools (which you control).

Here's an example of what you can do when it's connected to Claude.

![WhatsApp MCP](./example-use.png)

> To get updates on this and other projects I work on [enter your email here](https://docs.google.com/forms/d/1rTF9wMBTN0vPfzWuQa2BjfGKdKIpTbyeKxhPMcEzgyI/preview)

> *Caution:* as with many MCP servers, the WhatsApp MCP is subject to [the lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/). This means that project injection could lead to private data exfiltration.

## Installation

### Prerequisites

- Go
- Python 3.11+ (the MCP server reads ISO-8601 dates in all their forms and uses `zoneinfo`; `uv` picks a matching Python for you)
- Anthropic Claude Desktop app (or Cursor)
- UV (Python package manager), install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- FFmpeg (_optional_) - Only needed for audio messages. If you want to send audio files as playable WhatsApp voice messages, they must be in `.ogg` Opus format. With FFmpeg installed, the MCP server will automatically convert non-Opus audio files. Without FFmpeg, you can still send raw audio files using the `send_file` tool.

### Steps

1. **Clone this repository**

   ```bash
   git clone https://github.com/lharries/whatsapp-mcp.git
   cd whatsapp-mcp
   ```

2. **Run the WhatsApp bridge**

   Navigate to the whatsapp-bridge directory and run the Go application:

   ```bash
   cd whatsapp-bridge
   make run        # builds bin/whatsapp-bridge and runs it (or: `go run .`, never `go run main.go`: the bridge is several files)
   ```

   The first time you run it, you will be prompted to scan a QR code. Scan the QR code with your WhatsApp mobile app to authenticate.
   See [Running the bridge](#running-the-bridge) to keep it running as a service.

   After approximately 20 days, you will might need to re-authenticate.

3. **Connect to the MCP server**

   Copy the below json with the appropriate {{PATH}} values:

   ```json
   {
     "mcpServers": {
       "whatsapp": {
         "command": "{{PATH_TO_UV}}", // Run `which uv` and place the output here
         "args": [
           "--directory",
           "{{PATH_TO_SRC}}/whatsapp-mcp/whatsapp-mcp-server", // cd into the repo, run `pwd` and enter the output here + "/whatsapp-mcp-server"
           "run",
           "main.py"
         ]
       }
     }
   }
   ```

   For **Claude**, save this as `claude_desktop_config.json` in your Claude Desktop configuration directory at:

   ```
   ~/Library/Application Support/Claude/claude_desktop_config.json
   ```

   For **Cursor**, save this as `mcp.json` in your Cursor configuration directory at:

   ```
   ~/.cursor/mcp.json
   ```

4. **Restart Claude Desktop / Cursor**

   Open Claude Desktop and you should now see WhatsApp as an available integration.

   Or restart Cursor.

### Windows Compatibility

If you're running this project on Windows, be aware that `go-sqlite3` requires **CGO to be enabled** in order to compile and work properly. By default, **CGO is disabled on Windows**, so you need to explicitly enable it and have a C compiler installed.

#### Steps to get it working:

1. **Install a C compiler**  
   We recommend using [MSYS2](https://www.msys2.org/) to install a C compiler for Windows. After installing MSYS2, make sure to add the `ucrt64\bin` folder to your `PATH`.  
   → A step-by-step guide is available [here](https://code.visualstudio.com/docs/cpp/config-mingw).

2. **Enable CGO and run the app**

   ```bash
   cd whatsapp-bridge
   go env -w CGO_ENABLED=1
   go run .
   ```

Without this setup, you'll likely run into errors like:

> `Binary was compiled with 'CGO_ENABLED=0', go-sqlite3 requires cgo to work.`

## Architecture Overview

This application consists of two main components:

1. **Go WhatsApp Bridge** (`whatsapp-bridge/`): A Go application that connects to WhatsApp's web API, handles authentication via QR code, and stores message history in SQLite. It serves as the bridge between WhatsApp and the MCP server.

2. **Python MCP Server** (`whatsapp-mcp-server/`): A Python server implementing the Model Context Protocol (MCP), which provides standardized tools for Claude to interact with WhatsApp data and send/receive messages.

### Data Storage

- All message history is stored in a SQLite database within the `whatsapp-bridge/store/` directory
- The database maintains tables for chats, messages and `contacts_identity` (who is behind each phone number and LID, with address-book and push names)
- Messages are indexed for efficient searching and retrieval
- Downloaded media lives under `whatsapp-bridge/store/<chat>/<message id>/`

#### Database changes

The bridge upgrades `store/messages.db` by itself when it starts, only by adding to it (`ALTER TABLE … ADD COLUMN`, `CREATE … IF NOT EXISTS`); nothing is recreated or dropped, and existing messages are kept. Added over time: `messages.local_path`, `messages.mimetype`, `messages.forwarded`, `messages.quoted_id`, the `contacts_identity` table and the `idx_messages_chat_ts` index. The MCP server opens the database read-only and still works, with those fields empty, if the bridge has not upgraded it yet. Back up `store/messages.db` before the first start with a new version if you want a way back.

## Usage

Once connected, you can interact with your WhatsApp contacts through Claude, leveraging Claude's AI capabilities in your WhatsApp conversations.

### MCP Tools

Claude can access the following 15 tools to interact with WhatsApp.

**Reading**

- **search_contacts**: Search for contacts by name or phone number. Names come from the address book and push names; a person known by both a phone number and a LID is one result.
- **list_messages**: Retrieve messages with optional filters and context. See [Reading messages](#reading-messages).
- **list_chats**: List available chats with metadata (`query`, `limit`, `page`, `include_last_message`, `sort_by`). The last message of a chat is the real newest message.
- **get_chat**: Get information about a specific chat by JID
- **get_direct_chat_by_contact**: Find the direct chat with a specific contact
- **get_contact_chats**: List all chats involving a specific contact (each chat once)
- **get_last_interaction**: Get the most recent message with a contact
- **get_message_context**: Retrieve context around a specific message, both sides in chronological order. Takes an optional `chat_jid` for when the same message ID exists in more than one chat.
- **get_group_participants**: List the participants of a group with name, phone number, LID and admin status

**Sending**

- **send_message**: Send a text message to a phone number, JID or group. Supports real mentions, replying to a message and a dry run. Subject to the [send policy](#send-policy).
- **send_file**: Send a file (image, video, raw audio, document). Subject to the send policy.
- **send_audio_message**: Send an audio file as a WhatsApp voice message (needs an .ogg Opus file, or ffmpeg to convert). Subject to the send policy.

**Media**

- **download_media**: Download the media of one message and get the local file path. Failures return the bridge's real message and an `error_code`.
- **download_media_batch**: Download all the media of a chat inside a time window, one at a time, with a result per item. See [Downloading a window of media](#downloading-a-window-of-media).

**Diagnostics**

- **get_connection_status**: Is the bridge running and connected to WhatsApp? Returns the number, uptime, the time of the last event and message, pending media retries. See [Checking the bridge](#checking-the-bridge).

### Reading messages

`list_messages` returns text by default. Everything below is optional:

| Parameter | Meaning |
| --------- | ------- |
| `after`, `before` | ISO-8601 date or datetime, **with a UTC offset** (`2026-10-21T22:12:00-03:00`). Without an offset the time is read in `WHATSAPP_TIMEZONE` (an IANA name such as `America/Recife`) or, if that is not set, in the system time zone. Both limits are exclusive and compared as instants, whatever offset the stored times have. |
| `chat_jid`, `chat_name` | Filter by chat. `chat_name` is part of the name, any case; both together must match. |
| `sender_phone_number`, `query` | Filter by sender (phone or LID) and by text. |
| `limit`, `page`, `order` | Page size (at least 1), page number (from 0) and `"desc"` (newest first, default) or `"asc"`. |
| `include_context`, `context_before`, `context_after` | Add the neighbours of each match. The context comes from the match's own chat, grouped by chat, in chronological order, without repeated messages. The found messages are marked: `>>` at the start of the line in text, `is_match` in structured output. |
| `format` | `"text"` (default) or `"structured"`. |

With `format="structured"` the result is a dict: `messages`, `total` (matches, not counting context), `page`, `limit`, `has_more` and `next_page` (`null` on the last page). Each message has `id`, `timestamp` (ISO-8601 with offset), `chat_jid`, `chat_name`, `sender`, `sender_name`, `content`, `is_from_me`, `is_match` and `media` (`null` for text, otherwise `type`, `filename`, `mimetype`, `size`, `forwarded`, `quoted_message_id`, `local_path`).

In text output, a message with media starts with `[type - Message ID: … - Chat JID: … - File: name (mime, size) - Forwarded - Reply to: id - Local path: path]`. The local path appears only while the file exists. Mentions in the text (`@5511999990000`) are shown with the person's name when it is known.

### Sending messages

#### Mentions, replies and dry run

```text
send_message(recipient="<group JID>", message="@+55 51 98175-1110 can you check this?", reply_to="<message ID>", dry_run=true)
```

- **Mentions**: write `@+5551981751110`, `@5551981751110` or `@<lid>` (7 to 20 digits, no spaces) and the person is really tagged and notified; the token is sent as `@<digits>` and WhatsApp shows the name. `@` after a letter, number, `@`, `/` or a backtick is left as text, so e-mails, URLs, dates and code are safe. `mentions=[...]` takes JIDs, phones or LIDs to tag people without a token in the text. At most 50 per message (`INVALID_MENTION`). In a group addressed by LID the bridge swaps phone and LID for the one the group uses.
- **Reply**: `reply_to` is the ID of a message of that chat; it is quoted. An unknown ID is refused with `REPLY_TARGET_NOT_FOUND` and nothing is sent.
- **Dry run**: `dry_run=true` sends nothing and returns `preview` with the final text, the resolved chat (name and JID), the mentions and the quoted message. The send policy still applies, and nothing is written to the audit log.
- Formatting (`*bold*`, `_italic_`, `> quote`) goes through untouched.

#### Send policy

By default the server can send to anyone. To restrict it, copy `whatsapp-mcp-server/send_policy.example.json` to `send_policy.json` (it is git-ignored):

```json
{
  "mode": "denylist",
  "allow": ["Project Elbratec"],
  "deny": ["120363000000000000@g.us"],
  "override_token": "pick-a-value-only-you-know"
}
```

- `mode`: `denylist` (default: send to anyone not denied) or `allowlist` (send only to what is in `allow`). `deny` always wins.
- Entries are a **JID** (`…@g.us`, `…@s.whatsapp.net`, `…@lid`), a **phone number** or a **chat name** (exact, any case). Prefer JIDs: a name is not a stable identifier, since anyone with admin rights in a group can rename it. For a direct chat, a phone entry and a LID entry match the same person when the contact directory (`contacts_identity`) knows both ids; list both if you are not sure it does. Groups match by JID only.
- The destination is resolved to one canonical JID before the policy is checked, and that same JID is what the bridge is told to use. A name that matches no chat or several is refused (`RECIPIENT_NOT_FOUND`, `RECIPIENT_AMBIGUOUS`); text that is not a phone, a JID or a name is `RECIPIENT_INVALID`.
- A blocked send returns `success=false`, `error_code=SEND_BLOCKED_BY_POLICY` and sends nothing. `send_message`, `send_file` and `send_audio_message` take `override_token` to release one send; it must match the file. **The token is a speed bump, not a security boundary:** anyone who can read `send_policy.json` can read it, and the agent can be asked to read it.
- The file is read on every send, so edits apply at once. If it is invalid, or the chat list cannot be read to check name rules, every send is blocked (`SEND_BLOCKED_BY_POLICY` or `DIRECTORY_UNAVAILABLE`) and the token does not release them.
- Every attempt (sent, blocked, failed) is appended to `send_audit.log` (JSON lines, mode 0600, git-ignored): time, kind, recipient, chat, size, result, rule, whether the token was used. Never the message text or the token.

### Media Handling Features

#### Media Sending

- **Images, Videos, Documents**: Use the `send_file` tool to share any supported media type.
- **Voice Messages**: Use the `send_audio_message` tool to send audio files as playable WhatsApp voice messages.
  - For optimal compatibility, audio files should be in `.ogg` Opus format.
  - With FFmpeg installed, the system will automatically convert other audio formats (MP3, WAV, etc.) to the required format.
  - Without FFmpeg, you can still send raw audio files using the `send_file` tool, but they won't appear as playable voice messages.

#### Media Downloading

The bridge **saves incoming media automatically** (images, documents and audio up to 25 MB by default) as soon as it arrives, in `whatsapp-bridge/store/<chat>/<message id>/<filename>`. The message shows `Local path: …`. Media that was not saved (too large, another type, or it arrived while the bridge was off) can be fetched with `download_media` (`message_id` and `chat_jid` are shown with each message), which returns the file path.

Media that WhatsApp has already expired is recovered with the same "media retry" the official apps use: the bridge asks your phone to upload it again and waits up to 90 seconds. **The phone must be online and WhatsApp must not be force-stopped.**

##### Diagnosing a download failure

`download_media` returns `success=false`, the bridge's real message and an `error_code`:

| `error_code` | Meaning | What to do |
| ------------ | ------- | ---------- |
| `LINK_EXPIRED_PHONE_OFFLINE` | The link expired and the phone did not answer the re-upload request (a `hint` says so) | Open WhatsApp on the phone linked to the bridge, unlocked and online, and try again |
| `MEDIA_NOT_FOUND` | The message does not exist in that chat or is not a media message | Check `message_id` and `chat_jid` |
| `BRIDGE_UNREACHABLE` | The MCP server cannot reach the bridge | Is it running? Call `get_connection_status` |
| `DOWNLOAD_TIMEOUT` | Only with a time limit (batch downloads): the bridge did not answer in time | Raise `timeout_seconds` or retry |
| `UNKNOWN` | Anything else; read `message` | See `store/bridge.log` |

##### Downloading a window of media

```text
download_media_batch(chat_jid="<JID>", after="2026-10-20T22:12:00-03:00", types=["image", "document"])
```

Downloads the media of that chat inside the window (same `after`/`before` rules as `list_messages`), oldest first, **one at a time**. `types` is any of `image`, `video`, `audio`, `document` (default all). Limits: `max_size_mb` (default 25; larger media is skipped, nothing is downloaded), `max_items` (default 50, up to 200; `truncated=true` when more were left out) and `timeout_seconds` per item (default 120, up to 600). The result has `total`, `downloaded`, `already_downloaded`, `failed`, `skipped`, `truncated`, `aborted` and `items`; each item has `message_id`, `timestamp`, `media_type`, `filename`, `status` (`downloaded`, `already_downloaded`, `failed`, `skipped`), `path` and, when it did not download, `error_code`, `message` and `hint`. One failed item does not stop the batch; a bridge that does not answer does (`aborted="BRIDGE_UNREACHABLE"`, the rest `skipped`). Bad arguments return `error_code=INVALID_ARGUMENT`.

### Checking the bridge

`get_connection_status` (or `GET http://127.0.0.1:8080/api/status`) returns `connected`, `logged_in`, `phone`, `version`, `started_at`, `uptime_seconds`, `last_event_at`, `last_message_at`, `last_connected_at`, `last_disconnected_at` (all in UTC, `null` if it has not happened since the bridge started) and `pending_media_retries`. If the bridge does not answer, the tool returns `connected=false`, `bridge_reachable=false`, `error_code=BRIDGE_UNREACHABLE`, the time of the newest stored message (`last_message_at`) and the last write to the bridge log (`last_log_at`).

### Running the bridge

`make run` (in `whatsapp-bridge/`) builds `bin/whatsapp-bridge` and runs that binary, so it does not depend on the `go run` build cache. To keep it running in the background and restart it on failure:

```bash
cd whatsapp-bridge
make install-service     # writes ~/.config/systemd/user/whatsapp-bridge.service for this directory
systemctl --user daemon-reload && systemctl --user enable --now whatsapp-bridge
journalctl --user -u whatsapp-bridge -f
```

The path of the project must be made of letters, digits and `_ . / -` (the target refuses others, which would break the unit file). The first run needs a terminal for the QR code, so pair with `make run` before enabling the service.

Everything the bridge prints also goes to `whatsapp-bridge/store/bridge.log` (rotated at 5 MB, 3 old files kept, mode 0600). The pairing QR code is shown on the terminal only. The log contains message text, like the database does.

## Configuration

Environment variables are read **only when the process starts**: after changing one, restart the bridge (or the MCP server, for the ones it reads).

| Variable | Read by | Default | Meaning |
| -------- | ------- | ------- | ------- |
| `WHATSAPP_AUTO_DOWNLOAD` | bridge | on | `false` or `0` turns off saving incoming media on arrival |
| `WHATSAPP_AUTO_DOWNLOAD_MAX_MB` | bridge | `25` | Largest media saved automatically (by the size WhatsApp declares) |
| `WHATSAPP_AUTO_DOWNLOAD_TYPES` | bridge | `image,document,audio` | Comma-separated types saved automatically (`image`, `video`, `audio`, `document`) |
| `WHATSAPP_MEDIA_RETENTION_DAYS` | bridge | off | Delete downloaded files of messages older than this many days (the messages stay). Only a positive whole number turns it on. |
| `WHATSAPP_TIMEZONE` | MCP server | system zone | IANA zone (`America/Recife`) for dates given without an offset. An unknown name is an error. |
| `WHATSAPP_SEND_POLICY` | MCP server | `whatsapp-mcp-server/send_policy.json` | Path of the send policy file |
| `WHATSAPP_SEND_AUDIT_LOG` | MCP server | `whatsapp-mcp-server/send_audit.log` | Path of the send audit log |

The bridge also needs to be **restarted after updating it** for new behaviour to apply: recording of file name, MIME type, forwarded and reply information, automatic download, mentions and replies, the status endpoint and the log all live in the bridge.

## Error codes

Sending: `SEND_BLOCKED_BY_POLICY`, `RECIPIENT_NOT_FOUND`, `RECIPIENT_AMBIGUOUS`, `RECIPIENT_INVALID`, `DIRECTORY_UNAVAILABLE`, `INVALID_MENTION`, `REPLY_TARGET_NOT_FOUND`.
Downloading: `LINK_EXPIRED_PHONE_OFFLINE`, `MEDIA_NOT_FOUND`, `BRIDGE_UNREACHABLE`, `DOWNLOAD_TIMEOUT`, `SKIPPED_TOO_LARGE`, `NOT_ATTEMPTED` (items of a batch that stopped), `INVALID_ARGUMENT`, `UNKNOWN`.
Status: `BRIDGE_UNREACHABLE`, `UNKNOWN`.

## Technical Details

1. Claude sends requests to the Python MCP server
2. The MCP server queries the Go bridge for WhatsApp data or directly to the SQLite database
3. The Go accesses the WhatsApp API and keeps the SQLite database up to date
4. Data flows back through the chain to Claude
5. When sending messages, the request flows from Claude through the MCP server to the Go bridge and to WhatsApp

## Troubleshooting

- If you encounter permission issues when running uv, you may need to add it to your PATH or use the full path to the executable.
- Make sure both the Go application and the Python server are running for the integration to work properly. Ask for `get_connection_status` to see what the bridge is doing, and read `whatsapp-bridge/store/bridge.log`.

### Authentication Issues

- **QR Code Not Displaying**: If the QR code doesn't appear, try restarting the authentication script. If issues persist, check if your terminal supports displaying QR codes.
- **WhatsApp Already Logged In**: If your session is already active, the Go bridge will automatically reconnect without showing a QR code.
- **Device Limit Reached**: WhatsApp limits the number of linked devices. If you reach this limit, you'll need to remove an existing device from WhatsApp on your phone (Settings > Linked Devices).
- **No Messages Loading**: After initial authentication, it can take several minutes for your message history to load, especially if you have many chats.
- **WhatsApp Out of Sync**: If your WhatsApp messages get out of sync with the bridge, delete both database files (`whatsapp-bridge/store/messages.db` and `whatsapp-bridge/store/whatsapp.db`) and restart the bridge to re-authenticate.

For additional Claude Desktop integration troubleshooting, see the [MCP documentation](https://modelcontextprotocol.io/quickstart/server#claude-for-desktop-integration-issues). The documentation includes helpful tips for checking logs and resolving common issues.
