"""
scrapers/casas_bahia.py — Scraper da Casas Bahia (casasbahia.com.br).

Estratégia:
  - URL de busca: https://www.casasbahia.com.br/busca?q={keyword}
  - Proteção: WAF Akamai / PerimeterX — aplica delays generosos e stealth.
    Se bloqueado, o scraper detecta a página de challenge e registra aviso.
  - Paginação: parâmetro `&page={n}`
  - Seletores baseados no diagnóstico do v5 (Mar/2026).

Notas de manutenção:
  Se o WAF bloquear consistentemente, considere:
  1. Proxy residencial rotativo (brightdata.com, oxylabs.io)
  2. Ferramenta de monitoramento passivo (Distill Web Monitor)
  3. API oficial do Grupo Casas Bahia (solicitar acesso)
"""

from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating, parse_review_count

# ---------------------------------------------------------------------------
# Seletores CSS — Casas Bahia usa Vtex IO (React SSR)
# ---------------------------------------------------------------------------
_SELECTORS = {
    "item_container": (
        "[data-testid='product-card'], "
        "[class*='ProductCard'], "
        "[class*='product-card'], "
        "article[class*='product']"
    ),
    "title": (
        "[data-testid='product-name'], "
        "[class*='productName'], "
        "[class*='ProductName'], "
        "h3[class*='product']"
    ),
    "price": (
        "[data-testid='price-best-price'], "
        ".vtex-product-price-1-x-sellingPrice, "
        "[class*='sellingPrice'], "
        "[class*='bestPrice']"
    ),
    "seller":         "[data-testid='seller-name'], [class*='sellerName']",
    "rating":         "[class*='ratingValue'], [class*='rating-value']",
    "review_count":   "[class*='reviewCount'], [class*='review-count']",
    "tag_destaque":   "[data-testid='discount-badge'], [class*='discountBadge'], [class*='badge']",
    "sponsored":      "[data-testid='sponsored'], [class*='sponsored']",
    # Página de challenge / bloqueio Akamai
    "waf_block":      "#ak-challenge-error, #challenge-container, .ak-challenge",
}

_ITEMS_PER_PAGE = 24


class CasasBahiaScraper(BaseScraper):
    """Scraper modular para Casas Bahia."""

    platform_name = "Casas Bahia"

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        url = f"https://www.casasbahia.com.br/busca?q={encoded}"
        if page > 1:
            url += f"&page={page}"
        return url

    @staticmethod
    def _is_sponsored(item: Tag) -> bool:
        return bool(item.select_one(_SELECTORS["sponsored"]))

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        # Detecta bloqueio WAF antes de tentar parsear produtos
        if soup.select_one(_SELECTORS["waf_block"]):
            logger.warning(
                f"[{self.platform_name}] WAF/Akamai detectado. "
                "Considere proxy residencial para produção."
            )
            return []

        items = soup.select(_SELECTORS["item_container"])
        logger.info(f"[{self.platform_name}] {len(items)} itens encontrados na página")

        records = []
        organic_counter   = 0
        sponsored_counter = 0

        for idx, item in enumerate(items):
            pos_general = page_offset + idx + 1
            sponsored   = self._is_sponsored(item)

            if sponsored:
                sponsored_counter += 1
                pos_organic, pos_sponsored = None, sponsored_counter
            else:
                organic_counter += 1
                pos_organic, pos_sponsored = organic_counter, None

            title_el  = item.select_one(_SELECTORS["title"])
            title     = title_el.get_text(strip=True) if title_el else None

            price_el  = item.select_one(_SELECTORS["price"])
            price_raw = price_el.get_text(strip=True) if price_el else None

            seller_el = item.select_one(_SELECTORS["seller"])
            seller    = seller_el.get_text(strip=True) if seller_el else "Casas Bahia"

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
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_raw=price_raw,
                seller=seller,
                is_fulfillment=False,
                rating=rating,
                review_count=review_count,
                tag_destaque=tag,
            ))

        return records

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=6, max=20),
        reraise=True,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Busca keyword na Casas Bahia por até `page_limit` páginas."""
        all_records: List[Dict[str, Any]] = []

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()
                # Delays maiores para WAF não detectar padrão de bot
                self._random_delay(min_s=4.0, max_s=9.0)
                self._human_scroll(steps=10, step_px=300)

                offset  = (page - 1) * _ITEMS_PER_PAGE
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
