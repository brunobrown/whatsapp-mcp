"""Signing in, setting the first password, changing it, signing out.

The password is checked by AuthStore, the guessing is slowed by Throttle (an attempt made while waiting is refused
without being checked), and the session is whatever has sign_in and sign_out. The messages say what to do and
never why a password was wrong. No screen here.
"""
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ui.core.auth import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, AuthStore
from ui.core.throttle import FAILURE_MESSAGE, Throttle, wait_message

CODE_REQUEST_INTERVAL = 30          # seconds between two codes asked for
_WEAK = f"A senha precisa ter de {MIN_PASSWORD_LENGTH} a {MAX_PASSWORD_LENGTH} caracteres."
_BAD_CODE = "Código inválido ou vencido. O código está no log do bridge; peça um novo se for preciso."


@dataclass
class LoginResult:
    ok: bool
    message: Optional[str] = None
    wait: Optional[float] = None


@dataclass
class CodeResult:
    announced: bool
    code: Optional[str] = None
    message: Optional[str] = None


class LoginFlow:
    def __init__(self, auth: AuthStore, throttle: Throttle, session, client_ip: Optional[str], announce: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic):
        self._auth, self._throttle, self._session, self._ip, self._announce, self._clock = auth, throttle, session, client_ip, announce, clock
        self._last_code = -CODE_REQUEST_INTERVAL

    def mode(self) -> str:
        state = self._auth.state()
        return "setup" if state == "absent" else "login" if state == "set" else "broken"

    @staticmethod
    def broken_message() -> str:
        return "O arquivo ui_auth.json está danificado. Remova-o da pasta de configuração e reinicie o bridge para criar a senha de novo."

    def _held_back(self) -> Optional[LoginResult]:
        wait = self._throttle.wait(self._ip)
        return LoginResult(False, wait_message(wait), wait) if wait else None

    def _failed(self, message: str) -> LoginResult:
        self._throttle.failure(self._ip)
        return LoginResult(False, message, self._throttle.wait(self._ip))

    def login(self, password: str) -> LoginResult:
        if self.mode() == "setup":
            return LoginResult(False, "Ainda não há senha: use o código do log do bridge para criar a senha.")
        if held := self._held_back():
            return held
        if not self._auth.verify(password):
            return self._failed(FAILURE_MESSAGE)
        self._throttle.success(self._ip)
        self._session.sign_in()
        return LoginResult(True)

    def request_code(self) -> CodeResult:
        if self.mode() != "setup":
            return CodeResult(False, message="A senha já existe.")
        remaining = self._last_code + CODE_REQUEST_INTERVAL - self._clock()
        if remaining > 0:
            return CodeResult(False, message=f"Aguarde {int(remaining) + 1} segundos para pedir outro código.")
        code = self._auth.start_setup()
        self._last_code = self._clock()
        self._announce(code)
        return CodeResult(True, code=code)

    def setup(self, code: str, password: str, confirm: str) -> LoginResult:
        if self.mode() != "setup":
            return LoginResult(False, "Já existe uma senha. Entre com ela.")
        if held := self._held_back():
            return held
        if password != confirm:
            return LoginResult(False, "As senhas não são iguais.")
        outcome = self._auth.create(code, password)
        if outcome.reason == "weak_password":
            return LoginResult(False, _WEAK)
        if not outcome.ok:
            return self._failed(_BAD_CODE if outcome.reason == "invalid_code" else "Não foi possível gravar a senha.")
        self._throttle.success(self._ip)
        self._session.sign_in()
        return LoginResult(True)

    def change_password(self, current: str, new: str, confirm: str) -> LoginResult:
        if held := self._held_back():
            return held
        if new != confirm:
            return LoginResult(False, "As senhas novas não são iguais.")
        outcome = self._auth.change(current, new)
        if outcome.reason == "wrong_password":
            return self._failed(FAILURE_MESSAGE)
        if outcome.reason == "weak_password":
            return LoginResult(False, _WEAK)
        if not outcome.ok:
            return LoginResult(False, "Não foi possível gravar a nova senha.")
        self._throttle.success(self._ip)
        return LoginResult(True)

    def sign_out(self) -> None:
        self._session.sign_out()
