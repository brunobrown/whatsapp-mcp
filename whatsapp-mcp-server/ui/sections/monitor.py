"""Monitor profiles: when the agent is called and how it may answer (monitor_profiles.json). Read at every call."""
import re
from typing import Any, Dict, List

import monitor
from ui.core.registry import Field, JsonFile, Problem, Section, register

_NAME = re.compile(r"[^\x00-\x1f\x7f.]{1,64}")
_LIST_ITEM = 100
_LIMIT_KEYS = tuple(monitor.DEFAULT_LIMITS)
_LIMIT_LABELS = {
    "replies_per_chat_per_hour": "Respostas por conversa por hora",
    "replies_per_sender_per_hour": "Respostas por remetente por hora",
    "cooldown_seconds": "Intervalo entre respostas (s)",
    "max_reply_chars": "Tamanho máximo da resposta",
}
# form sub-field -> where it lives in the file
_PLAIN = ("enabled", "mode", "targets", "aliases", "triggers", "chats", "auto_chats", "topics", "auto_senders",
          "approval_token", "allow_own_alias", "cross_chat_replies", "max_event_age_minutes", "instructions")
_LISTS = ("targets", "aliases", "chats", "auto_chats", "topics", "auto_senders")
_FILE_KEYS = tuple(sorted(monitor._PROFILE_KEYS))


def _profile_fields():
    limits = tuple(Field(f"limit_{k}", _LIMIT_LABELS[k], "int", default=monitor.DEFAULT_LIMITS[k], bounds=(1, 1_000_000)) for k in _LIMIT_KEYS)
    return (
        Field("name", "Nome do perfil", "text", max_length=64, help="Identifica o perfil nas ferramentas. Sem pontos."),
        Field("enabled", "Ativo", "bool", default=True),
        Field("mode", "Modo de resposta", "choice", default="draft", options=monitor.MODES, risk_when=("auto",),
              risk="Em auto o agente responde sem aprovação humana a quem estiver liberado.",
              help="draft deixa a resposta pendente para aprovação. auto envia direto."),
        Field("targets", "Números monitorados", "list", max_length=_LIST_ITEM,
              help='Telefone, LID, JID ou "me" (a própria conta). Quem é mencionado, respondido ou escreve a esses números aciona o perfil.'),
        Field("aliases", "Apelidos do agente", "list", max_length=40,
              help=f"Nomes que chamam o agente no texto (2 a 40 caracteres, sem espaços; até {monitor.MAX_ALIASES})."),
        Field("triggers", "Gatilhos", "multichoice", options=monitor.TRIGGERS,
              help="Vazio usa o padrão do perfil: mention, reply e direct para números, alias e agent_reply para apelidos."),
        Field("chats", "Conversas permitidas", "list", max_length=_LIST_ITEM, help="JIDs de conversas. Vazio vale para todas."),
        Field("auto_chats", "Conversas em resposta automática", "list", max_length=_LIST_ITEM,
              help="No modo auto, qualquer pessoa dessas conversas é respondida sem token."),
        Field("senders_allow", "Remetentes permitidos", "list", max_length=_LIST_ITEM, help="Vazio permite qualquer remetente."),
        Field("senders_deny", "Remetentes bloqueados", "list", max_length=_LIST_ITEM),
        Field("auto_senders", "Remetentes em resposta automática", "list", max_length=_LIST_ITEM),
        Field("topics", "Assuntos", "list", max_length=60, help="Palavras-chave. Vazio aceita qualquer texto."),
        Field("allow_own_alias", "Aceitar apelido nas próprias mensagens", "bool", default=False),
        Field("cross_chat_replies", "Responder após ler outras conversas", "bool", default=False, risk_when=(True,),
              risk="Permite responder depois de ler outras conversas do perfil: o texto de uma pode induzir o agente a levar dados à outra."),
        Field("approval_token", "Token de aprovação", "secret", help="Exigido para enviar no modo auto. É um obstáculo, não uma barreira."),
        *limits,
        Field("max_event_age_minutes", "Idade máxima do evento (min)", "int", default=monitor.DEFAULT_MAX_EVENT_AGE_MINUTES, bounds=(1, 100_000)),
        Field("instructions", "Instruções ao agente", "text", max_length=8000,
              help="Orientação ao agente. O servidor não a impõe: não é um controle."),
    )


