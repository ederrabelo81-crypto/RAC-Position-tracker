"""
scrapers/shopee.py — Scraper da Shopee Brasil (shopee.com.br).

Estratégia (em ordem de prioridade):
  1. Intercepção de resposta XHR via page.on("response", ...) — mais estável
     que page.evaluate(fetch()) porque não depende do contexto JS da página.
  2. __NEXT_DATA__ JSON embutido (Shopee usa Next.js SSR na página de busca).
  3. Parse DOM com seletores fallback quando XHR não capturar dados.
  4. Debug HTML dump em logs/ quando todas as estratégias retornarem 0 itens.

Notas de manutenção:
  A Shopee faz redirecionamentos internos após o carregamento inicial (SPA),
  destruindo o contexto JS de execução. Por isso, usar page.evaluate() para
  chamar fetch() é frágil. A intercepção de respostas contorna esse problema
  porque o listener persiste independente de navegações na página.
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
    # Seletores DOM — cadeia de fallback para múltiplas versões do layout
    "item_candidates": [
        '[data-sqe="item"]',                          # v1 clássico
        'li[class*="shopee-search-item-result"]',     # v2
        'li[class*="col-xs-2-4"]',                    # grid padrão Shopee
        'div[class*="shopee-item-card"]',
        '[class*="product-briefing"]',
        'li[class*="search-item"]',
        '[class*="item-card"]',
        'a[data-sqe="link"][href*="/product/"]',      # fallback por link
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
    # Filtros de detecção
    "bot_check": "#robot-verify, [class*='bot-verify'], #captcha",
}

# Padrões de URL que indicam resposta da API de busca da Shopee
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
    # Intercepção XHR — captura resposta da API de busca
    # ------------------------------------------------------------------

    def _setup_xhr_intercept(self) -> None:
        """Registra listener para capturar respostas da API de busca da Shopee."""
        self._captured_items = []

        def handle_response(response):
            try:
                url = response.url
                if not any(pat in url for pat in _API_URL_PATTERNS):
                    return
                if response.status != 200:
                    return
                # Aceita qualquer resposta que seja texto (json)
                ct = response.headers.get("content-type", "").lower()
                if ct and "text/html" in ct:
                    return  # descarta HTML

                try:
                    body = response.text()
                    data = json.loads(body)
                except Exception:
                    return

                # Shopee pode retornar items em vários lugares
                items = (
                    data.get("items")
                    or data.get("data", {}).get("items")
                    or (data.get("result") or {}).get("items")
                    or []
                )
                if items:
                    self._captured_items.extend(items)
                    logger.debug(
                        f"[{self.platform_name}] XHR capturado: {len(items)} itens "
                        f"em {url[:70]}"
                    )
            except Exception:
                pass  # ignora respostas binárias ou erros de parse

        self._page.on("response", handle_response)

    def _parse_captured_items(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """Converte itens capturados via XHR em registros padronizados."""
        records = []

        for idx, item in enumerate(self._captured_items):
            info = item.get("item_basic") or item  # versões diferentes do JSON

            title       = info.get("name")
            price_cents = info.get("price") or info.get("price_min")
            price_float = price_cents / 100_000 if price_cents else None

            seller = info.get("shop_name") or "Shopee"
            rating_raw   = info.get("item_rating", {}).get("rating_star")
            review_raw   = info.get("item_rating", {}).get("rating_count", [0])
            review_count = sum(review_raw) if isinstance(review_raw, list) else review_raw

            pos_general = page_offset + idx + 1
            tag_labels  = info.get("label_ids", [])
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
                rating=float(rating_raw) if rating_raw else None,
                review_count=int(review_count) if review_count else None,
                tag_destaque=tag,
            ))

        return records

    # ------------------------------------------------------------------
    # Estratégia 2: __NEXT_DATA__ (Shopee usa Next.js SSR)
    # ------------------------------------------------------------------

    def _extract_next_data(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """Tenta extrair produtos do __NEXT_DATA__ injetado pelo Next.js SSR."""
        try:
            match = re.search(
                r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                html, re.DOTALL,
            )
            if not match:
                return []

            data = json.loads(match.group(1))
            # Navega por estrutura Next.js
            page_props = data.get("props", {}).get("pageProps", {})

            # Tenta encontrar array de items
            def find_items(obj, depth=0):
                if depth > 6:
                    return []
                if isinstance(obj, list) and len(obj) >= 3:
                    if any(isinstance(i, dict) and ("itemid" in i or "name" in i or "price" in i) for i in obj):
                        return obj
                if isinstance(obj, dict):
                    for v in obj.values():
                        result = find_items(v, depth + 1)
                        if result:
                            return result
                return []

            items = find_items(page_props)
            if not items:
                return []

            logger.info(f"[{self.platform_name}] {len(items)} itens via __NEXT_DATA__")
            # Usa o mesmo parser de XHR (formato similar)
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
    def _first_match(soup_or_tag, candidates: List[str]):
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
        """Fallback: extrai produtos do HTML renderizado."""
        soup = self._get_soup()

        if soup.select_one(_SELECTORS["bot_check"]):
            logger.warning(f"[{self.platform_name}] Bot-check detectado no DOM.")
            return []

        # Detecta container de produto
        items = []
        sel_used = "nenhum"
        for sel in _SELECTORS["item_candidates"]:
            items = soup.select(sel)
            if len(items) >= 3:
                sel_used = sel
                logger.debug(f"[{self.platform_name}] DOM seletor usado: {sel}")
                break

        logger.info(f"[{self.platform_name}] {len(items)} itens via DOM (fallback)")

        if not items:
            html = self._page.content()
            self._dump_debug_html(html, keyword)
            return []

        records = []
        for idx, item in enumerate(items):
            title_el = self._first_match(item, _SELECTORS["title_candidates"])
            price_el = self._first_match(item, _SELECTORS["price_candidates"])

            # Fallback de título por img[alt]
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

    def _dump_debug_html(self, html: str, keyword: str) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"shopee_debug_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens no DOM — HTML salvo: {path}"
            )
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Espera por produtos no DOM
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
    # Search principal
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=5, max=18),
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

        # Configura intercepção XHR ANTES de qualquer navegação
        self._setup_xhr_intercept()

        # Visita home para estabelecer cookies de sessão
        try:
            self._page.goto("https://shopee.com.br", wait_until="domcontentloaded")
            self._random_delay(min_s=2.5, max_s=5.0)
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] Erro ao visitar home: {exc}")

        for page in range(1, page_limit + 1):
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit}")
            offset = (page - 1) * _ITEMS_PER_PAGE

            try:
                encoded = quote_plus(keyword)
                search_url = (
                    f"https://shopee.com.br/search"
                    f"?keyword={encoded}&page={page - 1}"  # Shopee usa 0-indexed
                )

                # Limpa capturas antes de navegar para nova página
                self._captured_items = []

                self._page.goto(search_url, wait_until="domcontentloaded")

                # Aguarda produtos ou XHR (máx 15s cada)
                self._wait_for_products(timeout_ms=15_000)
                self._wait_for_network_idle()
                self._random_delay(min_s=3.0, max_s=7.0)
                self._human_scroll(steps=8, step_px=300)

                # Aguarda mais um pouco para XHR tardio ser capturado
                time.sleep(2.0)

                html = self._page.content()
                records: List[Dict[str, Any]] = []

                # --- Estratégia 1: XHR capturado ---
                if self._captured_items:
                    logger.info(
                        f"[{self.platform_name}] {len(self._captured_items)} itens via XHR"
                    )
                    records = self._parse_captured_items(
                        keyword, keyword_category_map, offset
                    )

                # --- Estratégia 2: __NEXT_DATA__ ---
                if not records:
                    records = self._extract_next_data(
                        html, keyword, keyword_category_map, offset
                    )

                # --- Estratégia 3: DOM fallback ---
                if not records:
                    logger.info(
                        f"[{self.platform_name}] Sem dados XHR/__NEXT_DATA__ — usando DOM fallback."
                    )
                    records = self._parse_dom(keyword, keyword_category_map, offset)

                all_records.extend(records)

                if not records:
                    logger.warning(
                        f"[{self.platform_name}] Página {page} retornou 0 itens. "
                        "Parando keyword."
                    )
                    break

                if page < page_limit:
                    self._random_delay(min_s=2.0, max_s=5.0)

            except Exception as exc:
                logger.error(f"[{self.platform_name}] Erro na página {page}: {exc}")
                raise

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
