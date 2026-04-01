"""
scrapers/google_shopping.py — Scraper do Google Shopping (google.com/search?tbm=shop).

Estratégia:
  - URL: https://www.google.com/search?tbm=shop&q={keyword}&gl=br&hl=pt-BR
  - Proteção: reCAPTCHA v3 / bot fingerprinting agressivo do Google.
    Com stealth e delays adequados, coletas esporádicas funcionam.
    Para volume alto (todas as keywords diariamente), use proxy residencial.
  - Paginação: parâmetro `&start={offset}` (10 resultados por página no shopping)
  - Patrocinados: anúncios no Google Shopping têm classe diferente dos orgânicos.

Manutenção de seletores:
  O Google Shopping rotaciona seus nomes de classe constantemente.
  Esta implementação usa cadeia de fallback + img[alt] + regex R$ para máxima resiliência.
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
# Seletores — cadeia de fallback por ordem de confiabilidade
# ---------------------------------------------------------------------------
_SELECTORS = {
    # Containers de produto — orgânicos
    "item_organic_candidates": [
        "[data-docid]",                   # atributo estável do Google Shopping
        ".sh-dgr__gr-auto",
        ".sh-dlr__list-result",
        ".KZmu8e",
        ".i0X6df",
        ".EI11Pd",
        "div[jsaction*='rcm']",
        "[data-item-id]",
    ],
    # Containers de patrocinados (anúncios PLA)
    "item_sponsored_candidates": [
        ".cu-container",
        ".pla-unit",
        "[data-hveid]",
        ".mnr-c.pla-unit",
        ".commercial-unit-desktop-top",
    ],
    # Título do produto (múltiplos fallbacks)
    "title_candidates": [
        ".Lq5OHe",
        ".tAxDx",
        ".rgHvZc",
        ".EI11Pd",
        ".muB3Ob",
        ".sh-np__click-target",
        "h3.sh-np__click-target",
        "h3",
        "h2",
        "[aria-label]",
    ],
    # Preço
    "price_candidates": [
        ".a8Pemb",
        ".OFFNJ",
        ".g9WsWb",
        ".kHxwFf span",
        ".P1usuSb",
        "[data-xpc='price']",
        "span[class*='price']",
        "span[class*='Price']",
    ],
    # Vendedor / loja
    "seller_candidates": [
        ".E5ocAb",
        ".aULzUe",
        ".IuHnof",
        ".NkoJne",
        ".vf0Yd",
        ".XrAfOe",
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
    # Contagem de avaliações — Google Shopping nem sempre exibe no grid;
    # captura quando disponível via aria-label "N avaliações" ou texto numérico
    "review_count_candidates": [
        "[aria-label*='avaliações']",
        "[aria-label*='reviews']",
        ".Rsc7Yb + span",   # span após o rating em alguns layouts
        ".QIrs8",           # classe vista em alguns resultados
    ],
    # Detecção de CAPTCHA / bloqueio
    "captcha": "#captcha-form, #recaptcha, .g-recaptcha, #challenge-form",
}

_RESULTS_PER_PAGE = 10


def _first_text(tag: Tag, candidates: List[str]) -> Optional[str]:
    """Tenta cada seletor e retorna o primeiro texto encontrado."""
    for sel in candidates:
        el = tag.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text
    return None


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
    # Extração robusta de título (3 estratégias)
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_title(raw: str) -> Optional[str]:
        """
        Remove fragmentos que não fazem parte do título do produto:
        padrões de preço (R$), avaliações ("4,5 estrelas"), sellers concatenados.
        Retorna None se o resultado ficar muito curto após a limpeza.
        """
        # Remove padrão de preço: R$ 1.234,56 ou R$1234
        cleaned = re.sub(r"R\$\s*[\d.,]+", "", raw)
        # Remove "X estrelas" / "X avaliações"
        cleaned = re.sub(r"\d[\d.,]*\s*(estrelas?|avaliações?|stars?)", "", cleaned, flags=re.I)
        # Remove múltiplos espaços
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned if len(cleaned) >= 5 else None

    @staticmethod
    def _extract_title(item: Tag) -> Optional[str]:
        # 1. Seletores CSS conhecidos — mais confiáveis (retornam só o nome)
        title = _first_text(item, _SELECTORS["title_candidates"])
        if title:
            return GoogleShoppingScraper._clean_title(title)

        # 2. img[alt] — Google preenche o alt só com o nome do produto
        img = item.select_one("img[alt]")
        if img:
            alt = img.get("alt", "").strip()
            if alt and len(alt) > 3:
                return GoogleShoppingScraper._clean_title(alt)

        # 3. aria-label no container — frequentemente concatena nome+preço+seller.
        # Só usa se for curto o suficiente para ser apenas o nome (< 120 chars).
        al = item.get("aria-label", "").strip()
        if al and len(al) < 120:
            cleaned = GoogleShoppingScraper._clean_title(al)
            if cleaned:
                return cleaned

        # 4. Primeiro link com texto curto (< 150 chars, sem R$) — último recurso
        for a_tag in item.select("a[href]"):
            txt = a_tag.get_text(strip=True)
            if txt and 5 < len(txt) < 150 and "R$" not in txt:
                return GoogleShoppingScraper._clean_title(txt)

        return None

    @staticmethod
    def _extract_price(item: Tag) -> Optional[str]:
        # 1. Seletores CSS conhecidos
        for sel in _SELECTORS["price_candidates"]:
            el = item.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if t:
                    return t

        # 2. Regex scan: procura qualquer "R$" no texto do item
        item_text = item.get_text(" ", strip=True)
        match = re.search(r"R\$\s*[\d.,]+", item_text)
        if match:
            return match.group(0)

        return None

    @staticmethod
    def _extract_seller(item: Tag) -> Optional[str]:
        for sel in _SELECTORS["seller_candidates"]:
            el = item.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if t:
                    return t
        return None

    # ------------------------------------------------------------------
    # Detecção de containers de produto
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_items(soup: BeautifulSoup, candidates: List[str]) -> tuple[List[Tag], str]:
        for sel in candidates:
            items = soup.select(sel)
            if len(items) >= 2:
                return items, sel
        return [], "nenhum"

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
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo: {path}"
            )
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
                f"[{self.platform_name}] reCAPTCHA/bloqueio detectado. "
                "Use proxy residencial para coletas em escala."
            )
            return []

        # Coleta orgânicos + patrocinados
        organic_items, org_sel = self._detect_items(soup, _SELECTORS["item_organic_candidates"])
        sponsored_items, _     = self._detect_items(soup, _SELECTORS["item_sponsored_candidates"])

        logger.info(
            f"[{self.platform_name}] {len(organic_items)} orgânicos (seletor: {org_sel}) + "
            f"{len(sponsored_items)} patrocinados"
        )

        if not organic_items and not sponsored_items:
            self._dump_debug(html, page, keyword)
            return []

        # Remove patrocinados que também estejam no set de orgânicos (evita duplicatas)
        organic_set = set(id(i) for i in organic_items)
        sponsored_unique = [i for i in sponsored_items if id(i) not in organic_set]

        # Preserva ordem DOM
        all_items_with_flag = (
            [(item, False) for item in organic_items] +
            [(item, True)  for item in sponsored_unique]
        )

        records = []
        organic_counter   = 0
        sponsored_counter = 0
        empty_title_count = 0

        for pos_general, (item, is_sponsored) in enumerate(all_items_with_flag, start=page_offset + 1):
            if is_sponsored:
                sponsored_counter += 1
                pos_organic, pos_sponsored = None, sponsored_counter
            else:
                organic_counter += 1
                pos_organic, pos_sponsored = organic_counter, None

            title    = self._extract_title(item)
            price_raw = self._extract_price(item)
            seller   = self._extract_seller(item) or "Google Shopping"

            rating_el    = item.select_one(_SELECTORS["rating_candidates"][0])
            rating       = parse_rating(rating_el.get_text() if rating_el else None)
            for rating_sel in _SELECTORS["rating_candidates"]:
                rel = item.select_one(rating_sel)
                if rel:
                    r = parse_rating(rel.get("aria-label") or rel.get_text())
                    if r:
                        rating = r
                        break

            tag_el = None
            for tag_sel in _SELECTORS["tag_candidates"]:
                tag_el = item.select_one(tag_sel)
                if tag_el:
                    break
            tag = tag_el.get_text(strip=True) if tag_el else None

            # Contagem de avaliações — Google Shopping exibe em alguns layouts
            review_count = None
            for rev_sel in _SELECTORS["review_count_candidates"]:
                rev_el = item.select_one(rev_sel)
                if rev_el:
                    raw = rev_el.get("aria-label") or rev_el.get_text(strip=True)
                    review_count = parse_review_count(raw)
                    if review_count and review_count > 5:  # evita capturar rating (≤5)
                        break
                    review_count = None

            if not title:
                empty_title_count += 1

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

        if empty_title_count > len(records) // 2:
            logger.warning(
                f"[{self.platform_name}] {empty_title_count}/{len(records)} itens sem título. "
                "Seletores podem estar desatualizados — HTML salvo para diagnóstico."
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
                # Delay mais longo — Google detecta padrões rápidos
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
