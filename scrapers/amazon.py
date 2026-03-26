"""
scrapers/amazon.py — Scraper da Amazon Brasil (amazon.com.br).

Estratégia de extração:
  - URL de busca: https://www.amazon.com.br/s?k={keyword_encoded}&page={n}
  - A Amazon usa proteções de bot robustas (CAPTCHA, fingerprinting JS).
    Este scraper aplica delays generosos e stealth. Para produção em escala,
    recomenda-se proxy residencial rotativo + serviço de resolução de CAPTCHA.
  - Distinção de patrocinado: `div[data-component-type="sp-sponsored-result"]`
  - Fulfillment: badge "Vendido pela Amazon" ou "Enviado pela Amazon"

Notas de manutenção:
  A Amazon muda frequentemente seus atributos data-*. Verifique periodicamente
  se o seletor principal `data-component-type="s-search-result"` ainda é válido.
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
    # container de cada resultado
    "item_container": 'div[data-component-type="s-search-result"]',
    # container de patrocinados
    "sponsored_container": 'div[data-component-type="sp-sponsored-result"]',

    "title":          "h2.a-size-mini span",
    "price_whole":    ".a-price-whole",     # parte inteira (ex: "2.799")
    "price_fraction": ".a-price-fraction",  # centavos (ex: "90")
    "seller":         ".a-size-small.a-color-base",
    "rating":         ".a-icon-alt",
    "review_count":   ".a-size-small[aria-label]",
    "tag_destaque":   ".a-badge-text",
    "fulfillment":    ".a-icon-prime",  # ícone Prime = fulfillment Amazon
}


class AmazonScraper(BaseScraper):
    """Scraper modular para a Amazon Brasil."""

    platform_name = "Amazon"

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        return f"https://www.amazon.com.br/s?k={encoded}&page={page}"

    @staticmethod
    def _is_sponsored(item: Tag) -> bool:
        # Método 1: atributo data-component-type de patrocinado
        if item.get("data-component-type") == "sp-sponsored-result":
            return True
        # Método 2: badge "Patrocinado" dentro do item
        for el in item.find_all("span", string=lambda t: t and "patrocinado" in t.lower()):
            return True
        return False

    @staticmethod
    def _extract_price(item: Tag) -> Optional[float]:
        """Combina parte inteira + fração de centavos da Amazon."""
        whole = item.select_one(_SELECTORS["price_whole"])
        frac  = item.select_one(_SELECTORS["price_fraction"])
        if not whole:
            return None
        # Remove pontos de milhar e vírgulas
        int_str = "".join(c for c in whole.get_text() if c.isdigit())
        dec_str = "".join(c for c in frac.get_text() if c.isdigit()) if frac else "00"
        dec_str = dec_str.ljust(2, "0")[:2]
        try:
            return float(f"{int_str}.{dec_str}")
        except ValueError:
            return None

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

            title_el  = item.select_one(_SELECTORS["title"])
            title     = title_el.get_text(strip=True) if title_el else None

            price     = self._extract_price(item)

            seller_el = item.select_one(_SELECTORS["seller"])
            seller    = seller_el.get_text(strip=True) if seller_el else "Amazon"

            # Prime (ícone) indica Fulfillment pela Amazon
            fulfillment = bool(item.select_one(_SELECTORS["fulfillment"]))

            rating_el    = item.select_one(_SELECTORS["rating"])
            rating_text  = rating_el.get_text() if rating_el else None
            rating       = parse_rating(rating_text)

            reviews_el   = item.select_one(_SELECTORS["review_count"])
            review_count = parse_review_count(
                reviews_el.get("aria-label", "") if reviews_el else None
            )

            tag_el = item.select_one(_SELECTORS["tag_destaque"])
            tag    = tag_el.get_text(strip=True) if tag_el else None

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_float=price,
                seller=seller,
                is_fulfillment=fulfillment,
                rating=rating,
                review_count=review_count,
                tag_destaque=tag,
            ))

        return records

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=6, max=25),
        reraise=True,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Busca keyword na Amazon Brasil por até `page_limit` páginas."""
        all_records: List[Dict[str, Any]] = []

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()
                self._random_delay(min_s=3.5, max_s=9.0)
                self._human_scroll(steps=10, step_px=320)

                soup = self._get_soup()

                # Detecta CAPTCHA da Amazon
                if soup.select_one("form[action='/errors/validateCaptcha']"):
                    logger.warning(
                        f"[{self.platform_name}] CAPTCHA detectado. "
                        "Interrompendo — configure proxy residencial para produção."
                    )
                    break

                # Sem resultados
                if soup.select_one(".s-no-outline"):
                    logger.warning(f"[{self.platform_name}] Sem resultados na página {page}.")
                    break

                offset  = (page - 1) * 16  # Amazon exibe ~16 produtos por página
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