# -- the file has profiles by name, with senders and limits nested; the form has a flat record per profile ------
def decode(file_values: Dict[str, Any]) -> Dict[str, Any]:
    profiles = file_values.get("profiles") if isinstance(file_values, dict) else None
    records: List[Dict[str, Any]] = []
    for name, body in (profiles.items() if isinstance(profiles, dict) else []):
        body = body if isinstance(body, dict) else {}
        record: Dict[str, Any] = {"name": name}
        for key in _PLAIN:
            if key in _LISTS:
                record[key] = list(body[key]) if isinstance(body.get(key), list) else []
            else:
                record[key] = body.get(key)
        senders = body.get("senders") if isinstance(body.get("senders"), dict) else {}
        record["senders_allow"] = list(senders["allow"]) if isinstance(senders.get("allow"), list) else []
        record["senders_deny"] = list(senders["deny"]) if isinstance(senders.get("deny"), list) else []
        limits = body.get("limits") if isinstance(body.get("limits"), dict) else {}
        for key in _LIMIT_KEYS:
            record[f"limit_{key}"] = limits.get(key)
        records.append(record)
    return {"profiles": records}


def encode(form_values: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for record in form_values.get("profiles") or []:
        body: Dict[str, Any] = {}
        for key in _PLAIN:
            value = record.get(key)
            if value is None or (key in _LISTS and not value) or (key == "triggers" and not value):
                continue
            body[key] = value
        senders = {k: record[f"senders_{k}"] for k in ("allow", "deny") if record.get(f"senders_{k}")}
        if senders:
            body["senders"] = senders
        limits = {k: record[f"limit_{k}"] for k in _LIMIT_KEYS if record.get(f"limit_{k}") is not None}
        if limits:
            body["limits"] = limits
        out[record["name"]] = body
    return {"profiles": out}


def check_form(form_values: Dict[str, Any]) -> List[Problem]:
    problems, seen = [], set()
    for index, record in enumerate(form_values.get("profiles") or []):
        name = record.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            problems.append(Problem(f"profiles.{index}.name", "o nome tem de ter de 1 a 64 caracteres, sem pontos nem caracteres de controle"))
        elif name in seen:
            problems.append(Problem(f"profiles.{index}.name", "já existe um perfil com este nome"))
        seen.add(name)
    return problems


_SUBFIELD = {"senders.allow": "senders_allow", "senders.deny": "senders_deny", "senders": "senders_allow"}


def _locate(message: str) -> Problem:
    """Tie a message of monitor.parse_profiles ('profiles.NAME.KEY ...') to that profile and field."""
    match = re.match(r"profiles\.(?P<name>.+?)\.(?P<key>[A-Za-z_]+(?:\.[a-z_]+)?)(?=[\s:.,]|$)", message)
    if not match:
        named = re.match(r"profiles\.(?P<name>.+?)(?=[\s:])", message)
        return Problem(f"profiles.{named.group('name')}" if named else "profiles", message)
    key = match.group("key")
    key = _SUBFIELD.get(key, f"limit_{key.split('.', 1)[1]}" if key.startswith("limits.") else key)
    return Problem(f"profiles.{match.group('name')}.{key}", message)


def validate(file_values: Dict[str, Any]) -> List[Problem]:
    _profiles, message = monitor.parse_profiles(file_values)
    return [_locate(message)] if message else []


SECTION = register(Section(
    id="monitor", title="Monitor e perfis", icon="visibility", order=20,
    description="Perfis que dizem quando o agente é chamado, a quem pode responder e com que limites. São lidos a cada chamada.",
    storage=JsonFile(lambda: monitor.CONFIG_PATH), validate=validate, decode=decode, encode=encode, check_form=check_form,
    fields=(Field("profiles", "Perfis", "records", id_key="name", fields=_profile_fields()),),
))
