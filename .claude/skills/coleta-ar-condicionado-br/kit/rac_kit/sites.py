"""Configuração por site: URLs de busca, identidade do produto e sinais de gate.

Cada ``Site`` descreve só o que muda de um marketplace para outro. Para um site
novo (loja própria, dealer), use ``site_generico`` ou registre um ``Site`` aqui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Pattern, Tuple
from urllib.parse import quote, quote_plus, urlsplit


@dataclass(frozen=True)
class Site:
    """O que o coletor precisa saber sobre um site.

    Attributes:
        chave: Identificador curto usado na linha de comando.
        nome: Nome gravado na coluna ``Plataforma``.
        home: URL da home (aquecimento e Referer da 1ª busca).
        url_busca: ``(keyword, pagina, itens_ja_vistos) -> URL`` da busca.
        re_produto: Regex que reconhece link de PRODUTO no ``href``. Com grupos,
            o id é a junção dos grupos; sem grupos, é o caminho da URL.
        prefixo_id: Prefixo recolocado no id (ex.: "MLB").
        re_patrocinado_href: Link de anúncio pago, quando o site o distingue.
        re_url_gate: URL que por si só já é tela de login/verificação.
        esperar_abck: True para sites atrás do Akamai (cookie ``_abck``).
        requer_login: True quando a busca só funciona com conta logada.
        min_links: Quantos produtos distintos provam que a página é uma SERP.
        intervalo: Pausa (mín, máx) em segundos entre páginas.
    """

    chave: str
    nome: str
    home: str
    url_busca: Callable[[str, int, int], str]
    re_produto: Pattern[str]
    prefixo_id: str = ""
    re_patrocinado_href: Optional[Pattern[str]] = None
    re_url_gate: Optional[Pattern[str]] = None
    esperar_abck: bool = False
    requer_login: bool = False
    min_links: int = 4
    intervalo: Tuple[float, float] = (4.0, 9.0)
    observacoes: str = field(default="", compare=False)

    def id_produto(self, href: Optional[str]) -> Optional[str]:
        """Id do produto dentro de um ``href``, ou None se não for link de produto."""
        if not href:
            return None
        m = self.re_produto.search(href)
        if not m:
            return None
        grupos = [g for g in m.groups() if g]
        if grupos:
            return self.prefixo_id + ".".join(grupos)
        caminho = urlsplit(href).path.rstrip("/").lower()
        return caminho or None


def _busca_ml(keyword: str, pagina: int, vistos: int) -> str:
    # O ML pagina por deslocamento (_Desde_N), e o tamanho da página muda:
    # use a contagem real de itens já vistos, nunca um passo fixo.
    slug = quote("-".join(keyword.split()), safe="")  # "/" não vira pasta
    base = f"https://lista.mercadolivre.com.br/{slug}"
    return base if pagina <= 1 else f"{base}_Desde_{vistos + 1}"


def _busca_amazon(keyword: str, pagina: int, vistos: int) -> str:
    return f"https://www.amazon.com.br/s?k={quote_plus(keyword)}&page={pagina}"


def _busca_magalu(keyword: str, pagina: int, vistos: int) -> str:
    base = f"https://www.magazineluiza.com.br/busca/{quote_plus(keyword)}/"
    return base if pagina <= 1 else f"{base}?page={pagina}"


def _busca_casasbahia(keyword: str, pagina: int, vistos: int) -> str:
    base = f"https://www.casasbahia.com.br/busca?q={quote_plus(keyword)}"
    return base if pagina <= 1 else f"{base}&page={pagina}"


def _busca_shopee(keyword: str, pagina: int, vistos: int) -> str:
    # A Shopee conta páginas a partir de zero.
    return f"https://shopee.com.br/search?keyword={quote_plus(keyword)}&page={pagina - 1}"


def _busca_leroy(keyword: str, pagina: int, vistos: int) -> str:
    base = f"https://www.leroymerlin.com.br/busca?term={quote_plus(keyword)}"
    return base if pagina <= 1 else f"{base}&page={pagina}"


SITES: Dict[str, Site] = {
    s.chave: s
    for s in (
        Site(
            chave="mercadolivre",
            nome="Mercado Livre",
            home="https://www.mercadolivre.com.br/",
            url_busca=_busca_ml,
            re_produto=re.compile(
                r"(?:/p/|/|[?&#]wid=|item_id(?:%3A|:))MLB-?(\d{6,})", re.I
            ),
            prefixo_id="MLB",
            re_patrocinado_href=re.compile(r"click1\.mercadolivre|mclics", re.I),
            re_url_gate=re.compile(r"/gz/account-verification|/jms/.*/lgz/|/login", re.I),
            min_links=6,
            observacoes="Sessão logada no perfil evita o gate de login.",
        ),
        Site(
            chave="amazon",
            nome="Amazon",
            home="https://www.amazon.com.br/",
            url_busca=_busca_amazon,
            re_produto=re.compile(
                r"(?:/|%2F)(?:dp|gp/product|gp/aw/d)(?:/|%2F)([A-Z0-9]{10})(?:[/?%&]|$)"
            ),
            re_patrocinado_href=re.compile(r"/sspa/click|/gp/slredirect", re.I),
            re_url_gate=re.compile(r"/errors/validateCaptcha|/ap/signin", re.I),
            observacoes="A busca não mostra o vendedor: buy box só no PDP.",
        ),
        Site(
            chave="magalu",
            nome="Magalu",
            home="https://www.magazineluiza.com.br/",
            url_busca=_busca_magalu,
            re_produto=re.compile(r"/p/([a-z0-9]{6,})(?:[/?#]|$)", re.I),
            re_patrocinado_href=re.compile(r"[?&]ads=patrocinado", re.I),
            re_url_gate=re.compile(r"sacola\.magazineluiza|/cliente/login|/login", re.I),
            esperar_abck=True,
            observacoes="Akamai: aquecer a home antes da busca é obrigatório.",
        ),
        Site(
            chave="casasbahia",
            nome="Casas Bahia",
            home="https://www.casasbahia.com.br/",
            url_busca=_busca_casasbahia,
            re_produto=re.compile(r"/p/(\d{5,})(?:[/?#]|$)"),
            esperar_abck=True,
            observacoes="Akamai. O lojista de cada oferta fica no PDP.",
        ),
        Site(
            chave="shopee",
            nome="Shopee",
            home="https://shopee.com.br/",
            url_busca=_busca_shopee,
            re_produto=re.compile(r"(?:-i\.|/product/)(\d+)[./](\d+)(?:[/?#]|$)"),
            re_url_gate=re.compile(r"/buyer/login|/verify/", re.I),
            requer_login=True,
            intervalo=(6.0, 12.0),
            observacoes="Exige conta logada; cada resultado é a oferta de UMA loja.",
        ),
        Site(
            chave="leroy",
            nome="Leroy Merlin",
            home="https://www.leroymerlin.com.br/",
            url_busca=_busca_leroy,
            re_produto=re.compile(r"_(\d{6,})(?:[/?#]|$)"),
            observacoes="Preço e vendedor confiáveis só no PDP.",
        ),
    )
}


def site_generico(
    url_busca: str,
    nome: Optional[str] = None,
    re_produto: Optional[str] = None,
) -> Site:
    """Monta um ``Site`` para loja própria ou dealer a partir da URL de busca.

    Args:
        url_busca: Modelo com ``{q}`` (keyword) e, opcionalmente, ``{pagina}``.
            Ex.: ``https://www.minhaloja.com.br/busca?q={q}&page={pagina}``.
        nome: Nome gravado em ``Plataforma`` (padrão: o domínio).
        re_produto: Regex de link de produto. Padrão: o ``/slug/p`` das lojas
            VTEX; ajuste para outras plataformas.

    Returns:
        O ``Site`` pronto para o coletor.

    Raises:
        ValueError: Se o modelo não tiver ``{q}`` ou não for http(s).
    """
    if "{q}" not in url_busca:
        raise ValueError("url_busca precisa conter {q} no lugar da keyword")
    partes = urlsplit(url_busca.replace("{q}", "x").replace("{pagina}", "1"))
    if partes.scheme not in ("http", "https") or not partes.netloc:
        raise ValueError("url_busca precisa começar com http:// ou https://")
    home = f"{partes.scheme}://{partes.netloc}/"

    def _busca(keyword: str, pagina: int, vistos: int) -> str:
        return url_busca.replace("{q}", quote_plus(keyword)).replace(
            "{pagina}", str(pagina)
        )

    return Site(
        chave="generico",
        nome=nome or partes.netloc.removeprefix("www."),
        home=home,
        url_busca=_busca,
        re_produto=re.compile(re_produto or r"/[^/?#]+/p(?:[/?#]|$)", re.I),
        min_links=3,
    )
