"""
bestsellers/sources/shopee.py — Lista ordenada por vendas da Shopee Brasil.

URL: https://shopee.com.br/search?keyword=ar%20condicionado&sortBy=sales
API: /api/v4/search/search_items com `by=sales`

Mecânica: ordenação por vendas — e a ÚNICA fonte do conjunto que declara
unidades por mês por anúncio ("948 Vendido/Mês"). Esse campo é a única variável
de velocidade real disponível a custo zero, e é o motivo de a Shopee continuar
na rotina mesmo sendo a coleta mais instável.

ARMADILHA REGISTRADA (08/08/2026): sem `sortBy=sales` a lista é RELEVÂNCIA —
mistura de patrocinado e engajamento. Pela relevância a Midea aparecia em 17 de
55 posições, melhor rank #4; pela ordenação de vendas, 1 de 35 e rank #14. São
dois universos, não duas leituras do mesmo. Por isso `by="sales"` é passado
explicitamente e o portão de validação confere o parâmetro.

BEST-EFFORT sem proxy residencial BR: o IP de datacenter é marcado antes do
fingerprint. Sessão expira em horas — re-capturar com
`python utils/session_grabber.py --site shopee`.
"""

import random
import re
import time
from typing import List, Optional

from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.config import BASE_VENDIDOS_MES
from bestsellers.models import BestSellerItem
from scrapers.shopee import ShopeeScraper

_KEYWORD = "ar condicionado"
_ORDENACAO = "sales"
_DELAY_ENTRE_PAGINAS = (3.0, 7.0)

# "948 Vendido/Mês", "1,2mil vendidos", "948 vendidos"
_RE_VENDIDOS = re.compile(r"([\d.,]+)\s*(mil|k)?\s*vendid", re.I)
_RE_POR_MES = re.compile(r"vendido\s*/\s*m[êe]s|/\s*m[êe]s", re.I)


class ShopeeBestSellers(BestSellerSource):
    """Coletor da lista ordenada por vendas da Shopee."""

    key = "shopee"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._shopee: Optional[ShopeeScraper] = None

    def _abrir(self) -> None:
        self._shopee = ShopeeScraper(headless=self.headless)
        self._shopee.__enter__()

    def _fechar(self) -> None:
        if self._shopee is not None:
            self._shopee.__exit__()
            self._shopee = None

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        shopee = self._shopee
        # O endpoint carrega o parâmetro de ordenação: é ele que o portão de
        # validação inspeciona para provar que a lista não é relevância.
        self.registrar_endpoint(
            f"https://shopee.com.br/api/v4/search/search_items"
            f"?by={_ORDENACAO}&keyword={_KEYWORD.replace(' ', '%20')}&order=desc"
        )

        itens: List[BestSellerItem] = []
        for pagina in range(max(1, paginas)):
            dados = shopee._fetch_page(_KEYWORD, pagina, by=_ORDENACAO)
            if dados is None:
                if shopee.captcha_hit:
                    raise RuntimeError(
                        "bloqueio da API v4 (403 / anti-fraude). Re-capture a "
                        "sessão: python utils/session_grabber.py --site shopee"
                    )
                break

            brutos = dados.get("items") or []
            if not brutos:
                logger.info(f"[{self.nome}] Sem mais resultados (pág {pagina + 1}).")
                break

            novos = self._parse(brutos, offset=len(itens))
            if not novos:
                shopee._dump_debug_response(_KEYWORD, pagina, dados)
                raise RuntimeError(
                    "API respondeu com itens mas nenhum parseou — a Shopee "
                    "trocou a estrutura do wrapper (dump salvo em logs/)."
                )
            itens.extend(novos)

            if len(brutos) < 60:
                break
            time.sleep(random.uniform(*_DELAY_ENTRE_PAGINAS))
        return itens

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, brutos: List[dict], offset: int) -> List[BestSellerItem]:
        shopee = self._shopee
        itens: List[BestSellerItem] = []

        for bruto in brutos:
            payload = shopee._extract_item_payload(bruto)
            if not payload:
                continue
            asset = payload.get("item_card_displayed_asset") or {}

            titulo = shopee._extract_name(payload, asset)
            if not titulo:
                continue

            vendidos, base = self._extrair_vendidos(shopee._extract_sold(payload, asset))
            itemid = payload.get("itemid") or payload.get("item_id")
            shopid = payload.get("shopid") or payload.get("shop_id")

            itens.append(BestSellerItem(
                rank=offset + len(itens) + 1,
                titulo=titulo,
                preco=shopee._normalize_price(shopee._extract_raw_price(payload, asset)),
                rating=self._extrair_rating(payload),
                reviews=self._extrair_reviews(payload),
                vendidos=vendidos,
                base_vendidos=base,
                seller=payload.get("shop_name") or payload.get("shop_location"),
                sku_plataforma=f"{shopid}_{itemid}" if itemid and shopid else None,
                url_produto=(
                    f"https://shopee.com.br/product/{shopid}/{itemid}"
                    if itemid and shopid else None
                ),
                patrocinado=shopee._is_sponsored(payload, bruto, asset),
            ))
        return itens

    @staticmethod
    def _extrair_vendidos(texto: Optional[str]) -> tuple:
        """
        (quantidade, base) a partir do texto de vendas do card.

        A base sai como 'mes' apenas quando o texto diz explicitamente
        "Vendido/Mês". Um "948 vendidos" sem qualificação é acumulado — marcar
        como mensal misturaria as duas unidades na soma de velocidade, que é
        exatamente o erro que a regra dura proíbe.
        """
        if not texto:
            return None, None
        achado = _RE_VENDIDOS.search(texto)
        if not achado:
            return None, None
        bruto = achado.group(1).replace(".", "").replace(",", ".")
        try:
            valor = float(bruto)
        except ValueError:
            return None, None
        if achado.group(2):
            valor *= 1000
        base = BASE_VENDIDOS_MES if _RE_POR_MES.search(texto) else "acumulado"
        return valor, base

    @staticmethod
    def _extrair_rating(payload: dict) -> Optional[float]:
        estrelas = payload.get("item_rating") or {}
        if isinstance(estrelas, dict):
            valor = estrelas.get("rating_star")
            if isinstance(valor, (int, float)) and 0 < valor <= 5:
                return round(float(valor), 2)
        valor = payload.get("shop_rating")
        if isinstance(valor, (int, float)) and 0 < valor <= 5:
            return round(float(valor), 2)
        return None

    @staticmethod
    def _extrair_reviews(payload: dict) -> Optional[int]:
        estrelas = payload.get("item_rating") or {}
        if isinstance(estrelas, dict):
            for chave in ("rating_count_total", "rcount_with_context", "rcount_with_image"):
                valor = estrelas.get(chave)
                if isinstance(valor, int) and valor >= 0:
                    return valor
            contagens = estrelas.get("rating_count")
            # rating_count[0] é o total; os demais são por nº de estrelas.
            if isinstance(contagens, list) and contagens:
                primeiro = contagens[0]
                if isinstance(primeiro, int) and primeiro >= 0:
                    return primeiro
        return None
