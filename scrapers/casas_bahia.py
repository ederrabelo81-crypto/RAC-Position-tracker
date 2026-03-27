"""
scrapers/casas_bahia.py — Scraper da Casas Bahia (casasbahia.com.br).

Estratégia (em ordem de prioridade):
  0. API VTEX direta via requests — bypass do Akamai WAF (chamadas HTTP simples
     não sofrem fingerprinting JS; tenta catalog_system e intelligent-search).
  1. Intercepção XHR da API VTEX IO via browser (se API direta falhar).
  2. Parse DOM com cadeia de seletores fallback + img[alt].
  3. Debug HTML dump automático em logs/ quando 0 itens.

Proteção: WAF Akamai / PerimeterX bloqueia browsers headless.
  A API direta contorna esse bloqueio na maioria dos casos.
  Se ainda bloquear: proxy residencial brasileiro.
Paginação: parâmetro &page={n}.
"""

import json
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

# Endpoints VTEX da Casas Bahia — chamados diretamente (sem browser, bypass Akamai)
_VTEX_BASE = "https://www.casasbahia.com.br"
_VTEX_CATALOG_URL = f"{_VTEX_BASE}/api/catalog_system/pub/products/search"
_VTEX_IS_URL      = f"{_VTEX_BASE}/_v/api/intelligent-search/product_search/pt/pt-BR/search"

_VTEX_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.casasbahia.com.br/",
}
_API_TIMEOUT = 8

_SELECTORS = {
    # Cadeia de fallback — Casas Bahia usa VTEX IO
    "item_candidates": [
        "[data-testid='product-card']",
        "[class*='ProductCard']",
        "[class*='product-card']",
        "article[class*='product']",
        "[class*='vtex-product-summary']",
        "li[class*='vtex-product-summary']",
        "[class*='productSummary']",
        "[class*='shelf-item']",
        "[class*='ShelfItem']",
        "li[class*='product']",
        "[class*='product-item']",
    ],
    "title_candidates": [
        "[data-testid='product-name']",
        "[class*='productName']",
        "[class*='ProductName']",
        "[class*='vtex-product-summary-2-x-productBrand']",
        "[class*='vtex-product-summary-2-x-nameContainer']",
        "h3[class*='product']",
        "h2[class*='product']",
        "h3", "h2",
    ],
    "price_candidates": [
        "[data-testid='price-best-price']",
        ".vtex-product-price-1-x-sellingPrice",
        "[class*='sellingPrice']",
        "[class*='bestPrice']",
        "[class*='productPrice']",
        "[class*='price']",
    ],
    "seller":        "[data-testid='seller-name'], [class*='sellerName']",
    "rating":        "[class*='ratingValue'], [class*='rating-value'], [class*='Rating']",
    "review_count":  "[class*='reviewCount'], [class*='review-count']",
    "tag_destaque":  "[data-testid='discount-badge'], [class*='discountBadge'], [class*='badge']",
    "sponsored":     "[data-testid='sponsored'], [class*='sponsored']",
    "waf_block":     "#ak-challenge-error, #challenge-container, .ak-challenge",
}

# Padrões de URL para XHR interception VTEX
_API_URL_PATTERNS = [
    "intelligent-search/product_search",
    "catalog_system/pub/products",
    "_v/api/intelligent",
    "product-search",
    "api/catalog",
    "search/products",
]

# Padrões de redirect/bloqueio
_BLOCKED_URL_PATTERNS = [
    "/login",
    "/captcha",
    "/blocked",
    "/challenge",
    "akamai",
]


