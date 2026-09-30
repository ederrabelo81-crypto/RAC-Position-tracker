"""
utils/keyword_taxonomy.py — Que tipo de busca é cada keyword da coleta.

Por que existe
--------------
O filtro "Keywords" do dashboard misturava três coisas diferentes na mesma
lista alfabética (conferido em 30/09/2026, 57 valores):

* buscas **genéricas** ("ar condicionado 12000 btus") — onde o consumidor ainda
  não escolheu marca e a prateleira é disputada de verdade;
* buscas **de marca** ("ar condicionado midea", "samsung windfree") — SERPs
  dominadas pela própria marca buscada;
* **vitrines de dealer** ("WebContinental", "PoloAr", "Leveros") — não são
  buscas: o coletor de dealers navega a vitrine de ar-condicionado do site e
  grava o nome da loja em `keyword` (`categoria = 'Dealers'`).

Somar as três num mesmo share infla a marca que tem mais keywords dirigidas a
ela na lista — viés de coleta, não de mercado. É a regra que o próprio
`config.BRAND_NEUTRAL_CATEGORIES` já registra; este módulo a torna utilizável
no painel.

A leitura de trade que sai daqui é a tríade clássica de busca:

* ``Genérica``          → **batalha**: quem ocupa a prateleira neutra;
* ``Marca própria``     → **defesa**: a Midea segura as buscas pelo próprio nome?
* ``Marca concorrente`` → **conquista**: a Midea aparece quando buscam o rival?

Uso:
    from utils.keyword_taxonomy import classify_keyword, TIPO_GENERICA
    classify_keyword("ar condicionado 12000 btus")          # -> "Genérica"
    classify_keyword("midea ecomaster")                    # -> "Marca própria"
    classify_keyword("WebContinental", categoria="Dealers")  # -> "Vitrine de dealer"
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence

__all__ = [
    "TIPO_GENERICA",
    "TIPO_MARCA_PROPRIA",
    "TIPO_MARCA_CONCORRENTE",
    "TIPO_VITRINE_DEALER",
    "TIPOS_BUSCA",
    "TIPOS_BUSCA_REAIS",
    "ICONE_TIPO",
    "GROUP_TOKENS",
    "classify_keyword",
    "keyword_intent",
    "sort_keywords",
    "keyword_options",
    "format_keyword_option",
    "expand_keyword_selection",
    "keywords_by_tipo",
]

TIPO_GENERICA = "Genérica"
TIPO_MARCA_PROPRIA = "Marca própria"
TIPO_MARCA_CONCORRENTE = "Marca concorrente"
TIPO_VITRINE_DEALER = "Vitrine de dealer"

#: Ordem de exibição — a genérica primeiro porque é onde a disputa acontece.
TIPOS_BUSCA: tuple = (
    TIPO_GENERICA,
    TIPO_MARCA_PROPRIA,
    TIPO_MARCA_CONCORRENTE,
    TIPO_VITRINE_DEALER,
)

#: Tipos que são BUSCA de fato. A vitrine de dealer fica de fora: ela é o
#: filtro de plataforma/canal disfarçado de keyword.
TIPOS_BUSCA_REAIS: tuple = TIPOS_BUSCA[:3]

ICONE_TIPO: Dict[str, str] = {
    TIPO_GENERICA: "🔎",
    TIPO_MARCA_PROPRIA: "🟦",
    TIPO_MARCA_CONCORRENTE: "🥊",
    TIPO_VITRINE_DEALER: "🏪",
}

#: Pseudo-opções do multiselect de keyword: escolher uma delas equivale a
#: escolher todas as keywords do tipo — a "consolidação direta" das genéricas
#: sem marcar 15 itens um a um. Rótulo fixo (sem contagem) de propósito: o
#: Streamlit guarda a seleção pelo texto, e um rótulo que muda com o período
#: invalidaria a escolha salva no session_state.
GROUP_TOKENS: Dict[str, str] = {
    "▸ Todas as genéricas": TIPO_GENERICA,
    "▸ Todas de marca própria": TIPO_MARCA_PROPRIA,
    "▸ Todas de marca concorrente": TIPO_MARCA_CONCORRENTE,
}

# Marcas do grupo Midea Carrier — mesmo corte de grupo do GfK que
# `bestsellers.config.GRUPO_MIDEA` usa. Linhas comerciais entram porque
# "midea ecomaster" e "airvolution" são buscas pela marca mesmo sem citá-la.
_TOKENS_GRUPO_MIDEA = (
    "midea", "springer", "carrier", "comfee",
    "ecomaster", "airvolution", "xtreme save",
)

# Categorias de `config.KEYWORDS_LIST`. Escritas aqui (e conferidas contra o
# config em `tests/test_keyword_taxonomy.py`) para o módulo funcionar no app
# magro sem importar a stack de coleta. "Modelo Midea" é o rótulo legado.
_CATEGORIAS_DIRIGIDAS = frozenset({"Marca", "Modelo / Linha", "Modelo Midea"})
_CATEGORIA_DEALER = "Dealers"

# Ordem de intenção dentro de cada tipo (do mais amplo ao mais específico).
_ORDEM_INTENCAO = (
    "Genérica", "Capacidade BTU", "Capacidade + Tipo", "Segmento",
    "Intenção Compra", "Preço / Promoção", "Conversacional IA",
    "Marca", "Modelo / Linha", "Modelo Midea", "Dealers",
)


def _norm(texto: Optional[str]) -> str:
    """Minúsculo, sem acento, espaços colapsados."""
    if not texto:
        return ""
    decomposto = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return " ".join(sem_acento.lower().split())


def _catalogo_config() -> Dict[str, str]:
    """{keyword normalizada: categoria} a partir de `config.KEYWORDS_LIST`.

    Falha de import (app magro sem o config) devolve ``{}`` — a classificação
    cai nas heurísticas por texto, que cobrem o mesmo conjunto.
    """
    try:
        from config import KEYWORDS_LIST
    except Exception:  # pragma: no cover - depende do ambiente
        return {}
    return {_norm(k.term): k.category for k in KEYWORDS_LIST}


def _lojas_proprias() -> frozenset:
    try:
        from utils.seller_surface import LOJAS_PROPRIAS
    except Exception:  # pragma: no cover - depende do ambiente
        return frozenset()
    return frozenset(_norm(p) for p in LOJAS_PROPRIAS)


def _marcas_concorrentes() -> List[str]:
    """Marcas do `config.BRANDS` que NÃO são do grupo Midea, normalizadas."""
    try:
        from config import BRANDS
    except Exception:  # pragma: no cover - depende do ambiente
        BRANDS = [
            "LG", "Samsung", "Gree", "Consul", "Electrolux", "Elgin", "Philco",
            "TCL", "Daikin", "Hisense", "Agratto", "Fujitsu", "Hitachi",
        ]
    fora = []
    for marca in BRANDS:
        n = _norm(marca)
        if n and not any(tok in n for tok in _TOKENS_GRUPO_MIDEA):
            fora.append(n)
    return fora


_CATALOGO: Dict[str, str] = _catalogo_config()
_DEALERS: frozenset = _lojas_proprias()
_RE_CONCORRENTE = re.compile(
    r"\b(?:" + "|".join(re.escape(m) for m in sorted(_marcas_concorrentes(), key=len, reverse=True)) + r")\b"
)
_RE_GRUPO_MIDEA = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _TOKENS_GRUPO_MIDEA) + r")\b"
)


def keyword_intent(keyword: Optional[str], categoria: Optional[str] = None) -> str:
    """Intenção da busca (a categoria da coleta), com fallback pelo config.

    Args:
        keyword: termo como gravado em `coletas.keyword`.
        categoria: `coletas.categoria`, quando disponível.

    Returns:
        A categoria conhecida, ou ``"Outros"``.
    """
    if categoria and str(categoria).strip():
        return str(categoria).strip()
    return _CATALOGO.get(_norm(keyword), "Outros")


def classify_keyword(keyword: Optional[str], categoria: Optional[str] = None) -> str:
    """Classifica a keyword num dos quatro tipos de busca.

    Precedência: vitrine de dealer (categoria ``Dealers`` ou nome de loja
    própria) → categoria dirigida a marca (própria vs. concorrente pelo texto)
    → marca citada no texto de uma keyword sem categoria → genérica.

    Args:
        keyword: termo como gravado em `coletas.keyword`.
        categoria: `coletas.categoria`, quando disponível (ganha do config).

    Returns:
        Um valor de `TIPOS_BUSCA`.

    Example:
        >>> classify_keyword("ar condicionado lg")
        'Marca concorrente'
        >>> classify_keyword("PoloAr")
        'Vitrine de dealer'
    """
    kw = _norm(keyword)
    cat = keyword_intent(keyword, categoria)

    if cat == _CATEGORIA_DEALER or (kw and kw in _DEALERS):
        return TIPO_VITRINE_DEALER

    cita_midea = bool(_RE_GRUPO_MIDEA.search(kw))
    cita_rival = bool(_RE_CONCORRENTE.search(kw))

    if cat in _CATEGORIAS_DIRIGIDAS:
        return TIPO_MARCA_PROPRIA if cita_midea else TIPO_MARCA_CONCORRENTE

    if cat == "Outros":
        # Keyword fora do config (nova, ou histórico antigo): decide pelo texto.
        if cita_midea:
            return TIPO_MARCA_PROPRIA
        if cita_rival:
            return TIPO_MARCA_CONCORRENTE

    return TIPO_GENERICA


def sort_keywords(
    keywords: Iterable[str],
    categorias: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Ordena por tipo de busca → intenção → texto (sem caixa).

    Args:
        keywords: termos a ordenar (duplicados e vazios são descartados).
        categorias: {keyword: categoria} observado, quando disponível.

    Returns:
        Lista ordenada. Antes, a ordem alfabética com caixa jogava "ArCerto"
        e "WebContinental" no meio das buscas.
    """
    categorias = categorias or {}
    unicos = {str(k) for k in keywords if k is not None and str(k).strip()}

    def _chave(kw: str):
        cat = categorias.get(kw)
        tipo = classify_keyword(kw, cat)
        intencao = keyword_intent(kw, cat)
        ordem_int = (
            _ORDEM_INTENCAO.index(intencao) if intencao in _ORDEM_INTENCAO
            else len(_ORDEM_INTENCAO)
        )
        return (TIPOS_BUSCA.index(tipo), ordem_int, kw.casefold())

    return sorted(unicos, key=_chave)


