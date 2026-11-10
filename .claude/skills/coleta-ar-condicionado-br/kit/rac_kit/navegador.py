"""Chrome COMUM, em perfil dedicado, controlado pela porta de depuração (CDP).

Por que assim
-------------
Três caminhos óbvios falham nos marketplaces brasileiros:

  * Browser aberto pelo Playwright (``launch``): sobe com marcas de automação.
    O Akamai (Magalu, Casas Bahia) nega com 403 e o Google recusa o login.
  * Porta de depuração no perfil PADRÃO do Chrome: o Chrome 136+ ignora.
  * COPIAR o perfil do usuário para outra pasta: o Chrome desloga as contas.

O que funciona: abrir o Chrome de verdade, sem nenhuma flag de automação, numa
pasta de perfil PRÓPRIA e ESTÁVEL (criada vazia, logada uma vez à mão), com a
porta de depuração ligada — e só então conectar nele. O login é feito sem
nenhum cliente conectado, então o site vê um navegador humano. A coleta roda
no IP residencial da máquina, que é o que os antibots aceitam.

Configuração por variável de ambiente (todas opcionais):
  COLETA_CHROME_EXE    caminho do chrome.exe, se não estiver no lugar padrão
  COLETA_PERFIL_DIR    pasta do perfil dedicado (padrão: ~/.coleta-ac/perfil)
  COLETA_CDP_PORTA     porta de depuração (padrão: 9222)
  COLETA_FECHAR_CHROME 1 fecha o Chrome ao fim (padrão: fica aberto, "quente")
"""

from __future__ import annotations

import logging
import os
import random
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, List, Optional, Tuple

# O fork precisa desta variável ANTES de ser importado: ela faz o Playwright
# obter o contexto de execução sem ligar o domínio Runtime, que o Akamai vê.
os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

log = logging.getLogger("rac_kit.navegador")

PORTA_PADRAO = 9222
PERFIL_PADRAO = Path.home() / ".coleta-ac" / "perfil"
MAX_RECONEXOES = 3

_FECHADO = (
    "target page, context or browser has been closed",
    "target closed",
    "browser has been closed",
    "connection closed",
)


def navegador_morreu(exc: BaseException) -> bool:
    """True se o erro é "o Chrome foi fechado", e não bloqueio do site.

    A distinção muda a resposta: bloqueio encerra a keyword; browser morto
    pede reconexão e nova tentativa.
    """
    return any(marca in str(exc).lower() for marca in _FECHADO)


def pasta_do_perfil() -> Path:
    """Pasta do perfil dedicado (``COLETA_PERFIL_DIR`` sobrepõe o padrão)."""
    alternativa = os.getenv("COLETA_PERFIL_DIR", "").strip()
    return Path(alternativa) if alternativa else PERFIL_PADRAO


def porta_cdp() -> int:
    """Porta de depuração (``COLETA_CDP_PORTA``, padrão 9222)."""
    bruto = os.getenv("COLETA_CDP_PORTA", "").strip()
    return int(bruto) if bruto.isdigit() else PORTA_PADRAO


def achar_chrome() -> Optional[str]:
    """Caminho do Chrome instalado (ou do Edge, como reserva)."""
    alternativa = os.getenv("COLETA_CHROME_EXE", "").strip()
    if alternativa and Path(alternativa).exists():
        return alternativa
    if os.name == "nt":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidatos = [
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Google\Chrome\Application\chrome.exe",
            rf"{local}\Google\Chrome\Application\chrome.exe" if local else "",
            rf"{pf}\Microsoft\Edge\Application\msedge.exe",
            rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
        ]
    else:
        candidatos = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    for caminho in candidatos:
        if caminho and Path(caminho).exists():
            return caminho
    return None


def argumentos_chrome(porta: int, perfil: Path, url: Optional[str] = None) -> List[str]:
    """Argumentos do Chrome COMUM — nenhum deles é flag de automação.

    ``--remote-allow-origins=*`` é exigido pelo Chrome 111+ para aceitar a
    conexão. ``--restore-last-session`` fica de fora de propósito: é um switch
    por presença, e incluí-lo reabriria as abas da sessão anterior.
    """
    args = [
        f"--remote-debugging-port={porta}",
        f"--user-data-dir={perfil}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--homepage=about:blank",
        "--disable-extensions",
    ]
    if url:
        args.append(url)
    return args


