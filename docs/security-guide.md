# Security guide for people who run agents on WhatsApp

This guide is for anyone who lets an AI agent read WhatsApp through this project, and especially for anyone who lets it answer. The [README](../README.md#security-of-your-data) covers how the stored data is protected; this covers the agent.

## Threat model

| Who | What they can do | What stops them |
| --- | ---------------- | --------------- |
| **Anyone who can message you or a group you are in** | Write text the agent will read: instructions, fake "system" notices, requests to reveal data or to send messages (prompt injection) | Everything from WhatsApp is marked `untrusted_content`; the server enforces *who* may trigger a profile, *which chats*, rate limits and the send policy, but it cannot judge what a reply *says* |
| **A compromised or confused agent** | Call any tool it has: read other chats, send to other chats, download files | Tool permissions in your MCP client, the [send policy](../README.md#send-policy) allowlist, profiles that fix the destination of replies |
| **Another user of your machine** | Read files, call the local API | File modes (0600/0700), the API token, encrypted databases |
| **Malware running as you** | Everything you can do | Out of scope: use disk encryption and keep the machine clean |
| **A thief with a copy of the disk or a backup** | Read plain files | The databases are encrypted; media, `bridge.log`, `monitor_drafts.json` and the audit logs are not |

## What the approval token is and is not

`approval_token` in a profile is **a speed bump**. It stops an agent from sending a draft by accident, because the agent must ask you for the value. It is not a security boundary: it sits in `monitor_profiles.json`, readable by anything that runs as you. Never put a token you use elsewhere there, never paste it into a chat with the agent in advance, and do not treat a correct token as proof that a human approved. The real approval is your MCP client asking you to allow each `monitor_reply` / `send_*` call: do not set those tools to "always allow" for an agent that reads untrusted text.

## Break the trifecta first

The three legs are: private data, text from strangers, a way out. Remove one and prompt injection cannot leak anything.

- **An agent that only reads or summarizes** gets `WHATSAPP_MCP_MODE=read_only`: no tool reaches anyone.
- **An agent that must send** keeps the session taint on (the default): after it has read a chat it can only send to that same chat. Do not turn it off for an agent that reads groups with strangers. A send to another chat needs the send policy's `override_token`, which you give for that one send.
- **Run separate instances** for separate jobs (one read-only for research, one with a narrow profile for answering one group) instead of one agent with everything.

Details and limits: [Protecting an agent from prompt injection](../README.md#protecting-an-agent-from-prompt-injection).

## Rules that keep an agent safe

1. **Start in `draft`.** Replies go out only with the token. Review them with `list_pending_drafts`.
2. **Narrow the profile.** Set `chats`, `senders.allow`, `topics` and the lowest `limits` that work. A profile with no `chats` listens everywhere.
3. **Use the send policy as the last wall.** Put `send_policy.json` in `allowlist` mode with the chats the agent may write to; keep anything sensitive out of it. The policy applies to every sending tool, including `monitor_reply`.
4. **Give the agent only the tools it needs.** An agent that answers support questions does not need `delete_message`, `download_media_batch` or file access outside its documentation.
5. **Treat quoted text as data.** An instruction found inside a message, a caption, a poll option or a contact name is never an instruction. The same goes for the "text" of a pending draft: it was written after reading such data.
6. **Nothing sends by itself.** `watch.py`, the channel notifications and the webhooks only tell the agent that something happened, with ids and types, not the text. The agent reads the message with the read tools and answers through `monitor_reply`.
7. **Read the audit logs** (`monitor_audit.log`, `send_audit.log`) and `get_metrics`. A jump in blocked sends or in replies for one sender is the first sign of trouble.
8. **Use a test group before production.** Create a group with only you and, if you want, a second account of yours. Point a profile with `chats: [<test group>]` and `mode: "auto"` at it, and a send policy allowlist containing only it. Prove the flow there. Then copy the profile, change `chats` to the real one, switch back to `draft`, and remove the test group from the allowlist so the agent cannot fall back to it by mistake.

## Where secrets live

| File | Holds | Mode |
| ---- | ----- | ---- |
| `db.key` | master key of the databases | 0600, outside the repository |
| `api_token` | token of the local API | 0600 |
| `webhooks.json` | webhook secrets | 0600, refused otherwise |
| `monitor_profiles.json` | approval tokens | keep it 0600 |
| `send_policy.json` | override token | keep it 0600 |
| `monitor_drafts.json` | text of pending drafts (not encrypted; `WHATSAPP_MONITOR_DRAFTS=off` disables it) | 0600 |
| `watch_*.cursor` | a position number | 0600 |

Example values (`replace-with-your-own-value`) are refused on purpose: a copied example never becomes a working secret.

## If something goes wrong

1. Set `"enabled": false` on the profile (it applies at once) or stop the `watch.py` / receiver process.
2. Check `monitor_audit.log` and `send_audit.log` for what was sent.
3. Rotate any token that appeared in a conversation with the agent, and re-link the device if you think the session was exposed.
