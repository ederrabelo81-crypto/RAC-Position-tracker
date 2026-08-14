"""
scrapers/playwright_runtime.py — dono ÚNICO do handle síncrono do Playwright.

Por que este módulo existe (incidente de 14/08/2026)
----------------------------------------------------
A API **síncrona** do Playwright roda um event loop asyncio dentro de um
greenlet da própria thread. Enquanto um handle está vivo, esse loop continua
"rodando" do ponto de vista do asyncio — e o ``sync_playwright().start()``
SEGUINTE, na mesma thread, morre com::

    It looks like you are using Playwright Sync API inside the asyncio loop.
    Please use the Async API instead.

Ou seja: **dois handles vivos na mesma thread são impossíveis.** Isso não era
problema enquanto cada scraper abria e fechava o seu. Passou a ser quando o
``scrapers/local_browser.py`` (modo ``RAC_LOCAL_CHROME``) começou a manter um
handle aberto pela coleta inteira, para compartilhar o Chrome real logado entre
ML/Shopee/Magalu/Casas Bahia. A partir daí, TODO scraper que abre browser
próprio — Amazon, Google Shopping, Leroy, Dealers, os ``PlainBrowser`` do
``bestsellers/`` e os próprios fallbacks do ML/Magalu/Casas Bahia — estourava a
exceção acima no ``_launch()`` e era pulado inteiro pelo ``main.py``:

    ERROR | Mercado Livre: falhou com erro inesperado — It looks like you are
            using Playwright Sync API inside the asyncio loop.

A solução é ter **um handle por thread, com contagem de referências**: quem
precisa do Playwright chama :func:`acquire`, quem termina chama :func:`release`,
e o handle só é parado de verdade quando o último usuário solta. O estado é
guardado em ``threading.local`` porque a restrição do asyncio é por thread — uma
thread nova legitimamente ganha o seu próprio handle.

Uso::

    from scrapers.playwright_runtime import acquire, release

    playwright, flavor = acquire()          # stock primeiro
    playwright, flavor = acquire(True)      # rebrowser-playwright primeiro
    ...
    release()                               # sempre pareado com o acquire
"""

import threading
from typing import Any, Optional, Tuple

from loguru import logger

# Flavors possíveis do handle. O fork `rebrowser-playwright` oculta o
# `Runtime.enable` do CDP, que o sensor.js do Akamai (Magalu/Casas Bahia) usa
# para detectar automação — por isso os caminhos com Chrome real o preferem.
FLAVOR_REBROWSER = "rebrowser-playwright"
FLAVOR_STOCK = "playwright"

_state = threading.local()


class _Runtime:
    """Handle do Playwright de UMA thread, com contagem de referências."""

    def __init__(self) -> None:
        self.handle: Optional[Any] = None
        self.flavor: str = ""
        self.refs: int = 0
        self.flavor_warned: bool = False


def _runtime() -> _Runtime:
    """Estado desta thread (criado na primeira chamada)."""
    rt: Optional[_Runtime] = getattr(_state, "runtime", None)
    if rt is None:
        rt = _Runtime()
        _state.runtime = rt
    return rt


def _import_sync_playwright(prefer_rebrowser: bool) -> Tuple[Optional[Any], str]:
    """
    Resolve o callable ``sync_playwright`` na ordem de preferência pedida.

    Args:
        prefer_rebrowser: True para tentar o fork anti-detecção primeiro.

    Returns:
        ``(sync_playwright, flavor)`` ou ``(None, "")`` se nenhum instalado.
    """
    order = (
        (FLAVOR_REBROWSER, FLAVOR_STOCK)
        if prefer_rebrowser
        else (FLAVOR_STOCK, FLAVOR_REBROWSER)
    )
    for flavor in order:
        try:
            if flavor == FLAVOR_REBROWSER:
                from rebrowser_playwright.sync_api import sync_playwright
            else:
                from playwright.sync_api import sync_playwright
            return sync_playwright, flavor
        except ImportError:
            continue
    return None, ""


