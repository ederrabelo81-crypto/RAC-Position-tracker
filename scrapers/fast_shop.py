"""
scrapers/fast_shop.py — Scraper da Fast Shop (fastshop.com.br).

Estratégia (em ordem de prioridade):
  1. Intercepção XHR da API VTEX IO (/api/catalog_system ou /_v/api/intelligent-search)
  2. Parse DOM com seletores VTEX IO + fallbacks genéricos
  3. Debug HTML dump em logs/ quando 0 itens

Plataforma: VTEX IO React — classes geradas como vtex-product-summary-2-x-*
URL de busca: /web/p/busca?q={keyword} ou /_v/api/intelligent-search/product_search
Paginação: &page={n} (1-indexed)
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, LOGS_DIR
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating, parse_review_count

_ITEMS_PER_PAGE = 20

_SELECTORS = {
    "item_candidates": [
        # VTEX IO padrão (plataforma da Fast Shop)
        '[class*="vtex-product-summary"]',
        'article[class*="vtex-product-summary"]',
        'li[class*="vtex-product-summary"]',
        'div[class*="productSummary"]',
        # data-testid / data-id VTEX
        '[data-testid="product-summary"]',
        '[class*="product-summary"]',
        # Fallback genérico angular/vue
        '.product-item',
        '[class*="ProductCard"]',
        '[class*="product-card"]',
        '[class*="ProductItem"]',
        # Fallback estrutural
        'li > a[href*="/produto/"]',
        'li > a[href*="/p/"]',
    ],
    "title_candidates": [
        '[class*="vtex-product-summary-2-x-productBrand"]',
        '[class*="vtex-product-summary-2-x-nameContainer"]',
        '[class*="productBrand"]',
        '[class*="productName"]',
        '[class*="product-name"]',
        '[class*="ProductTitle"]',
        'h2[class*="vtex"]',
        'span[class*="vtex-product-summary"]',
        'h2', 'h3',
    ],
    "price_candidates": [
        '[class*="vtex-product-price-1-x-sellingPriceValue"]',
        '[class*="vtex-product-price-1-x-sellingPrice"]',
        '[class*="sellingPriceValue"]',
        '[class*="sellingPrice"]',
        '[class*="productPrice"]',
        '[class*="product-price"]',
        '[class*="Price"]',
        'span[class*="vtex"][class*="price"]',
    ],
    "rating_candidates": [
        '[class*="vtex-product-review"]',
        '[class*="starValue"]',
        '[class*="rating"]',
        '[class*="Rating"]',
    ],
    "review_count_candidates": [
        '[class*="totalCount"]',
        '[class*="reviewCount"]',
        '[class*="review-count"]',
    ],
    "tag_candidates": [
        '[class*="vtex-product-summary-2-x-productNameBadge"]',
        '[class*="productBadge"]',
        '[class*="Badge"]',
        '[class*="label"]',
        '[class*="tag"]',
    ],
    "bot_check": "#px-captcha, #challenge-form, [id*='px-'], #distil_r_captcha",
}

# Padrões de URL para APIs VTEX IO e Fast Shop
_API_URL_PATTERNS = [
    "intelligent-search/product_search",
    "catalog_system/pub/products",
    "api/catalog",
    "_v/api",
    "product-search",
    "search/products",
]


class FastShopScraper(BaseScraper):
    """Scraper modular para a Fast Shop."""

    platform_name = "Fast Shop"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._captured_products: List[Dict] = []

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        url = f"https://www.fastshop.com.br/web/p/busca?q={encoded}"
        if page > 1:
            url += f"&page={page}"
        return url

    # ------------------------------------------------------------------
    # XHR interception
    # ------------------------------------------------------------------

    def _setup_xhr_intercept(self) -> None:
        self._captured_products = []

        def handle_response(response):
            try:
                url = response.url
                if not any(pat in url for pat in _API_URL_PATTERNS):
                    return
                if response.status != 200:
                    return
                if "json" not in response.headers.get("content-type", ""):
                    return
                data = response.json()
                products = (
                    data.get("products")
                    or data.get("items")
                    or data.get("data", {}).get("products")
                    or (data if isinstance(data, list) else [])
                )
                if products:
                    self._captured_products.extend(products)
                    logger.debug(
                        f"[{self.platform_name}] XHR capturado: "
                        f"{len(products)} produtos em {url[:70]}"
                    )
            except Exception:
                pass

        self._page.on("response", handle_response)

    def _parse_api_products(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        records = []
        for idx, prod in enumerate(self._captured_products):
            # Normaliza campos VTEX IO e genéricos
            title = (
                prod.get("productName")
                or prod.get("name")
                or prod.get("title")
            )
            # VTEX IO: items[0].sellers[0].commertialOffer.Price
            price_val = prod.get("price")
            try:
                items_data = prod.get("items", [{}])
                offer = (
                    items_data[0]
                    .get("sellers", [{}])[0]
                    .get("commertialOffer", {})
                )
                price_val = price_val or offer.get("Price") or offer.get("ListPrice")
            except (IndexError, KeyError, TypeError):
                pass

            try:
                price_float = float(str(price_val)) if price_val else None
            except (ValueError, TypeError):
                price_float = None

            pos = page_offset + idx + 1
            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos,
                position_organic=pos,
                position_sponsored=None,
                price_float=price_float,
                seller="Fast Shop",
                is_fulfillment=False,
                rating=None,
                review_count=None,
                tag_destaque=None,
            ))
        return records

    # ------------------------------------------------------------------
    # DOM parse
    # ------------------------------------------------------------------

    @staticmethod
    def _first_match(tag: Tag, candidates: List[str]) -> Optional[Tag]:
        for sel in candidates:
            el = tag.select_one(sel)
            if el:
                return el
        return None

    @staticmethod
    def _detect_items(soup: BeautifulSoup) -> tuple[List[Tag], str]:
        for sel in _SELECTORS["item_candidates"]:
            items = soup.select(sel)
            if len(items) >= 2:
                return items, sel
        return [], "nenhum"

    def _parse_dom(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        if soup.select_one(_SELECTORS["bot_check"]):
            logger.warning(f"[{self.platform_name}] Bot-check detectado (página {page}).")
            return []

        items, sel_used = self._detect_items(soup)
        logger.info(
            f"[{self.platform_name}] {len(items)} itens encontrados na página "
            f"(seletor: {sel_used})"
        )

        if not items:
            self._dump_debug(html, page, keyword)
            return []

        records = []
        for idx, item in enumerate(items):
            title_el  = self._first_match(item, _SELECTORS["title_candidates"])
            price_el  = self._first_match(item, _SELECTORS["price_candidates"])
            rating_el = self._first_match(item, _SELECTORS["rating_candidates"])
            review_el = self._first_match(item, _SELECTORS["review_count_candidates"])
            tag_el    = self._first_match(item, _SELECTORS["tag_candidates"])
            pos = page_offset + idx + 1

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title_el.get_text(strip=True) if title_el else None,
                position_general=pos,
                position_organic=pos,
                position_sponsored=None,
                price_raw=price_el.get_text(strip=True) if price_el else None,
                seller="Fast Shop",
                is_fulfillment=False,
                rating=parse_rating(rating_el.get_text() if rating_el else None),
                review_count=parse_review_count(review_el.get_text() if review_el else None),
                tag_destaque=tag_el.get_text(strip=True) if tag_el else None,
            ))

        return records

    # ------------------------------------------------------------------
    # Debug dump
    # ------------------------------------------------------------------

    def _dump_debug(self, html: str, page: int, keyword: str) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"fastshop_debug_p{page}_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo: {path}\n"
                "  → Inspecione classes vtex-product-summary-* no browser."
            )
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Espera
    # ------------------------------------------------------------------

    def _wait_for_products(self, timeout_ms: int = 15_000) -> bool:
        for sel in _SELECTORS["item_candidates"][:5]:
            try:
                self._page.wait_for_selector(sel, timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=18),
        reraise=True,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        all_records: List[Dict[str, Any]] = []
        self._setup_xhr_intercept()

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")
            self._captured_products = []

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_products(timeout_ms=15_000)
                self._wait_for_network_idle()
                self._random_delay(min_s=2.5, max_s=6.5)
                self._human_scroll(steps=8, step_px=300)
                time.sleep(1.5)  # aguarda XHR tardio

                offset = (page - 1) * _ITEMS_PER_PAGE

                if self._captured_products:
                    logger.info(
                        f"[{self.platform_name}] {len(self._captured_products)} itens via XHR"
                    )
                    records = self._parse_api_products(keyword, keyword_category_map, offset)
                else:
                    records = self._parse_dom(
                        self._page.content(), keyword, keyword_category_map, page, offset
                    )

                all_records.extend(records)

                if not records:
                    logger.warning(
                        f"[{self.platform_name}] Página {page} retornou 0 itens. "
                        "Parando keyword."
                    )
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
