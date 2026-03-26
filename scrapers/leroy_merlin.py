"""
scrapers/leroy_merlin.py — Scraper da Leroy Merlin Brasil (leroymerlin.com.br).

Estratégia:
  - URL de busca: https://www.leroymerlin.com.br/busca?term={keyword}
  - A Leroy Merlin usa Next.js; resultados geralmente renderizados no servidor,
    tornando o parse de HTML mais confiável que em SPAs puras.
  - Paginação via query param: ?term=...&page={n}
  - Sem distinção orgânico/patrocinado significativa (foco em varejo).
"""

from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating, parse_review_count

_SELECTORS = {
    "item_container": 'div[class*="product-card"], article[class*="product"]',
    "title":          '[class*="product-title"], [class*="ProductName"]',
    "price":          '[class*="product-price"], [class*="Price"]',
    "rating":         '[class*="rating-value"], [class*="Rating"]',
    "review_count":   '[class*="rating-count"], [class*="ReviewCount"]',
    "tag_destaque":   '[class*="product-tag"], [class*="badge"]',
}


class LeroyMerlinScraper(BaseScraper):
    """Scraper modular para Leroy Merlin Brasil."""

    platform_name = "Leroy Merlin"

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        base = f"https://www.leroymerlin.com.br/busca?term={encoded}"
        if page > 1:
            return f"{base}&page={page}"
        return base

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        # Tenta seletores alternativos por possíveis variações do DOM
        items = soup.select(_SELECTORS["item_container"])
        logger.info(f"[{self.platform_name}] {len(items)} itens encontrados na página")

        records = []
        for idx, item in enumerate(items):
            pos = page_offset + idx + 1

            title_el  = item.select_one(_SELECTORS["title"])
            title     = title_el.get_text(strip=True) if title_el else None

            price_el  = item.select_one(_SELECTORS["price"])
            price_raw = price_el.get_text(strip=True) if price_el else None

            rating_el    = item.select_one(_SELECTORS["rating"])
            rating       = parse_rating(rating_el.get_text() if rating_el else None)

            reviews_el   = item.select_one(_SELECTORS["review_count"])
            review_count = parse_review_count(reviews_el.get_text() if reviews_el else None)

            tag_el = item.select_one(_SELECTORS["tag_destaque"])
            tag    = tag_el.get_text(strip=True) if tag_el else None

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos,
                position_organic=pos,
                position_sponsored=None,
                price_raw=price_raw,
                seller="Leroy Merlin",
                is_fulfillment=False,
                rating=rating,
                review_count=review_count,
                tag_destaque=tag,
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
        """Busca keyword na Leroy Merlin por até `page_limit` páginas."""
        all_records: List[Dict[str, Any]] = []

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()
                self._random_delay(min_s=2.0, max_s=5.0)
                self._human_scroll(steps=8, step_px=350)

                offset  = (page - 1) * 24  # Leroy exibe ~24 produtos por página
                records = self._parse_results(
                    html=self._page.content(),
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                    page_offset=offset,
                )
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
