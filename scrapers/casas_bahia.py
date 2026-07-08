"""
scrapers/casas_bahia.py — Scraper da Casas Bahia (casasbahia.com.br).

Estratégia (em ordem de prioridade):
  0. API VTEX via curl_cffi — replica TLS fingerprint do Chrome real, muito mais
     difícil de bloquear que requests padrão. Tenta catalog_system e IS endpoints.
  1. API VTEX via requests — fallback simples (pode ser bloqueado pelo Akamai).
  2. Browser + sessão salva (session_grabber.py) + XHR interception.
  3. Parse DOM com cadeia de seletores fallback + img[alt].
  4. Debug HTML dump automático em logs/ quando 0 itens.

Proteção: WAF Akamai / PerimeterX bloqueia browsers headless.
  curl_cffi contorna o Akamai na camada TLS (JA3/JA4 fingerprint real do Chrome).
  Se ainda bloquear: rodar session_grabber.py para bypass manual ou proxy residencial.
Paginação: parâmetro &page={n}.
"""

import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests

# curl_cffi: TLS fingerprint real do Chrome — bypassa Akamai JA3/JA4 detection
try:
    from curl_cffi import requests as _cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False
from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, LOGS_DIR, PAGE_TIMEOUT
from scrapers.base import BaseScraper
from scrapers.local_browser import get_local_browser, is_local_chrome_enabled
from utils.text import parse_price, parse_rating, parse_review_count

_ITEMS_PER_PAGE = 24

# Endpoints VTEX da Casas Bahia — chamados diretamente (sem browser, bypass Akamai)
_VTEX_BASE = "https://www.casasbahia.com.br"
_VTEX_CATALOG_URL = f"{_VTEX_BASE}/api/catalog_system/pub/products/search"
_VTEX_IS_URL      = f"{_VTEX_BASE}/_v/api/intelligent-search/product_search/pt/pt-BR/search"

_VTEX_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.casasbahia.com.br",
    "Referer": "https://www.casasbahia.com.br/",
    # Sec-Fetch headers são críticos para Akamai — indicam requisição AJAX legítima
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
_API_TIMEOUT = 8

_SELECTORS = {
    # Cadeia de fallback — Casas Bahia usa VTEX IO
    "item_candidates": [
        "[data-testid='product-card']",
        "[class*='ProductCard']",
        "[class*='product-card']",
        "article[class*='product']",
        "[class*='vtex-product-summary']",
        "li[class*='vtex-product-summary']",
        "[class*='productSummary']",
        "[class*='shelf-item']",
        "[class*='ShelfItem']",
        "li[class*='product']",
        "[class*='product-item']",
    ],
    "title_candidates": [
        "[data-testid='product-name']",
        "[class*='productName']",
        "[class*='ProductName']",
        "[class*='vtex-product-summary-2-x-productBrand']",
        "[class*='vtex-product-summary-2-x-nameContainer']",
        "h3[class*='product']",
        "h2[class*='product']",
        "h3", "h2",
    ],
    "price_candidates": [
        "[data-testid='price-best-price']",
        ".vtex-product-price-1-x-sellingPrice",
        "[class*='sellingPrice']",
        "[class*='bestPrice']",
        "[class*='productPrice']",
        "[class*='price']",
    ],
    "seller":        "[data-testid='seller-name'], [class*='sellerName']",
    "rating":        "[class*='ratingValue'], [class*='rating-value'], [class*='Rating']",
    "review_count":  "[class*='reviewCount'], [class*='review-count']",
    "tag_destaque":  "[data-testid='discount-badge'], [class*='discountBadge'], [class*='badge']",
    "sponsored":     "[data-testid='sponsored'], [class*='sponsored']",
    "waf_block":     "#ak-challenge-error, #challenge-container, .ak-challenge",
}

# Padrões de URL para XHR interception VTEX
_API_URL_PATTERNS = [
    "intelligent-search/product_search",
    "catalog_system/pub/products",
    "_v/api/intelligent",
    "product-search",
    "api/catalog",
    "search/products",
]

# Padrões de redirect/bloqueio
_BLOCKED_URL_PATTERNS = [
    "/login",
    "/captcha",
    "/blocked",
    "/challenge",
    "akamai",
]

# Circuit breaker: após N keywords seguidas bloqueadas pelo Akamai, aborta a
# coleta inteira — cada keyword bloqueada queimava ~35-40s de navegação
# garantidamente inútil (31 keywords ≈ 20min por execução).
_ABORT_AFTER_BLOCKED_KEYWORDS = 3

# Campo de busca — usado pra busca ORGÂNICA (digitar + Enter) em vez de
# `goto('/busca?q=...')`. Um goto direto pra uma URL de resultado chega ao
# Akamai com assinatura de bot (usuário real só chega em /busca digitando no
# campo → navegação same-origin com Referer). Foi a lição que destravou o
# Magalu contra o MESMO customdeny do Akamai (novavp-a.akamaihd.net).
_SEARCH_INPUT_SELECTORS = (
    'input[data-testid="store-input"]',
    'input[data-testid="search-input"]',
    'input[placeholder*="Busca" i]',
    'input[placeholder*="busca" i]',
    'input[placeholder*="O que você procura" i]',
    'input[type="search"]',
    'input[name="q"]',
    'form[role="search"] input',
    'header input[type="text"]',
)


