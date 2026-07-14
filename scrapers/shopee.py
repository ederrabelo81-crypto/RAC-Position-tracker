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

import json
import math
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
from scrapers.local_browser import get_local_browser, is_local_chrome_enabled

_SHOPEE_BASE = "https://shopee.com.br"
_SHOPEE_API = f"{_SHOPEE_BASE}/api/v4/search/search_items"
_ITEMS_PER_PAGE = 60
_API_TIMEOUT = 12
_RETRY_ATTEMPTS = 3
# Throttle agressivo: Shopee rate-limita rápido por IP.
_INTER_PAGE_DELAY = (3.0, 7.0)
# Padrão de URL da API de busca interceptada no modo browser.
_SEARCH_API_PATTERN = "/api/v4/search/search_items"

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

# Cookies que só existem em sessão LOGADA. csrftoken/SPC_SI/SPC_SEC_SI saem
# até pra visitante anônimo — com eles mas SEM estes, a API v4 responde 403.
_LOGIN_COOKIE_NAMES = ("SPC_EC", "SPC_ST", "SPC_U")

# Circuit breaker: 403 da API v4 é bloqueio de sessão/IP, não falha pontual —
# após N keywords seguidas 100% bloqueadas, aborta a coleta Shopee inteira
# (31 keywords × 3 retries × backoff ≈ 5min de requests inúteis por execução).
_ABORT_AFTER_BLOCKED_KEYWORDS = 3


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
        # UA do browser que gerou a sessão (refresh_sessions_cdp) — replicar
        # o MESMO UA dos cookies reduz o cruzamento UA × sessão do anti-fraude.
        self._session_ua: str = ""
        self._has_login_cookies: bool = False
        # Estado do circuit breaker (ver _ABORT_AFTER_BLOCKED_KEYWORDS)
        self._hard_blocked: bool = False        # 403/anti-fraude na keyword atual
        self._blocked_keyword_streak: int = 0
        self.collection_aborted: bool = False
        # Dump de diagnóstico de "parse oco" (itens parseiam mas name/price vêm
        # nulos → nova troca de chave da API). Uma amostra por processo basta.
        self._shape_dumped: bool = False

        # ── Modo browser local (Chrome real logado) ──────────────────────
        # Quando RAC_LOCAL_CHROME=1: coletamos DENTRO do Chrome logado do
        # usuário e interceptamos a chamada NATIVA da API v4 (que carrega o
        # header anti-fraude `af-ac-enc-dat`, gerado pela JS da Shopee — o que
        # o replay via curl_cffi não tinha). É o caminho que realmente destrava
        # a Shopee no notebook do usuário. curl_cffi vira fallback.
        self._local_browser: Optional[Any] = None
        self._page: Optional[Any] = None
        self._local_active: bool = False
        self._captured_search: List[dict] = []
        self._xhr_page: Optional[Any] = None

    # ------------------------------------------------------------------
    # Context manager — modo browser local (preferido) ou sessão HTTP
    # ------------------------------------------------------------------

    def _launch(self) -> None:
        # Preferência: Chrome real logado (destrava a Shopee de fato).
        if is_local_chrome_enabled():
            lb = get_local_browser()
            if lb is not None:
                page = lb.new_page()
                if page is not None:
                    self._local_browser = lb
                    self._page = page
                    self._local_active = True
                    self._setup_xhr_intercept()
                    logger.info(
                        f"[{self.platform_name}] Modo browser local (Chrome logado) "
                        "— intercepta a API v4 nativa com o header anti-fraude"
                    )
                    return
            logger.warning(
                f"[{self.platform_name}] RAC_LOCAL_CHROME ligado mas o Chrome local "
                "não abriu — caindo para curl_cffi (provável 403 sem login/proxy)"
            )

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
        if n_cookies and not self._has_login_cookies:
            logger.warning(
                f"[{self.platform_name}] Sessão ANÔNIMA — nenhum cookie de login "
                f"({'/'.join(_LOGIN_COOKIE_NAMES)}). A API de busca retorna 403 "
                "sem login. No notebook, rode: RAC_LOCAL_CHROME=1 e faça login 1x "
                "com python scripts/setup_local_profile.py"
            )

    def _close(self) -> None:
        # Modo browser local: fecha SÓ a aba dedicada — a janela do Chrome é
        # compartilhada e fechada no fim da coleta (close_local_browser).
        if self._local_active:
            try:
                if self._page is not None and not self._page.is_closed():
                    self._page.close()
            except Exception:
                pass
            self._page = None
            self._xhr_page = None
            self._local_active = False
            return
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            pass
        self._session = None

    # ------------------------------------------------------------------
    # Modo browser local — navega a busca e intercepta a API v4 nativa
    # ------------------------------------------------------------------

    def _setup_xhr_intercept(self) -> None:
        """Registra 1 handler que captura as respostas de ``search_items``."""
        self._captured_search = []
        if self._xhr_page is self._page and self._page is not None:
            return
        self._xhr_page = self._page

        def handle_response(response):
            try:
                if _SEARCH_API_PATTERN not in response.url:
                    return
                if response.status != 200:
                    return
                data = json.loads(response.text())
                # Captura qualquer resposta de search_items que tenha a CHAVE
                # `items` — inclusive `items: []` (busca 0 resultados). Filtrar
                # por items truthy classificaria uma busca vazia legítima como
                # "sem resposta" (bloqueio) e incrementaria o circuit breaker.
                if isinstance(data, dict) and "items" in data:
                    self._captured_search.append(data)
            except Exception:
                pass

        try:
            self._page.on("response", handle_response)
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] Falha ao registrar handler XHR: {exc}")

    def _search_via_browser(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int,
    ) -> List[Dict[str, Any]]:
        """
        Coleta a busca DENTRO do Chrome logado, interceptando a API v4 nativa.

        A própria página da Shopee dispara ``search_items`` com o header
        anti-fraude ``af-ac-enc-dat`` (gerado pela JS dela). Ao interceptar a
        RESPOSTA, obtemos o mesmo JSON da API — sem precisar forjar o header,
        que era o que bloqueava o replay via curl_cffi.
        """
        lb = self._local_browser
        # Warm-up da home 1x (seta SPC_SI/cookies de sessão frescos).
        if lb is not None and self._page is not None:
            lb.warmup(self._page, f"{_SHOPEE_BASE}/", host_key="shopee")

        all_records: List[Dict[str, Any]] = []
        for page in range(page_limit):
            # A URL web usa `page` 0-indexado; a API responde `newest=page*60`.
            url = f"{_SHOPEE_BASE}/search?keyword={quote_plus(keyword)}&page={page}"
            self._captured_search = []
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            except Exception as exc:
                logger.warning(
                    f"[{self.platform_name}] goto busca '{keyword}' p{page+1} falhou: {exc}"
                )
                break

            # A SERP dispara search_items no load; scroll ajuda a garantir.
            captured = self._await_captured(timeout_s=12.0)
            if not captured:
                try:
                    for _ in range(4):
                        self._page.mouse.wheel(0, 700)
                        time.sleep(random.uniform(0.4, 0.9))
                except Exception:
                    pass
                captured = self._await_captured(timeout_s=8.0)

            if not captured:
                logger.warning(
                    f"[{self.platform_name}] '{keyword}' p{page+1}: nenhuma resposta "
                    "de search_items capturada (página pode exigir login/CAPTCHA)."
                )
                self._hard_blocked = True
                break

            # A SERP pode disparar VÁRIAS chamadas search_items (ads, prefetch,
            # resultados). Em vez de assumir que a 1ª é a boa, escolhe a resposta
            # que parseia MAIS registros — assim uma chamada de ads/vazia no topo
            # não mascara os resultados reais. Não concatena (duplicaria posições).
            best_records: List[Dict[str, Any]] = []
            best_items: List[dict] = []
            any_items = False
            for data in captured:
                data_items = data.get("items") or []
                if not data_items:
                    continue
                any_items = True
                recs = self._parse_items(data_items, keyword, keyword_category_map, page)
                if len(recs) > len(best_records):
                    best_records, best_items = recs, data_items

            if not any_items:
                logger.info(
                    f"[{self.platform_name}] Sem mais resultados (pág {page+1})."
                )
                break

            if not best_records:
                # Itens presentes mas nada parseou → estrutura da API mudou.
                # Dump para diagnóstico e marca como bloqueio (circuit breaker).
                first_with_items = next(
                    (d for d in captured if d.get("items")), captured[0]
                )
                self._dump_debug_response(keyword, page, first_with_items)
                self._hard_blocked = True
                break

            self._maybe_dump_hollow_parse(keyword, page, best_items, best_records)
            all_records.extend(best_records)
            logger.info(
                f"[{self.platform_name}] Pág {page+1}/{page_limit}: "
                f"{len(best_records)} produtos (browser local)"
            )

            if len(best_items) < _ITEMS_PER_PAGE:
                break
            time.sleep(random.uniform(*_INTER_PAGE_DELAY))

        return all_records

    def _await_captured(self, timeout_s: float) -> List[dict]:
        """Espera (até timeout) a interceptação de ao menos 1 search_items."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._captured_search:
                # Pequena folga para respostas paralelas chegarem ao buffer.
                time.sleep(0.6)
                return list(self._captured_search)
            try:
                self._page.wait_for_timeout(300)
            except Exception:
                time.sleep(0.3)
        return list(self._captured_search)

    # ------------------------------------------------------------------
    # Sessão / warm-up
    # ------------------------------------------------------------------

    def _load_session_cookies(self) -> int:
        """Aplica cookies da sessão capturada à sessão HTTP. Retorna a contagem."""
        try:
            from utils.session_grabber import load_session_meta
            meta = load_session_meta("shopee") or {}
            cookies = meta.get("cookies") or []
            self._session_ua = meta.get("userAgent") or ""
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] load_session falhou: {exc}")
            return 0

        names = set()
        for c in cookies:
            name, value = c.get("name"), c.get("value")
            if not name or value is None:
                continue
            names.add(name)
            domain = (c.get("domain") or "shopee.com.br").lstrip(".")
            try:
                self._session.cookies.set(name, value, domain=domain)
            except Exception:
                pass
            if name == "csrftoken":
                self._csrf_token = value
        self._has_login_cookies = any(n in names for n in _LOGIN_COOKIE_NAMES)
        return len(cookies)

    def _warmup(self) -> None:
        """GET na home para o anti-bot emitir cookies de sessão frescos."""
        try:
            kwargs: Dict[str, Any] = {
                "headers": {
                    "User-Agent": self._session_ua or _BASE_HEADERS["User-Agent"],
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
        if self._session_ua:
            headers["User-Agent"] = self._session_ua
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
                if resp.status_code == 403:
                    # 403 da API v4 é bloqueio DURO (sessão anônima/expirada ou
                    # anti-fraude) — não é transitório: re-tentar só queima
                    # tempo e marca ainda mais o IP/sessão.
                    logger.warning(
                        f"[{self.platform_name}] HTTP 403 (bloqueio duro) — sem retry. "
                        "Sessão sem login ou anti-fraude ativo."
                    )
                    self.captcha_hit = True
                    self._hard_blocked = True
                    return None
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
            self._hard_blocked = True
        else:
            logger.error(f"[{self.platform_name}] API erro {err} na pág {page+1}")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_seller(item: dict, asset: Optional[dict] = None) -> str:
        asset = asset or {}
        # Loja oficial (Shopee Mall): sinalizada no asset via seller_flag /
        # in_title_image_flags == "OFFICIAL_SHOP" (formato Jul/2026), ou pelo
        # legado is_official_shop.
        flag = asset.get("seller_flag")
        if isinstance(flag, dict) and flag.get("name") == "OFFICIAL_SHOP":
            return "Shopee Mall"
        for f in asset.get("in_title_image_flags") or []:
            if isinstance(f, dict) and f.get("name") == "OFFICIAL_SHOP":
                return "Shopee Mall"
        if item.get("is_official_shop"):
            return "Shopee Mall"
        if item.get("is_preferred_plus_seller") or item.get("shopee_verified"):
            return "Preferred+"
        return "3P"

    # Chaves de wrapper já vistas em respostas do search_items ao longo do tempo.
    # A Shopee troca o invólucro do item entre redesigns — antes era sempre
    # `item_basic`; versões mais novas usam `item`/`item_data` ou entregam os
    # campos do produto direto no wrapper (sem invólucro).
    _ITEM_WRAPPER_KEYS = ("item_basic", "item", "item_data", "basic", "data")

    @classmethod
    def _extract_item_payload(cls, wrapper: dict) -> Dict[str, Any]:
        """Localiza o dict do produto dentro de um item da resposta.

        Tenta as chaves de invólucro conhecidas e, se nenhuma existir, aceita o
        próprio wrapper quando ele já carrega os campos do produto (``itemid``/
        ``item_id``) — formato "flat" das versões mais novas da API. Retorna
        ``{}`` quando não reconhece a estrutura (dispara o dump de diagnóstico).
        """
        if not isinstance(wrapper, dict):
            return {}
        for key in cls._ITEM_WRAPPER_KEYS:
            payload = wrapper.get(key)
            if isinstance(payload, dict) and payload:
                # O invólucro pode conter um sub-invólucro (ex.: item_data.item).
                if not (payload.get("itemid") or payload.get("item_id")):
                    for inner in cls._ITEM_WRAPPER_KEYS:
                        nested = payload.get(inner)
                        if isinstance(nested, dict) and (
                            nested.get("itemid") or nested.get("item_id")
                        ):
                            return nested
                return payload
        # Formato "flat": o wrapper É o item.
        if wrapper.get("itemid") or wrapper.get("item_id"):
            return wrapper
        return {}

    # Chaves de NOME do produto já vistas no wrapper do search_items. A Shopee
    # renomeia/aninha esse campo entre redesigns — mantê-las num leque evita
    # produto NULL silencioso quando só a chave muda (ver regressão Jul/2026).
    _NAME_KEYS = ("name", "title", "item_name", "display_name", "product_name")
    # Chaves de PREÇO cruas (todas na escala/formato que _normalize_price trata).
    # `*_before_discount` fica por último — é o preço "de", só usado se não
    # houver o preço vigente.
    _PRICE_KEYS = (
        "price", "price_min", "price_max",
        "price_before_discount", "price_min_before_discount",
    )

    @classmethod
    def _extract_name(cls, item: dict, asset: Optional[dict] = None) -> Optional[str]:
        """Nome do produto: no formato Jul/2026 vive em
        ``item_card_displayed_asset.name``; no legado, direto no item."""
        for src in (asset or {}, item):
            if not isinstance(src, dict):
                continue
            for key in cls._NAME_KEYS:
                val = src.get(key)
                if isinstance(val, str) and val.strip():
                    return val
        return None

    @classmethod
    def _extract_raw_price(cls, item: dict, asset: Optional[dict] = None) -> Any:
        """Preço cru (escala ×100000). Formato Jul/2026: preço vive em
        ``item_data.item_card_display_price.price`` ou em
        ``item_card_displayed_asset.display_price.price``. Fallback: chaves
        diretas legadas e sub-dicts ``price_info``/``price_detail``."""
        asset = asset or {}
        # Novo: preço "vigente" nos blocos de display. `price` é o valor
        # exibido (com promo); só cai em `original_price` se não houver.
        for holder, keys in (
            (item.get("item_card_display_price"), ("price", "original_price")),
            (asset.get("display_price"), ("price",)),
        ):
            if isinstance(holder, dict):
                for key in keys:
                    val = holder.get(key)
                    if val:
                        return val
        # Legado: chave direta no item.
        for key in cls._PRICE_KEYS:
            val = item.get(key)
            if val:
                return val
        for holder_key in ("price_info", "price_detail", "item_price"):
            sub = item.get(holder_key)
            if isinstance(sub, dict):
                for key in cls._PRICE_KEYS:
                    val = sub.get(key)
                    if val:
                        return val
        return None

    @staticmethod
    def _extract_sold(item: dict, asset: Optional[dict] = None) -> Optional[str]:
        """Texto de volume de vendas (Tag Destaque). Formato Jul/2026:
        ``item_data.item_card_display_sold_count`` ou ``asset.sold_count.text``."""
        asset = asset or {}
        scd = item.get("item_card_display_sold_count")
        if isinstance(scd, dict):
            n = scd.get("historical_sold_count")
            if n is not None:  # 0 vendas é um valor válido, não "ausente"
                return f"{n} vendidos"
            txt = scd.get("historical_sold_count_text") or scd.get("display_sold_count_text")
            if txt:
                return txt
        sc = asset.get("sold_count")
        if isinstance(sc, dict) and sc.get("text"):
            return sc["text"]
        sold = item.get("historical_sold") or item.get("sold")
        return f"{sold} vendidos" if sold else None

    @staticmethod
    def _normalize_price(raw_price: Any) -> Optional[float]:
        """Converte o preço bruto da API para reais.

        Historicamente a Shopee guarda o preço × 100000 (ex.: 199900000 →
        R$ 1.999,00). Alguns formatos entregam já em reais, ou como string
        numérica SEM separadores ("199900000"). Heurística: só divide por
        100000 quando o número é grande o suficiente para ser a escala ×100000
        (um AC custa centenas/milhares de reais → valor cru na casa de 10^7–10^9).
        """
        # String numérica LIMPA → coage. Não removemos separadores: um decimal
        # BR ("1999,00") viraria "199900" e inflaria o preço 100x. Qualquer
        # string com vírgula/símbolo/separador cai no float() e vira None (seguro
        # por omissão) — a API v4 entrega o preço como inteiro sem separadores.
        if isinstance(raw_price, str):
            try:
                raw_price = float(raw_price.strip())
            except (ValueError, AttributeError):
                return None
        if not isinstance(raw_price, (int, float)) or isinstance(raw_price, bool):
            return None
        if not math.isfinite(raw_price):  # rejeita nan / inf / -inf
            return None
        if raw_price <= 0:
            return None
        if raw_price >= 1_000_000:
            return round(raw_price / 100000, 2)
        return round(float(raw_price), 2)

    @staticmethod
    def _count_missing_core(records: List[Dict[str, Any]]) -> int:
        """Nº de registros sem nome de produto OU sem preço — sinal de que a
        API mudou a chave de ``name``/``price`` (itens parseiam pelo id, mas os
        campos-núcleo vêm nulos). Alimenta o dump de diagnóstico."""
        miss = 0
        for r in records:
            if not r.get("Produto / SKU") or r.get("Preço (R$)") is None:
                miss += 1
        return miss

    def _maybe_dump_hollow_parse(
        self, keyword: str, page: int, items: List[dict], records: List[Dict[str, Any]]
    ) -> None:
        """Dispara o dump quando itens parseiam mas a maioria vem SEM nome/preço.

        O ``_dump_debug_response`` clássico só cobre "0 parsearam". Este cobre o
        modo insidioso da regressão Jul/2026: os itens parseiam pelo ``itemid``
        (seller/URL/rating vêm), porém ``name``/``price`` estão sob uma chave
        nova → ``produto``/``preco`` gravam NULL em silêncio. Aqui capturamos a
        amostra crua para mapear a chave nova. Uma amostra por processo basta.
        """
        if self._shape_dumped or not records:
            return
        missing = self._count_missing_core(records)
        # Maioria ESTRITA: metade exata não conta (não consome o dump único do
        # processo numa página só "meio oca"). Página de 1 registro oco dispara.
        if missing <= len(records) // 2:
            return
        logger.error(
            f"[{self.platform_name}] '{keyword}' p{page+1}: {missing}/{len(records)} "
            "itens parsearam SEM nome ou preço — a Shopee provavelmente trocou a "
            "chave de name/price no wrapper. Salvando amostra crua para mapear."
        )
        self._dump_debug_response(keyword, page, {"items": items})
        self._shape_dumped = True

    def _dump_debug_response(self, keyword: str, page: int, data: dict) -> None:
        """Salva a resposta crua quando itens vêm mas nada parseia.

        Grava em ``logs/shopee_debug_<keyword>_p<N>.json`` e loga as chaves reais
        do 1º wrapper — assim uma mudança de estrutura da API é diagnosticável
        sem re-rodar às cegas (padrão de dump de debug do projeto).
        """
        try:
            from pathlib import Path
            import re

            items = data.get("items") or []
            first_keys = sorted(items[0].keys()) if items and isinstance(items[0], dict) else []
            logger.error(
                f"[{self.platform_name}] '{keyword}' p{page+1}: API retornou "
                f"{len(items)} itens mas 0 parsearam — estrutura do wrapper mudou. "
                f"Chaves do 1º item: {first_keys}"
            )
            slug = re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_")[:40] or "kw"
            out = Path("logs") / f"shopee_debug_{slug}_p{page+1}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(items[:3], indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.error(
                f"[{self.platform_name}] Amostra (3 itens) salva em {out} — "
                "envie este arquivo para mapear o novo campo do produto."
            )
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] Falha ao gerar dump de debug: {exc}")

    def _parse_items(
        self,
        items: List[dict],
        keyword: str,
        keyword_category_map: dict,
        page: int,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        emitted = 0  # posição pelos itens EMITIDOS (pulados não deixam buraco)
        for wrapper in items:
            item = self._extract_item_payload(wrapper)
            # Bloco de apresentação (formato Jul/2026): carrega name/preço/
            # seller_flag/sold, irmão do item_data dentro do wrapper.
            asset = (
                wrapper.get("item_card_displayed_asset")
                if isinstance(wrapper, dict)
                else None
            )
            if not isinstance(asset, dict):
                asset = {}
            # Exige o item_data resolvido: o card Jul/2026 sempre o traz junto do
            # asset. Card só-asset (ex.: placeholder de ads) não tem ids/URL —
            # pular evita registro sem produto real consumindo posição de busca.
            if not item:
                continue

            name = self._extract_name(item, asset)
            price_float = self._normalize_price(self._extract_raw_price(item, asset))

            rating_info = item.get("item_rating") or {}
            rating = rating_info.get("rating_star")
            if rating is None:
                # Fallback formato novo: asset.rating.rating_text ("5.0").
                asset_rating = asset.get("rating") if isinstance(asset.get("rating"), dict) else {}
                try:
                    rating = float(asset_rating["rating_text"]) if asset_rating.get("rating_text") else None
                except (TypeError, ValueError):
                    rating = None
            rating_counts = rating_info.get("rating_count") or []
            review_count = sum(rating_counts) if isinstance(rating_counts, list) else None

            shop_data = item.get("shop_data") if isinstance(item.get("shop_data"), dict) else {}
            shop_name = (
                item.get("shop_name")
                or item.get("shopName")
                or shop_data.get("shop_name")
                or shop_data.get("name")
                # NÃO usar shop_location como fallback: é cidade/UF, não o nome
                # do vendedor — atribuiria seller falso ao buy box.
                or None
            )
            tipo_seller = self._classify_seller(item, asset)

            shop_rating = item.get("shop_rating")
            reputacao = f"{round(shop_rating, 2)}" if isinstance(shop_rating, (int, float)) else None

            tag_destaque = self._extract_sold(item, asset)

            shopid = item.get("shopid") or item.get("shop_id") or (
                wrapper.get("shopid") or wrapper.get("shop_id") if isinstance(wrapper, dict) else None
            )
            itemid = item.get("itemid") or item.get("item_id") or (
                wrapper.get("itemid") or wrapper.get("item_id") if isinstance(wrapper, dict) else None
            )
            url_produto = (
                f"{_SHOPEE_BASE}/product/{shopid}/{itemid}" if shopid and itemid else None
            )

            emitted += 1
            pos = page * _ITEMS_PER_PAGE + emitted
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
                # Marketplace puro: cada anúncio pertence a exatamente 1 loja.
                # None significaria "desconhecido"; aqui o valor é conhecido (=1).
                qtd_sellers=1,
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
        """Busca um termo na Shopee via API v4 (search_items).

        Caminho preferido (``RAC_LOCAL_CHROME=1``): coleta DENTRO do Chrome real
        logado e intercepta a chamada NATIVA de ``search_items`` — que já carrega
        o header anti-fraude ``af-ac-enc-dat``. É o que destrava a Shopee no
        notebook do usuário.

        Fallback (sem browser local): replay via curl_cffi + sessão capturada —
        best-effort, depende de cookies SPC_* válidos e instável sem proxy BR.

        Em ambos, extrai ``shop_name``, o tipo (Shopee Mall / Preferred+) e a
        reputação da loja quando presentes.

        Args:
            keyword: termo de busca.
            keyword_category_map: mapa keyword → categoria (para o registro).
            page_limit: nº máximo de páginas a coletar.

        Returns:
            Lista de registros normalizados (um por anúncio).
        """
        # Circuit breaker — coleta já abortada por bloqueios consecutivos:
        # pula a keyword sem gastar requests (todas retornariam 403).
        if self.collection_aborted:
            logger.debug(
                f"[{self.platform_name}] Coleta abortada (circuit breaker) — "
                f"pulando '{keyword}'"
            )
            return []

        self._hard_blocked = False

        # ── Caminho preferido: Chrome real logado (intercepta a API nativa) ──
        if self._local_active and self._page is not None:
            all_records = self._search_via_browser(
                keyword, keyword_category_map, page_limit
            )
            self._update_circuit_breaker(all_records)
            return all_records

        if not _HAS_CURL_CFFI:
            logger.warning(
                f"[{self.platform_name}] curl_cffi não instalado — coleta provavelmente "
                "será bloqueada. Instale: pip install curl_cffi"
            )

        all_records = []
        for page in range(page_limit):
            data = self._fetch_page(keyword, page)
            if data is None:
                break

            items = data.get("items") or []
            if not items:
                logger.info(f"[{self.platform_name}] Sem mais resultados (pág {page+1}).")
                break

            records = self._parse_items(items, keyword, keyword_category_map, page)
            if not records:
                # Itens vieram mas nada parseou → estrutura da API mudou.
                self._dump_debug_response(keyword, page, data)
                self._hard_blocked = True
                break
            self._maybe_dump_hollow_parse(keyword, page, items, records)
            all_records.extend(records)
            logger.info(
                f"[{self.platform_name}] Pág {page+1}/{page_limit}: "
                f"{len(records)} produtos (de {len(items)} retornados)"
            )

            if len(items) < _ITEMS_PER_PAGE:
                break
            time.sleep(random.uniform(*_INTER_PAGE_DELAY))

        self._update_circuit_breaker(all_records)
        return all_records

    def _update_circuit_breaker(self, all_records: List[Dict[str, Any]]) -> None:
        """Conta keywords seguidas bloqueadas; aborta a coleta após N.

        N keywords 100% bloqueadas = sessão/IP rejeitados de forma persistente
        (ou, no modo browser, página sem login / com CAPTCHA). Abortar evita
        gastar as keywords restantes.
        """
        if all_records:
            self._blocked_keyword_streak = 0
            return
        if not self._hard_blocked:
            return
        self._blocked_keyword_streak += 1
        if (
            self._blocked_keyword_streak >= _ABORT_AFTER_BLOCKED_KEYWORDS
            and not self.collection_aborted
        ):
            self.collection_aborted = True
            if self._local_active:
                hint = (
                    "no Chrome local a busca não retornou resultados — faça login "
                    "1x com python scripts/setup_local_profile.py e confira se a "
                    "Shopee abre normalmente (sem CAPTCHA)."
                )
            else:
                hint = (
                    "sessão sem login/expirada. No notebook use RAC_LOCAL_CHROME=1 "
                    "+ python scripts/setup_local_profile.py; na VM, proxy residencial BR."
                )
            logger.error(
                f"[{self.platform_name}] Circuit breaker: "
                f"{self._blocked_keyword_streak} keywords seguidas sem dados — "
                f"abortando a coleta Shopee (restantes serão puladas). {hint}"
            )
