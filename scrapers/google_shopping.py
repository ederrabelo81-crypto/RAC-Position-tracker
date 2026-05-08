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

ATUALIZAÇÃO 08/mai/2026: Múltiplas estratégias de extração de título + mais seletores CSS.
ATUALIZAÇÃO 09/mai/2026: Restaurada leaf-div como estratégia primária (COMMON_MISTAKES #2);
  aria-label rebaixado para último recurso; removido check hardcoded "Ar Condicionado".
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
# Seletores — confirmados em 31/mar/2026 + fallbacks legacy + NOVO 08/mai/2026
# ---------------------------------------------------------------------------
_SELECTORS = {
    # Container de card de produto (confirmado: 75/página em 31/mar/2026)
    # Fallbacks para versões anteriores do layout + novos padrões CSS observados
    "item_candidates": [
        "div.Ez5pwe",                    # NOVO layout (mai/2026) ← PRIMÁRIO
        "div.rwVHAc",                    # layout anterior (31/mar/2026)
        "div.sh-dgr__gr-auto",           # layout anterior (estável)
        "div.sh-dlr__list-result",       # variação conhecida
        "[data-docid]",                  # layout muito anterior
        "div[data-item-id]",             # atributo data genérico
        "div[class*='shopping'][class*='result']",  # CSS class pattern matching
        "div.i0X6df",                    # classe observada
        "div.KZmu8e",                    # outra variação
        ".cu-container",                 # PLAs patrocinados
        ".pla-unit",                     # PLA alternativo
        "div.sh-np",                     # resultado estrutural mínimo
        "div[jsaction*='rcm']",          # padrão JavaScript action
    ],
    # Preço — confirmado: span.VbBaOe (31/mar/2026), fallbacks legacy
    "price_candidates": [
        ".lmQWe",               # NOVO layout (mai/2026) ← PRIMÁRIO
        ".VbBaOe",              # layout anterior (31/mar/2026)
        ".a8Pemb",
        ".OFFNJ",
        ".g9WsWb",
        ".kHxwFf span",
        ".P1usuSb",
        "[data-xpc='price']",
        "span[class*='price']",
        "span[class*='Price']",
    ],
    # Vendedor / loja — confirmado 01/mai/2026: div.UsGWMe (aria-label="De {seller}")
    "seller_candidates": [
        ".n7emVc",              # NOVO layout (mai/2026) ← PRIMÁRIO
        ".UsGWMe",              # layout anterior (01/mai/2026)
        ".Baoj6d",              # classe auxiliar observada junto a UsGWMe
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

# Delay mínimo/máximo entre keywords do Google — maior que o global para
# reduzir probabilidade de reCAPTCHA em sequências rápidas.
# Aumentado de 12–22s para 25–45s (01/mai/2026) após CAPTCHA na 13ª keyword.
_MIN_DELAY_GOOGLE = 25.0
_MAX_DELAY_GOOGLE = 45.0

# Textos que indicam badge/promo, nunca nome de loja
_SELLER_BLACKLIST_RE = re.compile(
    r"desconto|frete|cupom|acima\s+de|compras|entrega|gr[áa]tis|\boff\b|^\d+\s*%|parcel",
    re.IGNORECASE,
)


class GoogleShoppingScraper(BaseScraper):
    """Scraper modular para Google Shopping Brasil."""

    platform_name = "Google Shopping"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self.captcha_hit: bool = False
        self._card_logged: bool = False
        self._cards_sem_seller: int = 0

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
    # Extração de título — múltiplas estratégias (NOVO 08/mai/2026)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title(item: Tag) -> Optional[str]:
        """
        Extrai o título do produto de um card Google Shopping.

        Estratégias em cascata (ordem importa — mais confiável primeiro):
        1. Leaf-div: primeiro <div> folha (sem filhos, sem classe), 15-200 chars,
           sem R$/\\n/\\xa0 — estratégia documentada em COMMON_MISTAKES.md #2.
        2. h2/h3/h4 com texto longo (headings semânticos).
        3. Link <a href="/shopping"> — texto do link de produto.
        4. img[alt] — Google preenche alt apenas com nome do produto.
        5. Seletores CSS legacy de layouts anteriores.
        6. aria-label do container — ÚLTIMO RECURSO: Google concatena
           "nome + R$ preço + seller" no aria-label; _clean_title() remove artefatos
           mas pode falhar; só usar quando tudo acima falhar.
        """
        # Estratégia 1: leaf-div — sem filhos, sem classe (COMMON_MISTAKES.md #2)
        for div in item.find_all("div"):
            if div.find():          # tem filhos → não é folha, pula
                continue
            if div.get("class"):    # tem classe → componente UI, pula
                continue
            text = div.get_text(strip=True)
            if (15 <= len(text) <= 200
                    and "R$" not in text
                    and "\n" not in text
                    and "\xa0" not in text):
                return GoogleShoppingScraper._clean_title(text)

        # Estratégia 2: h2/h3/h4 com texto longo (headings semânticos)
        for tag_name in ["h2", "h3", "h4"]:
            el = item.select_one(tag_name)
            if el:
                text = el.get_text(strip=True)
                if text and 15 <= len(text) <= 200 and "R$" not in text:
                    return GoogleShoppingScraper._clean_title(text)

        # Estratégia 3: <a> com href para /shopping/product
        link = item.select_one("a[href*='/shopping']")
        if not link:
            link = item.select_one("a[href*='product']")
        if link:
            text = link.get_text(strip=True)
            if text and 15 <= len(text) <= 200 and "R$" not in text:
                return GoogleShoppingScraper._clean_title(text)

        # Estratégia 4: img[alt] — Google preenche alt apenas com nome do produto
        img = item.select_one("img[alt]")
        if img:
            alt = img.get("alt", "").strip()
            if alt and len(alt) > 3 and "R$" not in alt:
                return GoogleShoppingScraper._clean_title(alt)

        # Estratégia 5: seletores CSS de layouts anteriores
        legacy_selectors = [
            ".gkQHve", ".Lq5OHe", ".tAxDx", ".rgHvZc", ".muB3Ob",
            ".sh-np__click-target", "h3.sh-np__click-target",
        ]
        for sel in legacy_selectors:
            el = item.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text and 15 <= len(text) <= 200:
                    return GoogleShoppingScraper._clean_title(text)

        # Estratégia 6: aria-label — ÚLTIMO RECURSO (ver COMMON_MISTAKES.md #2)
        # Google concatena nome+preço+seller; _clean_title() tenta remover artefatos.
        al = item.get("aria-label", "").strip()
        if al and 15 <= len(al) <= 300:
            cleaned = GoogleShoppingScraper._clean_title(al)
            if cleaned:
                return cleaned

        return None

    @staticmethod
    def _clean_title(raw: str) -> Optional[str]:
        """Remove artefatos de preço/rating que aparecem concatenados ao nome."""
        if not raw:
            return None

        # Remove "R$ ..." patterns no final
        raw = re.sub(r"\s*R\$\s+[\d.,]+.*$", "", raw, flags=re.IGNORECASE)
        # Remove "(X avaliações)" ou "(X reviews)"
        raw = re.sub(r"\s*\(\s*\d+\s*(avaliações|reviews?)\s*\)", "", raw, flags=re.IGNORECASE)
        # Remove "★ 4.5" patterns
        raw = re.sub(r"★\s+[\d.]+\s*$", "", raw)
        # Remove espaços extras
        raw = " ".join(raw.split())

        if 3 <= len(raw) <= 300:
            return raw
        return None

    # ------------------------------------------------------------------
    # Extração de preço
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_price(item: Tag) -> Optional[float]:
        """Extrai preço do card."""
        for price_sel in _SELECTORS["price_candidates"]:
            price_el = item.select_one(price_sel)
            if price_el:
                price_text = price_el.get_text(strip=True)
                return parse_price(price_text)
        return None

    # ------------------------------------------------------------------
    # Extração de seller
    # ------------------------------------------------------------------

    _RE_NOT_SELLER = re.compile(
        r"^(de|por|a partir|em|até|novo|usado|anúncio)",
        re.IGNORECASE,
    )

    @staticmethod
    def _extract_seller(item: Tag) -> Optional[str]:
        """Extrai nome do vendedor/loja do card."""
        for seller_sel in _SELECTORS["seller_candidates"]:
            seller_el = item.select_one(seller_sel)
            if seller_el:
                seller = seller_el.get_text(strip=True)
                if (seller
                    and len(seller) > 2
                    and len(seller) < 100
                    and not GoogleShoppingScraper._RE_NOT_SELLER.search(seller)
                    and not _SELLER_BLACKLIST_RE.search(seller)):
                    logger.debug(f"[Google Shopping] seller [texto]: {seller}")
                    return seller

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

        # Detecta CAPTCHA — marca flag para abortar keywords restantes
        if soup.select_one(_SELECTORS["captcha"]):
            logger.warning(
                f"[{self.platform_name}] reCAPTCHA detectado — abortando sessão. "
                "Use proxy residencial para coletas em escala."
            )
            self.captcha_hit = True
            return []  # registros já coletados anteriormente são preservados pelo caller

        items, sel_used = self._detect_items(soup)
        logger.info(
            f"[{self.platform_name}] {len(items)} cards encontrados "
            f"(seletor: {sel_used})"
        )

        if not items:
            self._dump_debug(html, page, keyword)
            return []

        # Log único do HTML do primeiro card para diagnóstico de seletores
        if not self._card_logged and items:
            self._card_logged = True
            try:
                logger.debug(
                    f"[{self.platform_name}] Primeiro card HTML (seletor: {sel_used}):\n"
                    f"{items[0].decode_contents()[:1200]}"
                )
            except Exception as _e:
                logger.debug(f"[{self.platform_name}] Erro ao logar card HTML: {_e}")

        records = []
        empty_title_count = 0
        empty_seller_count = 0

        for idx, item in enumerate(items):
            pos_general = page_offset + idx + 1

            title     = self._extract_title(item)
            price_raw = self._extract_price(item)
            seller    = self._extract_seller(item)
            if not seller:
                empty_seller_count += 1

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
                price_float=price_raw,  # price_raw já é float aqui
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

        n_total = len(records)
        self._cards_sem_seller += empty_seller_count
        pct_sem = empty_seller_count * 100 // max(n_total, 1)
        log_fn = logger.warning if pct_sem > 30 else logger.info
        log_fn(
            f"[{self.platform_name}] '{keyword}' p{page} → {n_total} cards, "
            f"{empty_seller_count} sem seller ({pct_sem}%)"
            + (" — seletores podem estar desatualizados" if pct_sem > 30 else "")
        )

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
            if self.captcha_hit:
                break

            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_network_idle()
                # Delay generoso — Google detecta padrões rápidos com alta precisão
                self._random_delay(min_s=_MIN_DELAY_GOOGLE, max_s=_MAX_DELAY_GOOGLE)
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

                if not records or self.captcha_hit:
                    break

                if page < page_limit:
                    self._random_delay(min_s=_MIN_DELAY_GOOGLE, max_s=_MAX_DELAY_GOOGLE)

            except Exception as exc:
                logger.error(f"[{self.platform_name}] Erro na página {page}: {exc}")
                raise

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
