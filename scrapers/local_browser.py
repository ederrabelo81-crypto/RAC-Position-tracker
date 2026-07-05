"""
scrapers/local_browser.py — Chrome local, persistente e LOGADO, compartilhado
pelos scrapers protegidos por antibot (Shopee, Magalu, Casas Bahia).

Por que este módulo existe
--------------------------
A abordagem anterior (perfil COPIADO para ``C:\\chrome-rac-cdp`` + Chrome
separado com ``--remote-debugging-port`` + conexão via CDP) falhava por dois
motivos ESTRUTURAIS — não era um bug pontual:

  1. **Chrome 136+ IGNORA ``--remote-debugging-port``** quando o
     ``--user-data-dir`` aponta para o perfil PADRÃO do usuário. Foi uma
     correção de segurança do Google (roubo de cookies). Ou seja: não dá pra
     "ligar o CDP no meu Chrome logado" — o Chrome silenciosamente descarta a
     porta de debug. Por isso o setup antigo COPIAVA o perfil pra outra pasta.
  2. **Copiar o perfil dispara a proteção "perfil realocado" do Chrome, que
     INVALIDA os logins** (Google e sessões salvas). O próprio
     ``setup_cdp_profile.ps1`` avisava: "vai abrir DESLOGADO… OBRIGATÓRIO
     logar na Shopee de novo". Resultado: a Shopee respondia 403 (sessão
     anônima) e a coleta não acontecia.

Solução
-------
Um ÚNICO diretório de perfil DEDICADO e ESTÁVEL, gerenciado pelo projeto
(``data/chrome_profile/``), aberto com ``launch_persistent_context`` usando o
**Chrome real** do usuário. O login da Shopee é feito UMA vez
(``scripts/setup_local_profile.py``) e PERSISTE entre execuções porque o
diretório nunca é copiado/movido — o Chrome não o trata como realocado.

  * Sem CDP, sem porta de debug, sem cópia de perfil, sem re-login diário.
  * Roda no notebook do usuário (IP residencial) — a combinação
    "Chrome genuíno + perfil com histórico/login + IP residencial" é
    exatamente a que os antibots (Akamai/Shopee) aceitam.
  * Um único browser é aberto por execução e COMPARTILHADO pelos 3 scrapers
    (cada um abre a sua própria aba). Fecha uma vez, no fim da coleta.

Ativação
--------
Defina ``RAC_LOCAL_CHROME=1`` (o launcher ``scripts/collect_local_authenticated``
já faz isso). Sem essa env, nada muda: os scrapers seguem no comportamento
antigo (curl_cffi / CDP), sem regressão para a VM/GitHub Actions.

Anti-detecção
-------------
Preferimos o fork ``rebrowser-playwright`` (oculta o ``Runtime.enable`` que o
sensor.js do Akamai detecta). Como é um Chrome REAL com perfil real, evitamos
stealth-JS pesado (que deixaria o fingerprint MAIS sintético) — apenas
removemos a flag ``--enable-automation`` e o ``navigator.webdriver``.
"""

import atexit
import os
import random
import time
from pathlib import Path
from typing import Any, Optional, Tuple

from loguru import logger

# O patch de runtime do rebrowser precisa ser setado ANTES do import dele
# (mesmo requisito do scrapers/magalu.py). `addBinding` obtém o execution
# context sem ligar o domínio Runtime do CDP, que o Akamai detecta.
os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Diretório do perfil dedicado (estável). Compartilhado pelos 3 scrapers e
# pelo script de setup — é aqui que o login da Shopee fica salvo.
DEFAULT_PROFILE_DIR = _PROJECT_ROOT / "data" / "chrome_profile"

# UA padrão coerente com Chrome desktop no Windows (o site cruza UA × cookies).
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Args de lançamento: desligam sinais óbvios de automação sem recorrer a
# stealth-JS (que num Chrome real seria contraproducente).
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=IsolateOrigins,site-per-process",
]

# Remove a barra "Chrome está sendo controlado por software de teste".
_IGNORE_DEFAULT_ARGS = ["--enable-automation"]


def is_local_chrome_enabled() -> bool:
    """True quando ``RAC_LOCAL_CHROME`` está ligado (opt-in explícito)."""
    return os.getenv("RAC_LOCAL_CHROME", "").strip().lower() in (
        "1", "true", "yes", "sim", "on"
    )


def _resolve_profile_dir() -> Path:
    """Diretório do perfil dedicado (env ``RAC_CHROME_PROFILE_DIR`` sobrepõe)."""
    override = os.getenv("RAC_CHROME_PROFILE_DIR", "").strip()
    return Path(override) if override else DEFAULT_PROFILE_DIR


