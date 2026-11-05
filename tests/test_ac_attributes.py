"""Testes de utils/ac_attributes.py — nomes REAIS do catálogo PriceTrack (03/11/2026)."""
from __future__ import annotations

import pandas as pd
import pytest

from utils import ac_attributes as aa

ECOMASTER = "AR CONDICIONADO SPLIT 18000 BTU FRIO AI ECOMASTER - INVERTER - SPRINGER MIDEA - 220V - 42EZVCA18M5"
ECOMASTER_QF = "AR CONDICIONADO SPLIT 9000 BTU QUENTE/FRIO AI ECOMASTER - INVERTER - MIDEA - 220V - 42EZVQA09M5"
HISENSE = "AR CONDICIONADO SPLIT HI WALL INVERTER 9000 BTUS FRIO - HISENSE - 220V - AS-09UW2RLCMA00E"
SAMSUNG = "AR CONDICIONADO SPLIT HI WALL 24000 BTU QUENTE E FRIO WINDFREE CONNECT AI – INVERTER – SAMSUNG – 220V – F‑AR24DXFAAWK"


@pytest.mark.parametrize("titulo,btu,tec,ciclo,volt", [
    (ECOMASTER, 18000, "INVERTER", "FRIO", "220V"),
    (ECOMASTER_QF, 9000, "INVERTER", "QUENTE/FRIO", "220V"),
    (HISENSE, 9000, "INVERTER", "FRIO", "220V"),
    (SAMSUNG, 24000, "INVERTER", "QUENTE/FRIO", "220V"),
    ("AR CONDICIONADO JANELA 7.500 BTUS FRIO MECANICO ON/OFF 127V", 7500, "ON/OFF", "FRIO", "127V"),
    ("Split 12000 Btu Bivolt", 12000, None, None, "BIVOLT"),
    (None, None, None, None, None),
])
def test_parse_do_nome(titulo, btu, tec, ciclo, volt):
    assert aa.parse_btu(titulo) == btu
    assert aa.parse_tecnologia(titulo) == tec
    assert aa.parse_ciclo(titulo) == ciclo
    assert aa.parse_voltagem(titulo) == volt


def _df():
    return pd.DataFrame([
        {"sku": "42EZVCA18M5", "title": ECOMASTER, "subcategory": "HI-WALL"},
        {"sku": "42EZVCA18M5", "title": ECOMASTER, "subcategory": None},   # histórico antigo
        {"sku": "PISO1", "title": "AR CONDICIONADO PISO TETO 36000 BTU FRIO - INVERTER", "subcategory": "TETO"},
        {"sku": "HW30K", "title": "AR CONDICIONADO SPLIT HI WALL 36000 BTU FRIO - INVERTER", "subcategory": "HI-WALL"},
        {"sku": "ONOFF", "title": "AR CONDICIONADO SPLIT 12000 BTU FRIO ON/OFF", "subcategory": "HI-WALL"},
        {"sku": "SEMMAPA", "title": ECOMASTER, "subcategory": ""},
    ])


def test_subcategory_map_e_fill_classificam_o_historico():
    df = _df()
    mapa = aa.subcategory_map(df)
    assert mapa["42EZVCA18M5"] == "HI-WALL"
    assert "SEMMAPA" not in mapa
    cheio = aa.fill_subcategory(df, mapa)
    assert cheio.loc[1, "subcategory"] == "HI-WALL"
    assert cheio.loc[5, "subcategory"] == ""          # na dúvida, fora


def test_escopo_padrao_hiwall_inverter_ate_30k():
    df = aa.add_attributes(aa.fill_subcategory(_df(), aa.subcategory_map(_df())))
    assert aa.in_default_scope(df).tolist() == [True, True, False, False, False, False]


def test_subcategory_map_mais_frequente_vence():
    df = pd.DataFrame({"sku": ["A", "A", "A"], "subcategory": ["hi-wall", "HI-WALL", "TETO"]})
    assert aa.subcategory_map(df) == {"A": "HI-WALL"}


def test_loja_de_marca_sai_do_mercado():
    lojas = aa.brand_store_marketplaces(
        ["MAGAZINE LUIZA", "MIDEA", "LG", "GREE STORE", "BRASTEMP"],
        ["MIDEA", "SPRINGER MIDEA", "GREE"],
    )
    assert lojas == {"MIDEA", "LG", "BRASTEMP"}


@pytest.mark.parametrize("bruta", [
    "MIDEA", "MIDEA CARRIER", "SPRINGER CARRIER MIDEA", "SPRINGER MIDEA",
    "CARRIER", "SPRINGER", " midea  carrier ",
])
def test_grupo_midea_vira_um_rotulo(bruta):
    assert aa.normalize_brand(bruta) == "MIDEA"


@pytest.mark.parametrize("bruta,esperada", [
    ("LG", "LG"), ("gree", "GREE"), ("ELGIN", "ELGIN"), (None, ""),
])
def test_outras_marcas_ficam_como_estao(bruta, esperada):
    assert aa.normalize_brand(bruta) == esperada
