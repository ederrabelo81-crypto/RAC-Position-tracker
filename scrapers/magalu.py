"""
scrapers/magalu.py — Scraper do Magazine Luiza (magazineluiza.com.br).

Estratégia de extração (em ordem de prioridade):
  1. Intercepção XHR da API interna (/api/product-search/v3/queries/search)
  2. Parse DOM com cadeia de seletores fallback (vários layouts)
  3. Dump de debug HTML quando 0 itens (para análise manual de seletores)

Proteções detectadas:
  - PerimeterX (px-captcha, _pxAppId)
  - Cloudflare (#challenge-form)
  - Página silenciosa (carregou mas sem conteúdo útil)

Paginação: parâmetro ?page={n} ou &page={n} na URL de busca.
"""

import json
import re
import time
import random
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, LOGS_DIR
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating, parse_review_count

# ---------------------------------------------------------------------------
# Seletores CSS — cadeia de fallback para múltiplas versões do layout
# ---------------------------------------------------------------------------
_SELECTORS = {
    # Container de produto — tenta em ordem até encontrar resultados.
    # Magalu migrou para design system "nm-" (New Magalu) em 2024/2025.
    # Os seletores nm-* têm prioridade; data-testid e sc-* como fallback legado.
    "item_candidates": [
        'li[class*="nm-product-card"]',               # nm design system (atual)
        '[data-testid="product-card-container"]',     # legado v3
        'li[data-testid="product-card"]',             # legado v1
        '[data-testid="item"]',                       # legado v2
        'li[class*="ProductCard"]',                   # styled-components v1
        'li[class*="product-card"]',                  # styled-components v2
        '[class*="ProductCard__Wrapper"]',
        'a[data-testid="product-card-link"]',         # link direto
        'li[class*="nm-"]',                           # nm genérico
        '[data-cy="product-card"]',
        'li > a[href*="/p/"]',                        # estrutura de link (último recurso)
    ],
    # Título do produto
    "title_candidates": [
        '[data-testid="product-title"]',              # legado
        '[data-testid="product-name"]',               # legado
        'h2[class*="nm-product-card"]',               # nm atual
        '[class*="nm-product-card__name"]',           # nm específico
        '[class*="nm-product-card__title"]',          # nm alternativo
        'h2[data-testid]',
        '[class*="ProductTitle"]',
        '[class*="product-title"]',
        '[class*="Title__Wrapper"]',
        'h2[class*="sc-"]',
        'h2',
    ],
    # Preço principal
    "price_candidates": [
        '[data-testid="price-value"]',                # legado
        '[data-testid="main-price"]',                 # legado
        '[class*="nm-price-details__main-price"]',    # nm atual
        '[class*="nm-price"]',                        # nm genérico
        'p[class*="nm-price"]',                       # nm em parágrafo
        '[class*="PriceTag__Price"]',
        '[class*="price-value"]',
        '[class*="Price__Value"]',
        'p[class*="Price"]',
    ],
    # Seller/lojista
    "seller_candidates": [
        '[data-testid="seller-name"]',
        '[class*="nm-seller"]',                       # nm atual
        '[class*="nm-product-card__seller"]',         # nm específico
        '[class*="SellerName"]',
        '[class*="seller-name"]',
        'a[href*="/loja/"]',
    ],
    # Avaliação
    "rating_candidates": [
        '[data-testid="review-score"]',
        '[class*="nm-rating"]',
        '[class*="Rating__Score"]',
        '[class*="rating-score"]',
    ],
    # Contagem de avaliações
    "review_count_candidates": [
        '[data-testid="review-count"]',
        '[data-testid="reviews-count"]',
        '[class*="nm-review-count"]',
        '[class*="ReviewCount"]',
        '[class*="review-count"]',
    ],
    # Tag destaque (Best Seller, Mais Vendido, etc.)
    "tag_candidates": [
        '[data-testid="product-tag"]',
        '[data-testid="badge"]',
        '[class*="nm-badge"]',
        '[class*="ProductBadge"]',
        '[class*="Badge"]',
    ],
    # Patrocinado
    "sponsored_candidates": [
        '[data-testid="sponsored-tag"]',
        '[class*="nm-sponsored"]',
        '[class*="Sponsored"]',
        '[class*="sponsored"]',
    ],
    # Detecção de bloqueio
    "px_block":    "#px-captcha, #pxCaptcha, [id*='px-'], [class*='px-captcha']",
    "cf_block":    "#challenge-form, #challenge-running",
    "no_results":  '[data-testid="no-results"], [class*="NoResults"], [class*="empty-results"]',
}

