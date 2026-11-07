"""Agent safety: the two measures that break the path from a stranger's text to a message that leaks data.

Both are read by the MCP server, which the client starts. A value the client sets in the server's own `env` wins
over what is saved here, and this screen cannot see it.
"""
import server_mode
import taint
from ui.core.registry import EnvKeys, Field, Section, register

SECTION = register(Section(
    id="agent_safety", title="Segurança do agente", icon="security", order=15, storage=EnvKeys(), validate=lambda values: [],
    description="Um agente que lê dados privados, lê texto de desconhecidos e pode enviar mensagens pode ser induzido por esse texto a passar os dados adiante. "
                "Tirar uma das três pernas resolve; estas duas medidas fazem isso. Valem para o servidor MCP, quando ele inicia.",
    fields=(
        Field("mcp_mode", "Modo do servidor", "choice", default=server_mode.FULL, options=(server_mode.FULL, server_mode.READ_ONLY),
              env_var=server_mode.ENV, risk_when=(server_mode.FULL,),
              risk="No modo completo o servidor oferece as ferramentas que alcançam outras pessoas: enviar, votar, reagir, apagar e responder pelo monitor.",
              help="read_only não oferece nenhuma ferramenta que alcance outras pessoas: o agente só lê, resume e busca. "
                   "Um valor desconhecido na variável vale read_only. Se o seu cliente MCP define esta variável no env do servidor, ela vence."),
        Field("session_taint", "Contaminação da sessão", "choice", default="block", options=("block", "off"),
              env_var=taint.ENV, risk_when=("off",),
              risk="Com off, o agente pode enviar a uma conversa o que leu em outra: uma mensagem maliciosa pode levá-lo a vazar dados.",
              help="block só deixa enviar à própria conversa que a sessão leu; para liberar um envio, só o token de liberação da política de envio. "
                   "Esta variável vence a chave session_taint da política. Se o seu cliente MCP a define no env do servidor, ela vence esta tela."),
    ),
))
