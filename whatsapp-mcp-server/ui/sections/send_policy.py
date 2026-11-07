"""Send policy: which chats the agent may send to (send_policy.json). Read at every send, so no restart."""
import send_policy
from ui.core.problems import from_message
from ui.core.registry import Field, JsonFile, Problem, Section, register

_KEYS = ("mode", "allow", "deny", "override_token", "session_taint")
_ENTRY = 200


def validate(values):
    policy, message = send_policy.parse_policy(values)
    if message:
        return [from_message(message, _KEYS)]
    problems = []
    if values.get("session_taint") not in (None, "block", "off"):
        problems.append(Problem("session_taint", "deve ser block ou off"))
    if values.get("override_token") in send_policy.PLACEHOLDER_TOKENS:
        problems.append(Problem("override_token", "ainda é o valor do arquivo de exemplo, que não libera nada: escolha o seu"))
    return problems


SECTION = register(Section(
    id="send_policy", title="Política de envio", icon="shield", order=10,
    description="Define para quais conversas o agente pode enviar mensagens. É lida a cada envio.",
    storage=JsonFile(lambda: send_policy.POLICY_PATH),
    validate=validate,
    fields=(
        Field("mode", "Modo", "choice", default="denylist", options=("denylist", "allowlist"),
              help="denylist: envia a todos, menos aos da lista de bloqueio. allowlist: envia só aos da lista de permissão."),
        Field("allow", "Lista de permissão", "list", default=[], max_length=_ENTRY,
              help="Nomes de conversas, números ou JIDs (123@g.us). Vale no modo allowlist."),
        Field("deny", "Lista de bloqueio", "list", default=[], max_length=_ENTRY,
              help="Nomes de conversas, números ou JIDs. Vale nos dois modos e vence a lista de permissão."),
        Field("override_token", "Token de liberação", "secret",
              help="Quem o informa em um envio libera aquele envio bloqueado. É um obstáculo, não uma barreira."),
        Field("session_taint", "Contaminação da sessão", "choice", default="block", options=("block", "off"), risk_when=("off",),
              risk="Com off, o agente pode enviar a uma conversa o que leu em outra: uma mensagem maliciosa pode levá-lo a vazar dados.",
              help="block impede enviar a uma conversa diferente das que a sessão já leu. A variável WHATSAPP_SESSION_TAINT, se definida, vence este valor."),
    ),
))
