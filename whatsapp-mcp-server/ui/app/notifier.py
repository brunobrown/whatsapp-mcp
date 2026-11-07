"""Where a message to the operator goes.

What belongs to a field or to the operation on screen stays beside it (scope "local"). What matters wherever the
operator is looking (saved, could not save, the bridge went away or came back, the session is ending, a restart is
needed) is "global": a native notification when the browser allows it, else a snackbar, and never the same one
twice within a minute. The texts are fixed sentences: no value from a form, no secret, no message text.
"""
import re
import time
import zlib
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

REPEAT_SECONDS = 60
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text: str, limit: int = 60) -> str:
    return _CONTROL.sub(" ", text).strip()[:limit]


@dataclass(frozen=True)
class Notice:
    key: str
    kind: str          # success, error, warning or info
    title: str
    body: str
    scope: str         # "local" or "global"

    @staticmethod
    def field_error(message: str) -> "Notice":
        return Notice("field_error", "error", "Valor inválido", _clean(message, 200), "local")

    @staticmethod
    def saved(section_title: str) -> "Notice":
        return Notice(f"saved:{_clean(section_title)}", "success", "Configuração salva", f"{_clean(section_title)}: as alterações foram gravadas.", "global")

    @staticmethod
    def save_failed(section_title: str) -> "Notice":
        return Notice(f"save_failed:{_clean(section_title)}", "error", "Não foi possível salvar",
                      f"{_clean(section_title)}: nada foi gravado. Veja a mensagem na seção.", "global")

    @staticmethod
    def bridge_down() -> "Notice":
        return Notice("bridge_down", "warning", "Bridge indisponível", "O bridge não responde. O que depende dele não pode ser validado.", "global")

    @staticmethod
    def bridge_up() -> "Notice":
        return Notice("bridge_up", "success", "Bridge disponível", "O bridge voltou a responder.", "global")

    @staticmethod
    def session_ending() -> "Notice":
        return Notice("session_ending", "warning", "Sessão prestes a expirar", "Por inatividade, será preciso entrar de novo.", "global")

    @staticmethod
    def restart_needed(process: str) -> "Notice":
        name = "o bridge" if process == "bridge" else "o servidor MCP"
        return Notice(f"restart:{process}", "info", "Reinício necessário", f"Reinicie {name} para que as alterações valham.", "global")


class Notifier:
    """Decides, and hands a global notice to the browser or to the page. The service and the snackbar are given."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last: Dict[str, float] = {}
        self._native: Optional[bool] = None       # None: not asked yet

    def _repeats(self, key: str) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < REPEAT_SECONDS:
            return True
        self._last[key] = now
        if len(self._last) > 200:
            for old in [k for k, t in self._last.items() if now - t >= REPEAT_SECONDS]:
                del self._last[old]
        return False

    async def _try_native(self, notice: Notice, service: Any) -> bool:
        if service is None or self._native is False:
            return False
        try:
            if self._native is None:
                self._native = bool(await service.request_permissions())
                if not self._native:
                    return False
            await service.show(id=zlib.crc32(notice.key.encode()) & 0x7FFFFFFF, title=notice.title, body=notice.body)
            return True
        except Exception:                          # a browser without support, a refused permission, a service that fails
            self._native = False
            return False

    async def deliver(self, notice: Notice, service: Any, show_snackbar: Callable[[Notice], Any]) -> str:
        """"inline" (the screen shows it where it belongs), "skipped" (said a moment ago), "native" or "snackbar"."""
        if notice.scope == "local":
            return "inline"
        if self._repeats(notice.key):
            return "skipped"
        if await self._try_native(notice, service):
            return "native"
        show_snackbar(notice)
        return "snackbar"
