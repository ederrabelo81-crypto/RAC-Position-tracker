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

from scrapers import playwright_runtime

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

# Quantas vezes uma run tenta ressuscitar o Chrome compartilhado antes de
# desistir. Fechar a janela no meio da coleta é acidente comum (e recuperável);
# cair sempre é sintoma de ambiente quebrado — aí insistir só custa 20s por
# tentativa e atrasa a degradação para o caminho alternativo.
#
# O orçamento é da RUN (módulo), não da instância: quando o singleton morre e é
# substituído, um contador por instância zeraria e o teto anunciado viraria
# ficção — o Chrome poderia ser relançado indefinidamente.
_MAX_RECONNECTS = 3
_RECONNECTS_USED = 0

# Idem para o singleton: depois de N falhas de abertura no processo, para de
# tentar (o Chrome não existe / a porta está tomada) e deixa os scrapers
# seguirem pelo caminho antigo.
_MAX_LAUNCH_FAILURES = 2

# Args do Chrome COMUM (sem NADA de automação). --remote-allow-origins=* é
# obrigatório para o connect_over_cdp funcionar no Chrome 111+.
def _keep_extensions() -> bool:
    """Se True, sobe o Chrome COM as extensões do perfil (padrão: sem elas).

    O perfil dedicado costuma estar sincronizado com a conta Google do usuário,
    então ele herda as extensões dele. Ao abrir, várias disparam aba de
    boas-vindas/atualização de uma vez (visto em 31/07/2026: uma enxurrada de
    abas por cima do login). Extensão não serve para nada na coleta — e o
    antibot não lê a linha de comando do Chrome, só o fingerprint da página.
    Ligue com ``RAC_CHROME_KEEP_EXTENSIONS=1`` se precisar de alguma.
    """
    return os.getenv("RAC_CHROME_KEEP_EXTENSIONS", "").strip().lower() in (
        "1", "true", "yes", "sim", "on"
    )


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
    if not _keep_extensions():
        args.append("--disable-extensions")
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
    """Resolve ``sync_playwright``, preferindo o fork rebrowser-playwright.

    Mantido para os scripts de setup (``scripts/setup_local_profile.py``), que
    rodam em processo próprio. Na coleta, quem inicia o Playwright é o
    ``scrapers/playwright_runtime`` — handle único por thread.
    """
    return playwright_runtime._import_sync_playwright(prefer_rebrowser=True)


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
        if self.is_alive():
            return True

        # Único ponto do projeto que abre Chrome — é aqui que a desistência tem
        # que valer. Enquanto o teto era conferido só no `reconnect()` e no
        # `get_local_browser()`, quem tivesse guardado uma instância (a Magalu
        # guarda) relançava o browser por dentro do `new_page()` e o modo local
        # "desligado" voltava sozinho.
        if _LOCAL_MODE_DISABLED:
            logger.debug(
                "[LocalBrowser] Modo local desligado nesta run — não abrindo "
                "Chrome."
            )
            return False

        # Estado parcial de uma tentativa anterior (conexão caiu, contexto
        # morto): solta tudo antes de reconectar, senão o handle órfão do
        # Playwright segura o event loop da thread e o próximo
        # `sync_playwright().start()` — o dos scrapers com browser próprio —
        # morre com "Sync API inside the asyncio loop".
        if self._browser is not None or self.context is not None:
            self._disconnect()

        self._pw_handle, flavor = playwright_runtime.acquire(prefer_rebrowser=True)
        if self._pw_handle is None:
            logger.error(
                "[LocalBrowser] Playwright não instalado. Execute: "
                "pip install rebrowser-playwright && "
                "python -m rebrowser_playwright install chromium"
            )
            return False
        self.flavor = flavor
        if flavor != playwright_runtime.FLAVOR_REBROWSER:
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
                self._release_handle()
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
                self._abandon_spawned()
                self._release_handle()
                return False
        else:
            logger.info(
                f"[LocalBrowser] Reutilizando Chrome já aberto em {endpoint}"
            )

        # 3) Ataca via CDP (rebrowser oculta o Runtime.enable).
        try:
            self._browser = self._pw_handle.chromium.connect_over_cdp(
                endpoint, timeout=15_000
            )
        except Exception as exc:
            logger.error(
                f"[LocalBrowser] Falha ao conectar CDP em {endpoint}: {exc}"
            )
            self._browser = None
            self._abandon_spawned()
            self._release_handle()
            return False

        try:
            if not self._browser.contexts:
                logger.error("[LocalBrowser] Chrome sem contexto — abrindo aba nova")
                self.context = self._browser.new_context()
            else:
                self.context = self._browser.contexts[0]
            self.context.set_default_timeout(45_000)
        except Exception as exc:
            logger.error(f"[LocalBrowser] Contexto CDP indisponível: {exc}")
            self._disconnect()
            return False

        logger.info(
            f"[LocalBrowser] Conectado via CDP ({flavor}) em {endpoint} — "
            f"Chrome real, fingerprint nativo (perfil {self.profile_dir})"
        )
        return True

    def is_alive(self) -> bool:
        """
        True quando o CDP ainda responde — ou seja, dá pra abrir aba.

        O Chrome compartilhado é do usuário e some no meio da coleta com uma
        facilidade banal: janela fechada, update do Chrome, crash da aba. Antes
        desta checagem o singleton continuava entregando um contexto morto pelo
        resto da run, e cada keyword virava
        ``BrowserContext.new_page: Target page, context or browser has been
        closed`` (log de 14/08/2026, 5 keywords da Casas Bahia perdidas).
        """
        if self._browser is None or self.context is None:
            return False
        try:
            return bool(self._browser.is_connected())
        except Exception:
            return False

    def reconnect(self) -> bool:
        """
        Reata o CDP depois de a conexão cair (o Chrome pode ter sido fechado).

        Solta a conexão morta e refaz o ``launch()``: se o Chrome ainda estiver
        de pé, reconecta nele (perfil quente preservado); se tiver morrido,
        ``launch()`` abre um novo no mesmo perfil dedicado.

        Returns:
            True se voltou utilizável. False quando o limite de tentativas da
            run (:data:`_MAX_RECONNECTS`) já foi gasto — aí os scrapers devem
            cair para o caminho alternativo em vez de insistir.
        """
        global _RECONNECTS_USED

        if _RECONNECTS_USED >= _MAX_RECONNECTS:
            # Desliga o modo local de vez: sem isso o `get_local_browser()`
            # apenas descartava o singleton e abria OUTRO Chrome do zero, e o
            # teto não segurava nada — o coletor ficava relançando browser em
            # vez de degradar.
            _disable_local_mode(
                f"o Chrome local caiu {_RECONNECTS_USED}x nesta run"
            )
            return False

        _RECONNECTS_USED += 1
        logger.warning(
            f"[LocalBrowser] Conexão com o Chrome caiu — reconectando "
            f"({_RECONNECTS_USED}/{_MAX_RECONNECTS})..."
        )
        self._disconnect()
        if self.launch():
            logger.success("[LocalBrowser] Chrome local reconectado ✓")
            return True
        return False

    def _disconnect(self) -> None:
        """Solta CDP + handle do Playwright, mantendo o processo do Chrome."""
        try:
            if self._browser is not None:
                self._browser.close()  # em CDP, close() apenas DESCONECTA
        except Exception:
            pass
        self._browser = None
        self.context = None
        self._warmed_hosts.clear()
        self._release_handle()

    def close(self) -> None:
        """Desconecta o CDP. Mantém o Chrome aberto (a menos que KEEP=0)."""
        self._disconnect()
        self._abandon_spawned()

    def _release_handle(self) -> None:
        """Devolve a referência do handle compartilhado do Playwright."""
        if self._pw_handle is None:
            return
        self._pw_handle = None
        self.flavor = ""
        playwright_runtime.release()

    def _abandon_spawned(self) -> None:
        """
        Larga o Chrome que ESTE processo abriu — no fim da coleta ou no erro.

        Único lugar onde a regra do ``RAC_LOCAL_CHROME_KEEP`` mora. Com KEEP
        ligado (padrão) o processo fica de pé de propósito: no caminho de erro
        ele pode estar só lento para abrir a porta, e a tentativa seguinte o
        reaproveita em vez de abrir um segundo Chrome no mesmo perfil. Chamar
        isto no erro também importa porque a instância é descartada pelo
        ``get_local_browser`` — sem ele, ninguém mais teria a referência do
        processo e KEEP=0 deixaria de valer justamente ali.
        """
        if self._spawned is None:
            return
        if not _keep_chrome_open():
            try:
                self._spawned.terminate()
            except Exception:
                pass
        self._spawned = None

    def new_page(self) -> Optional[Any]:
        """
        Abre uma aba dedicada para um scraper. None se o browser não abriu.

        Uma falha aqui quase nunca é "aba demais": é o Chrome compartilhado
        que morreu. Por isso a primeira falha dispara uma reconexão e uma nova
        tentativa, em vez de condenar o scraper (e todos os seguintes) a um
        contexto fechado.
        """
        for attempt in (1, 2):
            if not self.is_alive() and not self.launch():
                return None
            try:
                page = self.context.new_page()
                page.set_default_timeout(45_000)
                return page
            except Exception as exc:
                if attempt == 1:
                    logger.warning(
                        f"[LocalBrowser] Aba não abriu ({exc}) — o Chrome "
                        "compartilhado provavelmente foi fechado."
                    )
                    if self.reconnect():
                        continue
                logger.error(f"[LocalBrowser] Falha ao abrir aba: {exc}")
                return None

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
_LAUNCH_FAILURES = 0
# Modo local desligado para o resto do processo: o Chrome provou não parar de
# pé. Um único estado de desistência para as duas formas de esgotamento
# (reconexões e aberturas) — enquanto era só um contador por caminho, cada um
# ignorava o teto do outro.
_LOCAL_MODE_DISABLED = False