class CasasBahiaScraper(BaseScraper):
    """Scraper modular para Casas Bahia."""

    platform_name = "Casas Bahia"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._captured_products: List[Dict] = []

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        url = f"https://www.casasbahia.com.br/busca?q={encoded}"
        if page > 1:
            url += f"&page={page}"
        return url

    # ------------------------------------------------------------------
    # Estratégia 0: VTEX API direta (bypass Akamai WAF)
    # ------------------------------------------------------------------

    def _vtex_api_search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """
        Consulta a API VTEX da Casas Bahia diretamente via requests.
        A requisição HTTP simples frequentemente não é bloqueada pelo Akamai,
        que foca em bloquear browsers automatizados (fingerprinting JS).
        Tenta dois endpoints: catalog_system e intelligent-search.
        """
        from_idx = page_offset
        to_idx   = page_offset + _ITEMS_PER_PAGE - 1

        # Endpoint 1: Catalog System (mais simples e estável)
        try:
            encoded = quote_plus(keyword)
            resp = requests.get(
                f"{_VTEX_CATALOG_URL}/{encoded}",
                headers=_VTEX_HEADERS,
                params={"_from": from_idx, "_to": to_idx},
                timeout=_API_TIMEOUT,
            )
            if resp.status_code == 200:
                products = resp.json()
                if isinstance(products, list) and products:
                    logger.info(
                        f"[{self.platform_name}] VTEX Catalog API: "
                        f"{len(products)} produtos (pág {page})"
                    )
                    return self._parse_api_products(keyword, keyword_category_map, page_offset,
                                                    products=products)
        except Exception as e:
            logger.debug(f"[{self.platform_name}] VTEX Catalog API erro: {e}")

        # Endpoint 2: Intelligent Search
        try:
            resp = requests.get(
                _VTEX_IS_URL,
                headers=_VTEX_HEADERS,
                params={
                    "query": keyword,
                    "page": page,
                    "count": _ITEMS_PER_PAGE,
                    "sort": "score_desc",
                    "hideUnavailableItems": "false",
                },
                timeout=_API_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                products = (
                    data.get("products")
                    or (data.get("productSearch") or {}).get("products")
                    or []
                )
                if products:
                    logger.info(
                        f"[{self.platform_name}] VTEX IS API: "
                        f"{len(products)} produtos (pág {page})"
                    )
                    return self._parse_api_products(keyword, keyword_category_map, page_offset,
                                                    products=products)
        except Exception as e:
            logger.debug(f"[{self.platform_name}] VTEX IS API erro: {e}")

        return []

    # ------------------------------------------------------------------
    # Detecção de bloqueio
    # ------------------------------------------------------------------

    def _check_blocked(self, html: str) -> bool:
        current_url = self._page.url
        for p in _BLOCKED_URL_PATTERNS:
            if p in current_url:
                logger.warning(f"[{self.platform_name}] Redirecionado para bloqueio: {current_url}")
                return True

        # Detecta página de erro Akamai WAF
        # ("Ops! Algo deu errado." + CSS de novavp-a.akamaihd.net)
        html_head = html[:8000]
        if "akamaihd.net" in html_head or "Ops! Algo deu errado" in html_head:
            logger.warning(
                f"[{self.platform_name}] Akamai WAF bloqueou a requisição. "
                "IP identificado como bot. Solução: proxy residencial brasileiro."
            )
            return True

        soup = BeautifulSoup(html_head, "html.parser")
        # Seletor WAF + classe page-not-found (Akamai)
        if soup.select_one(_SELECTORS["waf_block"]) or soup.select_one("body.page-not-found, .page-not-found"):
            logger.warning(f"[{self.platform_name}] WAF/Akamai detectado via seletor CSS.")
            return True

        return False

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
                ct = response.headers.get("content-type", "").lower()
                if "text/html" in ct:
                    return
                try:
                    data = json.loads(response.text())
                except Exception:
                    return
                products = (
                    data.get("products")
                    or data.get("items")
                    or data.get("data", {}).get("products")
                    or (data.get("productSearch") or {}).get("products")
                    or (data if isinstance(data, list) else [])
                )
                if products and isinstance(products, list):
                    self._captured_products.extend(products)
                    logger.debug(
                        f"[{self.platform_name}] XHR: {len(products)} produtos em {url[:70]}"
                    )
            except Exception:
                pass

        self._page.on("response", handle_response)

    def _parse_api_products(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
        products: Optional[List[Dict]] = None,
    ) -> List[Dict[str, Any]]:
        source = products if products is not None else self._captured_products
        records = []
        for idx, prod in enumerate(source):
            title = prod.get("productName") or prod.get("name") or prod.get("title")
            price_val = prod.get("price")
            try:
                offer = (
                    prod.get("items", [{}])[0]
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
                seller="Casas Bahia",
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
    def _first_match(tag: Tag, candidates) -> Optional[Tag]:
        if isinstance(candidates, str):
            return tag.select_one(candidates)
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
        items, sel_used = self._detect_items(soup)
        logger.info(
            f"[{self.platform_name}] {len(items)} itens (seletor: {sel_used})"
        )

        if not items:
            self._dump_debug(html, page, keyword)
            return []

        records = []
        sponsored_counter = 0
        organic_counter   = 0

        for idx, item in enumerate(items):
            sponsored = bool(item.select_one(_SELECTORS["sponsored"]))
            pos_general = page_offset + idx + 1

            if sponsored:
                sponsored_counter += 1
                pos_organic, pos_sponsored = None, sponsored_counter
            else:
                organic_counter += 1
                pos_organic, pos_sponsored = organic_counter, None

            title_el = self._first_match(item, _SELECTORS["title_candidates"])
            price_el = self._first_match(item, _SELECTORS["price_candidates"])

            # Fallback de título por img[alt]
            title = title_el.get_text(strip=True) if title_el else None
            if not title:
                img = item.select_one("img[alt]")
                if img:
                    title = img.get("alt", "").strip() or None

            seller_el = item.select_one(_SELECTORS["seller"])
            seller    = seller_el.get_text(strip=True) if seller_el else "Casas Bahia"

            rating_el    = item.select_one(_SELECTORS["rating"])
            reviews_el   = item.select_one(_SELECTORS["review_count"])
            tag_el       = item.select_one(_SELECTORS["tag_destaque"])

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_raw=price_el.get_text(strip=True) if price_el else None,
                seller=seller,
                is_fulfillment=False,
                rating=parse_rating(rating_el.get_text() if rating_el else None),
                review_count=parse_review_count(reviews_el.get_text() if reviews_el else None),
                tag_destaque=tag_el.get_text(strip=True) if tag_el else None,
            ))

        return records

    # ------------------------------------------------------------------
    # Debug dump — obrigatório quando 0 itens
    # ------------------------------------------------------------------

    def _dump_debug(self, html: str, page: int, keyword: str) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"casasbahia_debug_p{page}_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo para diagnóstico: {path}"
            )
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Espera
    # ------------------------------------------------------------------

    def _wait_for_products(self, timeout_ms: int = 12_000) -> bool:
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
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=6, max=20),
        reraise=False,
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
            offset = (page - 1) * _ITEMS_PER_PAGE
            records: List[Dict[str, Any]] = []

            # --- Estratégia 0: VTEX API direta (primário — bypass Akamai WAF) ---
            # Chamadas HTTP simples via requests geralmente não são bloqueadas pelo
            # Akamai Bot Manager, que foca em fingerprinting de browsers headless.
            records = self._vtex_api_search(keyword, keyword_category_map, page, offset)

            if not records:
                # --- Estratégias 1/2: Browser + XHR + DOM ---
                try:
                    self._page.goto(url, wait_until="domcontentloaded", timeout=40_000)
                except Exception as exc:
                    logger.warning(f"[{self.platform_name}] Timeout no goto: {exc}")
                    break

                self._wait_for_products(timeout_ms=4_000)
                self._wait_for_network_idle()
                self._random_delay(min_s=4.0, max_s=9.0)
                self._human_scroll(steps=10, step_px=300)
                time.sleep(1.5)

                html = self._page.content()

                # Detecta bloqueio Akamai (fail fast)
                if self._check_blocked(html):
                    self._dump_debug(html, page, keyword)
                    break

                # Estratégia 1: XHR capturado
                if self._captured_products:
                    logger.info(
                        f"[{self.platform_name}] {len(self._captured_products)} produtos via XHR"
                    )
                    records = self._parse_api_products(keyword, keyword_category_map, offset)

                # Estratégia 2: DOM
                if not records:
                    records = self._parse_dom(html, keyword, keyword_category_map, page, offset)

            all_records.extend(records)

            if not records:
                logger.warning(
                    f"[{self.platform_name}] Página {page} retornou 0 itens. Parando."
                )
                break

            if page < page_limit:
                self._random_delay()

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
