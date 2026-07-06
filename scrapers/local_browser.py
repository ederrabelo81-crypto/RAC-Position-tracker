"""
scrapers/local_browser.py — Chrome local COMUM, dedicado e LOGADO, atacado via
CDP e compartilhado pelos scrapers protegidos por antibot (Shopee, Magalu,
Casas Bahia).

Por que este módulo existe
--------------------------
A abordagem de CDP com perfil COPIADO (``C:\\chrome-rac-cdp``) falhava porque
copiar o perfil DESLOGA as contas (proteção "perfil realocado" do Chrome), e
apontar o ``--remote-debugging-port`` para o perfil PADRÃO não funciona (o
Chrome 136+ ignora a flag no perfil padrão).

E lançar o Chrome via ``launch_persistent_context`` (Playwright) resolvia o
login, mas re-introduzia a detecção de automação: o browser sobe com flags de
automação e ``navigator.webdriver``, o que o Akamai da Magalu/Casas Bahia
bloqueia de imediato (403) e o Google recusa no login ("navegador pode não ser
seguro").

Solução (a que funcionava para a Magalu, com o bug do perfil corrigido)
-----------------------------------------------------------------------
Abrimos um **Chrome COMUM** (o mesmo binário do usuário), como um browser de
verdade — SEM flags de automação, SEM ``navigator.webdriver`` — apontando para
um diretório de perfil DEDICADO e ESTÁVEL (``data/chrome_profile/``), com a
porta de debug ligada. Depois **atacamos via CDP** (``connect_over_cdp``) com o
fork ``rebrowser-playwright`` (que oculta o ``Runtime.enable``).

  * Diretório DEDICADO (não uma cópia do perfil padrão) → o Chrome 136+ permite
    a porta de debug E o login persiste (o Chrome não o trata como realocado).
  * Chrome COMUM → durante o login manual (setup) nenhum cliente CDP está
    conectado, então a página vê um browser 100% humano — o login pelo Google
    passa. Na coleta, o CDP ataca esse mesmo Chrome real (fingerprint aceito).
  * Roda no notebook do usuário (IP residencial) — a combinação que os antibots
    aceitam.
  * Um único Chrome por execução, compartilhado pelos 3 scrapers (cada um abre a
    sua aba). O Chrome fica aberto entre execuções (perfil "quente"); só
    desconectamos o CDP no fim.

Ativação
--------
``RAC_LOCAL_CHROME=1`` (o launcher ``scripts/collect_local_authenticated``
já faz isso). Sem essa env, nada muda (VM/GitHub Actions seguem no caminho
antigo — sem regressão).
"""

import atexit
import os
import random
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional, Tuple

from loguru import logger

# O patch de runtime do rebrowser precisa ser setado ANTES do import dele
# (mesmo requisito do scrapers/magalu.py). `addBinding` obtém o execution
# context sem ligar o domínio Runtime do CDP, que o Akamai detecta.
os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Diretório do perfil dedicado (estável). Compartilhado pelos 3 scrapers e
# pelo script de setup — é aqui que o login da Shopee fica salvo. Não é uma
# cópia do perfil do usuário: é um perfil próprio do projeto, logado 1x.
DEFAULT_PROFILE_DIR = _PROJECT_ROOT / "data" / "chrome_profile"

_DEFAULT_CDP_PORT = 9222

# Args do Chrome COMUM (sem NADA de automação). --remote-allow-origins=* é
# obrigatório para o connect_over_cdp funcionar no Chrome 111+.
def _chrome_args(port: int, profile_dir: Path, start_url: Optional[str] = None) -> list:
    # Obs: NÃO passar --restore-last-session — é um switch por PRESENÇA (o Chrome
    # o lê via HasSwitch, ignorando "=false"), então incluí-lo ATIVA a
    # restauração. O padrão (sem a flag) já não restaura as abas anteriores.
    args = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--homepage=about:blank",
    ]
    if start_url:
        args.append(start_url)
    return args


