"""
scrapers/amazon.py — Scraper da Amazon Brasil (amazon.com.br).

Estratégia de extração:
  - URL de busca: https://www.amazon.com.br/s?k={keyword_encoded}&page={n}
  - A Amazon usa proteções de bot robustas (CAPTCHA, fingerprinting JS).
    Este scraper aplica delays generosos e stealth. Para produção em escala,
    recomenda-se proxy residencial rotativo + serviço de resolução de CAPTCHA.
  - Distinção de patrocinado: `div[data-component-type="sp-sponsored-result"]`
  - Fulfillment: badge Prime ou "Vendido pela Amazon" / "Enviado pela Amazon"

Buy box / seller (foco Mai/2026): a SERP da Amazon NÃO mostra "Vendido por"
  (isso só existe no PDP). Quando o vendedor não é extraído do card, o buy box
  fica desconhecido (None) em vez de virar "Amazon/1P" fantasma — caso contrário
  100% dos registros sairiam como vitória 1P da Amazon, distorcendo o share of
  buy box. O campo de exibição `seller` mantém "Amazon" como fallback.

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

from config import MAX_PAGES, LOGS_DIR, PAGE_TIMEOUT
from scrapers.base import BaseScraper
from utils.amazon_sellers import (
    AmazonSellerCache,
    extract_seller_from_pdp,
    is_amazon_self,
    pdp_budget,
    resolution_enabled,
)
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
    # Link do produto — âncora do título leva ao PDP (/dp/ASIN)
    "url_candidates": [
        "h2 a.a-link-normal",
        "h2 a",
        "a.a-link-normal.s-no-outline",
        'a[href*="/dp/"]',
    ],
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

    # Após N CAPTCHAs consecutivos, rotaciona o browser pra resetar
    # fingerprint TLS. Mantém um teto pra evitar loop infinito quando o
    # IP inteiro do datacenter está em blacklist.
    _MAX_BROWSER_ROTATIONS = 2

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        # Flag pública lida em main._run_scraper pra abortar keywords restantes
        # quando IP foi marcado pelo Amazon (CAPTCHA infinito).
        self.captcha_hit: bool = False
        self._rotations_done: int = 0
        # Resolução de buy box via PDP — cache carregado sob demanda e
        # orçamento contado por execução do scraper, não por página.
        self._seller_cache: Optional[AmazonSellerCache] = None
        self._pdp_budget_left: int = pdp_budget()

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

    @staticmethod
    def _classify_seller(seller: Optional[str]) -> Optional[str]:
        """Amazon 1P (vendido pela Amazon) vs 3P (marketplace de terceiros).

        Retorna None quando o vendedor é desconhecido: a SERP não expõe o
        "Vendido por", então um card sem seller extraído NÃO pode ser contado
        como vitória 1P da Amazon (inflaria o share of buy box). Só o caller
        com um nome de fato observado deve chamar este método.
        """
        name = (seller or "").strip().lower()
        if not name:
            return None
        if "amazon" in name:
            return "1P"
        return "3P"

    @staticmethod
    def _extract_offers_count(item: Tag) -> Optional[int]:
        """
        Conta ofertas concorrentes na buy box a partir de "X ofertas a partir de"
        / "X novos" / "X de R$..." na linha do resultado.
        """
        for el in item.find_all(["span", "a"]):
            t = el.get_text(" ", strip=True).lower()
            m = re.search(r"(\d+)\s*(?:nova?s?\s*)?ofertas?", t)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
        return None

    @staticmethod
    def _extract_url(item: Tag) -> Optional[str]:
        """Extrai a URL do PDP. Hrefs da Amazon são relativos — prefixa o domínio."""
        for sel in _SELECTORS["url_candidates"]:
            el = item.select_one(sel)
            href = el.get("href") if el else None
            if href:
                href = href.split("?")[0].split("#")[0].strip()
                if href.startswith("/"):
                    href = f"https://www.amazon.com.br{href}"
                return href
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

        # Detecção de bloqueios — sinaliza pro caller via captcha_hit/_blocked_page
        # pra orquestrador decidir rotação/abortar.
        if soup.select_one(_SELECTORS["captcha"]):
            logger.warning(
                f"[{self.platform_name}] CAPTCHA detectado (página {page}). "
                "Configure proxy residencial para produção."
            )
            self._blocked_page = True
            return []
        if soup.select_one(_SELECTORS["bot_check"]):
            logger.warning(f"[{self.platform_name}] Bot-check detectado (página {page}).")
            self._blocked_page = True
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

            # "Vendido por" só aparece no PDP, não na SERP. Quando não há
            # vendedor extraído, mantemos "Amazon" apenas no campo de exibição
            # `seller`; buy box e tipo ficam desconhecidos (None) em vez de
            # virarem 100% "Amazon/1P" fantasma no share of buy box.
            seller_extracted = self._extract_seller(item)
            seller_display = seller_extracted or "Amazon"
            qtd_sellers = self._extract_offers_count(item)
            tipo_seller = self._classify_seller(seller_extracted)

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

            url_produto = self._extract_url(item)

            record = self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_float=price,
                seller=seller_display,
                buy_box_seller=seller_extracted,
                qtd_sellers=qtd_sellers,
                tipo_seller=tipo_seller,
                is_fulfillment=fulfillment,
                rating=rating,
                review_count=review_count,
                tag_destaque=tag,
                url_produto=url_produto,
            )
            if seller_extracted is None:
                # _build_record cai pra `seller` ("Amazon") quando buy_box=None;
                # aqui o vendedor é desconhecido (SERP sem "Vendido por") e não
                # pode virar vitória 1P fantasma no share of buy box.
                record["Buy Box Seller"] = None
                # ASIN guardado no registro para a etapa opcional de PDP; sai
                # do dict antes do CSV/Supabase (não é coluna do schema).
                record["_asin"] = self._extract_asin(item)
            records.append(record)

        self._resolve_buybox_via_pdp(records)
        for record in records:
            record.pop("_asin", None)

        return records

    # ------------------------------------------------------------------
    # Buy box via PDP (opcional — ver utils/amazon_sellers.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_asin(item: Tag) -> Optional[str]:
        """
        Extrai o ASIN do card da SERP.

        A Amazon expõe o ASIN em ``data-asin`` no container do resultado —
        atributo funcional (a própria página o usa), bem mais estável que as
        classes de layout.

        Args:
            item: container do card.

        Returns:
            ASIN de 10 caracteres, ou None.
        """
        asin = (item.get("data-asin") or "").strip()
        if re.fullmatch(r"[A-Z0-9]{10}", asin):
            return asin
        parent = item.find_parent(attrs={"data-asin": True})
        if parent:
            asin = (parent.get("data-asin") or "").strip()
            if re.fullmatch(r"[A-Z0-9]{10}", asin):
                return asin
        return None

    def _resolve_buybox_via_pdp(self, records: List[Dict[str, Any]]) -> None:
        """
        Preenche ``Buy Box Seller`` abrindo o PDP dos produtos sem vendedor.

        Desligada por padrão: só roda com ``RAC_AMAZON_PDP_BUYBOX=1``. A SERP
        da Amazon não expõe "Vendido por" (só o PDP), então este é o único
        caminho para o campo — mas cada ASIN novo custa uma requisição na
        plataforma mais agressiva em anti-bot do conjunto. Por isso o teto por
        execução (``RAC_AMAZON_PDP_BUDGET``, default 40): batendo o limite os
        demais registros seguem sem buy box, em vez de arriscar derrubar uma
        coleta que hoje é estável. Degradar é melhor que derrubar.

        Args:
            records: registros da página; alterados no lugar.

        Note:
            Falhas são silenciosas por registro (o campo fica None, como antes)
            e o ASIN entra em quarentena no cache para não consumir o orçamento
            de novo na próxima execução.
        """
        if not resolution_enabled():
            return

        pendentes = [
            rec for rec in records
            if not rec.get("Buy Box Seller") and rec.get("_asin")
        ]
        if not pendentes:
            return

        if self._seller_cache is None:
            self._seller_cache = AmazonSellerCache()
        cache = self._seller_cache

        resolvidos_cache = 0
        resolvidos_pdp = 0
        for rec in pendentes:
            asin = rec["_asin"]

            conhecido = cache.get(asin)
            if conhecido:
                rec["Buy Box Seller"] = conhecido
                rec["Tipo Seller"] = "1P" if is_amazon_self(conhecido) else "3P"
                resolvidos_cache += 1
                continue

            if self._pdp_budget_left <= 0 or not cache.should_retry(asin):
                continue

            self._pdp_budget_left -= 1
            nome = self._fetch_pdp_seller(asin)
            if nome:
                cache.put(asin, nome)
                rec["Buy Box Seller"] = nome
                rec["Tipo Seller"] = "1P" if is_amazon_self(nome) else "3P"
                resolvidos_pdp += 1
            else:
                cache.mark_failed(asin, "PDP sem 'Vendido por'")

        # Salva sempre: `mark_failed` também suja o cache, e é justamente no
        # caso em que NADA resolveu (Amazon bloqueando todos os PDPs) que a
        # quarentena precisa chegar ao disco — senão os mesmos ASINs mortos
        # reconsomem o orçamento na próxima execução, que é exatamente o que a
        # quarentena existe para evitar. `save()` já é no-op se nada mudou.
        cache.save()
        if resolvidos_cache or resolvidos_pdp:
            logger.info(
                f"[{self.platform_name}] Buy box resolvida: "
                f"{resolvidos_cache} do cache + {resolvidos_pdp} via PDP "
                f"({len(pendentes)} pendentes · orçamento restante "
                f"{self._pdp_budget_left})"
            )

    def _fetch_pdp_seller(self, asin: str) -> Optional[str]:
        """
        Abre o PDP do ASIN e devolve o vendedor da buy box.

        Args:
            asin: identificador do produto na Amazon.

        Returns:
            Nome do vendedor, ou None se o PDP não revelou (challenge de bot,
            produto fora do ar, ou layout desconhecido).
        """
        if self._page is None:
            return None
        try:
            self._page.goto(
                f"https://www.amazon.com.br/dp/{asin}",
                timeout=PAGE_TIMEOUT,
                wait_until="domcontentloaded",
            )
            self._random_delay()
            return extract_seller_from_pdp(self._page.content())
        except Exception as exc:
            logger.debug(
                f"[{self.platform_name}] PDP {asin} falhou: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

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
        """Busca keyword na Amazon Brasil por até `page_limit` páginas.

        Se detectar CAPTCHA, tenta rotacionar o browser (novo fingerprint TLS)
        até `_MAX_BROWSER_ROTATIONS` vezes. Após o limite, marca `captcha_hit`
        para o orquestrador abortar as keywords restantes da sessão.
        """
        all_records: List[Dict[str, Any]] = []

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                self._blocked_page = False
                self._page.goto(url, wait_until="domcontentloaded")
                self._wait_for_products(timeout_ms=12_000)
                self._wait_for_network_idle()
                self._random_delay(min_s=3.5, max_s=9.0)
                self._human_scroll(steps=10, step_px=320)

                # captura screenshot da página de busca
                self._last_screenshot_busca = None
                self._last_screenshot_busca = self.capture_screenshot(identifier=f"{keyword}_p{page}", tipo="busca")

                offset  = (page - 1) * 16
                records = self._parse_results(
                    html=self._page.content(),
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                    page=page,
                    page_offset=offset,
                )
                all_records.extend(records)

                # CAPTCHA detectado: tenta rotacionar browser e refazer a página
                if self._blocked_page and self._rotations_done < self._MAX_BROWSER_ROTATIONS:
                    self._rotations_done += 1
                    logger.warning(
                        f"[{self.platform_name}] CAPTCHA — rotacionando browser "
                        f"({self._rotations_done}/{self._MAX_BROWSER_ROTATIONS}) e "
                        f"refazendo página {page}"
                    )
                    self._rotate_browser()
                    self._random_delay(min_s=8.0, max_s=15.0)
                    self._blocked_page = False
                    self._page.goto(url, wait_until="domcontentloaded")
                    self._wait_for_products(timeout_ms=12_000)
                    self._wait_for_network_idle()
                    self._random_delay(min_s=4.0, max_s=8.0)
                    self._human_scroll(steps=10, step_px=320)
                    records = self._parse_results(
                        html=self._page.content(),
                        keyword=keyword,
                        keyword_category_map=keyword_category_map,
                        page=page,
                        page_offset=offset,
                    )
                    all_records.extend(records)

                if self._blocked_page and self._rotations_done >= self._MAX_BROWSER_ROTATIONS:
                    logger.error(
                        f"[{self.platform_name}] CAPTCHA persistente após "
                        f"{self._rotations_done} rotações — abortando keywords restantes "
                        "(provavelmente IP em blacklist)."
                    )
                    self.captcha_hit = True
                    break

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

        self._log_search_result(keyword, len(all_records))
        
        # ------------------------------------------------------------------
        # Sistema adaptativo: registra resultado para aprendizado contínuo
        # ------------------------------------------------------------------
        try:
            from utils.adaptive_scraper import AdaptiveScraperConfig
            
            # Amazon usa browser-first com fallback de rotação
            strategy = "browser_first"
            if self.captcha_hit:
                strategy = "local_chrome"  # Fallback após CAPTCHA
            
            duration_est = sum(r.get('duration', 0.5) for r in all_records) if all_records else len(all_records) * 0.5
            
            success = len(all_records) > 0
            error_type = None
            if not success:
                if self.captcha_hit:
                    error_type = "captcha_block"
                elif self._blocked_page:
                    error_type = "waf_block"
                else:
                    error_type = "no_results"
            
            AdaptiveScraperConfig.record_result(
                platform=self.platform_name,
                strategy=strategy,
                success=success,
                items_collected=len(all_records),
                duration_seconds=max(duration_est, 1.0),
                error_type=error_type,
                pages_attempted=page_limit,
                pages_successful=(page_limit if success else 0),
                wait_time_ms=5000,  # Amazon usa delays maiores
                notes=f"Keyword: {keyword[:50]}" if keyword else None
            )
            
            # Registra evento WAF se houve bloqueio
            if error_type in ("captcha_block", "waf_block"):
                AdaptiveScraperConfig.record_waf_block(
                    platform=self.platform_name,
                    ip_type="residential",
                    strategy_used=strategy,
                    recovery_method="browser_rotation" if self._rotations_done > 0 else None
                )
        except Exception as exc:
            logger.debug(f"[AdaptiveScraper] Erro ao registrar resultado: {exc}")
        
        return all_records