def acquire(prefer_rebrowser: bool = False) -> Tuple[Optional[Any], str]:
    """
    Devolve o handle do Playwright desta thread, iniciando-o se necessário.

    Cada chamada bem-sucedida incrementa a contagem de referências e **deve**
    ser pareada com um :func:`release`.

    Args:
        prefer_rebrowser: quando True, prefere o fork ``rebrowser-playwright``
            (anti-detecção do Akamai). Só tem efeito na primeira aquisição da
            thread — a partir daí o handle vivo é reaproveitado, porque um
            segundo handle é impossível (ver docstring do módulo).

    Returns:
        ``(playwright, flavor)``; ``(None, "")`` se o Playwright não está
        instalado ou o driver não subiu.
    """
    rt = _runtime()

    if rt.handle is not None:
        rt.refs += 1
        if (
            prefer_rebrowser
            and rt.flavor != FLAVOR_REBROWSER
            and not rt.flavor_warned
        ):
            rt.flavor_warned = True
            logger.warning(
                "[Playwright] Handle vivo é o stock, mas este caminho pede "
                "rebrowser-playwright (Runtime.enable oculto). Um segundo "
                "handle é impossível na mesma thread — seguindo com o stock. "
                "Para o Akamai não flagar, rode as plataformas com Chrome real "
                "antes das demais ou instale o fork: "
                "pip install rebrowser-playwright"
            )
        return rt.handle, rt.flavor

    sync_playwright, flavor = _import_sync_playwright(prefer_rebrowser)
    if sync_playwright is None:
        logger.error(
            "[Playwright] Nenhum Playwright instalado. Execute: "
            "pip install playwright && python -m playwright install chromium"
        )
        return None, ""

    try:
        rt.handle = sync_playwright().start()
    except Exception as exc:
        logger.error(f"[Playwright] Falha ao iniciar o runtime ({flavor}): {exc}")
        rt.handle = None
        return None, ""

    rt.flavor = flavor
    rt.refs = 1
    rt.flavor_warned = False
    logger.debug(f"[Playwright] Runtime iniciado ({flavor})")
    return rt.handle, flavor


def release() -> None:
    """
    Solta uma referência; para o handle quando a última é liberada.

    Seguro de chamar a mais (release sem acquire) — a contagem nunca fica
    negativa e um handle já parado é ignorado.
    """
    rt = _runtime()
    if rt.handle is None:
        rt.refs = 0
        return

    rt.refs -= 1
    if rt.refs > 0:
        return

    try:
        rt.handle.stop()
    except Exception as exc:
        logger.debug(f"[Playwright] Erro ao parar o runtime: {exc}")
    finally:
        rt.handle = None
        rt.flavor = ""
        rt.refs = 0
        rt.flavor_warned = False


# Trechos que o Playwright usa quando a aba/browser do outro lado morreu. A
# classe (`TargetClosedError`) cobre o caso comum, mas o driver também devolve
# `Error` com essas mensagens — e o fork rebrowser tem classes próprias, então
# comparar por identidade de classe não serve.
_CLOSED_MARKERS = (
    "target page, context or browser has been closed",
    "target closed",
    "browser has been closed",
    "browser has disconnected",
    "connection closed",
    "target crashed",
)


def is_target_closed(exc: BaseException) -> bool:
    """
    True quando a exceção significa "a aba/o browser do outro lado morreu".

    Distinguir isso de timeout/bloqueio é o que permite ao scraper reagir
    certo: browser morto se resolve reabrindo aba (ou degradando para HTTP),
    enquanto bloqueio anti-bot pede parar a keyword.
    """
    if type(exc).__name__ == "TargetClosedError":
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _CLOSED_MARKERS)


def is_active() -> bool:
    """True se esta thread tem um handle do Playwright vivo."""
    return _runtime().handle is not None


def active_flavor() -> str:
    """Flavor do handle vivo (``""`` quando não há nenhum)."""
    return _runtime().flavor


def refcount() -> int:
    """Nº de usuários que ainda não soltaram o handle (diagnóstico/testes)."""
    return _runtime().refs


def shutdown() -> None:
    """
    Para o handle desta thread imediatamente, ignorando a contagem.

    Só para encerramento de processo e testes — no fluxo normal use
    :func:`release`, que respeita os demais usuários.
    """
    rt = _runtime()
    if rt.handle is None:
        rt.refs = 0
        return
    try:
        rt.handle.stop()
    except Exception as exc:
        logger.debug(f"[Playwright] Erro ao parar o runtime: {exc}")
    finally:
        rt.handle = None
        rt.flavor = ""
        rt.refs = 0
        rt.flavor_warned = False
