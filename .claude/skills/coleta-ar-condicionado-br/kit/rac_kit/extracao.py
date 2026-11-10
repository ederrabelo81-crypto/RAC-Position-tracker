"""Extração ESTRUTURAL dos cards de uma busca e leitura da buy box no PDP.

Não usa nome de classe CSS, que os marketplaces trocam sem aviso. A âncora é o
que uma página de resultado sempre tem: links para produtos, na ordem em que
aparecem. O card de cada produto é o maior bloco em volta do link que ainda
fala de UM produto só.

O que isto entrega bem: posição, título, link, id e marcação de patrocinado.
O que é heurístico: preço (o card mistura preço riscado, à vista e parcela) e
vendedor (a maioria das buscas não diz quem vende). Para buy box, abra o PDP.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .sites import Site
from .texto import parse_price_brazil

_RE_PRECO = re.compile(r"R\$\s*\d[\d.]*(?:,\d{1,2})?")
# "10x de R$ 199,90" é parcela, não preço.
_RE_PARCELA_ANTES = re.compile(r"\d{1,2}\s*x\s*(?:de\s*)?$", re.I)
# Início de palavra: sem isso "old" casaria com "font-bold" e apagaria o preço.
_RE_RISCADO = re.compile(
    r"(?<![a-z])(?:old|original|previous|strike|riscad|list-?price|de-por)", re.I
)
_RE_PATROCINADO = re.compile(r"\bpatrocinado\b|\ban[uú]ncio\b|\bsponsored\b", re.I)
_RE_VENDIDO_POR = re.compile(
    r"vendido(?:\s+e\s+entregue)?\s+por[:\s]*\n?\s*([^\n]{2,80})", re.I
)
_RE_SO_PRECO = re.compile(r"^\s*(?:R\$|\d+\s*x|\d+%)", re.I)


def _texto(tag: Tag, sep: str = " ") -> str:
    return re.sub(r"[ \t\xa0]+", " ", tag.get_text(sep)).strip()


def _ids_no_bloco(bloco: Tag, site: Site) -> set:
    return {
        pid
        for a in bloco.find_all("a", href=True)
        if (pid := site.id_produto(a["href"]))
    }


def _card_de(ancora: Tag, pid: str, site: Site) -> Tag:
    """Maior ancestral do link que contém links de um único produto."""
    card: Tag = ancora
    for pai in ancora.parents:
        if not isinstance(pai, Tag) or pai.name in ("body", "html", "[document]"):
            break
        if _ids_no_bloco(pai, site) != {pid}:
            break
        card = pai
    return card


def _titulo(card: Tag, ancoras: List[Tag]) -> Optional[str]:
    candidatos: List[str] = []
    for h in card.find_all(["h2", "h3", "h4"]):
        candidatos.append(_texto(h))
    candidatos.extend(sorted((_texto(a) for a in ancoras), key=len, reverse=True))
    for img in card.find_all("img", alt=True):
        candidatos.append(img["alt"].strip())
    for a in ancoras:
        candidatos.append((a.get("title") or "").strip())
    for c in candidatos:
        if 12 <= len(c) <= 300 and not _RE_SO_PRECO.match(c):
            return c
    return None


def _preco(card: Tag) -> Optional[float]:
    """Primeiro preço do card que não é riscado nem parcela."""
    copia = BeautifulSoup(str(card), "html.parser")
    for morto in copia.find_all(["s", "del", "strike"]):
        morto.decompose()
    for tag in copia.find_all(class_=_RE_RISCADO):
        tag.decompose()
    # Sem separador: o ML quebra "R$", "1.994", "," e "91" em spans distintos.
    texto = copia.get_text("").replace("\xa0", " ")
    for m in _RE_PRECO.finditer(texto):
        if _RE_PARCELA_ANTES.search(texto[max(0, m.start() - 12):m.start()]):
            continue
        valor = parse_price_brazil(m.group(0))
        if valor:
            return valor
    return None


def _iter_jsonld(soup: BeautifulSoup) -> Iterator[Dict[str, Any]]:
    """Todos os objetos JSON-LD da página, achatando @graph e ItemList."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            dado = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        pilha = [dado]
        while pilha:
            item = pilha.pop()
            if isinstance(item, list):
                pilha.extend(item)
            elif isinstance(item, dict):
                yield item
                for chave in ("@graph", "itemListElement", "item"):
                    if chave in item:
                        pilha.append(item[chave])


def _e_produto(obj: Dict[str, Any]) -> bool:
    """True para ``@type`` Product, em qualquer grafia do schema.org.

    Aceita "Product", "https://schema.org/Product" e listas mistas.
    """
    tipos = obj.get("@type")
    tipos = tipos if isinstance(tipos, list) else [tipos]
    return any(
        isinstance(t, str) and t.rstrip("/").rsplit("/", 1)[-1] == "Product"
        for t in tipos
    )


