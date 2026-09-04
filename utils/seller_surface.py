"""
utils/seller_surface.py — Onde a disputa acontece: superfície de cada plataforma.

Por que existe
--------------
No produto voltado ao **seller**, "ganhou a buy box" só significa alguma coisa
onde há mais de um jogador. Na loja própria do lojista não há: ele define
sortimento, ordem da vitrine, preço e destaque. A coleta prova isso — conferido
em 04/09/2026, sobre 3 dias:

    Frigelar, Dufrio, Leveros, CentralAr, PoloAr…  buy_box_seller preenchido
    em 100% das linhas, e o vencedor é sempre o próprio dono do site.

Somar essas linhas ao win rate de buy box **infla o número e mente sobre a
competição**: o dealer apareceria ganhando 100% de um campeonato que ele joga
sozinho. Daí a classificação em três superfícies, e a regra dura de que win
rate de buy box só existe em ``marketplace``.

Por que não dá para derivar de ``config.PLATFORM_TYPE``
------------------------------------------------------
Porque ``PLATFORM_TYPE`` classifica o *porte comercial*, não a superfície:
``Leroy Merlin`` (marketplace, N lojistas disputam a página) e
``WebContinental`` (loja própria, um lojista só) compartilham o mesmo rótulo
``"Nacional Varejo Especializado"``. Derivar dali colocaria uma loja própria no
denominador de buy box.

A fonte de verdade da loja própria é ``scrapers.dealers.DEALER_CONFIGS``: se a
plataforma está lá, ela é um site que o próprio lojista opera —
``tests/test_seller_surface.py`` garante que este módulo e aquele dicionário
não divirjam.
"""
from __future__ import annotations

from typing import Dict, FrozenSet

__all__ = [
    "SUPERFICIE_MARKETPLACE",
    "SUPERFICIE_LOJA_PROPRIA",
    "SUPERFICIE_COMPARADOR",
    "MARKETPLACES",
    "LOJAS_PROPRIAS",
    "COMPARADORES",
    "superficie_de",
    "disputa_buybox",
    "mapa_superficies",
    "validar_registro",
]

SUPERFICIE_MARKETPLACE = "marketplace"
SUPERFICIE_LOJA_PROPRIA = "loja_propria"
SUPERFICIE_COMPARADOR = "comparador"

# ---------------------------------------------------------------------------
# Marketplaces — N lojistas disputam a MESMA página, com a mesma régua.
# É a única superfície onde buy box, share de vitrine e posição relativa
# significam competição. Os nomes são exatamente os de `coletas.plataforma`.
# ---------------------------------------------------------------------------
MARKETPLACES: FrozenSet[str] = frozenset({
    "Amazon",
    "Mercado Livre",
    "Magalu",
    "Casas Bahia",
    "Shopee",
    "Leroy Merlin",
})

# ---------------------------------------------------------------------------
# Comparador — o Google Shopping não tem buy box: ele lista lojas concorrendo
# por um clique. `qtd_sellers` ali é "quantas lojas comparando", que é uma
# pergunta legítima e DIFERENTE de buy box. Fica em superfície própria para não
# ser somado nem a marketplace nem a loja própria.
# ---------------------------------------------------------------------------
COMPARADORES: FrozenSet[str] = frozenset({
    "Google Shopping",
})


# ---------------------------------------------------------------------------
# Lojas próprias — um lojista só, dono da vitrine inteira.
#
# A lista é ESCRITA aqui, não importada de `scrapers.dealers`, por um motivo
# operacional: o app do seller é magro e não tem Playwright instalado, e
# importar `DEALER_CONFIGS` puxaria a stack de coleta inteira. O preço disso é
# divergir com o tempo — por isso `validar_registro()` compara as duas listas e
# `tests/test_seller_surface.py` reprova o PR se alguém adicionar um dealer
# aqui e esquecer ali (ou vice-versa).
# ---------------------------------------------------------------------------
LOJAS_PROPRIAS: FrozenSet[str] = frozenset({
    "ADias", "Angeloni", "ArCerto", "ArmazemParaiba", "Belmicro", "Bemol",
    "Carajas", "Carrefour", "CasasDAgua", "CenterKennedy", "CentralAr",
    "Climario", "Dufrio", "Edimil", "Eletrozema", "EngageEletro",
    "FerreiraCosta", "Frigelar", "FrioPecas", "Fujioka", "GBarbosa", "Gazin",
    "GoCompras", "GrupoMateus", "ImperioDigital", "Leveros", "Martinello",
    "NorteRefrigeracao", "NossoLar", "PoloAr", "QueroQuero", "TVLar",
    "TopMoveis", "UnicaAR", "WebContinental", "Zenir",
})


