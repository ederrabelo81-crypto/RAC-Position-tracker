"""Laço de coleta: keyword -> página -> diagnóstico -> cards -> buy box.

Três regras moldam este arquivo:

  * Bloqueio não se insiste. Depois de ``max_bloqueios`` páginas negadas em
    sequência o site é FREADO: as keywords restantes ficam registradas como
    não observadas e a coleta para. Repetir contra uma sessão marcada só
    aprofunda a marca.
  * Toda página que não virou dado deixa evidência (HTML em ``logs/``) e uma
    linha na cobertura. Keyword bloqueada não some: vira "não observada".
  * Navegador fechado não é bloqueio: reconecta e tenta de novo.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import bloqueio
from .extracao import extrair_cards, ler_buy_box
from .navegador import navegador_morreu
from .saida import montar_registro
from .sites import Site

log = logging.getLogger("rac_kit.coleta")


@dataclass
class Opcoes:
    """Parâmetros de uma coleta.

    Attributes:
        paginas: Páginas de resultado por keyword.
        buy_box: Quantos produtos por página têm o PDP aberto para ler quem
            vende (0 desliga). Cada PDP custa uma navegação: dimensione.
        pasta_logs: Onde gravar o HTML das páginas que não viraram dado.
        max_bloqueios: Páginas negadas em sequência até frear o site.
        espera_captcha: Segundos de tolerância para alguém resolver o CAPTCHA
            na janela do Chrome (0 desliga).
        dormir: Função de pausa (trocável em teste).
    """

    paginas: int = 1
    buy_box: int = 0
    pasta_logs: Path = Path("logs")
    max_bloqueios: int = 3
    espera_captcha: int = 120
    dormir: Callable[[float], None] = field(default=time.sleep, repr=False)


@dataclass
class Parcial:
    """Resultado acumulado de uma coleta, legível mesmo se ela for interrompida."""

    registros: List[Dict[str, Any]] = field(default_factory=list)
    cobertura: List[Dict[str, Any]] = field(default_factory=list)


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")[:40] or "kw"


def _gravar_evidencia(html: str, causa: str, site: Site, keyword: str,
                      pagina: int, pasta: Path) -> Optional[str]:
    """Salva o HTML com a causa no NOME do arquivo — o nome já é o diagnóstico."""
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        caminho = pasta / f"{causa}_{site.chave}_{_slug(keyword)}_p{pagina}.html"
        caminho.write_text(html or "", encoding="utf-8")
        return str(caminho)
    except OSError as exc:
        log.warning("Não foi possível gravar a evidência: %s", exc)
        return None


def _carregar(aba: Any, url: str, referer: str, opcoes: Opcoes) -> Tuple[str, str, Optional[int]]:
    """Navega com Referer, espera e rola a página. Devolve (html, url final, status)."""
    resposta = aba.goto(url, referer=referer, wait_until="domcontentloaded", timeout=45_000)
    status = resposta.status if resposta is not None else None
    opcoes.dormir(random.uniform(1.5, 3.0))
    for _ in range(4):  # resultados carregam sob rolagem
        aba.mouse.wheel(0, random.randint(900, 1600))
        opcoes.dormir(random.uniform(0.4, 0.9))
    return aba.content(), aba.url, status


def _esperar_captcha(aba: Any, site: Site, opcoes: Opcoes) -> bloqueio.Veredito:
    """Dá tempo para uma pessoa resolver o CAPTCHA na janela. Não o contorna."""
    log.warning(
        "CAPTCHA em %s — resolva na janela do Chrome (até %s s).",
        site.nome, opcoes.espera_captcha,
    )
    restante = opcoes.espera_captcha
    veredito = bloqueio.Veredito(bloqueio.CAPTCHA, "desafio de CAPTCHA na página")
    while restante > 0:
        opcoes.dormir(5)
        restante -= 5
        veredito = bloqueio.diagnosticar(aba.content(), site, aba.url)
        if veredito.causa != bloqueio.CAPTCHA:
            break
    return veredito


def _resolver_buy_box(chrome: Any, cards: List[Dict[str, Any]], referer: str,
                      opcoes: Opcoes) -> None:
    """Abre o PDP dos primeiros cards sem vendedor e grava quem vende."""
    alvos = [c for c in cards if not c.get("seller")][: opcoes.buy_box]
    if not alvos:
        return
    aba = chrome.nova_aba()
    if aba is None:
        return
    try:
        for card in alvos:
            try:
                aba.goto(card["url"], referer=referer,
                         wait_until="domcontentloaded", timeout=45_000)
                opcoes.dormir(random.uniform(2.0, 5.0))
                vendedor, fonte = ler_buy_box(aba.content())
            except Exception as exc:  # noqa: BLE001 — erro do driver
                if navegador_morreu(exc):
                    log.warning("Chrome fechado durante a leitura de buy box.")
                    return
                log.debug("PDP %s falhou: %s", card["url"], exc)
                continue
            # Sem leitura fica None: produto sem buy box observada não entra
            # em ranking de vendedor.
            card["buy_box_seller"], card["fonte_buy_box"] = vendedor, fonte
    finally:
        try:
            aba.close()
        except Exception:  # noqa: BLE001
            pass


def coletar_site(
    chrome: Any,
    site: Site,
    keywords: Sequence[str],
    opcoes: Optional[Opcoes] = None,
    parcial: Optional[Parcial] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Coleta as keywords de um site.

    Args:
        chrome: ``ChromeLocal`` conectado (ou objeto com ``nova_aba``/``aquecer``).
        site: Configuração do site.
        keywords: Buscas a fazer, na ordem.
        opcoes: Parâmetros; o padrão é 1 página, sem PDP.
        parcial: Onde acumular o resultado. Quem chama passa o seu para
            conseguir gravar o que já foi coletado se a coleta for
            interrompida (Ctrl+C, erro inesperado).

    Returns:
        ``(registros, cobertura)``. ``cobertura`` tem uma entrada por
        (keyword, página) tentada ou adiada, com ``causa`` e ``detalhe`` —
        inclusive as que NÃO viraram dado.
    """
    opcoes = opcoes or Opcoes()
    parcial = parcial if parcial is not None else Parcial()
    registros, cobertura = parcial.registros, parcial.cobertura
    bloqueios_seguidos = 0
    aba: Optional[Any] = None
    referer = site.home

    def anotar(keyword: str, pagina: int, causa: str, detalhe: str,
               produtos: int = 0, evidencia: Optional[str] = None) -> None:
        cobertura.append({
            "plataforma": site.nome, "keyword": keyword, "pagina": pagina,
            "causa": causa, "detalhe": detalhe, "produtos": produtos,
            "evidencia": evidencia,
        })

    for indice, keyword in enumerate(keywords):
        if bloqueios_seguidos >= opcoes.max_bloqueios:
            for adiada in keywords[indice:]:
                anotar(adiada, 1, "adiada",
                       f"site freado após {bloqueios_seguidos} páginas negadas seguidas")
            log.error("%s FREADO — %s keywords não observadas.",
                      site.nome, len(keywords) - indice)
            break

        vistos = 0
        for pagina in range(1, opcoes.paginas + 1):
            veredito: Optional[bloqueio.Veredito] = None
            html = ""
            for tentativa in (1, 2):
                if aba is None:
                    aba = chrome.nova_aba()
                    if aba is None:
                        break
                chrome.aquecer(aba, site.home, site.esperar_abck)
                try:
                    html, url_final, status = _carregar(
                        aba, site.url_busca(keyword, pagina, vistos), referer, opcoes
                    )
                except Exception as exc:  # noqa: BLE001 — erro do driver
                    if navegador_morreu(exc) and tentativa == 1:
                        aba = None  # reconecta e tenta a mesma página
                        continue
                    veredito = bloqueio.Veredito(
                        bloqueio.BLOQUEIO, f"navegação falhou: {exc}"
                    )
                    break
                veredito = bloqueio.diagnosticar(html, site, url_final, status)
                if veredito.causa == bloqueio.CAPTCHA and opcoes.espera_captcha > 0:
                    veredito = _esperar_captcha(aba, site, opcoes)
                    html, url_final = aba.content(), aba.url
                referer = url_final
                break

            if veredito is None:
                anotar(keyword, pagina, "sem_navegador", "o Chrome local não respondeu")
                bloqueios_seguidos = opcoes.max_bloqueios  # sem browser, nada adianta
                break

            if veredito.ok:
                bloqueios_seguidos = 0
                cards = extrair_cards(html, site, referer)
                if opcoes.buy_box > 0:
                    _resolver_buy_box(chrome, cards, referer, opcoes)
                registros.extend(montar_registro(c, site, keyword) for c in cards)
                anotar(keyword, pagina, bloqueio.OK, veredito.detalhe, len(cards))
                vistos += len(cards)
                log.info("%s | %s p%s: %s produtos", site.nome, keyword, pagina, len(cards))
            elif veredito.causa == bloqueio.VAZIA:
                bloqueios_seguidos = 0
                anotar(keyword, pagina, veredito.causa, veredito.detalhe)
                opcoes.dormir(random.uniform(*site.intervalo))
                break
            else:
                bloqueios_seguidos += 1
                evidencia = _gravar_evidencia(
                    html, veredito.causa, site, keyword, pagina, opcoes.pasta_logs
                )
                anotar(keyword, pagina, veredito.causa, veredito.detalhe,
                       evidencia=evidencia)
                log.warning("%s | %s p%s: %s (%s)", site.nome, keyword, pagina,
                            veredito.causa.upper(), veredito.detalhe)
                # Pausa também aqui: bater de novo logo após uma negação é o
                # que transforma um desafio em bloqueio.
                opcoes.dormir(random.uniform(*site.intervalo))
                break  # não adianta pedir a página 2 de uma busca negada

            opcoes.dormir(random.uniform(*site.intervalo))

    if aba is not None:
        try:
            aba.close()
        except Exception:  # noqa: BLE001
            pass
    return registros, cobertura
