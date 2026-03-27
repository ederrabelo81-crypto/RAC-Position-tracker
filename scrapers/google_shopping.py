"""
scrapers/google_shopping.py — Scraper do Google Shopping (google.com/search?tbm=shop).

Estratégia:
  - URL: https://www.google.com/search?tbm=shop&q={keyword}&gl=br&hl=pt-BR
  - Proteção: reCAPTCHA v3 / bot fingerprinting agressivo do Google.
    Com stealth e delays adequados, coletas esporádicas funcionam.
    Para volume alto (todas as keywords diariamente), use proxy residencial.
  - Paginação: parâmetro `&start={offset}` (10 resultados por página no shopping)
  - Patrocinados: anúncios no Google Shopping têm classe diferente dos orgânicos.

Notas de manutenção:
  O Google Shopping muda seus seletores com frequência.
  Se nenhum item for encontrado, rode o diagnóstico:
    python diagnostico.py --platform google_shopping --visible
"""

from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating

# ---------------------------------------------------------------------------
# Seletores — Google Shopping (confirmado Mar/2026)
# ---------------------------------------------------------------------------
_SELECTORS = {
    # Container de cada produto orgânico
    "item_organic":    ".sh-dgr__gr-auto, .sh-dlr__list-result, [data-docid]",
    # Container de anúncios patrocinados (ficam acima dos orgânicos)
    "item_sponsored":  ".cu-container, .pla-unit, [data-hveid]",

    "title":           ".Lq5OHe, .tAxDx, h3[class*='sh-']",
    "price":           ".a8Pemb, .OFFNJ, [data-xpc='price']",
    "seller":          ".aULzUe, .E5ocAb, .IuHnof",
    "rating":          ".Rsc7Yb, .yi40Hd",
    "review_count":    ".HiT7Id",
    "tag_destaque":    ".Ib8pOd",  # badge de oferta
    # Detecção de CAPTCHA / bloqueio
    "captcha":         "#captcha-form, #recaptcha, .g-recaptcha, #challenge-form",
}

_RESULTS_PER_PAGE = 10


class GoogleShoppingScraper(BaseScraper):
    """Scraper modular para Google Shopping Brasil."""

    platform_name = "Google Shopping"

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        offset  = (page - 1) * _RESULTS_PER_PAGE
        url = (
            f"https://www.google.com/search?tbm=shop"
            f"&q={encoded}&gl=br&hl=pt-BR"
        )
        if offset > 0:
            url += f"&start={offset}"
        return url

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        # Detecta CAPTCHA
        if soup.select_one(_SELECTORS["captcha"]):
            logger.warning(
                f"[{self.platform_name}] reCAPTCHA/bloqueio detectado. "
                "Use proxy residencial para coletas em escala."
            )
            return []

        # Coleta orgânicos + patrocinados separadamente
        organic_items   = soup.select(_SELECTORS["item_organic"])
        sponsored_items = soup.select(_SELECTORS["item_sponsored"])
        all_items       = [(item, False) for item in organic_items] + \
                          [(item, True)  for item in sponsored_items]

        # Ordena pela posição no DOM (mantém ordem visual da página)
        all_items.sort(key=lambda x: list(soup.descendants).index(x[0])
                       if x[0] in soup.descendants else 9999)

        logger.info(
            f"[{self.platform_name}] {len(organic_items)} orgânicos + "
            f"{len(sponsored_items)} patrocinados"
        )

        records = []
        organic_counter   = 0
        sponsored_counter = 0

        for pos_general, (item, is_sponsored) in enumerate(all_items, start=page_offset + 1):
            if is_sponsored:
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
            seller    = seller_el.get_text(strip=True) if seller_el else "Google Shopping"

            rating_el    = item.select_one(_SELECTORS["rating"])
            rating       = parse_rating(rating_el.get_text() if rating_el else None)

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
                review_count=None,
                tag_destaque=tag,
            ))

        return records

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=8, max=25),
        reraise=True,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Busca keyword no Google Shopping por até `page_limit` páginas."""
        all_records: List[Dict[str, Any]] = []

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()
                # Delay mais longo — Google detecta padrões rápidos
                self._random_delay(min_s=5.0, max_s=10.0)
                self._human_scroll(steps=8, step_px=350)

                offset  = (page - 1) * _RESULTS_PER_PAGE
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
