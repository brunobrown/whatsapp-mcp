# Interface file fixtures (written by 0.6.0)

Sample configuration files written by the 0.6.0 interface code (tag `v0.6.0`), run once with a temporary
`WHATSAPP_CONFIG_DIR`. They are data only: the code that made them is not in this repository. The backend
that replaced it must read these files unchanged and write files of the same shape.

| File | What it shows |
| --- | --- |
| `ui_auth.json` | scrypt record (`version`, `scrypt.n/r/p`, hex `salt` of 16 bytes, hex `hash` of 32 bytes) |
| `ui_settings.json` | `{"version": 1, "env": {NAME: "text"}}` with editable variables only |
| `send_policy.json` | send policy file |
| `monitor_profiles.json` | monitor profiles, keyed by profile name |
| `webhooks.json` | webhook endpoints with a secret |
| `channel.json` | channel configuration |

## Test password

`ui_auth.json` verifies the password `correct horse battery staple` (NFC-normalised UTF-8, scrypt
n=32768, r=8, p=1, 32-byte key).

All names, numbers, tokens and secrets in these files are made up. The files are written with mode 0600 by the
tool, committed here as ordinary data files.
