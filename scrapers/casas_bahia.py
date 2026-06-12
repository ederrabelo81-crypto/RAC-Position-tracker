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
        # Página com handler de XHR já registrado (evita handlers duplicados
        # acumulando entre keywords — cada search() chamava page.on de novo)
        self._xhr_page: Optional[Any] = None

        # Circuit breaker (ver _ABORT_AFTER_BLOCKED_KEYWORDS)
        self._akamai_blocked: bool = False       # bloqueio na keyword atual
        self._blocked_keyword_streak: int = 0
        self._collection_aborted: bool = False

    def _vtex_headers(self) -> Dict[str, str]:
        """Headers da API VTEX com o UA alinhado à sessão/plataforma."""
        headers = dict(_VTEX_HEADERS)
        headers["User-Agent"] = self._user_agent
        return headers

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
        sellerDefault (bool) e commertialOffer.Price/IsAvailable. O seller com
        sellerDefault=True é o vencedor da buy box; o total de sellers distintos
        com oferta disponível é a competição na listagem.

        Returns dict: buy_box_seller, qtd_sellers, tipo_seller, price_float.
        """
        buy_box_name: Optional[str] = None
        buy_box_id: Optional[str] = None
        buy_box_price: Optional[float] = None
        distinct_sellers: set = set()

        for item in (prod.get("items") or []):
            for seller in (item.get("sellers") or []):
                offer = seller.get("commertialOffer") or {}
                available = offer.get("IsAvailable", True)
                sid = seller.get("sellerId")
                sname = seller.get("sellerName")
                if available and (sid or sname):
                    distinct_sellers.add(str(sid or sname))
                # Buy box = sellerDefault; fallback para o primeiro disponível
                if seller.get("sellerDefault") or buy_box_name is None:
                    if available or buy_box_name is None:
                        buy_box_name = sname or buy_box_name
                        buy_box_id = sid or buy_box_id
                        price = offer.get("Price") or offer.get("ListPrice")
                        try:
                            buy_box_price = float(str(price)) if price else buy_box_price
                        except (ValueError, TypeError):
                            pass

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

            records.append(self._build_record(
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
            ))

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

    def _browser_search_page(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        offset: int,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Estratégia browser pra UMA página: goto + XHR interception + DOM parse.

        Returns:
            Lista de registros (pode ser vazia), ou None quando o Akamai
            bloqueou / o goto falhou — sinal pro caller parar a keyword.
        """
        url = self._build_url(keyword, page)

        # Sessão manual: só no browser PRÓPRIO. No modo CDP o Chrome real já
        # tem cookies vivos — injetar os salvos (mais antigos) degrada a sessão.
        if page == 1 and not self._cdp_active:
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

            if self._cdp_active:
                # --- CDP: Chrome real primeiro (Akamai aceita o fingerprint) ---
                browser_records = self._browser_search_page(
                    keyword, keyword_category_map, page, offset
                )
                if browser_records is None:
                    break
                records = browser_records
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
                    "restantes serão puladas). Caminhos: coleta via Chrome CDP "
                    "(scripts/start_chrome_cdp.bat + RAC_CDP_URL) ou proxy "
                    "residencial BR."
                )

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
