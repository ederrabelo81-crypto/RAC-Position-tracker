"""
scrapers/shopee.py — Scraper da Shopee Brasil (shopee.com.br).

Foco (Mai/2026): inteligência de sellers. Cada resultado da busca da Shopee é
a oferta de UMA loja (marketplace puro), então o "buy box seller" é a própria
loja do anúncio. Extraímos:
  * buy_box_seller  — shop_name do anúncio
  * tipo_seller     — "Shopee Mall" (is_official_shop), "Preferred+"
                      (is_preferred_plus_seller) ou "3P"
  * reputacao_seller— shop_rating, quando presente
  * tag_destaque    — volume de vendas ("X vendidos")

Estratégia: API interna v4 (a mesma usada pelo app/web), via curl_cffi com
impersonation chrome124 (replica o TLS JA3/JA4 do Chrome real). Carrega a
sessão capturada por `utils/session_grabber.py --site shopee` (cookies SPC_*
+ csrftoken) e replica o request — mais rápido e menos detectável que navegar
o browser por keyword.

⚠️  BEST-EFFORT sem proxy residencial BR:
    - O IP de datacenter (Oracle VM / GitHub Actions) é marcado pelo anti-bot
      da Shopee ANTES do fingerprint. Sem proxy BR a coleta é instável.
    - error=90309999 → bloqueio anti-fraude: falta o header `af-ac-enc-dat`,
      gerado dinamicamente pela JS. Re-capture a sessão (logado, após navegar
      pela busca) com session_grabber. Como último recurso, a Shopee Open
      Platform API (https://open.shopee.com/) é a via oficial.
    - A sessão expira em horas → re-capturar periodicamente.
"""

import random
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from loguru import logger

try:
    from curl_cffi import requests as _cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    _cffi_requests = None  # type: ignore[assignment]
    _HAS_CURL_CFFI = False

import requests as _std_requests

from config import MAX_PAGES
from scrapers.base import BaseScraper

_SHOPEE_BASE = "https://shopee.com.br"
_SHOPEE_API = f"{_SHOPEE_BASE}/api/v4/search/search_items"
_ITEMS_PER_PAGE = 60
_API_TIMEOUT = 12
_RETRY_ATTEMPTS = 3
# Throttle agressivo: Shopee rate-limita rápido por IP.
_INTER_PAGE_DELAY = (3.0, 7.0)

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{_SHOPEE_BASE}/search",
    "x-requested-with": "XMLHttpRequest",
    "x-shopee-language": "pt-BR",
    "x-api-source": "pc",
}


