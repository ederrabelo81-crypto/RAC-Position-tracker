"""
scrapers/magalu.py — Scraper do Magazine Luiza (magazineluiza.com.br).

Estratégia de extração (em ordem de prioridade):
  1. Intercepção XHR da API interna (/api/product-search/v3/queries/search)
  2. Parse DOM com cadeia de seletores fallback (vários layouts)
  3. Dump de debug HTML quando 0 itens (para análise manual de seletores)

Proteções detectadas:
  - Akamai Bot Manager (novo em 2025 — requer proxy residencial)
  - PerimeterX (px-captcha, _pxAppId)
  - Cloudflare (#challenge-form)
  - Página silenciosa (carregou mas sem conteúdo útil)

Paginação: parâmetro ?page={n} ou &page={n} na URL de busca.
"""

import json
import re
import time
import random
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential, wait_random

from config import MAX_PAGES, LOGS_DIR, USER_AGENTS
from scrapers.base import BaseScraper
from utils.text import parse_price, parse_rating, parse_review_count, now_brt


class MagaluSoftBlockException(Exception):
    """Levantada quando o Magalu retorna página vazia sem mensagem de 'sem resultados' (soft-block silencioso)."""
    pass


class MagaluAkamaiBlockException(Exception):
    """
    Levantada quando o Akamai Bot Manager bloqueia o scraper.

    Diferente de MagaluSoftBlockException — Akamai é bloqueio hard que não
    responde a retry com backoff. Requer proxy residencial brasileiro.
    """
    pass


# ---------------------------------------------------------------------------
# Seletores CSS — cadeia de fallback para múltiplas versões do layout
# ---------------------------------------------------------------------------
_SELECTORS = {
    # Container de produto — tenta em ordem até encontrar resultados.
    # Magalu migrou para design system "nm-" (New Magalu) em 2024/2025.
    # Os seletores nm-* têm prioridade; data-testid e sc-* como fallback legado.
    "item_candidates": [
        'li[class*="nm-product-card"]',               # nm design system (atual)
        '[data-testid="product-card-container"]',     # legado v3
        'li[data-testid="product-card"]',             # legado v1
        '[data-testid="item"]',                       # legado v2
        'li[class*="ProductCard"]',                   # styled-components v1
        'li[class*="product-card"]',                  # styled-components v2
        '[class*="ProductCard__Wrapper"]',
        'a[data-testid="product-card-link"]',         # link direto
        'li[class*="nm-"]',                           # nm genérico
        '[data-cy="product-card"]',
        'li > a[href*="/p/"]',                        # estrutura de link (último recurso)
    ],
    # Título do produto
    "title_candidates": [
        '[data-testid="product-title"]',              # legado
        '[data-testid="product-name"]',               # legado
        'h2[class*="nm-product-card"]',               # nm atual
        '[class*="nm-product-card__name"]',           # nm específico
        '[class*="nm-product-card__title"]',          # nm alternativo
        'h2[data-testid]',
        '[class*="ProductTitle"]',
        '[class*="product-title"]',
        '[class*="Title__Wrapper"]',
        'h2[class*="sc-"]',
        'h2',
    ],
    # Preço principal
    "price_candidates": [
        '[data-testid="price-value"]',                # legado
        '[data-testid="main-price"]',                 # legado
        '[class*="nm-price-details__main-price"]',    # nm atual
        '[class*="nm-price"]',                        # nm genérico
        'p[class*="nm-price"]',                       # nm em parágrafo
        '[class*="PriceTag__Price"]',
        '[class*="price-value"]',
        '[class*="Price__Value"]',
        'p[class*="Price"]',
    ],
    # Seller/lojista
    "seller_candidates": [
        '[data-testid="seller-name"]',
        '[class*="nm-seller"]',                       # nm atual
        '[class*="nm-product-card__seller"]',         # nm específico
        '[class*="SellerName"]',
        '[class*="seller-name"]',
        'a[href*="/loja/"]',
    ],
    # Avaliação
    "rating_candidates": [
        '[data-testid="review-score"]',
        '[class*="nm-rating"]',
        '[class*="Rating__Score"]',
        '[class*="rating-score"]',
    ],
    # Contagem de avaliações
    "review_count_candidates": [
        '[data-testid="review-count"]',
        '[data-testid="reviews-count"]',
        '[class*="nm-review-count"]',
        '[class*="ReviewCount"]',
        '[class*="review-count"]',
    ],
    # Tag destaque (Best Seller, Mais Vendido, etc.)
    "tag_candidates": [
        '[data-testid="product-tag"]',
        '[data-testid="badge"]',
        '[class*="nm-badge"]',
        '[class*="ProductBadge"]',
        '[class*="Badge"]',
    ],
    # Patrocinado
    "sponsored_candidates": [
        '[data-testid="sponsored-tag"]',
        '[class*="nm-sponsored"]',
        '[class*="Sponsored"]',
        '[class*="sponsored"]',
    ],
    # Detecção de bloqueio
    "px_block":    "#px-captcha, #pxCaptcha, [id*='px-'], [class*='px-captcha']",
    "cf_block":    "#challenge-form, #challenge-running",
    "akamai_block": "script[src*='akamai'], script[src*='botmanager'], [class*='akamai'], [id*='akamai']",
    "no_results":  '[data-testid="no-results"], [class*="NoResults"], [class*="empty-results"]',
}

