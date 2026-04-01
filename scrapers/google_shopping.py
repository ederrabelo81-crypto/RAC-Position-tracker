"""
scrapers/google_shopping.py — Scraper do Google Shopping (google.com/search?tbm=shop).

Estratégia:
  - URL: https://www.google.com/search?tbm=shop&q={keyword}&gl=br&hl=pt-BR
  - Proteção: reCAPTCHA v3 / bot fingerprinting agressivo do Google.
    Com stealth e delays adequados, coletas esporádicas funcionam.
    Para volume alto (todas as keywords diariamente), use proxy residencial.
  - Paginação: parâmetro `&start={offset}` (10 resultados por página no shopping)

Manutenção de seletores — estrutura confirmada via debug HTML de 31/mar/2026:
  Container: div.rwVHAc (75 por página)
  Título:    primeiro <div> folha (sem filhos, sem classe) com 15-200 chars, sem R$
  Preço:     span.VbBaOe — texto "R$\xa02.184,05" (non-breaking space, não espaço normal)
  O Google Shopping rotaciona nomes de classe constantemente; guardamos fallbacks.
  Quando 0 itens: HTML salvo em logs/google_debug_p{n}_{kw}.html
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

# ---------------------------------------------------------------------------
# Seletores — confirmados em 31/mar/2026 + fallbacks legacy
# ---------------------------------------------------------------------------
_SELECTORS = {
    # Container de card de produto (confirmado: 75/página em 31/mar/2026)
    # Fallbacks para versões anteriores do layout
    "item_candidates": [
        "div.rwVHAc",           # layout atual (31/mar/2026) ← PRIMÁRIO
        "[data-docid]",         # layout anterior (estável por anos)
        ".sh-dgr__gr-auto",
        ".sh-dlr__list-result",
        ".KZmu8e",
        ".i0X6df",
        "div[jsaction*='rcm']",
        ".cu-container",        # PLAs patrocinados
        ".pla-unit",
    ],
    # Preço — confirmado: span.VbBaOe (31/mar/2026), fallbacks legacy
    "price_candidates": [
        ".VbBaOe",              # layout atual (31/mar/2026) ← PRIMÁRIO
        ".a8Pemb",
        ".OFFNJ",
        ".g9WsWb",
        ".kHxwFf span",
        ".P1usuSb",
        "[data-xpc='price']",
        "span[class*='price']",
        "span[class*='Price']",
    ],
    # Vendedor / loja — fallbacks, o layout atual não usa classe estável
    "seller_candidates": [
        ".E5ocAb",
        ".aULzUe",
        ".IuHnof",
        ".NkoJne",
        ".vf0Yd",
        ".XrAfOe",
        ".LbUacb",
    ],
    # Rating
    "rating_candidates": [
        ".Rsc7Yb",
        ".yi40Hd",
        "[aria-label*='estrela']",
        "[aria-label*='star']",
        "[class*='rating']",
    ],
    # Badge de oferta
    "tag_candidates": [
        ".Ib8pOd",
        "[class*='badge']",
        "[class*='offer']",
        "[class*='tag']",
    ],
    # Contagem de avaliações (best-effort — nem sempre disponível no grid)
    "review_count_candidates": [
        "[aria-label*='avaliações']",
        "[aria-label*='reviews']",
        ".Rsc7Yb + span",
        ".QIrs8",
    ],
    # Detecção de CAPTCHA / bloqueio
    "captcha": "#captcha-form, #recaptcha, .g-recaptcha, #challenge-form",
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

    # ------------------------------------------------------------------
    # Detecção de containers de produto
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_items(soup: BeautifulSoup) -> tuple[List[Tag], str]:
        """Retorna (items, selector_usado) usando a cadeia de fallback."""
        for sel in _SELECTORS["item_candidates"]:
            items = soup.select(sel)
            if len(items) >= 2:
                return items, sel
        return [], "nenhum"

    # ------------------------------------------------------------------
    # Extração de título — estratégia leaf-div (confirmada 31/mar/2026)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title(item: Tag) -> Optional[str]:
        """
        Extrai o título do produto de um card .rwVHAc.

        No layout atual, o título está em um <div> FOLHA (sem filhos, sem classe)
        dentro do container. É o único div com texto longo (15-200 chars) que
        não contém "R$" nem quebras de linha.

        Fallbacks para layouts anteriores: seletores CSS legacy e img[alt].
        """
        # Estratégia 1 (layout atual): primeiro div folha com texto de produto
        for div in item.find_all("div"):
            if div.find():          # tem filhos → não é folha, pula
                continue
            if div.get("class"):    # tem classe → provável componente UI, pula
                continue
            text = div.get_text(strip=True)
            if (15 <= len(text) <= 200
                    and "R$" not in text
                    and "\n" not in text
                    and "\xa0" not in text):
                return GoogleShoppingScraper._clean_title(text)

        # Estratégia 2 (layouts legacy): seletores CSS conhecidos
        legacy_selectors = [
            ".Lq5OHe", ".tAxDx", ".rgHvZc", ".muB3Ob",
            ".sh-np__click-target", "h3.sh-np__click-target",
        ]
        for sel in legacy_selectors:
            el = item.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return GoogleShoppingScraper._clean_title(text)

        # Estratégia 3: img[alt] — Google preenche o alt apenas com o nome
        img = item.select_one("img[alt]")
        if img:
            alt = img.get("alt", "").strip()
            if alt and len(alt) > 3:
                return GoogleShoppingScraper._clean_title(alt)

        # Estratégia 4: aria-label curto (< 120 chars) no container
        al = item.get("aria-label", "").strip()
        if al and len(al) < 120 and "R$" not in al:
            return GoogleShoppingScraper._clean_title(al)

        return None

    @staticmethod
    def _clean_title(raw: str) -> Optional[str]:
        """Remove artefatos de preço/rating que aparecem concatenados ao nome."""
        cleaned = re.sub(r"R\$[\s\xa0]*[\d.,]+", "", raw)
        cleaned = re.sub(r"\d[\d.,]*\s*(estrelas?|avaliações?|stars?)", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned if len(cleaned) >= 5 else None

    # ------------------------------------------------------------------
    # Extração de preço
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_price(item: Tag) -> Optional[str]:
        """
        Extrai o preço do card.

        No layout atual: span.VbBaOe com texto "R$\xa02.184,05".
        parse_price() trata \xa0 (non-breaking space) como separador.
        """
        for sel in _SELECTORS["price_candidates"]:
            el = item.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if t and "R$" in t or re.search(r"[\d.,]+", t):
                    return t

        # Fallback regex no texto completo do card
        item_text = item.get_text(" ", strip=True).replace("\xa0", " ")
        match = re.search(r"R\$\s*[\d.,]+", item_text)
        if match:
            return match.group(0)

        return None

    # ------------------------------------------------------------------
    # Extração de seller
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_seller(item: Tag) -> Optional[str]:
        for sel in _SELECTORS["seller_candidates"]:
            el = item.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if t and len(t) < 60:
                    return t
        return None

    # ------------------------------------------------------------------
    # Debug dump
    # ------------------------------------------------------------------

    def _dump_debug(self, html: str, page: int, keyword: str) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"google_debug_p{page}_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(f"[{self.platform_name}] HTML salvo para diagnóstico: {path}")
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Parse principal
    # ------------------------------------------------------------------

    def _parse_results(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page: int = 1,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        # Detecta CAPTCHA
        if soup.select_one(_SELECTORS["captcha"]):
            logger.warning(
                f"[{self.platform_name}] reCAPTCHA detectado. "
                "Use proxy residencial para coletas em escala."
            )
            return []

        items, sel_used = self._detect_items(soup)
        logger.info(
            f"[{self.platform_name}] {len(items)} cards encontrados "
            f"(seletor: {sel_used})"
        )

        if not items:
            self._dump_debug(html, page, keyword)
            return []

        records = []
        empty_title_count = 0

        for idx, item in enumerate(items):
            pos_general = page_offset + idx + 1

            title     = self._extract_title(item)
            price_raw = self._extract_price(item)
            seller    = self._extract_seller(item) or "Google Shopping"

            # Rating
            rating = None
            for rating_sel in _SELECTORS["rating_candidates"]:
                rel = item.select_one(rating_sel)
                if rel:
                    r = parse_rating(rel.get("aria-label") or rel.get_text())
                    if r:
                        rating = r
                        break

            # Tag de destaque
            tag = None
            for tag_sel in _SELECTORS["tag_candidates"]:
                tag_el = item.select_one(tag_sel)
                if tag_el:
                    tag = tag_el.get_text(strip=True)
                    break

            # Contagem de avaliações (best-effort)
            review_count = None
            for rev_sel in _SELECTORS["review_count_candidates"]:
                rev_el = item.select_one(rev_sel)
                if rev_el:
                    raw_rv = rev_el.get("aria-label") or rev_el.get_text(strip=True)
                    rc = parse_review_count(raw_rv)
                    if rc and rc > 5:        # descarta valores que seriam ratings (≤5)
                        review_count = rc
                        break

            if not title:
                empty_title_count += 1

            # Google Shopping: todos os resultados são PLAs (anúncios pagos).
            # Registramos como orgânicos em ordem de posição (sem posição patrocinada)
            # para manter consistência com os outros scrapers.
            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_general,
                position_sponsored=None,
                price_raw=price_raw,
                seller=seller,
                is_fulfillment=False,
                rating=rating,
                review_count=review_count,
                tag_destaque=tag,
            ))

        if empty_title_count > 0:
            logger.info(
                f"[{self.platform_name}] {len(records) - empty_title_count}/"
                f"{len(records)} títulos extraídos "
                f"(seletor: {sel_used})"
            )
        if empty_title_count > len(records) // 2:
            logger.warning(
                f"[{self.platform_name}] {empty_title_count}/{len(records)} sem título — "
                "seletores possivelmente desatualizados. HTML salvo."
            )
            self._dump_debug(html, page, keyword)

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
                # Delay generoso — Google detecta padrões rápidos com alta precisão
                self._random_delay(min_s=5.0, max_s=10.0)
                self._human_scroll(steps=8, step_px=350)

                offset  = (page - 1) * _RESULTS_PER_PAGE
                records = self._parse_results(
                    html=self._page.content(),
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                    page=page,
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
