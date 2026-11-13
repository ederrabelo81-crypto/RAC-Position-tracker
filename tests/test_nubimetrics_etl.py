"""Módulo nubimetrics/: filtros, transformação, carga e ETL (sem rede).

Usa as amostras REAIS de nubimetrics/catalog/samples/ (conta Midea Brasil,
MLB1646, 13/11/2026) via o cliente falso de tests/nubimetrics_fake.py.
"""
import asyncio
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from nubimetrics_fake import FakeNubi, amostra  # noqa: E402

from nubimetrics import analytics as an  # noqa: E402
from nubimetrics import load, transform  # noqa: E402
from nubimetrics.filters import (  # noqa: E402
    SEG_HW_INVERTER, SEG_HW_ONOFF, SEG_JANELA, SEG_PISO_TETO, SEG_PORTATIL,
    btu_do_modelo, classificar, faixa_btu, is_midea, marca_exibicao, normalizar_marca,
    parse_btu, parse_voltagem,
)

HOJE = dt.date(2026, 11, 13)


# ── filtros ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texto,btu", [
    ("Ar Split 9.000 Btus", 9000), ("Inverter 12000btu", 12000), ("12k Midea", 12000),
    ("18 000 BTUs", 18000), ("Ar 220V sem btu", None), ("Agratto Zen 9.000 Btus Frio Inv", 9000),
])
def test_parse_btu(texto, btu):
    assert parse_btu(texto) == btu


def test_btu_pelo_modelo_e_faixas():
    assert btu_do_modelo("38EZVCC12M5") == 12000
    assert btu_do_modelo("96662756") is None          # só dígitos: não é código de modelo
    assert [faixa_btu(b) for b in (7500, 9000, 12000, 18000, 24000, 36000)] == \
        ["≤7k", "9k", "12k", "18k", "22–24k", "30k+"]


def test_marca_normalizada():
    assert normalizar_marca("Britânia") == normalizar_marca("BRITANIA") == "BRITANIA"
    assert marca_exibicao("MIDEA") == "Midea" and is_midea("Midea") and not is_midea("LG")
    assert parse_voltagem("Frio 220v") == "220V" and parse_voltagem("127V") == "127V"


@pytest.mark.parametrize("titulo,seg,fonte", [
    ("Ar Condicionado Split Hi Wall Inverter Midea 12.000 Btus Frio 220v", SEG_HW_INVERTER, "title_regex"),
    ("Ar Condicionado Split Hi Wall On/Off 9000 Btus", SEG_HW_ONOFF, "title_regex"),
    ("Ar-Condicionado Portátil Electrolux 12.000 BTUS", SEG_PORTATIL, "title_regex"),
    ("Ar Condicionado Janela 7500 Btus Mecânico", SEG_JANELA, "title_regex"),
    ("Ar Condicionado Split Piso Teto 36000 Btus", SEG_PISO_TETO, "title_regex"),
    ("Ar Condicionado Split Philco Espelhado 1", SEG_HW_INVERTER, "title_default"),
])
def test_classificar(titulo, seg, fonte):
    c = classificar(titulo)
    assert (c.segmento, c.match_source) == (seg, fonte)


def test_classificar_btu_pelo_modelo_quando_titulo_truncado():
    c = classificar("Ar Condicionado Hw Inverter Midea Ai Eco", "38EZVCC12M5")
    assert (c.btu, c.match_source) == (12000, "model_regex")


# ── transformação (amostras reais) ──────────────────────────────────────────

def test_mercado_marca_mes_corrente_como_parcial():
    linhas = transform.mercado_mensal({"tamanho": amostra("nubi_category_market_size"),
                                       "estrutura": amostra("nubi_category_market_structure")}, HOJE)
    assert len(linhas) == 13
    nov = linhas[-1]
    assert nov["mes"] == "2026-11-01" and nov["parcial"] is True
    assert round(nov["receita_brl"] / 1e6, 2) == 173.34     # bate com a UI ("R$ 173,3M")


def test_ranking_fallback_nao_grava_mes_errado():
    bruto = amostra("meli_market_rankings__brands")          # pedido nov → veio out (fallback)
    assert bruto["period_fallback"] is True
    assert transform.ranking(bruto, dt.date(2026, 11, 1), "brands") == []


def test_ranking_posicao_unica_mesmo_com_empate():
    bruto = {**amostra("meli_market_rankings__catalog"), "period_fallback": False}
    linhas = transform.ranking(bruto, dt.date(2026, 10, 1), "catalog")
    assert [r["posicao"] for r in linhas] == list(range(1, len(linhas) + 1))


def test_soma_das_marcas_bate_com_a_categoria():
    marcas = transform.ranking({**amostra("meli_market_rankings__brands"), "period_fallback": False},
                               dt.date(2026, 10, 1), "brands")
    mercado = transform.mercado_mensal({"tamanho": amostra("nubi_category_market_size"), "estrutura": {}}, HOJE)
    assert transform.checar_consistencia(marcas, mercado) is None
    assert marcas[1]["nome"] == "MIDEA" and round(marcas[1]["share_brl"], 1) == 13.3


def test_concorrente_converte_strings_arredondadas():
    linhas = transform.concorrente([{"concorrente": "x", "serie": amostra("nubi_competition_seller_evolution")}])
    assert linhas and {"visitas", "unidades", "receita_brl", "conversao_pct"} <= set(linhas[0])
    assert transform._num("1.3K") == 1300 and transform._num("3M") == 3_000_000


