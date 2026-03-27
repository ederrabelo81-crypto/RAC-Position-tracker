"""
scrapers/leroy_merlin.py — Scraper da Leroy Merlin Brasil (leroymerlin.com.br).

Estratégia (em ordem de prioridade):
  1. Algolia API direta — Leroy expõe appId/apiKey/indexName no HTML da página.
     POST https://{appId}-dsn.algolia.net/1/indexes/{indexName}/query
     Resposta: {"hits": [...], "nbPages": N}
  2. Intercepção XHR da API Algolia (algolia.net) — captura chamadas do JS client.
  3. Parse DOM com cadeia de seletores fallback.
  4. Debug HTML dump em logs/ quando 0 itens.

Plataforma: Next.js + Algolia InstantSearch (client-side rendering).
Paginação: page param 0-indexed na Algolia API.
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, LOGS_DIR
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating, parse_review_count

_ITEMS_PER_PAGE = 24

# Algolia credentials exposed in the Leroy Merlin page HTML
_ALGOLIA_APP_ID    = "1CF3ZT43ZU"
_ALGOLIA_API_KEY   = "28e054533dcdd3d71379fc3f38e78f1e"
_ALGOLIA_INDEX     = "production_products"
_ALGOLIA_SEARCH_URL = (
    f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net"
    f"/1/indexes/{_ALGOLIA_INDEX}/query"
)
_ALGOLIA_HEADERS = {
    "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
    "X-Algolia-API-Key": _ALGOLIA_API_KEY,
    "Content-Type": "application/json",
}

_SELECTORS = {
    "item_candidates": [
        # data-testid (Next.js padrão Leroy)
        '[data-testid="product-card"]',
        '[data-testid="product-item"]',
        '[data-testid="product"]',
        '[data-cy="product-card"]',
        # Leroy Merlin styled-components e design system
        'li[class*="ProductCard"]',
        'li[class*="product-card"]',
        'div[class*="ProductCard"]',
        'article[class*="product"]',
        '[class*="Card__Wrapper"]',
        '[class*="ProductList__Item"]',
        # Fallback estrutural
        'li > a[href*="/p/"]',
        'li > a[href*="/produto/"]',
        'ul[class*="product"] > li',
        'ul[class*="Product"] > li',
    ],
    "title_candidates": [
        '[data-testid="product-title"]',
        '[data-testid="product-name"]',
        '[class*="ProductTitle"]',
        '[class*="product-title"]',
        '[class*="ProductName"]',
        'h2[class*="sc-"]',
        'h3[class*="sc-"]',
        'h2', 'h3',
    ],
    "price_candidates": [
        '[data-testid="product-price"]',
        '[data-testid="price"]',
        '[class*="ProductPrice"]',
        '[class*="PriceTag"]',
        '[class*="product-price"]',
        '[class*="Price__Value"]',
        'span[class*="price"]',
    ],
    "rating_candidates": [
        '[data-testid="rating"]',
        '[class*="RatingStars"]',
        '[class*="rating-value"]',
        '[class*="Rating"]',
    ],
    "review_count_candidates": [
        '[data-testid="review-count"]',
        '[class*="ReviewCount"]',
        '[class*="rating-count"]',
    ],
    "tag_candidates": [
        '[data-testid="badge"]',
        '[class*="ProductBadge"]',
        '[class*="product-tag"]',
        '[class*="Badge"]',
        '[class*="Label"]',
    ],
    "bot_check": "#px-captcha, #challenge-form, [class*='bot-check']",
}

# Padrões de URL para XHR interception (Algolia + APIs internas)
_API_URL_PATTERNS = [
    "algolia.net",
    "algolia.io",
    "/api/v3/search",
    "/api/v3/products",
    "/api/catalog",
    "product-search",
]


class LeroyMerlinScraper(BaseScraper):
    """Scraper modular para Leroy Merlin Brasil."""

    platform_name = "Leroy Merlin"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._captured_products: List[Dict] = []

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        base = f"https://www.leroymerlin.com.br/busca?term={encoded}"
        return f"{base}&page={page}" if page > 1 else base

    # ------------------------------------------------------------------
    # Estratégia 1: Algolia API direta
    # ------------------------------------------------------------------

    def _algolia_search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """
        Consulta diretamente a API Algolia da Leroy Merlin.
        Usa as credenciais públicas expostas no HTML da página.
        """
        payload = {
            "query": keyword,
            "hitsPerPage": _ITEMS_PER_PAGE,
            "page": page - 1,  # Algolia é 0-indexed
            "attributesToRetrieve": [
                "description", "name", "title", "productName",
                "price", "preco", "sellingPrice", "bestPrice",
                "priceRange", "rating", "ratingAverage",
                "reviewCount", "totalReviews", "objectID",
            ],
        }
        try:
            resp = requests.post(
                _ALGOLIA_SEARCH_URL,
                headers=_ALGOLIA_HEADERS,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", [])
            nb_pages = data.get("nbPages", 1)
            if hits:
                logger.info(
                    f"[{self.platform_name}] Algolia: {len(hits)} hits "
                    f"(página {page}/{nb_pages})"
                )
                return self._parse_algolia_hits(hits, keyword, keyword_category_map, page_offset)
            logger.debug(f"[{self.platform_name}] Algolia: 0 hits para '{keyword}' página {page}")
            return []
        except Exception as e:
            logger.warning(f"[{self.platform_name}] Algolia API erro: {e}")
            return []

    def _parse_algolia_hits(
        self,
        hits: List[Dict],
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        records = []
        for idx, hit in enumerate(hits):
            title = (
                hit.get("description")
                or hit.get("name")
                or hit.get("productName")
                or hit.get("title")
            )
            # Algolia Leroy: price pode ser float direto ou em priceRange
            price_val = (
                hit.get("price")
                or hit.get("preco")
                or hit.get("sellingPrice")
                or hit.get("bestPrice")
                or (hit.get("priceRange") or {}).get("sellingPrice", {}).get("lowPrice")
                or (hit.get("priceRange") or {}).get("minPrice")
            )
            # Alguns hits têm prices como dict {"value": 123.45}
            if isinstance(price_val, dict):
                price_val = price_val.get("value") or price_val.get("amount")

            try:
                price_float = float(str(price_val).replace(",", ".")) if price_val else None
            except (ValueError, TypeError):
                price_float = None

            rating = hit.get("rating") or hit.get("ratingAverage")
            review_count = hit.get("reviewCount") or hit.get("totalReviews")
            pos = page_offset + idx + 1

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos,
                position_organic=pos,
                position_sponsored=None,
                price_float=price_float,
                seller="Leroy Merlin",
                is_fulfillment=False,
                rating=float(rating) if rating else None,
                review_count=int(review_count) if review_count else None,
                tag_destaque=None,
            ))
        return records

    # ------------------------------------------------------------------
    # Estratégia 2: XHR interception (Algolia + APIs internas)
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
                # Algolia response format
                products = (
                    data.get("hits")
                    or data.get("products")
                    or data.get("items")
                    or data.get("data", {}).get("products")
                    or self._deep_find_products(data)
                    or []
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

    @staticmethod
    def _deep_find_products(obj, depth: int = 0) -> List[Dict]:
        """
        Busca recursiva por array de produtos em objetos JSON aninhados.
        Considera array de produto quando tem ≥3 itens com chave 'id' ou 'sku'.
        """
        if depth > 6:
            return []
        if isinstance(obj, list) and len(obj) >= 3:
            if any(isinstance(i, dict) and ("id" in i or "sku" in i or "name" in i or "objectID" in i) for i in obj):
                return obj
        if isinstance(obj, dict):
            for v in obj.values():
                result = LeroyMerlinScraper._deep_find_products(v, depth + 1)
                if result:
                    return result
        return []

    def _parse_captured_products(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """Converte produtos capturados via XHR (Algolia hits ou formato genérico)."""
        return self._parse_algolia_hits(
            self._captured_products, keyword, keyword_category_map, page_offset
        )

    # ------------------------------------------------------------------
    # Estratégia 3: DOM parse
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
                seller="Leroy Merlin",
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
            path = log_dir / f"leroy_debug_p{page}_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo: {path}\n"
                "  → Nota: Leroy usa Algolia. Verifique logs de XHR acima."
            )
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Espera
    # ------------------------------------------------------------------

    def _wait_for_products(self, timeout_ms: int = 15_000) -> bool:
        for sel in _SELECTORS["item_candidates"][:6]:
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

            # --- Estratégia 1: Algolia API direta ---
            offset = (page - 1) * _ITEMS_PER_PAGE
            records = self._algolia_search(keyword, keyword_category_map, page, offset)

            if not records:
                # Carrega a página para acionar XHR interception e DOM fallback
                try:
                    self._page.goto(url, wait_until="domcontentloaded")
                    self._wait_for_products(timeout_ms=15_000)
                    self._wait_for_network_idle()
                    self._random_delay(min_s=2.5, max_s=6.0)
                    self._human_scroll(steps=8, step_px=350)
                    time.sleep(1.5)

                    html = self._page.content()

                    # --- Estratégia 2: XHR capturado (Algolia via browser) ---
                    if self._captured_products:
                        logger.info(
                            f"[{self.platform_name}] {len(self._captured_products)} itens via XHR"
                        )
                        records = self._parse_captured_products(
                            keyword, keyword_category_map, offset
                        )

                    # --- Estratégia 3: DOM ---
                    if not records:
                        records = self._parse_dom(
                            html, keyword, keyword_category_map, page, offset
                        )

                except Exception as exc:
                    logger.error(f"[{self.platform_name}] Erro na página {page}: {exc}")
                    raise

            all_records.extend(records)

            if not records:
                logger.warning(
                    f"[{self.platform_name}] Página {page} retornou 0 itens. "
                    "Parando keyword."
                )
                break

            if page < page_limit:
                self._random_delay()

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
