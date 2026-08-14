"""
scrapers/magalu.py — Scraper da Magalu (magazineluiza.com.br).

Estratégia (Mai/2026, redesign após bypass falho do sensor.js Akamai):

  **Modo principal:** Playwright persistente — abre 1 browser real, mantém
  aberto durante toda a coleta. Cada keyword vira `page.goto('/busca/...')`,
  parser extrai `__NEXT_DATA__` do HTML renderizado.

  **Por quê:** Akamai usa 2 camadas:
    1. TLS fingerprint (JA3/JA4) — curl_cffi `impersonate=chrome124` bypassa
    2. sensor.js validation — _abck sai em modo "challenge"; só vira
       "validated" depois que o sensor.js POSTa fingerprint pro Akamai
       e ele aprova. Em headless, sensor.js detecta automação e MANTÉM
       challenge. Resultado: home passa, /busca/ continua bloqueando.

  Tentamos resolver com cookie injection no curl_cffi, mas Akamai vincula
  cookies à sessão TLS+fingerprint do browser que os emitiu — transferir
  cookies entre browser e curl_cffi não basta. O browser TEM que ficar
  aberto.

  **Custos:** ~3-5s por keyword (page.goto + render + scroll). Pra coletas
  de 31 keywords × 1-2 páginas, total ~3-6min — aceitável.

Extração (revisão Ago/2026, após a coleta fechar 40 keywords em 0 produtos):

  Três parsers rodam em cascata sobre o MESMO HTML que o browser entregou:

    1. `__NEXT_DATA__`  — payload do Pages Router (o mais rico)
    2. RSC flight       — `self.__next_f.push([1,"..."])` do App Router
    3. Cards do DOM     — `a[data-testid="product-card-container"]` & cia.

  Só o primeiro existia. Quando a Magalu mudou o que serve na SERP, a página
  chegava inteira no parser e saía como "0 produtos" — indistinguível de um
  bloqueio. O 3º parser lê o que o browser renderizou, então segura a coleta
  mesmo sem nenhum payload JSON embutido.

  **Muro de login:** a busca passou a redirecionar pra tela de identificação
  em sessões consideradas suspeitas. Ela vem HTTP 200 com >1 MB — passa por
  toda a detecção de bloqueio Akamai. É detectada explicitamente (URL de SSO
  ou ≥2 marcadores textuais sem nenhum card na página), o scraper tenta
  dispensar o modal e, se não der, aborta com a ação necessária no log
  (logar no perfil via `scripts/setup_local_profile.py --site magalu`).

Env vars:
  MAGALU_HEADLESS=false       → browser visível (default true). REQUIRED
                                em produção: o sensor.js do Akamai detecta
                                Chromium headless e mantém _abck em
                                "challenge" → /busca/ retorna 0 produtos.
                                Combine com xvfb-run no cron pra display
                                virtual (ver scripts/collect_*_linux.sh).
  MAGALU_FORCE_CURL=true      → desabilita browser e tenta só curl_cffi
                                (NÃO funcionará atualmente, deixado pra
                                futuro se Akamai mudar de comportamento)
  MAGALU_CDP_URL=http://...   → conecta a um Chrome real já aberto com
                                --remote-debugging-port (ver docs/
                                cdp_magalu_collection.md).

  Modo CDP — anti-detecção: o Playwright stock liga o domínio `Runtime` do
  CDP, que o sensor.js do Akamai detecta (mantém _abck em "challenge" mesmo
  num Chrome real). Por isso o scraper prefere o fork `rebrowser-playwright`,
  que oculta o `Runtime.enable`. Instale com:
    pip install rebrowser-playwright

Setup VM (uma vez):
  sudo apt-get install -y xvfb
"""

import json
import os
import random
import re
import time
from datetime import datetime
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
from scrapers import playwright_runtime
from scrapers.base import BaseScraper
from scrapers.local_browser import get_local_browser, is_local_chrome_enabled
from utils.text import parse_price


# Modo do patch de runtime do rebrowser-playwright. `addBinding` é o recomendado:
# obtém o execution context via Runtime.addBinding em vez de Runtime.enable, que
# o sensor.js do Akamai detecta (getter que só dispara com o domínio Runtime do
# CDP ligado → _abck preso em "challenge"). `setdefault` permite override por env.
os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")


def _import_sync_playwright() -> Tuple[Optional[Any], str]:
    """
    Resolve `sync_playwright`, preferindo o fork rebrowser-playwright.

    O rebrowser-playwright corrige o vazamento do `Runtime.enable` que o
    Akamai usa pra detectar automação via CDP — sem ele, o modo CDP é
    flagado mesmo conectado a um Chrome 100% real.

    Returns:
        (callable sync_playwright, flavor) ou (None, "") se nenhum instalado.
    """
    try:
        from rebrowser_playwright.sync_api import sync_playwright
        return sync_playwright, "rebrowser-playwright"
    except ImportError:
        pass
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright, "playwright"
    except ImportError:
        return None, ""


_ITEMS_PER_PAGE = 60  # Magalu mobile retorna ~60 itens por página

# Cache de sessão validada (cookies do Akamai). Reutilizar entre execuções
# evita o overhead de abrir o browser toda vez. Akamai valida o `_abck` por
# ~30min — usamos margem de 25min pra renovar antes de expirar.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_SESSION_CACHE = _PROJECT_ROOT / "data" / "magalu_session.json"
_SESSION_MAX_AGE_SEC = 25 * 60  # 25 minutos

# Perfil Chrome persistente. `launch_persistent_context` reusa esse diretório
# entre runs — cookies, histórico, fingerprint do CDP, tudo acumula. Akamai
# trata um perfil "antigo" como muito mais legítimo que um browser efêmero
# acabado de spawnar. Após a primeira validação visível, o cookie _abck
# fica gravado aqui e sensor.js libera direto nas próximas execuções
# (inclusive em headless, em alguns casos).
_BROWSER_PROFILE_DIR = _PROJECT_ROOT / "data" / "magalu_chrome_profile"

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

# Seletores candidatos do campo de busca — usados pra busca orgânica (digitar
# no input + Enter) em vez de `page.goto('/busca/...')`. Um goto direto chega
# ao Akamai com `Sec-Fetch-Site: none` e sem `Referer` — assinatura clássica
# de bot. Digitar no campo gera uma navegação same-origin com Referer da home.
_SEARCH_INPUT_SELECTORS = (
    'input[data-testid="input-search"]',
    'input[data-testid="search-input"]',
    'input[type="search"]',
    'input[name="q"]',
    'form[role="search"] input',
    'header input[type="text"]',
)

# ── Login wall (Ago/2026) ────────────────────────────────────────────────
# A Magalu passou a interpor uma tela/modal de identificação antes da SERP
# quando a sessão é considerada suspeita (ou quando o perfil do Chrome está
# deslogado). Essa página vem com HTTP 200 e >50 KB — passa por TODA a
# detecção de bloqueio Akamai, chega no parser, que não acha produto nenhum
# e devolve 0 itens sem dizer o porquê. Detectar explicitamente é o que
# transforma "0 produtos" num diagnóstico acionável.
_LOGIN_URL_MARKERS = (
    "/login",
    "/entrar",
    "/sso/",
    "/autenticacao",
    "id.magazineluiza.com.br",
    "sso.magazineluiza.com.br",
    "autenticacao.magazineluiza.com.br",
    "/cliente/entrar",
)

# Marcadores textuais da tela de identificação. Exigimos ≥2 porque o header
# padrão da Magalu já traz "Entre ou cadastre-se" em TODA página — um único
# hit não distingue SERP normal de muro de login.
_LOGIN_HTML_PATTERNS = (
    "entre ou cadastre-se",
    "entre na sua conta",
    "acesse sua conta",
    "identifique-se",
    "faça login",
    "esqueci minha senha",
    'name="password"',
    'type="password"',
    'data-testid="login',
)

# Botões que fecham o modal de identificação sem exigir credenciais.
_LOGIN_DISMISS_SELECTORS = (
    '[data-testid="modal-close"]',
    '[data-testid="close-button"]',
    '[data-testid="close"]',
    'button[aria-label="Fechar"]',
    'button[aria-label="fechar"]',
    'button[title="Fechar"]',
    'button[aria-label*="echar"]',
)

# ── Parser de DOM (Ago/2026, atualizado Dez/2026) ────────────────────────
# Fallback para quando o payload JSON não está mais no HTML (migração pro
# App Router do Next.js remove o <script id="__NEXT_DATA__"> e serializa o
# estado como RSC flight em `self.__next_f.push`). Seletores confirmados em
# produção pelo scraper Node (`magalu_shopee/src/config/selectors.ts`).
#
# ATUALIZADO: Dez/2026 - Magalu mudou layout dos cards na SERP.
# Novos seletores adicionados para cobrir variações do layout.
_DOM_CARD_SELECTORS = (
    # Seletores primários (data-testid - preferenciais)
    'a[data-testid="product-card-container"]',
    'li[data-testid="product-card"] a[href*="/p/"]',
    '[data-testid="product-card"] a[href*="/p/"]',
    '[data-testid="product-list"] a[href*="/p/"]',
    
    # Seletores secundários (estrutura semântica - fallback)
    'article a[href*="/p/"]',
    'div[class*="ProductCard"] a[href*="/p/"]',
    'div[class*="product-card"] a[href*="/p/"]',
    'div[class*="Card"] a[href*="/p/"]',
    
    # Seletores terciários (lista de produtos - último recurso)
    'ul[class*="ProductList"] li a[href*="/p/"]',
    'div[class*="ProductList"] a[href*="/p/"]',
    'section[class*="Products"] a[href*="/p/"]',
)

