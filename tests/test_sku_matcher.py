"""
tests/test_sku_matcher.py — Resolução de SKU por atributos (de-para v2).

Catálogo-fixture espelha a estrutura real (TCL/GREE/MIDEA/AGRATTO) o suficiente
para exercitar as regras anti-fusão e o "não chutar". Os 5 SKUs de fusão do
relatório de validação são cobertos pela INVARIANTE: títulos de modelos
distintos nunca compartilham o mesmo sku_v2.

Rodar:  pytest -q
"""
import pytest

from utils.sku_matcher import build_catalog, resolve_sku

# Catálogo-fixture (subset realista de produtos_catalogo).
_ROWS = [
    # TCL 9000 FRIO — Elite tem 3 SKUs (ambíguo); T-Pro tem 1 (resolve)
    dict(sku="TAC-09CTG2-INV", marca="TCL", capacidade_btu=9000, ciclo="FRIO",
         familia_linha="TCL-T-PRO-2-9000-F", voltagem="220V", ativo=True),
    dict(sku="TAC-09CSGV-INV", marca="TCL", capacidade_btu=9000, ciclo="FRIO",
         familia_linha="TCL-ELITE-9000-F", voltagem="220V", ativo=True),
    dict(sku="TAC-09CSA1", marca="TCL", capacidade_btu=9000, ciclo="FRIO",
         familia_linha="TCL-ELITE-9000-F", voltagem="220V", ativo=True),
    dict(sku="TAC-09CSA2", marca="TCL", capacidade_btu=9000, ciclo="FRIO",
         familia_linha="TCL-ELITE-9000-F", voltagem="110V", ativo=True),
    # TCL 12000 FRIO — FreshIN e T-Pro resolvem (1 SKU cada)
    dict(sku="TAC-12CFG3W-INV", marca="TCL", capacidade_btu=12000, ciclo="FRIO",
         familia_linha="TCL-FRESHIN-12000-F", voltagem="220V", ativo=True),
    dict(sku="TAC-12CTG2-INV", marca="TCL", capacidade_btu=12000, ciclo="FRIO",
         familia_linha="TCL-T-PRO-2-12000-F", voltagem="220V", ativo=True),
    # GREE 9000 FRIO — G-Top tem 2 SKUs (ambíguo)
    dict(sku="GWC09AGA", marca="GREE", capacidade_btu=9000, ciclo="FRIO",
         familia_linha="GREE-G-TOP-9000-F", voltagem="220V", ativo=True),
    dict(sku="GWC09ATB", marca="GREE", capacidade_btu=9000, ciclo="FRIO",
         familia_linha="GREE-G-TOP-9000-F", voltagem="220V", ativo=True),
    # MIDEA 12000 FRIO — Ecomaster com 1 SKU (resolve)
    dict(sku="42EZVCA12M5", marca="MIDEA", capacidade_btu=12000, ciclo="FRIO",
         familia_linha="MIDEA-ECOMASTER-12000-F", voltagem="220V", ativo=True),
    # AGRATTO 12000 FRIO — 2 SKUs separáveis por VOLTAGEM
    dict(sku="AGRATTO-FIT-12-220", marca="AGRATTO", capacidade_btu=12000,
         ciclo="FRIO", familia_linha="AGRATTO-FIT-12000-F", voltagem="220V", ativo=True),
    dict(sku="AGRATTO-FIT-12-110", marca="AGRATTO", capacidade_btu=12000,
         ciclo="FRIO", familia_linha="AGRATTO-FIT-12000-F", voltagem="110V", ativo=True),
]
CAT = build_catalog(_ROWS)


def r(titulo, marca=None):
    return resolve_sku(titulo, marca, CAT)


# ── ALTA: linha única crava SKU ────────────────────────────────────────
def test_linha_unica_crava_sku():
    res = r("Ar Condicionado TCL T-Pro 2.0 9.000 BTUs Inverter Frio")
    assert res.sku_v2 == "TAC-09CTG2-INV" and res.confianca == "alta"

def test_freshin_resolve():
    res = r("Ar Condicionado TCL FreshIN 3.0 12.000 BTUs Inverter Frio")
    assert res.sku_v2 == "TAC-12CFG3W-INV" and res.confianca == "alta"

def test_ecomaster_resolve():
    res = r("Ar Condicionado Midea AI Ecomaster 12.000 BTUs Inverter Frio")
    assert res.sku_v2 == "42EZVCA12M5" and res.confianca == "alta"