def _oferta_jsonld(obj: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    """(preço, vendedor) de um Product do schema.org.

    Com várias ofertas, o vendedor só é devolvido se todas apontarem o mesmo:
    escolher "a primeira" seria eleger um vencedor que a página não declara.
    """
    ofertas = obj.get("offers")
    lista = ofertas if isinstance(ofertas, list) else [ofertas]
    lista = [o for o in lista if isinstance(o, dict)]
    if not lista:
        return None, None
    bruto = lista[0].get("price", lista[0].get("lowPrice"))
    preco = parse_price_brazil(str(bruto)) if bruto not in (None, "") else None
    nomes = set()
    for oferta in lista:
        vendedor = oferta.get("seller")
        nome = vendedor.get("name") if isinstance(vendedor, dict) else None
        if isinstance(nome, str) and nome.strip():
            nomes.add(nome.strip())
    return preco, (nomes.pop() if len(nomes) == 1 else None)


def _precos_jsonld(soup: BeautifulSoup, site: Site) -> Dict[str, float]:
    precos: Dict[str, float] = {}
    for obj in _iter_jsonld(soup):
        if not _e_produto(obj):
            continue
        pid = site.id_produto(str(obj.get("url") or ""))
        preco, _ = _oferta_jsonld(obj)
        if pid and preco:
            precos.setdefault(pid, preco)
    return precos


def extrair_cards(html: str, site: Site, url_pagina: str = "") -> List[Dict[str, Any]]:
    """Lê os produtos de uma página de busca, na ordem em que aparecem.

    Args:
        html: HTML renderizado da busca.
        site: Configuração do site.
        url_pagina: URL da página, para resolver links relativos.

    Returns:
        Uma lista de dicts com ``id_produto``, ``titulo``, ``url``, ``preco``,
        ``patrocinado``, ``seller`` e as três posições (geral, orgânica,
        patrocinada). Campo que a página não informa fica ``None`` — ausência
        de dado é dado ausente, nunca um palpite.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    precos_ld = _precos_jsonld(soup, site)

    por_id: Dict[str, List[Tag]] = {}
    for a in soup.find_all("a", href=True):
        pid = site.id_produto(a["href"])
        if pid:
            por_id.setdefault(pid, []).append(a)

    cards: List[Dict[str, Any]] = []
    organica = patrocinada = 0
    for geral, (pid, ancoras) in enumerate(por_id.items(), start=1):
        card = _card_de(ancoras[0], pid, site)
        hrefs = [a["href"] for a in ancoras]
        pago = bool(
            (site.re_patrocinado_href
             and any(site.re_patrocinado_href.search(h) for h in hrefs))
            or _RE_PATROCINADO.search(_texto(card))
        )
        if pago:
            patrocinada += 1
        else:
            organica += 1
        vendido = _RE_VENDIDO_POR.search(card.get_text("\n"))
        # Prefira o link limpo ao de rastreio de anúncio.
        limpos = [
            h for h in hrefs
            if not (site.re_patrocinado_href and site.re_patrocinado_href.search(h))
        ]
        cards.append({
            "id_produto": pid,
            "titulo": _titulo(card, ancoras),
            "url": urljoin(url_pagina or site.home, (limpos or hrefs)[0]),
            "preco": precos_ld.get(pid) or _preco(card),
            "patrocinado": pago,
            "seller": vendido.group(1).strip(" .") if vendido else None,
            "posicao_geral": geral,
            "posicao_organica": None if pago else organica,
            "posicao_patrocinada": patrocinada if pago else None,
        })
    return cards


def ler_buy_box(html: str) -> Tuple[Optional[str], Optional[str]]:
    """Quem vende o produto, lido na página do produto (PDP).

    O rótulo impresso na tela ("Vendido por", "Vendido e entregue por") manda:
    é o que o comprador vê. O JSON-LD entra só como segunda fonte, e a origem
    vai junto para quem lê saber o quanto confiar.

    Args:
        html: HTML renderizado do PDP.

    Returns:
        ``(vendedor, fonte)`` com fonte ``"rotulo"`` ou ``"json-ld"``, ou
        ``(None, None)`` quando a página não diz quem vende.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for lixo in soup.find_all(["script", "style", "noscript"]):
        if lixo.get("type") != "application/ld+json":
            lixo.extract()
    m = _RE_VENDIDO_POR.search(soup.get_text("\n"))
    if m:
        # "Vendido por LojaX e enviado pela Amazon": o nome acaba antes da entrega.
        nome = re.split(
            r"\s{2,}|\s[|•]\s|\s+e\s+(?:enviado|entregue)\s+(?:por|pel[ao])\b",
            m.group(1).strip(), flags=re.I,
        )[0].strip(" .")
        if nome:
            return nome, "rotulo"
    soup = BeautifulSoup(html or "", "html.parser")
    for obj in _iter_jsonld(soup):
        if _e_produto(obj):
            _, nome = _oferta_jsonld(obj)
            if nome:
                return nome, "json-ld"
    return None, None