def is_local_chrome_enabled() -> bool:
    """True quando ``RAC_LOCAL_CHROME`` está ligado (opt-in explícito)."""
    return os.getenv("RAC_LOCAL_CHROME", "").strip().lower() in (
        "1", "true", "yes", "sim", "on"
    )


def _resolve_profile_dir() -> Path:
    """Diretório do perfil dedicado (env ``RAC_CHROME_PROFILE_DIR`` sobrepõe)."""
    override = os.getenv("RAC_CHROME_PROFILE_DIR", "").strip()
    return Path(override) if override else DEFAULT_PROFILE_DIR


def _resolve_port() -> int:
    """Porta do DevTools Protocol (env ``RAC_CDP_PORT``, padrão 9222)."""
    raw = os.getenv("RAC_CDP_PORT", "").strip()
    if raw.isdigit():
        return int(raw)
    return _DEFAULT_CDP_PORT


def _keep_chrome_open() -> bool:
    """Se True (padrão), deixa o Chrome aberto entre execuções (perfil quente).

    Defina ``RAC_LOCAL_CHROME_KEEP=0`` para encerrar o Chrome que ESTE processo
    abriu ao fim da coleta.
    """
    return os.getenv("RAC_LOCAL_CHROME_KEEP", "1").strip().lower() not in (
        "0", "false", "no", "nao", "off"
    )


