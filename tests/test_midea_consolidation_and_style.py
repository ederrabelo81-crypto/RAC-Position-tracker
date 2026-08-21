"""
tests/test_midea_consolidation_and_style.py

Trava duas regressões que quebraram a página 📈 Price Evolution:

1. **Consolidação de marca.** Todas as variantes do grupo Midea gravadas nas
   fontes (incl. ``"SPRINGER CARRIER MIDEA"`` do ``pricetrack_daily``) precisam
   colapsar para o rótulo canônico ``"Midea"`` em todas as janelas do painel.
   Antes, ``"SPRINGER CARRIER MIDEA"`` não era alias e aparecia como uma série
   separada no gráfico por marca.

2. **Styler grande derrubava a aba Detail.** ``_style_midea_df`` devolvia um
   Pandas Styler mesmo para frames enormes (o dump cru do Detail passa de 600k
   linhas porque ``pricetrack_daily`` é lido sem ``limit``). O Styler força o
   Streamlit a computar contexto por célula, que o Pandas capa em
   ``styler.render.max_elements`` — acima disso ``st.dataframe`` levantava
   ``StreamlitAPIException``. Agora a função devolve o DataFrame cru quando o
   frame excede o teto.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402 — importável sem renderizar


# ---------------------------------------------------------------------------
# 1. Consolidação de marca
# ---------------------------------------------------------------------------

def test_springer_carrier_midea_canonicaliza_para_midea():
    assert app._canonical_marca("SPRINGER CARRIER MIDEA") == "Midea"


def test_todas_variantes_do_grupo_midea_colapsam():
    for raw in [
        "MIDEA", "Midea", "midea",
        "MIDEA CARRIER", "Midea Carrier",
        "Springer Midea", "SPRINGER MIDEA",
        "Springer", "SPRINGER",
        "SPRINGER CARRIER MIDEA",
    ]:
        assert app._canonical_marca(raw) == "Midea", raw


def test_marcas_alheias_ao_grupo_permanecem_distintas():
    # Carrier/Trane são marcas próprias no de-para — não devem virar Midea.
    assert app._canonical_marca("Carrier") == "Carrier"
    assert app._canonical_marca("TRANE") == "TRANE"
    assert app._canonical_marca("LG") == "LG"


def test_expand_brands_midea_inclui_variante_springer_carrier():
    expanded = {v.strip().casefold() for v in app._expand_brands(["Midea"])}
    assert "springer carrier midea" in expanded


# ---------------------------------------------------------------------------
# 2. Styler acima do teto de render vira DataFrame cru (sem StreamlitAPIException)
# ---------------------------------------------------------------------------

def test_style_midea_df_frame_pequeno_retorna_styler():
    small = pd.DataFrame({"marca": ["Midea", "LG"], "preco": [1994.91, 3200.0]})
    styled = app._style_midea_df(small)
    assert isinstance(styled, pd.io.formats.style.Styler)


def test_style_midea_df_frame_gigante_retorna_dataframe_cru():
    max_elements = int(pd.get_option("styler.render.max_elements"))
    # linhas × colunas > teto ⇒ um Styler não renderizaria; deve cair no df cru.
    n = (max_elements // 2) + 10
    big = pd.DataFrame({"marca": ["Midea"] * n, "preco": [1994.91] * n})
    assert big.size > max_elements
    styled = app._style_midea_df(big)
    assert isinstance(styled, pd.DataFrame)
    assert not isinstance(styled, pd.io.formats.style.Styler)
