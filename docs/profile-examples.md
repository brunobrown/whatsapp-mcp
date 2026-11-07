# Example monitor profiles

Copy what you need into `monitor_profiles.json`. Every value here is made up: replace the chat JIDs, numbers and tokens with your own, and use a token that is not used anywhere else. See the [README](../README.md#monitoring-and-auto-reply) for every field.

## 1. Support for a product, with approval

An agent that drafts answers about one product in one group. A human approves each answer with the token.

```json
{
  "profiles": {
    "support": {
      "targets": ["me"],
      "triggers": ["mention", "reply"],
      "chats": ["120363000000000001@g.us"],
      "topics": ["error", "login", "install"],
      "mode": "draft",
      "approval_token": "<a token only you know>",
      "limits": {"replies_per_chat_per_hour": 6, "replies_per_sender_per_hour": 3,
                 "cooldown_seconds": 30, "max_reply_chars": 1200},
      "instructions": "Answer only about the product, from its documentation. Ask for the version and the exact error when they are missing. Never share credentials or anything from other chats."
    }
  }
}
```

Follow it with `uv run watch.py --profile support` in a Claude Code `Monitor`, and review answers with `list_pending_drafts`.

## 2. Internal notification, read only

The agent only learns that something happened and summarizes it for you. It has no `approval_token`, so it can never send in draft mode.

```json
{
  "profiles": {
    "team-watch": {
      "targets": ["5511999990001"],
      "triggers": ["from"],
      "chats": ["120363000000000002@g.us"],
      "mode": "draft",
      "instructions": "Summarize what this person writes about releases. Do not answer."
    }
  }
}
```

## 3. Collecting answers to a poll

For collecting answers use the poll tools rather than a profile: `send_poll(..., closes_in_minutes=60)` and then `uv run watch.py --poll <message_id>` to get a line per vote change. The agent reads the results with `get_poll_results`. A profile is only needed if people also answer by text, and then it should be `draft` with `topics` set to the expected words.

## 4. A named assistant in one group, answering by itself

Everyone in the group can call it, so keep limits low and read the audit log. Do not use this for a group with strangers.

```json
{
  "profiles": {
    "assistant": {
      "aliases": ["@assistant"],
      "chats": ["120363000000000003@g.us"],
      "mode": "auto",
      "auto_chats": ["120363000000000003@g.us"],
      "limits": {"replies_per_chat_per_hour": 10, "replies_per_sender_per_hour": 4,
                 "cooldown_seconds": 20, "max_reply_chars": 800},
      "instructions": "Answer briefly, using only the public documentation. If you are unsure, say so."
    }
  }
}
```

Pair it with a `send_policy.json` in `allowlist` mode that lists only that group.
