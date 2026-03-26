"""
scrapers/shopee.py — Scraper da Shopee Brasil (shopee.com.br).

Estratégia:
  - A Shopee é 100% SPA (React) — o HTML inicial não contém produtos.
    Os dados vêm de requisições XHR para a API interna:
    https://shopee.com.br/api/v4/search/search_items?keyword=...&limit=60&newest={offset}
  - Esta é a abordagem recomendada (API não documentada > scraping de DOM).
  - O Playwright aguarda a resposta da API interceptando requisições de rede.
  - Fallback: parse do DOM renderizado após carregamento completo.

Notas de manutenção:
  A Shopee pode exigir cookies de sessão (necessário visitar a home primeiro)
  e headers de autenticação em requisições XHR. Se a API retornar 403,
  ative `_use_dom_fallback = True` para usar o parse de DOM.
"""

import json
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating

# Shopee exibe 60 itens por página via API
_ITEMS_PER_PAGE = 60

_SELECTORS = {
    # DOM fallback
    "item_container": '[data-sqe="item"]',
    "title":          '[data-sqe="name"]',
    "price":          ".shopee-price",
    "rating":         ".shopee-rating-stars__stars",
    "review_count":   ".shopee-rating-stars__count",
}


class ShopeeScraper(BaseScraper):
    """Scraper modular para a Shopee Brasil."""

    platform_name = "Shopee"

    # Troque para True se a API retornar 403 persistentemente
    _use_dom_fallback: bool = False

    @staticmethod
    def _build_api_url(keyword: str, offset: int = 0) -> str:
        encoded = quote_plus(keyword)
        return (
            f"https://shopee.com.br/api/v4/search/search_items"
            f"?keyword={encoded}&limit={_ITEMS_PER_PAGE}&newest={offset}"
            f"&order=relevancy&page_type=search&scenario=PAGE_GLOBAL_SEARCH"
            f"&version=2"
        )

    @staticmethod
    def _build_search_url(keyword: str) -> str:
        encoded = quote_plus(keyword)
        return f"https://shopee.com.br/search?keyword={encoded}"

    def _fetch_via_api(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
    ) -> List[Dict[str, Any]]:
        """
        Intercepta ou requisita a API interna da Shopee para obter JSON de produtos.
        Mais estável que parse de DOM para SPAs.
        """
        offset = (page - 1) * _ITEMS_PER_PAGE
        api_url = self._build_api_url(keyword, offset)

        # Usa o contexto de browser (cookies já estabelecidos) para fazer a request
        response = self._page.evaluate(
            """async (url) => {
                const resp = await fetch(url, {
                    credentials: 'include',
                    headers: { 'x-api-source': 'pc', 'af-ac-enc-dat': '' }
                });
                return resp.ok ? await resp.json() : null;
            }""",
            api_url,
        )

        if not response or "items" not in response.get("data", {}):
            logger.warning(f"[{self.platform_name}] API retornou resposta inválida.")
            return []

        items_data = response["data"]["items"]
        logger.info(f"[{self.platform_name}] {len(items_data)} itens via API")

        records = []
        for idx, item in enumerate(items_data):
            info = item.get("item_basic", {})

            title = info.get("name")
            price_cents = info.get("price")  # Shopee armazena preço em centavos * 100000
            price_float = price_cents / 100000 if price_cents else None

            seller = info.get("shop_name") or "Shopee"
            rating = info.get("item_rating", {}).get("rating_star")
            review_count = info.get("item_rating", {}).get("rating_count", [0])
            review_count = sum(review_count) if isinstance(review_count, list) else review_count

            # Shopee não diferencia orgânico/patrocinado no JSON público
            pos_general = offset + idx + 1

            tag_labels = info.get("label_ids", [])
            tag = "Destaque" if tag_labels else None

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_general,
                position_sponsored=None,
                price_float=price_float,
                seller=seller,
                is_fulfillment=bool(info.get("shopee_verified")),
                rating=float(rating) if rating else None,
                review_count=int(review_count) if review_count else None,
                tag_destaque=tag,
            ))

        return records

    def _fetch_via_dom(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
    ) -> List[Dict[str, Any]]:
        """Fallback: parse do DOM renderizado."""
        soup = self._get_soup()
        items = soup.select(_SELECTORS["item_container"])
        logger.info(f"[{self.platform_name}] {len(items)} itens via DOM (fallback)")

        records = []
        offset = (page - 1) * _ITEMS_PER_PAGE

        for idx, item in enumerate(items):
            title_el = item.select_one(_SELECTORS["title"])
            title    = title_el.get_text(strip=True) if title_el else None

            price_el  = item.select_one(_SELECTORS["price"])
            price_raw = price_el.get_text(strip=True) if price_el else None

            pos_general = offset + idx + 1

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_general,
                position_sponsored=None,
                price_raw=price_raw,
                seller="Shopee",
                is_fulfillment=False,
                rating=None,
                review_count=None,
                tag_destaque=None,
            ))

        return records

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=15),
        reraise=True,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Busca keyword na Shopee por até `page_limit` páginas."""
        all_records: List[Dict[str, Any]] = []

        # Visita home primeiro para estabelecer cookies de sessão
        try:
            self._page.goto("https://shopee.com.br", wait_until="domcontentloaded")
            self._random_delay(min_s=2.0, max_s=4.0)
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] Erro ao visitar home: {exc}")

        for page in range(1, page_limit + 1):
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit}")

            try:
                if not self._use_dom_fallback:
                    # Navega para página de busca (garante cookies fresh)
                    search_url = self._build_search_url(keyword)
                    self._page.goto(search_url, wait_until="domcontentloaded")
                    self._wait_for_network_idle()
                    self._random_delay(min_s=2.5, max_s=5.0)
                    self._human_scroll(steps=5, step_px=300)

                    records = self._fetch_via_api(keyword, keyword_category_map, page)
                    if not records:
                        logger.info(f"[{self.platform_name}] Sem dados via API. Usando DOM fallback.")
                        records = self._fetch_via_dom(keyword, keyword_category_map, page)
                else:
                    search_url = self._build_search_url(keyword)
                    self._page.goto(search_url, wait_until="domcontentloaded")
                    self._wait_for_network_idle()
                    self._random_delay(min_s=3.0, max_s=7.0)
                    self._human_scroll(steps=12, step_px=250)
                    records = self._fetch_via_dom(keyword, keyword_category_map, page)

                all_records.extend(records)

                if not records:
                    break

                if page < page_limit:
                    self._random_delay()

            except Exception as exc:
                logger.error(f"[{self.platform_name}] Erro na página {page}: {exc}")
                raise

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
