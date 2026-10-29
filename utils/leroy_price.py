"""
utils/leroy_price.py — Preço da oferta lido no PDP da Leroy Merlin.

Por que existe (Out/2026)
-------------------------
A coleta da Leroy lia o preço de ``averagePromotionalPrice`` do índice Algolia.
O nome diz o que é: uma **média** do preço promocional — não o preço que o
cliente paga no site. Dois casos medidos:

- item 3962339062 (Elgin 12K): R$ 2.290,53 nos quatro turnos contra R$ 2.339,00
  à vista no PDP (30/09/2026, ``docs/AVALIACAO_SCRAPLING.md`` §4);
- item 371381 (Split Vize Midea 12K **quente/frio**): R$ 1.091,25 em todos os
  turnos por meses, contra R$ 1.552,81 do modelo **só frio** (371378). Virou o
  "Piso geral" e a "Marca campeã" do 📅 Daily Price Vision, e ninguém achava a
  oferta no site.

Média que não se move é exatamente o sinal de oferta que não existe mais (ou
nunca existiu como tal). O único lugar com o preço praticado é o PDP.

Fonte: JSON-LD
--------------
O PDP publica ``<script type="application/ld+json">`` com o ``Product`` e suas
``offers`` (schema.org: ``price``/``lowPrice`` + ``availability`` + ``seller``)
— o mesmo bloco de onde ``utils.leroy_sellers`` já tira o nome do seller.
JSON-LD é o contrato que a loja mantém para o Google Shopping, então muda bem
menos que o DOM.

Regras duras
------------
1. **Indisponível não tem preço.** ``OutOfStock``/``SoldOut``/``Discontinued``/
   ``InStoreOnly`` devolvem ``preco=None`` com ``disponivel=False`` — a linha
   continua na coleta (posição, share), mas não compete no piso.
2. **Com várias ofertas, o preço é o do vencedor da buy box** quando o JSON-LD
   diz o seller de cada uma. Sem como casar, fica o menor preço disponível e a
   camada diz isso (``jsonld_menor``) — nunca um preço escolhido em silêncio.
3. **Dois ``Product`` diferentes no mesmo PDP = ambíguo**, sem preço. Escolher
   o primeiro gravaria o preço de um produto relacionado com a cara do item.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

from bs4 import BeautifulSoup

from utils.leroy_sellers import is_leroy_self
from utils.seller_names import seller_key
from utils.text import parse_price

# Faixa plausível para um item da categoria (de suporte a cassete). Fora dela é
# preço em centavos, parcela ou lixo de template — melhor sem preço.
_PRECO_MIN = 10.0
_PRECO_MAX = 100_000.0

# Decimal no formato schema.org (ponto como separador, sem milhar).
_DECIMAL_SCHEMA = re.compile(r"^\d+(?:\.\d+)?$")

# Sufixos schema.org de `availability` (a loja grava a URL inteira ou só o nome).
_INDISPONIVEL = ("outofstock", "soldout", "discontinued", "instoreonly")
_DISPONIVEL = (
    "instock", "onlineonly", "limitedavailability",
    "preorder", "presale", "backorder",
)


@dataclass(frozen=True)
class PdpPrice:
    """
    Resultado da leitura de preço de um PDP.

    Attributes:
        preco: preço praticado, ou None (indisponível ou não encontrado).
        disponivel: True/False quando o JSON-LD declara; None quando omite.
        camada: de onde veio — ``jsonld`` (oferta única), ``jsonld_vencedor``
            (oferta casada com o vencedor da buy box), ``jsonld_menor`` (várias
            ofertas sem como casar), ``indisponivel``, ``ambiguo`` ou
            ``sem_preco``.
    """

    preco: Optional[float]
    disponivel: Optional[bool]
    camada: str

    @property
    def lido(self) -> bool:
        """True quando o PDP respondeu a pergunta (tem preço ou está indisponível)."""
        return self.preco is not None or self.disponivel is False


SEM_PRECO = PdpPrice(None, None, "sem_preco")


def _tipos(node: dict) -> List[str]:
    """``@type`` normalizado para lista minúscula (schema.org aceita str ou lista)."""
    raw = node.get("@type")
    if isinstance(raw, str):
        return [raw.lower()]
    if isinstance(raw, list):
        return [str(t).lower() for t in raw]
    return []


def _ld_payloads(html: str) -> List[Any]:
    """JSONs dos blocos ``application/ld+json`` do HTML (só eles — ver regra 3)."""
    payloads: List[Any] = []
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", attrs={"type": True}):
        if (script.get("type") or "").strip().lower() != "application/ld+json":
            continue
        text = (script.string or script.get_text() or "").strip()
        if not text:
            continue
        try:
            payloads.append(json.loads(text))
        except (json.JSONDecodeError, ValueError):
            continue
    return payloads


def _produtos(payloads: Iterable[Any]) -> List[dict]:
    """
    ``Product`` de primeiro nível de cada bloco (raiz, lista ou ``@graph``).

    Não desce em ``ItemList``/``isRelatedTo``: produto relacionado no JSON-LD
    não é o item da página.
    """
    produtos: List[dict] = []
    for payload in payloads:
        nos = payload if isinstance(payload, list) else [payload]
        for no in list(nos):
            if isinstance(no, dict) and isinstance(no.get("@graph"), list):
                nos = list(nos) + no["@graph"]
        for no in nos:
            if isinstance(no, dict) and "product" in _tipos(no) and no.get("offers"):
                produtos.append(no)
    return produtos


def _num(raw: Any) -> Optional[float]:
    """Preço do JSON-LD (número ou string) dentro da faixa plausível."""
    if raw is None or isinstance(raw, bool):
        return None
    texto = str(raw).strip()
    if isinstance(raw, (int, float)):
        valor: Optional[float] = float(raw)
    elif _DECIMAL_SCHEMA.match(texto):
        # schema.org grava decimal com ponto ("2339" / "2339.00"). Não passa
        # por `parse_price`, que trunca inteiro de 4+ dígitos ("2339" → 233.0).
        valor = float(texto)
    else:
        valor = parse_price(texto)
    if valor is None or not (_PRECO_MIN <= valor <= _PRECO_MAX):
        return None
    return round(valor, 2)


def _disponibilidade(offer: dict) -> Optional[bool]:
    """True/False pela ``availability`` da oferta; None quando omitida/desconhecida."""
    raw = str(offer.get("availability") or "").strip().lower()
    if not raw:
        return None
    sufixo = raw.rstrip("/").rsplit("/", 1)[-1]
    if sufixo in _INDISPONIVEL:
        return False
    if sufixo in _DISPONIVEL:
        return True
    return None


def _ofertas(produto: dict) -> List[dict]:
    """Ofertas do produto, abrindo ``AggregateOffer.offers`` quando houver."""
    bruto = produto.get("offers")
    lista = bruto if isinstance(bruto, list) else [bruto]
    ofertas: List[dict] = []
    for offer in lista:
        if not isinstance(offer, dict):
            continue
        internas = offer.get("offers")
        if "aggregateoffer" in _tipos(offer) and isinstance(internas, list) and internas:
            ofertas.extend(o for o in internas if isinstance(o, dict))
        else:
            ofertas.append(offer)
    return ofertas


def _preco_oferta(offer: dict) -> Optional[float]:
    """``price``, senão ``lowPrice`` (AggregateOffer), senão ``priceSpecification.price``."""
    for chave in ("price", "lowPrice"):
        valor = _num(offer.get(chave))
        if valor is not None:
            return valor
    spec = offer.get("priceSpecification")
    specs = spec if isinstance(spec, list) else [spec]
    for item in specs:
        if isinstance(item, dict):
            valor = _num(item.get("price"))
            if valor is not None:
                return valor
    return None


def _seller_oferta(offer: dict) -> Optional[str]:
    """Nome do seller declarado na oferta (``seller.name``), se houver."""
    seller = offer.get("seller")
    if isinstance(seller, dict):
        nome = seller.get("name")
        return str(nome).strip() if nome else None
    if isinstance(seller, str):
        return seller.strip() or None
    return None


def _mesmo_seller(oferta: Optional[str], vencedor: str) -> bool:
    """Casa o seller da oferta com o vencedor (Leroy em qualquer grafia, resto por chave)."""
    if not oferta:
        return False
    if is_leroy_self(vencedor):
        return is_leroy_self(oferta)
    return seller_key(oferta) == seller_key(vencedor)


def extract_pdp_price(html: str, vencedor: Optional[str] = None) -> PdpPrice:
    """
    Lê o preço praticado no JSON-LD de um PDP da Leroy Merlin.

    Args:
        html: HTML do PDP (via requests ou browser — o JSON-LD vem no SSR).
        vencedor: vencedor da buy box, quando já conhecido. Usado só para
            escolher entre várias ofertas.

    Returns:
        `PdpPrice`. ``preco`` None quando indisponível (``disponivel=False``),
        ambíguo ou ausente — ver as regras do módulo.

    Example:
        >>> ld = {"@type": "Product", "offers": {"@type": "Offer",
        ...       "price": "2339.00", "availability": "https://schema.org/InStock"}}
        >>> html = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
        >>> extract_pdp_price(html)
        PdpPrice(preco=2339.0, disponivel=True, camada='jsonld')
    """
    if not html:
        return SEM_PRECO

    produtos = _produtos(_ld_payloads(html))
    if not produtos:
        return SEM_PRECO

    # Mesmo Product repetido em dois blocos é comum (SSR + hidratação);
    # Products diferentes não.
    assinaturas = {
        json.dumps(p.get("offers"), sort_keys=True, default=str) for p in produtos
    }
    if len(assinaturas) > 1:
        return PdpPrice(None, None, "ambiguo")

    ofertas = _ofertas(produtos[0])
    if not ofertas:
        return SEM_PRECO

    disponiveis = [o for o in ofertas if _disponibilidade(o) is not False]
    if not disponiveis:
        return PdpPrice(None, False, "indisponivel")

    if vencedor and len(disponiveis) > 1:
        casadas = [o for o in disponiveis if _mesmo_seller(_seller_oferta(o), vencedor)]
        precos = [p for p in (_preco_oferta(o) for o in casadas) if p is not None]
        if precos:
            return PdpPrice(min(precos), _disponibilidade(casadas[0]), "jsonld_vencedor")

    com_preco = [(o, _preco_oferta(o)) for o in disponiveis]
    com_preco = [(o, p) for o, p in com_preco if p is not None]
    if not com_preco:
        return SEM_PRECO
    oferta, preco = min(com_preco, key=lambda par: par[1])
    camada = "jsonld" if len(com_preco) == 1 else "jsonld_menor"
    return PdpPrice(preco, _disponibilidade(oferta), camada)
