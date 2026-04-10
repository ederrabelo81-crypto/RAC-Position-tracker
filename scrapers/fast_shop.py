"""
scrapers/fast_shop.py — Scraper da Fast Shop (fastshop.com.br).

Estratégia (em ordem de prioridade):
  0. API VTEX via curl_cffi — replica TLS fingerprint Chrome real, bypassa PerimeterX.
     PerimeterX descarta conexões Python (requests/urllib3) pelo JA3 fingerprint.
     curl_cffi com impersonate="chrome124" contorna isso na camada TLS.
  1. API VTEX Intelligent-Search via requests — fallback (pode ser bloqueado).
  2. Intercepção XHR da API VTEX IO (intelligent-search, catalog_system, GraphQL)
  3. Parse DOM com seletores VTEX IO + fallbacks genéricos
  4. Debug HTML dump em logs/ quando 0 itens

Plataforma: VTEX IO React — classes geradas como vtex-product-summary-2-x-*
URL de busca: /busca?q={keyword}
Paginação: &page={n} (1-indexed)
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

# curl_cffi: TLS fingerprint real do Chrome — bypassa PerimeterX JA3/JA4 detection.
# FastShop usa PerimeterX que descarta conexões Python com timeout silencioso.
try:
    from curl_cffi import requests as _cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

_ITEMS_PER_PAGE = 20

# VTEX Intelligent-Search endpoint padrão
_VTEX_SEARCH_URL = (
    "https://www.fastshop.com.br"
    "/_v/api/intelligent-search/product_search/pt/pt-BR/search"
)
_VTEX_CATALOG_URL = (
    "https://www.fastshop.com.br"
    "/api/catalog_system/pub/products/search"
)

_VTEX_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-vtex-uid": "false",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Timeout reduzido para a API direta — evita travar 15s × 3 tentativas
_API_TIMEOUT = 8

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
        # Fallback genérico
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

# Padrões de URL para XHR interception
_API_URL_PATTERNS = [
    "intelligent-search/product_search",
    "intelligent-search/product_search_v2",
    "catalog_system/pub/products",
    "api/io/_v/api",
    "_v/api/intelligent",
    "product-search",
    "search/products",
    "graphql",
    "searchQuery",
]


class FastShopScraper(BaseScraper):
    """Scraper modular para a Fast Shop."""

    platform_name = "Fast Shop"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._captured_products: List[Dict] = []

    # ------------------------------------------------------------------
    # URL do browser
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        url = f"https://www.fastshop.com.br/busca?q={encoded}"
        if page > 1:
            url += f"&page={page}"
        return url

    # ------------------------------------------------------------------
    # Estratégia 0: VTEX API via curl_cffi (TLS fingerprint Chrome real)
    # ------------------------------------------------------------------

    def _vtex_cffi_search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """
        Consulta a API VTEX usando curl_cffi com impersonation do Chrome124.
        A Fast Shop usa PerimeterX que faz timeout silencioso em conexões Python
        (JA3 fingerprint diferente do Chrome). curl_cffi replica o TLS handshake
        exato do Chrome real, contornando a detecção na camada de transporte.
        """
        if not _HAS_CURL_CFFI:
            return []

        cffi_session = _cffi_requests.Session()
        params_is = {
            "query": keyword,
            "page": page,
            "count": _ITEMS_PER_PAGE,
            "sort": "score_desc",
            "hideUnavailableItems": "false",
        }

        # Endpoint 1: Intelligent-Search
        try:
            resp = cffi_session.get(
                _VTEX_SEARCH_URL,
                headers=_VTEX_HEADERS,
                params=params_is,
                impersonate="chrome124",
                timeout=15,
            )
            if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
                data = resp.json()
                products = (
                    data.get("products")
                    or data.get("data", {}).get("products")
                    or (data.get("productSearch") or {}).get("products")
                    or []
                )
                if products:
                    logger.info(
                        f"[{self.platform_name}] VTEX curl_cffi IS: "
                        f"{len(products)} produtos (pág {page})"
                    )
                    return self._parse_vtex_products(products, keyword, keyword_category_map, page_offset)
            else:
                logger.debug(
                    f"[{self.platform_name}] curl_cffi IS: HTTP {resp.status_code}"
                )
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] curl_cffi IS erro: {exc}")

        # Endpoint 2: Catalog System (fallback)
        from_idx = page_offset
        to_idx   = page_offset + _ITEMS_PER_PAGE - 1
        try:
            encoded = __import__("urllib.parse", fromlist=["quote_plus"]).quote_plus(keyword)
            resp = cffi_session.get(
                f"{_VTEX_CATALOG_URL}/{encoded}",
                headers=_VTEX_HEADERS,
                params={"_from": from_idx, "_to": to_idx},
                impersonate="chrome124",
                timeout=15,
            )
            if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
                products = resp.json()
                if isinstance(products, list) and products:
                    logger.info(
                        f"[{self.platform_name}] VTEX curl_cffi catalog: "
                        f"{len(products)} produtos (pág {page})"
                    )
                    return self._parse_vtex_products(products, keyword, keyword_category_map, page_offset)
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] curl_cffi catalog erro: {exc}")

        return []

    # ------------------------------------------------------------------
    # Estratégia 1: VTEX Intelligent-Search API direta (requests)
    # ------------------------------------------------------------------

    def _vtex_search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """
        Chama a API VTEX Intelligent-Search diretamente sem browser.
        Tenta endpoint v1 (query param) e catalog_system como fallback.
        """
        params = {
            "query": keyword,
            "page": page,
            "count": _ITEMS_PER_PAGE,
            "sort": "score_desc",
            "hideUnavailableItems": "false",
        }
        try:
            resp = requests.get(
                _VTEX_SEARCH_URL,
                headers=_VTEX_HEADERS,
                params=params,
                timeout=_API_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                products = (
                    data.get("products")
                    or data.get("data", {}).get("products")
                    or (data.get("productSearch") or {}).get("products")
                    or []
                )
                if products:
                    logger.info(
                        f"[{self.platform_name}] VTEX IS API: {len(products)} produtos (pág {page})"
                    )
                    return self._parse_vtex_products(products, keyword, keyword_category_map, page_offset)
                logger.debug(f"[{self.platform_name}] VTEX IS API: 0 produtos para '{keyword}'")
        except Exception as e:
            logger.debug(f"[{self.platform_name}] VTEX IS API erro: {e}")

        # Fallback: catalog_system
        from_idx = page_offset
        to_idx   = page_offset + _ITEMS_PER_PAGE - 1
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
                        f"[{self.platform_name}] VTEX catalog API: {len(products)} produtos (pág {page})"
                    )
                    return self._parse_vtex_products(products, keyword, keyword_category_map, page_offset)
        except Exception as e:
            logger.debug(f"[{self.platform_name}] VTEX catalog API erro: {e}")

        return []

    def _parse_vtex_products(
        self,
        products: List[Dict],
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        records = []
        for idx, prod in enumerate(products):
            title = (
                prod.get("productName")
                or prod.get("name")
                or prod.get("title")
            )
            # VTEX: price em items[0].sellers[0].commertialOffer.Price
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
    # Estratégia 2: XHR interception
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
                    body = response.text()
                    data = json.loads(body)
                except Exception:
                    return

                # Tenta encontrar lista de produtos em vários formatos
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
                        f"[{self.platform_name}] XHR capturado: "
                        f"{len(products)} produtos em {url[:70]}"
                    )
            except Exception:
                pass

        self._page.on("response", handle_response)

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

            # Fallback de título
            title = title_el.get_text(strip=True) if title_el else None
            if not title:
                img = item.select_one("img[alt]")
                if img:
                    title = img.get("alt", "").strip() or None

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
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
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=8, max=20),
        reraise=False,  # retorna [] em vez de quebrar o teste inteiro
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

            # --- Estratégia 0: VTEX API via curl_cffi (TLS fingerprint Chrome real) ---
            records = self._vtex_cffi_search(keyword, keyword_category_map, page, offset)

            # --- Estratégia 1: VTEX API via requests (fallback) ---
            if not records:
                records = self._vtex_search(keyword, keyword_category_map, page, offset)

            if not records:
                # Carrega browser para XHR interception + DOM fallback
                try:
                    self._page.goto(url, wait_until="domcontentloaded", timeout=35_000)
                except Exception as exc:
                    logger.warning(
                        f"[{self.platform_name}] Timeout ao carregar página {page}: {exc}\n"
                        "  → Site pode estar fora do ar ou bloqueando. Parando keyword."
                    )
                    break  # não tenta mais páginas, mas não quebra o teste

                try:
                    self._wait_for_products(timeout_ms=15_000)
                    self._wait_for_network_idle()
                    self._random_delay(min_s=2.5, max_s=6.5)
                    self._human_scroll(steps=8, step_px=300)
                    time.sleep(2.0)

                    # --- Estratégia 2: XHR capturado ---
                    if self._captured_products:
                        logger.info(
                            f"[{self.platform_name}] {len(self._captured_products)} itens via XHR"
                        )
                        records = self._parse_vtex_products(
                            self._captured_products, keyword, keyword_category_map, offset
                        )

                    # --- Estratégia 3: DOM ---
                    if not records:
                        records = self._parse_dom(
                            self._page.content(), keyword, keyword_category_map, page, offset
                        )

                except Exception as exc:
                    logger.error(f"[{self.platform_name}] Erro na página {page}: {exc}")
                    self._dump_debug(self._page.content() if self._page else "", page, keyword)
                    break

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
