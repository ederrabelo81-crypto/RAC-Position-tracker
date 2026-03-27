"""
scrapers/shopee.py — Scraper da Shopee Brasil (shopee.com.br).

Estratégia (em ordem de prioridade):
  1. Intercepção XHR via page.on("response") — persiste durante redirecionamentos SPA.
  2. __NEXT_DATA__ JSON embutido (Shopee usa Next.js SSR).
  3. Parse DOM com seletores fallback.
  4. Debug HTML dump em logs/ quando 0 itens.

Proteção anti-bot:
  A Shopee redireciona silenciosamente para /buyer/login quando detecta automação.
  O scraper detecta esse redirect logo após o goto() e falha rapidamente (fail fast)
  em vez de aguardar 76s por XHR de produtos que nunca chegam.
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, LOGS_DIR
from scrapers.base import BaseScraper
from utils.text import parse_rating

_ITEMS_PER_PAGE = 60

_SELECTORS = {
    "item_candidates": [
        '[data-sqe="item"]',
        'li[class*="shopee-search-item-result"]',
        'li[class*="col-xs-2-4"]',
        'div[class*="shopee-item-card"]',
        '[class*="product-briefing"]',
        'li[class*="search-item"]',
        '[class*="item-card"]',
        'a[data-sqe="link"][href*="/product/"]',
    ],
    "title_candidates": [
        '[data-sqe="name"]',
        '[class*="shopee-item-card__text-name"]',
        '[class*="item-card-content__text--title"]',
        '[class*="name"]',
        'div[class*="truncate"]',
        'span[class*="name"]',
    ],
    "price_candidates": [
        '.shopee-price',
        '[class*="shopee-price"]',
        '[class*="price-current"]',
        '[class*="price"]',
    ],
    "bot_check": "#robot-verify, [class*='bot-verify'], #captcha",
}

# URLs que indicam redirecionamento anti-bot (fail fast)
_BLOCKED_URL_PATTERNS = [
    "/buyer/login",
    "/verify",
    "/login?",
    "captcha",
    "robot",
    "blocked",
]

_API_URL_PATTERNS = [
    "api/v4/search/search_items",
    "api/v4/recommend/recommend_search",
    "api/v2/search_items",
    "search/search_items",
    "search_items",
]


class ShopeeScraper(BaseScraper):
    """Scraper modular para a Shopee Brasil."""

    platform_name = "Shopee"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._captured_items: List[Dict] = []

    # ------------------------------------------------------------------
    # Detecção de redirect anti-bot (fail fast)
    # ------------------------------------------------------------------

    def _check_blocked(self) -> bool:
        """
        Verifica se a Shopee redirecionou para login/captcha após a navegação.
        Retorna True se bloqueado, False se OK.
        """
        current_url = self._page.url
        for pattern in _BLOCKED_URL_PATTERNS:
            if pattern in current_url:
                logger.warning(
                    f"[{self.platform_name}] Redirecionado para bloqueio/login: {current_url}\n"
                    "  → Shopee detectou automação. Tente: (1) aumentar delays, "
                    "(2) visitar mais páginas antes da busca, (3) proxy residencial."
                )
                self._dump_debug_html(self._page.content(), "bloqueio")
                return True

        # Verifica também pelo conteúdo HTML
        html = self._page.content()
        if any(p in html[:3000] for p in ['og:url" content="https://shopee.com.br/buyer/login',
                                           '"loginRedirect"', 'id="login-form"']):
            logger.warning(
                f"[{self.platform_name}] HTML de login detectado (sem redirect de URL). "
                "Bot bloqueado pela Shopee."
            )
            self._dump_debug_html(html, "login_html")
            return True

        return False

    # ------------------------------------------------------------------
    # XHR interception
    # ------------------------------------------------------------------

    def _setup_xhr_intercept(self) -> None:
        self._captured_items = []

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
                items = (
                    data.get("items")
                    or data.get("data", {}).get("items")
                    or (data.get("result") or {}).get("items")
                    or []
                )
                if items:
                    self._captured_items.extend(items)
                    logger.debug(
                        f"[{self.platform_name}] XHR: {len(items)} itens em {url[:70]}"
                    )
            except Exception:
                pass

        self._page.on("response", handle_response)

    def _parse_captured_items(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        records = []
        for idx, item in enumerate(self._captured_items):
            info = item.get("item_basic") or item
            title       = info.get("name")
            price_cents = info.get("price") or info.get("price_min")
            price_float = price_cents / 100_000 if price_cents else None
            seller       = info.get("shop_name") or "Shopee"
            rating_raw   = info.get("item_rating", {}).get("rating_star")
            review_raw   = info.get("item_rating", {}).get("rating_count", [0])
            review_count = sum(review_raw) if isinstance(review_raw, list) else review_raw
            pos_general  = page_offset + idx + 1
            tag          = "Destaque" if info.get("label_ids") else None

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
                rating=float(rating_raw) if rating_raw else None,
                review_count=int(review_count) if review_count else None,
                tag_destaque=tag,
            ))
        return records

    # ------------------------------------------------------------------
    # __NEXT_DATA__ (Next.js SSR)
    # ------------------------------------------------------------------

    def _extract_next_data(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        try:
            match = re.search(
                r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                html, re.DOTALL,
            )
            if not match:
                return []
            data = json.loads(match.group(1))
            page_props = data.get("props", {}).get("pageProps", {})

            def find_items(obj, depth=0):
                if depth > 6:
                    return []
                if isinstance(obj, list) and len(obj) >= 3:
                    if any(isinstance(i, dict) and ("itemid" in i or "name" in i or "price" in i) for i in obj):
                        return obj
                if isinstance(obj, dict):
                    for v in obj.values():
                        r = find_items(v, depth + 1)
                        if r:
                            return r
                return []

            items = find_items(page_props)
            if not items:
                return []
            logger.info(f"[{self.platform_name}] {len(items)} itens via __NEXT_DATA__")
            old = self._captured_items
            self._captured_items = items
            records = self._parse_captured_items(keyword, keyword_category_map, page_offset)
            self._captured_items = old
            return records
        except Exception as e:
            logger.debug(f"[{self.platform_name}] __NEXT_DATA__ erro: {e}")
            return []

    # ------------------------------------------------------------------
    # DOM fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _first_match(soup_or_tag, candidates):
        for sel in candidates:
            el = soup_or_tag.select_one(sel)
            if el:
                return el
        return None

    def _parse_dom(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        soup = self._get_soup()
        if soup.select_one(_SELECTORS["bot_check"]):
            logger.warning(f"[{self.platform_name}] Bot-check detectado no DOM.")
            return []

        items = []
        sel_used = "nenhum"
        for sel in _SELECTORS["item_candidates"]:
            items = soup.select(sel)
            if len(items) >= 3:
                sel_used = sel
                break

        logger.info(f"[{self.platform_name}] {len(items)} itens via DOM (seletor: {sel_used})")

        if not items:
            self._dump_debug_html(self._page.content(), keyword)
            return []

        records = []
        for idx, item in enumerate(items):
            title_el = self._first_match(item, _SELECTORS["title_candidates"])
            price_el = self._first_match(item, _SELECTORS["price_candidates"])
            title = title_el.get_text(strip=True) if title_el else None
            if not title:
                img = item.select_one("img[alt]")
                if img:
                    title = img.get("alt", "").strip() or None
            pos = page_offset + idx + 1
            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos,
                position_organic=pos,
                position_sponsored=None,
                price_raw=price_el.get_text(strip=True) if price_el else None,
                seller="Shopee",
                is_fulfillment=False,
                rating=None,
                review_count=None,
                tag_destaque=None,
            ))
        return records

    # ------------------------------------------------------------------
    # Debug dump
    # ------------------------------------------------------------------

    def _dump_debug_html(self, html: str, label: str) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe = label[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"shopee_debug_{safe}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(f"[{self.platform_name}] HTML salvo: {path}")
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Espera
    # ------------------------------------------------------------------

    def _wait_for_products(self, timeout_ms: int = 12_000) -> bool:
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
        reraise=False,  # não propaga — retorna [] em vez de quebrar o teste
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        all_records: List[Dict[str, Any]] = []
        self._setup_xhr_intercept()

        # Visita home para cookies de sessão
        try:
            self._page.goto("https://shopee.com.br", wait_until="domcontentloaded",
                            timeout=20_000)
            self._random_delay(min_s=2.0, max_s=4.0)
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] Erro ao visitar home: {exc}")

        for page in range(1, page_limit + 1):
            encoded = quote_plus(keyword)
            search_url = f"https://shopee.com.br/search?keyword={encoded}&page={page - 1}"
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {search_url}")
            self._captured_items = []
            offset = (page - 1) * _ITEMS_PER_PAGE

            try:
                self._page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:
                logger.warning(f"[{self.platform_name}] Timeout no goto: {exc}")
                break

            # ── FAIL FAST se redirecionado para login/captcha ──
            if self._check_blocked():
                break

            self._wait_for_products(timeout_ms=12_000)
            self._wait_for_network_idle()
            self._random_delay(min_s=3.0, max_s=6.0)
            self._human_scroll(steps=8, step_px=300)
            time.sleep(2.0)

            html = self._page.content()
            records: List[Dict[str, Any]] = []

            # Estratégia 1: XHR
            if self._captured_items:
                logger.info(f"[{self.platform_name}] {len(self._captured_items)} itens via XHR")
                records = self._parse_captured_items(keyword, keyword_category_map, offset)

            # Estratégia 2: __NEXT_DATA__
            if not records:
                records = self._extract_next_data(html, keyword, keyword_category_map, offset)

            # Estratégia 3: DOM
            if not records:
                records = self._parse_dom(keyword, keyword_category_map, offset)

            all_records.extend(records)

            if not records:
                logger.warning(f"[{self.platform_name}] Página {page} sem itens. Parando.")
                break

            if page < page_limit:
                self._random_delay(min_s=2.0, max_s=5.0)

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