def test_detalhe_categoria_so_no_mes_pedido():
    linhas = [{"mes": "2026-10-01", "tipo": "real"}]
    det = {**amostra("meli_category_detail"), "period_applied": "2026-10-01"}
    transform.detalhe_categoria(det, linhas, dt.date(2026, 10, 1))
    assert linhas[0]["saturacao"] == "Média"
    outra = [{"mes": "2026-10-01", "tipo": "real"}]
    transform.detalhe_categoria(amostra("meli_category_detail"), outra, dt.date(2026, 10, 1))  # veio nov
    assert "saturacao" not in outra[0]


# ── carga ───────────────────────────────────────────────────────────────────

class _Tabela:
    def __init__(self, log):
        self.log = log

    def upsert(self, lote, on_conflict):
        self.log.append((lote, on_conflict))
        return self

    def execute(self):
        return type("R", (), {"data": []})()


class _Cliente:
    def __init__(self):
        self.log = []
        self.rpcs = []

    def table(self, nome):
        return _Tabela(self.log)

    def rpc(self, nome, params):
        self.rpcs.append((nome, params))
        return type("Q", (), {"execute": lambda s: type("R", (), {"data": len(params["p_linhas"])})()})()


def test_upsert_iguala_as_chaves_do_lote():
    cli = _Cliente()
    load.upsert(cli, "nubi_ranking_mensal", [{"a": 1, "b": 2}, {"a": 3}])
    lote, chave = cli.log[0]
    assert all(set(x) == {"a", "b", "atualizado_em"} for x in lote)
    assert chave == "categoria_id,mes,dimensao,posicao"


def test_foto_vazia_mantem_a_anterior():
    cli = _Cliente()
    assert load.substituir_anuncios(cli, []) == 0 and cli.rpcs == []


# ── ETL de ponta a ponta (MCP falso) ────────────────────────────────────────

@pytest.fixture
def dados(monkeypatch):
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: FakeNubi())
    return run.transformar(asyncio.run(run.extrair(2, True, HOJE)), HOJE)


def test_etl_monta_todas_as_tabelas(dados):
    assert {x["tipo"] for x in dados["mercado"]} == {"real", "previsao"}
    dims = {r["dimensao"] for r in dados["rankings"]}
    assert dims == {"brands", "seller", "catalog", "items", "official_store", "search_terms"}
    assert len(dados["anuncios"]) == 10 and dados["concorrente"]
    assert all(a["atualizado_em"] for a in dados["anuncios"])        # NOT NULL na RPC


def test_etl_dry_run_nao_grava(monkeypatch):
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: FakeNubi())
    monkeypatch.setattr(run, "gravar", lambda *a, **k: pytest.fail("dry-run gravou"))
    assert run.main(["--dry-run"]) == run.EXIT_OK


# ── análises do painel ──────────────────────────────────────────────────────

def _frames(dados):
    rk = pd.DataFrame(dados["rankings"])
    a = an.preparar_anuncios(pd.DataFrame(dados["anuncios"]),
                             pd.DataFrame([{"nickname": "DUFRIO REFRIGERAÇÃO", "display_name": "Dufrio",
                                            "is_midea_partner": True},
                                           {"nickname": "BELMICROSHOP", "display_name": "Bel Micro",
                                            "is_midea_partner": True}]))
    return rk, a


def test_kpis_midea_outubro(dados):
    rk, _ = _frames(dados)
    k = an.kpis_marca(rk, pd.Timestamp("2026-10-01"))
    assert round(k["mercado_receita"] / 1e6, 1) == 223.5
    assert k["posicao"] == 2 and round(k["share"], 1) == 13.3 and round(k["gap_rival_pp"], 1) == -6.7


def test_penetracao_e_oportunidades(dados):
    _, a = _frames(dados)
    rec = an.filtrar(a)
    pen = an.penetracao(rec).set_index("seller_exib")
    assert pen.loc["Bel Micro", "share_midea"] == 0 and pen.loc["Dufrio", "indice_penetracao"] > 1
    opps = an.oportunidades(rec)
    assert not opps.empty and list(opps["prioridade"]) == list(range(1, len(opps) + 1))
    assert opps["impacto_brl"].is_monotonic_decreasing
    assert set(opps["seller"]) <= {"Dufrio", "Bel Micro", "Mercado (todos)"}   # só parceiros + mercado


def test_formatacao_pt_br():
    assert an.fmt_brl(33_970_000) == "R$ 34,0 mi" and an.fmt_brl(345_000) == "R$ 345 mil"
    assert an.fmt_int(15567, " un") == "15.567 un" and an.fmt_pct(19.6) == "19,6%"
    assert an.fmt_pp(1.234) == "+1,2 p.p."


def test_resumo_em_ingles(dados):
    rk, a = _frames(dados)
    k = an.kpis_marca(rk, pd.Timestamp("2026-10-01"))
    en = an.resumo_en(k, pd.Timestamp("2026-10-01"), an.oportunidades(an.filtrar(a)))
    assert en.iloc[0]["Value"] == "Oct 2026" and "Midea revenue share" in set(en["Metric"])


def test_meta_de_share():
    r = an.meta_share(100.0, 13.0, 15.0)
    assert r["falta"] == pytest.approx(2.0)
