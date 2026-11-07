"""Webhooks: signed HTTP calls the bridge makes when something happens (webhooks.json). Read when the bridge starts.

The rules of the file live in the bridge, so it is the bridge that validates it (POST /api/config/validate-webhooks);
without the bridge the file cannot be checked and so cannot be saved.
"""
import re
from typing import Any, Dict, List

from ui.core.bridge import BridgeClient, Unavailable
from ui.core.registry import Field, JsonFile, Problem, Section, register
from ui.core.store import config_path

FILE_NAME = "webhooks.json"
BRIDGE_PROBLEM = "_bridge"          # the field of a problem that is not about a value: the check could not be made
EVENTS = ("*", "message", "poll_created", "poll_vote", "reaction", "message_edit", "message_deleted", "poll_closed",
          "connection", "ping")
client = BridgeClient()              # replaced by tests

_ENDPOINT_KEYS = ("name", "url", "secret", "events", "chats", "include_content", "include_own", "allow_private_network", "persist_queue")
_BY_NAME = re.compile(r'endpoint "(?P<name>.*?)": (?P<key>name|url|secret|events|chats)\b')
_BY_NUMBER = re.compile(r"endpoint (?P<number>\d+): (?P<key>name|url|secret|events|chats)\b")
_UNKNOWN_EVENT = re.compile(r'endpoint "(?P<name>.*?)": unknown event')
_UNAVAILABLE = {
    "unreachable": "o bridge não responde, então o arquivo não pode ser validado nem salvo",
    "unauthorized": "o bridge recusou o token da API; reinicie o bridge e o servidor MCP para que usem o mesmo token",
    "unsupported": "este bridge é antigo e não valida webhooks; atualize e reinicie o bridge",
    "unexpected": "o bridge respondeu algo inesperado ao validar o arquivo",
}


def _locate(message: str) -> Problem:
    for pattern, field_of in ((_BY_NAME, lambda m: f"endpoints.{m['name']}.{m['key']}"),
                              (_BY_NUMBER, lambda m: f"endpoints.{int(m['number']) - 1}.{m['key']}"),
                              (_UNKNOWN_EVENT, lambda m: f"endpoints.{m['name']}.events")):
        match = pattern.search(message)
        if match:
            return Problem(field_of(match), message)
    return Problem("endpoints", message)


def validate(values: Dict[str, Any]) -> List[Problem]:
    result = client.validate_webhooks(values)
    if isinstance(result, Unavailable):
        return [Problem(BRIDGE_PROBLEM, _UNAVAILABLE.get(result.reason, _UNAVAILABLE["unexpected"]))]
    return [] if result.ok else [_locate(result.error or "o bridge recusou o arquivo")]


SECTION = register(Section(
    id="webhooks", title="Webhooks", icon="webhook", order=30, restart="bridge",
    description="O bridge avisa um programa seu, com um POST assinado, quando algo acontece. Sem este arquivo, nada sai do bridge. É lido na partida.",
    storage=JsonFile(lambda: config_path(FILE_NAME)), validate=validate,
    fields=(
        Field("enabled", "Webhooks ativos", "bool", default=True, help="Desligado, o bridge ignora todos os endpoints."),
        Field("endpoints", "Endpoints", "records", id_key="name", fields=(
            Field("name", "Nome", "text", max_length=64),
            Field("url", "URL", "text", max_length=2000,
                  help="https:// para qualquer host; http:// só para loopback. Sem usuário e senha, e nunca a API do próprio bridge."),
            Field("secret", "Segredo", "secret", max_length=500, help="Assina cada envio. Pelo menos 32 caracteres aleatórios."),
            Field("events", "Eventos", "multichoice", options=EVENTS, help='"*" inclui todos.'),
            Field("chats", "Conversas", "list", max_length=100,
                  help="Só eventos destas conversas (JID ou número). Use junto com o conteúdo incluído."),
            Field("include_content", "Incluir o conteúdo", "bool", default=False, risk_when=(True,),
                  risk="O evento passa a levar o texto e os nomes de terceiros, que o receptor deve tratar como não confiáveis.",
                  help="Desligado, o evento leva só ids, tipo, conversa, remetente, hora e o tamanho do texto."),
            Field("include_own", "Incluir o que esta conta faz em outro aparelho", "bool", default=False),
            Field("allow_private_network", "Permitir destino em rede privada", "bool", default=False, risk_when=(True,),
                  risk="Libera destinos em redes privadas: um endpoint mal configurado pode alcançar serviços internos."),
            Field("persist_queue", "Guardar a fila em disco", "bool", default=False,
                  help="A fila sobrevive a uma reinicialização. O arquivo guarda ids, nunca o texto."),
        )),
    ),
))