class ShopeeScraper(BaseScraper):
    """
    Scraper da Shopee via API v4 (HTTP puro — não inicia browser).

    Compatível com a interface BaseScraper (_build_record, context manager).
    Carrega a sessão capturada por session_grabber para autenticar a API.
    """

    platform_name = "Shopee"

    def __init__(self, headless: bool = True) -> None:
        # headless ignorado — este scraper não usa browser
        super().__init__(headless=True)
        self._session: Optional[Any] = None
        self._csrf_token: str = ""
        self.captcha_hit: bool = False

    # ------------------------------------------------------------------
    # Context manager — sem browser; só prepara a sessão HTTP
    # ------------------------------------------------------------------

    def _launch(self) -> None:
        if _HAS_CURL_CFFI:
            self._session = _cffi_requests.Session()
            flavor = "curl_cffi (chrome124)"
        else:
            self._session = _std_requests.Session()
            flavor = "requests (curl_cffi indisponível — mais detectável)"

        # Carrega cookies da sessão capturada (session_grabber.py --site shopee)
        n_cookies = self._load_session_cookies()

        # Warm-up: GET na home para coletar cookies frescos na mesma sessão
        self._warmup()

        logger.info(
            f"[{self.platform_name}] Sessão HTTP pronta via {flavor} | "
            f"cookies salvos: {n_cookies} | csrf: {'sim' if self._csrf_token else 'não'}"
        )
        if not self._csrf_token:
            logger.warning(
                f"[{self.platform_name}] Sem csrftoken — API pode rejeitar. "
                "Capture a sessão logado: python utils/session_grabber.py --site shopee"
            )

    def _close(self) -> None:
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            pass
        self._session = None

    # ------------------------------------------------------------------
    # Sessão / warm-up
    # ------------------------------------------------------------------

    def _load_session_cookies(self) -> int:
        """Aplica cookies da sessão capturada à sessão HTTP. Retorna a contagem."""
        try:
            from utils.session_grabber import load_session
            cookies = load_session("shopee") or []
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] load_session falhou: {exc}")
            return 0

        for c in cookies:
            name, value = c.get("name"), c.get("value")
            if not name or value is None:
                continue
            domain = (c.get("domain") or "shopee.com.br").lstrip(".")
            try:
                self._session.cookies.set(name, value, domain=domain)
            except Exception:
                pass
            if name == "csrftoken":
                self._csrf_token = value
        return len(cookies)

    def _warmup(self) -> None:
        """GET na home para o anti-bot emitir cookies de sessão frescos."""
        try:
            kwargs: Dict[str, Any] = {
                "headers": {
                    "User-Agent": _BASE_HEADERS["User-Agent"],
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": _BASE_HEADERS["Accept-Language"],
                    "Upgrade-Insecure-Requests": "1",
                },
                "timeout": _API_TIMEOUT,
            }
            if _HAS_CURL_CFFI:
                kwargs["impersonate"] = "chrome124"
            resp = self._session.get(f"{_SHOPEE_BASE}/", **kwargs)
            # csrftoken pode ser emitido só no warm-up
            if not self._csrf_token:
                tok = self._session.cookies.get("csrftoken")
                if tok:
                    self._csrf_token = tok
            logger.debug(f"[{self.platform_name}] Warm-up home: HTTP {resp.status_code}")
            time.sleep(1.5)
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] Warm-up falhou: {exc}")

    # ------------------------------------------------------------------
    # Chamada à API
    # ------------------------------------------------------------------

    def _fetch_page(self, keyword: str, page: int) -> Optional[dict]:
        newest = page * _ITEMS_PER_PAGE
        params = {
            "by": "relevancy",
            "keyword": keyword,
            "limit": _ITEMS_PER_PAGE,
            "newest": newest,
            "order": "desc",
            "page_type": "search",
            "scenario": "PAGE_GLOBAL_SEARCH",
            "version": "2",
        }
        headers = dict(_BASE_HEADERS)
        if self._csrf_token:
            headers["x-csrftoken"] = self._csrf_token

        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                kwargs: Dict[str, Any] = {
                    "params": params, "headers": headers, "timeout": _API_TIMEOUT,
                }
                if _HAS_CURL_CFFI:
                    kwargs["impersonate"] = "chrome124"
                resp = self._session.get(_SHOPEE_API, **kwargs)

                ct = resp.headers.get("content-type", "")
                if resp.status_code != 200 or "application/json" not in ct:
                    logger.warning(
                        f"[{self.platform_name}] HTTP {resp.status_code} CT={ct[:40]} "
                        f"(tentativa {attempt}/{_RETRY_ATTEMPTS})"
                    )
                    if attempt < _RETRY_ATTEMPTS:
                        time.sleep(attempt * 3)
                    continue

                data = resp.json()
                err = data.get("error")
                if err:
                    self._log_api_error(err, page)
                    return None
                return data
            except Exception as exc:
                logger.warning(
                    f"[{self.platform_name}] Falha tentativa {attempt}/{_RETRY_ATTEMPTS}: {exc}"
                )
                if attempt < _RETRY_ATTEMPTS:
                    time.sleep(attempt * 3)
        return None

    def _log_api_error(self, err: Any, page: int) -> None:
        if err == 90309999:
            logger.error(
                f"[{self.platform_name}] Bloqueio anti-fraude (90309999) pág {page+1}: "
                "header 'af-ac-enc-dat' ausente (gerado pela JS do browser). "
                "Re-capture a sessão logado após navegar pela busca, ou use a "
                "Shopee Open Platform API. Sem proxy BR isto é esperado."
            )
            self.captcha_hit = True
        else:
            logger.error(f"[{self.platform_name}] API erro {err} na pág {page+1}")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_seller(item: dict) -> str:
        if item.get("is_official_shop"):
            return "Shopee Mall"
        if item.get("is_preferred_plus_seller") or item.get("shopee_verified"):
            return "Preferred+"
        return "3P"

    def _parse_items(
        self,
        items: List[dict],
        keyword: str,
        keyword_category_map: dict,
        page: int,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for idx, wrapper in enumerate(items):
            item = wrapper.get("item_basic") or wrapper.get("item") or {}
            if not item:
                continue

            name = item.get("name")
            # Preço: Shopee guarda em centavos × 100000
            raw_price = item.get("price")
            price_float = (
                round(raw_price / 100000, 2) if isinstance(raw_price, (int, float)) and raw_price > 0
                else None
            )

            rating_info = item.get("item_rating") or {}
            rating = rating_info.get("rating_star")
            rating_counts = rating_info.get("rating_count") or []
            review_count = sum(rating_counts) if isinstance(rating_counts, list) else None

            shop_name = item.get("shop_name") or item.get("shop_location") or None
            tipo_seller = self._classify_seller(item)

            shop_rating = item.get("shop_rating")
            reputacao = f"{round(shop_rating, 2)}" if isinstance(shop_rating, (int, float)) else None

            sold = item.get("historical_sold") or item.get("sold")
            tag_destaque = f"{sold} vendidos" if sold else None

            shopid, itemid = item.get("shopid"), item.get("itemid")
            url_produto = (
                f"{_SHOPEE_BASE}/product/{shopid}/{itemid}" if shopid and itemid else None
            )

            pos = page * _ITEMS_PER_PAGE + idx + 1
            records.append(self._build_record(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                title=name,
                position_general=pos,
                position_organic=pos,
                position_sponsored=None,
                price_float=price_float,
                seller=shop_name,
                buy_box_seller=shop_name,
                qtd_sellers=None,  # marketplace puro: cada anúncio é 1 loja
                tipo_seller=tipo_seller,
                reputacao_seller=reputacao,
                is_fulfillment=False,
                rating=float(rating) if isinstance(rating, (int, float)) else None,
                review_count=int(review_count) if review_count else None,
                tag_destaque=tag_destaque,
                url_produto=url_produto,
            ))
        return records

    # ------------------------------------------------------------------
    # Interface pública
    # ------------------------------------------------------------------

    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        if not _HAS_CURL_CFFI:
            logger.warning(
                f"[{self.platform_name}] curl_cffi não instalado — coleta provavelmente "
                "será bloqueada. Instale: pip install curl_cffi"
            )

        all_records: List[Dict[str, Any]] = []
        for page in range(page_limit):
            data = self._fetch_page(keyword, page)
            if data is None:
                break

            items = data.get("items") or []
            if not items:
                logger.info(f"[{self.platform_name}] Sem mais resultados (pág {page+1}).")
                break

            records = self._parse_items(items, keyword, keyword_category_map, page)
            all_records.extend(records)
            logger.info(
                f"[{self.platform_name}] Pág {page+1}/{page_limit}: "
                f"{len(records)} produtos (de {len(items)} retornados)"
            )

            if len(items) < _ITEMS_PER_PAGE:
                break
            time.sleep(random.uniform(*_INTER_PAGE_DELAY))

        return all_records
