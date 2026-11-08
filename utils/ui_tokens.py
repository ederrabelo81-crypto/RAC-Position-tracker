"""
utils/ui_tokens.py — tokens visuais únicos dos painéis (app.py e seller_app).

Por que existe (Nov/2026): o `app.py` tinha DUAS paletas de marca que se
contradiziam — `_BRAND_FIXED` (gráficos) pintava a Midea de ``#1a56db`` e a
Philco de vermelho; `_BRAND_COLORS` (chips da Ficha do Produto) pintava a Midea
de ``#4338ca`` e a Philco de laranja. A mesma marca trocava de cor ao mudar de
página. O `seller_app` usava as cores automáticas do Vega-Lite, que mudam com a
ordem das colunas — o Mercado Livre podia ser azul numa aba e laranja na outra.

Regras:
    1. Uma marca / um marketplace tem UMA cor, em todo painel.
    2. O azul ``MIDEA_BLUE`` é reservado ao dado da Midea. Botão, aba e foco
       usam ``ACCENT`` (neutro) — senão a interface compete com a série que o
       gráfico quer destacar.
    3. Fora das marcas fixas, a cor vem de ``BRAND_ROTATION`` pulando as já
       usadas, nunca de uma cor fixa repetida.

Módulo puro (sem Streamlit): testado em ``tests/test_ui_tokens.py``.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

import math

# ── Interface ───────────────────────────────────────────────────────────────
#: Cor de destaque da INTERFACE (botões, abas, foco). Ardósia-azulada neutra:
#: distinta do azul da Midea, legível em contraste AA sobre branco.
ACCENT = "#0f766e"
TEXT = "#1e293b"
TEXT_MUTED = "#64748b"
GRID = "#e2e8f0"
NEUTRAL = "#94a3b8"          # marca "outras" / contexto em gráfico de foco

# Semântica de estado (selo de frescor, deltas, estados vazios).
OK = "#15803d"
WARN = "#b45309"
BAD = "#b91c1c"
INFO = "#1d4ed8"
UNKNOWN = "#64748b"

MIDEA_BLUE = "#1a56db"

# ── Marcas ──────────────────────────────────────────────────────────────────
#: Cor fixa por marca. Mutuamente distintas entre as 8 primeiras (as que
#: aparecem juntas no top 10 de quase toda plataforma).
BRAND_COLORS: Dict[str, str] = {
    "Midea":          MIDEA_BLUE,
    "Midea Carrier":  MIDEA_BLUE,   # rótulo do grupo no Cockpit (mesma empresa)
    "Springer Midea": "#3b82f6",    # mesma família, tom mais claro
    "Carrier":        "#60a5fa",
    "Elgin":          "#f97316",
    "LG":             "#db2777",
    "Gree":           "#059669",
    "Samsung":        "#8b5cf6",
    "TCL":            "#0891b2",
    "Philco":         "#dc2626",
    "Agratto":        "#ca8a04",
    "Electrolux":     "#334155",
    "Consul":         "#0e7490",
    "Komeco":         "#7c3aed",
    "Daikin":         "#0c4a6e",
    "Fujitsu":        "#7f1d1d",
    "Britânia":       "#be123c",
    "Britania":       "#be123c",
}

#: Rotação para marcas sem cor fixa (pula as já usadas no mesmo gráfico).
BRAND_ROTATION: List[str] = [
    "#4d7c0f", "#b45309", "#be185d", "#0369a1", "#6d28d9",
    "#15803d", "#9a3412", "#475569", "#a16207", "#0f766e",
]

# ── Marketplaces / plataformas ──────────────────────────────────────────────
PLATFORM_COLORS: Dict[str, str] = {
    "Mercado Livre":   "#eab308",
    "Amazon":          "#f97316",
    "Magalu":          "#2563eb",
    "Casas Bahia":     "#1e3a8a",
    "Shopee":          "#ea580c",
    "Leroy Merlin":    "#16a34a",
    "Google Shopping": "#64748b",
}


def brand_color(brand: object, default: str = TEXT_MUTED) -> str:
    """Cor fixa da marca ou ``default`` para marca desconhecida/vazia."""
    if brand is None:
        return default
    if isinstance(brand, float) and math.isnan(brand):
        return default
    return BRAND_COLORS.get(str(brand).strip(), default)


def _color_map(values: Iterable[object], fixed: Dict[str, str]) -> Dict[str, str]:
    """Mapa valor→cor: fixas primeiro, o resto pela rotação sem repetir."""
    unique = sorted({str(v) for v in values
                     if v is not None and not (isinstance(v, float) and math.isnan(v))})
    cmap: Dict[str, str] = {v: fixed[v] for v in unique if v in fixed}
    used = set(cmap.values())
    rotation = [c for c in BRAND_ROTATION if c not in used] or BRAND_ROTATION
    i = 0
    for v in unique:
        if v not in cmap:
            cmap[v] = rotation[i % len(rotation)]
            i += 1
    return cmap


def brand_color_map(values: Iterable[object]) -> Dict[str, str]:
    """Mapa discreto marca→cor para gráficos (Plotly ``color_discrete_map``)."""
    return _color_map(values, BRAND_COLORS)


def platform_color_map(values: Iterable[object]) -> Dict[str, str]:
    """Mapa discreto plataforma→cor (dealers caem na rotação)."""
    return _color_map(values, PLATFORM_COLORS)


def focus_color_map(values: Iterable[object], focus: Iterable[str],
                    context: str = NEUTRAL) -> Dict[str, str]:
    """Foco + contexto: as marcas de ``focus`` com a cor própria, o resto cinza.

    Com 8+ linhas coloridas nenhuma se destaca; com a Midea e 2–3 rivais em cor
    e o resto em cinza, o olho vai direto para a comparação que importa.
    """
    foco = {str(f) for f in focus}
    base = brand_color_map(values)
    return {k: (c if k in foco else context) for k, c in base.items()}