def find_chrome_exe() -> Optional[str]:
    """Localiza o executável do Chrome (ou Edge como fallback).

    Prioriza ``RAC_CHROME_EXE``. Cobre os caminhos padrão do Windows (o alvo
    principal deste modo) e alguns de Linux/Mac para dev/teste.
    """
    override = os.getenv("RAC_CHROME_EXE", "").strip()
    if override and Path(override).exists():
        return override

    candidates = []
    if os.name == "nt":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pfx86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pfx86}\Google\Chrome\Application\chrome.exe",
        ]
        if local:
            candidates.append(rf"{local}\Google\Chrome\Application\chrome.exe")
        candidates += [
            rf"{pf}\Microsoft\Edge\Application\msedge.exe",
            rf"{pfx86}\Microsoft\Edge\Application\msedge.exe",
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/opt/pw-browsers/chromium",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def cdp_endpoint_if_up(port: int) -> Optional[str]:
    """Retorna ``http://127.0.0.1:{port}`` se já há um Chrome ouvindo, senão None."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=2
        ) as resp:
            if resp.status == 200:
                return f"http://127.0.0.1:{port}"
    except Exception:
        return None
    return None


def spawn_chrome(
    port: int,
    profile_dir: Path,
    start_url: Optional[str] = None,
) -> Optional[subprocess.Popen]:
    """
    Abre um Chrome COMUM (destacado) com a porta de debug no perfil dedicado.

    Destacado (survive-parent) porque o Chrome fica aberto entre execuções — é
    o modelo comprovado do CDP (perfil "quente" para o Akamai). Retorna o
    processo, ou None se o Chrome não foi encontrado.
    """
    exe = find_chrome_exe()
    if exe is None:
        logger.error(
            "[LocalBrowser] Chrome não encontrado. Instale o Google Chrome ou "
            "defina RAC_CHROME_EXE com o caminho do chrome.exe."
        )
        return None

    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [exe] + _chrome_args(port, profile_dir, start_url)

    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP → sobrevive ao Python
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(args, **kwargs)
        logger.info(
            f"[LocalBrowser] Chrome comum aberto (exe={Path(exe).name}, "
            f"port={port}, profile={profile_dir})"
        )
        return proc
    except Exception as exc:
        logger.error(f"[LocalBrowser] Falha ao abrir o Chrome: {exc}")
        return None


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
    Wrapper de um ``connect_over_cdp`` sobre o Chrome comum do perfil dedicado.

    Compartilhado por todos os scrapers de uma execução: cada scraper abre a
    sua própria aba (``new_page``) e fecha SÓ essa aba. O CDP é desconectado uma
    única vez, no fim da coleta (``close_local_browser``, também em ``atexit``);
    o Chrome fica aberto (perfil quente) a menos que ``RAC_LOCAL_CHROME_KEEP=0``.
    """

    def __init__(self) -> None:
        self._pw_handle: Optional[Any] = None
        self._browser: Optional[Any] = None
        self.context: Optional[Any] = None
        self.flavor: str = ""
        self.profile_dir: Path = _resolve_profile_dir()
        self.port: int = _resolve_port()
        self._spawned: Optional[subprocess.Popen] = None
        # Domínios já aquecidos nesta sessão (evita repetir warm-up por scraper).
        self._warmed_hosts: set = set()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def launch(self) -> bool:
        """Garante o Chrome comum + conecta via CDP. True se pronto para uso."""
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
                "[LocalBrowser] ⚠️  Playwright STOCK detectado — o Runtime.enable "
                "do CDP é visível pro sensor.js do Akamai (Magalu/Casas Bahia "
                "vão tomar 403). INSTALE o fork antes de coletar:\n"
                "    pip install rebrowser-playwright"
            )

        # 1) Já há um Chrome ouvindo na porta? (setup deixou aberto, ou run
        #    anterior). Reusa — o perfil quente é melhor pro antibot.
        endpoint = cdp_endpoint_if_up(self.port)
        if endpoint is None:
            # 2) Não: abre um Chrome comum e espera a porta subir.
            self._spawned = spawn_chrome(self.port, self.profile_dir)
            if self._spawned is None:
                return False
            for _ in range(40):  # ~20s
                time.sleep(0.5)
                endpoint = cdp_endpoint_if_up(self.port)
                if endpoint:
                    break
            if endpoint is None:
                logger.error(
                    f"[LocalBrowser] Chrome não expôs a porta de debug {self.port} "
                    "em 20s. Feche Chromes abertos nesse perfil e tente de novo."
                )
                return False
        else:
            logger.info(
                f"[LocalBrowser] Reutilizando Chrome já aberto em {endpoint}"
            )

        # 3) Ataca via CDP (rebrowser oculta o Runtime.enable).
        try:
            self._pw_handle = sync_playwright().start()
            self._browser = self._pw_handle.chromium.connect_over_cdp(
                endpoint, timeout=15_000
            )
        except Exception as exc:
            logger.error(
                f"[LocalBrowser] Falha ao conectar CDP em {endpoint}: {exc}"
            )
            self._safe_stop_handle()
            return False

        if not self._browser.contexts:
            logger.error("[LocalBrowser] Chrome sem contexto — abrindo aba nova")
            try:
                self.context = self._browser.new_context()
            except Exception:
                self._safe_stop_handle()
                return False
        else:
            self.context = self._browser.contexts[0]
        self.context.set_default_timeout(45_000)

        logger.info(
            f"[LocalBrowser] Conectado via CDP ({flavor}) em {endpoint} — "
            f"Chrome real, fingerprint nativo (perfil {self.profile_dir})"
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
        """Desconecta o CDP. Mantém o Chrome aberto (a menos que KEEP=0)."""
        try:
            if self._browser is not None:
                self._browser.close()  # em CDP, close() apenas DESCONECTA
        except Exception:
            pass
        self._browser = None
        self.context = None
        self._safe_stop_handle()
        self._warmed_hosts.clear()

        # Só encerra o Chrome se ESTE processo o abriu E o usuário pediu.
        if self._spawned is not None and not _keep_chrome_open():
            try:
                self._spawned.terminate()
            except Exception:
                pass
        self._spawned = None

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
                # cacheia como aquecido: a próxima chamada re-tenta o warm-up.
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
    Retorna o Chrome local compartilhado, abrindo/atacando-o na primeira chamada.

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
    """Desconecta o Chrome compartilhado (fim da coleta e via atexit)."""
    global _LOCAL_BROWSER
    if _LOCAL_BROWSER is not None:
        _LOCAL_BROWSER.close()
        _LOCAL_BROWSER = None
