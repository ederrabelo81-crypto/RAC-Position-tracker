"""Aceitação do sell-out diário contra os números de 11/11/2026 (mantenedor, 17/11/2026).

Roda contra os exports REAIS da Nubimetrics em ``uploads/nubimetrics/`` (ou
``NUBI_EXPORTS_DIR``). Eles não vão para o git (dado licenciado da conta Midea):
sem a pasta, o módulo inteiro é pulado — o CI fica verde e quem tem os
arquivos roda ``pytest tests/test_nubimetrics_aceitacao.py -v``.

Tolerâncias: ±1% no Total AC, ±4% no HW Inverter (classificação).

O HW Inverter depende do export de ATRIBUTOS (``--atributos`` no importador):
sem ele só o título decide e ~37% das unidades têm título cortado em 40
caracteres. Nesse caso os testes de HW marcam ``xfail`` mostrando o desvio —
não passam calados nem escondem o número.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd
import pytest

from nubimetrics import diario as D
from nubimetrics import import_export as IE

PASTA = Path(os.environ.get("NUBI_EXPORTS_DIR", Path(__file__).resolve().parent.parent / "uploads" / "nubimetrics"))
if not PASTA.is_dir() or not any(PASTA.glob("*.xlsx")):
    pytest.skip(f"exports da Nubimetrics ausentes em {PASTA}", allow_module_level=True)

DIA = dt.date(2026, 11, 11)
TOTAL_AC = [2490, 4345, 6564, 5264, 7302, 5443, 5774, 6466, 7220, 7656, 13995, 4705, 3611, 3462, 3098]
HW = [2198, 3965, 5709, 4263, 5990, 4546, 4851, 5579, 6175, 6419, 11522, 3962, 2906, 2558, 2617]


@pytest.fixture(scope="module")
def lote():
    l = IE.ler_lote(IE._arquivos([str(PASTA)]))
    assert l.fino, "falta o export 'RAC | Top anuncios por dia'"
    return l


@pytest.fixture(scope="module")
def linhas(lote):
    attrs = [a for a in lote.atributos if not a.empty]
    return D.montar_linhas(IE.folhas_unificadas(lote), pd.concat(attrs) if attrs else None)


@pytest.fixture(scope="module")
def com_atributos(lote) -> bool:
    return any(not a.empty for a in lote.atributos)


def _perto(obtido: float, esperado: float, tol: float) -> bool:
    return abs(obtido / esperado - 1) <= tol


def _hw_ou_xfail(ok: bool, com_atributos: bool, msg: str) -> None:
    if ok:
        return
    if not com_atributos:
        pytest.xfail(f"sem export de atributos (só título): {msg}")
    pytest.fail(msg)


# ── integridade ─────────────────────────────────────────────────────────────

def test_exports_fecham_com_o_total_e_entre_si(lote, linhas):
    assert lote.problemas == []
    assert IE.conferir(lote, IE.folhas_unificadas(lote)) == []


# ── Total AC (±1%) ──────────────────────────────────────────────────────────

def test_total_ac_11_11(linhas):
    x = linhas[linhas["data"] == DIA]
    assert _perto(x["unidades"].sum(), 13995, 0.01)
    assert _perto(x["gmv_brl"].sum(), 32.54e6, 0.01)


@pytest.mark.parametrize("marca,unid", [("MIDEA", 2927), ("LG", 2326), ("PHILCO", 1760), ("GREE", 1009)])
def test_total_ac_marcas_11_11(linhas, marca, unid):
    # Linha MIDEA da Nubimetrics (o painel soma as outras grafias Midea à parte).
    x = linhas[(linhas["data"] == DIA) & (linhas["marca"] == marca)]
    assert _perto(x["unidades"].sum(), unid, 0.01)


@pytest.mark.parametrize("seller,unid", [("DUFRIO REFRIGERAÇÃO", 1793), ("CENTRALAR.COM", 1717),
                                         ("CLIMARIOREFRIGERACAO", 1596), ("ECONTINENTAL ES", 1029),
                                         ("FRIOPEÇAS", 952)])
def test_total_ac_sellers_11_11(linhas, seller, unid):
    x = linhas[(linhas["data"] == DIA) & (linhas["seller"] == seller)]
    assert _perto(x["unidades"].sum(), unid, 0.01)


def test_total_ac_serie_diaria(linhas):
    s = linhas.groupby("data")["unidades"].sum()
    for i, esperado in enumerate(TOTAL_AC):
        assert _perto(s[dt.date(2026, 11, 1 + i)], esperado, 0.01), f"{1 + i:02d}/11"


# ── HW Inverter (±4%) ───────────────────────────────────────────────────────

def _hw(linhas: pd.DataFrame) -> pd.DataFrame:
    return linhas[linhas["segmento"] == D.SEG_HW_INVERTER]


def test_hw_11_11_total(linhas, com_atributos):
    x = _hw(linhas)[lambda d: d["data"] == DIA]
    u, g = x["unidades"].sum(), x["gmv_brl"].sum()
    _hw_ou_xfail(_perto(u, 11522, 0.04) and _perto(g, 25e6, 0.04), com_atributos,
                 f"HW 11/11 = {u:,.0f} unid. / R$ {g / 1e6:.2f} mi (esperado 11.522 / ~R$ 25 mi)")


@pytest.mark.parametrize("marca,unid", [("MIDEA", 2785), ("LG", 2269), ("GREE", 919)])
def test_hw_marcas_11_11(linhas, com_atributos, marca, unid):
    u = _hw(linhas)[lambda d: (d["data"] == DIA) & (d["marca"] == marca)]["unidades"].sum()
    _hw_ou_xfail(_perto(u, unid, 0.04), com_atributos, f"HW {marca} 11/11 = {u:,.0f} (esperado {unid:,})")


@pytest.mark.parametrize("seller,unid,gmv", [("DUFRIO REFRIGERAÇÃO", 1765, 3.64e6),
                                             ("CLIMARIOREFRIGERACAO", 1583, 3.79e6),
                                             ("CENTRALAR.COM", 1552, None)])
def test_hw_sellers_11_11(linhas, com_atributos, seller, unid, gmv):
    x = _hw(linhas)[lambda d: (d["data"] == DIA) & (d["seller"] == seller)]
    ok = _perto(x["unidades"].sum(), unid, 0.04) and (gmv is None or _perto(x["gmv_brl"].sum(), gmv, 0.04))
    _hw_ou_xfail(ok, com_atributos, f"HW {seller} 11/11 = {x['unidades'].sum():,.0f} unid. / "
                 f"R$ {x['gmv_brl'].sum() / 1e6:.2f} mi (esperado {unid:,})")


def test_hw_quinzena_lg_a_frente(linhas, com_atributos):
    x = _hw(linhas)[lambda d: d["data"] <= dt.date(2026, 11, 15)]
    por = x.groupby("marca")["unidades"].sum()
    ok = (_perto(x["unidades"].sum(), 73_300, 0.04) and _perto(por["LG"], 19_045, 0.04)
          and _perto(por["MIDEA"], 15_821, 0.04) and por["LG"] > por["MIDEA"])
    _hw_ou_xfail(ok, com_atributos, f"HW 01–15/11 = {x['unidades'].sum():,.0f} (LG {por['LG']:,.0f}, "
                 f"MIDEA {por['MIDEA']:,.0f}; esperado ~73,3 mil, 19.045, 15.821)")


def test_hw_serie_diaria(linhas, com_atributos):
    s = _hw(linhas).groupby("data")["unidades"].sum()
    fora = [(f"{1 + i:02d}/11", int(s[dt.date(2026, 11, 1 + i)]), e) for i, e in enumerate(HW)
            if not _perto(s[dt.date(2026, 11, 1 + i)], e, 0.04)]
    _hw_ou_xfail(not fora, com_atributos, f"{len(fora)} dia(s) fora de ±4%: {fora}")