_DOM_TITLE_SELECTORS = (
    # Prioridade: data-testid
    '[data-testid="product-title"]',
    # Secundário: headings semânticos
    'h2', 'h3', 'h4',
    # Terciário: classes comuns
    '[class*="ProductTitle"]',
    '[class*="product-name"]',
    '[class*="ProductName"]',
    '[class*="title"]',
)

_DOM_PRICE_SELECTORS = (
    # Prioridade: data-testid
    '[data-testid="price-value"]',
    '[data-testid="price-best"]',
    '[data-testid="best-price"]',
    '[data-testid="current-price"]',
    '[data-testid="price"]',
    # Secundário: classes comuns
    '[class*="PriceValue"]',
    '[class*="price-value"]',
    '[class*="Price"]',
    '[class*="price"]',
)

_DOM_RATING_SELECTOR = '[data-testid="review"]'

_DOM_SELLER_SELECTORS = (
    # Prioridade: data-testid
    '[data-testid="seller-name"]',
    '[data-testid="product-seller"]',
    # Secundário: classes comuns
    '[class*="SellerName"]',
    '[class*="seller-name"]',
    '[class*="Seller"]',
)
_DOM_SPONSORED_PATTERNS = ("patrocinado", "patrocinada", "anúncio", "anuncio", "publicidade")

# Regex de preço BR dentro do texto do card (último recurso do parser DOM).
_DOM_PRICE_RE = re.compile(r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}")

# Erros de rede do Chrome que NÃO são bloqueio anti-bot — internet caiu,
# DNS falhou, proxy derrubou a conexão. Contá-los como bloqueio Akamai
# manda o diagnóstico (e o operador) pro lado errado.
_NETWORK_ERROR_MARKERS = (
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_NETWORK_CHANGED",
    "ERR_PROXY_CONNECTION_FAILED",
    "Connection was reset",
)

# Causas possíveis de uma keyword voltar vazia — alimentam a mensagem do
# circuit breaker com a ação certa em vez de sempre culpar o Akamai.
_REASON_AKAMAI = "akamai"
_REASON_LOGIN = "login"
_REASON_LAYOUT = "layout"
_REASON_NETWORK = "rede"
_REASON_UNKNOWN = "desconhecida"

# Circuit breaker: após N keywords seguidas 100% bloqueadas, aborta a coleta
# Magalu inteira em vez de insistir nas ~31 keywords (cada uma ~15-20s de
# navegação garantidamente bloqueada — ~9min de trabalho inútil por execução).
_ABORT_AFTER_BLOCKED_KEYWORDS = 5

# Muro de login não passa com insistência: aborta mais cedo, porque a ação
# necessária é humana (logar no Chrome do perfil dedicado).
_ABORT_AFTER_LOGIN_KEYWORDS = 2