def superficie_de(plataforma: str) -> str:
    """Classifica a plataforma na superfície onde ela é observada.

    Args:
        plataforma: valor de ``coletas.plataforma`` (ex.: "Magalu", "Dufrio").

    Returns:
        ``marketplace``, ``comparador`` ou ``loja_propria``.

    Note:
        O default é ``loja_propria`` de propósito: plataforma nova que ninguém
        classificou é um site de lojista até prova em contrário, e loja própria
        fica FORA do win rate. Errar para o lado que não infla o número.
    """
    if plataforma in MARKETPLACES:
        return SUPERFICIE_MARKETPLACE
    if plataforma in COMPARADORES:
        return SUPERFICIE_COMPARADOR
    return SUPERFICIE_LOJA_PROPRIA


def disputa_buybox(plataforma: str) -> bool:
    """A plataforma tem buy box disputada por mais de um lojista?

    Só ``marketplace``. É este predicado que decide o denominador do win rate.
    """
    return superficie_de(plataforma) == SUPERFICIE_MARKETPLACE


def mapa_superficies() -> Dict[str, str]:
    """Mapa {plataforma: superfície} para semear a tabela de referência do banco.

    Inclui as plataformas conhecidas em código — marketplaces, comparadores e
    todo dealer de ``DEALER_CONFIGS``. Plataforma que apareça na coleta sem
    estar aqui cai no default de ``superficie_de``.
    """
    mapa: Dict[str, str] = {p: SUPERFICIE_MARKETPLACE for p in MARKETPLACES}
    mapa.update({p: SUPERFICIE_COMPARADOR for p in COMPARADORES})
    mapa.update({p: SUPERFICIE_LOJA_PROPRIA for p in LOJAS_PROPRIAS})
    return mapa


def _dealers_do_coletor() -> FrozenSet[str] | None:
    """Chaves de ``DEALER_CONFIGS``, ou ``None`` se a stack de coleta não existe.

    Usado SÓ para validar (``validar_registro``), nunca para classificar: a
    classificação tem que funcionar igual no app magro, onde este import falha.
    ``None`` distingue "não deu para conferir" de "conferi e está vazio" — sem
    isso, um ambiente sem Playwright passaria na validação por engano.
    """
    try:
        from scrapers.dealers import DEALER_CONFIGS
    except Exception:  # pragma: no cover - app magro, sem os extras de coleta
        return None
    return frozenset(DEALER_CONFIGS.keys())


def validar_registro() -> None:
    """Falha alto se a classificação for ambígua. Roda no CI.

    Raises:
        ValueError: se uma plataforma estiver em duas superfícies ao mesmo
            tempo — o caso perigoso é um dealer de ``DEALER_CONFIGS`` listado
            também como marketplace, que devolveria a loja própria ao
            denominador de buy box pela porta dos fundos.
    """
    sobreposicao = sorted((MARKETPLACES | COMPARADORES) & LOJAS_PROPRIAS)
    if sobreposicao:
        raise ValueError(
            "Plataforma em duas superfícies ao mesmo tempo: "
            f"{sobreposicao}. Um site de DEALER_CONFIGS é loja própria — "
            "listá-lo como marketplace/comparador o devolve ao denominador de "
            "buy box e infla o win rate."
        )

    colisao = sorted(MARKETPLACES & COMPARADORES)
    if colisao:
        raise ValueError(
            f"Plataforma é marketplace e comparador ao mesmo tempo: {colisao}."
        )

    if not MARKETPLACES:
        raise ValueError(
            "Nenhum marketplace classificado — o win rate de buy box ficaria "
            "sem denominador e o produto do seller não teria o que medir."
        )

    # A lista fixa não pode divergir da fonte de verdade do coletor. Só dá para
    # conferir onde a stack de coleta existe (CI e PC coletor); no app magro o
    # import falha e a conferência é pulada, de propósito.
    dealers = _dealers_do_coletor()
    if dealers is None:
        return
    faltando = sorted(dealers - LOJAS_PROPRIAS)
    se_foi = sorted(LOJAS_PROPRIAS - dealers)
    if faltando or se_foi:
        raise ValueError(
            "LOJAS_PROPRIAS divergiu de scrapers.dealers.DEALER_CONFIGS. "
            f"Em DEALER_CONFIGS e não aqui: {faltando or '-'}; "
            f"aqui e não em DEALER_CONFIGS: {se_foi or '-'}. "
            "Dealer fora desta lista é classificado como loja própria pelo "
            "default, o que é seguro; mas dealer removido do coletor e mantido "
            "aqui esconde uma plataforma que deixou de existir."
        )