def _resolve_headless() -> bool:
    """Headless off por padrão — o sensor.js detecta Chromium headless.

    Só liga headless se ``RAC_LOCAL_HEADLESS`` for explicitamente truthy (útil
    combinado com ``xvfb-run`` num display virtual, quando disponível).
    """
    return os.getenv("RAC_LOCAL_HEADLESS", "").strip().lower() in (
        "1", "true", "yes", "sim", "on"
    )


def _import_sync_playwright() -> Tuple[Optional[Any], str]:
    """Resolve ``sync_playwright``, preferindo o fork rebrowser-playwright."""
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


class LocalBrowser:
    """
    Wrapper de um ``launch_persistent_context`` sobre o perfil dedicado.

    Compartilhado por todos os scrapers de uma execução: cada scraper abre a
    sua própria aba (``new_page``) e fecha SÓ essa aba ao terminar. O contexto
    (a janela do Chrome) é fechado uma única vez, no fim da coleta, via
    ``close_local_browser`` (também registrado em ``atexit``).
    """

    def __init__(self) -> None:
        self._pw_handle: Optional[Any] = None
        self.context: Optional[Any] = None
        self.flavor: str = ""
        self.profile_dir: Path = _resolve_profile_dir()
        self.user_agent: str = _DEFAULT_UA
        # Domínios já aquecidos nesta sessão (evita repetir warm-up por scraper).
        self._warmed_hosts: set = set()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def launch(self) -> bool:
        """Abre o Chrome persistente. Retorna True se pronto para uso."""
        if self.context is not None:
            return True

        sync_playwright, flavor = _import_sync_playwright()
        if sync_playwright is None:
            logger.error(
                "[LocalBrowser] Playwright não instalado. Execute: "
                "pip install rebrowser-playwright && "
                "python -m rebrowser_playwright install chromium"
            )
            return False
        self.flavor = flavor
        if flavor != "rebrowser-playwright":
            logger.warning(
                "[LocalBrowser] Playwright STOCK — o Runtime.enable é visível "
                "pro sensor.js do Akamai. Instale o fork: "
                "pip install rebrowser-playwright"
            )

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        profile_fresh = not any(self.profile_dir.iterdir())
        headless = _resolve_headless()

        try:
            self._pw_handle = sync_playwright().start()
        except Exception as exc:
            logger.error(f"[LocalBrowser] Falha ao iniciar Playwright: {exc}")
            return False

        context = None
        channel_used = None
        for channel in ("chrome", "msedge", None):
            try:
                context = self._pw_handle.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=headless,
                    channel=channel,
                    user_agent=self.user_agent,
                    viewport={"width": 1366, "height": 768},
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                    args=_LAUNCH_ARGS,
                    ignore_default_args=_IGNORE_DEFAULT_ARGS,
                )
                channel_used = channel or "chromium"
                break
            except Exception as exc:
                msg = str(exc).lower()
                if "singletonlock" in msg or "already in use" in msg or "profile" in msg and "lock" in msg:
                    logger.error(
                        "[LocalBrowser] O perfil já está em uso por outro Chrome. "
                        "Feche o Chrome de setup (setup_local_profile) e qualquer "
                        f"Chrome aberto em {self.profile_dir} e tente de novo."
                    )
                    self._safe_stop_handle()
                    return False
                logger.debug(f"[LocalBrowser] launch channel={channel} falhou: {exc}")
                continue

        if context is None:
            logger.error(
                "[LocalBrowser] Não foi possível abrir nenhum Chrome "
                "(chrome/msedge/chromium). Rode: "
                "python -m rebrowser_playwright install chromium"
            )
            self._safe_stop_handle()
            return False

        self.context = context
        context.set_default_timeout(45_000)

        # Remoção mínima do navigator.webdriver — num Chrome real o resto do
        # fingerprint já é legítimo; stealth-JS pesado só pioraria.
        try:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        except Exception:
            pass

        logger.info(
            f"[LocalBrowser] Chrome persistente aberto "
            f"(channel={channel_used}, flavor={flavor}, "
            f"headless={headless}, profile={'NOVO' if profile_fresh else 'existente'}, "
            f"dir={self.profile_dir})"
        )
        if profile_fresh:
            logger.warning(
                "[LocalBrowser] Perfil NOVO — a Shopee vai exigir login. Rode 1x: "
                "python scripts/setup_local_profile.py"
            )
        return True

    def new_page(self) -> Optional[Any]:
        """Abre uma aba dedicada para um scraper. None se o browser não abriu."""
        if self.context is None and not self.launch():
            return None
        try:
            page = self.context.new_page()
            page.set_default_timeout(45_000)
            return page
        except Exception as exc:
            logger.error(f"[LocalBrowser] Falha ao abrir aba: {exc}")
            return None

    def close(self) -> None:
        """Fecha o contexto persistente (a janela do Chrome) e o Playwright."""
        try:
            if self.context is not None:
                self.context.close()
        except Exception:
            pass
        self.context = None
        self._safe_stop_handle()
        self._warmed_hosts.clear()

    def _safe_stop_handle(self) -> None:
        try:
            if self._pw_handle is not None:
                self._pw_handle.stop()
        except Exception:
            pass
        self._pw_handle = None

    # ------------------------------------------------------------------
    # Warm-up genérico (Akamai / sensor.js)
    # ------------------------------------------------------------------

    def warmup(
        self,
        page: Any,
        home_url: str,
        *,
        wait_abck: bool = False,
        host_key: Optional[str] = None,
    ) -> bool:
        """
        Visita a home do site, emula interação humana e (opcional) espera o
        Akamai validar o cookie ``_abck`` (vira ``~0~``).

        Um ``goto`` direto na rota de busca chega ao antibot antes do sensor.js
        validar a sessão → bloqueio. Aquecer a home primeiro promove o cookie
        para as rotas de busca/API. Idempotente por host (``host_key``).

        Args:
            page:      aba (Page) do scraper.
            home_url:  URL da home do site.
            wait_abck: se True, aguarda o ``_abck`` virar validado (Akamai).
            host_key:  chave para deduplicar warm-ups (default: derivada da URL).

        Returns:
            True se a home carregou (e, quando ``wait_abck``, o cookie validou).
        """
        key = host_key or home_url
        if key in self._warmed_hosts:
            return True

        try:
            page.goto(home_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            logger.warning(f"[LocalBrowser] Warm-up goto {home_url} falhou: {exc}")
            return False

        # Akamai/Shopee pontuam mouse/scroll — emula humano antes de esperar.
        try:
            for _ in range(3):
                page.mouse.move(random.randint(150, 1200), random.randint(150, 700))
                time.sleep(random.uniform(0.3, 0.7))
            page.mouse.wheel(0, random.randint(400, 900))
            time.sleep(random.uniform(0.8, 1.5))
        except Exception:
            pass

        if wait_abck:
            try:
                page.wait_for_function(
                    """() => {
                        const m = document.cookie.match(/_abck=([^;]+)/);
                        return m && /~0~/.test(decodeURIComponent(m[1]));
                    }""",
                    timeout=20_000,
                )
                logger.info(f"[LocalBrowser] _abck validado pelo sensor.js ✓ ({key})")
            except Exception:
                # Não valido = sessão que o Akamai ainda pode bloquear. NÃO
                # cacheia como aquecido: a próxima chamada re-tenta o warm-up
                # (dá mais tempo ao sensor.js) em vez de seguir com sessão fria.
                logger.warning(
                    f"[LocalBrowser] _abck não validou em 20s ({key}) — "
                    "não cacheando o warm-up; a próxima keyword re-tenta"
                )
                return False

        self._warmed_hosts.add(key)
        return True


# ---------------------------------------------------------------------------
# Singleton de processo — um único Chrome por execução, compartilhado
# ---------------------------------------------------------------------------

_LOCAL_BROWSER: Optional[LocalBrowser] = None
_ATEXIT_REGISTERED = False


def get_local_browser() -> Optional[LocalBrowser]:
    """
    Retorna o Chrome local compartilhado, abrindo-o na primeira chamada.

    Returns:
        O ``LocalBrowser`` pronto, ou None se desabilitado / falha ao abrir.
    """
    global _LOCAL_BROWSER, _ATEXIT_REGISTERED

    if not is_local_chrome_enabled():
        return None

    if _LOCAL_BROWSER is not None and _LOCAL_BROWSER.context is not None:
        return _LOCAL_BROWSER

    lb = LocalBrowser()
    if not lb.launch():
        return None

    _LOCAL_BROWSER = lb
    if not _ATEXIT_REGISTERED:
        atexit.register(close_local_browser)
        _ATEXIT_REGISTERED = True
    return _LOCAL_BROWSER


def close_local_browser() -> None:
    """Fecha o Chrome compartilhado (chamado no fim da coleta e via atexit)."""
    global _LOCAL_BROWSER
    if _LOCAL_BROWSER is not None:
        _LOCAL_BROWSER.close()
        _LOCAL_BROWSER = None
