"""Per-user configuration directory and the token of the bridge's local API.

The directory and its files are the same ones the bridge uses (see whatsapp-bridge/apiguard.go).
"""
import os
import re
import sys
from typing import Dict, Optional

CONFIG_DIR_NAME = "whatsapp-mcp"
API_TOKEN_FILE = "api_token"
_TOKEN = re.compile(r"[0-9a-f]{64}")

BRIDGE_UNAUTHORIZED = "BRIDGE_UNAUTHORIZED"
UNAUTHORIZED_HINT = (
    "O bridge recusou o token da API. Reinicie o bridge e o servidor MCP para que usem o mesmo token "
    "(ele fica em api_token, no diretório de configuração do usuário)."
)


def config_dir() -> str:
    """WHATSAPP_CONFIG_DIR, else the user's configuration directory (the same rule as Go's os.UserConfigDir) plus whatsapp-mcp."""
    configured = os.environ.get("WHATSAPP_CONFIG_DIR")
    if configured:
        return configured
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(base, CONFIG_DIR_NAME)


def read_api_token() -> Optional[str]:
    """The API token, read from disk on every call so that a restarted bridge is followed. None when there is none."""
    try:
        with open(os.path.join(config_dir(), API_TOKEN_FILE), encoding="ascii") as f:
            token = f.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token if _TOKEN.fullmatch(token) else None


def auth_headers() -> Dict[str, str]:
    """The Authorization header for the bridge; empty when there is no token (an older bridge needs none)."""
    token = read_api_token()
    return {"Authorization": f"Bearer {token}"} if token else {}