# URL da API interna XHR do Magalu (mais estável que HTML)
_API_URL = (
    "https://www.magazineluiza.com.br/api/product-search/v3/queries/search"
    "?query={kw}&page={page}&size=24&sort=relevance&include=facets,suggestions"
)


class MagaluScraper(BaseScraper):
    """Scraper modular para o Magazine Luiza."""

    platform_name = "Magalu"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._api_results: List[Dict] = []   # resultados capturados via XHR

    # ------------------------------------------------------------------
    # URL builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        """URL de busca padrão da Magalu."""
        encoded = quote_plus(keyword)
        base = f"https://www.magazineluiza.com.br/busca/{encoded}/"
        return f"{base}?page={page}" if page > 1 else base

    @staticmethod
    def _build_url_v2(keyword: str, page: int = 1) -> str:
        """URL alternativa usada em algumas versões do site (?q= format)."""
        encoded = quote_plus(keyword)
        return (
            f"https://www.magazineluiza.com.br/busca/"
            f"?q={encoded}&from=submit&page={page}"
        )

    # ------------------------------------------------------------------
    # Intercepção XHR — captura resposta da API interna
    # ------------------------------------------------------------------

    def _setup_xhr_intercept(self) -> None:
        """Registra listener para capturar respostas da API interna do Magalu."""
        self._api_results = []

        def handle_response(response):
            try:
                url = response.url
                if (
                    "product-search" in url
                    or "search_items" in url
                    or ("/api/" in url and "search" in url)
                ) and response.status == 200:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        data = response.json()
                        self._api_results.append(data)
                        logger.debug(
                            f"[{self.platform_name}] XHR capturado: {url[:80]}"
                        )
            except Exception:
                pass  # respostas binárias/erros não bloqueiam

        self._page.on("response", handle_response)

    def _parse_api_results(
        self,
        data: Dict,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """Extrai registros da resposta JSON da API interna."""
        records = []

        # Tenta diferentes estruturas conhecidas da API
        products = (
            data.get("products")
            or data.get("items")
            or data.get("results")
            or data.get("data", {}).get("products")
            or []
        )

        org_ctr = spo_ctr = 0
        for idx, prod in enumerate(products):
            pos_general = page_offset + idx + 1
            sponsored = prod.get("isSponsored") or prod.get("sponsored", False)

            if sponsored:
                spo_ctr += 1
                pos_organic, pos_sponsored = None, spo_ctr
            else:
                org_ctr += 1
                pos_organic, pos_sponsored = org_ctr, None

            # Normaliza campos que podem ter nomes diferentes entre versões
            title = (
                prod.get("title")
                or prod.get("name")
                or prod.get("description")
            )
            price_val = (
                prod.get("price")
                or prod.get("sellPrice")
                or prod.get("bestPrice")
                or prod.get("priceValue")
            )
            _seller_raw = prod.get("seller")
            seller = (
                prod.get("sellerName")
                or (_seller_raw.get("name") if isinstance(_seller_raw, dict) else _seller_raw)
                or "Magalu"
            )
            rating = prod.get("rating") or prod.get("ratingAverage")
            review_count = prod.get("reviewCount") or prod.get("ratingsCount")
            tag = prod.get("badge") or prod.get("tag")

            try:
                price_float = float(str(price_val).replace(",", ".")) if price_val else None
            except (ValueError, TypeError):
                price_float = None

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_float=price_float,
                seller=seller,
                is_fulfillment=False,
                rating=parse_rating(str(rating)) if rating else None,
                review_count=int(review_count) if review_count else None,
                tag_destaque=str(tag) if tag else None,
            ))

        return records

    # ------------------------------------------------------------------
    # Parse DOM — com cadeia de fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _first_match(item: Tag, candidates: List[str]) -> Optional[Tag]:
        """Retorna o primeiro elemento que combinar com qualquer seletor."""
        for sel in candidates:
            el = item.select_one(sel)
            if el:
                return el
        return None

    @staticmethod
    def _detect_items(soup: BeautifulSoup) -> tuple[List[Tag], str]:
        """
        Itera pelos seletores de container até encontrar ≥3 itens.
        Retorna (items, seletor_usado).
        """
        for sel in _SELECTORS["item_candidates"]:
            items = soup.select(sel)
            if len(items) >= 3:
                return items, sel
        return [], "nenhum"

    @staticmethod
    def _is_sponsored_dom(item: Tag) -> bool:
        for sel in _SELECTORS["sponsored_candidates"]:
            if item.select_one(sel):
                return True
        text = item.get_text(" ", strip=True).lower()
        return "patrocinado" in text or "sponsored" in text

    def _parse_results_dom(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        # Detecção de bloqueios silenciosos
        if soup.select_one(_SELECTORS["px_block"]):
            logger.warning(f"[{self.platform_name}] PerimeterX detectado (página {page})")
            return []
        if soup.select_one(_SELECTORS["cf_block"]):
            logger.warning(f"[{self.platform_name}] Cloudflare challenge detectado (página {page})")
            return []

        items, sel_used = self._detect_items(soup)
        logger.info(
            f"[{self.platform_name}] {len(items)} itens encontrados na página "
            f"(seletor: {sel_used})"
        )

        # Debug dump quando vazio
        if len(items) == 0:
            self._dump_debug_html(html, page, keyword)
            return []

        records = []
        org_ctr = spo_ctr = 0

        for idx, item in enumerate(items):
            pos_general = page_offset + idx + 1
            sponsored = self._is_sponsored_dom(item)

            if sponsored:
                spo_ctr += 1
                pos_organic, pos_sponsored = None, spo_ctr
            else:
                org_ctr += 1
                pos_organic, pos_sponsored = org_ctr, None

            title_el  = self._first_match(item, _SELECTORS["title_candidates"])
            price_el  = self._first_match(item, _SELECTORS["price_candidates"])
            seller_el = self._first_match(item, _SELECTORS["seller_candidates"])
            rating_el = self._first_match(item, _SELECTORS["rating_candidates"])
            review_el = self._first_match(item, _SELECTORS["review_count_candidates"])
            tag_el    = self._first_match(item, _SELECTORS["tag_candidates"])

            # Título: fallback para img[alt] quando seletores CSS não encontram
            title = title_el.get_text(strip=True) if title_el else None
            if not title:
                img = item.select_one("img[alt]")
                if img:
                    title = img.get("alt", "").strip() or None

            # Preço: fallback regex R$ quando seletores CSS não encontram
            price_raw = price_el.get_text(strip=True) if price_el else None
            if not price_raw:
                item_text = item.get_text(" ", strip=True)
                m = re.search(r"R\$\s*[\d.,]+", item_text)
                if m:
                    price_raw = m.group(0)

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_raw=price_raw,
                seller=seller_el.get_text(strip=True) if seller_el else "Magalu",
                is_fulfillment=False,
                rating=parse_rating(rating_el.get_text() if rating_el else None),
                review_count=parse_review_count(review_el.get_text() if review_el else None),
                tag_destaque=tag_el.get_text(strip=True) if tag_el else None,
            ))

        return records

    # ------------------------------------------------------------------
    # Debug dump
    # ------------------------------------------------------------------

    def _dump_debug_html(self, html: str, page: int, keyword: str) -> None:
        """Salva HTML bruto em logs/ para análise manual de seletores."""
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"magalu_debug_p{page}_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo para diagnóstico: {path}\n"
                f"  → Abra o arquivo no browser e inspecione o seletor correto."
            )
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Espera inteligente por conteúdo
    # ------------------------------------------------------------------

    def _wait_for_products(self, timeout_ms: int = 15_000) -> bool:
        """
        Aguarda até que algum seletor de container de produto apareça.
        Retorna True se encontrou, False se timeout.
        """
        # Inclui nm-* no topo — design system atual do Magalu
        for sel in _SELECTORS["item_candidates"][:6]:  # testa os 6 primeiros
            try:
                self._page.wait_for_selector(sel, timeout=timeout_ms)
                logger.debug(f"[{self.platform_name}] Produtos encontrados com: {sel}")
                return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Search principal
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=6, max=25),
        reraise=True,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Busca keyword na Magalu por até `page_limit` páginas."""
        all_records: List[Dict[str, Any]] = []

        # Configura intercepção XHR antes de navegar
        self._setup_xhr_intercept()

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")

                # Aguarda produtos aparecerem (máx 15s)
                found = self._wait_for_products(timeout_ms=15_000)
                if not found:
                    logger.debug(
                        f"[{self.platform_name}] wait_for_products timeout — "
                        "continuando com o que carregou"
                    )

                # Aguarda rede estabilizar + delay humanizado
                self._wait_for_network_idle()
                self._random_delay(min_s=3.5, max_s=8.5)
                self._human_scroll(steps=10, step_px=280)
                self._random_delay(min_s=1.0, max_s=2.5)

                # --- Tenta usar resultados XHR capturados primeiro ---
                api_records: List[Dict[str, Any]] = []
                if self._api_results:
                    offset = (page - 1) * 24
                    for data in self._api_results:
                        api_records.extend(
                            self._parse_api_results(
                                data, keyword, keyword_category_map, offset
                            )
                        )
                    self._api_results = []   # limpa para próxima página

                if api_records:
                    logger.info(
                        f"[{self.platform_name}] {len(api_records)} itens via API XHR"
                    )
                    all_records.extend(api_records)
                else:
                    # --- Fallback: parse DOM ---
                    offset = (page - 1) * 40
                    records = self._parse_results_dom(
                        html=self._page.content(),
                        keyword=keyword,
                        keyword_category_map=keyword_category_map,
                        page=page,
                        page_offset=offset,
                    )

                    # Se ainda 0, tenta URL alternativa (?q= format) na pág 1
                    if not records and page == 1:
                        alt_url = self._build_url_v2(keyword, page)
                        logger.info(
                            f"[{self.platform_name}] 0 resultados — tentando URL alternativa: {alt_url}"
                        )
                        self._page.goto(alt_url, wait_until="domcontentloaded")
                        self._wait_for_products(timeout_ms=12_000)
                        self._wait_for_network_idle()
                        self._random_delay(min_s=2.5, max_s=6.0)
                        self._human_scroll(steps=8, step_px=280)

                        # Checa XHR capturado na segunda tentativa
                        if self._api_results:
                            for data in self._api_results:
                                records.extend(
                                    self._parse_api_results(
                                        data, keyword, keyword_category_map, 0
                                    )
                                )
                            self._api_results = []
                        else:
                            records = self._parse_results_dom(
                                html=self._page.content(),
                                keyword=keyword,
                                keyword_category_map=keyword_category_map,
                                page=page,
                                page_offset=0,
                            )

                    all_records.extend(records)
                    if not records:
                        logger.warning(
                            f"[{self.platform_name}] Página {page} retornou 0 itens — "
                            "possível bloqueio ou fim de resultados. Parando keyword."
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
