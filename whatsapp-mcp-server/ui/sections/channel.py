"""Claude Code channel: tells an open session that something arrived (channel.json). Off by default; read when the server starts."""
import channel
from ui.core.problems import from_message
from ui.core.registry import Field, JsonFile, Section, register

_KEYS = ("enabled", "profiles", "polls", "include_excerpt", "excerpt_chars", "batch_seconds", "min_interval_seconds",
         "max_batch", "poll_interval_seconds")
_DEFAULTS = channel.Config()


def validate(values):
    cfg = channel.parse_config(values)
    return [from_message(cfg.error, _KEYS)] if cfg.error else []


SECTION = register(Section(
    id="channel", title="Canal do Claude Code", icon="notifications", order=40, restart="server",
    description="Avisa uma sessão aberta do Claude Code, só com ids e horários, quando um perfil do monitor ou uma enquete tem novidade. Pesquisa em andamento; vem desligado.",
    storage=JsonFile(channel.config_path), validate=validate,
    fields=(
        Field("enabled", "Canal ativo", "bool", default=False,
              help="Só vale se o Claude Code for iniciado com o servidor como canal de desenvolvimento."),
        Field("profiles", "Perfis do monitor", "list", default=[], max_length=100,
              help="Os perfis são o único controle sobre o que vira aviso: conversas, remetentes, gatilhos e assuntos. Até 20."),
        Field("polls", "Enquetes", "list", default=[], max_length=100, help="Ids de mensagens de enquete cujos votos e encerramento são avisados. Até 20."),
        Field("include_excerpt", "Incluir um trecho do texto", "bool", default=_DEFAULTS.include_excerpt,
              risk="O trecho é texto de um desconhecido colocado diante do agente. Deixe desligado, a menos que aceite esse risco.",
              help="Desligado, o aviso leva só ids, tipos e horários."),
        Field("excerpt_chars", "Tamanho do trecho", "int", default=_DEFAULTS.excerpt_chars, bounds=(1, 280)),
        Field("batch_seconds", "Agrupar avisos em (s)", "float", default=_DEFAULTS.batch_seconds, bounds=(0, 60)),
        Field("min_interval_seconds", "Intervalo mínimo entre avisos (s)", "float", default=_DEFAULTS.min_interval_seconds, bounds=(0, 3600)),
        Field("max_batch", "Eventos por aviso", "int", default=_DEFAULTS.max_batch, bounds=(1, 100)),
        Field("poll_interval_seconds", "Consulta às enquetes (s)", "float", default=_DEFAULTS.poll_interval_seconds, bounds=(0.05, 60)),
    ),
))