def keyword_options(
    keywords: Iterable[str],
    categorias: Optional[Dict[str, str]] = None,
    incluir_dealers: bool = False,
) -> List[str]:
    """Opções do multiselect de keyword: atalhos de grupo + buscas ordenadas.

    Args:
        keywords: valores distintos de `coletas.keyword`.
        categorias: {keyword: categoria} observado, quando disponível.
        incluir_dealers: mantém as vitrines de dealer na lista. Padrão False:
            elas são filtradas pelo filtro de Plataforma/Canal, não por busca.

    Returns:
        ``GROUP_TOKENS`` dos tipos presentes + keywords ordenadas.
    """
    ordenadas = sort_keywords(keywords, categorias)
    if not incluir_dealers:
        ordenadas = [
            k for k in ordenadas
            if classify_keyword(k, (categorias or {}).get(k)) != TIPO_VITRINE_DEALER
        ]
    presentes = {classify_keyword(k, (categorias or {}).get(k)) for k in ordenadas}
    atalhos = [tok for tok, tipo in GROUP_TOKENS.items() if tipo in presentes]
    return atalhos + ordenadas


def format_keyword_option(opcao: str) -> str:
    """Rótulo exibido: ícone do tipo antes da keyword (atalho passa intacto)."""
    if opcao in GROUP_TOKENS:
        return opcao
    return f"{ICONE_TIPO[classify_keyword(opcao)]} {opcao}"


def keywords_by_tipo(
    keywords: Iterable[str],
    tipos: Sequence[str],
    categorias: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Keywords de `keywords` cujo tipo está em `tipos`, na ordem de exibição."""
    alvo = set(tipos)
    return [
        k for k in sort_keywords(keywords, categorias)
        if classify_keyword(k, (categorias or {}).get(k)) in alvo
    ]


def expand_keyword_selection(
    selecao: Sequence[str],
    keywords: Iterable[str],
    categorias: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Troca os atalhos de grupo pelas keywords que eles representam.

    Args:
        selecao: o que o usuário marcou (atalhos e/ou keywords).
        keywords: universo de keywords disponíveis.
        categorias: {keyword: categoria} observado, quando disponível.

    Returns:
        Lista de keywords concretas, sem duplicata, na ordem de exibição.
        Seleção vazia devolve vazia (= sem filtro).
    """
    if not selecao:
        return []
    universo = list(keywords)
    tipos = [GROUP_TOKENS[s] for s in selecao if s in GROUP_TOKENS]
    concretas = [s for s in selecao if s not in GROUP_TOKENS]
    if tipos:
        concretas.extend(keywords_by_tipo(universo, tipos, categorias))
    return sort_keywords(dict.fromkeys(concretas), categorias)
