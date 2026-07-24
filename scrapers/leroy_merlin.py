"""
scrapers/leroy_merlin.py — Scraper da Leroy Merlin Brasil (leroymerlin.com.br).

Estratégia (em ordem de prioridade):
  1. Algolia API direta — Leroy expõe appId/apiKey/indexName no HTML da página.
     POST https://{appId}-dsn.algolia.net/1/indexes/{indexName}/query
     Resposta: {"hits": [...], "nbPages": N}
  2. Intercepção XHR da API Algolia (algolia.net) — captura chamadas do JS client.
  3. Parse DOM com cadeia de seletores fallback.
  4. Debug HTML dump em logs/ quando 0 itens.

Plataforma: Next.js + Algolia InstantSearch (client-side rendering).
Paginação: page param 0-indexed na Algolia API.

Seller / buy box (Jul/2026)
---------------------------
O índice Algolia só expõe o seller como ObjectId opaco em ``marketplaceSellers``
— não há nome em campo algum. A resolução acontece em camadas de custo zero
(mapa estático → cache em disco → campos inline do hit) e, para o que sobra,
abrindo **1 PDP por seller novo** (não por produto), com o resultado persistido
em ``data/leroy_sellers.json``. Ver `utils/leroy_sellers.py`.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES, LOGS_DIR
from scrapers.base import BaseScraper
from utils.leroy_sellers import (
    LeroySellerCache,
    extract_seller_from_pdp,
    is_leroy_self,
)
from utils.text import parse_price, parse_rating, parse_review_count, now_brt

_ITEMS_PER_PAGE = 24

# Mapeamento estático de IDs MongoDB (marketplaceSellers) → nomes de seller.
# IDs são estáveis. Serve como semente: o cache persistente
# (data/leroy_sellers.json) é populado automaticamente via PDP e tem prioridade
# de escrita, mas este mapa continua valendo para correções manuais.
LEROY_SELLER_ID_MAP: dict[str, str] = {
    "6353074400c2dc08ca5a2114": "Elgin",
}

# Sentinela usada quando nem o cache nem o PDP identificam o lojista.
UNRESOLVED_SELLER = "3P (não identificado)"

# --- Resolução via PDP (única fonte que expõe o nome do lojista) -------------
# O índice Algolia só traz o ObjectId do seller; o nome aparece apenas no PDP
# ("Vendido e entregue por X"). Como a chave é o *seller ID* e não o produto,
# basta 1 PDP por ID novo — dezenas por run na primeira coleta, ~zero depois,
# graças ao cache em disco.
_PDP_RESOLVE_ENABLED = os.environ.get("LEROY_PDP_RESOLVE", "1") not in ("0", "false", "False")
_PDP_MAX_PER_RUN = int(os.environ.get("LEROY_PDP_MAX_PER_RUN", "40") or 40)
_PDP_TIMEOUT = float(os.environ.get("LEROY_PDP_TIMEOUT", "12") or 12)

_PDP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

# Algolia credentials exposed in the Leroy Merlin page HTML
_ALGOLIA_APP_ID    = "1CF3ZT43ZU"
_ALGOLIA_API_KEY   = "28e054533dcdd3d71379fc3f38e78f1e"
_ALGOLIA_INDEX     = "production_products"
_ALGOLIA_SEARCH_URL = (
    f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net"
    f"/1/indexes/{_ALGOLIA_INDEX}/query"
)
_ALGOLIA_HEADERS = {
    "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
    "X-Algolia-API-Key": _ALGOLIA_API_KEY,
    "Content-Type": "application/json",
}

_SELECTORS = {
    "item_candidates": [
        # data-testid (Next.js padrão Leroy)
        '[data-testid="product-card"]',
        '[data-testid="product-item"]',
        '[data-testid="product"]',
        '[data-cy="product-card"]',
        # Leroy Merlin styled-components e design system
        'li[class*="ProductCard"]',
        'li[class*="product-card"]',
        'div[class*="ProductCard"]',
        'article[class*="product"]',
        '[class*="Card__Wrapper"]',
        '[class*="ProductList__Item"]',
        # Fallback estrutural
        'li > a[href*="/p/"]',
        'li > a[href*="/produto/"]',
        'ul[class*="product"] > li',
        'ul[class*="Product"] > li',
    ],
    "title_candidates": [
        '[data-testid="product-title"]',
        '[data-testid="product-name"]',
        '[class*="ProductTitle"]',
        '[class*="product-title"]',
        '[class*="ProductName"]',
        'h2[class*="sc-"]',
        'h3[class*="sc-"]',
        'h2', 'h3',
    ],
    "price_candidates": [
        '[data-testid="product-price"]',
        '[data-testid="price"]',
        '[class*="ProductPrice"]',
        '[class*="PriceTag"]',
        '[class*="product-price"]',
        '[class*="Price__Value"]',
        'span[class*="price"]',
    ],
    "rating_candidates": [
        '[data-testid="rating"]',
        '[class*="RatingStars"]',
        '[class*="rating-value"]',
        '[class*="Rating"]',
    ],
    "review_count_candidates": [
        '[data-testid="review-count"]',
        '[class*="ReviewCount"]',
        '[class*="rating-count"]',
    ],
    "tag_candidates": [
        '[data-testid="badge"]',
        '[class*="ProductBadge"]',
        '[class*="product-tag"]',
        '[class*="Badge"]',
        '[class*="Label"]',
    ],
    "bot_check": "#px-captcha, #challenge-form, [class*='bot-check']",
}

# Padrões de URL para XHR interception (Algolia + APIs internas)
_API_URL_PATTERNS = [
    "algolia.net",
    "algolia.io",
    "/api/v3/search",
    "/api/v3/products",
    "/api/catalog",
    "product-search",
]


class LeroyMerlinScraper(BaseScraper):
    """Scraper modular para Leroy Merlin Brasil."""

    platform_name = "Leroy Merlin"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._captured_products: List[Dict] = []
        self._dynamic_seller_cache: dict[str, str] = {}
        self._seller_warned: set[str] = set()  # IDs already warned — suppresses repeat WARNINGs
        # Cache persistente id → nome (data/leroy_sellers.json). Sobrevive entre
        # runs, então o custo de PDP é pago uma vez por seller novo.
        self._seller_cache = LeroySellerCache()
        self._pdp_session: Optional[requests.Session] = None
        self._pdp_budget = _PDP_MAX_PER_RUN
        self._pdp_requests_strikes = 0  # falhas seguidas do caminho leve (requests)
        self._seller_metrics: dict[str, int] = {
            "total_hits": 0,
            "marketplace_sellers_absent": 0,    # 1P puro
            "marketplace_sellers_list_dict": 0,  # Caso A — dict inline
            "marketplace_sellers_list_str": 0,   # Caso B — MongoDB ID
            "marketplace_sellers_dict": 0,       # Caso C
            "resolved_via_static_map": 0,
            "resolved_via_disk_cache": 0,
            "resolved_via_dynamic_cache": 0,
            "resolved_via_inline_hit": 0,
            "resolved_via_pdp": 0,
            "resolved_via_scalar_fields": 0,
            "pdp_fetch_attempts": 0,
            "pdp_fetch_failures": 0,
            "fallback_3p_unidentified": 0,
            "fallback_leroy_default": 0,
        }
        if len(self._seller_cache):
            logger.info(
                f"[{self.platform_name}] Cache de sellers: "
                f"{len(self._seller_cache)} IDs conhecidos"
            )

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    @staticmethod
    def _build_url(keyword: str, page: int = 1) -> str:
        encoded = quote_plus(keyword)
        base = f"https://www.leroymerlin.com.br/busca?term={encoded}"
        return f"{base}&page={page}" if page > 1 else base

    # ------------------------------------------------------------------
    # Estratégia 1: Algolia API direta
    # ------------------------------------------------------------------

    def _algolia_search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """
        Consulta diretamente a API Algolia da Leroy Merlin.
        Usa as credenciais públicas expostas no HTML da página.
        """
        payload = {
            "query": keyword,
            "hitsPerPage": _ITEMS_PER_PAGE,
            "page": page - 1,  # Algolia é 0-indexed
            # Sem attributesToRetrieve → retorna todos os campos do hit.
            # Isso nos permite descobrir os campos reais de preço/título
            # e evita o problema de 0% de preços causado por whitelist incompleta.
        }
        try:
            resp = requests.post(
                _ALGOLIA_SEARCH_URL,
                headers=_ALGOLIA_HEADERS,
                json=payload,
                timeout=5,   # fail fast — DNS falha em <1s no Windows (Errno 11001)
            )
            resp.raise_for_status()
            data = resp.json()
            
            # NOVO 08/mai/2026: Suportar múltiplas estruturas de resposta Algolia
            # A API pode retornar hits em diferentes chaves dependendo do versioning
            hits = (
                data.get("hits", [])
                or (data.get("results", [{}])[0].get("hits", []) if data.get("results") else [])
                or data.get("products", [])
                or data.get("data", [])
                or []
            )
            
            nb_pages = data.get("nbPages", 1)
            if hits:
                logger.info(
                    f"[{self.platform_name}] Algolia: {len(hits)} hits "
                    f"(página {page}/{nb_pages})"
                )
                debug_env = os.environ.get("LEROY_DEBUG_HITS")
                if debug_env:
                    try:
                        n = int(debug_env)
                    except ValueError:
                        n = 10
                    log_dir = Path(LOGS_DIR)
                    log_dir.mkdir(parents=True, exist_ok=True)
                    safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
                    debug_path = log_dir / f"leroy_hits_{safe_kw}_{int(time.time())}.json"
                    with open(debug_path, "w", encoding="utf-8") as fh:
                        json.dump(hits[:n], fh, ensure_ascii=False, indent=2)
                    logger.debug(f"[{self.platform_name}] Hits raw salvos: {debug_path}")
                return self._parse_algolia_hits(hits, keyword, keyword_category_map, page_offset)
            logger.debug(f"[{self.platform_name}] Algolia: 0 hits para '{keyword}' página {page}")
            return []
        except Exception as e:
            logger.warning(f"[{self.platform_name}] Algolia API erro: {e}")
            return []

    def _lookup_seller_id(self, seller_id: str) -> Optional[str]:
        """
        Resolve um seller ID **sem custo de rede**.

        Camadas: mapa estático → cache em disco → cache da execução atual.
        A resolução cara (PDP) fica em `_resolve_via_pdp`, chamada só para os
        IDs que sobram depois desta.

        Args:
            seller_id: ObjectId vindo de ``marketplaceSellers``.

        Returns:
            Nome do seller, ou None se nenhum cache conhece o ID.
        """
        if not seller_id or not isinstance(seller_id, str):
            return None

        if seller_id in LEROY_SELLER_ID_MAP:
            self._seller_metrics["resolved_via_static_map"] += 1
            return LEROY_SELLER_ID_MAP[seller_id]

        cached = self._seller_cache.get(seller_id)
        if cached:
            self._seller_metrics["resolved_via_disk_cache"] += 1
            return cached

        if seller_id in self._dynamic_seller_cache:
            self._seller_metrics["resolved_via_dynamic_cache"] += 1
            return self._dynamic_seller_cache[seller_id]

        return None

    # ------------------------------------------------------------------
    # Resolução via PDP
    # ------------------------------------------------------------------

    def _fetch_pdp_html(self, url: str) -> Optional[str]:
        """
        Baixa o HTML de um PDP.

        Tenta primeiro ``requests`` (rápido, sem browser). Se falhar — bloqueio,
        timeout ou resposta suspeita de challenge — cai para o browser
        Playwright já aberto, que carrega cookies e fingerprint reais.

        Args:
            url: URL absoluta do PDP.

        Returns:
            HTML da página, ou None se ambas as estratégias falharem.
        """
        # Se o caminho leve já falhou 3x seguidas (bloqueio/DNS), não insiste —
        # vai direto ao browser e economiza uma requisição por PDP restante.
        if self._pdp_requests_strikes < 3:
            if self._pdp_session is None:
                self._pdp_session = requests.Session()
                self._pdp_session.headers.update(_PDP_HEADERS)
            try:
                resp = self._pdp_session.get(url, timeout=_PDP_TIMEOUT, allow_redirects=True)
                html = resp.text or ""
                # <1KB ou status != 200 = challenge/erro, não PDP renderizado.
                if resp.status_code == 200 and len(html) > 1024:
                    self._pdp_requests_strikes = 0
                    return html
                self._pdp_requests_strikes += 1
                logger.debug(
                    f"[{self.platform_name}] PDP via requests inutilizável "
                    f"(HTTP {resp.status_code}, {len(html)} bytes): {url[:80]}"
                )
            except requests.RequestException as exc:
                self._pdp_requests_strikes += 1
                logger.debug(f"[{self.platform_name}] PDP via requests falhou: {exc}")

            if self._pdp_requests_strikes == 3:
                logger.info(
                    f"[{self.platform_name}] PDP via requests indisponível "
                    "(3 falhas) — usando o browser para o resto do run."
                )

        # Fallback: browser já aberto pelo BaseScraper.
        if self._page is None:
            return None
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            return self._page.content()
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] PDP via browser falhou: {exc}")
            return None

    def _resolve_via_pdp(self, seller_id: str, product_url: Optional[str]) -> Optional[str]:
        """
        Abre o PDP de um produto do seller e extrai o nome do lojista.

        É a única fonte que expõe o nome ("Vendido e entregue por X") — o índice
        Algolia só carrega o ObjectId. Custa 1 requisição por *seller novo*
        (não por produto), e o resultado é persistido em disco.

        Args:
            seller_id: ObjectId do seller a resolver.
            product_url: URL de um PDP representativo desse seller.

        Returns:
            Nome do seller, ou None (quota estourada, sem URL, fetch falhou ou
            o PDP não expôs o lojista).
        """
        if not _PDP_RESOLVE_ENABLED or not product_url:
            return None
        if self._pdp_budget <= 0:
            return None
        if not self._seller_cache.should_retry(seller_id):
            return None

        self._pdp_budget -= 1
        self._seller_metrics["pdp_fetch_attempts"] += 1

        html = self._fetch_pdp_html(product_url)
        if not html:
            self._seller_metrics["pdp_fetch_failures"] += 1
            self._seller_cache.mark_failed(seller_id, product_url)
            return None

        name = extract_seller_from_pdp(html, seller_id)
        # Produto marcado como 3P no Algolia não pode resolver para a própria
        # Leroy — sinal de PDP genérico/errado, melhor descartar.
        if not name or is_leroy_self(name):
            self._seller_metrics["pdp_fetch_failures"] += 1
            self._seller_cache.mark_failed(seller_id, product_url)
            return None

        self._seller_cache.put(seller_id, name)
        self._dynamic_seller_cache[seller_id] = name
        self._seller_metrics["resolved_via_pdp"] += 1
        logger.info(
            f"[{self.platform_name}] Seller resolvido via PDP: {seller_id} → {name}"
        )
        return name

    # Scalar fields in the Algolia hit that may carry the seller name directly.
    # NOTE: "brand" intentionally excluded — product attribute, not a seller.
    _SELLER_NAME_FIELDS = (
        "sellerName",
        "seller",
        "merchant",
        "marketplace_seller",
        "vendorName",
        "storeName",
    )

    # Normalized names that identify Leroy Merlin's own 1P seller entry,
    # used to skip the self-entry when scanning `sellers` / `installmentsBySeller`.
    _LEROY_SELF_NAMES: frozenset[str] = frozenset({
        "leroymerlin", "leroy merlin", "leroy_merlin", "leroy", "1",
    })

    def _find_seller_in_hit(self, hit: dict, target_id: str) -> Optional[str]:
        """
        Looks for the seller name **inline** in the Algolia hit, without any
        network call.  Checks two VTEX-standard fields in priority order:

        1. ``sellers`` — list of dicts: [{sellerId, sellerName, ...}, ...]
           VTEX Algolia integrations always expose this when products have
           marketplace offers.  We first try to match by sellerId == target_id;
           if no ID match, we return the single non-Leroy entry (unambiguous).

        2. ``installmentsBySeller`` — dict keyed by sellerId, each value is a
           dict that may carry sellerName/storeName.

        Returns the clean seller name string, or None if not found.
        """
        leroy = self._LEROY_SELF_NAMES

        # --- 1. `sellers` array ---
        sellers_arr = hit.get("sellers")
        if isinstance(sellers_arr, list) and sellers_arr:
            non_leroy: list[str] = []
            for s in sellers_arr:
                if not isinstance(s, dict):
                    continue
                name = (
                    s.get("sellerName")
                    or s.get("seller_name")
                    or s.get("storeName")
                    or s.get("name")
                )
                if not name or not isinstance(name, str):
                    continue
                clean = name.strip()
                if clean.lower() in leroy:
                    continue
                sid = s.get("sellerId") or s.get("seller_id") or s.get("id")
                if sid and str(sid) == target_id:
                    return clean          # exact ID match — highest confidence
                non_leroy.append(clean)
            if len(non_leroy) == 1:
                return non_leroy[0]      # only one 3P seller — unambiguous

        # --- 2. `installmentsBySeller` dict ---
        ibs = hit.get("installmentsBySeller")
        if isinstance(ibs, dict):
            for seller_key, seller_data in ibs.items():
                if not isinstance(seller_data, dict):
                    continue
                name = seller_data.get("sellerName") or seller_data.get("storeName")
                if not name or not isinstance(name, str):
                    continue
                clean = name.strip()
                if clean.lower() in leroy:
                    continue
                if seller_key == target_id:
                    return clean

        return None

    def _classify_hit_seller(self, hit: dict) -> Dict[str, Any]:
        """
        Classifica o seller de um hit Algolia **sem tocar na rede**.

        Camadas (todas de custo zero):
          1. ``marketplaceSellers`` ausente/vazio → 1P (sortimento Leroy)
          2. Lista de dicts com nome inline
          3. Mapa estático → cache em disco → cache da execução
          4. Campos inline do hit (`sellers`, `installmentsBySeller`)
          5. Campos escalares (sellerName, merchant, …)

        Quando nada resolve, devolve ``seller=None`` e o ``seller_id`` fica
        pendente para a rodada de PDP em `_parse_algolia_hits`.

        Args:
            hit: hit bruto da API Algolia.

        Returns:
            Dict com ``seller`` (str|None), ``seller_id`` (str|None),
            ``qtd_sellers`` (int|None) e ``tipo_seller`` ("1P"|"3P").
        """
        self._seller_metrics["total_hits"] += 1

        if not hasattr(self, '_seller_field_logged'):
            self._seller_field_logged = True
            ms = hit.get("marketplaceSellers")
            logger.debug(
                f"[{self.platform_name}] marketplaceSellers "
                f"type={type(ms).__name__} "
                f"value={json.dumps(ms, ensure_ascii=False)[:300]}"
            )

        marketplace_sellers = hit.get("marketplaceSellers")

        # Absent or empty list → 1P product sold directly by Leroy Merlin
        if not marketplace_sellers:
            self._seller_metrics["marketplace_sellers_absent"] += 1
            self._seller_metrics["fallback_leroy_default"] += 1
            return {
                "seller": "Leroy Merlin",
                "seller_id": None,
                "qtd_sellers": 1,
                "tipo_seller": "1P",
            }

        # Nº de sellers de marketplace anunciados pelo próprio índice.
        qtd_sellers = (
            len(marketplace_sellers)
            if isinstance(marketplace_sellers, (list, dict))
            else None
        )

        if isinstance(marketplace_sellers, list) and marketplace_sellers:
            first = marketplace_sellers[0]

            # Case A: list of dicts carrying the name inline
            if isinstance(first, dict):
                self._seller_metrics["marketplace_sellers_list_dict"] += 1
                seller = (
                    first.get("sellerName")
                    or first.get("seller_name")
                    or first.get("sellerId")
                    or first.get("seller_id")
                )
                if seller:
                    self._seller_metrics["resolved_via_inline_hit"] += 1
                    return {
                        "seller": str(seller).strip(),
                        "seller_id": None,
                        "qtd_sellers": qtd_sellers,
                        "tipo_seller": "3P",
                    }
                # dict sem campo utilizável — cai para 1P por falta de evidência
                self._seller_metrics["fallback_leroy_default"] += 1
                return {
                    "seller": "Leroy Merlin",
                    "seller_id": None,
                    "qtd_sellers": qtd_sellers,
                    "tipo_seller": "1P",
                }

            # Case B: list of MongoDB ObjectId strings
            if isinstance(first, str):
                self._seller_metrics["marketplace_sellers_list_str"] += 1

                # Layers 1-3: mapa estático / cache em disco / cache do run
                cached = self._lookup_seller_id(first)
                if cached:
                    return {
                        "seller": cached,
                        "seller_id": first,
                        "qtd_sellers": qtd_sellers,
                        "tipo_seller": "3P",
                    }

                # Layer 4: inline `sellers` / `installmentsBySeller`
                inline = self._find_seller_in_hit(hit, first)
                if inline:
                    self._dynamic_seller_cache[first] = inline
                    self._seller_metrics["resolved_via_inline_hit"] += 1
                    logger.debug(
                        f"[{self.platform_name}] Seller resolvido inline: {first[:8]}… → {inline}"
                    )
                    return {
                        "seller": inline,
                        "seller_id": first,
                        "qtd_sellers": qtd_sellers,
                        "tipo_seller": "3P",
                    }

                # Layer 5: scalar name fields
                for field in self._SELLER_NAME_FIELDS:
                    val = hit.get(field)
                    if val and isinstance(val, str) and val.strip():
                        name = val.strip()
                        self._dynamic_seller_cache[first] = name
                        self._seller_metrics["resolved_via_scalar_fields"] += 1
                        return {
                            "seller": name,
                            "seller_id": first,
                            "qtd_sellers": qtd_sellers,
                            "tipo_seller": "3P",
                        }

                # Pendente — a rodada de PDP tenta resolver antes do fallback.
                return {
                    "seller": None,
                    "seller_id": first,
                    "qtd_sellers": qtd_sellers,
                    "tipo_seller": "3P",
                }

        # Case C: dict keyed by sellerId
        if isinstance(marketplace_sellers, dict) and marketplace_sellers:
            self._seller_metrics["marketplace_sellers_dict"] += 1
            first_key = next(iter(marketplace_sellers))
            entry = marketplace_sellers[first_key]
            if isinstance(entry, dict):
                seller = (
                    entry.get("sellerName")
                    or entry.get("seller_name")
                    or first_key
                )
                if seller:
                    self._seller_metrics["resolved_via_inline_hit"] += 1
                    return {
                        "seller": str(seller).strip(),
                        "seller_id": str(first_key),
                        "qtd_sellers": qtd_sellers,
                        "tipo_seller": "3P",
                    }

        self._seller_metrics["fallback_leroy_default"] += 1
        return {
            "seller": "Leroy Merlin",
            "seller_id": None,
            "qtd_sellers": qtd_sellers,
            "tipo_seller": "1P",
        }

    def _resolve_pending_sellers(
        self,
        pending: Dict[str, str],
    ) -> Dict[str, str]:
        """
        Resolve, via PDP, os seller IDs que sobraram das camadas de cache.

        Roda **uma vez por página**, sobre IDs *únicos* — é isso que torna a
        estratégia viável: 24 hits podem virar 1-3 PDPs, e cada ID resolvido
        nunca mais é buscado (cache em disco).

        Args:
            pending: mapa ``seller_id → URL de um PDP representativo``.

        Returns:
            Mapa ``seller_id → nome`` apenas dos que foram resolvidos.
        """
        if not pending or not _PDP_RESOLVE_ENABLED:
            return {}

        resolved: Dict[str, str] = {}
        for seller_id, product_url in pending.items():
            # Orçamento estourado: nada mais será buscado nesta execução, então
            # não há motivo para continuar iterando.
            if self._pdp_budget <= 0:
                break

            attempts_before = self._seller_metrics["pdp_fetch_attempts"]
            name = self._resolve_via_pdp(seller_id, product_url)
            if name:
                resolved[seller_id] = name

            # Só espaça quando houve requisição de verdade. IDs em quarentena
            # voltam sem tocar a rede — dormir aí seria tempo morto acumulado
            # a cada página das 40 keywords do run.
            if self._seller_metrics["pdp_fetch_attempts"] > attempts_before:
                self._random_delay(min_s=0.4, max_s=1.2)

        # Salva também quando nada resolveu: a quarentena dos IDs que falharam
        # é o que evita repetir os mesmos PDPs mortos na próxima coleta.
        self._seller_cache.save()
        return resolved

    def _parse_algolia_hits(
        self,
        hits: List[Dict],
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        # Congela a lista de hits antes de qualquer navegação. No caminho de XHR
        # o caller passa `self._captured_products`, que o listener de `response`
        # continua alimentando: se a passada de PDP navegar o browser, os XHRs
        # do PDP (Algolia de produtos relacionados) entrariam nesta mesma lista
        # depois de `seller_info` estar montado — desalinhando os índices e
        # misturando produtos do PDP no resultado da busca.
        hits = list(hits)

        # --- Passada 1: classifica sellers e junta os IDs pendentes ---
        # Trabalhar por ID único (e não por produto) é o que torna a resolução
        # via PDP barata: dezenas de hits colapsam em poucos IDs novos.
        seller_info: List[Dict[str, Any]] = []
        pending: Dict[str, str] = {}
        for hit in hits:
            info = self._classify_hit_seller(hit)
            seller_info.append(info)
            sid = info.get("seller_id")
            if info.get("seller") is None and sid and sid not in pending:
                url = self._extract_algolia_url(hit)
                if url:
                    pending[sid] = url

        # --- Passada 2: resolve os pendentes abrindo 1 PDP por seller novo ---
        if pending:
            logger.debug(
                f"[{self.platform_name}] {len(pending)} seller(s) pendente(s) "
                f"para resolução via PDP"
            )
            resolved = self._resolve_pending_sellers(pending)
            for info in seller_info:
                sid = info.get("seller_id")
                if info.get("seller") is None and sid in resolved:
                    info["seller"] = resolved[sid]

        records = []

        for idx, hit in enumerate(hits):
            info = seller_info[idx]
            # Título: prioridade para nome curto (name/shortName/title).
            # "description" em Leroy tende a conter texto de bullet points.
            title = (
                hit.get("name")
                or hit.get("shortName")
                or hit.get("title")
                or hit.get("productName")
                or hit.get("description")
            )
            
            # NOVO 08/mai/2026: Validar título antes de continuar
            if not title:
                logger.warning(
                    f"[{self.platform_name}] Hit sem título detectado: "
                    f"objectID={hit.get('objectID', 'N/A')}, pula registro"
                )
                continue  # ← PULA registros sem título

            # Preço: campos confirmados via debug em produção (30/03/2026).
            # averagePromotionalPrice e medianPromotionalPrice são os únicos
            # campos de preço retornados pelo índice production_products.
            price_val = (
                hit.get("averagePromotionalPrice")
                or hit.get("medianPromotionalPrice")
                or hit.get("price")
                or hit.get("preco")
                or hit.get("sellingPrice")
                or hit.get("bestPrice")
                or (hit.get("priceRange") or {}).get("sellingPrice", {}).get("lowPrice")
                or (hit.get("priceRange") or {}).get("minPrice")
            )
            if isinstance(price_val, dict):
                price_val = price_val.get("value") or price_val.get("amount")

            try:
                price_float = float(str(price_val).replace(",", ".")) if price_val else None
            except (ValueError, TypeError):
                price_float = None

            # Log único das chaves do hit para diagnóstico de campo seller.
            if not hasattr(self, '_algolia_keys_logged'):
                self._algolia_keys_logged = True
                logger.debug(
                    f"[{self.platform_name}] Algolia hit keys disponíveis: {list(hit.keys())}"
                )
                logger.debug(
                    f"[{self.platform_name}] Algolia hit sample: "
                    f"{json.dumps(hit, ensure_ascii=False)[:800]}"
                )

            seller = info.get("seller")
            if seller is None:
                # Nem cache nem PDP identificaram o lojista — WARNING uma vez
                # por ID único, para o log continuar legível.
                sid = info.get("seller_id")
                if sid and sid not in self._seller_warned:
                    self._seller_warned.add(sid)
                    logger.warning(
                        f"[{self.platform_name}] Seller ID não resolvido: {sid} "
                        f"(produto: {str(hit.get('name', '?'))[:60]})"
                    )
                self._seller_metrics["fallback_3p_unidentified"] += 1
                seller = UNRESOLVED_SELLER
            tipo_seller = info.get("tipo_seller") or (
                "1P" if seller.strip() == "Leroy Merlin" else "3P"
            )

            rating = (
                hit.get("rating")
                or hit.get("ratingAverage")
                or hit.get("averageRating")
                or (hit.get("ratings") or {}).get("average")
                or (hit.get("reviews") or {}).get("average")
            )
            review_count = (
                hit.get("reviewCount")
                or hit.get("totalReviews")
                or hit.get("numberOfRatings")
                or hit.get("numberOfReviews")
                or hit.get("reviewsCount")
                or (hit.get("ratings") or {}).get("total")
                or (hit.get("reviews") or {}).get("total")
            )
            pos = page_offset + idx + 1

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title,
                position_general=pos,
                position_organic=pos,
                position_sponsored=None,
                price_float=price_float,
                seller=seller,
                buy_box_seller=seller,
                qtd_sellers=info.get("qtd_sellers"),
                tipo_seller=tipo_seller,
                is_fulfillment=False,
                rating=float(rating) if rating else None,
                review_count=int(review_count) if review_count else None,
                tag_destaque=None,
                url_produto=self._extract_algolia_url(hit),
            ))

        if hits:
            n_1p = sum(1 for r in records if r.get("Seller / Vendedor") == "Leroy Merlin")
            n_unresolved = sum(
                1 for r in records
                if r.get("Seller / Vendedor") == UNRESOLVED_SELLER
            )
            n_3p_resolved = len(records) - n_1p - n_unresolved
            logger.info(
                f"[{self.platform_name}] Sellers: {n_1p} 1P (Leroy Merlin) | "
                f"{n_3p_resolved} 3P resolvidos | {n_unresolved} 3P não resolvidos"
            )

        return records

    # ------------------------------------------------------------------
    # Estratégia 2: XHR interception (Algolia + APIs internas)
    # ------------------------------------------------------------------

    def _setup_xhr_intercept(self) -> None:
        self._captured_products = []

        def handle_response(response):
            try:
                url = response.url
                if not any(pat in url for pat in _API_URL_PATTERNS):
                    return
                if response.status != 200:
                    return
                if "json" not in response.headers.get("content-type", ""):
                    return
                data = response.json()
                # Algolia response format
                products = (
                    data.get("hits")
                    or data.get("products")
                    or data.get("items")
                    or data.get("data", {}).get("products")
                    or self._deep_find_products(data)
                    or []
                )
                if products:
                    self._captured_products.extend(products)
                    logger.debug(
                        f"[{self.platform_name}] XHR capturado: "
                        f"{len(products)} produtos em {url[:70]}"
                    )
            except Exception:
                pass

        self._page.on("response", handle_response)

    @staticmethod
    def _deep_find_products(obj, depth: int = 0) -> List[Dict]:
        """
        Busca recursiva por array de produtos em objetos JSON aninhados.
        Considera array de produto quando tem ≥3 itens com chave 'id' ou 'sku'.
        """
        if depth > 6:
            return []
        if isinstance(obj, list) and len(obj) >= 3:
            if any(isinstance(i, dict) and ("id" in i or "sku" in i or "name" in i or "objectID" in i) for i in obj):
                return obj
        if isinstance(obj, dict):
            for v in obj.values():
                result = LeroyMerlinScraper._deep_find_products(v, depth + 1)
                if result:
                    return result
        return []

    def _parse_captured_products(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_offset: int,
    ) -> List[Dict[str, Any]]:
        """Converte produtos capturados via XHR (Algolia hits ou formato genérico)."""
        return self._parse_algolia_hits(
            self._captured_products, keyword, keyword_category_map, page_offset
        )

    # ------------------------------------------------------------------
    # Estratégia 3: DOM parse
    # ------------------------------------------------------------------

    @staticmethod
    def _first_match(tag: Tag, candidates: List[str]) -> Optional[Tag]:
        for sel in candidates:
            el = tag.select_one(sel)
            if el:
                return el
        return None

    @staticmethod
    def _extract_algolia_url(hit: dict) -> Optional[str]:
        """Extrai/constrói a URL do PDP a partir de um hit Algolia (padrão VTEX)."""
        url = (
            hit.get("url")
            or hit.get("productUrl")
            or hit.get("link")
            or hit.get("permalink")
        )
        if url and isinstance(url, str):
            url = url.strip()
            if url.startswith("/"):
                url = f"https://www.leroymerlin.com.br{url}"
            return url
        # VTEX: linkText → https://www.leroymerlin.com.br/{linkText}/p
        link_text = hit.get("linkText") or hit.get("slug")
        if link_text and isinstance(link_text, str):
            return f"https://www.leroymerlin.com.br/{link_text.strip('/')}/p"
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

        if soup.select_one(_SELECTORS["bot_check"]):
            logger.warning(f"[{self.platform_name}] Bot-check detectado (página {page}).")
            return []

        items, sel_used = self._detect_items(soup)
        logger.info(
            f"[{self.platform_name}] {len(items)} itens encontrados na página "
            f"(seletor: {sel_used})"
        )

        if not items:
            self._dump_debug(html, page, keyword)
            return []

        records = []
        for idx, item in enumerate(items):
            title_el  = self._first_match(item, _SELECTORS["title_candidates"])
            price_el  = self._first_match(item, _SELECTORS["price_candidates"])
            rating_el = self._first_match(item, _SELECTORS["rating_candidates"])
            review_el = self._first_match(item, _SELECTORS["review_count_candidates"])
            tag_el    = self._first_match(item, _SELECTORS["tag_candidates"])
            pos = page_offset + idx + 1

            # URL do produto — âncora para /p/ ou /produto/
            url_produto = None
            link_el = (
                item.select_one('a[href*="/p/"]')
                or item.select_one('a[href*="/produto/"]')
                or item.select_one("a[href]")
            )
            if link_el and link_el.get("href"):
                href = link_el.get("href").strip()
                if href.startswith("/"):
                    href = f"https://www.leroymerlin.com.br{href}"
                url_produto = href

            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=title_el.get_text(strip=True) if title_el else None,
                position_general=pos,
                position_organic=pos,
                position_sponsored=None,
                price_raw=price_el.get_text(strip=True) if price_el else None,
                seller="Leroy Merlin",
                # No fallback DOM não há array de sellers da Algolia — o grid
                # renderizado é sempre sortimento próprio Leroy (1P).
                buy_box_seller="Leroy Merlin",
                tipo_seller="1P",
                is_fulfillment=False,
                rating=parse_rating(rating_el.get_text() if rating_el else None),
                review_count=parse_review_count(review_el.get_text() if review_el else None),
                tag_destaque=tag_el.get_text(strip=True) if tag_el else None,
                url_produto=url_produto,
            ))

        return records

    # ------------------------------------------------------------------
    # Debug dump
    # ------------------------------------------------------------------

    def _dump_debug(self, html: str, page: int, keyword: str) -> None:
        try:
            log_dir = Path(LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            safe_kw = keyword[:30].replace(" ", "_").replace("/", "-")
            path = log_dir / f"leroy_debug_p{page}_{safe_kw}.html"
            path.write_text(html, encoding="utf-8")
            logger.warning(
                f"[{self.platform_name}] 0 itens — HTML salvo: {path}\n"
                "  → Nota: Leroy usa Algolia. Verifique logs de XHR acima."
            )
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Erro ao salvar debug: {e}")

    # ------------------------------------------------------------------
    # Espera
    # ------------------------------------------------------------------

    def _wait_for_products(self, timeout_ms: int = 15_000) -> bool:
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

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=6, max=18),
        reraise=False,
    )
    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Busca um termo no Leroy Merlin via API Algolia.

        Intercepta o XHR do Algolia e classifica cada hit como 1P (sortimento
        próprio Leroy) ou 3P (marketplace), preenchendo ``buy_box_seller`` e
        ``tipo_seller``.

        Args:
            keyword: termo de busca.
            keyword_category_map: mapa keyword → categoria (para o registro).
            page_limit: nº máximo de páginas a coletar.

        Returns:
            Lista de registros normalizados (um por hit do Algolia).
        """
        all_records: List[Dict[str, Any]] = []
        self._setup_xhr_intercept()

        for page in range(1, page_limit + 1):
            url = self._build_url(keyword, page)
            logger.info(f"[{self.platform_name}] Página {page}/{page_limit} → {url}")
            self._captured_products = []
            offset = (page - 1) * _ITEMS_PER_PAGE
            records: List[Dict[str, Any]] = []
            self._last_screenshot_busca = None  # reset antes de nova página

            # --- Estratégia 1: Algolia API direta (primário — mais rápido) ---
            # Prova-se mais confiável quando o DNS resolve (maioria dos casos).
            records = self._algolia_search(keyword, keyword_category_map, page, offset)
            if records:
                logger.info(f"[{self.platform_name}] {len(records)} itens via Algolia API")
                # A API Algolia não renderiza página — navega o browser apenas
                # para capturar o screenshot de evidência quando habilitado.
                if self.screenshot_manager is not None:
                    try:
                        self._page.goto(url, wait_until="domcontentloaded", timeout=40_000)
                        self._wait_for_products(timeout_ms=4_000)
                        self._human_scroll(steps=6, step_px=350)
                        shot = self.capture_screenshot(
                            identifier=f"{keyword}_p{page}", tipo="busca"
                        )
                        for rec in records:
                            rec["Screenshot Busca"] = shot
                    except Exception as exc:
                        logger.debug(
                            f"[{self.platform_name}] Screenshot (Algolia path) falhou: {exc}"
                        )

            # --- Estratégia 2: Browser + XHR (fallback para falha de DNS) ---
            # O browser tem seu próprio stack DNS — funciona mesmo quando
            # requests.post() falha com Errno 11001 no Windows.
            if not records:
                try:
                    self._page.goto(url, wait_until="domcontentloaded", timeout=40_000)
                    self._wait_for_products(timeout_ms=4_000)
                    self._wait_for_network_idle()
                    self._random_delay(min_s=2.5, max_s=5.0)
                    self._human_scroll(steps=8, step_px=350)
                    time.sleep(2.0)

                    # captura screenshot da página de busca
                    self._last_screenshot_busca = self.capture_screenshot(identifier=f"{keyword}_p{page}", tipo="busca")

                    html = self._page.content()

                    if self._captured_products:
                        logger.info(
                            f"[{self.platform_name}] {len(self._captured_products)} itens via XHR (browser)"
                        )
                        records = self._parse_captured_products(
                            keyword, keyword_category_map, offset
                        )

                    # --- Estratégia 3: DOM ---
                    if not records:
                        records = self._parse_dom(html, keyword, keyword_category_map, page, offset)

                except Exception as exc:
                    logger.warning(f"[{self.platform_name}] Erro no browser (pág {page}): {exc}")

            all_records.extend(records)

            if not records:
                logger.warning(
                    f"[{self.platform_name}] Página {page} retornou 0 itens. "
                    "Parando keyword."
                )
                break

            if page < page_limit:
                self._random_delay()

        self._log_search_result(keyword, len(all_records))
        logger.info(json.dumps({
            "plataforma": "Leroy Merlin",
            "keyword": keyword,
            "seller_metrics": self._seller_metrics,
            "timestamp": now_brt().isoformat(),
        }, ensure_ascii=False))
        self._seller_metrics = {k: 0 for k in self._seller_metrics}
        return all_records