def endpoint_cdp(porta: int) -> Optional[str]:
    """``http://127.0.0.1:<porta>`` se já há um Chrome ouvindo, senão None."""
    # Sem proxy: com proxy de sistema configurado, a sonda em 127.0.0.1 sairia
    # por ele, falharia, e o kit abriria um segundo Chrome no mesmo perfil.
    direto = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with direto.open(
            f"http://127.0.0.1:{porta}/json/version", timeout=2
        ) as resp:
            if resp.status == 200:
                return f"http://127.0.0.1:{porta}"
    except OSError:
        return None
    return None


def abrir_chrome(
    porta: int, perfil: Path, url: Optional[str] = None
) -> Optional[subprocess.Popen]:
    """Abre o Chrome comum, destacado deste processo, no perfil dedicado."""
    exe = achar_chrome()
    if exe is None:
        log.error(
            "Chrome não encontrado. Instale o Google Chrome ou defina "
            "COLETA_CHROME_EXE com o caminho do executável."
        )
        return None
    perfil.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # destacado
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen([exe] + argumentos_chrome(porta, perfil, url), **kwargs)
    except OSError as exc:
        log.error("Falha ao abrir o Chrome: %s", exc)
        return None


def _importar_playwright() -> Tuple[Optional[Any], str]:
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


