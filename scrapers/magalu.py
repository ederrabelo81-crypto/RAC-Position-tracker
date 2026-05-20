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
from scrapers.base import BaseScraper
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

        # Contador de bloqueios consecutivos pra disparar revalidação de sessão
        # (visita home + interação humana pra sensor.js promover _abck).
        self._consecutive_blocks: int = 0
        self._blocks_before_reval: int = 3

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

        try:
            self._pw_handle = sync_playwright().start()
        except Exception as exc:
            logger.error(f"[Magalu] Falha ao iniciar Playwright: {exc}")
            return False

        # ── Modo CDP: conecta ao Chrome real do usuário via DevTools Protocol ──
        # Quando MAGALU_CDP_URL está setado (ex: http://localhost:9222), conecta
        # a um Chrome já aberto pelo usuário com --remote-debugging-port. Usa o
        # Chrome real + IP residencial + cookies/histórico acumulados de meses
        # de navegação — combinação que Akamai aceita como "usuário humano".
        cdp_url = os.getenv("MAGALU_CDP_URL", "").strip()
        if cdp_url:
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
        # SÓ no modo launch (Chromium efêmero do Playwright). No modo CDP é
        # contraproducente: o Chrome real já tem navigator.webdriver,
        # window.chrome (com .app + funções nativas) e navigator.plugins
        # legítimos — sobrescrevê-los por stubs JS deixa o fingerprint MAIS
        # sintético (descritores não-nativos detectáveis), não menos.
        if not self._is_cdp:
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

        # Persistent context já vem com 1 página aberta (about:blank) —
        # reusa em vez de criar nova, pra economizar 1 sinal de automação.
        if self._pw_context.pages:
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
        # Akamai a promover o cookie pra rota /busca/.
        try:
            cal_url = f"{_MAGALU_MOBILE_BASE}/busca/ar+condicionado/"
            self._pw_page.goto(cal_url, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(random.uniform(2.0, 3.5))
            html = self._pw_page.content()
            if len(html) > 50_000 and "Não é possível acessar a página" not in html:
                logger.info(
                    f"[Magalu] Busca de calibração OK ({len(html):,} bytes) — sessão pronta"
                )
            else:
                logger.warning(
                    f"[Magalu] Busca de calibração suspeita (len={len(html):,}) — pode dar 403"
                )
        except Exception as exc:
            logger.warning(f"[Magalu] Busca de calibração falhou: {exc}")

        # Tenta extrair BUILD_ID daqui pra futuras chamadas
        try:
            html = self._pw_page.content()
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

    def _close_persistent_browser(self) -> None:
        """Fecha o browser persistente limpando recursos.

        No modo CDP (conectado a Chrome externo), NÃO fecha contexto/browser —
        é o Chrome do usuário, deixa aberto pra próximo run.
        """
        if self._is_cdp:
            # CDP: só desconecta; não fecha página, contexto ou browser
            try:
                if self._pw_browser:
                    self._pw_browser.close()  # close() em CDP-browser apenas desconecta
            except Exception:
                pass
            try:
                if self._pw_handle:
                    self._pw_handle.stop()
            except Exception:
                pass
            self._pw_page = None
            self._pw_context = None
            self._pw_browser = None
            self._pw_handle = None
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
        try:
            if self._pw_handle:
                self._pw_handle.stop()
        except Exception:
            pass
        self._pw_page = None
        self._pw_context = None
        self._pw_browser = None
        self._pw_handle = None

    def _search_via_browser(self, keyword: str, page_num: int) -> Optional[str]:
        """
        Navega pra `/busca/{keyword}` dentro do browser persistente e retorna
        o HTML renderizado. None se browser não tá aberto/erro.

        Tenta domínio mobile primeiro; se bloqueado, tenta desktop como fallback
        — Akamai às vezes aceita um quando rejeita o outro com o mesmo _abck
        em estado "challenge".
        """
        if not self._pw_page:
            return None

        slug = quote_plus(keyword.strip())
        candidates = [
            (_MAGALU_MOBILE_BASE, _MAGALU_MOBILE_HOME),
            (_MAGALU_DESKTOP_BASE, _MAGALU_DESKTOP_HOME),
        ]

        for base, home in candidates:
            url = f"{base}/busca/{slug}/"
            if page_num > 1:
                url += f"?page={page_num}"

            try:
                self._pw_page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            except Exception as exc:
                logger.warning(
                    f"[Magalu] Browser goto '{keyword}' p{page_num} em "
                    f"{base[8:30]}: {exc}"
                )
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

            try:
                html = self._pw_page.content()
            except Exception as exc:
                logger.warning(f"[Magalu] Browser content() erro: {exc}")
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
                logger.warning(
                    f"[Magalu] Browser bloqueio em '{keyword}' p{page_num} "
                    f"({base[8:30]}, len={len(html):,}) — tentando próximo domínio"
                )
                self._dump_block_html(html, f"browser_{keyword}_p{page_num}")
                continue

            if blocked and has_next_data:
                logger.info(
                    f"[Magalu] Página marcada bloqueada mas __NEXT_DATA__ presente "
                    f"em '{keyword}' p{page_num} — tentando extração"
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

    def _search_page(
        self,
        keyword: str,
        keyword_category_map: dict,
        page: int,
    ) -> List[Dict[str, Any]]:
        """
        Executa a busca em UMA página. Estratégias em cascata:
          A. Browser persistente (modo principal — Akamai aceita)
          B. curl_cffi HTML com cookies do browser (fallback)
        """
        records: List[Dict[str, Any]] = []
        html: Optional[str] = None

        # Revalida sessão após N bloqueios consecutivos
        if (
            self._browser_mode
            and self._pw_page
            and self._consecutive_blocks >= self._blocks_before_reval
        ):
            self._refresh_browser_session()

        # --- Estratégia A: Browser persistente (modo principal) ---
        if self._browser_mode and self._pw_page:
            html = self._search_via_browser(keyword, page)
            if html:
                next_data = self._extract_next_data_from_html(html)
                if next_data:
                    products = self._find_products_in_json(next_data)
                    if products:
                        records = self._parse_products(
                            products, keyword, keyword_category_map, page
                        )
                        logger.info(
                            f"[{self.platform_name}] {len(records)} produtos via browser"
                        )
                        new_build = self._extract_build_id(html)
                        if new_build and new_build != self._build_id:
                            self._build_id = new_build

        # --- Estratégia B: curl_cffi HTML com cookies recém-extraídos ---
        # Refresca cookies do browser antes de cada tentativa curl_cffi —
        # _abck pode ter sido promovido durante a navegação anterior.
        if not records:
            if self._pw_context and self._cffi_session is not None:
                try:
                    cookies = self._pw_context.cookies()
                    self._apply_cookies_to_cffi(cookies)
                except Exception:
                    pass

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
                            "curl_cffi HTML"
                        )
                        new_build = self._extract_build_id(html)
                        if new_build and new_build != self._build_id:
                            self._build_id = new_build

        if records:
            self._consecutive_blocks = 0
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

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