# curl_cffi só serve quando o browser está fora do ar. Após N falhas
# seguidas, desliga o fallback: cada tentativa custa ~45s de timeout e o
# Akamai não aceita cookies de browser numa sessão TLS diferente.
_CFFI_FAILURES_BEFORE_DISABLE = 2


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
        self._session_validated: bool = False  # True após cookies validados aplicados

        # ── Browser persistente (modo principal Mai/2026) ────────────────
        # Mantém Playwright aberto durante TODA a coleta — buscas viram
        # navigate dentro dele em vez de HTTP request via curl_cffi (que
        # falha pq Akamai não aceita cookies sem fingerprint browser).
        self._pw_handle = None         # sync_playwright() handle
        self._pw_browser = None        # Browser instance
        self._pw_context = None        # BrowserContext
        self._pw_page = None           # Page (reusada entre keywords)
        self._browser_mode: bool = True  # True = usa Playwright pra cada busca
        self._is_cdp: bool = False     # True = conectado a Chrome externo via CDP (não fechar)
        self._is_local: bool = False   # True = usa o Chrome local compartilhado (não fechar)
        self._local_browser = None     # handle do LocalBrowser compartilhado (modo local)

        # Contador de bloqueios consecutivos pra disparar revalidação de sessão
        # (visita home + interação humana pra sensor.js promover _abck).
        self._consecutive_blocks: int = 0
        self._blocks_before_reval: int = 3

        # Circuit breaker — aborta a coleta inteira após N keywords seguidas
        # bloqueadas, evitando ~9min de navegação inútil por execução quando
        # o Akamai está negando a rota /busca/ de forma persistente.
        self._blocked_keyword_streak: int = 0
        self.collection_aborted: bool = False

        # Causa da última keyword vazia — decide a mensagem (e a ação) do
        # circuit breaker: Akamai, muro de login, layout novo ou rede.
        self._last_failure_reason: str = _REASON_UNKNOWN
        self._login_required: bool = False

        # curl_cffi é só rede de segurança pra quando o browser cai. Depois de
        # algumas falhas seguidas ele é desligado (cada tentativa custa ~45s).
        self._cffi_failures: int = 0
        self._cffi_disabled: bool = False

        # Resolução de headless (prioridade: env var > CLI --no-headless > True)
        env_headless = os.getenv("MAGALU_HEADLESS")
        if env_headless is not None:
            self._browser_headless: bool = env_headless.lower() != "false"
        else:
            # `headless=False` (--no-headless do CLI) → browser visível.
            # Recomendado pra Magalu local: browser visível passa muito
            # mais fácil pelo sensor.js do Akamai.
            self._browser_headless = headless

        if os.getenv("MAGALU_FORCE_CURL", "").lower() == "true":
            self._browser_mode = False  # modo legado, provavelmente falha

    # ------------------------------------------------------------------
    # Cache de sessão validada (cookies Akamai)
    # ------------------------------------------------------------------

    @staticmethod
    def _load_cached_session() -> Optional[List[Dict[str, Any]]]:
        """Carrega cookies salvos se cache estiver fresco (<25min)."""
        if not _BROWSER_SESSION_CACHE.exists():
            return None
        try:
            data = json.loads(_BROWSER_SESSION_CACHE.read_text(encoding="utf-8"))
            saved_at = datetime.fromisoformat(data["saved_at"])
            age_sec = (datetime.now() - saved_at).total_seconds()
            if age_sec > _SESSION_MAX_AGE_SEC:
                logger.info(
                    f"[Magalu] Cache de sessão expirado ({age_sec / 60:.1f}min, "
                    f"limite {_SESSION_MAX_AGE_SEC / 60:.0f}min)"
                )
                return None
            logger.info(
                f"[Magalu] Reusando sessão validada (idade {age_sec / 60:.1f}min, "
                f"{len(data['cookies'])} cookies)"
            )
            return data["cookies"]
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.warning(f"[Magalu] Cache de sessão corrompido ({exc}) — ignorando")
            return None

    @staticmethod
    def _save_cached_session(cookies: List[Dict[str, Any]]) -> None:
        """Salva cookies em disco com timestamp ISO."""
        try:
            _BROWSER_SESSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _BROWSER_SESSION_CACHE.write_text(
                json.dumps({
                    "saved_at": datetime.now().isoformat(),
                    "cookies": cookies,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"[Magalu] Sessão validada salva em {_BROWSER_SESSION_CACHE}")
        except Exception as exc:
            logger.warning(f"[Magalu] Falha ao salvar cache de sessão: {exc}")

    @staticmethod
    def _invalidate_cached_session() -> None:
        """Remove o cache de sessão (forçar revalidação)."""
        try:
            if _BROWSER_SESSION_CACHE.exists():
                _BROWSER_SESSION_CACHE.unlink()
                logger.info("[Magalu] Cache de sessão invalidado")
        except Exception as exc:
            logger.debug(f"[Magalu] Falha ao remover cache: {exc}")

    def _open_persistent_browser(self) -> bool:
        """
        Abre Playwright e mantém o browser aberto durante toda a coleta.
        Visita home + faz uma busca de calibração pra Akamai aceitar `/busca/`.

        Retorna True se browser está aberto e operacional.
        """
        sync_playwright, pw_flavor = _import_sync_playwright()
        if sync_playwright is None:
            logger.error(
                "[Magalu] Playwright não instalado. Execute: "
                "pip install rebrowser-playwright && "
                "python -m rebrowser_playwright install chromium"
            )
            return False
        if playwright_runtime.is_active():
            # Já há handle vivo na thread (Chrome local/outro scraper): é ele
            # que vamos usar — anunciar outro flavor confundiria o diagnóstico.
            pw_flavor = playwright_runtime.active_flavor()
        if pw_flavor == "rebrowser-playwright":
            logger.info(
                "[Magalu] Playwright: rebrowser-playwright "
                f"(runtime fix={os.environ['REBROWSER_PATCHES_RUNTIME_FIX_MODE']}) "
                "— Runtime.enable oculto do sensor.js Akamai"
            )
        else:
            logger.warning(
                "[Magalu] Playwright stock — modo CDP detectável pelo Akamai "
                "(Runtime.enable vaza). Instale: pip install rebrowser-playwright"
            )

        ua = _DESKTOP_UA_BY_CHROME.get(
            self._impersonate, _DESKTOP_UA_BY_CHROME["chrome124"]
        )

        cdp_url = os.getenv("MAGALU_CDP_URL", "").strip()

        # ── Modo local compartilhado (RAC_LOCAL_CHROME, sem CDP) ──────────
        # No notebook do usuário, reusa o MESMO Chrome persistente logado que
        # Shopee/Casas Bahia usam (perfil dedicado, IP residencial). Sem porta
        # de debug (o Chrome 136+ ignora --remote-debugging-port no perfil
        # padrão) e sem abrir um segundo browser. O contexto é compartilhado e
        # fechado no fim da coleta — aqui só abrimos uma aba dedicada.
        channel_used = None
        profile_is_fresh = False
        if not cdp_url and is_local_chrome_enabled():
            lb = get_local_browser()
            if lb is not None and lb.is_alive():
                self._pw_context = lb.context
                self._local_browser = lb
                self._is_local = True
                channel_used = "local-shared"
                # NÃO adquire handle próprio — o LocalBrowser já gerencia o
                # handle compartilhado via playwright_runtime. Adquirir aqui
                # cria referência duplicada e causa "session closed" quando
                # um dos módulos libera o handle antes do outro.
                logger.info(
                    "[Magalu] Chrome local compartilhado (perfil logado) — "
                    "sem CDP/porta de debug"
                )
            else:
                logger.warning(
                    "[Magalu] RAC_LOCAL_CHROME ligado mas o Chrome local não "
                    "abriu — caindo para launch próprio"
                )

        # Handle compartilhado por thread — ver scrapers/playwright_runtime.
        # SÓ adquire se NÃO estiver usando Chrome local (ele já tem o handle).
        if not self._is_local:
            self._pw_handle, _ = playwright_runtime.acquire(prefer_rebrowser=True)
            if self._pw_handle is None:
                logger.error("[Magalu] Falha ao iniciar Playwright.")
                return False

        # ── Modo CDP: conecta ao Chrome real do usuário via DevTools Protocol ──
        # Quando MAGALU_CDP_URL está setado (ex: http://localhost:9222), conecta
        # a um Chrome já aberto pelo usuário com --remote-debugging-port. Usa o
        # Chrome real + IP residencial + cookies/histórico acumulados de meses
        # de navegação — combinação que Akamai aceita como "usuário humano".
        if self._is_local:
            pass  # contexto já resolvido acima
        elif cdp_url:
            logger.info(f"[Magalu] Conectando via CDP a {cdp_url} ...")
            try:
                self._pw_browser = self._pw_handle.chromium.connect_over_cdp(cdp_url)
            except Exception as exc:
                logger.error(
                    f"[Magalu] Falha ao conectar CDP em {cdp_url}: {exc}. "
                    f"O Chrome está aberto com --remote-debugging-port=9222?"
                )
                self._close_persistent_browser()
                return False

            if not self._pw_browser.contexts:
                logger.error("[Magalu] Chrome CDP não tem nenhum contexto aberto.")
                self._close_persistent_browser()
                return False
            self._pw_context = self._pw_browser.contexts[0]
            self._is_cdp = True
            channel_used = "cdp"
            profile_is_fresh = False
            logger.info(
                f"[Magalu] CDP conectado ({len(self._pw_browser.contexts)} ctx, "
                f"{len(self._pw_context.pages)} page(s) existentes)"
            )
        else:
            mode = "visible" if not self._browser_headless else "headless"
            logger.info(f"[Magalu] Abrindo browser persistente ({mode})...")

            _BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            profile_is_fresh = not any(_BROWSER_PROFILE_DIR.iterdir())
            logger.info(
                f"[Magalu] Profile dir: {_BROWSER_PROFILE_DIR} "
                f"({'NOVO' if profile_is_fresh else 'existente'})"
            )

            channel_used = None
            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-infobars",
            ]
            for channel in ("chrome", "msedge", None):
                try:
                    self._pw_context = self._pw_handle.chromium.launch_persistent_context(
                        user_data_dir=str(_BROWSER_PROFILE_DIR),
                        headless=self._browser_headless,
                        channel=channel,
                        user_agent=ua,
                        viewport={"width": 1366, "height": 768},
                        locale="pt-BR",
                        timezone_id="America/Sao_Paulo",
                        args=launch_args,
                    )
                    channel_used = channel or "chromium"
                    break
                except Exception as exc:
                    logger.debug(f"[Magalu] Falha launch channel={channel}: {exc}")
                    continue

            if self._pw_context is None:
                logger.error(
                    "[Magalu] Não foi possível iniciar nenhum browser. Rode: "
                    "python -m playwright install chromium"
                )
                self._close_persistent_browser()
                return False

        # Stealth JS — esconde marcadores de automação do sensor.js.
        # SÓ no modo launch (Chromium efêmero do Playwright). Em Chrome real
        # (CDP ou local) é contraproducente: o browser já tem navigator.webdriver,
        # window.chrome (com .app + funções nativas) e navigator.plugins
        # legítimos — sobrescrevê-los por stubs JS deixa o fingerprint MAIS
        # sintético (descritores não-nativos detectáveis), não menos.
        if not self._is_cdp and not self._is_local:
            self._pw_context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => false});
                try { delete navigator.__proto__.webdriver; } catch(_) {}
                window.chrome = {
                    runtime: { onConnect: {addListener: () => {}}, onMessage: {addListener: () => {}} },
                    loadTimes: () => ({}),
                    csi: () => ({}),
                };
                Object.defineProperty(navigator, 'plugins', {
                    get: () => { const a = [1,2,3,4,5]; a.item = () => null; return a; }
                });
                Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR', 'pt', 'en-US', 'en']});
                Object.defineProperty(document, 'hidden', {get: () => false});
                Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});
            """)

        # Chrome real (CDP ou local): NUNCA sequestra uma aba existente — o
        # Chrome é do usuário/compartilhado. Uma aba dedicada isola a coleta do
        # uso normal do browser e das outras plataformas (Shopee/CB).
        if self._is_cdp or self._is_local:
            try:
                # No modo local a aba vem do LocalBrowser: ele reconecta o CDP
                # se a janela tiver morrido entre o `is_alive()` e agora. Pedir
                # direto ao contexto guardado abortaria a Magalu inteira num
                # caso que o próprio projeto já sabe recuperar.
                if self._is_local and self._local_browser is not None:
                    self._pw_page = self._local_browser.new_page()
                    if self._pw_page is None:
                        raise RuntimeError("Chrome compartilhado indisponível")
                    self._pw_context = self._local_browser.context
                else:
                    self._pw_page = self._pw_context.new_page()
            except Exception as exc:
                logger.error(f"[Magalu] Falha ao abrir aba dedicada no Chrome real: {exc}")
                self._close_persistent_browser()
                return False
        # Persistent context já vem com 1 página aberta (about:blank) —
        # reusa em vez de criar nova, pra economizar 1 sinal de automação.
        elif self._pw_context.pages:
            self._pw_page = self._pw_context.pages[0]
        else:
            self._pw_page = self._pw_context.new_page()
        self._pw_page.set_default_timeout(45_000)

        logger.info(
            f"[Magalu] Browser persistente aberto (channel={channel_used}, "
            f"profile={'novo' if profile_is_fresh else 'reusado'})"
        )

        # 1ª navegação: home — gera _abck inicial (challenge) e roda sensor.js
        try:
            self._pw_page.goto(
                _MAGALU_DESKTOP_HOME,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except Exception as exc:
            logger.warning(
                f"[Magalu] Home goto warn: {exc} — prosseguindo (sensor pode estar rodando)"
            )

        # Emula interação humana — Akamai pontua mouse/scroll
        try:
            for _ in range(3):
                self._pw_page.mouse.move(
                    random.randint(150, 1200), random.randint(150, 700)
                )
                time.sleep(random.uniform(0.3, 0.7))
            self._pw_page.mouse.wheel(0, 400)
            time.sleep(random.uniform(0.8, 1.5))
            self._pw_page.mouse.wheel(0, 200)
            time.sleep(random.uniform(0.6, 1.2))
        except Exception:
            pass

        # Espera o _abck validar (poll cookie até ~25s). Sem isso, /busca/ 403.
        try:
            self._pw_page.wait_for_function(
                """() => {
                    const m = document.cookie.match(/_abck=([^;]+)/);
                    return m && /~0~/.test(decodeURIComponent(m[1]));
                }""",
                timeout=25_000,
            )
            logger.info("[Magalu] _abck validado pelo sensor.js ✓")
        except Exception:
            logger.warning(
                "[Magalu] _abck não validou em 25s — buscas podem falhar"
            )

        # Calibração: navega pra busca dentro do mesmo browser. Isso força
        # Akamai a promover o cookie pra rota /busca/ — e, de quebra, é o
        # momento mais barato pra descobrir que a coleta NÃO vai funcionar.
        # O critério antigo (só tamanho do HTML) dava "OK" pra uma tela de
        # login de 1,1 MB; agora a calibração só passa se der pra EXTRAIR
        # produto da página.
        try:
            cal_url = f"{_MAGALU_MOBILE_BASE}/busca/ar+condicionado/"
            self._pw_page.goto(cal_url, wait_until="domcontentloaded", timeout=30_000)
            # A SERP hidrata client-side — sem esperar o card, a calibração
            # pode julgar uma página ainda montando como "sem produtos".
            try:
                self._pw_page.wait_for_selector(
                    'a[href*="/p/"], [data-testid="product-card"]',
                    timeout=12_000,
                )
            except Exception:
                pass
            time.sleep(random.uniform(2.0, 3.5))
            html = self._safe_content()
            self._calibrate_from_html(html)
        except Exception as exc:
            logger.warning(f"[Magalu] Busca de calibração falhou: {exc}")

        # Tenta extrair BUILD_ID daqui pra futuras chamadas
        try:
            html = self._safe_content()
            build_id = self._extract_build_id(html)
            if build_id:
                self._build_id = build_id
                logger.debug(f"[Magalu] BUILD_ID via browser: {build_id}")
        except Exception:
            pass

        # Tenta copiar cookies pro curl_cffi também (futuro speed-up)
        try:
            cookies = self._pw_context.cookies()
            applied = self._apply_cookies_to_cffi(cookies)
            abck_value = next(
                (c["value"] for c in cookies if c["name"] == "_abck"), ""
            )
            abck_status = "validated" if "~0~" in abck_value else "challenge"
            logger.info(
                f"[Magalu] {applied}/{len(cookies)} cookies copiados pro cffi "
                f"(_abck status={abck_status})"
            )
        except Exception:
            pass

        return True

    def _calibrate_from_html(self, html: str) -> bool:
        """
        Valida a busca de calibração EXTRAINDO produtos, não medindo bytes.

        Uma tela de identificação da Magalu tem >1 MB de HTML e passava pelo
        antigo `len(html) > 50_000` como "sessão pronta" — a coleta então
        rodava 40 keywords pra descobrir o óbvio. Aqui o problema aparece
        aos 40 segundos de execução, com o motivo certo.

        Args:
            html: HTML da SERP de calibração.

        Returns:
            True se a página rende produtos (coleta deve funcionar).
        """
        try:
            current_url = self._pw_page.url if self._pw_page else ""
        except Exception:
            current_url = ""

        if self._looks_like_login_wall(html, current_url):
            if not self._dismiss_login_wall():
                self._flag_login_required("busca de calibração")
                self._dump_block_html(html, "login_calibracao")
                return False
            html = self._safe_content()
            if not html:
                return False

        for parser_name, parser in (
            ("__NEXT_DATA__", self._products_from_next_data),
            ("RSC/App Router", self._find_products_in_rsc),
            ("DOM", self._parse_dom_cards),
        ):
            try:
                products = parser(html)
            except Exception:
                continue
            if products:
                logger.info(
                    f"[{self.platform_name}] Busca de calibração OK "
                    f"({len(html):,} bytes, {len(products)} produtos via "
                    f"{parser_name}) — sessão pronta"
                )
                return True

        if self._has_product_markup(html):
            self._last_failure_reason = _REASON_LAYOUT
            logger.error(
                f"[{self.platform_name}] Calibração: a SERP renderizou "
                f"({len(html):,} bytes) mas nenhum parser extraiu produtos — "
                "layout novo. HTML salvo pra atualizar os seletores."
            )
            self._dump_block_html(html, "layout_calibracao")
        else:
            logger.warning(
                f"[{self.platform_name}] Busca de calibração suspeita "
                f"(len={len(html):,}, sem produtos) — as keywords podem "
                "voltar vazias"
            )
            self._dump_block_html(html, "calibracao")
        return False

    def _close_persistent_browser(self) -> None:
        """Fecha o browser persistente limpando recursos.

        No modo CDP (conectado a Chrome externo), NÃO fecha contexto/browser —
        é o Chrome do usuário, deixa aberto pra próximo run.
        """
        # Modo local compartilhado: fecha SÓ a aba dedicada. O contexto (janela)
        # é compartilhado com Shopee/CB e fechado no fim da coleta pelo
        # LocalBrowser. NÃO zeramos _is_local aqui — ele é usado no
        # _release_pw_handle() pra saber se deve liberar o handle.
        if self._is_local:
            try:
                if self._pw_page and not self._pw_page.is_closed():
                    self._pw_page.close()
            except Exception:
                pass
            self._pw_page = None
            self._pw_context = None
            self._local_browser = None
            # _is_local permanece True até o fim do __exit__ — assim o
            # _release_pw_handle() sabe que NÃO deve chamar playwright_runtime.release()
            return

        if self._is_cdp:
            # CDP: fecha SÓ a aba dedicada da coleta e desconecta — nunca o
            # Chrome do usuário (contexto/browser ficam abertos pro próximo run)
            try:
                if self._pw_page and not self._pw_page.is_closed():
                    self._pw_page.close()
            except Exception:
                pass
            try:
                if self._pw_browser:
                    self._pw_browser.close()  # close() em CDP-browser apenas desconecta
            except Exception:
                pass
            self._pw_page = None
            self._pw_context = None
            self._pw_browser = None
            self._release_pw_handle()
            self._is_cdp = False
            return

        try:
            if self._pw_page:
                self._pw_page.close()
        except Exception:
            pass
        try:
            if self._pw_context:
                self._pw_context.close()
        except Exception:
            pass
        try:
            if self._pw_browser:
                self._pw_browser.close()
        except Exception:
            pass
        self._pw_page = None
        self._pw_context = None
        self._pw_browser = None
        self._release_pw_handle()

    def _release_pw_handle(self) -> None:
        """Devolve a referência do handle compartilhado do Playwright.

        Nunca chama ``stop()`` direto: o handle é da thread, não deste
        scraper — pará-lo com o Chrome local ainda conectado invalidaria as
        abas dos outros scrapers.

        No modo local (_is_local=True), NÃO libera o handle — ele pertence ao
        LocalBrowser e será liberado no fim da coleta por ele. Liberar aqui
        causa "session closed" nos outros scrapers que compartilham o browser.
        """
        if self._pw_handle is None:
            return
        # Modo local: o handle é gerenciado pelo LocalBrowser, não por nós.
        if self._is_local:
            self._pw_handle = None
            return
        self._pw_handle = None
        playwright_runtime.release()

    def _safe_content(self, page: Any = None) -> str:
        """
        `page.content()` com uma segunda chance.

        O rebrowser-playwright perde o execution context quando o evaluate
        cai no meio de uma navegação ("Cannot find context with specified
        id" / "session closed"). A página está viva — foi o timing. Sem o
        retry, a tentativa da keyword inteira era descartada por causa de
        uma janela de milissegundos.

        Args:
            page: página a ler (default: a aba de coleta).

        Returns:
            HTML da página, ou "" se as duas tentativas falharem.
        """
        target = page or self._pw_page
        if target is None:
            return ""
        for attempt in (1, 2):
            try:
                return target.content()
            except Exception as exc:
                if attempt == 2:
                    logger.warning(
                        f"[{self.platform_name}] content() falhou 2×: {exc}"
                    )
                else:
                    time.sleep(1.5)
        return ""

    def _page_is_dead(self) -> bool:
        """True se a aba de coleta não existe mais (fechada/crashed)."""
        if self._pw_page is None:
            return True
        try:
            return self._pw_page.is_closed()
        except Exception:
            return True

    def _revive_page(self) -> bool:
        """
        Recria a aba de coleta quando a atual morreu — ex.: usuário fechou a
        aba/janela do Chrome CDP no meio da coleta ('Target page, context or
        browser has been closed'). Sem isso, TODAS as keywords restantes caíam
        pro fallback curl_cffi, bem mais frágil contra o Akamai.

        Returns:
            True se há uma aba viva pronta pra navegar.
        """
        if not self._page_is_dead():
            return True

        # 1) Nova aba no mesmo contexto (Chrome ainda aberto, só a aba fechou)
        try:
            if self._pw_context is not None:
                self._pw_page = self._pw_context.new_page()
                self._pw_page.set_default_timeout(45_000)
                logger.warning(
                    f"[{self.platform_name}] Aba de coleta foi fechada — "
                    "nova aba criada no mesmo Chrome"
                )
                return True
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] new_page falhou: {exc}")

        # 1.5) Chrome local compartilhado: pede a aba ao LocalBrowser, que
        #      reconecta (ou reabre) a janela quando ela morreu por inteiro —
        #      o contexto guardado aqui já está morto nesse caso.
        if self._is_local:
            lb = get_local_browser()
            page = lb.new_page() if lb is not None else None
            if page is not None:
                self._local_browser = lb
                self._pw_context = lb.context
                self._pw_page = page
                self._pw_page.set_default_timeout(45_000)
                logger.warning(
                    f"[{self.platform_name}] Chrome compartilhado reaberto — "
                    "nova aba de coleta"
                )
                return True
            # LocalBrowser não abriu — cai pro fallback curl_cffi, mas mantém
            # _is_local=True pra não tentar liberar handle que nunca tivemos.
            self._pw_context = None
            self._local_browser = None

        # 2) Modo CDP: reconecta ao Chrome (janela pode ter sido reaberta)
        cdp_url = os.getenv("MAGALU_CDP_URL", "").strip()
        if self._is_cdp and cdp_url and self._pw_handle is not None:
            try:
                self._pw_browser = self._pw_handle.chromium.connect_over_cdp(cdp_url)
                if self._pw_browser.contexts:
                    self._pw_context = self._pw_browser.contexts[0]
                    self._pw_page = self._pw_context.new_page()
                    self._pw_page.set_default_timeout(45_000)
                    logger.warning(
                        f"[{self.platform_name}] Reconectado ao Chrome CDP "
                        "após desconexão"
                    )
                    return True
            except Exception as exc:
                logger.warning(f"[{self.platform_name}] Reconexão CDP falhou: {exc}")

        self._pw_page = None
        logger.warning(
            f"[{self.platform_name}] Browser indisponível — seguindo só com "
            "curl_cffi (fallback). Reabra o Chrome do perfil dedicado: "
            "python scripts/setup_local_profile.py --site magalu"
        )
        return False

    def _find_search_input(self) -> Optional[Any]:
        """Retorna o ElementHandle do campo de busca visível, ou None."""
        if not self._pw_page:
            return None
        for sel in _SEARCH_INPUT_SELECTORS:
            try:
                el = self._pw_page.query_selector(sel)
                if el and el.is_visible():
                    return el
            except Exception:
                continue
        return None

    def _organic_search(self, keyword: str) -> bool:
        """
        Executa a busca como um humano: localiza o campo de busca, digita a
        keyword com cadência realista e tecla Enter.

        Por quê: `page.goto('/busca/...')` chega ao Akamai com
        `Sec-Fetch-Site: none` e sem `Referer` — assinatura de bot que faz a
        rota /busca/ pontuar acima do limite e ser negada (a home, mais
        leniente, passa). Digitar no campo gera uma navegação same-origin
        com Referer da home, indistinguível de um usuário real.

        Retorna True se a página resultante é uma SERP /busca/.
        """
        page = self._pw_page
        if page is None:
            return False

        inp = self._find_search_input()
        if inp is None:
            # Página atual sem campo de busca (ex: página de bloqueio) —
            # volta pra home pra recuperar um ponto de partida limpo.
            try:
                page.goto(
                    _MAGALU_DESKTOP_HOME,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                time.sleep(random.uniform(1.0, 2.0))
            except Exception as exc:
                logger.debug(
                    f"[{self.platform_name}] Organic: home goto falhou: {exc}"
                )
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
            page.keyboard.press("Enter")
            page.wait_for_url("**/busca/**", timeout=20_000)
            return True
        except Exception as exc:
            logger.debug(
                f"[{self.platform_name}] Busca orgânica de '{keyword}' falhou: {exc}"
            )
            return False

    @staticmethod
    def _extract_akamai_reference(html: str) -> Optional[str]:
        """Extrai o número de referência da página de bloqueio Akamai, se houver.

        O `Reference #...` codifica qual regra do Bot Manager disparou —
        logá-lo ajuda a diagnosticar (e abrir suporte com a Magalu/Akamai).
        """
        m = re.search(r"Reference[^0-9]{0,12}([0-9][0-9a-fA-F.]+)", html)
        return m.group(1) if m else None

    # ------------------------------------------------------------------
    # Muro de login (Ago/2026) — a Magalu passou a exigir identificação
    # ------------------------------------------------------------------

    @staticmethod
    def _has_product_markup(html: str) -> bool:
        """True se o HTML tem cards de produto renderizados (SERP de verdade)."""
        if not html:
            return False
        
        # Verifica por data-testid (padrão Magalu)
        has_testid = (
            'data-testid="product-card' in html
            or 'data-testid="product-title"' in html
        )
        
        # Verifica por estrutura semântica alternativa (layout novo)
        has_semantic = (
            '<article' in html and '/p/' in html
            or 'class="ProductCard' in html
            or 'class="product-card' in html
            or 'class="ProductList' in html
        )
        
        return has_testid or has_semantic

    @classmethod
    def _looks_like_login_wall(cls, html: str, url: str = "") -> bool:
        """
        True se a página é a tela/modal de identificação da Magalu.

        A checagem é conservadora de propósito: o header padrão do site traz
        "Entre ou cadastre-se" em toda página, então exigimos (a) ausência de
        cards de produto e (b) ≥2 marcadores de login — ou uma URL de SSO,
        que é evidência suficiente sozinha.

        Args:
            html: HTML renderizado da página.
            url:  URL final após redirects (page.url), quando disponível.

        Returns:
            True quando a coleta esbarrou no muro de login.
        """
        lowered_url = (url or "").lower()
        if any(marker in lowered_url for marker in _LOGIN_URL_MARKERS):
            return True
        if not html:
            return False
        # SERP renderizada (mesmo com modal por cima) ainda tem os produtos no
        # DOM — dá pra extrair sem passar pelo login.
        if cls._has_product_markup(html):
            return False
        head = html[:200_000].lower()
        hits = sum(1 for pattern in _LOGIN_HTML_PATTERNS if pattern in head)
        return hits >= 2

    def _dismiss_login_wall(self) -> bool:
        """
        Tenta fechar o modal de identificação sem credenciais (ESC + botões
        de fechar). Muitos "muros" da Magalu são modais dispensáveis sobre uma
        SERP já renderizada.

        Returns:
            True se após a tentativa a página tem cards de produto.
        """
        page = self._pw_page
        if page is None:
            return False
        try:
            page.keyboard.press("Escape")
            time.sleep(random.uniform(0.4, 0.8))
        except Exception:
            pass
        for selector in _LOGIN_DISMISS_SELECTORS:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click(timeout=3_000)
                    time.sleep(random.uniform(0.5, 1.0))
                    break
            except Exception:
                continue
        html = self._safe_content(page)
        if not html:
            return False
        if self._has_product_markup(html):
            logger.info(
                f"[{self.platform_name}] Modal de identificação dispensado — "
                "SERP acessível por baixo ✓"
            )
            return True
        return False

    def _flag_login_required(self, where: str) -> None:
        """Marca a coleta como bloqueada por login e loga a ação necessária."""
        self._last_failure_reason = _REASON_LOGIN
        if self._login_required:
            return
        self._login_required = True
        logger.error(
            f"[{self.platform_name}] A Magalu está exigindo LOGIN em {where} — "
            "a busca redireciona pra tela de identificação em vez da SERP. "
            "Ação: abra o Chrome do perfil dedicado "
            "(`python scripts/setup_local_profile.py --site magalu`), faça "
            "login na conta Magalu, confirme que /busca/ abre normalmente e "
            "rode a coleta de novo. Ver docs/cdp_magalu_collection.md."
        )

    def _search_via_browser(self, keyword: str, page_num: int) -> Optional[str]:
        """
        Navega pra `/busca/{keyword}` dentro do browser persistente e retorna
        o HTML renderizado. None se browser não tá aberto / tudo bloqueado.

        Ordem de tentativas:
          1. Busca orgânica (digita no campo + Enter) — só page 1; gera
             navegação same-origin com Referer, bem menos detectável.
          2. `goto` direto na URL /busca/ mobile, com Referer da home.
          3. `goto` direto na URL /busca/ desktop, com Referer da home.
        """
        if not self._revive_page():
            return None

        slug = quote_plus(keyword.strip())

        attempts: List[Tuple[str, Optional[str], Optional[str]]] = []
        if page_num == 1:
            attempts.append(("orgânica", None, None))
        attempts.append(("goto-mobile", _MAGALU_MOBILE_BASE, _MAGALU_MOBILE_HOME))
        attempts.append(("goto-desktop", _MAGALU_DESKTOP_BASE, _MAGALU_DESKTOP_HOME))

        for label, base, home in attempts:
            if base is None:
                # Tentativa orgânica (digita no campo de busca).
                if not self._organic_search(keyword):
                    continue
            else:
                url = f"{base}/busca/{slug}/"
                if page_num > 1:
                    url += f"?page={page_num}"
                try:
                    # referer=home → Sec-Fetch-Site: same-origin (menos suspeito
                    # que o goto "cru" sem Referer que o código antigo emitia).
                    self._pw_page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=40_000,
                        referer=home,
                    )
                except Exception as exc:
                    logger.warning(
                        f"[{self.platform_name}] Browser goto '{keyword}' "
                        f"p{page_num} em {base[8:30]}: {exc}"
                    )
                    # Rede caiu / DNS falhou: não é anti-bot. Marca a causa
                    # (o circuit breaker precisa saber) e dá um respiro antes
                    # da próxima tentativa em vez de martelar a conexão morta.
                    if any(m in str(exc) for m in _NETWORK_ERROR_MARKERS):
                        self._last_failure_reason = _REASON_NETWORK
                        time.sleep(random.uniform(3.0, 6.0))
                    # Aba/janela fechada no meio da coleta → tenta reviver
                    # antes da próxima tentativa; sem browser, vai pro cffi.
                    if "closed" in str(exc).lower() and not self._revive_page():
                        return None
                    continue

            # Espera produtos aparecerem (sinal de página renderizada com sucesso).
            try:
                self._pw_page.wait_for_selector(
                    'a[href*="/p/"], [data-testid="product-card"]',
                    timeout=12_000,
                )
            except Exception:
                pass

            # Scroll pra carregar lazy items
            try:
                for _ in range(3):
                    self._pw_page.mouse.wheel(0, 600)
                    time.sleep(random.uniform(0.3, 0.8))
            except Exception:
                pass

            time.sleep(random.uniform(0.8, 1.5))

            html = self._safe_content()
            if not html:
                continue

            # Muro de login (Ago/2026): a página vem 200 e grande, então
            # precisa ser checada ANTES da heurística de bloqueio Akamai —
            # senão passa batido e o parser devolve 0 itens sem explicação.
            try:
                current_url = self._pw_page.url or ""
            except Exception:
                current_url = ""
            if self._looks_like_login_wall(html, current_url):
                logger.warning(
                    f"[{self.platform_name}] Tela de identificação em "
                    f"'{keyword}' p{page_num} via {label} "
                    f"(url={current_url[:80]}) — tentando dispensar o modal"
                )
                if self._dismiss_login_wall():
                    html = self._safe_content()
                    if not html:
                        continue
                else:
                    self._flag_login_required(f"'{keyword}' p{page_num}")
                    self._dump_block_html(html, f"login_{keyword}_p{page_num}")
                    continue

            blocked = (
                "Não é possível acessar a página" in html
                or len(html) < 5_000
            )

            # Bypass: mesmo em "bloqueio", verifica se há __NEXT_DATA__ válido
            # com produtos. Akamai às vezes serve a página normal e marca
            # apenas o response code; o JSON embutido fica acessível.
            has_next_data = (
                '__NEXT_DATA__' in html
                and ('"products"' in html or '"product"' in html)
            )

            if blocked and not has_next_data:
                ref = self._extract_akamai_reference(html)
                ref_txt = f", Akamai Ref #{ref}" if ref else ""
                logger.warning(
                    f"[{self.platform_name}] Browser bloqueio em '{keyword}' "
                    f"p{page_num} via {label} (len={len(html):,}{ref_txt}) "
                    "— próxima tentativa"
                )
                self._dump_block_html(html, f"browser_{keyword}_p{page_num}")
                continue

            if blocked and has_next_data:
                logger.info(
                    f"[{self.platform_name}] Página marcada bloqueada mas "
                    f"__NEXT_DATA__ presente em '{keyword}' p{page_num} "
                    "— tentando extração"
                )

            if label == "orgânica":
                logger.info(
                    f"[{self.platform_name}] '{keyword}' p{page_num} carregada "
                    "via busca orgânica ✓"
                )
            return html

        return None

    def _apply_cookies_to_cffi(self, cookies: List[Dict[str, Any]]) -> int:
        """Aplica lista de cookies (formato Playwright) na sessão curl_cffi."""
        applied = 0
        for c in cookies:
            domain = (c.get("domain") or "magazineluiza.com.br").lstrip(".")
            try:
                self._cffi_session.cookies.set(  # type: ignore[union-attr]
                    c["name"],
                    c["value"],
                    domain=domain,
                    path=c.get("path", "/"),
                )
                applied += 1
            except Exception:
                pass
        return applied

    def _ensure_validated_session(self, force_refresh: bool = False) -> bool:
        """
        Garante que self._cffi_session tem cookies Akamai validados.
        Tenta cache primeiro; se ausente/expirado, abre Playwright pra validar.
        Retorna True em sucesso.
        """
        if self._session_validated and not force_refresh:
            return True

        cookies: Optional[List[Dict[str, Any]]] = None

        if not force_refresh:
            cookies = self._load_cached_session()

        if not cookies:
            cookies = self._validate_session_via_browser()
            if cookies:
                self._save_cached_session(cookies)

        if not cookies:
            logger.error("[Magalu] Não foi possível obter sessão validada")
            return False

        applied = self._apply_cookies_to_cffi(cookies)
        self._session_validated = True
        logger.info(
            f"[Magalu] {applied}/{len(cookies)} cookies aplicados ao curl_cffi"
        )
        return True

    # ------------------------------------------------------------------
    # Override ciclo de vida — curl_cffi como data plane; Playwright só
    # pra validar sessão Akamai (1×, depois cache em disco)
    # ------------------------------------------------------------------

    def __enter__(self) -> "MagaluScraper":
        if not _HAS_CURL_CFFI:
            raise RuntimeError(
                "curl_cffi não instalado. Execute: pip install curl-cffi>=0.6.0"
            )
        self._cffi_session = cffi_requests.Session()
        logger.info(
            f"[{self.platform_name}] Sessão curl_cffi iniciada "
            f"(impersonate={self._impersonate})"
        )

        if self._browser_mode:
            # Abre browser persistente — fica aberto durante toda a coleta
            opened = self._open_persistent_browser()
            if not opened:
                logger.error(
                    "[Magalu] Browser não abriu — coleta provavelmente falhará"
                )
        else:
            logger.warning(
                "[Magalu] MAGALU_FORCE_CURL=true — modo curl_cffi puro (deve falhar "
                "com Akamai atual; mantido pra debug/futuro)"
            )

        return self

    def __exit__(self, *_) -> None:
        self._close_persistent_browser()
        try:
            if self._cffi_session is not None:
                self._cffi_session.close()
        except Exception:
            pass
        self._cffi_session = None
        self._session_validated = False

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
        DEPRECATED (Mai/2026): Magalu desabilitou o endpoint `_next/data`
        — sempre retorna 404 mesmo com BUILD_ID correto. Função mantida
        pra futuro caso volte. Retorna None imediatamente.
        """
        return None

        # --- código preservado abaixo (não executado) ---
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
    # Estratégia 2: RSC flight payload (Next.js App Router)
    # ------------------------------------------------------------------
    # No App Router não existe mais <script id="__NEXT_DATA__">: o estado do
    # servidor é transmitido em pedaços `self.__next_f.push([1,"<chunk>"])`.
    # Concatenando os chunks (cada um é uma string JSON escapada) obtemos o
    # mesmo payload de produtos que antes vinha no __NEXT_DATA__.

    _RSC_CHUNK_RE = re.compile(
        r'self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*("(?:[^"\\]|\\.)*")', re.DOTALL
    )

    @classmethod
    def _extract_rsc_payload(cls, html: str) -> Optional[str]:
        """
        Reconstrói o payload RSC (flight) a partir dos chunks `__next_f.push`.

        Args:
            html: HTML da página renderizada.

        Returns:
            Texto do payload concatenado, ou None se a página não usa RSC.
        """
        chunks = cls._RSC_CHUNK_RE.findall(html)
        if not chunks:
            return None
        parts: List[str] = []
        for raw in chunks:
            try:
                parts.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return "".join(parts) if parts else None

    @staticmethod
    def _balanced_slice(text: str, start: int) -> Optional[str]:
        """
        Recorta a estrutura JSON balanceada que começa em `text[start]`.

        Conta apenas o par de colchetes/chaves do caractere inicial e ignora
        o conteúdo de strings (com escapes) — o payload RSC vem concatenado
        com lixo em volta, então `json.loads` do texto inteiro não serve.

        Args:
            text:  Texto contendo JSON embutido.
            start: Índice do `[` ou `{` inicial.

        Returns:
            Substring JSON balanceada, ou None se não fechar.
        """
        opening = text[start:start + 1]
        closing = {"[": "]", "{": "}"}.get(opening)
        if closing is None:
            return None
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == opening:
                depth += 1
            elif ch == closing:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return None

    def _find_products_in_rsc(self, html: str) -> List[Dict]:
        """
        Procura o array de produtos dentro do payload RSC do App Router.

        Returns:
            Lista de produtos (mesmo formato do __NEXT_DATA__) ou [] se nada
            reconhecível foi encontrado.
        """
        payload = self._extract_rsc_payload(html)
        if not payload:
            return []

        for key in ("products", "items", "results"):
            needle = f'"{key}":'
            cursor = 0
            while True:
                idx = payload.find(needle, cursor)
                if idx == -1:
                    break
                cursor = idx + len(needle)
                start = cursor
                while start < len(payload) and payload[start] in " \t\r\n":
                    start += 1
                if start >= len(payload) or payload[start] != "[":
                    continue
                blob = self._balanced_slice(payload, start)
                if not blob:
                    continue
                try:
                    arr = json.loads(blob)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(arr, list) and arr
                    and self._array_has_priced_products(arr)
                ):
                    return [item for item in arr if isinstance(item, dict)]
        return []

    # ------------------------------------------------------------------
    # Estratégia 3: parser de DOM (cards renderizados)
    # ------------------------------------------------------------------
    # Rede de segurança final: quando nem __NEXT_DATA__ nem RSC entregam o
    # payload (mudança de layout, hidratação client-side, HTML parcial), os
    # cards ainda estão no DOM que o browser renderizou. Os seletores são os
    # mesmos confirmados em produção pelo scraper Node.

    @staticmethod
    def _dom_text(node: Any, selectors: Tuple[str, ...]) -> Optional[str]:
        """Primeiro texto não-vazio entre os seletores, dentro de `node`."""
        for selector in selectors:
            try:
                el = node.select_one(selector)
            except Exception:
                continue
            if el:
                text = el.get_text(" ", strip=True)
                if text:
                    return text
        return None

    @staticmethod
    def _is_card_container(node: Any) -> bool:
        """True se o elemento delimita UM produto (e não a lista inteira)."""
        try:
            testid = node.get("data-testid") or ""
            class_attr = node.get("class", [])
            class_str = " ".join(class_attr) if isinstance(class_attr, list) else str(class_attr)
            
            return (
                node.name == "li" 
                or "product-card" in testid
                or "ProductCard" in class_str
                or "product-card" in class_str
            )
        except Exception:
            return False

    def _parse_dom_cards(self, html: str) -> List[Dict[str, Any]]:
        """
        Extrai produtos dos cards renderizados no HTML.

        Devolve dicts no MESMO formato dos produtos do JSON do Next.js
        (title/path/seller/rating/badge + `bestPriceTemplate` com o preço
        em texto BR), pra reaproveitar `_parse_products` sem ramificação.

        Args:
            html: HTML renderizado da SERP.

        Returns:
            Lista de produtos na ordem em que aparecem na página.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            logger.debug(f"[{self.platform_name}] DOM parse falhou: {exc}")
            return []

        cards: List[Any] = []
        for selector in _DOM_CARD_SELECTORS:
            try:
                cards = soup.select(selector)
            except Exception:
                continue
            if cards:
                break
        if not cards:
            return []

        products: List[Dict[str, Any]] = []
        seen_hrefs: set = set()

        for card in cards:
            href = card.get("href") or ""
            if "/p/" not in href:
                continue
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            # O título às vezes vive fora do <a> (card = <li> com o link
            # dentro). Sobe um nível quando não achar no próprio card — mas
            # só se o pai for o container do card: subir até a <ul> misturaria
            # o texto de TODOS os produtos e o preço sairia do card errado.
            scope = card
            title = self._dom_text(scope, _DOM_TITLE_SELECTORS)
            parent = card.parent
            if not title and parent is not None and self._is_card_container(parent):
                scope = parent
                title = self._dom_text(scope, _DOM_TITLE_SELECTORS)
            if not title:
                title = (card.get("title") or "").strip() or None
            if not title:
                continue

            card_text = scope.get_text(" ", strip=True)

            price_text = self._dom_text(scope, _DOM_PRICE_SELECTORS)
            if not price_text or parse_price(price_text) is None:
                match = _DOM_PRICE_RE.search(card_text)
                price_text = match.group(0) if match else price_text
            if not price_text:
                continue

            product_id = ""
            id_match = re.search(r"/p/([a-z0-9]+)/", href, re.IGNORECASE)
            if id_match:
                product_id = id_match.group(1).upper()

            seller = self._dom_text(scope, _DOM_SELLER_SELECTORS)
            if not seller:
                # A URL do card carrega o slug do seller — é o vencedor da
                # buy box daquela listagem, informação central do projeto.
                seller_match = re.search(r"[?&]seller_id=([^&\"']+)", href)
                if seller_match:
                    seller = seller_match.group(1).strip()

            rating_text = self._dom_text(scope, (_DOM_RATING_SELECTOR,))
            rating_score: Optional[float] = None
            rating_count: Optional[int] = None
            if rating_text:
                score_match = re.search(r"(\d+[.,]\d+|\d+)", rating_text)
                if score_match:
                    try:
                        rating_score = float(score_match.group(1).replace(",", "."))
                    except ValueError:
                        rating_score = None
                count_match = re.search(r"\((\d[\d.]*)\)", rating_text)
                if count_match:
                    try:
                        rating_count = int(count_match.group(1).replace(".", ""))
                    except ValueError:
                        rating_count = None

            lowered = card_text.lower()
            badge = next(
                (p for p in _DOM_SPONSORED_PATTERNS if p in lowered), None
            )

            product: Dict[str, Any] = {
                "id": product_id or href,
                "title": title,
                "path": href,
                "bestPriceTemplate": price_text,
            }
            if seller:
                product["seller"] = {"description": seller}
            if rating_score is not None or rating_count is not None:
                product["rating"] = {"score": rating_score, "count": rating_count}
            if badge:
                product["badge"] = "Patrocinado"
            products.append(product)

        return products

    # ------------------------------------------------------------------
    # Parser do JSON do Next.js — caminha a estrutura procurando produtos
    # ------------------------------------------------------------------

    # Campos que indicam preço em qualquer profundidade do dict (numa SERP
    # legítima do Magalu, todo produto tem pelo menos um destes).
    _PRICE_KEYS = (
        "bestPrice", "currentPrice", "salesPrice", "finalPrice", "price",
        "promotionalPrice", "sellingPrice", "bestPriceTemplate",
        "priceTemplate", "currentPriceTemplate", "prices",
    )

    def _find_products_in_json(self, data: Any) -> List[Dict]:
        """
        Caminha o JSON do Next.js procurando o array de produtos COM preço.

        Bug em produção (Mai/16): o walker pegava arrays de carrosséis
        promocionais ("produtos similares", "mais vendidos") que contêm
        title+id mas SEM price/path. Resultado: 37 registros lixo no DB.
        Fix: arrays só são aceitos se a maioria dos itens tem campo de preço.
        """
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
            if (
                isinstance(node, list) and node
                and self._array_has_priced_products(node)
            ):
                return node

        # Fallback: walk recursivo — também filtra arrays sem preços
        found: List[Dict] = []
        self._walk_find_product_array(data, found, depth=0)
        return found

    def _looks_like_product(self, obj: Any) -> bool:
        if not isinstance(obj, dict):
            return False
        has_id = any(k in obj for k in ("id", "productId", "sku", "variationId"))
        has_title = any(k in obj for k in ("title", "name", "productName"))
        return has_id and has_title

    def _has_price_field(self, obj: Any) -> bool:
        """True se o dict tem qualquer campo conhecido de preço (não vazio)."""
        if not isinstance(obj, dict):
            return False
        for key in self._PRICE_KEYS:
            val = obj.get(key)
            if val is None:
                continue
            if isinstance(val, (int, float)) and val > 0:
                return True
            if isinstance(val, str) and val.strip():
                return True
            if isinstance(val, dict) and val:
                return True
        return False

    def _array_has_priced_products(self, arr: List[Any]) -> bool:
        """
        True se ≥50% dos primeiros 5 itens parecem produto E têm preço.
        Evita arrays de carrosséis promocionais (sem price/path).
        """
        sample = [x for x in arr[:5] if self._looks_like_product(x)]
        if not sample:
            return False
        with_price = sum(1 for x in sample if self._has_price_field(x))
        return with_price * 2 >= len(sample)  # maioria

    def _walk_find_product_array(
        self, node: Any, found: List[Dict], depth: int
    ) -> None:
        if found or depth > 8:
            return
        if isinstance(node, list):
            if node and self._array_has_priced_products(node):
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

    def _extract_seller_count(self, product: Dict) -> Optional[int]:
        """
        Extrai o nº de sellers competindo pelo produto no payload do Magalu.

        Lê apenas campos tipados do JSON — nunca regex sobre o blob serializado,
        que confundiria preço, BTU e contagem de avaliações com nº de ofertas.
        Estruturas conhecidas:
          - ``sellers``: [ {...}, {...} ]        → len()
          - ``sellerCount`` / ``sellersCount``   → int direto
          - ``offers``: [ ... ] / ``offerCount`` → nº de ofertas

        Args:
            product: dict do produto vindo de ``__NEXT_DATA__`` ou RSC.

        Returns:
            Nº de sellers, ou None quando o payload não informa. Devolve None
            (não 1) no silêncio: desconhecido ≠ seller único, e assumir 1
            inflaria artificialmente o share de buy box do 1P.
        """
        for key in ("sellers", "offers", "marketplaceSellers"):
            val = product.get(key)
            if isinstance(val, list) and val:
                return len(val)

        for key in ("sellerCount", "sellersCount", "offerCount", "offersCount"):
            val = product.get(key)
            if isinstance(val, bool):
                continue
            if isinstance(val, int) and 1 <= val <= 200:
                return val
            if isinstance(val, str) and val.strip().isdigit():
                count = int(val.strip())
                if 1 <= count <= 200:
                    return count

        return None

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
        skipped_no_price = 0

        for idx, prod in enumerate(products):
            title = (
                prod.get("title")
                or prod.get("name")
                or prod.get("productName")
                or self._deep_get(prod, "product", "title")
            )
            if not title:
                continue

            price = self._extract_price(prod)
            # Produtos sem preço extraível são lixo de carrosséis promocionais
            # ou de "produtos similares". Não inserimos no banco.
            if price is None:
                skipped_no_price += 1
                continue

            # Detecta patrocinado — o campo varia entre versões do payload do
            # Next.js; testamos as chaves conhecidas + um badge/label textual
            # explícito ("Patrocinado"/"Anúncio"). Conservador: sem sinal
            # claro, fica orgânico (não infere ads por heurística frouxa).
            _badge_txt = " ".join(str(v) for v in (
                prod.get("badge"),
                self._deep_get(prod, "label", "text"),
                prod.get("tag"),
            ) if v).lower()
            sponsored = bool(
                prod.get("sponsored")
                or prod.get("isSponsored")
                or prod.get("isAd")
                or prod.get("advertisement")
                or self._deep_get(prod, "marketing", "sponsored")
                or self._deep_get(prod, "advertising", "sponsored")
                or "patrocinad" in _badge_txt
                or "anúncio" in _badge_txt
                or "anuncio" in _badge_txt
            )

            pos_general = offset + idx + 1
            if sponsored:
                sponsored_counter += 1
                pos_organic, pos_sponsored = None, sponsored_counter
            else:
                organic_counter += 1
                pos_organic, pos_sponsored = organic_counter, None

            seller = self._extract_seller(prod) or "Magalu"
            # Tipo de seller: sortimento próprio Magalu (1P) vs parceiro
            # do marketplace (3P). Magalu marca o 1P como "Magalu"/"magazineluiza".
            _seller_lc = seller.strip().lower()
            tipo_seller = (
                "1P" if _seller_lc in ("magalu", "magazine luiza", "magazineluiza")
                else "3P"
            )

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
                buy_box_seller=seller,
                qtd_sellers=self._extract_seller_count(prod),
                tipo_seller=tipo_seller,
                is_fulfillment=False,
                rating=rating_f,
                review_count=review_i,
                tag_destaque=prod.get("badge") or self._deep_get(prod, "label", "text"),
                url_produto=self._extract_url(prod),
            ))

        if skipped_no_price:
            logger.warning(
                f"[{self.platform_name}] '{keyword}' p{page}: descartados "
                f"{skipped_no_price} itens sem preço (provavelmente carrossel "
                "promocional ou página degradada do Akamai)"
            )
        return records

    # ------------------------------------------------------------------
    # Helpers de delay
    # ------------------------------------------------------------------

    def _delay(self, min_s: float, max_s: float) -> None:
        time.sleep(random.uniform(min_s, max_s))

    def _refresh_browser_session(self) -> None:
        """
        Revalida a sessão visitando a home + emulando interação humana.
        Disparado após N bloqueios consecutivos pro sensor.js do Akamai
        ter chance de promover o _abck cookie.
        """
        if not self._pw_page:
            return
        try:
            logger.info(
                f"[{self.platform_name}] Revalidando sessão "
                f"(após {self._consecutive_blocks} bloqueios)..."
            )
            self._pw_page.goto(
                _MAGALU_DESKTOP_HOME,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            for _ in range(4):
                self._pw_page.mouse.move(
                    random.randint(150, 1200), random.randint(150, 700)
                )
                time.sleep(random.uniform(0.4, 0.9))
            self._pw_page.mouse.wheel(0, 500)
            time.sleep(random.uniform(1.0, 1.8))
            self._pw_page.mouse.wheel(0, 300)
            time.sleep(random.uniform(0.8, 1.5))

            # Aplica cookies frescos no curl_cffi pra futuro fallback
            try:
                cookies = self._pw_context.cookies()
                self._apply_cookies_to_cffi(cookies)
            except Exception:
                pass

            self._consecutive_blocks = 0
            logger.info(f"[{self.platform_name}] Sessão revalidada ✓")
        except Exception as exc:
            logger.warning(f"[{self.platform_name}] Revalidação falhou: {exc}")

    # ------------------------------------------------------------------
    # Extração — 3 parsers em cascata sobre o MESMO HTML
    # ------------------------------------------------------------------

    def _records_from_html(
        self,
        html: str,
        keyword: str,
        keyword_category_map: dict,
        page: int,
        source: str,
    ) -> List[Dict[str, Any]]:
        """
        Converte o HTML de uma SERP em registros, tentando os 3 parsers.

        Ordem: `__NEXT_DATA__` (payload completo, o mais rico) → RSC flight
        do App Router → cards do DOM. Antes só existia o primeiro: quando a
        Magalu mudava o layout, a página chegava inteira aqui e saía como
        "0 produtos", indistinguível de um bloqueio.

        Args:
            html:                 HTML renderizado da busca.
            keyword:              Termo pesquisado.
            keyword_category_map: Mapa keyword → categoria.
            page:                 Número da página (1-based).
            source:               "browser" ou "curl_cffi" (só pra log).

        Returns:
            Registros prontos pro DataFrame — [] se nenhum parser reconheceu.
        """
        parsers = (
            ("__NEXT_DATA__", self._products_from_next_data),
            ("RSC/App Router", self._find_products_in_rsc),
            ("DOM", self._parse_dom_cards),
        )
        for parser_name, parser in parsers:
            try:
                products = parser(html)
            except Exception as exc:
                logger.warning(
                    f"[{self.platform_name}] Parser {parser_name} falhou em "
                    f"'{keyword}' p{page}: {exc}"
                )
                continue
            if not products:
                continue
            records = self._parse_products(
                products, keyword, keyword_category_map, page
            )
            if records:
                logger.info(
                    f"[{self.platform_name}] {len(records)} produtos via "
                    f"{source} ({parser_name})"
                )
                new_build = self._extract_build_id(html)
                if new_build and new_build != self._build_id:
                    self._build_id = new_build
                return records
        return []

    def _products_from_next_data(self, html: str) -> List[Dict]:
        """Produtos do `<script id="__NEXT_DATA__">`, ou [] se ausente."""
        next_data = self._extract_next_data_from_html(html)
        if not next_data:
            return []
        return self._find_products_in_json(next_data)

    def _diagnose_empty_html(self, html: str, keyword: str, page: int) -> None:
        """
        Explica (e dumpa) uma página que carregou mas não rendeu produtos.

        Sem isso, os três cenários abaixo produziam exatamente a mesma linha
        de log — "0 itens" — e o operador não tinha como saber qual deles
        estava acontecendo nem o que fazer a respeito.
        """
        try:
            current_url = self._pw_page.url if self._pw_page else ""
        except Exception:
            current_url = ""

        if self._looks_like_login_wall(html, current_url):
            self._flag_login_required(f"'{keyword}' p{page}")
            self._dump_block_html(html, f"login_{keyword}_p{page}")
            return

        if self._has_product_markup(html):
            # Cards no DOM mas nenhum parser extraiu: layout novo. É bug
            # nosso, não bloqueio — o dump é o material pra corrigir.
            self._last_failure_reason = _REASON_LAYOUT
            logger.error(
                f"[{self.platform_name}] '{keyword}' p{page}: a página tem "
                f"cards de produto ({len(html):,} bytes) mas NENHUM parser "
                "extraiu itens — layout da Magalu provavelmente mudou. "
                "Revise os seletores em _DOM_CARD_SELECTORS usando o HTML "
                "salvo abaixo."
            )
            self._dump_block_html(html, f"layout_{keyword}_p{page}")
            return

        self._last_failure_reason = _REASON_UNKNOWN
        logger.warning(
            f"[{self.platform_name}] '{keyword}' p{page}: página carregou "
            f"({len(html):,} bytes) sem cards nem payload de produtos "
            f"(url={current_url[:80]}) — HTML salvo pra diagnóstico"
        )
        self._dump_block_html(html, f"vazia_{keyword}_p{page}")

    def _search_page(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
    ) -> List[Dict[str, Any]]:
        """
        Executa a busca em UMA página. Estratégias em cascata:
          A. Browser persistente (modo principal — Akamai aceita)
          B. curl_cffi, SÓ quando o browser não entregou HTML nenhum
        """
        records: List[Dict[str, Any]] = []
        browser_html: Optional[str] = None

        # Revalida sessão após N bloqueios consecutivos
        if (
            self._browser_mode
            and self._pw_page
            and self._consecutive_blocks >= self._blocks_before_reval
        ):
            self._refresh_browser_session()

        # --- Estratégia A: Browser persistente (modo principal) ---
        if self._browser_mode and self._pw_page:
            browser_html = self._search_via_browser(keyword, page)
            if browser_html:
                records = self._records_from_html(
                    browser_html, keyword, keyword_category_map, page, "browser"
                )
                if not records:
                    self._diagnose_empty_html(browser_html, keyword, page)
            elif self._last_failure_reason not in (_REASON_LOGIN, _REASON_NETWORK):
                self._last_failure_reason = _REASON_AKAMAI

        # --- Estratégia B: curl_cffi (só como rede de segurança) ---
        # Quando o browser ENTREGOU o HTML e mesmo assim não saiu produto, o
        # curl_cffi não tem nada a acrescentar: o Akamai amarra os cookies à
        # sessão TLS que os emitiu, então a mesma busca via cffi devolve 403
        # depois de ~45s de timeout. Rodar isso em toda keyword só queimava
        # tempo de coleta e enchia o log de "HTML search bloqueado".
        should_try_cffi = (
            not records
            and browser_html is None
            and not self._cffi_disabled
            and self._cffi_session is not None
        )
        if should_try_cffi:
            # Refresca cookies do browser antes da tentativa — _abck pode ter
            # sido promovido durante a navegação anterior.
            if self._pw_context:
                try:
                    self._apply_cookies_to_cffi(self._pw_context.cookies())
                except Exception:
                    pass

            html = self._fetch_html_search(keyword, page)
            if html:
                records = self._records_from_html(
                    html, keyword, keyword_category_map, page, "curl_cffi"
                )
            if records:
                self._cffi_failures = 0
            else:
                self._cffi_failures += 1
                # Em MAGALU_FORCE_CURL o cffi é o único caminho — desligá-lo
                # deixaria a coleta sem plano nenhum.
                if (
                    self._browser_mode
                    and self._cffi_failures >= _CFFI_FAILURES_BEFORE_DISABLE
                ):
                    self._cffi_disabled = True
                    logger.warning(
                        f"[{self.platform_name}] Fallback curl_cffi desligado "
                        f"após {self._cffi_failures} falhas seguidas — o Akamai "
                        "não aceita os cookies do browser numa sessão TLS "
                        "diferente. A coleta segue só pelo browser."
                    )

        if records:
            self._consecutive_blocks = 0
            self._last_failure_reason = _REASON_UNKNOWN
            # A busca voltou a funcionar: se o muro de login foi dispensado
            # (ou era intermitente), a coleta não deve mais abortar cedo.
            self._login_required = False
        else:
            self._consecutive_blocks += 1

        return records

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

        Modo principal: browser persistente (Playwright) — abre 1×, mantém
        aberto, navega pra `/busca/<keyword>` por keyword.

        Returns:
            Lista de registros prontos para o DataFrame.
        """
        if self._cffi_session is None:
            logger.error(
                f"[{self.platform_name}] Sessão não inicializada — "
                "use 'with MagaluScraper() as s:'"
            )
            return []

        # Circuit breaker — se a coleta já foi abortada por bloqueios
        # consecutivos, pula a keyword instantaneamente (sem abrir navegação).
        if self.collection_aborted:
            logger.debug(
                f"[{self.platform_name}] Coleta abortada (circuit breaker) — "
                f"pulando '{keyword}'"
            )
            return []

        all_records: List[Dict[str, Any]] = []

        # Delay curto entre keywords (browser já fica aberto, sem warm-up)
        self._delay(2.0, 4.0)

        for page in range(1, page_limit + 1):
            logger.info(
                f"[{self.platform_name}] '{keyword}' página {page}/{page_limit}"
            )
            records = self._search_page(keyword, keyword_category_map, page)
            all_records.extend(records)

            if not records:
                logger.warning(
                    f"[{self.platform_name}] '{keyword}' p{page} retornou 0 itens — "
                    "interrompendo keyword."
                )
                break

            if page < page_limit:
                self._delay(3.0, 6.0)

        # Circuit breaker — conta keywords seguidas sem nenhum produto. Após
        # _ABORT_AFTER_BLOCKED_KEYWORDS, aborta a coleta Magalu inteira em vez
        # de gastar ~15-20s por keyword nas ~31 restantes, todas bloqueadas.
        if all_records:
            self._blocked_keyword_streak = 0
        else:
            self._blocked_keyword_streak += 1
            self._maybe_trip_circuit_breaker()

        self._log_search_result(keyword, len(all_records))
        return all_records

    def _maybe_trip_circuit_breaker(self) -> None:
        """
        Aborta a coleta Magalu quando as keywords vêm vazias em sequência.

        A mensagem é escolhida pela causa registrada em
        `_last_failure_reason`: culpar o Akamai quando o problema é login
        expirado (ou layout novo, ou a internet caída) manda o operador
        investigar a coisa errada.
        """
        if self.collection_aborted:
            return

        # Muro de login exige ação humana — não adianta gastar 5 keywords.
        limit = (
            _ABORT_AFTER_LOGIN_KEYWORDS if self._login_required
            else _ABORT_AFTER_BLOCKED_KEYWORDS
        )
        if self._blocked_keyword_streak < limit:
            return

        self.collection_aborted = True
        streak = self._blocked_keyword_streak
        prefix = (
            f"[{self.platform_name}] Circuit breaker disparado: {streak} "
            "keywords seguidas sem produtos. Abortando a coleta Magalu — as "
            "keywords restantes serão puladas. "
        )

        if self._login_required:
            logger.error(
                prefix + "Causa: a Magalu está exigindo LOGIN na busca. "
                "Ação: rode `python scripts/setup_local_profile.py --site "
                "magalu`, faça login na janela do Chrome e repita a coleta. "
                "Ver docs/cdp_magalu_collection.md (seção Muro de login)."
            )
        elif self._last_failure_reason == _REASON_LAYOUT:
            logger.error(
                prefix + "Causa: as páginas CARREGARAM com cards de produto, "
                "mas nenhum parser extraiu itens — o layout da Magalu mudou. "
                "Ação: abra os HTMLs `logs/magalu_block_layout_*.html` e "
                "atualize os seletores em scrapers/magalu.py."
            )
        elif self._last_failure_reason == _REASON_NETWORK:
            logger.error(
                prefix + "Causa: erros de REDE do Chrome (conexão caiu/DNS), "
                "não anti-bot. Ação: verifique a internet da máquina "
                "coletora e rode de novo."
            )
        else:
            logger.error(
                prefix + "Causa provável: IP ou perfil do Chrome flagado pelo "
                "Bot Manager do Akamai. Ver docs/cdp_magalu_collection.md "
                "(seção Troubleshooting)."
            )
