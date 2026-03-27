"""
scrapers/fast_shop.py — Scraper da Fast Shop (fastshop.com.br).

Estratégia:
  - URL de busca: https://www.fastshop.com.br/web/c/{slug}?q={keyword}
  - Fast Shop usa Angular/Vue; aguarda carregamento completo via networkidle.
  - Menor volume de resultados que Magalu/ML, mas foco em produtos premium
    como ar condicionado de alta capacidade (marca Daikin, Mitsubishi, etc.).
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
    "item_container": '.product-item, [class*="ProductCard"], [class*="product-card"]',
    "title":          '.product-name, [class*="productName"], [class*="ProductTitle"]',
    "price":          '.product-price, [class*="productPrice"], [class*="Price"]',
    "rating":         '[class*="rating"], [class*="Rating"]',
    "review_count":   '[class*="review-count"], [class*="ReviewCount"]',
    "tag_destaque":   '[class*="tag"], [class*="badge"], [class*="label"]',
}


class FastShopScraper(BaseScraper):
    """Scraper modular para a Fast Shop."""

    platform_name = "Fast Shop"

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        url = f"https://www.fastshop.com.br/web/p/busca?q={encoded}"
        if page > 1:
            url += f"&page={page}"
        return url

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
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
                seller="Fast Shop",
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
        """Busca keyword na Fast Shop por até `page_limit` páginas."""
        all_records: List[Dict[str, Any]] = []

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()
                self._random_delay(min_s=2.0, max_s=5.0)
                self._human_scroll(steps=8, step_px=300)

                offset  = (page - 1) * 20
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
