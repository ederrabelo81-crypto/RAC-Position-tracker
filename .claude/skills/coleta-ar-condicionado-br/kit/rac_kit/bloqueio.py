"""Diagnóstico da página: é resultado de busca, bloqueio, login ou layout novo?

Regra central — EVIDÊNCIA antes de STRING. O HTML de uma busca normal contém
"faça login", "captcha" e scripts de verificação no cabeçalho, em modais
ocultos e em JSON de estado. Decidir bloqueio por ``"texto" in html`` descarta
páginas cheias de produto. Aqui a ordem é:

  1. a URL é de login/verificação  -> gate, sem discussão;
  2. a página tem links de produto -> é resultado, ignore qualquer frase;
  3. só então status, tamanho e frases decidem a causa.

As causas nunca têm a mesma cara, porque o conserto de cada uma é diferente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Set

from .sites import Site

OK = "ok"
LOGIN = "login"          # o site quer conta logada -> logar o perfil dedicado
BLOQUEIO = "bloqueio"    # antibot negou (403, página-toco) -> parar e esfriar
CAPTCHA = "captcha"      # desafio humano -> resolver na janela, à mão
VAZIA = "vazia"          # a busca realmente não tem resultado
LAYOUT = "layout"        # página carregou, mas nenhum produto reconhecido

_RE_HREF = re.compile(r'href="([^"]+)"')
_RE_BLOQUEIO = re.compile(
    r"access denied|errors\.edgesuite\.net|reference\s*#\s*[\d.a-f]+"
    r"|request blocked|pardon our interruption|acesso negado",
    re.I,
)
_RE_CAPTCHA = re.compile(
    r"g-recaptcha|recaptcha/api|px-captcha|hcaptcha|validateCaptcha"
    r"|n[aã]o sou um rob[oô]|digite os caracteres",
    re.I,
)
_RE_LOGIN = re.compile(
    r"para continuar,? acesse sua conta|fa[cç]a (?:seu )?login|entre na sua conta"
    r"|identifique-se|digite seu e-?mail|verifica[cç][aã]o de (?:conta|dispositivo)",
    re.I,
)
_RE_VAZIA = re.compile(
    r"n[aã]o encontramos (?:nenhum )?resultado|nenhum resultado"
    r"|n[aã]o h[aá] (?:an[uú]ncios|produtos)|sem resultados para",
    re.I,
)

# Página de bloqueio do Akamai e afins tem poucas centenas de bytes.
_TAMANHO_MINIMO = 1024


@dataclass(frozen=True)
class Veredito:
    """Resultado do diagnóstico de uma página."""

    causa: str
    detalhe: str
    produtos: int = 0

    @property
    def ok(self) -> bool:
        return self.causa == OK


def ids_de_produto(html: str, site: Site) -> Set[str]:
    """Ids de produto distintos nos ``href`` do HTML (não depende de classe CSS)."""
    ids: Set[str] = set()
    for m in _RE_HREF.finditer(html or ""):
        pid = site.id_produto(m.group(1).replace("&amp;", "&"))
        if pid:
            ids.add(pid)
    return ids


def diagnosticar(
    html: Optional[str],
    site: Site,
    url: str = "",
    status: Optional[int] = None,
) -> Veredito:
    """Classifica a página devolvida por uma busca.

    Args:
        html: HTML renderizado (``page.content()``).
        site: Configuração do site.
        url: URL final, depois de redirecionamentos.
        status: Código HTTP da navegação, quando conhecido.

    Returns:
        ``Veredito`` com a causa (``ok``, ``login``, ``bloqueio``, ``captcha``,
        ``vazia`` ou ``layout``) e uma frase explicando a evidência.
    """
    html = html or ""

    if site.re_url_gate and url and site.re_url_gate.search(url):
        return Veredito(LOGIN, f"URL de login/verificação: {url}")

    n = len(ids_de_produto(html, site))
    if n >= site.min_links:
        return Veredito(OK, f"{n} produtos distintos na página", n)

    if status in (403, 429):
        return Veredito(BLOQUEIO, f"HTTP {status}", n)
    if len(html) < _TAMANHO_MINIMO:
        return Veredito(BLOQUEIO, f"página com {len(html)} bytes", n)
    if _RE_BLOQUEIO.search(html):
        return Veredito(BLOQUEIO, "página de bloqueio do antibot", n)
    if _RE_CAPTCHA.search(html):
        return Veredito(CAPTCHA, "desafio de CAPTCHA na página", n)
    if _RE_LOGIN.search(html):
        return Veredito(LOGIN, "página pede login e não tem produto", n)
    if _RE_VAZIA.search(html):
        return Veredito(VAZIA, "o site informa que não há resultado", n)
    if n:
        # Poucos produtos e nenhuma evidência contrária: busca curta, não bloqueio.
        return Veredito(OK, f"{n} produtos distintos (busca curta)", n)
    return Veredito(
        LAYOUT,
        "página carregou sem nenhum link de produto reconhecido — "
        "o site mudou ou a regex de produto não serve",
        0,
    )
