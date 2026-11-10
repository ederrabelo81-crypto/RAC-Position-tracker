"""
utils/leroy_ads.py — Produtos patrocinados da Leroy Merlin (busca e PDP).

Por que existe (Nov/2026)
-------------------------
A coleta da Leroy lê a busca pela API Algolia, que só devolve o resultado
orgânico: anúncio nenhum passa por ela. Por isso a Leroy nunca apareceu no
📣 Share of Voice Patrocinado — toda linha saía com ``Patrocinado? = Não``.

A Leroy mostra anúncio em dois lugares, e nenhum deles é intercalado no grid
orgânico:

* **busca** — uma prateleira "Produtos patrocinados" na página de resultado;
* **PDP** — outra prateleira de patrocinados dentro da página do produto.

As duas só existem no HTML renderizado. Este módulo lê esse HTML e devolve os
anúncios na ordem em que aparecem, sem depender de classe CSS (a Leroy troca as
classes do styled-components a cada deploy): a âncora é o TEXTO do rótulo
("Produtos patrocinados", "Patrocinado") e a identidade é o id numérico do slug
do produto (``…-tcl_92311464``), o mesmo que ``utils.offer_identity`` usa.

Regras
------
* Só entra anúncio com **evidência** — rótulo de patrocinado na prateleira ou
  no card. Prateleira sem rótulo ("Quem viu, viu também") não é mídia.
* A prateleira é o MENOR ancestral do rótulo que contém link de produto: subir
  além disso engoliria o grid orgânico inteiro como se fosse anúncio.
* Função pura (HTML → lista); quem navega e grava é o scraper.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from utils.text import parse_price

__all__ = [
    "SponsoredItem",
    "SPONSORED_LABEL_PATTERN",
    "extract_sponsored",
    "leroy_product_id",
]

_BASE_URL = "https://www.leroymerlin.com.br/"

#: Rótulos de mídia paga. Fonte única: o scraper usa o mesmo padrão para
#: esperar a prateleira hidratar (``wait_for_function``).
SPONSORED_LABEL_PATTERN = r"patrocinad[oa]s?|an[uú]ncios?\s+patrocinad"
_RE_LABEL = re.compile(SPONSORED_LABEL_PATTERN, re.IGNORECASE)

# Rótulo é texto CURTO. Um parágrafo que só cita a palavra (descrição de
# produto, política de anúncios no rodapé) não é cabeçalho de prateleira.
_MAX_LABEL_CHARS = 40

# Id do produto no slug da Leroy: ``/<slug>_<6+ dígitos>``.
_RE_PRODUCT_ID = re.compile(r"_(\d{6,})(?:[/?#]|$)")
_RE_PRICE = re.compile(r"R\$\s*[\d.\s\xa0]+,\d{2}")

# Teto de subida a partir do rótulo — a prateleira fica a poucos níveis dele.
_MAX_ANCESTOR_DEPTH = 12


@dataclass
class SponsoredItem:
    """Um anúncio na ordem em que aparece na prateleira.

    Attributes:
        slot: posição dentro da superfície (1 = primeiro anúncio).
        product_id: id numérico do produto (slug ``_<id>``).
        url: URL absoluta do PDP anunciado.
        title: título do card, se legível.
        price: preço exibido no card, se legível.
    """

    slot: int
    product_id: str
    url: str
    title: Optional[str] = None
    price: Optional[float] = None


def leroy_product_id(url: Optional[str]) -> Optional[str]:
    """Id numérico do produto a partir da URL da Leroy (``…_92311464``).

    Args:
        url: URL absoluta ou relativa do PDP.

    Returns:
        O id, ou None quando a URL não segue o padrão de produto.
    """
    if not url:
        return None
    m = _RE_PRODUCT_ID.search(url.split("?", 1)[0] + "?")
    return m.group(1) if m else None


def _product_links(node: Tag) -> List[Tag]:
    return [
        a for a in node.find_all("a", href=True)
        if leroy_product_id(a["href"])
    ]


def _distinct_ids(links: Iterable[Tag]) -> List[str]:
    vistos: Dict[str, None] = {}
    for a in links:
        pid = leroy_product_id(a["href"])
        if pid:
            vistos.setdefault(pid, None)
    return list(vistos)


def _is_label(text: str) -> bool:
    texto = " ".join(text.split())
    return bool(texto) and len(texto) <= _MAX_LABEL_CHARS and bool(_RE_LABEL.search(texto))


def _label_nodes(soup: BeautifulSoup) -> List[Tag]:
    """Elementos cujo texto PRÓPRIO é um rótulo de patrocinado."""
    nodes: List[Tag] = []
    for s in soup.find_all(string=_RE_LABEL):
        if not isinstance(s, NavigableString) or not _is_label(str(s)):
            continue
        parent = s.parent
        if parent is None or parent.name in ("script", "style", "noscript", "title"):
            continue
        nodes.append(parent)
    # aria-label / title sem texto visível (chip só com ícone).
    for el in soup.find_all(attrs={"aria-label": _RE_LABEL}):
        if _is_label(el.get("aria-label", "")):
            nodes.append(el)
    return nodes


def _card_for(link: Tag, product_id: str) -> Tag:
    """Maior ancestral do link que ainda contém UM produto só (o card)."""
    card: Tag = link
    node = link.parent
    for _ in range(_MAX_ANCESTOR_DEPTH):
        if not isinstance(node, Tag) or node.name in ("body", "html", "[document]"):
            break
        if _distinct_ids(_product_links(node)) != [product_id]:
            break
        card = node
        node = node.parent
    return card


def _title_of(card: Tag, link: Tag) -> Optional[str]:
    for el in card.select("h2, h3, h4, [data-testid*='title' i], [data-testid*='name' i]"):
        t = " ".join(el.get_text(" ", strip=True).split())
        if t and not _is_label(t):
            return t
    for attr in ("title", "aria-label"):
        t = (link.get(attr) or "").strip()
        if t and not _is_label(t):
            return t
    img = card.find("img", alt=True)
    if img and img["alt"].strip():
        return img["alt"].strip()
    t = " ".join(link.get_text(" ", strip=True).split())
    return t or None


def _price_of(card: Tag) -> Optional[float]:
    m = _RE_PRICE.search(card.get_text(" ", strip=True))
    return parse_price(m.group(0)) if m else None


_RE_HEADING = ("h1", "h2", "h3", "h4")
# Prateleira de anúncio tem poucos produtos; um bloco "rotulado" com mais que
# isto é o grid orgânico engolido por um ancestral grande demais.
_MAX_SHELF_ITEMS = 24


def _opens_other_section(node: Tag) -> bool:
    """O irmão abre OUTRA seção da página?

    Sim quando o primeiro elemento relevante dele (em ordem de documento) é um
    cabeçalho que não é rótulo de patrocinado — "Resultados", "Mais
    produtos". Título de card (``<h3>`` depois do link, ou dentro do ``<a>``)
    não conta: ele vem junto de um produto, não antes dele.
    """
    candidatos = [node] if node.name in _RE_HEADING else []
    candidatos += node.find_all(lambda t: t.name in _RE_HEADING or (
        t.name == "a" and t.has_attr("href") and leroy_product_id(t["href"])
    ))
    for el in candidatos:
        if el.name == "a":
            return False
        if el.find_parent("a") is not None:
            continue
        texto = " ".join(el.get_text(" ", strip=True).split())
        if texto and not _is_label(texto):
            return True
    return False


def _shelf_links(label: Tag) -> List[Tag]:
    """Links de produto da prateleira (ou do card) que o rótulo encabeça.

    Sobe do rótulo até o primeiro ancestral com link de produto. Se esse
    ancestral é um card (1 produto), o card inteiro é o anúncio. Se tem vários,
    a prateleira vai do ramo do rótulo até o próximo cabeçalho de OUTRA seção —
    é o que impede um ``<main>`` com o rótulo e o grid orgânico lado a lado de
    virar "tudo patrocinado".
    """
    branch: Tag = label
    node = label.parent
    for _ in range(_MAX_ANCESTOR_DEPTH):
        if not isinstance(node, Tag) or node.name in ("body", "html", "[document]"):
            return []
        if _product_links(node):
            break
        branch, node = node, node.parent
    else:
        return []

    todos = _product_links(node)
    if len(_distinct_ids(todos)) <= 1:
        return todos

    links: List[Tag] = []
    for sib in branch.next_siblings:
        if not isinstance(sib, Tag):
            continue
        if _opens_other_section(sib):
            break
        links.extend(_product_links(sib))
    # Sem link depois do rótulo não há como saber qual pedaço do bloco é a
    # prateleira — melhor perder o anúncio do que gravar o grid como mídia.
    return links if len(_distinct_ids(links)) <= _MAX_SHELF_ITEMS else []


def extract_sponsored(html: Optional[str], base_url: str = _BASE_URL) -> List[SponsoredItem]:
    """Anúncios de uma página da Leroy (busca ou PDP), na ordem de exibição.

    Duas formas de evidência, combinadas na ordem do documento:

    1. **Prateleira rotulada** — um cabeçalho "Produtos patrocinados": todos os
       produtos do menor bloco que o contém são anúncios.
    2. **Card rotulado** — um chip "Patrocinado" dentro de um card: só aquele
       produto (o bloco que o contém tem UM produto só).

    Args:
        html: HTML renderizado da página (``page.content()``).
        base_url: base para resolver hrefs relativos.

    Returns:
        Lista de ``SponsoredItem`` (slot 1..N), sem produto repetido. Vazia se
        não houver anúncio com evidência — inclusive HTML vazio ou None.
    """
    if not html or not _RE_LABEL.search(html):
        return []
    soup = BeautifulSoup(html, "html.parser")
    for el in soup(["script", "style", "noscript"]):
        el.decompose()

    links_anunciados: List[Tag] = []
    for label in _label_nodes(soup):
        links_anunciados.extend(_shelf_links(label))

    if not links_anunciados:
        return []

    # Ordem do documento, não a ordem em que os rótulos foram achados.
    ordem = {id(a): i for i, a in enumerate(soup.find_all("a", href=True))}
    links_anunciados.sort(key=lambda a: ordem.get(id(a), 0))

    itens: List[SponsoredItem] = []
    vistos: set = set()
    for a in links_anunciados:
        pid = leroy_product_id(a["href"])
        if not pid or pid in vistos:
            continue
        vistos.add(pid)
        card = _card_for(a, pid)
        itens.append(SponsoredItem(
            slot=len(itens) + 1,
            product_id=pid,
            url=urljoin(base_url, a["href"]).split("#", 1)[0],
            title=_title_of(card, a),
            price=_price_of(card),
        ))
    return itens
