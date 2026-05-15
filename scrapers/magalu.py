"""
scrapers/magalu.py — Scraper da Magalu (magazineluiza.com.br).

Estratégia (em ordem de prioridade):
  0. Next.js __NEXT_DATA__ via curl_cffi `_next/data/{BUILD_ID}/busca/{slug}.json`
     — JSON puro, mesma payload que o site monta em runtime.
  1. HTML mobile via curl_cffi (`m.magazineluiza.com.br/busca/...`) + extração
     de `__NEXT_DATA__` embutido no HTML — fallback para BUILD_ID mudado.
  2. HTML desktop via curl_cffi (`www.magazineluiza.com.br/busca/...`) — mesmo
     parser, domínio diferente.

Proteção: Akamai Bot Manager (substituiu Radware em Mai/2026).
  Akamai detecta no nível TLS (JA3/JA4). Puppeteer-stealth e Playwright NÃO
  bypassam isso — o Chromium tem fingerprint TLS diferente do Chrome real.
  Solução: `curl_cffi` com `impersonate="chrome124"` replica o handshake TLS
  exato do Chrome real, mesma técnica já validada em Casas Bahia.

Sem browser: zero overhead de Playwright, zero detecção JS sensor, ~5-10×
mais rápido que Puppeteer.
"""

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None  # type: ignore[assignment]
    _HAS_CURL_CFFI = False

from config import MAX_PAGES, LOGS_DIR
from scrapers.base import BaseScraper
from utils.text import parse_price


_ITEMS_PER_PAGE = 60  # Magalu mobile retorna ~60 itens por página

# Domínios — mobile costuma ter Akamai mais leniente que desktop
_MAGALU_MOBILE_HOME = "https://m.magazineluiza.com.br/"
_MAGALU_MOBILE_BASE = "https://m.magazineluiza.com.br"
_MAGALU_DESKTOP_HOME = "https://www.magazineluiza.com.br/"
_MAGALU_DESKTOP_BASE = "https://www.magazineluiza.com.br"

# Tempo entre requests para não disparar rate limit (segundos)
_INTER_REQUEST_DELAY = (3.0, 7.0)
_INTER_PAGE_DELAY = (5.0, 9.0)
_API_TIMEOUT = 20

# Impersonations rotacionadas — todos baseados em Chrome real, mas versões
# diferentes geram JA3 fingerprints distintos (Akamai monitora repetição).
_IMPERSONATIONS = ["chrome124", "chrome120", "chrome131", "chrome119"]

# User-agents alinhados com cada impersonate (mobile Android — site detecta
# UA-CH `Sec-CH-UA-Mobile` e prefere mobile no m.magazineluiza)
_MOBILE_UA_BY_CHROME = {
    "chrome124": "Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "chrome120": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "chrome131": "Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "chrome119": "Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
}
_DESKTOP_UA_BY_CHROME = {
    "chrome124": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "chrome120": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "chrome131": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "chrome119": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
}

# Indicadores de bloqueio Akamai/WAF — fail fast quando detectados
_AKAMAI_BLOCK_PATTERNS = (
    "Pardon Our Interruption",
    "Reference&#32;",
    "Reference #",
    "errors.edgesuite.net",
    "Access Denied",
    "Não é possível acessar a página",
    "akamaihd.net/akam",
    "ak_bmsc",
)