# URL da API interna XHR do Magalu (mais estável que HTML)
_API_URL = (
    "https://www.magazineluiza.com.br/api/product-search/v3/queries/search"
    "?query={kw}&page={page}&size=24&sort=relevance&include=facets,suggestions"
)


class MagaluScraper(BaseScraper):
    """Scraper modular para o Magazine Luiza."""

    platform_name = "Magalu"

    # Rotar o browser a cada N keywords para evitar Akamai Bot Manager
    _ROTATION_INTERVAL = 15

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._api_results: List[Dict] = []   # resultados capturados via XHR
        self._keywords_processed: int = 0    # contador para rotação proativa

    # ------------------------------------------------------------------
    # URL builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        """URL de busca padrão da Magalu."""
        encoded = quote_plus(keyword)
        base = f"https://www.magazineluiza.com.br/busca/{encoded}/"
        return f"{base}?page={page}" if page > 1 else base

    @staticmethod
    def _build_url_v2(keyword: str, page: int = 1) -> str:
        """URL alternativa usada em algumas versões do site (?q= format)."""
        encoded = quote_plus(keyword)
        return (
            f"https://www.magazineluiza.com.br/busca/"
            f"?q={encoded}&from=submit&page={page}"
        )

    # ------------------------------------------------------------------
    # Intercepção XHR — captura resposta da API interna
    # ------------------------------------------------------------------

    def _setup_xhr_intercept(self) -> None:
        """Registra listener para capturar respostas da API interna do Magalu."""
        def handle_route(route):
            request = route.request
            # Intercepta requisições para API do Magalu
            if "api/product-search" in request.url:
                response = route.fetch()
                if response.status == 200:
                    try:
                        data = response.json()
                        self._api_results.append(data)
                        logger.debug(
                            f"[{self.platform_name}] XHR capturado: "
                            f"{data.get('count', 0)} itens na resposta"
                        )
                    except Exception as e:
                        logger.debug(f"[{self.platform_name}] Erro ao parsear XHR: {e}")
                route.continue_()
            else:
                route.continue_()

        self._page.route("**/*", handle_route)

    # ------------------------------------------------------------------
    # Detecção de bloqueios
    # ------------------------------------------------------------------

    def _is_akamai_blocked(self, html: str, page: int = 1) -> bool:
        """
        Detecta se Akamai Bot Manager bloqueou a página.
        Akamai deixa pouco sinal na HTML — indicadores são:
          - Script tags com src*=akamai ou botmanager
          - Página vazia/blank (HTTP 200 mas sem conteúdo)
          - Múltiplas tentativas sem resultados
        """
        soup = BeautifulSoup(html, "html.parser")
        
        # Verifica presença de scripts Akamai
        scripts = soup.find_all("script")
        for script in scripts:
            src = script.get("src", "")
            if src and ("akamai" in src.lower() or "botmanager" in src.lower()):
                logger.warning(
                    f"[{self.platform_name}] Script Akamai detectado em página {page}"
                )
                return True
        
        # Verifica se a página está vazia demais (bloqueio silencioso)
        body = soup.body
        if body:
            # Se body tem menos de 2000 caracteres e sem conteúdo útil
            body_text = body.get_text(strip=True)
            if len(body_text) < 1000:
                logger.warning(
                    f"[{self.platform_name}] Página {page} muito pequena ({len(body_text)} chars) — "
                    "possível bloqueio Akamai"
                )
                return True
        
        return False

    def _detect_soft_block(self, html: str, page: int = 1) -> bool:
        """
        Detecta soft-block (página vazia sem erro explícito).
        Diferente de Akamai — soft-block é tratado com retry + backoff.
        """
        soup = BeautifulSoup(html, "html.parser")

        # Procura por indicador explícito "sem resultados"
        no_results = soup.select_one(_SELECTORS["no_results"])
        if no_results:
            logger.debug(
                f"[{self.platform_name}] 'Nenhum resultado' detectado em página {page}"
            )
            return False

        # Se não tem itens e não tem aviso explícito — soft-block
        items = []
        for selector in _SELECTORS["item_candidates"]:
            items = soup.select(selector)
            if items:
                break

        if not items:
            logger.warning(
                f"[{self.platform_name}] Soft-block detectado em página {page} "
                "(0 itens, sem aviso explícito)"
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Parse de resultados
    # ------------------------------------------------------------------

    def _parse_api_results(
        self,
        api_data: Dict[str, Any],
        keyword: str,
        keyword_category_map: dict,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Extrai produtos da resposta JSON da API interna."""
        records: List[Dict[str, Any]] = []

        products = api_data.get("products", [])
        if not products:
            logger.debug(f"[{self.platform_name}] API retornou 0 produtos")
            return records

        for idx, product in enumerate(products, start=page_offset + 1):
            try:
                # Extrai dados base
                product_id = product.get("productId") or product.get("id", "")
                title = product.get("name", "")
                url = product.get("url", "")

                # Preço (pode estar em múltiplas localizações)
                price_obj = product.get("price", {})
                if isinstance(price_obj, dict):
                    price_str = str(price_obj.get("price", 0))
                else:
                    price_str = str(price_obj) if price_obj else "0"

                price = parse_price(price_str) if price_str else None

                # Seller
                seller_obj = product.get("seller", {})
                if isinstance(seller_obj, dict):
                    seller = seller_obj.get("name", "")
                else:
                    seller = str(seller_obj) if seller_obj else ""

                # Avaliação
                rating = parse_rating(product.get("score"))
                review_count = parse_review_count(product.get("reviews"))

                # Validação mínima
                if not title or not product_id:
                    logger.debug(f"[{self.platform_name}] Produto sem título/ID ignorado")
                    continue

                if url and not url.startswith("http"):
                    url = f"https://www.magazineluiza.com.br{url}"

                records.append(self._build_record(
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                    title=title.strip(),
                    position_general=idx,
                    position_organic=idx,
                    position_sponsored=None,
                    price_float=price,
                    seller=seller.strip() if seller else "Magalu",
                    is_fulfillment=False,
                    rating=rating,
                    review_count=review_count,
                    url_produto=url.strip() if url else None,
                ))

            except Exception as e:
                logger.debug(f"[{self.platform_name}] Erro parseando produto: {e}")
                continue

        logger.info(
            f"[{self.platform_name}] API: {len(records)} produtos extraídos"
        )
        return records

    def _parse_results_dom(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page: int = 1,
        page_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Parse do DOM quando XHR falha."""
        records: List[Dict[str, Any]] = []

        soup = BeautifulSoup(html, "html.parser")

        # Encontra itens
        items = []
        for selector in _SELECTORS["item_candidates"]:
            items = soup.select(selector)
            if items:
                logger.debug(
                    f"[{self.platform_name}] Seletor '{selector}' retornou {len(items)} itens"
                )
                break

        if not items:
            logger.info(
                f"[{self.platform_name}] 0 itens encontrados na página (seletor: nenhum)"
            )
            # Salva HTML para debug
            debug_file = (
                LOGS_DIR / f"magalu_debug_p{page}_{keyword.replace(' ', '_')[:30]}.html"
            )
            debug_file.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo para diagnóstico: {debug_file}\n"
                f"  → Abra o arquivo no browser e inspecione o seletor correto."
            )
            return records

        # Parse de cada item
        for idx, item in enumerate(items, start=page_offset + 1):
            try:
                # Título
                title = ""
                for selector in _SELECTORS["title_candidates"]:
                    elem = item.select_one(selector)
                    if elem:
                        title = elem.get_text(strip=True)
                        if title:
                            break

                # URL (link do produto)
                url = ""
                for link_selector in ['a[href*="/p/"]', 'a[href*="magalu"]', 'a']:
                    link_elem = item.select_one(link_selector)
                    if link_elem and link_elem.get("href"):
                        url = link_elem.get("href")
                        if not url.startswith("http"):
                            url = f"https://www.magazineluiza.com.br{url}"
                        break

                # Preço
                price = None
                for selector in _SELECTORS["price_candidates"]:
                    price_elem = item.select_one(selector)
                    if price_elem:
                        price_str = price_elem.get_text(strip=True)
                        price = parse_price(price_str)
                        if price:
                            break

                # Seller
                seller = "Magalu"
                for selector in _SELECTORS["seller_candidates"]:
                    seller_elem = item.select_one(selector)
                    if seller_elem:
                        seller = seller_elem.get_text(strip=True)
                        if seller:
                            break

                # Avaliação
                rating = None
                for selector in _SELECTORS["rating_candidates"]:
                    rating_elem = item.select_one(selector)
                    if rating_elem:
                        rating_str = rating_elem.get_text(strip=True)
                        rating = parse_rating(rating_str)
                        if rating:
                            break

                # Contagem de avaliações
                review_count = None
                for selector in _SELECTORS["review_count_candidates"]:
                    review_elem = item.select_one(selector)
                    if review_elem:
                        review_str = review_elem.get_text(strip=True)
                        review_count = parse_review_count(review_str)
                        if review_count:
                            break

                # Validação mínima
                if not title or not url:
                    logger.debug(f"[{self.platform_name}] Item sem título/URL ignorado")
                    continue

                records.append(self._build_record(
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                    title=title.strip(),
                    position_general=idx,
                    position_organic=idx,
                    position_sponsored=None,
                    price_float=price,
                    seller=seller.strip() if seller else "Magalu",
                    is_fulfillment=False,
                    rating=rating,
                    review_count=review_count,
                    url_produto=url.strip(),
                ))

            except Exception as e:
                logger.debug(f"[{self.platform_name}] Erro parseando item: {e}")
                continue

        logger.info(f"[{self.platform_name}] DOM: {len(records)} produtos extraídos")
        return records

    # ------------------------------------------------------------------
    # Métodos auxiliares
    # ------------------------------------------------------------------

    def _wait_for_products(self, timeout_ms: int = 15_000) -> bool:
        """Aguarda produtos aparecerem na página."""
        try:
            for selector in _SELECTORS["item_candidates"]:
                try:
                    self._page.wait_for_selector(selector, timeout=timeout_ms)
                    logger.debug(
                        f"[{self.platform_name}] Produtos detectados via '{selector}'"
                    )
                    return True
                except Exception:
                    continue
            logger.debug(f"[{self.platform_name}] Nenhum seletor de produto respondeu")
            return False
        except Exception as e:
            logger.debug(f"[{self.platform_name}] wait_for_products erro: {e}")
            return False

    def _wait_for_network_idle(self, timeout_ms: int = 5_000) -> None:
        """Aguarda requisições pendentes terminarem."""
        try:
            self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception as e:
            logger.debug(f"[{self.platform_name}] wait_for_network_idle timeout: {e}")

    def _human_scroll(self, steps: int = 10, step_px: int = 280) -> None:
        """Scroll humanizado com delays aleatórios."""
        for i in range(steps):
            self._page.evaluate(
                f"window.scrollBy(0, {step_px})"
            )
            time.sleep(random.uniform(0.3, 0.8))

    def _random_delay(self, min_s: float = 1.0, max_s: float = 3.0) -> None:
        """Delay aleatório humanizado."""
        delay = random.uniform(min_s, max_s)
        time.sleep(delay)

    # ------------------------------------------------------------------
    # Search — orquestração principal
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=15, max=120) + wait_random(0, 5),
        retry=retry_if_exception_type(MagaluSoftBlockException),
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
        soft_block_detected = False

        # Delay humanizado entre keywords para evitar padrão detectável
        if self._keywords_processed > 0:
            _inter_delay = random.uniform(8.0, 20.0)
            logger.debug(
                f"[{self.platform_name}] Aguardando {_inter_delay:.1f}s antes da próxima keyword..."
            )
            time.sleep(_inter_delay)

        # Rotação proativa a cada _ROTATION_INTERVAL keywords (reseta fingerprint)
        self._keywords_processed += 1
        if self._keywords_processed > 1 and self._keywords_processed % self._ROTATION_INTERVAL == 0:
            logger.info(
                f"[{self.platform_name}] Rotação proativa — "
                f"{self._keywords_processed} keywords processadas"
            )
            self._rotate_browser()

        # Configura intercepção XHR antes de navegar
        self._setup_xhr_intercept()

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")

            try:
                # Rotaciona User-Agent e injeta headers realistas a cada navegação
                _ua = random.choice(USER_AGENTS)
                self._page.set_extra_http_headers({
                    "User-Agent": _ua,
                    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;"
                        "q=0.9,image/avif,image/webp,*/*;q=0.8"
                    ),
                    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                })

                self._page.goto(url, wait_until="domcontentloaded")

                # DETECÇÃO AKAMAI — AQUI ESTÁ A CORREÇÃO CRÍTICA
                current_html = self._page.content()
                if self._is_akamai_blocked(current_html, page):
                    logger.error(
                        f"[{self.platform_name}] 🚫 Akamai Bot Manager detectado (página {page}) "
                        "— bloqueio hard. Proxy residencial brasileiro é necessário para bypass."
                    )
                    raise MagaluAkamaiBlockException(
                        f"Akamai bloqueou em '{keyword}' — requer proxy residencial"
                    )

                # Aguarda produtos aparecerem (máx 15s)
                found = self._wait_for_products(timeout_ms=15_000)
                if not found:
                    logger.debug(
                        f"[{self.platform_name}] wait_for_products timeout — "
                        "continuando com o que carregou"
                    )

                # Aguarda rede estabilizar + delay humanizado
                self._wait_for_network_idle()
                self._random_delay(min_s=3.5, max_s=8.5)
                self._human_scroll(steps=10, step_px=280)
                self._random_delay(min_s=1.0, max_s=2.5)

                # captura screenshot da página de busca
                self._last_screenshot_busca = self.capture_screenshot(identifier=f"{keyword}_p{page}", tipo="busca")

                # --- Tenta usar resultados XHR capturados primeiro ---
                api_records: List[Dict[str, Any]] = []
                if self._api_results:
                    offset = (page - 1) * 24
                    for data in self._api_results:
                        api_records.extend(
                            self._parse_api_results(
                                data, keyword, keyword_category_map, offset
                            )
                        )
                    self._api_results = []   # limpa para próxima página

                if api_records:
                    logger.info(
                        f"[{self.platform_name}] {len(api_records)} itens via API XHR"
                    )
                    all_records.extend(api_records)
                else:
                    # --- Fallback: parse DOM ---
                    offset = (page - 1) * 40
                    records = self._parse_results_dom(
                        html=self._page.content(),
                        keyword=keyword,
                        keyword_category_map=keyword_category_map,
                        page=page,
                        page_offset=offset,
                    )

                    # Se ainda 0, tenta URL alternativa (?q= format) na pág 1
                    if not records and page == 1:
                        alt_url = self._build_url_v2(keyword, page)
                        logger.info(
                            f"[{self.platform_name}] 0 resultados — tentando URL alternativa: {alt_url}"
                        )
                        self._page.goto(alt_url, wait_until="domcontentloaded")
                        self._wait_for_products(timeout_ms=12_000)
                        self._wait_for_network_idle()
                        self._random_delay(min_s=2.5, max_s=6.0)
                        self._human_scroll(steps=8, step_px=280)

                        # recaptura screenshot da página alternativa
                        self._last_screenshot_busca = self.capture_screenshot(identifier=f"{keyword}_p{page}", tipo="busca")

                        # Checa XHR capturado na segunda tentativa
                        if self._api_results:
                            for data in self._api_results:
                                records.extend(
                                    self._parse_api_results(
                                        data, keyword, keyword_category_map, 0
                                    )
                                )
                            self._api_results = []
                        else:
                            records = self._parse_results_dom(
                                html=self._page.content(),
                                keyword=keyword,
                                keyword_category_map=keyword_category_map,
                                page=page,
                                page_offset=0,
                            )

                    if not records:
                        _current_html = self._page.content()
                        if self._detect_soft_block(_current_html, page):
                            soft_block_detected = True
                            raise MagaluSoftBlockException(
                                f"Soft-block em '{keyword}' (página {page}) — "
                                "retry com backoff será ativado"
                            )
                        logger.warning(
                            f"[{self.platform_name}] Página {page} retornou 0 itens — "
                            "fim de resultados legítimo. Parando keyword."
                        )
                        break
                    all_records.extend(records)

                if page < page_limit:
                    self._random_delay(min_s=2.0, max_s=5.0)

            except MagaluAkamaiBlockException:
                # Akamai hard block — aborta toda a keyword imediatamente
                logger.error(
                    f"[{self.platform_name}] Akamai bloqueou scraper — "
                    f"abortando keyword '{keyword}'"
                )
                break  # sai do loop de páginas, retorna o que coletou até agora
            except Exception as exc:
                logger.error(f"[{self.platform_name}] Erro na página {page}: {exc}")
                raise

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        logger.info(json.dumps({
            "plataforma": self.platform_name,
            "keyword": keyword,
            "itens_coletados": len(all_records),
            "soft_block_detectado": soft_block_detected,
            "timestamp": now_brt().isoformat(),
        }, ensure_ascii=False))
        return all_records
