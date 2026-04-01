"""
scrapers/amazon.py — Scraper da Amazon Brasil (amazon.com.br).

Estratégia de extração:
  - URL de busca: https://www.amazon.com.br/s?k={keyword_encoded}&page={n}
  - A Amazon usa proteções de bot robustas (CAPTCHA, fingerprinting JS).
    Este scraper aplica delays generosos e stealth. Para produção em escala,
    recomenda-se proxy residencial rotativo + serviço de resolução de CAPTCHA.
  - Distinção de patrocinado: `div[data-component-type="sp-sponsored-result"]`
  - Fulfillment: badge Prime ou "Vendido pela Amazon" / "Enviado pela Amazon"

Notas de manutenção:
  A Amazon muda frequentemente seus atributos data-*. Ao receber 0 resultados,
  verifique o arquivo logs/amazon_debug_p{n}_{kw}.html para inspecionar o DOM.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, LOGS_DIR
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating, parse_review_count

_SELECTORS = {
    # Container de cada resultado orgânico
    "item_container": 'div[data-component-type="s-search-result"]',
    # Containers alternativos (fallback)
    "item_candidates": [
        'div[data-component-type="s-search-result"]',
        'div[data-asin]:not([data-asin=""])',
        '[data-cy="title-recipe"]',
        'div.s-result-item[data-asin]',
    ],
    # Título — cadeia de fallback
    "title_candidates": [
        "h2.a-size-mini span",
        "h2 a span.a-text-normal",
        "h2 span.a-text-normal",
        "h2 a span",
        "h2 span",
    ],
    "price_whole":    ".a-price-whole",
    "price_fraction": ".a-price-fraction",
    # Seller — NOTE: .a-size-small.a-color-base é demasiado genérico (captura ratings).
    # Usamos _extract_seller() baseada em texto "Vendido por" / link de seller.
    "seller_link": 'a[href*="seller="], a[href*="/shops/"], a[href*="m=A"]',
    # ".a-icon-alt" = span oculto "4,5 de 5 estrelas" — parse_rating extrai o float
    "rating":         ".a-icon-alt",
    # Contagem de avaliações: elemento com aria-label "1.234 avaliações" (plural).
    # NÃO usar [aria-label*='estrela'] aqui — captura o ícone de rating em vez da contagem.
    "review_count":   (
        "a[aria-label*='avaliações'], "
        "span[aria-label*='avaliações'], "
        "[data-csa-c-slot-id='alf-reviews'] .a-size-base"
    ),
    "tag_destaque":   ".a-badge-text",
    "fulfillment":    ".a-icon-prime, [aria-label='Amazon Prime'], [class*='prime']",

    # Detecção de bloqueios
    "captcha":        "form[action='/errors/validateCaptcha'], #captcha, #captcha-form",
    "bot_check":      "#px-captcha, [id*='px-'], #distil_r_captcha",
    # "Sem resultados" real — NÃO usar .s-no-outline (é classe do container de resultados!)
    "no_results":     (
        ".a-section.a-spacing-small.a-text-center h3, "
        "[class*='no-results'], "
        ".s-no-outline.s-latency-cf-section"  # apenas quando COMBINADO com latency-cf
    ),
}


class AmazonScraper(BaseScraper):
    """Scraper modular para a Amazon Brasil."""

    platform_name = "Amazon"

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        return f"https://www.amazon.com.br/s?k={encoded}&page={page}"

    @staticmethod
    def _detect_items(soup: BeautifulSoup) -> tuple[List[Tag], str]:
        """Testa seletores em ordem e retorna o primeiro com ≥1 item."""
        for sel in _SELECTORS["item_candidates"]:
            items = soup.select(sel)
            # Filtra containers sem data-asin (banners, ads sem produto real)
            items = [i for i in items if i.get("data-asin", "").strip()]
            if items:
                return items, sel
        return [], "nenhum"

    @staticmethod
    def _is_sponsored(item: Tag) -> bool:
        if item.get("data-component-type") == "sp-sponsored-result":
            return True
        for el in item.find_all(
            "span",
            string=lambda t: t and ("patrocinado" in t.lower() or "sponsored" in t.lower()),
        ):
            return True
        return False

    @staticmethod
    def _extract_price(item: Tag) -> Optional[float]:
        """Combina parte inteira + fração da Amazon."""
        whole = item.select_one(_SELECTORS["price_whole"])
        frac  = item.select_one(_SELECTORS["price_fraction"])
        if not whole:
            return None
        int_str = "".join(c for c in whole.get_text() if c.isdigit())
        dec_str = "".join(c for c in frac.get_text() if c.isdigit()) if frac else "00"
        dec_str = dec_str.ljust(2, "0")[:2]
        try:
            return float(f"{int_str}.{dec_str}")
        except ValueError:
            return None

    @staticmethod
    def _first_match(item: Tag, candidates: List[str]) -> Optional[Tag]:
        for sel in candidates:
            el = item.select_one(sel)
            if el:
                return el
        return None

    @staticmethod
    def _extract_seller(item: Tag) -> Optional[str]:
        """
        Extrai o nome do vendedor de forma robusta.

        Estratégias em ordem:
          1. Link com href de seller (atributo seller= ou /shops/)
          2. Texto "Vendido por X" em qualquer span/a da linha
          3. Texto "por X" em span pequeno (excluindo "por R$", "por estrelas")

        NÃO usa .a-size-small.a-color-base — essa classe é genérica demais e
        frequentemente captura o texto de avaliação ("4,5 de 5 estrelas").
        """
        # 1. Link direto do seller na Amazon
        for a in item.select(_SELECTORS["seller_link"]):
            t = a.get_text(strip=True)
            if t and 2 < len(t) < 80:
                return t

        # 2. Span/a com "Vendido por" — padrão de seller de terceiros
        for el in item.find_all(["span", "a"]):
            t = el.get_text(strip=True)
            if "Vendido por" in t:
                seller = t.split("Vendido por")[-1].strip()
                if seller and len(seller) < 80:
                    return seller

        # 3. Texto "por X" curto sem dígitos na sequência (≠ "por R$ 1.999")
        for el in item.find_all("span"):
            t = el.get_text(strip=True)
            if t.startswith("por ") and len(t) < 60 and not re.match(r"por\s*R?\$?\s*\d", t):
                return t[4:].strip()

        return None

    def _dump_debug_html(self, html: str, page: int, keyword: str) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"amazon_debug_p{page}_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo: {path}\n"
                "  → Abra no browser e inspecione data-component-type nos containers."
            )
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        # Detecção de bloqueios
        if soup.select_one(_SELECTORS["captcha"]):
            logger.warning(
                f"[{self.platform_name}] CAPTCHA detectado (página {page}). "
                "Configure proxy residencial para produção."
            )
            return []
        if soup.select_one(_SELECTORS["bot_check"]):
            logger.warning(f"[{self.platform_name}] Bot-check detectado (página {page}).")
            return []

        items, sel_used = self._detect_items(soup)
        logger.info(
            f"[{self.platform_name}] {len(items)} itens encontrados "
            f"(seletor: {sel_used})"
        )

        if not items:
            self._dump_debug_html(html, page, keyword)
            return []

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

            title_el = self._first_match(item, _SELECTORS["title_candidates"])
            title    = title_el.get_text(strip=True) if title_el else None

            price = self._extract_price(item)

            seller = self._extract_seller(item) or "Amazon"

            fulfillment = bool(item.select_one(_SELECTORS["fulfillment"]))

            rating_el   = item.select_one(_SELECTORS["rating"])
            rating      = parse_rating(rating_el.get_text() if rating_el else None)

            reviews_el = item.select_one(_SELECTORS["review_count"])
            if reviews_el:
                # Prioriza aria-label ("1.234 avaliações") sobre texto visível
                review_raw = reviews_el.get("aria-label") or reviews_el.get_text(strip=True)
                review_count = parse_review_count(review_raw)
            else:
                review_count = None

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

    def _wait_for_products(self, timeout_ms: int = 12_000) -> bool:
        """Aguarda container de resultado aparecer."""
        for sel in _SELECTORS["item_candidates"]:
            try:
                self._page.wait_for_selector(sel, timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

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
                self._wait_for_products(timeout_ms=12_000)
                self._wait_for_network_idle()
                self._random_delay(min_s=3.5, max_s=9.0)
                self._human_scroll(steps=10, step_px=320)

                offset  = (page - 1) * 16
                records = self._parse_results(
                    html=self._page.content(),
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                    page=page,
                    page_offset=offset,
                )
                all_records.extend(records)

                if not records:
                    logger.warning(
                        f"[{self.platform_name}] Página {page} retornou 0 itens — "
                        "possível bloqueio ou fim de resultados."
                    )
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
