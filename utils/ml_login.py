"""
utils/ml_login.py — Login do Mercado Livre numa página Playwright já aberta.

Compartilhado por dois chamadores:

  * ``scripts/setup_local_profile.py --auto`` — o setup interativo do perfil;
  * ``scrapers/mercado_livre.py`` — o **relogin automático** do turno quando a
    sessão do perfil expira (Nov/2026).

Por que o relogin dentro do turno (incidente de 31/10–03/11/2026)
------------------------------------------------------------------
A sessão do ML no perfil dedicado expirou TRÊS vezes em duas semanas
(19–21/10, 25–27/10 e 31/10–03/11). Em todas o perfil continuava com o cookie
de login e o ML cobrava login/verificação em toda SERP: o FREIO parava a
coleta depois de 4 keywords e o ML caía de ~12 mil linhas/turno para 0–700.
O diagnóstico já saía certo no livro-razão (``sessão: EXPIRADA``), mas o
conserto dependia de alguém relogar o perfil na mão — e a coleta ficava dias
magra até isso acontecer.

O que este módulo NÃO faz: atravessar 2FA, código por e-mail ou verificação de
dispositivo. Esses desafios existem para exigir um humano; quando aparecem o
resultado é ``DESAFIO`` e quem chama devolve o caso para o relogin manual.
"""

from __future__ import annotations

import os
from typing import Any, Tuple

# Abrimos a HOME/rota de conta, não uma rota de login hardcoded: o ML troca o
# caminho do fluxo (`/jms/mlb/lgz/msl/login` virou 404 em 31/07/2026) e uma URL
# morta trava o login inteiro. A home sempre existe e o "Entre" leva ao fluxo
# VIGENTE.
ML_HOME = "https://www.mercadolivre.com.br/"

# Rota de conta: quando anônimo (ou com sessão recusada), o ML redireciona para
# o login atual.
ML_ACCOUNT_URL = "https://myaccount.mercadolivre.com.br/"

ML_EMAIL_FIELD = "input[name='user_id'], input#user_id, input[type='email']"
ML_PASSWORD_FIELD = "input[name='password'], input#password, input[type='password']"

# Campo de código (2FA / e-mail / SMS) — sinal de desafio que exige humano.
ML_CODE_FIELD = (
    "input[autocomplete='one-time-code'], input[name='code'], "
    "input[name*='code' i]"
)

# Candidatos ao link de login da home, do mais explícito ao mais genérico —
# testados UM A UM (não em união com `.first`, que entrega quem vier primeiro
# no DOM, podendo ser um card promocional apontando para a rota velha).
# Todos excluem `/msl/`: é justamente o caminho que virou 404 em 31/07/2026.
ML_LOGIN_LINK_SELECTORS = (
    "a[data-link-id='login']",
    "a[href*='/lgz/']:not([href*='/msl/'])",
    "a:has-text('Entre'):not([href*='/msl/'])",
    "a:has-text('Entrar'):not([href*='/msl/'])",
    "a[href*='login']:not([href*='/msl/'])",
)

# Resultados de `fazer_login_ml`.
ENVIADO = "ENVIADO"            # credenciais aceitas, nenhum desafio na tela
DESAFIO = "DESAFIO"            # código/2FA/verificação ou senha recusada
FORM_AUSENTE = "FORM_AUSENTE"  # não achei o formulário de login
SEM_CREDENCIAIS = "SEM_CREDENCIAIS"
ERRO = "ERRO"


def credenciais_ml() -> Tuple[str, str]:
    """(e-mail, senha) do ``.env`` (ML_EMAIL / ML_PASSWORD); vazios se faltar."""
    return (
        os.getenv("ML_EMAIL", "").strip(),
        os.getenv("ML_PASSWORD", "").strip(),
    )


def _visivel(page: Any, seletor: str, timeout: int) -> bool:
    """True se o primeiro elemento do seletor fica visível dentro do prazo."""
    try:
        page.locator(seletor).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


def tem_campo_email(page: Any) -> bool:
    """True se o formulário de login (campo de e-mail/usuário) está visível."""
    return _visivel(page, ML_EMAIL_FIELD, 8_000)


def abrir_login_ml(page: Any) -> bool:
    """
    Leva a página até o formulário de login VIGENTE do Mercado Livre.

    Nada de rota fixa: tenta a rota de conta (que redireciona para o login
    atual quando a sessão não vale) e, se não der, percorre os candidatos a
    link "Entre" da home até um deles revelar o formulário.

    Returns:
        True se o campo de e-mail está visível na página.
    """
    try:
        page.goto(ML_ACCOUNT_URL, wait_until="domcontentloaded", timeout=45_000)
    except Exception:
        pass
    if tem_campo_email(page):
        return True

    for seletor in ML_LOGIN_LINK_SELECTORS:
        try:
            page.goto(ML_HOME, wait_until="domcontentloaded", timeout=45_000)
            page.locator(seletor).first.click(timeout=6_000)
            page.wait_for_load_state("domcontentloaded")
        except Exception:
            continue
        if tem_campo_email(page):
            return True
    return False


def _pausa(page: Any, ms: int) -> None:
    """Espera sem `time.sleep` (a página Playwright já tem o próprio relógio)."""
    try:
        page.wait_for_timeout(ms)
    except Exception:
        pass


def fazer_login_ml(page: Any, email: str, senha: str) -> str:
    """
    Preenche e envia o login do ML (e-mail → senha) na página dada.

    Nunca loga as credenciais. Depois do envio, olha a tela: campo de código
    ou o de senha ainda visível = ``DESAFIO`` (2FA, verificação de dispositivo
    ou senha recusada — em todos os casos, coisa de humano).

    Args:
        page:  página Playwright (aba do Chrome local ou do setup).
        email: ML_EMAIL.
        senha: ML_PASSWORD.

    Returns:
        Um de ENVIADO, DESAFIO, FORM_AUSENTE, SEM_CREDENCIAIS, ERRO.
    """
    if not email or not senha:
        return SEM_CREDENCIAIS
    if not abrir_login_ml(page):
        return FORM_AUSENTE
    try:
        campo_email = page.locator(ML_EMAIL_FIELD).first
        campo_email.click(timeout=10_000)
        campo_email.type(email, delay=110)
        page.locator("button[type='submit']").first.click(timeout=10_000)
        page.wait_for_load_state("domcontentloaded")
        _pausa(page, 2_500)

        if not _visivel(page, ML_PASSWORD_FIELD, 15_000):
            # Sem campo de senha depois do e-mail: o ML desviou para código
            # por e-mail/app ou verificação — não há o que preencher.
            return DESAFIO

        campo_senha = page.locator(ML_PASSWORD_FIELD).first
        campo_senha.click(timeout=15_000)
        campo_senha.type(senha, delay=110)
        page.locator("button[type='submit']").first.click(timeout=10_000)
        page.wait_for_load_state("domcontentloaded")
        _pausa(page, 3_000)
    except Exception:
        return ERRO

    if _visivel(page, ML_CODE_FIELD, 2_000) or _visivel(page, ML_PASSWORD_FIELD, 1_000):
        return DESAFIO
    return ENVIADO
