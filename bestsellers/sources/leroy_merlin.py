"""
bestsellers/sources/leroy_merlin.py — Lista "Mais Vendidos" da Leroy Merlin.

Índice Algolia: `production_products_most_sales` (o mesmo que a UI usa quando
o usuário escolhe "Mais vendidos"). A coleta bate direto no índice — sem
browser, sem Akamai no caminho.

⚠️ MECÂNICA SOB SUSPEITA. A Leroy declara a ordenação como mais vendidos, mas
o comportamento estatístico não confirma: em 48h, 41% dos itens não mudaram de
posição e o deslocamento mediano foi de 1 posição, contra 12–19% parados e 3–4
posições nas demais plataformas. Hipótese aberta: a ordenação pondera curadoria
1P, margem ou disponibilidade. Enquanto isso não for esclarecido com o
varejista, o número do Leroy NÃO sustenta sozinho decisão de corte de verba —
o relatório imprime esse aviso junto do resultado (`veredito`).

Seller: o índice devolve o lojista 3P como ObjectId opaco. A resolução
(mapa estático → cache em disco → PDP) é do `LeroyMerlinScraper`, reaproveitada
aqui — 1 PDP por seller novo, não por produto.
"""

from typing import Any, Dict, List, Optional

import requests
from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.models import BestSellerItem
from scrapers.leroy_merlin import (
    _ALGOLIA_API_KEY,
    _ALGOLIA_APP_ID,
    _ALGOLIA_HEADERS,
    LeroyMerlinScraper,
)

_INDICE_MAIS_VENDIDOS = "production_products_most_sales"
_URL_ALGOLIA = (
    f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net"
    f"/1/indexes/{_INDICE_MAIS_VENDIDOS}/query"
    f"?x-algolia-application-id={_ALGOLIA_APP_ID}"
    f"&x-algolia-api-key={_ALGOLIA_API_KEY}"
)
_TERMO = "ar condicionado"
_HITS_POR_PAGINA = 36
_TIMEOUT = 8


class LeroyMerlinBestSellers(BestSellerSource):
    """Coletor do índice de mais vendidos da Leroy Merlin (Algolia)."""

    key = "leroymerlin"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        # Instanciado só pela lógica de seller/URL — não abre browser.
        self._leroy: Optional[LeroyMerlinScraper] = None

    def _abrir(self) -> None:
        self._leroy = LeroyMerlinScraper(headless=self.headless)

    def _fechar(self) -> None:
        self._leroy = None

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        self.registrar_endpoint(_URL_ALGOLIA)
        itens: List[BestSellerItem] = []

        for pagina in range(max(1, paginas)):
            hits = self._consultar(pagina)
            if not hits:
                break
            itens.extend(self._parse(hits, offset=len(itens)))
            if len(hits) < _HITS_POR_PAGINA:
                break
        return itens

    def _consultar(self, pagina: int) -> List[Dict[str, Any]]:
        """Uma página do índice de mais vendidos."""
        payload = {
            "query": _TERMO,
            "hitsPerPage": _HITS_POR_PAGINA,
            "page": pagina,  # Algolia é 0-indexed
        }
        try:
            resposta = requests.post(
                _URL_ALGOLIA, headers=_ALGOLIA_HEADERS, json=payload, timeout=_TIMEOUT
            )
        except Exception as exc:
            raise RuntimeError(f"Algolia inacessível: {exc}") from exc

        if resposta.status_code == 404:
            # Índice renomeado é a falha silenciosa clássica desta fonte: a
            # coleta continuaria "funcionando" com a ordenação errada se
            # caíssemos no índice padrão.
            raise RuntimeError(
                f"índice '{_INDICE_MAIS_VENDIDOS}' não existe mais (HTTP 404). "
                "A Leroy renomeou a ordenação — confira o parâmetro sortBy na "
                "UI antes de trocar a constante."
            )
        resposta.raise_for_status()
        dados = resposta.json()

        hits = dados.get("hits") or []
        if hits:
            logger.info(
                f"[{self.nome}] Algolia mais-vendidos: {len(hits)} hits "
                f"(página {pagina + 1}/{dados.get('nbPages', '?')})"
            )
        return hits

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, hits: List[Dict[str, Any]], offset: int) -> List[BestSellerItem]:
        itens: List[BestSellerItem] = []
        for hit in hits:
            titulo = (
                hit.get("name")
                or hit.get("shortName")
                or hit.get("title")
                or hit.get("productName")
            )
            if not titulo:
                continue

            info = self._leroy._classify_hit_seller(hit)
            itens.append(BestSellerItem(
                rank=offset + len(itens) + 1,
                titulo=titulo,
                preco=self._preco(hit),
                rating=self._numero(hit, ("rating", "ratingAverage", "averageRating")),
                reviews=self._inteiro(
                    hit, ("reviewCount", "totalReviews", "numberOfReviews", "reviewsCount")
                ),
                seller=info.get("seller"),
                sku_plataforma=str(hit.get("objectID") or hit.get("id") or "") or None,
                url_produto=self._leroy._extract_algolia_url(hit),
            ))
        return itens

    @staticmethod
    def _preco(hit: Dict[str, Any]) -> Optional[float]:
        """
        Preço praticado.

        `averagePromotionalPrice`/`medianPromotionalPrice` são os campos que o
        índice realmente devolve (confirmado em produção); os demais ficam como
        rede de segurança para variações do índice.
        """
        bruto = (
            hit.get("averagePromotionalPrice")
            or hit.get("medianPromotionalPrice")
            or hit.get("price")
            or hit.get("sellingPrice")
            or hit.get("bestPrice")
        )
        if isinstance(bruto, dict):
            bruto = bruto.get("value") or bruto.get("amount")
        try:
            valor = float(str(bruto).replace(",", ".")) if bruto else None
        except (TypeError, ValueError):
            return None
        return valor if valor and valor > 0 else None

    @staticmethod
    def _numero(hit: Dict[str, Any], chaves) -> Optional[float]:
        for chave in chaves:
            valor = hit.get(chave)
            if isinstance(valor, (int, float)) and 0 < float(valor) <= 5:
                return round(float(valor), 2)
        return None

    @staticmethod
    def _inteiro(hit: Dict[str, Any], chaves) -> Optional[int]:
        for chave in chaves:
            valor = hit.get(chave)
            if isinstance(valor, bool):
                continue
            if isinstance(valor, int) and valor >= 0:
                return valor
        return None