class MagaluScraper(BaseScraper):
    """
    Scraper Magalu via API JSON (Next.js _next/data) + curl_cffi.

    Não usa Playwright/Puppeteer — bate direto nos endpoints JSON com TLS
    fingerprint do Chrome real (impersonate). Akamai não consegue distinguir
    do browser legítimo.
    """

    platform_name = "Magalu"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._cffi_session = None
        self._impersonate = random.choice(_IMPERSONATIONS)
        self._build_id: Optional[str] = None
        self._home_used: str = _MAGALU_MOBILE_HOME

    # ------------------------------------------------------------------
    # Override ciclo de vida — NÃO inicia Playwright (causa do bloqueio Akamai)
    # ------------------------------------------------------------------

    def __enter__(self) -> "MagaluScraper":
        if not _HAS_CURL_CFFI:
            raise RuntimeError(
                "curl_cffi não instalado. Execute: pip install curl-cffi>=0.6.0"
            )
        self._cffi_session = cffi_requests.Session()
        logger.info(
            f"[{self.platform_name}] Sessão curl_cffi iniciada "
            f"(impersonate={self._impersonate}) | sem browser"
        )
        return self

    def __exit__(self, *_) -> None:
        try:
            if self._cffi_session is not None:
                self._cffi_session.close()
        except Exception:
            pass
        self._cffi_session = None

    # ------------------------------------------------------------------
    # Headers — mimetiza navegação real do Chrome com Client Hints
    # ------------------------------------------------------------------

    def _mobile_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        ua = _MOBILE_UA_BY_CHROME.get(self._impersonate, _MOBILE_UA_BY_CHROME["chrome124"])
        major = self._impersonate.replace("chrome", "")
        h = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Cache-Control": "max-age=0",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
            "sec-ch-ua": f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
        }
        if referer:
            h["Referer"] = referer
        return h

    def _next_data_headers(self, referer: str) -> Dict[str, str]:
        """Headers específicos para chamadas /_next/data — XHR-like."""
        ua = _MOBILE_UA_BY_CHROME.get(self._impersonate, _MOBILE_UA_BY_CHROME["chrome124"])
        major = self._impersonate.replace("chrome", "")
        return {
            "User-Agent": ua,
            "Accept": "*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Referer": referer,
            "x-nextjs-data": "1",
            "sec-ch-ua": f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    # ------------------------------------------------------------------
    # Detecção de bloqueio
    # ------------------------------------------------------------------

    def _is_blocked(self, status_code: int, text: str) -> bool:
        """True se a resposta indica bloqueio Akamai/WAF."""
        if status_code in (403, 429, 503):
            return True
        if len(text) < 1000:
            # Página normal tem >50 KB. Resposta minúscula = bloqueio ou erro
            # (Akamai retorna ~21 chars: "Reference #xxx").
            return True
        head = text[:8000]
        for sign in _AKAMAI_BLOCK_PATTERNS:
            if sign.lower() in head.lower():
                return True
        return False

    def _dump_block_html(self, html: str, label: str) -> None:
        """Salva HTML de página bloqueada para diagnóstico em logs/."""
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^a-z0-9_-]+", "_", label.lower())[:60]
            stamp = time.strftime("%Y%m%d_%H%M%S")
            path = log_dir / f"magalu_block_{safe}_{stamp}.html"
            path.write_text(html[:50_000], encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] HTML de bloqueio salvo: {path}"
            )
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] Falha ao salvar dump: {exc}")

    # ------------------------------------------------------------------
    # Warm-up + descoberta do BUILD_ID
    # ------------------------------------------------------------------

    def _fetch_home(self) -> Optional[str]:
        """
        Visita a home pra (a) Akamai emitir cookies de sessão fresca,
        (b) extrair BUILD_ID do Next.js. Tenta mobile primeiro, desktop como
        fallback. Retorna HTML da home se sucesso, None se bloqueado.
        """
        for home_url in (_MAGALU_MOBILE_HOME, _MAGALU_DESKTOP_HOME):
            try:
                logger.debug(
                    f"[{self.platform_name}] Warm-up: GET {home_url} "
                    f"(impersonate={self._impersonate})"
                )
                resp = self._cffi_session.get(  # type: ignore[union-attr]
                    home_url,
                    headers=self._mobile_headers(),
                    impersonate=self._impersonate,
                    timeout=_API_TIMEOUT,
                )
                if self._is_blocked(resp.status_code, resp.text):
                    logger.warning(
                        f"[{self.platform_name}] Warm-up bloqueado em {home_url} "
                        f"(HTTP {resp.status_code}, len={len(resp.text)})"
                    )
                    if len(resp.text) > 200:
                        self._dump_block_html(resp.text, f"home_{home_url[8:30]}")
                    continue

                self._home_used = home_url
                logger.info(
                    f"[{self.platform_name}] Warm-up OK em {home_url} "
                    f"({len(resp.text):,} bytes, cookies={len(self._cffi_session.cookies.jar)})"  # type: ignore[union-attr]
                )
                return resp.text
            except Exception as exc:
                logger.warning(
                    f"[{self.platform_name}] Erro no warm-up de {home_url}: {exc}"
                )
                continue

        logger.error(f"[{self.platform_name}] Warm-up falhou em mobile e desktop")
        return None

    def _extract_build_id(self, html: str) -> Optional[str]:
        """Lê __NEXT_DATA__ no HTML e retorna o buildId do Next.js."""
        match = re.search(
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(\{.+?\})</script>',
            html,
            re.DOTALL,
        )
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
            build_id = data.get("buildId")
            if isinstance(build_id, str) and build_id:
                return build_id
        except json.JSONDecodeError:
            pass
        return None

    def _ensure_build_id(self) -> bool:
        """Garante que self._build_id está populado. True se sucesso."""
        if self._build_id:
            return True
        home_html = self._fetch_home()
        if not home_html:
            return False
        self._build_id = self._extract_build_id(home_html)
        if self._build_id:
            logger.info(
                f"[{self.platform_name}] BUILD_ID descoberto: {self._build_id}"
            )
            return True
        logger.warning(
            f"[{self.platform_name}] __NEXT_DATA__ presente mas sem buildId — "
            "vai usar HTML scraping como fallback"
        )
        return False

    # ------------------------------------------------------------------
    # Estratégia 0: Next.js _next/data JSON endpoint
    # ------------------------------------------------------------------

    def _fetch_next_data(self, keyword: str, page: int) -> Optional[Dict]:
        """
        Chama o endpoint `/_next/data/{BUILD_ID}/busca/{slug}.json`.
        Retorna o JSON parseado ou None se falhar/bloqueado.

        Esse endpoint é o que o Next.js usa pra hydrar a página depois da
        navegação client-side. Retorna JSON com `pageProps.searchResult` ou
        estrutura similar — bem mais limpo que o HTML completo.
        """
        if not self._build_id:
            return None

        slug = quote_plus(keyword.strip())
        base = _MAGALU_MOBILE_BASE if "m.magazineluiza" in self._home_used else _MAGALU_DESKTOP_BASE
        url = f"{base}/_next/data/{self._build_id}/busca/{slug}.json"
        params = {"q": keyword}
        if page > 1:
            params["page"] = str(page)

        referer = f"{base}/busca/{slug}/"
        try:
            resp = self._cffi_session.get(  # type: ignore[union-attr]
                url,
                headers=self._next_data_headers(referer),
                params=params,
                impersonate=self._impersonate,
                timeout=_API_TIMEOUT,
            )
            if resp.status_code == 404:
                # BUILD_ID invalidado por deploy — invalida e força redescoberta
                logger.warning(
                    f"[{self.platform_name}] _next/data 404 — BUILD_ID expirou "
                    f"({self._build_id}). Invalidando cache."
                )
                self._build_id = None
                return None
            if self._is_blocked(resp.status_code, resp.text):
                logger.warning(
                    f"[{self.platform_name}] _next/data bloqueado: HTTP "
                    f"{resp.status_code}, len={len(resp.text)}"
                )
                return None
            ct = resp.headers.get("content-type", "")
            if "application/json" not in ct:
                logger.debug(
                    f"[{self.platform_name}] _next/data CT inesperado: {ct[:60]}"
                )
                return None
            return resp.json()
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] _next/data erro: {exc}")
            return None

    # ------------------------------------------------------------------
    # Estratégia 1: HTML scraping com extração de __NEXT_DATA__
    # ------------------------------------------------------------------

    def _fetch_html_search(self, keyword: str, page: int) -> Optional[str]:
        """
        Baixa o HTML da página de busca. O JSON com produtos vem embutido
        no <script id="__NEXT_DATA__"> — mesma payload do _next/data.
        """
        slug = quote_plus(keyword.strip())
        base = _MAGALU_MOBILE_BASE if "m.magazineluiza" in self._home_used else _MAGALU_DESKTOP_BASE
        url = f"{base}/busca/{slug}/"
        params = {"page": str(page)} if page > 1 else {}
        try:
            resp = self._cffi_session.get(  # type: ignore[union-attr]
                url,
                headers=self._mobile_headers(referer=self._home_used),
                params=params,
                impersonate=self._impersonate,
                timeout=_API_TIMEOUT,
            )
            if self._is_blocked(resp.status_code, resp.text):
                logger.warning(
                    f"[{self.platform_name}] HTML search bloqueado: HTTP "
                    f"{resp.status_code}, len={len(resp.text)}"
                )
                if len(resp.text) > 200:
                    self._dump_block_html(resp.text, f"search_{keyword}_p{page}")
                return None
            return resp.text
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] HTML search erro: {exc}")
            return None

    @staticmethod
    def _extract_next_data_from_html(html: str) -> Optional[Dict]:
        """Extrai e parseia <script id='__NEXT_DATA__'>...</script>."""
        match = re.search(
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(\{.+?\})</script>',
            html,
            re.DOTALL,
        )
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.debug(f"Magalu: falha ao parsear __NEXT_DATA__: {exc}")
            return None

    # ------------------------------------------------------------------
    # Parser do JSON do Next.js — caminha a estrutura procurando produtos
    # ------------------------------------------------------------------

    def _find_products_in_json(self, data: Any) -> List[Dict]:
        """
        Caminha o JSON do Next.js procurando o array de produtos.
        A estrutura típica é `pageProps.searchResult.products` mas pode variar.
        Aceita qualquer array com objetos que tenham (id|productId) + (title|name).
        """
        # Caminhos preferenciais — testa primeiro
        preferred_paths = (
            ("props", "pageProps", "searchResult", "products"),
            ("props", "pageProps", "products"),
            ("pageProps", "searchResult", "products"),
            ("pageProps", "products"),
            ("searchResult", "products"),
            ("products",),
        )

        for path in preferred_paths:
            node: Any = data
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    node = None
                    break
                node = node[key]
            if isinstance(node, list) and node and self._looks_like_product(node[0]):
                return node

        # Fallback: walk recursivo procurando o primeiro array de produtos
        found: List[Dict] = []
        self._walk_find_product_array(data, found, depth=0)
        return found

    def _looks_like_product(self, obj: Any) -> bool:
        if not isinstance(obj, dict):
            return False
        has_id = any(k in obj for k in ("id", "productId", "sku", "variationId"))
        has_title = any(k in obj for k in ("title", "name", "productName"))
        return has_id and has_title

    def _walk_find_product_array(
        self, node: Any, found: List[Dict], depth: int
    ) -> None:
        if found or depth > 8:
            return
        if isinstance(node, list):
            if node and self._looks_like_product(node[0]):
                found.extend(item for item in node if isinstance(item, dict))
                return
            for item in node:
                self._walk_find_product_array(item, found, depth + 1)
                if found:
                    return
        elif isinstance(node, dict):
            for value in node.values():
                self._walk_find_product_array(value, found, depth + 1)
                if found:
                    return

    @staticmethod
    def _deep_get(obj: Any, *keys: str) -> Any:
        for k in keys:
            if not isinstance(obj, dict):
                return None
            obj = obj.get(k)
            if obj is None:
                return None
        return obj

    def _extract_price(self, product: Dict) -> Optional[float]:
        """
        Extrai preço de um produto Magalu. Estruturas típicas:
          - bestPrice: 1994.91 (number)
          - price: 1994.91 (number)
          - price: { value: 1994.91, currency: 'BRL' }
          - priceTemplate: '1.994,91' (string BR)
          - bestPriceTemplate: '1.994,91'
        """
        candidates: List[Any] = []
        for key in (
            "bestPrice", "currentPrice", "salesPrice", "finalPrice",
            "price", "promotionalPrice", "sellingPrice",
        ):
            if key in product:
                candidates.append(product[key])

        # Inline em objeto aninhado
        attrs = product.get("attributes") or product.get("priceTemplate")
        if attrs is not None:
            candidates.append(attrs)
        prices = product.get("prices")
        if isinstance(prices, dict):
            candidates.append(prices)

        for c in candidates:
            val = self._coerce_price(c)
            if val and val > 0:
                return val

        # Fallback: template string já formatado
        for key in ("bestPriceTemplate", "priceTemplate", "currentPriceTemplate"):
            tpl = product.get(key)
            if isinstance(tpl, str):
                parsed = parse_price(tpl)
                if parsed and parsed > 0:
                    return parsed
        return None

    def _coerce_price(self, val: Any, depth: int = 0) -> Optional[float]:
        if depth > 4:
            return None
        if isinstance(val, (int, float)):
            return float(val) if val > 0 else None
        if isinstance(val, str):
            return parse_price(val)
        if isinstance(val, dict):
            for key in (
                "bestPrice", "salesPrice", "value", "amount",
                "currentPrice", "price", "priceValue",
            ):
                if key in val:
                    result = self._coerce_price(val[key], depth + 1)
                    if result:
                        return result
        return None

    def _extract_seller(self, product: Dict) -> Optional[str]:
        """Extrai nome do seller. Magalu = 1P; marketplace = nome do parceiro."""
        # Tentativas em ordem de confiança
        for path in (
            ("seller", "description"),
            ("seller", "name"),
            ("sellerDescription",),
            ("seller",),
            ("partner", "name"),
        ):
            val = self._deep_get(product, *path)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None

    def _extract_url(self, product: Dict) -> Optional[str]:
        """Constrói a URL absoluta do produto."""
        path = product.get("path") or product.get("url") or product.get("href")
        if not isinstance(path, str):
            return None
        if path.startswith("http"):
            return path
        base = _MAGALU_MOBILE_BASE if "m.magazineluiza" in self._home_used else _MAGALU_DESKTOP_BASE
        return f"{base}{'' if path.startswith('/') else '/'}{path}"

    def _parse_products(
        self,
        products: List[Dict],
        keyword: str,
        keyword_category_map: dict,
        page: int,
    ) -> List[Dict[str, Any]]:
        """Converte produtos do JSON Magalu em registros padronizados."""
        records: List[Dict[str, Any]] = []
        offset = (page - 1) * _ITEMS_PER_PAGE
        organic_counter = 0
        sponsored_counter = 0

        for idx, prod in enumerate(products):
            title = (
                prod.get("title")
                or prod.get("name")
                or prod.get("productName")
                or self._deep_get(prod, "product", "title")
            )
            if not title:
                continue

            # Detecta patrocinado — campo varia, tentamos vários
            sponsored = bool(
                prod.get("sponsored")
                or prod.get("isSponsored")
                or prod.get("isAd")
                or self._deep_get(prod, "marketing", "sponsored")
            )

            pos_general = offset + idx + 1
            if sponsored:
                sponsored_counter += 1
                pos_organic, pos_sponsored = None, sponsored_counter
            else:
                organic_counter += 1
                pos_organic, pos_sponsored = organic_counter, None

            price = self._extract_price(prod)
            seller = self._extract_seller(prod) or "Magalu"

            rating_val = self._deep_get(prod, "rating", "score")
            if rating_val is None:
                rating_val = prod.get("rating")
            review_val = self._deep_get(prod, "rating", "count")
            if review_val is None:
                review_val = prod.get("reviewCount") or prod.get("reviewsCount")

            try:
                rating_f = float(rating_val) if rating_val is not None else None
            except (ValueError, TypeError):
                rating_f = None
            try:
                review_i = int(review_val) if review_val is not None else None
            except (ValueError, TypeError):
                review_i = None

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos_general,
                position_organic=pos_organic,
                position_sponsored=pos_sponsored,
                price_float=price,
                seller=seller,
                is_fulfillment=False,
                rating=rating_f,
                review_count=review_i,
                tag_destaque=prod.get("badge") or self._deep_get(prod, "label", "text"),
                url_produto=self._extract_url(prod),
            ))
        return records

    # ------------------------------------------------------------------
    # Helpers de delay
    # ------------------------------------------------------------------

    def _delay(self, min_s: float, max_s: float) -> None:
        time.sleep(random.uniform(min_s, max_s))

    # ------------------------------------------------------------------
    # Interface pública
    # ------------------------------------------------------------------

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
        """
        Busca produtos no Magalu para a keyword especificada.

        Estratégias (em cascata):
          1. Next.js _next/data JSON (mais rápido, payload limpo)
          2. HTML + __NEXT_DATA__ extraction (fallback se BUILD_ID inválido)

        Returns:
            Lista de registros prontos para o DataFrame.
        """
        if self._cffi_session is None:
            logger.error(
                f"[{self.platform_name}] Sessão curl_cffi não inicializada — "
                "use 'with MagaluScraper() as s:'"
            )
            return []

        all_records: List[Dict[str, Any]] = []

        # Warm-up + descoberta do BUILD_ID (uma vez por keyword — barato e
        # essencial pra ter cookies frescos do Akamai)
        if not self._ensure_build_id():
            logger.warning(
                f"[{self.platform_name}] '{keyword}' — sem BUILD_ID; "
                "tentando HTML scraping direto..."
            )

        self._delay(*_INTER_REQUEST_DELAY)

        for page in range(1, page_limit + 1):
            logger.info(
                f"[{self.platform_name}] '{keyword}' página {page}/{page_limit}"
            )
            records: List[Dict[str, Any]] = []

            # --- Estratégia 0: _next/data JSON ---
            if self._build_id:
                data = self._fetch_next_data(keyword, page)
                if data:
                    products = self._find_products_in_json(data)
                    if products:
                        records = self._parse_products(
                            products, keyword, keyword_category_map, page
                        )
                        logger.info(
                            f"[{self.platform_name}] {len(records)} produtos via _next/data"
                        )

            # --- Estratégia 1: HTML + __NEXT_DATA__ ---
            if not records:
                html = self._fetch_html_search(keyword, page)
                if html:
                    next_data = self._extract_next_data_from_html(html)
                    if next_data:
                        products = self._find_products_in_json(next_data)
                        if products:
                            records = self._parse_products(
                                products, keyword, keyword_category_map, page
                            )
                            logger.info(
                                f"[{self.platform_name}] {len(records)} produtos via "
                                "HTML+__NEXT_DATA__"
                            )
                            # BUILD_ID pode ter mudado — atualiza pro próximo page
                            new_build = self._extract_build_id(html)
                            if new_build and new_build != self._build_id:
                                logger.info(
                                    f"[{self.platform_name}] BUILD_ID atualizado: "
                                    f"{self._build_id} → {new_build}"
                                )
                                self._build_id = new_build

            all_records.extend(records)

            if not records:
                logger.warning(
                    f"[{self.platform_name}] Página {page} retornou 0 itens — "
                    "interrompendo keyword."
                )
                break

            if page < page_limit:
                self._delay(*_INTER_PAGE_DELAY)

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