def _disable_local_mode(motivo: str) -> None:
    """Desiste do Chrome compartilhado até o fim do processo."""
    global _LOCAL_MODE_DISABLED
    if _LOCAL_MODE_DISABLED:
        return
    _LOCAL_MODE_DISABLED = True
    logger.error(
        f"[LocalBrowser] Modo Chrome local DESLIGADO — {motivo}. O resto da "
        "coleta segue pelo caminho alternativo (HTTP/browser próprio), sujeito "
        "a Akamai/login gate. Confira o Chrome e o perfil dedicado: "
        "python scripts/setup_local_profile.py --site magalu"
    )


def get_local_browser() -> Optional[LocalBrowser]:
    """
    Retorna o Chrome local compartilhado, abrindo/atacando-o na primeira chamada.

    Auto-cura: se o Chrome da run anterior/atual morreu (janela fechada, update,
    crash), reconecta antes de devolver. Quando nem isso resolve, devolve None
    — que é o contrato que os scrapers já tratam, caindo para curl_cffi ou
    browser próprio. Devolver um handle morto, como antes, transformava um
    acidente recuperável em coleta zerada.

    Returns:
        O ``LocalBrowser`` pronto, ou None se desabilitado / falha ao abrir.
    """
    global _LOCAL_BROWSER, _ATEXIT_REGISTERED, _LAUNCH_FAILURES

    if not is_local_chrome_enabled():
        return None

    lb = _LOCAL_BROWSER
    if lb is not None:
        if lb.is_alive():
            return lb
        if lb.reconnect():
            return lb
        # Não voltou: derruba o singleton para não servir contexto morto.
        # O próximo scraper tenta do zero (novo Chrome), até o teto de falhas.
        close_local_browser()

    # Depois de desistir, desistiu: vale tanto para o teto de reconexões quanto
    # para o de aberturas. O teto de reconexões é conferido AQUI também, e não
    # só dentro do `reconnect()`: abrir um LocalBrowser novo é a mesma coisa que
    # reconectar, do ponto de vista de quem está esperando a coleta andar.
    if _RECONNECTS_USED >= _MAX_RECONNECTS:
        _disable_local_mode(
            f"o Chrome local caiu {_RECONNECTS_USED}x nesta run"
        )
    if _LOCAL_MODE_DISABLED:
        logger.debug("[LocalBrowser] Modo local desligado — seguindo sem ele.")
        return None

    lb = LocalBrowser()
    if not lb.launch():
        _LAUNCH_FAILURES += 1
        if _LAUNCH_FAILURES >= _MAX_LAUNCH_FAILURES:
            _disable_local_mode(
                f"o Chrome não abriu em {_LAUNCH_FAILURES} tentativas"
            )
        return None

    _LAUNCH_FAILURES = 0
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
