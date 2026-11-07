"""The database and its keys, shown and never edited.

Whether a key or token exists, and where, is shown. The content never is, and nothing here can be changed:
switching the encryption off would expose the messages, and the key is not something a screen should hold. The
two secrets are secret fields: the screen gets only whether each one is set.
"""
import os

import localconfig
from ui.core.registry import Field, ReadOnly, Section, register


def values():
    folder = localconfig.config_dir()
    key_file = os.environ.get("WHATSAPP_DB_KEY_FILE") or os.path.join(folder, "db.key")
    token_file = os.path.join(folder, localconfig.API_TOKEN_FILE)
    key_in_env = bool(os.environ.get("WHATSAPP_DB_KEY"))
    key_set, token_set = key_in_env or os.path.isfile(key_file), os.path.isfile(token_file)
    encryption = (os.environ.get("WHATSAPP_DB_ENCRYPTION") or "on").strip().lower()
    return {
        "config_dir": folder,
        "encryption": "desligada (os bancos ficam em SQLite comum)" if encryption == "off" else "ligada",
        "db_key": "set" if key_set else "",
        "api_token": "set" if token_set else "",
        "location": ("Chave: definida pela variável WHATSAPP_DB_KEY" if key_in_env else f"Chave: {key_file}")
                    + f". Token da API: {token_file}.",
    }


SECTION = register(Section(
    id="database", title="Banco e chaves", icon="lock", order=80, storage=ReadOnly(values), validate=lambda v: [],
    description="Somente leitura. Chave perdida significa dados cifrados irrecuperáveis: guarde o db.key em um cofre. "
                "Mídia e o log do bridge não são cifrados.",
    fields=(
        Field("config_dir", "Pasta de configuração", "text", readonly=True),
        Field("encryption", "Cifragem dos bancos", "text", readonly=True, env_var="WHATSAPP_DB_ENCRYPTION",
              help="Definida pela variável WHATSAPP_DB_ENCRYPTION no ambiente do bridge."),
        Field("db_key", "Chave dos bancos", "secret", readonly=True, env_var="WHATSAPP_DB_KEY_FILE"),
        Field("api_token", "Token da API local", "secret", readonly=True,
              help="O servidor MCP e esta interface o leem a cada chamada."),
        Field("location", "Onde estão os arquivos", "text", readonly=True),
    ),
))
