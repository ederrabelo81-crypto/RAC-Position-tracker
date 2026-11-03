"""
tests/test_ml_login.py — fluxo de login do ML compartilhado (utils/ml_login.py).

O relogin automático do turno (Nov/2026) usa este fluxo; o desfecho decide se a
coleta segue ou volta para o relogin manual. Desafio (código/2FA) NUNCA pode
sair como ENVIADO — senão o diagnóstico diria que a sessão foi renovada.

Rode: pytest tests/test_ml_login.py
"""
import sys
from pathlib import Path
from typing import Any, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import ml_login  # noqa: E402


class _Loc:
    def __init__(self, page: "_Page", sel: str) -> None:
        self.page, self.sel = page, sel

    @property
    def first(self) -> "_Loc":
        return self

    def wait_for(self, state: Any = None, timeout: Any = None) -> None:
        if self.sel not in self.page.visiveis:
            raise TimeoutError(self.sel)

    def click(self, timeout: Any = None) -> None:
        self.page.cliques.append(self.sel)
        if self.sel == "button[type='submit']":
            self.page.avanca()

    def type(self, texto: str, delay: Any = None) -> None:
        self.page.digitado.append((self.sel, texto))


class _Page:
    """Login em etapas: e-mail → senha → `final` (conjunto de campos visíveis)."""

    def __init__(self, etapas: List[Set[str]]) -> None:
        self.etapas = etapas
        self.visiveis = etapas[0]
        self.cliques: List[str] = []
        self.digitado: List[Any] = []
        self.url = "https://www.mercadolivre.com.br/"
        self.url_final = "https://www.mercadolivre.com.br/"

    def avanca(self) -> None:
        self.etapas = self.etapas[1:] or self.etapas
        self.visiveis = self.etapas[0]
        if len(self.etapas) == 1:
            self.url = self.url_final

    def goto(self, url: str, **_k: Any) -> None:
        self.url = url

    def locator(self, sel: str) -> _Loc:
        return _Loc(self, sel)

    def wait_for_load_state(self, *_a: Any, **_k: Any) -> None:
        pass

    def wait_for_timeout(self, _ms: int) -> None:
        pass


E, S, C = ml_login.ML_EMAIL_FIELD, ml_login.ML_PASSWORD_FIELD, ml_login.ML_CODE_FIELD


def test_login_completo_e_enviado() -> None:
    page = _Page([{E}, {S}, set()])
    assert ml_login.fazer_login_ml(page, "a@b.c", "x") == ml_login.ENVIADO
    assert page.digitado == [(E, "a@b.c"), (S, "x")]


def test_continua_no_fluxo_de_login_nao_e_enviado() -> None:
    # nenhum campo conhecido, mas a URL não saiu do login: desafio desconhecido
    page = _Page([{E}, {S}, set()])
    page.url_final = "https://www.mercadolivre.com.br/jms/mlb/lgz/challenge"
    assert ml_login.fazer_login_ml(page, "a@b.c", "x") == ml_login.DESAFIO


def test_codigo_depois_da_senha_e_desafio() -> None:
    assert ml_login.fazer_login_ml(_Page([{E}, {S}, {C}]), "a@b.c", "x") == ml_login.DESAFIO


def test_senha_recusada_e_desafio() -> None:
    # a tela voltou com o campo de senha: senha errada ou verificação extra
    assert ml_login.fazer_login_ml(_Page([{E}, {S}, {S}]), "a@b.c", "x") == ml_login.DESAFIO


def test_sem_campo_de_senha_depois_do_email_e_desafio() -> None:
    # o ML desviou para código por e-mail/app logo após o usuário
    assert ml_login.fazer_login_ml(_Page([{E}, {C}]), "a@b.c", "x") == ml_login.DESAFIO


def test_sem_formulario() -> None:
    assert ml_login.fazer_login_ml(_Page([set()]), "a@b.c", "x") == ml_login.FORM_AUSENTE


def test_sem_credenciais_nao_navega() -> None:
    page = _Page([{E}])
    assert ml_login.fazer_login_ml(page, "", "x") == ml_login.SEM_CREDENCIAIS
    assert page.cliques == []


def test_credenciais_vem_do_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("ML_EMAIL", " a@b.c ")
    monkeypatch.setenv("ML_PASSWORD", "x")
    assert ml_login.credenciais_ml() == ("a@b.c", "x")
