"""The read-only pages: the bridge's state and counters, pending drafts and the audit logs.

Views are functions of what the sources return. A page refreshes itself at a moderate pace while it is open
(Poller) and stops when it is left. Nothing here can send, approve, discard or change anything.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

import flet as ft

from ui.app.fields import MIN_TARGET
from ui.core.bridge import Status, Unavailable
from ui.sections import ops

REFRESH_SECONDS = 5.0
_DASH = "—"
_UNAVAILABLE = {
    "unreachable": "O bridge não responde. Confira se ele está rodando; esta página volta a mostrar o estado quando ele responder.",
    "unauthorized": "O bridge recusou o token da API. Reinicie o bridge e o servidor MCP para que usem o mesmo token.",
    "unexpected": "O bridge deu uma resposta inesperada.",
    "unsupported": "Este bridge é antigo e não responde ao que esta página pede. Atualize e reinicie o bridge.",
}


# ---- reading values the way a person would --------------------------------------------------------------
def human_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return _DASH
    minutes = int(seconds // 60)
    if minutes < 1:
        return "menos de 1 min"
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h" if hours else f"{days} d"
    if hours:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    return f"{mins} min"


def human_when(value: Optional[str]) -> str:
    if not value:
        return _DASH
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return _DASH
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---- small pieces ---------------------------------------------------------------------------------------
def _heading(text: str, level: int = 1) -> ft.Control:
    return ft.Semantics(header=True, heading_level=level, content=ft.Text(text, size=28 if level == 1 else 19, weight=ft.FontWeight.W_700))


def _line(label: str, value: Any) -> ft.Control:
    return ft.Row(spacing=8, wrap=True, controls=[ft.Text(f"{label}:", size=16, color=ft.Colors.ON_SURFACE_VARIANT),
                                                   ft.Text(str(value), size=16, selectable=True)])


def _notice(text: str, colour=ft.Colors.TERTIARY, icon=ft.Icons.WARNING_AMBER) -> ft.Control:
    return ft.Semantics(live_region=True, container=True, content=ft.Container(
        padding=12, border=ft.Border.all(1, colour), border_radius=6, content=ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.START, controls=[
            ft.Icon(icon, color=colour, size=22), ft.Text(text, size=16, expand=True)])))


def _count(value: Any) -> str:
    return _DASH if value is None else str(value)


# ---- status and counters --------------------------------------------------------------------------------
def _connection(data: Dict[str, Any]) -> List[ft.Control]:
    parts: List[ft.Control] = []
    if data.get("connected"):
        parts.append(_notice("Conectado ao WhatsApp.", ft.Colors.PRIMARY, ft.Icons.CHECK_CIRCLE_OUTLINE))
    else:
        parts.append(_notice("Desconectado do WhatsApp. O bridge está rodando, mas não está ligado à conta."))
    if data.get("logged_in") is False:
        parts.append(_notice("O bridge não está pareado com uma conta. O pareamento (QR code) é feito no terminal onde o bridge roda."))
    parts += [_line("Número", data.get("phone") or _DASH), _line("Versão", data.get("version") or _DASH),
              _line("No ar há", human_duration(data.get("uptime_seconds"))), _line("Iniciado em", human_when(data.get("started_at"))),
              _line("Último evento", human_when(data.get("last_event_at"))), _line("Última mensagem", human_when(data.get("last_message_at"))),
              _line("Última conexão", human_when(data.get("last_connected_at"))), _line("Última desconexão", human_when(data.get("last_disconnected_at"))),
              _line("Downloads de mídia a repetir", _count(data.get("pending_media_retries")))]
    return parts


def _counters(data: Dict[str, Any]) -> List[ft.Control]:
    messages, hooks = data.get("messages"), data.get("webhooks")
    replies = (data.get("monitor_replies") or {}).get("by_profile") or {}
    codes = (data.get("monitor_replies") or {}).get("error_codes") or {}
    sends = data.get("sends") or {}
    parts: List[ft.Control] = [_heading(f"Contadores das últimas {int(data.get('window_hours', 24))} horas", 2)]
    parts.append(_line("Mensagens recebidas", "indisponível (o banco não pôde ser lido)" if messages is None else messages["received"]))
    if messages is not None:
        parts.append(_line("Mensagens desta conta", messages["from_this_account"]))
    parts.append(_line("Rascunhos pendentes", _count(data.get("pending_drafts"))))
    for profile, by_result in sorted(replies.items()):
        parts.append(_line(f"Perfil {profile}", ", ".join(f"{k}: {v}" for k, v in sorted(by_result.items())) or _DASH))
    if codes:
        parts.append(_line("Erros do monitor", ", ".join(f"{k}: {v}" for k, v in sorted(codes.items()))))
    by_result = sends.get("by_result") or {}
    parts.append(_line("Envios", (", ".join(f"{k}: {v}" for k, v in sorted(by_result.items())) or "nenhum") + f" (com liberação: {sends.get('with_override', 0)})"))
    if hooks is None:
        parts.append(_line("Webhooks", "indisponível (o bridge não respondeu)"))
    else:
        parts.append(_line("Webhooks", f"{'ativos' if hooks.get('enabled') else 'desligados'}, {hooks.get('endpoints', 0)} endpoint(s): "
                                       f"{hooks.get('delivered', 0)} entregues, {hooks.get('failed', 0)} falhas, {hooks.get('dropped', 0)} descartados, {hooks.get('queued', 0)} na fila"))
    return parts


def status_view(status: Union[Status, Unavailable, None], counters: Optional[Dict[str, Any]], on_refresh: Optional[Callable[[], Any]] = None) -> ft.Control:
    parts: List[ft.Control] = [_heading("Status")]
    if status is None:
        parts.append(ft.Semantics(live_region=True, container=True, content=ft.Row(spacing=12, controls=[
            ft.ProgressRing(width=24, height=24, stroke_width=3), ft.Text("Carregando o estado do bridge…", size=16)])))
    elif isinstance(status, Unavailable):
        parts.append(_notice(_UNAVAILABLE.get(status.reason, _UNAVAILABLE["unexpected"])))
    else:
        parts += _connection(status.data)
    if counters:
        parts += _counters(counters)
    if on_refresh:
        parts.append(ft.OutlinedButton(content="Atualizar agora", icon=ft.Icons.REFRESH, height=MIN_TARGET, on_click=lambda e: on_refresh()))
    return ft.Container(max_width=760, content=ft.Column(spacing=12, controls=parts))


# ---- drafts and audit -----------------------------------------------------------------------------------
def _table(columns: Sequence[str], rows: Sequence[Sequence[str]], empty: str) -> ft.Control:
    if not rows:
        return ft.Text(empty, size=16, color=ft.Colors.ON_SURFACE_VARIANT)
    return ft.Row(scroll=ft.ScrollMode.AUTO, controls=[ft.DataTable(
        columns=[ft.DataColumn(label=ft.Text(c, size=14, weight=ft.FontWeight.W_600)) for c in columns], heading_row_height=44, data_row_min_height=44,
        rows=[ft.DataRow(cells=[ft.DataCell(ft.Text(str(v), size=14, selectable=True)) for v in row]) for row in rows])])


def activity_view(drafts: Sequence[Dict[str, Any]], sends: Sequence[Dict[str, Any]], replies: Sequence[Dict[str, Any]],
                  on_refresh: Optional[Callable[[], Any]] = None) -> ft.Control:
    draft_rows = [[d.get("id"), d.get("profile"), d.get("chat_jid"), d.get("sender"), human_when(d.get("created_at")), human_when(d.get("expires_at"))] for d in drafts]
    send_rows = [[human_when(r.get("ts")), r.get("kind"), r.get("chat_jid"), r.get("result"), r.get("rule") or _DASH, "liberado" if r.get("override") else _DASH]
                 for r in sends]
    reply_rows = [[human_when(r.get("ts")), r.get("profile"), r.get("chat_jid"), r.get("sender"), r.get("mode"), r.get("result"), r.get("error_code") or _DASH]
                  for r in replies]
    parts: List[ft.Control] = [
        _heading("Rascunhos e auditoria"),
        ft.Text("Só leitura. O texto de um rascunho não aparece aqui: leia-o com a ferramenta list_pending_drafts, no cliente do agente.", size=16),
        _heading("Rascunhos pendentes", 2),
        _table(["Id", "Perfil", "Conversa", "Remetente", "Criado", "Vence"], draft_rows, "Nenhum rascunho pendente."),
        _heading("Envios (auditoria)", 2),
        _table(["Quando", "Tipo", "Conversa", "Resultado", "Regra", "Liberação"], send_rows, "Nenhum envio registrado."),
        _heading("Respostas do monitor (auditoria)", 2),
        _table(["Quando", "Perfil", "Conversa", "Remetente", "Modo", "Resultado", "Erro"], reply_rows, "Nenhuma resposta do monitor registrada."),
    ]
    if on_refresh:
        parts.append(ft.OutlinedButton(content="Atualizar agora", icon=ft.Icons.REFRESH, height=MIN_TARGET, on_click=lambda e: on_refresh()))
    return ft.Container(content=ft.Column(spacing=12, controls=parts))


# ---- refreshing while the page is open ------------------------------------------------------------------
async def _in_thread(fn: Callable[[], Any]) -> Any:
    return await asyncio.to_thread(fn)


class Poller:
    """Fetch, hand the result over, wait, repeat; until stopped. A result that arrives after stop is dropped."""

    def __init__(self, fetch: Callable[[], Any], on_result: Callable[[Any], None], interval: float = REFRESH_SECONDS,
                 sleep: Callable[[float], Any] = asyncio.sleep, run_blocking: Callable[[Callable[[], Any]], Any] = _in_thread):
        self._fetch, self._on_result, self._interval, self._sleep, self._run = fetch, on_result, interval, sleep, run_blocking
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        while not self._stopped:
            try:
                result = await self._run(self._fetch)
            except Exception:                         # one bad look must not end the refreshing
                result = None
            if self._stopped:
                return
            if result is not None:
                self._on_result(result)
            await self._sleep(self._interval)


def load_status() -> Dict[str, Any]:
    return {"status": ops.bridge_status(), "counters": ops.counters()}


def load_activity() -> Dict[str, Any]:
    return {"drafts": ops.pending_drafts(), "sends": ops.audit_tail("send"), "replies": ops.audit_tail("monitor")}


@ft.component
def StatusPage():
    data, set_data = ft.use_state(None)
    poller = ft.use_ref(None)

    def start() -> None:
        poller.current = Poller(load_status, set_data)
        ft.context.page.run_task(poller.current.run)

    def stop() -> None:
        if poller.current:
            poller.current.stop()

    ft.use_effect(start, [], cleanup=stop)
    return status_view(data["status"] if data else None, data["counters"] if data else None)


@ft.component
def ActivityPage():
    data, set_data = ft.use_state(None)
    poller = ft.use_ref(None)

    def start() -> None:
        poller.current = Poller(load_activity, set_data)
        ft.context.page.run_task(poller.current.run)

    def stop() -> None:
        if poller.current:
            poller.current.stop()

    ft.use_effect(start, [], cleanup=stop)
    if data is None:
        return ft.Row(spacing=12, controls=[ft.ProgressRing(width=24, height=24, stroke_width=3), ft.Text("Carregando…", size=16)])
    return activity_view(data["drafts"], data["sends"], data["replies"])
