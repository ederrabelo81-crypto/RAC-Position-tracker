"""
scrapers/magalu.py — Scraper do Magazine Luiza (magazineluiza.com.br).

Estratégia de extração:
  - URL de busca: https://www.magazineluiza.com.br/busca/{keyword_encoded}/
  - Paginação: parâmetro `?page={n}` na URL
  - Anti-bot: Magalu usa Cloudflare + PerimeterX; o script aplica delays
    generosos, scroll humano e cabeçalhos stealth.
  - Estrutura HTML: os resultados estão em <li> com data-testid="product-card"
    (pode mudar; atualize _SELECTORS se necessário)

Notas de manutenção:
  Se a Magalu alterar o DOM, inspecione a requisição XHR que popula os
  resultados em: https://www.magazineluiza.com.br/busca/?q=...
  A API interna retorna JSON e pode ser mais estável que o scraping de HTML.
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
# Seletores CSS centralizados
# ---------------------------------------------------------------------------
_SELECTORS = {
    "item_container": 'li[data-testid="product-card"]',
    "title":          '[data-testid="product-title"]',
    "price":          '[data-testid="price-value"]',
    "seller":         '[data-testid="seller-name"]',
    "rating":         '[data-testid="review-score"]',
    "review_count":   '[data-testid="review-count"]',
    "tag_destaque":   '[data-testid="product-tag"]',
    # Magalu não tem fulfillment próprio da mesma forma que o ML
    "sponsored":      '[data-testid="sponsored-tag"]',
}


class MagaluScraper(BaseScraper):
    """Scraper modular para o Magazine Luiza."""

    platform_name = "Magalu"

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        """
        Constrói URL de busca paginada da Magalu.

        Ex: https://www.magazineluiza.com.br/busca/ar+condicionado+split/?page=2
        """
        encoded = quote_plus(keyword)
        base = f"https://www.magazineluiza.com.br/busca/{encoded}/"
        if page > 1:
            return f"{base}?page={page}"
        return base

    @staticmethod
    def _is_sponsored(item: Tag) -> bool:
        sponsored_el = item.select_one(_SELECTORS["sponsored"])
        if sponsored_el:
            return True
        # fallback: atributo data-position="ad" ou similar
        return item.get("data-position", "").lower() in ("ad", "ads", "sponsored")

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
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

            title_el = item.select_one(_SELECTORS["title"])
            title    = title_el.get_text(strip=True) if title_el else None

            price_el  = item.select_one(_SELECTORS["price"])
            price_raw = price_el.get_text(strip=True) if price_el else None

            seller_el = item.select_one(_SELECTORS["seller"])
            seller    = seller_el.get_text(strip=True) if seller_el else "Magalu"

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
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=5, max=20),
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

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()

                # Magalu tem proteção mais agressiva; aguarda mais e faz scroll lento
                self._random_delay(min_s=3.0, max_s=8.0)
                self._human_scroll(steps=12, step_px=250)

                soup = self._get_soup()

                # Detecta bloqueio por Cloudflare / CAPTCHA
                if "challenge" in self._page.url or soup.select_one("#challenge-form"):
                    logger.warning(
                        f"[{self.platform_name}] Possível CAPTCHA/Cloudflare detectado. "
                        "Considere usar proxy residencial rotativo."
                    )
                    break

                # Detecta página sem resultados
                if soup.select_one('[data-testid="no-results"]'):
                    logger.warning(f"[{self.platform_name}] Sem resultados na página {page}.")
                    break

                # Página 1 não tem offset; páginas seguintes têm ~40 itens cada
                offset = (page - 1) * 40
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
