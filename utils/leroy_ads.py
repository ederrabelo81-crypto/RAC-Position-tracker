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
from urllib.parse import parse_qs, quote, quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from utils.text import parse_price

__all__ = [
    "SponsoredItem",
    "SPONSORED_LABEL_PATTERN",
    "extract_sponsored",
    "leroy_product_id",
    "is_not_found_page",
    "is_search_results_url",
    "organic_before_shelf",
    "serp_url_template",
    "fill_serp_template",
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
    # Metadado do próprio card (10/11/2026): o link do anúncio carrega
    # ``data-gtm-item-list="Produtos Patrocinados"`` e
    # ``data-gtm-partner-name="retail media"`` — evidência mesmo se o
    # cabeçalho da prateleira mudar de texto.
    for el in soup.find_all("a", attrs={"data-gtm-item-list": _RE_LABEL}):
        nodes.append(el)
    for el in soup.find_all("a", attrs={"data-gtm-partner-name": re.compile("retail media", re.I)}):
        nodes.append(el)
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
    """Preço praticado do card.

    O ``data-gtm-item-price`` do link é o preço cobrado; o texto do card traz
    antes o preço "de" riscado (R$ 3.332,00 → R$ 2.665,56 no dump real), que é
    o que o 1º ``R$`` do texto pegaria.
    """
    for el in [card] + card.find_all(attrs={"data-gtm-item-price": True}):
        bruto = el.get("data-gtm-item-price") if isinstance(el, Tag) else None
        if bruto:
            try:
                valor = float(str(bruto).replace(",", "."))
            except ValueError:
                continue
            if valor > 0:
                return valor
    m = _RE_PRICE.search(card.get_text(" ", strip=True))
    return parse_price(m.group(0)) if m else None


_RE_HEADING = ("h1", "h2", "h3", "h4")
# Prateleira de anúncio tem poucos produtos; um bloco "rotulado" com mais que
# isto é o grid orgânico engolido por um ancestral grande demais.
_MAX_SHELF_ITEMS = 24


def _is_card_title(heading: Tag, limite: Tag) -> bool:
    """O cabeçalho é título de card (o menor bloco com produto em volta dele
    tem UM produto só), e não cabeçalho de seção?"""
    if heading.find_parent("a") is not None:
        return True
    node: Optional[Tag] = heading
    while isinstance(node, Tag):
        ids = _distinct_ids(_product_links(node))
        if ids:
            return len(ids) == 1
        if node is limite:
            return False
        node = node.parent
    return False


def _opens_other_section(node: Tag) -> bool:
    """O irmão abre OUTRA seção da página ("Resultados", "Mais produtos")?

    Sim quando tem um cabeçalho que não é rótulo de patrocinado nem título de
    card — título de card vem colado num produto só, antes ou depois do link.
    """
    if node.name in _RE_HEADING:
        texto = " ".join(node.get_text(" ", strip=True).split())
        return bool(texto) and not _is_label(texto)
    for h in node.find_all(_RE_HEADING):
        texto = " ".join(h.get_text(" ", strip=True).split())
        if texto and not _is_label(texto) and not _is_card_title(h, node):
            return True
    return False


def _shelf_links(label: Tag) -> List[Tag]:
    """Links de produto da prateleira (ou do card) que o rótulo encabeça.

    Sobe do rótulo até o primeiro ancestral com link de produto. Se esse
    ancestral é um card (1 produto), o card inteiro é o anúncio. Se tem vários,
    a prateleira são os irmãos SEGUINTES ao ramo do rótulo, e só enquanto
    parecem a mesma prateleira:

    * o 1º irmão com vários produtos é o carrossel — a leitura para nele;
    * cards soltos como irmãos (1 produto cada) seguem enquanto forem a mesma
      tag;
    * cabeçalho de outra seção encerra.

    É o que impede um ``<main>`` com o rótulo e o grid orgânico lado a lado
    de virar "tudo patrocinado", com ou sem cabeçalho entre os dois.
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
    tag_card: Optional[str] = None
    for sib in branch.next_siblings:
        if not isinstance(sib, Tag):
            continue
        if _opens_other_section(sib):
            break
        sib_links = _product_links(sib)
        if not sib_links:
            if links:
                break
            continue
        n_ids = len(_distinct_ids(sib_links))
        if n_ids > 1:
            if not links:
                links = sib_links  # o carrossel inteiro
            break
        if tag_card is not None and sib.name != tag_card:
            break
        tag_card = sib.name
        links.extend(sib_links)
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


# --- URL da busca (Nov/2026) --------------------------------------------------
# Em 10/11/2026 ``/busca?term=<kw>`` passou a abrir a página de erro da Leroy
# ("Desculpe, não localizamos a página pesquisada") — a coleta orgânica não
# notou porque sai da Algolia. O formato novo não estava em lugar nenhum do
# HTML, então o scraper busca pelo campo do próprio site e aprende a URL que o
# site gerar (``serp_url_template``), em vez de fixar outro formato de memória.

_NOT_FOUND_MARKERS = (
    "não localizamos a página",
    "nao localizamos a pagina",
    "página não encontrada",
    "pagina nao encontrada",
)


def is_not_found_page(html: Optional[str]) -> bool:
    """A página é o "não localizamos a página pesquisada" da Leroy?

    Args:
        html: HTML renderizado (``page.content()``) ou texto da página.

    Returns:
        True quando algum marcador da página de erro aparece.
    """
    if not html:
        return False
    bruto = html.lower()
    if "not-found-notice" in bruto:  # bloco da página de erro (10/11/2026)
        return True
    # A frase vem quebrada por tag: "não localizamos <br> a página pesquisada".
    texto = " ".join(re.sub(r"<[^>]+>", " ", bruto).split())
    return any(m in texto for m in _NOT_FOUND_MARKERS)


_KW_PLACEHOLDER = "{kw}"


def serp_url_template(url: str, keyword: str) -> Optional[str]:
    """Molde de URL de busca a partir de uma URL que o site gerou.

    Troca o termo buscado (em qualquer das codificações usuais) por ``{kw}``.

    Args:
        url: URL da página de resultados, como o site a escreveu.
        keyword: termo digitado no campo de busca.

    Returns:
        O molde, ou None quando o termo não aparece na URL (aí não há o que
        aprender — a próxima keyword volta a usar o campo de busca).

    Example:
        >>> serp_url_template("https://x/search?q=ar+condicionado", "ar condicionado")
        'https://x/search?q={kw}'
    """
    if not url or not keyword:
        return None
    for cod in (quote_plus(keyword), quote(keyword), quote(keyword, safe=""), keyword,
                keyword.replace(" ", "-")):
        for variante in (cod, cod.lower()):
            if variante and variante in url:
                return url.replace(variante, _KW_PLACEHOLDER)
    return None


def fill_serp_template(template: str, keyword: str) -> str:
    """Preenche o molde de ``serp_url_template`` com outro termo.

    O hífen entra quando o molde veio de um slug (``/busca/ar-condicionado``);
    nos demais casos o termo vai codificado como em formulário (``+``).
    """
    antes = template.split(_KW_PLACEHOLDER, 1)[0]
    if "?" in antes or "=" in antes:
        return template.replace(_KW_PLACEHOLDER, quote_plus(keyword))
    return template.replace(_KW_PLACEHOLDER, quote(keyword.strip().lower().replace(" ", "-")))


def is_search_results_url(url: Optional[str]) -> bool:
    """A URL é de página de RESULTADOS de busca (não home, PDP ou categoria)?

    O dump do PC coletor (10/11/2026) terminou na home depois da busca pelo
    campo, e a prateleira de patrocinados da HOME seria gravada como anúncio
    da keyword. Só conta caminho de busca com o termo na query.
    """
    if not url:
        return False
    parsed = urlparse(url)
    if not parsed.path.rstrip("/").lower().endswith(("/search", "/busca")):
        return False
    q = parse_qs(parsed.query)
    return any(q.get(k, [""])[0].strip() for k in ("term", "searchTerm", "q"))


_RE_ORGANICO = re.compile(r"algolia", re.I)


def organic_before_shelf(html: Optional[str]) -> int:
    """Quantos produtos ORGÂNICOS a página mostra antes da prateleira de anúncio.

    No dump real (10/11/2026) a prateleira "Produtos Patrocinados" fica DEPOIS
    dos 36 produtos da busca (cards com ``data-gtm-partner-name="algolia"``).
    Gravar o anúncio com ``Posição Geral`` 1..10 o poria no "top-10" do 📣
    Share of Voice, quando o consumidor só o vê depois de rolar a página
    inteira. Este número é o deslocamento da posição geral dos anúncios.

    Args:
        html: HTML renderizado da página de busca.

    Returns:
        Produtos orgânicos distintos antes do 1º anúncio (0 se a prateleira
        vem antes do grid, ou se a página não marca os orgânicos).
    """
    if not html:
        return 0
    soup = BeautifulSoup(html, "html.parser")
    vistos: Dict[str, None] = {}
    for a in soup.find_all("a", href=True):
        lista = a.get("data-gtm-item-list") or ""
        parceiro = a.get("data-gtm-partner-name") or ""
        if _RE_LABEL.search(lista) or re.search("retail media", parceiro, re.I):
            break
        if _RE_ORGANICO.search(parceiro):
            pid = leroy_product_id(a["href"])
            if pid:
                vistos.setdefault(pid, None)
    else:
        return 0  # nenhum anúncio marcado: sem como medir
    return len(vistos)
