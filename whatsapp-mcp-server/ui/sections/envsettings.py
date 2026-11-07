"""Settings that exist only as environment variables: media, general and the interface itself.

The interface keeps them in ui_settings.json; the bridge and the server apply them at start, only where the
environment does not already define the variable. Which process must restart is told per field (envlayer.SCOPES).
"""
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import drafts
from ui import launcher
from ui.core.registry import EnvKeys, Field, Problem, Section, register

environ = os.environ        # replaced by tests: whether the process was allowed a remote bind
MEDIA_TYPES = ("image", "video", "audio", "document")


def _media(values):
    return []


MEDIA = register(Section(
    id="media", title="Mídia", icon="perm_media", order=50, storage=EnvKeys(), validate=_media,
    description="O que o bridge baixa e guarda sozinho. Lido na partida do bridge.",
    fields=(
        Field("auto_download", "Baixar mídia ao chegar", "bool", default=True, env_var="WHATSAPP_AUTO_DOWNLOAD"),
        Field("auto_download_max_mb", "Tamanho máximo baixado (MB)", "float", default=25, bounds=(0.1, 4096),
              env_var="WHATSAPP_AUTO_DOWNLOAD_MAX_MB", help="Pelo tamanho que o WhatsApp declara."),
        Field("auto_download_types", "Tipos baixados", "multichoice", options=MEDIA_TYPES, default=["image", "document", "audio"],
              env_var="WHATSAPP_AUTO_DOWNLOAD_TYPES"),
        Field("media_retention_days", "Apagar mídia baixada após (dias)", "int", bounds=(1, 3650), env_var="WHATSAPP_MEDIA_RETENTION_DAYS",
              help="Vazio mantém para sempre. Apaga os arquivos; as mensagens ficam."),
        Field("keep_deleted_content", "Guardar o conteúdo de mensagens apagadas para todos", "bool", default=False, risk_when=(True,),
              env_var="WHATSAPP_KEEP_DELETED_CONTENT",
              risk="Contraria o pedido de quem apagou: o texto e a mídia continuam no seu computador.",
              help="Desligado, o texto e a mídia somem quando o autor apaga para todos."),
        Field("log_messages", "Gravar o texto das mensagens no log", "bool", default=False, env_var="WHATSAPP_LOG_MESSAGES", risk_when=(True,),
              risk="O log do bridge passa a conter o que as pessoas escrevem, e ele não é cifrado.",
              help="Desligado, o log mostra só o tamanho do texto."),
    ),
))


def _general(values):
    zone = values.get("timezone")
    if zone:
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return [Problem("timezone", "não é um fuso horário conhecido; use um nome IANA, como America/Recife")]
    return []


GENERAL = register(Section(
    id="general", title="Geral", icon="tune", order=60, storage=EnvKeys(), validate=_general,
    description="Fuso das datas informadas sem deslocamento e validade dos rascunhos pendentes. Lido na partida do servidor MCP.",
    fields=(
        Field("timezone", "Fuso horário", "text", env_var="WHATSAPP_TIMEZONE", max_length=64,
              help="Nome IANA (America/Recife) para datas sem deslocamento. Vazio usa o fuso do sistema."),
        Field("draft_ttl_hours", "Validade de um rascunho (horas)", "int", default=drafts.DEFAULT_TTL_HOURS,
              bounds=(1, drafts.MAX_TTL_HOURS), env_var="WHATSAPP_MONITOR_DRAFT_TTL_HOURS"),
    ),
))


def _interface(values):
    host = values.get("host")
    if host and not launcher._is_loopback(host) and (environ.get("WHATSAPP_UI_ALLOW_REMOTE") or "").strip().lower() != "true":
        return [Problem("host", "fora do loopback a interface só sobe com WHATSAPP_UI_ALLOW_REMOTE=true no ambiente do bridge, "
                                "que não pode ser definido por esta tela; sem isso ela deixaria de abrir")]
    return []


INTERFACE = register(Section(
    id="interface", title="Interface", icon="web", order=70, storage=EnvKeys(), validate=_interface,
    description="Onde esta própria interface escuta. Lido na partida do bridge.",
    fields=(
        Field("enabled", "Interface ativa", "bool", default=True, env_var="WHATSAPP_UI"),
        Field("host", "Endereço", "text", default=launcher.DEFAULT_HOST, env_var="WHATSAPP_UI_HOST", max_length=253,
              risk="Fora do loopback a interface fica acessível a outras máquinas, com senha mas sem TLS.",
              help="127.0.0.1 só aceita acessos deste computador."),
        Field("port", "Porta", "int", default=launcher.DEFAULT_PORT, bounds=(launcher.MIN_PORT, launcher.MAX_PORT), env_var="WHATSAPP_UI_PORT"),
    ),
))