class CasasBahiaScraper(BaseScraper):
    """Scraper modular para Casas Bahia."""

    platform_name = "Casas Bahia"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._captured_products: List[Dict] = []
        # Session curl_cffi aquecida (cookies Akamai). Reaproveitada entre
        # keywords; o Akamai valida o _abck por ~30min, renovamos a cada 10min.
        self._cffi_session: Optional[Any] = None
        self._cffi_session_ts: float = 0.0

        # UA coerente: o Akamai cruza UA × Client Hints × TLS × cookies. O
        # sorteio do BaseScraper podia cair em UA Linux/Firefox rodando num
        # Chrome real Windows — flag instantânea. Prioridade: UA do browser
        # que gerou a sessão salva (refresh_sessions_cdp) > UA Chrome/Windows.
        self._user_agent = _VTEX_HEADERS["User-Agent"]
        try:
            from utils.session_grabber import load_session_meta
            session_ua = (load_session_meta("casasbahia") or {}).get("userAgent")
            if session_ua:
                self._user_agent = session_ua
        except Exception:
            pass

        # Modo CDP (Chrome real do usuário) — setado em _launch()
        self._cdp_active: bool = False
        # Modo browser local (Chrome real logado, RAC_LOCAL_CHROME) — usa o
        # mesmo caminho do CDP (browser real primeiro), porém sem porta de
        # debug: o próprio processo abre o Chrome persistente compartilhado.
        self._local_active: bool = False
        self._local_browser: Optional[Any] = None
        # Página com handler de XHR já registrado (evita handlers duplicados
        # acumulando entre keywords — cada search() chamava page.on de novo)
        self._xhr_page: Optional[Any] = None

        # Circuit breaker (ver _ABORT_AFTER_BLOCKED_KEYWORDS)
        self._akamai_blocked: bool = False       # bloqueio na keyword atual
        self._blocked_keyword_streak: int = 0
        self._collection_aborted: bool = False

        # Warm-up CDP feito 1x por execução: visita a home e espera o sensor.js
        # do Akamai validar o _abck (vira "~0~"). Sem isso, um goto direto pra
        # /busca chega ao Akamai antes da validação → bloqueio (foi o que
        # derrubou a coleta mesmo via Chrome real). Mesma lição do Magalu.
        self._cdp_warmed: bool = False

    def _vtex_headers(self) -> Dict[str, str]:
        """Headers da API VTEX com o UA alinhado à sessão/plataforma."""
        headers = dict(_VTEX_HEADERS)
        headers["User-Agent"] = self._user_agent
        return headers

    @property
    def _real_browser_active(self) -> bool:
        """True quando há um Chrome REAL (CDP externo ou local logado) em uso.

        Ambos os modos seguem o mesmo caminho rico: browser real primeiro
        (fingerprint aceito pelo Akamai), APIs VTEX como fallback.
        """
        return self._cdp_active or self._local_active

    # ------------------------------------------------------------------
    # Browser: modo CDP (Chrome real) ou launch próprio (fallback)
    # ------------------------------------------------------------------

    def _launch(self) -> None:
        """
        Conecta ao Chrome real via CDP quando RAC_CDP_URL/MAGALU_CDP_URL está
        setado (mesma técnica que destravou o Magalu): cookies, fingerprint e
        sensor.js validado são do browser que o Akamai já aceita. Injetar os
        cookies salvos num browser Playwright próprio NÃO basta — o Akamai
        vincula o _abck ao fingerprint do browser que o emitiu.

        Sem CDP, cai no launch padrão do BaseScraper.
        """
        # Preferência no notebook: Chrome real logado compartilhado
        # (RAC_LOCAL_CHROME). Mesmo fingerprint aceito pelo Akamai, sem depender
        # de porta de debug (que o Chrome 136+ ignora no perfil padrão).
        if is_local_chrome_enabled():
            lb = get_local_browser()
            if lb is not None:
                page = lb.new_page()
                if page is not None:
                    self._local_browser = lb
                    self._context = lb.context
                    self._page = page
                    self._page.set_default_timeout(PAGE_TIMEOUT)
                    self._local_active = True
                    self._setup_xhr_intercept()
                    logger.info(
                        f"[{self.platform_name}] Chrome real local (perfil "
                        "compartilhado) — fingerprint nativo contra o Akamai"
                    )
                    return
            logger.warning(
                f"[{self.platform_name}] RAC_LOCAL_CHROME ligado mas o Chrome local "
                "não abriu — tentando CDP/curl_cffi"
            )

        cdp_url = (
            os.getenv("RAC_CDP_URL", "").strip()
            or os.getenv("MAGALU_CDP_URL", "").strip()
        )
        if not cdp_url:
            super()._launch()
            return

        # rebrowser-playwright oculta o Runtime.enable do sensor.js (mesmo
        # requisito do magalu.py — o import seta REBROWSER_PATCHES_RUNTIME_FIX_MODE)
        from scrapers.magalu import _import_sync_playwright
        sync_playwright, flavor = _import_sync_playwright()
        if sync_playwright is None:
            super()._launch()
            return

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(
                cdp_url, timeout=10_000
            )
            if not self._browser.contexts:
                raise RuntimeError("Chrome CDP sem contexto aberto")
            self._context = self._browser.contexts[0]
            # Aba DEDICADA — nunca sequestra/navega uma aba do usuário
            self._page = self._context.new_page()
            self._page.set_default_timeout(PAGE_TIMEOUT)
            self._cdp_active = True
            logger.info(
                f"[{self.platform_name}] Chrome real conectado via CDP em "
                f"{cdp_url} ({flavor}) — fingerprint nativo contra o Akamai"
            )
        except Exception as exc:
            logger.warning(
                f"[{self.platform_name}] CDP indisponível ({exc}) — "
                "abrindo browser próprio (fallback, Akamai pode bloquear)"
            )
            try:
                if self._playwright:
                    self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
            self._browser = None
            self._context = None
            self._page = None
            self._cdp_active = False
            super()._launch()

    def _close(self) -> None:
        # Modo browser local: fecha SÓ a aba — a janela é compartilhada e
        # fechada no fim da coleta (close_local_browser).
        if self._local_active:
            try:
                if self._page and not self._page.is_closed():
                    self._page.close()
            except Exception:
                pass
            self._page = None
            self._context = None
            self._xhr_page = None
            self._local_active = False
            return

        if not self._cdp_active:
            super()._close()
            return
        # CDP: fecha SÓ a aba dedicada e desconecta — o Chrome é do usuário
        try:
            if self._page and not self._page.is_closed():
                self._page.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()  # close() em CDP-browser apenas desconecta
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._cdp_active = False

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        url = f"https://www.casasbahia.com.br/busca?q={encoded}"
        if page > 1:
            url += f"&page={page}"
        return url

    # ------------------------------------------------------------------
    # Warm-up de sessão Akamai (curl_cffi)
    # ------------------------------------------------------------------

    _SESSION_MAX_AGE_SEC = 10 * 60  # renova cookies Akamai a cada 10min

    def _get_warmed_session(self) -> Optional[Any]:
        """
        Retorna uma session curl_cffi com cookies Akamai válidos.

        Passos (mesma técnica que destrava Magalu/Casas Bahia na camada TLS):
          1. Cria session com impersonation chrome124 (JA3/JA4 do Chrome real).
          2. Injeta cookies de sessão manual (session_grabber.py), se existirem.
          3. GET na home → Akamai emite _abck/bm_sz/ak_bmsc na MESMA session.
          4. Cacheia a session por ~10min (evita warm-up a cada keyword).

        Returns:
            Session aquecida, ou None se curl_cffi indisponível.
        """
        if not _HAS_CURL_CFFI:
            return None

        now = time.time()
        if self._cffi_session is not None and (now - self._cffi_session_ts) < self._SESSION_MAX_AGE_SEC:
            return self._cffi_session

        session = _cffi_requests.Session()

        # Cookies de sessão manual (opcional) — Akamai libera mais fácil com sessão real
        try:
            from utils.session_grabber import load_session
            session_cookies = load_session("casasbahia") or []
            for c in session_cookies:
                domain = c.get("domain", "casasbahia.com.br").lstrip(".")
                session.cookies.set(c["name"], c["value"], domain=domain)
            if session_cookies:
                logger.info(
                    f"[{self.platform_name}] Sessão manual aplicada "
                    f"({len(session_cookies)} cookies)"
                )
        except Exception:
            pass

        # Warm-up: GET na home para o Akamai emitir cookies frescos
        try:
            warm_headers = {
                "User-Agent": self._user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": _VTEX_HEADERS["Accept-Language"],
                "Upgrade-Insecure-Requests": "1",
            }
            resp = session.get(
                _VTEX_BASE, headers=warm_headers,
                impersonate="chrome124", timeout=_API_TIMEOUT,
            )
            akamai_cookies = [
                n for n in ("_abck", "bm_sz", "ak_bmsc", "bm_sv", "AKA_A2")
                if n in session.cookies
            ]
            logger.info(
                f"[{self.platform_name}] Warm-up home: HTTP {resp.status_code} | "
                f"cookies Akamai: {', '.join(akamai_cookies) or 'nenhum'}"
            )
            # Pequena pausa humana entre warm-up e API
            time.sleep(1.5)
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] Warm-up falhou: {exc}")
            # Mesmo sem warm-up, tenta usar a session (pode ter cookies manuais)

        self._cffi_session = session
        self._cffi_session_ts = now
        return session

    # ------------------------------------------------------------------
    # Estratégia 0: VTEX API via curl_cffi (TLS fingerprint real do Chrome)
    # ------------------------------------------------------------------

    def _vtex_cffi_search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """
        Consulta a API VTEX usando curl_cffi com impersonation do Chrome124.
        O Akamai Bot Manager usa JA3/JA4 TLS fingerprint para detectar bots;
        curl_cffi replica o fingerprint exato do Chrome real, bypass efetivo.
        """
        if not _HAS_CURL_CFFI:
            return []

        from_idx = page_offset
        to_idx   = page_offset + _ITEMS_PER_PAGE - 1
        encoded  = quote_plus(keyword)

        # Reutiliza UMA session em toda a busca: o warm-up na home faz o Akamai
        # emitir cookies frescos (_abck/bm_sz/ak_bmsc) que precisam viajar junto
        # com a chamada de API seguinte. Criar uma session nova por request (bug
        # anterior) descartava esses cookies → 403/HTML. Mesma técnica do Magalu.
        cffi_session = self._get_warmed_session()
        if cffi_session is None:
            return []

        def _cffi_get(url: str, params: dict) -> Optional[object]:
            """Helper: GET com curl_cffi na session aquecida + content-type guard."""
            resp = cffi_session.get(
                url, headers=self._vtex_headers(), params=params,
                impersonate="chrome124", timeout=_API_TIMEOUT,
            )
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "application/json" in ct:
                return resp.json()
            logger.debug(
                f"[{self.platform_name}] curl_cffi: HTTP {resp.status_code} "
                f"CT={ct[:60]} url={url[:70]}"
            )
            return None

        # Endpoint 1: Catalog System
        try:
            data = _cffi_get(
                f"{_VTEX_CATALOG_URL}/{encoded}",
                {"_from": from_idx, "_to": to_idx},
            )
            if isinstance(data, list) and data:
                logger.info(
                    f"[{self.platform_name}] VTEX curl_cffi catalog: "
                    f"{len(data)} produtos (pág {page})"
                )
                return self._parse_api_products(keyword, keyword_category_map,
                                                page_offset, products=data)
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] VTEX curl_cffi catalog erro: {exc}")

        # Endpoint 2: Intelligent Search
        try:
            data = _cffi_get(
                _VTEX_IS_URL,
                {"query": keyword, "page": page, "count": _ITEMS_PER_PAGE,
                 "sort": "score_desc", "hideUnavailableItems": "false"},
            )
            if isinstance(data, dict):
                products = (
                    data.get("products")
                    or (data.get("productSearch") or {}).get("products")
                    or []
                )
                if products:
                    logger.info(
                        f"[{self.platform_name}] VTEX curl_cffi IS: "
                        f"{len(products)} produtos (pág {page})"
                    )
                    return self._parse_api_products(keyword, keyword_category_map,
                                                    page_offset, products=products)
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] VTEX curl_cffi IS erro: {exc}")

        return []

    # ------------------------------------------------------------------
    # Estratégia 1: VTEX API via requests (fallback)
    # ------------------------------------------------------------------

    def _vtex_api_search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """
        Consulta a API VTEX da Casas Bahia diretamente via requests.
        A requisição HTTP simples frequentemente não é bloqueada pelo Akamai,
        que foca em bloquear browsers automatizados (fingerprinting JS).
        Tenta dois endpoints: catalog_system e intelligent-search.
        """
        from_idx = page_offset
        to_idx   = page_offset + _ITEMS_PER_PAGE - 1

        # Endpoint 1: Catalog System (mais simples e estável)
        try:
            encoded = quote_plus(keyword)
            resp = requests.get(
                f"{_VTEX_CATALOG_URL}/{encoded}",
                headers=self._vtex_headers(),
                params={"_from": from_idx, "_to": to_idx},
                timeout=_API_TIMEOUT,
            )
            if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
                products = resp.json()
                if isinstance(products, list) and products:
                    logger.info(
                        f"[{self.platform_name}] VTEX Catalog API: "
                        f"{len(products)} produtos (pág {page})"
                    )
                    return self._parse_api_products(keyword, keyword_category_map, page_offset,
                                                    products=products)
        except Exception as e:
            logger.debug(f"[{self.platform_name}] VTEX Catalog API erro: {e}")

        # Endpoint 2: Intelligent Search
        try:
            resp = requests.get(
                _VTEX_IS_URL,
                headers=self._vtex_headers(),
                params={
                    "query": keyword,
                    "page": page,
                    "count": _ITEMS_PER_PAGE,
                    "sort": "score_desc",
                    "hideUnavailableItems": "false",
                },
                timeout=_API_TIMEOUT,
            )
            if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
                data = resp.json()
                products = (
                    data.get("products")
                    or (data.get("productSearch") or {}).get("products")
                    or []
                )
                if products:
                    logger.info(
                        f"[{self.platform_name}] VTEX IS API: "
                        f"{len(products)} produtos (pág {page})"
                    )
                    return self._parse_api_products(keyword, keyword_category_map, page_offset,
                                                    products=products)
        except Exception as e:
            logger.debug(f"[{self.platform_name}] VTEX IS API erro: {e}")

        return []

    # ------------------------------------------------------------------
    # Detecção de bloqueio
    # ------------------------------------------------------------------

    def _check_blocked(self, html: str) -> bool:
        current_url = self._page.url
        for p in _BLOCKED_URL_PATTERNS:
            if p in current_url:
                logger.warning(f"[{self.platform_name}] Redirecionado para bloqueio: {current_url}")
                return True

        # Detecta página de erro Akamai WAF
        # ("Ops! Algo deu errado." + CSS de novavp-a.akamaihd.net)
        html_head = html[:8000]
        if "akamaihd.net" in html_head or "Ops! Algo deu errado" in html_head:
            logger.warning(
                f"[{self.platform_name}] Akamai WAF bloqueou a requisição. "
                "IP identificado como bot. Solução: proxy residencial brasileiro."
            )
            return True

        soup = BeautifulSoup(html_head, "html.parser")
        # Seletor WAF + classe page-not-found (Akamai)
        if soup.select_one(_SELECTORS["waf_block"]) or soup.select_one("body.page-not-found, .page-not-found"):
            logger.warning(f"[{self.platform_name}] WAF/Akamai detectado via seletor CSS.")
            return True

        return False

    # ------------------------------------------------------------------
    # XHR interception
    # ------------------------------------------------------------------

    def _setup_xhr_intercept(self) -> None:
        self._captured_products = []

        # Handler já registrado nesta página — só zera o buffer. Sem este
        # guard, cada search() empilhava um handler novo na MESMA página e
        # os produtos capturados via XHR saíam multiplicados.
        if self._xhr_page is self._page and self._page is not None:
            return
        self._xhr_page = self._page

        def handle_response(response):
            try:
                url = response.url
                if not any(pat in url for pat in _API_URL_PATTERNS):
                    return
                if response.status != 200:
                    return
                ct = response.headers.get("content-type", "").lower()
                if "text/html" in ct:
                    return
                try:
                    data = json.loads(response.text())
                except Exception:
                    return
                products = (
                    data.get("products")
                    or data.get("items")
                    or data.get("data", {}).get("products")
                    or (data.get("productSearch") or {}).get("products")
                    or (data if isinstance(data, list) else [])
                )
                if products and isinstance(products, list):
                    self._captured_products.extend(products)
                    logger.debug(
                        f"[{self.platform_name}] XHR: {len(products)} produtos em {url[:70]}"
                    )
            except Exception:
                pass

        self._page.on("response", handle_response)

    @staticmethod
    def _classify_seller(seller_name: Optional[str], seller_id: Optional[str]) -> Optional[str]:
        """Classifica seller VTEX da Casas Bahia em 1P (próprio) vs 3P (marketplace).

        Retorna None quando não há NENHUM dado de seller (payload sem
        ``sellers[]``): dado desconhecido não pode ser contado como vitória 1P.
        """
        name = (seller_name or "").strip().lower()
        if not name and not seller_id:
            return None
        if seller_id and str(seller_id) == "1":
            return "1P"
        if any(t in name for t in ("casas bahia", "casasbahia", "via varejo", "grupo casas bahia")):
            return "1P"
        # VTEX: sellerId "1" é a casa; qualquer outro id/nome é marketplace.
        return "3P"

    def _extract_vtex_sellers(self, prod: Dict) -> Dict[str, Any]:
        """
        Extrai buy box e competição de sellers do produto VTEX.

        Estrutura VTEX: prod["items"][i]["sellers"][j] tem sellerName, sellerId,
        sellerDefault (bool) e commertialOffer.Price/IsAvailable. Prioridade do
        vencedor da buy box (independente da ORDEM em que os sellers aparecem
        no array — ver nota abaixo): sellerDefault DISPONÍVEL > qualquer
        DISPONÍVEL > sellerDefault indisponível (best-effort) > primeiro do
        array. O total de sellers distintos com oferta disponível é a
        competição na listagem.

        Returns dict: buy_box_seller, qtd_sellers, tipo_seller, price_float.
        """
        all_sellers: List[Dict[str, Any]] = []
        distinct_sellers: set = set()

        for item in (prod.get("items") or []):
            for seller in (item.get("sellers") or []):
                offer = seller.get("commertialOffer") or {}
                available = bool(offer.get("IsAvailable", True))
                sid = seller.get("sellerId")
                sname = seller.get("sellerName")
                if available and (sid or sname):
                    distinct_sellers.add(str(sid or sname))
                price = offer.get("Price") or offer.get("ListPrice")
                try:
                    price_f = float(str(price)) if price else None
                except (ValueError, TypeError):
                    price_f = None
                all_sellers.append({
                    "sid": sid, "sname": sname, "available": available,
                    "is_default": bool(seller.get("sellerDefault")),
                    "price": price_f,
                })

        # Passe por prioridade explícita (NÃO single-pass): um sellerDefault
        # indisponível que apareça ANTES de um seller disponível no array não
        # pode "travar" o vencedor — precisa ceder pro disponível. Um loop de
        # estado único (visto num bug anterior) é sensível à ordem do array;
        # esta busca em camadas é determinística independente da ordem.
        buy_box = next(
            (s for s in all_sellers if s["available"] and s["is_default"]), None
        ) or next((s for s in all_sellers if s["available"]), None) \
          or next((s for s in all_sellers if s["is_default"]), None) \
          or (all_sellers[0] if all_sellers else None)

        buy_box_name = buy_box["sname"] if buy_box else None
        buy_box_id = buy_box["sid"] if buy_box else None
        buy_box_price = buy_box["price"] if buy_box else None

        # Payload sem sellers[] → buy box desconhecida (None), NÃO vitória 1P
        # da casa. O caller decide o que mostrar no campo display `seller`.
        return {
            "buy_box_seller": buy_box_name,
            "qtd_sellers": len(distinct_sellers) or None,
            "tipo_seller": self._classify_seller(buy_box_name, buy_box_id),
            "price_float": buy_box_price,
        }

    @staticmethod
    def _extract_vtex_rating(prod: Dict) -> Tuple[Optional[float], Optional[int]]:
        """Extrai (avaliação média, nº de avaliações) do payload VTEX IS.

        A VTEX Intelligent Search expõe rating em formatos que variam conforme
        o app de reviews instalado: número simples (``rating``,
        ``aggregateRating``), dict aninhado (``rating: {average, count}`` /
        ``{value, totalCount}``) ou campos separados (``reviews``,
        ``totalReviews``, ``reviewCount``).

        Args:
            prod: dict do produto como veio da API VTEX IS.

        Returns:
            Tupla (rating, review_count); (None, None) quando ausente/ inválido.
        """
        rating: Optional[float] = None
        review_count: Optional[int] = None

        raw = prod.get("rating")
        if raw is None:
            raw = prod.get("aggregateRating")
        if isinstance(raw, dict):
            for key in ("average", "value", "ratingValue", "rating"):
                if raw.get(key) is not None:
                    try:
                        rating = float(str(raw[key]).replace(",", "."))
                        break
                    except (TypeError, ValueError):
                        continue
            for key in ("count", "totalCount", "reviewCount", "ratingCount"):
                if raw.get(key) is not None:
                    try:
                        review_count = int(float(str(raw[key])))
                        break
                    except (TypeError, ValueError):
                        continue
        elif raw is not None:
            try:
                rating = float(str(raw).replace(",", "."))
            except (TypeError, ValueError):
                rating = None

        if review_count is None:
            for key in ("reviews", "totalReviews", "reviewCount"):
                val = prod.get(key)
                if isinstance(val, (int, float)):
                    review_count = int(val)
                    break
                if isinstance(val, list):
                    review_count = len(val)
                    break

        # Sanity: escala VTEX é 0-5; fora disso é lixo de payload
        if rating is not None and not 0 <= rating <= 5:
            rating = None
        if review_count is not None and review_count < 0:
            review_count = None
        return rating, review_count

    def _parse_api_products(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
        products: Optional[List[Dict]] = None,
    ) -> List[Dict[str, Any]]:
        source = products if products is not None else self._captured_products
        records = []
        with_rating = 0
        for idx, prod in enumerate(source):
            title = prod.get("productName") or prod.get("name") or prod.get("title")

            sellers_info = self._extract_vtex_sellers(prod)
            price_float = sellers_info["price_float"]
            if price_float is None:
                # Fallback para o campo price simples (IS endpoint às vezes traz)
                try:
                    price_float = float(str(prod.get("price"))) if prod.get("price") else None
                except (ValueError, TypeError):
                    price_float = None

            rating, review_count = self._extract_vtex_rating(prod)
            if rating is not None:
                with_rating += 1

            pos = page_offset + idx + 1
            record = self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos,
                position_organic=pos,
                position_sponsored=None,
                price_float=price_float,
                # Display: na vitrine da Casas Bahia o anúncio é exibido pela
                # casa mesmo sem sellers[] no payload.
                seller=sellers_info["buy_box_seller"] or "Casas Bahia",
                buy_box_seller=sellers_info["buy_box_seller"],
                qtd_sellers=sellers_info["qtd_sellers"],
                tipo_seller=sellers_info["tipo_seller"],
                is_fulfillment=False,
                rating=rating,
                review_count=review_count,
                tag_destaque=None,
            )
            if sellers_info["buy_box_seller"] is None:
                # _build_record cai para `seller` quando buy_box_seller=None;
                # aqui o dado é desconhecido (payload sem sellers[]) e não pode
                # virar vitória fantasma da casa no share of buy box.
                record["Buy Box Seller"] = None
            records.append(record)
        if source:
            logger.debug(
                f"[{self.platform_name}] {with_rating}/{len(source)} produtos "
                "com rating no payload VTEX IS"
            )
        return records

    # ------------------------------------------------------------------
    # JSON embutido no HTML (SSR) — a página renderiza os cards do lado do
    # servidor; o dado completo (incl. sellers[]) pode estar num <script>
    # em vez de vir por uma chamada de API separada que o browser dispare.
    # ------------------------------------------------------------------

    @staticmethod
    def _is_vtex_product_list(data: Any) -> bool:
        """True se ``data`` é uma lista não-vazia de dicts no shape VTEX
        (``sellers`` no item, ou dentro de ``items[]``)."""
        return bool(
            isinstance(data, list) and data and isinstance(data[0], dict)
            and any(
                "sellers" in p
                or (p.get("items") and isinstance(p["items"], list) and p["items"]
                    and isinstance(p["items"][0], dict) and "sellers" in p["items"][0])
                for p in data[:3] if isinstance(p, dict)
            )
        )

    @classmethod
    def _collect_vtex_product_lists(
        cls, data: Any, out: List[List[Dict]], _depth: int = 0
    ) -> None:
        """
        Varredura recursiva que ACUMULA (não para no primeiro achado) todo
        array no shape VTEX encontrado na árvore JSON — cobre tanto REST puro
        quanto payloads aninhados de SSR/GraphQL. Uma página SSR real costuma
        ter VÁRIOS arrays desse shape (grade de busca, carrossel de
        recomendados, "vistos recentemente"…): parar no primeiro devolveria
        produtos ERRADOS pra keyword atual se um widget menor aparecesse antes
        na árvore. `_find_vtex_product_list` decide depois qual usar.
        """
        if _depth > 8:
            return
        if isinstance(data, list):
            if cls._is_vtex_product_list(data):
                out.append(data)
                return  # não desce dentro de uma lista já reconhecida
            for item in data:
                cls._collect_vtex_product_lists(item, out, _depth + 1)
            return
        if isinstance(data, dict):
            for v in data.values():
                cls._collect_vtex_product_lists(v, out, _depth + 1)

    @classmethod
    def _find_vtex_product_list(cls, data: Any) -> Optional[List[Dict]]:
        """
        Encontra o array de produtos VTEX mais provável de ser a grade de
        busca (não um widget de recomendação) em qualquer árvore JSON.

        Entre todos os arrays no shape VTEX encontrados, prefere o MAIOR —
        a grade de busca tem tipicamente muito mais itens (até
        ``_ITEMS_PER_PAGE``) que carrosséis de recomendação/cross-sell
        (tipicamente poucos itens), então o tamanho é um proxy razoável sem
        precisar decodificar a estrutura exata do payload de cada site.

        Args:
            data: árvore JSON (dict/list/escalar) já parseada.

        Returns:
            A maior lista de produtos reconhecida, ou None se nenhuma.
        """
        candidates: List[List[Dict]] = []
        cls._collect_vtex_product_lists(data, candidates)
        return max(candidates, key=len) if candidates else None

    @staticmethod
    def _iter_balanced_spans(text: str):
        """Gera os spans (start, end) de cada bloco ``{...}``/``[...]``
        BALANCEADO de nível superior em ``text`` (ciente de strings, pra não
        contar chave/colchete dentro de literais). Não desce em blocos
        aninhados — cada span devolvido é o maior bloco a partir daquele
        ponto; quem quiser os aninhados chama de novo sobre o interior."""
        n = len(text)
        i = 0
        while i < n:
            ch = text[i]
            if ch not in "{[":
                i += 1
                continue
            close_ch = "}" if ch == "{" else "]"
            depth = 0
            in_str = False
            str_quote = ""
            escape = False
            j = i
            end = None
            while j < n:
                c = text[j]
                if in_str:
                    if escape:
                        escape = False
                    elif c == "\\":
                        escape = True
                    elif c == str_quote:
                        in_str = False
                elif c in ('"', "'"):
                    in_str = True
                    str_quote = c
                elif c == ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
                j += 1
            if end is not None:
                yield i, end
                i = end + 1
            else:
                # Sem fechamento balanceado (JS não-JSON) — pula pro próximo
                # candidato depois deste ponto, sem re-varrer o que já viu.
                i = j + 1

    @classmethod
    def _extract_json_blobs(cls, text: str, _depth: int = 0) -> List[Any]:
        """
        Extrai e faz parse de TODOS os blobs JSON válidos de um texto de
        ``<script>`` — incluindo os que estão ANINHADOS dentro de um wrapper
        JS que não é JSON válido por si só (IIFE, try/catch, atribuição
        condicional: ``(function(){ var d = {...}; window.X = d; })();``).

        Para cada bloco ``{...}``/``[...]`` balanceado de nível superior:
        tenta o bloco INTEIRO como JSON primeiro. Se parsear, usa — e NÃO
        desce nele (o payload já é autocontido, descer só re-processaria à
        toa). Se falhar (é JS de verdade, não JSON puro), recursa no
        INTERIOR do bloco atrás de blobs JSON menores lá dentro — sem isso,
        um JSON válido só existente DENTRO de um wrapper JS nunca seria
        encontrado (o bloco externo falha o parse e era descartado por
        inteiro, junto com o que houvesse dentro dele).

        Também cobre, por conta do scan de blocos balanceados em vez de
        "primeiro { até o fim do texto": JSON com raiz em array
        (``[{...}, {...}]``) e múltiplas atribuições no mesmo script
        (``window.__A__={...}; window.__B__={...};``).

        Args:
            text: conteúdo do ``<script>``.
            _depth: profundidade de recursão atual — corta em wrappers
                aninhados demais (scripts hostis/minificados).

        Returns:
            Lista de payloads JSON já parseados (dict/list), na ordem em que
            foram encontrados.
        """
        if _depth > 4:
            return []
        blobs: List[Any] = []
        for start, end in cls._iter_balanced_spans(text):
            span = text[start:end + 1]
            try:
                blobs.append(json.loads(span))
            except json.JSONDecodeError:
                # Bloco não é JSON puro (provável JS) — o payload real pode
                # estar aninhado mais fundo dentro dele; sem os delimitadores
                # externos pra não re-encontrar o mesmo span de novo.
                blobs.extend(cls._extract_json_blobs(span[1:-1], _depth + 1))
        return blobs

    def _extract_embedded_products(self, html: str) -> Optional[List[Dict]]:
        """
        Varre os ``<script>`` da página por um blob JSON com produtos VTEX
        (``__NEXT_DATA__``, estado do Nuxt/Redux, ou qualquer outro nome —
        não fixamos o id do script, só reconhecemos o SHAPE do produto).

        Zero requisições extras: opera sobre o HTML que o caller já baixou
        (o mesmo usado no fallback de DOM), então não arrisca bloqueio novo.
        Acumula candidatos de TODOS os scripts (não para no primeiro achado)
        e escolhe o maior no fim — mesma lógica anti-carrossel-errado do
        ``_find_vtex_product_list``, agora entre scripts também.

        Returns:
            A maior lista de produtos VTEX reconhecida na página, ou None.
        """
        soup = BeautifulSoup(html, "html.parser")
        all_candidates: List[List[Dict]] = []
        for script in soup.find_all("script"):
            text = script.string or script.get_text() or ""
            text = text.strip()
            # < 200: script trivial, não vale a pena tentar. > 3MB: bundle JS
            # minificado, não payload de dados — pular evita scan caro à toa.
            if len(text) < 200 or len(text) > 3_000_000:
                continue
            for payload in self._extract_json_blobs(text):
                self._collect_vtex_product_lists(payload, all_candidates)
        return max(all_candidates, key=len) if all_candidates else None

    # ------------------------------------------------------------------
    # DOM parse
    # ------------------------------------------------------------------

    @staticmethod
    def _first_match(tag: Tag, candidates) -> Optional[Tag]:
        if isinstance(candidates, str):
            return tag.select_one(candidates)
        for sel in candidates:
            el = tag.select_one(sel)
            if el:
                return el
        return None

    @staticmethod
    def _detect_items(soup: BeautifulSoup) -> tuple[List[Tag], str]:
        for sel in _SELECTORS["item_candidates"]:
            items = soup.select(sel)
            if len(items) >= 2:
                return items, sel
        return [], "nenhum"

    def _parse_dom(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        items, sel_used = self._detect_items(soup)
        logger.info(
            f"[{self.platform_name}] {len(items)} itens (seletor: {sel_used})"
        )

        if not items:
            self._dump_debug(html, page, keyword)
            return []

        records = []
        sponsored_counter = 0
        organic_counter   = 0

        for idx, item in enumerate(items):
            sponsored = bool(item.select_one(_SELECTORS["sponsored"]))
            pos_general = page_offset + idx + 1

            if sponsored:
                sponsored_counter += 1
                pos_organic, pos_sponsored = None, sponsored_counter
            else:
                organic_counter += 1
                pos_organic, pos_sponsored = organic_counter, None

            title_el = self._first_match(item, _SELECTORS["title_candidates"])
            price_el = self._first_match(item, _SELECTORS["price_candidates"])

            # Fallback de título por img[alt]
            title = title_el.get_text(strip=True) if title_el else None
            if not title:
                img = item.select_one("img[alt]")
                if img:
                    title = img.get("alt", "").strip() or None

            seller_el = item.select_one(_SELECTORS["seller"])
            seller    = seller_el.get_text(strip=True) if seller_el else "Casas Bahia"

            rating_el    = item.select_one(_SELECTORS["rating"])
            reviews_el   = item.select_one(_SELECTORS["review_count"])
            tag_el       = item.select_one(_SELECTORS["tag_destaque"])

            record = self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_raw=price_el.get_text(strip=True) if price_el else None,
                seller=seller,
                is_fulfillment=False,
                rating=parse_rating(rating_el.get_text() if rating_el else None),
                review_count=parse_review_count(reviews_el.get_text() if reviews_el else None),
                tag_destaque=tag_el.get_text(strip=True) if tag_el else None,
            )
            # DOM não expõe o array sellers[]: não sabemos quem vence a buy box
            # nem 1P/3P. Não marcar "Casas Bahia" como vencedor (vitória fantasma
            # da casa no share of buy box) — deixa o campo honestamente vazio,
            # igual ao _parse_api_products quando falta sellers[].
            record["Buy Box Seller"] = None
            records.append(record)

        return records

    # ------------------------------------------------------------------
    # Debug dump — obrigatório quando 0 itens
    # ------------------------------------------------------------------

    def _dump_debug(self, html: str, page: int, keyword: str) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"casasbahia_debug_p{page}_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo para diagnóstico: {path}"
            )
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Espera
    # ------------------------------------------------------------------

    def _wait_for_products(self, timeout_ms: int = 12_000) -> bool:
        for sel in _SELECTORS["item_candidates"][:6]:
            try:
                self._page.wait_for_selector(sel, timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _warmup_cdp_session(self) -> bool:
        """
        Aquece a home no Chrome CDP e espera o Akamai validar o _abck.

        Mesma lição do Magalu: um goto direto pra /busca chega ao Akamai antes
        do sensor.js validar o _abck → bloqueio (visto na coleta mesmo via
        Chrome real). Visitar a home, emular interação humana e esperar o
        _abck virar "~0~" promove o cookie pras rotas de busca/API.

        Idempotente: roda só na 1ª keyword (cacheia em self._cdp_warmed).
        """
        if self._cdp_warmed or not self._real_browser_active or self._page is None:
            return self._cdp_warmed

        try:
            self._page.goto(_VTEX_BASE, wait_until="domcontentloaded", timeout=40_000)
            # Akamai pontua mouse/scroll — emula humano antes de esperar o _abck
            try:
                for _ in range(3):
                    self._page.mouse.move(
                        random.randint(150, 1200), random.randint(150, 700)
                    )
                    time.sleep(random.uniform(0.3, 0.7))
                self._page.mouse.wheel(0, 500)
                time.sleep(random.uniform(0.8, 1.5))
            except Exception:
                pass

            # Espera o _abck validar (sensor.js POSTa fingerprint → Akamai aprova)
            try:
                self._page.wait_for_function(
                    """() => {
                        const m = document.cookie.match(/_abck=([^;]+)/);
                        return m && /~0~/.test(decodeURIComponent(m[1]));
                    }""",
                    timeout=20_000,
                )
                logger.info(
                    f"[{self.platform_name}] _abck validado pelo sensor.js ✓ "
                    "(warm-up CDP)"
                )
            except Exception:
                logger.warning(
                    f"[{self.platform_name}] _abck não validou em 20s — "
                    "a busca pode ainda ser bloqueada"
                )
            self._cdp_warmed = True
            return True
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] Warm-up CDP falhou: {exc}")
            return False

    def _vtex_fetch_in_page(self, keyword: str, page: int) -> Optional[List[Dict]]:
        """
        Chama as APIs VTEX DE DENTRO da página (same-origin) e devolve o array
        de produtos COM ``items[].sellers[]`` — a fonte do vencedor da buy box
        e do split 1P/3P.

        Depois do warm-up, o fetch carrega o _abck validado + o fingerprint do
        Chrome real, então o Akamai libera o JSON (o que o curl_cffi de IP
        datacenter não conseguia). Tenta, em ordem:
          1. catalog_system (``/api/catalog_system/pub/products/search``) —
             a fonte CANÔNICA de ``sellers[]`` na VTEX; retorna um array direto.
          2. intelligent-search — fallback (algumas contas não expõem o catalog).

        Loga um diagnóstico por endpoint (status→nº de produtos) quando nenhum
        retorna, para revelar exatamente o que a Casas Bahia responde.

        Returns:
            Lista de produtos VTEX (com ``items[].sellers[]``), ou None.
        """
        if self._page is None:
            return None
        count = _ITEMS_PER_PAGE
        frm = (page - 1) * count
        to = frm + count - 1
        kw = quote_plus(keyword)
        # Do mais rico (sellers[] completo) ao menos rico.
        candidates = [
            f"/api/catalog_system/pub/products/search?ft={kw}&_from={frm}&_to={to}",
            f"/api/catalog_system/pub/products/search/{kw}?_from={frm}&_to={to}",
            f"/_v/api/intelligent-search/product_search/pt/pt-BR/search"
            f"?query={kw}&page={page}&count={count}&sort=score_desc"
            f"&hideUnavailableItems=false",
        ]
        try:
            result = self._page.evaluate(
                """async (urls) => {
                    async function one(u) {
                        try {
                            const r = await fetch(u, {
                                headers: {'accept': 'application/json'},
                                credentials: 'include',
                            });
                            const status = r.status;
                            const ct = r.headers.get('content-type') || '';
                            if (!r.ok || ct.indexOf('json') === -1)
                                return {u, status, n: 0, products: null};
                            const j = await r.json();
                            const prods = Array.isArray(j) ? j
                                : (j.products
                                   || (j.productSearch && j.productSearch.products)
                                   || []);
                            return {u, status, n: prods.length, products: prods};
                        } catch (e) {
                            return {u, status: -1, n: 0, products: null, error: String(e)};
                        }
                    }
                    const tried = [];
                    for (const u of urls) {
                        const res = await one(u);
                        tried.push({u: res.u, status: res.status, n: res.n});
                        if (res.products && res.products.length)
                            return {ok: true, endpoint: res.u, products: res.products, tried};
                    }
                    return {ok: false, products: null, tried};
                }""",
                candidates,
            )
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] in-page VTEX fetch erro: {exc}")
            return None

        if not result:
            return None
        if result.get("ok") and result.get("products"):
            ep = (result.get("endpoint") or "?").split("?")[0].split("/api/")[-1]
            logger.info(
                f"[{self.platform_name}] VTEX in-page OK via …/{ep[:40]} — "
                f"{len(result['products'])} produtos c/ sellers[] (pág {page})"
            )
            return result["products"]
        tried = result.get("tried") or []
        diag = " | ".join(
            f"{(t.get('u') or '').split('?')[0].split('/api/')[-1][:28]}:"
            f"{t.get('status')}→{t.get('n')}"
            for t in tried
        )
        logger.warning(
            f"[{self.platform_name}] in-page VTEX sem produtos (pág {page}) — "
            f"[endpoint:status→n] {diag}"
        )
        return None

    def _find_search_input(self) -> Optional[Any]:
        """Retorna o ElementHandle do campo de busca visível, ou None."""
        if not self._page:
            return None
        for sel in _SEARCH_INPUT_SELECTORS:
            try:
                el = self._page.query_selector(sel)
                if el and el.is_visible():
                    return el
            except Exception:
                continue
        return None

    def _organic_search(self, keyword: str) -> bool:
        """
        Busca como humano: localiza o campo de busca, digita a keyword com
        cadência realista e tecla Enter — gerando uma navegação same-origin
        (com Referer) para ``/busca``, bem menos suspeita que o goto cru.

        Se a página atual não tiver campo de busca (ex: veio de um /busca
        anterior), volta pra home primeiro. Retorna True se caiu numa SERP
        ``/busca``; False (o caller cai no goto) se não achou o campo/falhou.
        """
        page = self._page
        if page is None:
            return False

        inp = self._find_search_input()
        if inp is None:
            try:
                page.goto(_VTEX_BASE, wait_until="domcontentloaded", timeout=30_000)
                time.sleep(random.uniform(1.0, 2.0))
            except Exception as exc:
                logger.debug(f"[{self.platform_name}] Organic: home goto falhou: {exc}")
                return False
            inp = self._find_search_input()
        if inp is None:
            return False

        try:
            inp.click()
            time.sleep(random.uniform(0.3, 0.7))
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(keyword, delay=random.randint(60, 140))
            time.sleep(random.uniform(0.4, 0.9))
            prev_url = page.url
            page.keyboard.press("Enter")
            # Espera a URL MUDAR para uma /busca — não basta "estar em /busca".
            # Nas keywords 2+ o browser JÁ parte da SERP anterior, então um
            # wait_for_url("**/busca**") casaria de imediato e a gente leria os
            # resultados da keyword ANTERIOR (contaminação cross-keyword). Exigir
            # que o href mude garante que a navegação da busca nova completou.
            page.wait_for_function(
                "prev => location.href !== prev && location.href.includes('/busca')",
                arg=prev_url,
                timeout=20_000,
            )
            logger.info(
                f"[{self.platform_name}] '{keyword}' carregada via busca orgânica ✓"
            )
            return True
        except Exception as exc:
            logger.debug(
                f"[{self.platform_name}] Busca orgânica de '{keyword}' falhou: {exc}"
            )
            return False

    def _browser_search_page(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        offset: int,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Estratégia browser pra UMA página: busca orgânica (ou goto) + XHR
        interception + JSON embutido (SSR) + DOM parse.

        Returns:
            Lista de registros (pode ser vazia), ou None quando o Akamai
            bloqueou / o goto falhou — sinal pro caller parar a keyword.
        """
        url = self._build_url(keyword, page)

        # Sessão manual: só no browser PRÓPRIO. Num Chrome real (CDP ou local)
        # os cookies já estão vivos — injetar os salvos (mais antigos) degrada.
        if page == 1 and not self._real_browser_active:
            try:
                from utils.session_grabber import apply_session_to_context
                if apply_session_to_context("casasbahia", self._context):
                    logger.info(
                        f"[{self.platform_name}] Sessão manual aplicada "
                        "(session_grabber) — pode bypass Akamai"
                    )
                else:
                    logger.warning(
                        f"[{self.platform_name}] Sem sessão manual. "
                        "Execute: python utils/session_grabber.py --site casasbahia"
                    )
            except Exception:
                pass

        # Revive: no CDP o usuário pode fechar a aba/janela no meio da coleta
        if self._page is None or self._page.is_closed():
            try:
                self._page = self._context.new_page()
                self._page.set_default_timeout(PAGE_TIMEOUT)
                self._setup_xhr_intercept()
                logger.warning(
                    f"[{self.platform_name}] Aba de coleta foi fechada — "
                    "nova aba criada"
                )
            except Exception as exc:
                logger.warning(
                    f"[{self.platform_name}] Browser indisponível: {exc}"
                )
                return None

        # Navegação: na 1ª página, tenta busca ORGÂNICA (digitar no campo +
        # Enter) — reduz o sinal de bot do goto direto a /busca, que é o que
        # dispara o customdeny do Akamai (confirmado no dump: página
        # novavp-a.akamaihd.net "Ops! Algo deu errado"). Se o campo não for
        # achado, cai no goto normal (que ainda funciona quando o perfil não
        # está flagado). Páginas > 1 usam goto same-origin (já tem Referer).
        navigated = False
        if page == 1:
            navigated = self._organic_search(keyword)
        if not navigated:
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            except Exception as exc:
                logger.warning(f"[{self.platform_name}] Timeout no goto: {exc}")
                return None

        self._wait_for_products(timeout_ms=4_000)

        # Fail-fast: checa bloqueio ANTES dos waits caros (network idle +
        # delay + scroll somavam ~20s extras por keyword já bloqueada).
        html = self._page.content()
        if self._check_blocked(html):
            self._dump_debug(html, page, keyword)
            self._akamai_blocked = True
            return None

        self._wait_for_network_idle()
        self._random_delay(min_s=4.0, max_s=9.0)
        self._human_scroll(steps=10, step_px=300)
        time.sleep(1.5)

        html = self._page.content()
        if self._check_blocked(html):
            self._dump_debug(html, page, keyword)
            self._akamai_blocked = True
            return None

        # XHR capturado tem prioridade (payload VTEX completo com sellers[])
        if self._captured_products:
            logger.info(
                f"[{self.platform_name}] {len(self._captured_products)} produtos via XHR"
            )
            return self._parse_api_products(keyword, keyword_category_map, offset)

        # JSON embutido no HTML (SSR) — mesmo custo de rede do fallback DOM
        # (já baixamos esse HTML), mas com sellers[] completo quando presente.
        embedded = self._extract_embedded_products(html)
        if embedded:
            logger.info(
                f"[{self.platform_name}] {len(embedded)} produtos via JSON "
                "embutido no HTML (SSR)"
            )
            return self._parse_api_products(
                keyword, keyword_category_map, offset, products=embedded
            )

        return self._parse_dom(html, keyword, keyword_category_map, page, offset)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=6, max=20),
        reraise=False,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Busca um termo na Casas Bahia via API VTEX intelligent-search.

        Usa sessão curl_cffi com warm-up de cookies Akamai e intercepta o XHR
        da busca; do array ``sellers[]`` extrai o vencedor da buy box
        (``sellerDefault``), o nº de sellers e o tipo (1P/3P).

        No modo CDP (RAC_CDP_URL setado), o browser real vem PRIMEIRO — é o
        fingerprint que o Akamai já aceita; as APIs VTEX viram fallback.

        Args:
            keyword: termo de busca.
            keyword_category_map: mapa keyword → categoria (para o registro).
            page_limit: nº máximo de páginas a coletar.

        Returns:
            Lista de registros normalizados (um por oferta).
        """
        # Circuit breaker — coleta já abortada por bloqueios consecutivos:
        # pula a keyword sem gastar ~40s de navegação garantidamente bloqueada.
        if self._collection_aborted:
            logger.debug(
                f"[{self.platform_name}] Coleta abortada (circuit breaker) — "
                f"pulando '{keyword}'"
            )
            return []

        all_records: List[Dict[str, Any]] = []
        self._setup_xhr_intercept()
        self._akamai_blocked = False

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")
            self._captured_products = []
            offset = (page - 1) * _ITEMS_PER_PAGE
            records: List[Dict[str, Any]] = []
            browser_records: Optional[List[Dict[str, Any]]] = None

            if self._real_browser_active:
                # --- Chrome real (CDP ou local): fingerprint aceito ---
                # 1) Warm-up 1x: home + sensor.js valida o _abck (mesma lição
                #    do Magalu — sem isso o /busca chega ao Akamai sem o cookie
                #    validado e é bloqueado, como nos logs de 12/Jun).
                if page == 1:
                    self._warmup_cdp_session()

                # 2) Navega a busca DENTRO do browser (organicamente: digita no
                #    campo) e extrai via XHR → JSON embutido no HTML (SSR,
                #    c/ sellers[]) → DOM. Vem PRIMEIRO: disparar as APIs VTEX do
                #    catalog a partir da home ANTES de ver resultados é sinal de
                #    bot (e, na CB, elas voltam 403/vazio de qualquer jeito).
                browser_records = self._browser_search_page(
                    keyword, keyword_category_map, page, offset
                )
                if browser_records is None:
                    break
                records = browser_records

                # 3) Fallback rico: API VTEX via fetch same-origin (raramente
                #    responde na CB, mas mantém a chance quando o catalog libera).
                if not records:
                    products = self._vtex_fetch_in_page(keyword, page)
                    if products:
                        records = self._parse_api_products(
                            keyword, keyword_category_map, offset, products=products
                        )

                # 4) Último recurso: curl_cffi (provável bloqueio, mas tenta)
                if not records:
                    records = self._vtex_cffi_search(
                        keyword, keyword_category_map, page, offset
                    ) or self._vtex_api_search(
                        keyword, keyword_category_map, page, offset
                    )
            else:
                # --- Estratégia 0: VTEX API via curl_cffi (TLS Chrome real) ---
                records = self._vtex_cffi_search(
                    keyword, keyword_category_map, page, offset
                )

                # --- Estratégia 1: VTEX API via requests (fallback) ---
                if not records:
                    records = self._vtex_api_search(
                        keyword, keyword_category_map, page, offset
                    )

                # --- Estratégias 2/3: Browser + sessão salva + XHR + DOM ---
                if not records:
                    browser_records = self._browser_search_page(
                        keyword, keyword_category_map, page, offset
                    )
                    if browser_records is None:
                        break
                    records = browser_records

            all_records.extend(records)

            if not records:
                logger.warning(
                    f"[{self.platform_name}] Página {page} retornou 0 itens. Parando."
                )
                break

            if page < page_limit:
                self._random_delay()

        # Circuit breaker — N keywords seguidas bloqueadas pelo Akamai = IP/
        # fingerprint rejeitados de forma persistente; aborta o restante.
        if all_records:
            self._blocked_keyword_streak = 0
        elif self._akamai_blocked:
            self._blocked_keyword_streak += 1
            if (
                self._blocked_keyword_streak >= _ABORT_AFTER_BLOCKED_KEYWORDS
                and not self._collection_aborted
            ):
                self._collection_aborted = True
                logger.error(
                    f"[{self.platform_name}] Circuit breaker: "
                    f"{self._blocked_keyword_streak} keywords seguidas bloqueadas "
                    "pelo Akamai — abortando a coleta Casas Bahia (keywords "
                    "restantes serão puladas). Caminhos: no notebook use "
                    "RAC_LOCAL_CHROME=1 (Chrome real residencial) ou proxy "
                    "residencial BR na VM."
                )

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