# ── AMBÍGUA: família com >1 SKU não crava (não chuta) ──────────────────
def test_elite_ambiguo_nao_crava():
    res = r("Ar Condicionado TCL Elite 9.000 BTUs Inverter Frio")
    assert res.sku_v2 is None and res.confianca == "ambigua"
    assert res.familia_v2 == "TCL-ELITE-9000-F"
    assert len(res.candidatos) == 3 and res.is_pendencia

def test_gtop_ambiguo_nao_crava():
    res = r("Ar Condicionado Gree G-Top Auto 9.000 BTUs Inverter Frio")
    assert res.sku_v2 is None and res.confianca == "ambigua"
    assert set(res.candidatos) == {"GWC09AGA", "GWC09ATB"}


# ── Desempate por VOLTAGEM ─────────────────────────────────────────────
def test_voltagem_desempata():
    res = r("Ar Condicionado Agratto Fit 12.000 BTUs Inverter Frio 220V")
    assert res.sku_v2 == "AGRATTO-FIT-12-220" and res.confianca == "alta"
    assert res.metodo == "familia_mais_voltagem"

def test_sem_voltagem_fica_ambiguo():
    res = r("Ar Condicionado Agratto Fit 12.000 BTUs Inverter Frio")
    assert res.sku_v2 is None and len(res.candidatos) == 2


# ── ANTI-FUSÃO (defeito B): On/Off nunca cai em SKU inverter ───────────
def test_onoff_nao_cai_em_inverter():
    res = r("Ar Condicionado TCL 12.000 BTUs On/Off Frio")
    assert res.sku_v2 is None   # NUNCA TAC-12CFG3W-INV

def test_serie_a1_onoff_nao_funde():
    res = r("Ar Condicionado TCL Serie A1 9.000 BTUs On/Off Frio")
    assert res.sku_v2 is None   # NUNCA TAC-09CSA1

def test_generico_sem_linha_nao_crava():
    res = r("Ar Condicionado TCL 12.000 BTUs Inverter Frio")
    assert res.sku_v2 is None and res.familia_v2 == "TCL-12000-F"


def test_fusao_5_skus_separa_modelos_distintos():
    """Os títulos que hoje fundem TAC-12CFG3W-INV NÃO podem mais dividir o
    mesmo SKU entre modelos distintos."""
    titulos = [
        "Ar Condicionado TCL Serie A1 12.000 BTUs On/Off Frio",
        "Ar Condicionado TCL FreshIN 3.0 12.000 BTUs Inverter Frio",
        "Ar Condicionado TCL T-Pro 2.0 12.000 BTUs Inverter Frio",
        "Ar Condicionado TCL 12.000 BTUs Inverter Frio",
        "Ar Condicionado TCL 12.000 BTUs On/Off Frio",
    ]
    skus = [r(t).sku_v2 for t in titulos]
    # FreshIN e T-Pro resolvem para SKUs DIFERENTES; o resto fica None.
    assert skus[1] == "TAC-12CFG3W-INV"
    assert skus[2] == "TAC-12CTG2-INV"
    assert skus[1] != skus[2]
    assert skus[0] is None and skus[3] is None and skus[4] is None
    # nenhum SKU não-nulo se repete entre modelos distintos
    nn = [s for s in skus if s]
    assert len(nn) == len(set(nn))


# ── Guardas e namespace ────────────────────────────────────────────────
def test_guarda_fora_tipo():
    assert r("Ar Condicionado de Janela Midea 12.000 BTUs Frio").estado == "FORA_ESCOPO"
    assert r("Ar Condicionado Portatil Midea 12000 BTUs Frio").estado == "FORA_ESCOPO"

def test_guarda_nao_ac():
    assert r("Filtro de ar condicionado automotivo Fiat Palio").estado == "NAO_AC"

def test_sku_resolvido_sempre_no_namespace():
    titulos = [
        "Ar Condicionado TCL T-Pro 2.0 9.000 BTUs Inverter Frio",
        "Ar Condicionado TCL FreshIN 3.0 12.000 BTUs Inverter Frio",
        "Ar Condicionado Midea AI Ecomaster 12.000 BTUs Inverter Frio",
        "Ar Condicionado Agratto Fit 12.000 BTUs Inverter Frio 220V",
    ]
    for t in titulos:
        res = r(t)
        if res.sku_v2:
            assert res.sku_v2 in CAT.skus