class ChromeLocal:
    """Conexão única com o Chrome do perfil dedicado, para toda a coleta.

    Use como gerenciador de contexto. Peça cada aba a ``nova_aba()``: se a
    janela tiver sido fechada no meio da coleta, ele reconecta (até
    ``MAX_RECONEXOES`` vezes) em vez de devolver um contexto morto.

    Um processo só pode ter UM ``sync_playwright()`` vivo por thread. Esta
    classe é a dona dele: não abra outro por fora.
    """

    def __init__(self) -> None:
        self.perfil = pasta_do_perfil()
        self.porta = porta_cdp()
        self.sabor = ""
        self._pw: Optional[Any] = None
        self._browser: Optional[Any] = None
        self._contexto: Optional[Any] = None
        self._processo: Optional[subprocess.Popen] = None
        self._reconexoes = 0
        self._aquecidos: set = set()

    def __enter__(self) -> "ChromeLocal":
        if not self.conectar():
            raise RuntimeError("não foi possível conectar ao Chrome local")
        return self

    def __exit__(self, *exc: object) -> None:
        self.fechar()

    def vivo(self) -> bool:
        """True quando a conexão ainda responde."""
        if self._browser is None or self._contexto is None:
            return False
        try:
            return bool(self._browser.is_connected())
        except Exception:  # noqa: BLE001 — qualquer falha aqui significa "morto"
            return False

    def conectar(self) -> bool:
        """Garante o Chrome aberto e conecta nele. True se pronto."""
        if self.vivo():
            return True
        self._soltar()

        fabrica, self.sabor = _importar_playwright()
        if fabrica is None:
            log.error("Playwright ausente. Rode: pip install -r requirements.txt")
            return False
        if self.sabor != "rebrowser-playwright":
            log.warning(
                "Playwright comum em uso: sites com Akamai (Magalu, Casas Bahia) "
                "tendem a negar. Instale o fork: pip install rebrowser-playwright"
            )

        endpoint = endpoint_cdp(self.porta)
        if endpoint is None:
            self._processo = abrir_chrome(self.porta, self.perfil)
            if self._processo is None:
                return False
            for _ in range(40):  # ~20 s
                time.sleep(0.5)
                endpoint = endpoint_cdp(self.porta)
                if endpoint:
                    break
            if endpoint is None:
                log.error(
                    "O Chrome não abriu a porta %s em 20 s. Feche as janelas "
                    "do Chrome que usam este perfil e tente de novo.", self.porta,
                )
                return False
        else:
            log.info("Reutilizando o Chrome já aberto em %s", endpoint)

        try:
            self._pw = fabrica().start()
            self._browser = self._pw.chromium.connect_over_cdp(endpoint, timeout=15_000)
            contextos = self._browser.contexts
            self._contexto = contextos[0] if contextos else self._browser.new_context()
            self._contexto.set_default_timeout(45_000)
        except Exception as exc:  # noqa: BLE001 — erro do driver, sem tipo estável
            log.error("Falha ao conectar em %s: %s", endpoint, exc)
            self._soltar()
            return False
        log.info("Conectado ao Chrome real (%s), perfil %s", self.sabor, self.perfil)
        return True

    def nova_aba(self) -> Optional[Any]:
        """Abre uma aba. Reconecta uma vez se o Chrome tiver sido fechado."""
        for tentativa in (1, 2):
            if not self.vivo() and not self._reconectar():
                return None
            try:
                aba = self._contexto.new_page()  # type: ignore[union-attr]
                aba.set_default_timeout(45_000)
                return aba
            except Exception as exc:  # noqa: BLE001
                log.warning("A aba não abriu (%s)", exc)
                self._soltar()
                if tentativa == 2:
                    return None
        return None

    def aquecer(self, aba: Any, home: str, esperar_abck: bool = False) -> bool:
        """Visita a home e age como gente antes da primeira busca.

        Ir direto à URL de busca chega ao antibot antes de o sensor validar a
        sessão. Em sites com Akamai, ``esperar_abck`` aguarda o cookie
        ``_abck`` ser validado (passa a conter ``~0~``).

        Returns:
            True se a home carregou (e o cookie validou, quando pedido). Um
            aquecimento que não validou NÃO é guardado: a próxima chamada
            tenta de novo.
        """
        if home in self._aquecidos:
            return True
        try:
            aba.goto(home, wait_until="domcontentloaded", timeout=45_000)
            for _ in range(3):
                aba.mouse.move(random.randint(150, 1200), random.randint(150, 700))
                time.sleep(random.uniform(0.3, 0.7))
            aba.mouse.wheel(0, random.randint(400, 900))
            time.sleep(random.uniform(0.8, 1.5))
        except Exception as exc:  # noqa: BLE001
            log.warning("Aquecimento em %s falhou: %s", home, exc)
            return False
        if esperar_abck:
            try:
                aba.wait_for_function(
                    "() => { const m = document.cookie.match(/_abck=([^;]+)/);"
                    " return m && /~0~/.test(decodeURIComponent(m[1])); }",
                    timeout=20_000,
                )
            except Exception:  # noqa: BLE001 — timeout: sessão ainda em desafio
                log.warning("Cookie _abck não validou em 20 s (%s)", home)
                return False
        self._aquecidos.add(home)
        return True

    def fechar(self) -> None:
        """Desconecta. O Chrome fica aberto, salvo ``COLETA_FECHAR_CHROME=1``."""
        self._soltar()
        if self._processo is not None and os.getenv("COLETA_FECHAR_CHROME") == "1":
            try:
                self._processo.terminate()
            except OSError:
                pass
        self._processo = None

    def _reconectar(self) -> bool:
        if self._reconexoes >= MAX_RECONEXOES:
            log.error("O Chrome caiu %s vezes nesta coleta — desistindo.", self._reconexoes)
            return False
        self._reconexoes += 1
        log.warning("Conexão caiu — reconectando (%s/%s)", self._reconexoes, MAX_RECONEXOES)
        return self.conectar()

    def _soltar(self) -> None:
        """Solta a conexão e o Playwright, sem matar o processo do Chrome."""
        for alvo, metodo in ((self._browser, "close"), (self._pw, "stop")):
            if alvo is not None:
                try:
                    getattr(alvo, metodo)()  # em CDP, close() só desconecta
                except Exception:  # noqa: BLE001
                    pass
        self._browser = self._contexto = self._pw = None
        # Aba nova depois de uma queda é sessão fria: aquecer de novo.
        self._aquecidos.clear()
