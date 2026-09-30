"""Testes do núcleo analítico do Cockpit do Trade (`utils/shelf_insights.py`).

Os cenários reproduzem padrões REAIS conferidos no banco em 28–29/09/2026:
cada turno do PC coletor gravado duas vezes (run UUID4 + run UUID5 do
reforço `upload_csv.py`), o Mercado Livre com 1.938 linhas na Abertura e
32–166 na Tarde, e a Amazon com o seller real só em `buy_box_seller`.

Rodar:
    pytest tests/test_shelf_insights.py -q
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from utils.shelf_insights import (  # noqa: E402
    ROTULO_GRUPO,
    build_alerts,
    buybox_on_brand,
    comparable_days,
    coverage_by_turno,
    dedup_snapshot,
    extract_btu,
    keyword_battle,
    midea_share_by,
    share_of_shelf,
    share_trend,
    shelf_price_by_btu,
    top_n,
)

D1, D2 = date(2026, 9, 28), date(2026, 9, 29)


def _serp(dia, turno, plataforma, keyword, marcas, run="r1", created="2026-09-29T08:00", **extra):
    """Uma SERP: marcas[i] ocupa a posição orgânica i+1."""
    linhas = []
    for i, marca in enumerate(marcas, start=1):
        linha = {
            "data": dia, "turno": turno, "plataforma": plataforma,
            "keyword": keyword, "marca": marca, "posicao_organica": i,
            "produto": f"{marca} {keyword} #{i}", "run_id": run,
            "created_at": created, "estado_match": "MAPEADO",
        }
        linha.update(extra)
        linhas.append(linha)
    return linhas


class TestDedup:
    def test_run_duplicada_conta_uma_vez(self):
        """O mesmo turno gravado por duas runs (UUID4 e UUID5) — caso real."""
        a = _serp(D2, "Abertura", "Magalu", "ar condicionado", ["Midea", "LG"], run="uuid4",
                  created="2026-09-29T11:47")
        b = _serp(D2, "Abertura", "Magalu", "ar condicionado", ["Midea", "LG"], run="uuid5",
                  created="2026-09-29T11:48")
        out = dedup_snapshot(pd.DataFrame(a + b))
        assert len(out) == 2
        assert set(out["run_id"]) == {"uuid5"}  # a mais recente vence

    def test_run_de_recuperacao_substitui_so_a_propria_plataforma(self):
        ml_manha = _serp(D2, "Abertura", "Mercado Livre", "ar condicionado", ["LG", "Gree"], run="a",
                         created="2026-09-29T09:00")
        ml_retry = _serp(D2, "Abertura", "Mercado Livre", "ar condicionado", ["Midea", "LG"], run="b",
                         created="2026-09-29T15:25")
        magalu = _serp(D2, "Abertura", "Magalu", "ar condicionado", ["TCL"], run="a",
                       created="2026-09-29T09:00")
        out = dedup_snapshot(pd.DataFrame(ml_manha + ml_retry + magalu))
        ml = out[out["plataforma"] == "Mercado Livre"]
        assert set(ml["run_id"]) == {"b"}
        assert set(out[out["plataforma"] == "Magalu"]["run_id"]) == {"a"}

    def test_duas_linhas_na_mesma_posicao_viram_uma(self):
        linhas = _serp(D2, "Abertura", "Amazon", "ar condicionado", ["Midea"])
        linhas.append({**linhas[0], "produto": "outro"})
        assert len(dedup_snapshot(pd.DataFrame(linhas))) == 1

    def test_sem_run_id_ainda_deduplica_posicao(self):
        linhas = _serp(D2, "Abertura", "Amazon", "ar condicionado", ["Midea", "LG"])
        df = pd.DataFrame(linhas + linhas).drop(columns=["run_id", "created_at"])
        assert len(dedup_snapshot(df)) == 2

    def test_created_at_misturando_texto_e_timestamp(self):
        """Supabase devolve texto ISO; o Parquet, timestamp — não pode quebrar."""
        a = _serp(D1, "Abertura", "Magalu", "k", ["LG"], run="frio")
        b = _serp(D2, "Abertura", "Magalu", "k", ["Midea"], run="quente")
        df = pd.DataFrame(a + b)
        df["created_at"] = df["created_at"].astype(object)
        df.loc[df["data"] == D1, "created_at"] = pd.Timestamp("2026-09-28T08:00", tz="UTC")
        out = dedup_snapshot(df)
        assert set(out["run_id"]) == {"frio", "quente"}

    def test_vazio(self):
        assert dedup_snapshot(pd.DataFrame()).empty


class TestShareDePrateleira:
    def test_share_simples(self):
        df = pd.DataFrame(_serp(D2, "Abertura", "Magalu", "ar condicionado",
                                ["Midea", "LG", "Midea", "Gree"]))
        out = share_of_shelf(df, by=("plataforma",), n=10)
        midea = out[out["marca"] == ROTULO_GRUPO].iloc[0]
        assert midea["slots"] == 2 and midea["total"] == 4
        assert midea["share"] == pytest.approx(0.5)

    def test_grupo_soma_carrier(self):
        df = pd.DataFrame(_serp(D2, "Abertura", "Magalu", "k", ["Midea", "Carrier", "LG", "LG"]))
        out = share_of_shelf(df, by=("plataforma",))
        assert out.loc[out["marca"] == ROTULO_GRUPO, "share"].iloc[0] == pytest.approx(0.5)

    def test_prateleira_corta_no_top_n(self):
        df = pd.DataFrame(_serp(D2, "Abertura", "Magalu", "k", ["LG"] * 10 + ["Midea"] * 5))
        out = share_of_shelf(df, n=10)
        assert ROTULO_GRUPO not in set(out["marca"])
        assert len(top_n(df, 10)) == 10

    def test_nao_ac_sai_do_denominador(self):
        linhas = _serp(D2, "Abertura", "Magalu", "k", ["Midea", "Desconhecida", "LG"])
        linhas[1]["estado_match"] = "NAO_AC"
        out = share_of_shelf(pd.DataFrame(linhas))
        assert out.loc[out["marca"] == ROTULO_GRUPO, "share"].iloc[0] == pytest.approx(0.5)

    def test_midea_share_by_lider_e_gap(self):
        df = pd.DataFrame(
            _serp(D2, "Abertura", "Magalu", "k", ["LG", "LG", "LG", "Midea"])
            + _serp(D2, "Abertura", "Amazon", "k", ["Midea", "Gree"])
        )
        out = midea_share_by(df, by=("plataforma",)).set_index("plataforma")
        assert out.at["Magalu", "lider"] == "LG"
        assert out.at["Magalu", "share_lider"] == pytest.approx(0.75)
        assert out.at["Magalu", "gap_lider"] == pytest.approx(0.5)
        assert out.at["Magalu", "melhor_pos_midea"] == 4
        assert out.at["Amazon", "melhor_pos_midea"] == 1

    def test_midea_ausente_tem_share_zero_e_sem_posicao(self):
        df = pd.DataFrame(_serp(D2, "Abertura", "Shopee", "k", ["LG", "TCL"]))
        out = midea_share_by(df, by=("plataforma",))
        assert out["share_midea"].iloc[0] == 0
        assert out["melhor_pos_midea"].iloc[0] is None

    def test_midea_share_sem_dimensao(self):
        df = pd.DataFrame(_serp(D2, "Abertura", "Shopee", "k", ["Midea", "TCL"]))
        out = midea_share_by(df, by=())
        assert len(out) == 1 and out["share_midea"].iloc[0] == pytest.approx(0.5)


class TestSerieDiaria:
    def test_peso_igual_por_plataforma(self):
        """ML com 10 linhas e Magalu com 2 pesam igual no dia."""
        df = pd.DataFrame(
            _serp(D2, "Abertura", "Mercado Livre", "k", ["LG"] * 10)
            + _serp(D2, "Abertura", "Magalu", "k", ["Midea", "Midea"])
        )
        out = share_trend(df)
        midea = out[out["marca"] == ROTULO_GRUPO]["share"].iloc[0]
        assert midea == pytest.approx(0.5)  # (0 + 1) / 2, não 2/12
        assert out["plataformas"].iloc[0] == 2


class TestComparacaoEntreDias:
    def test_so_turnos_em_comum(self):
        df = pd.DataFrame(
            _serp(D1, "Abertura", "Magalu", "k", ["LG"])
            + _serp(D1, "Tarde", "Magalu", "k", ["LG"])
            + _serp(D2, "Abertura", "Magalu", "k", ["Midea"])
        )
        ult, ant, meta = comparable_days(df)
        assert meta["ultimo"] == D2 and meta["anterior"] == D1
        assert set(ant["turno"]) == {"Abertura"}
        assert meta["turnos"] == {"Magalu": ["Abertura"]}

    def test_so_buscas_observadas_nos_dois_dias(self):
        """ML coletou 2 buscas ontem e 1 hoje: a que sumiu não entra em nenhum lado."""
        df = pd.DataFrame(
            _serp(D1, "Abertura", "Mercado Livre", "k1", ["Midea"])
            + _serp(D1, "Abertura", "Mercado Livre", "k2", ["LG"])
            + _serp(D2, "Abertura", "Mercado Livre", "k1", ["Midea"])
        )
        ult, ant, meta = comparable_days(df)
        assert set(ant["keyword"]) == {"k1"}
        assert meta["buscas_descartadas"] == 1
        # Sem o corte, o share iria de 50% para 100% sem o mercado mudar.
        assert midea_share_by(ult, by=())["share_midea"].iloc[0] == 1.0
        assert midea_share_by(ant, by=())["share_midea"].iloc[0] == 1.0

    def test_um_dia_so(self):
        df = pd.DataFrame(_serp(D2, "Abertura", "Magalu", "k", ["LG"]))
        ult, ant, meta = comparable_days(df)
        assert len(ult) == 1 and ant.empty and meta["anterior"] is None


class TestBatalhaPorKeyword:
    def test_ordena_pela_distancia_ao_lider_e_calcula_delta(self):
        ontem = pd.DataFrame(
            _serp(D1, "Abertura", "Magalu", "ar condicionado", ["Midea", "LG"])
            + _serp(D1, "Abertura", "Magalu", "ar condicionado inverter", ["Midea", "Midea"])
        )
        hoje = pd.DataFrame(
            _serp(D2, "Abertura", "Magalu", "ar condicionado", ["LG", "LG", "LG", "Midea"])
            + _serp(D2, "Abertura", "Magalu", "ar condicionado inverter", ["Midea", "Midea"])
        )
        out = keyword_battle(hoje, ontem)
        assert out["keyword"].iloc[0] == "ar condicionado"
        assert out["delta_pp"].iloc[0] == pytest.approx(-25.0)
        assert out["lider"].iloc[0] == "LG"


class TestBuyBoxDasOfertasMidea:
    def test_so_buy_box_observada_entra_no_ranking(self):
        df = pd.DataFrame([
            {"data": D2, "turno": "A", "plataforma": "Amazon", "produto": "p1", "marca": "Midea",
             "seller": "Amazon", "buy_box_seller": "Web Continental"},
            {"data": D2, "turno": "A", "plataforma": "Amazon", "produto": "p2", "marca": "Midea",
             "seller": "Amazon", "buy_box_seller": None},
            {"data": D2, "turno": "A", "plataforma": "Magalu", "produto": "p3", "marca": "Midea",
             "seller": "Frigelar", "buy_box_seller": "Frigelar"},
            {"data": D2, "turno": "A", "plataforma": "Magalu", "produto": "p4", "marca": "LG",
             "seller": "LG", "buy_box_seller": "LG"},
        ])
        rank, meta = buybox_on_brand(df)
        assert meta["ofertas_midea"] == 3
        assert meta["com_buybox"] == 2
        assert meta["cobertura"] == pytest.approx(2 / 3)
        assert set(rank["seller"]) == {"Web Continental", "Frigelar"}
        assert "Amazon" not in set(rank["seller"])

    def test_mesma_oferta_em_varias_keywords_conta_uma_vez(self):
        base = {"data": D2, "turno": "A", "plataforma": "Magalu", "produto": "p1",
                "marca": "Midea", "buy_box_seller": "Frigelar"}
        df = pd.DataFrame([{**base, "keyword": "k1"}, {**base, "keyword": "k2"}])
        rank, meta = buybox_on_brand(df)
        assert meta["ofertas_midea"] == 1 and rank["ofertas"].iloc[0] == 1


class TestPrecoNaVitrine:
    @pytest.mark.parametrize("nome,btu", [
        ("Ar Condicionado Split Midea 12.000 BTUs Inverter", 12000),
        ("Split LG 9000 BTU Dual Inverter", 9000),
        ("Ar 18,000 btus", 18000),
        ("Ventilador de teto", None),
    ])
    def test_extract_btu(self, nome, btu):
        assert extract_btu(nome) == btu

    def test_indice_por_btu_e_exclui_portatil(self):
        df = pd.DataFrame([
            {"produto": "Split Midea 12000 BTUs", "preco": 2200, "marca": "Midea", "estado_match": "MAPEADO"},
            {"produto": "Split LG 12000 BTUs", "preco": 2000, "marca": "LG", "estado_match": "MAPEADO"},
            {"produto": "Split Gree 12000 BTUs", "preco": 2400, "marca": "Gree", "estado_match": "MAPEADO"},
            {"produto": "Portátil Midea 12000 BTUs", "preco": 1500, "marca": "Midea", "estado_match": "FORA_ESCOPO"},
            {"produto": "Portátil Philco 12000 BTUs", "preco": 1400, "marca": "Philco", "estado_match": None},
            {"produto": "Kit instalação 12000 BTUs", "preco": 150, "marca": "Desconhecida", "estado_match": None},
        ])
        out = shelf_price_by_btu(df).set_index("btu")
        assert out.at[12000, "mediana_midea"] == 2200
        assert out.at[12000, "mediana_rivais"] == 2200  # (2000, 2400)
        assert out.at[12000, "indice"] == pytest.approx(100.0)
        assert out.at[12000, "ofertas_rivais"] == 2


class TestCoberturaEAlertas:
    def _cenario(self):
        linhas = []
        # ML: Abertura cheia nos dois dias; Tarde quase vazia no último (login gate).
        for dia in (D1, D2):
            linhas += _serp(dia, "Abertura", "Mercado Livre", "ar condicionado", ["LG"] * 10)
        linhas += _serp(D1, "Tarde", "Mercado Livre", "ar condicionado", ["LG"] * 10)
        linhas += _serp(D2, "Tarde", "Mercado Livre", "ar condicionado", ["Midea"])
        # Magalu: Midea cai de 50% para 10% e perde o top 3 em "ar condicionado".
        linhas += _serp(D1, "Abertura", "Magalu", "ar condicionado", ["Midea"] * 5 + ["LG"] * 5)
        linhas += _serp(D2, "Abertura", "Magalu", "ar condicionado",
                        ["LG", "LG", "LG", "LG", "Midea"] + ["LG"] * 5)
        return pd.DataFrame(linhas)

    def test_coleta_parcial_detectada(self):
        cob = coverage_by_turno(self._cenario())
        ml_tarde = cob[(cob["plataforma"] == "Mercado Livre") & (cob["turno"] == "Tarde") & (cob["data"] == D2)]
        assert bool(ml_tarde["parcial"].iloc[0])
        assert not cob[cob["plataforma"] == "Magalu"]["parcial"].any()

    def test_alertas(self):
        df = self._cenario()
        cob = coverage_by_turno(df)
        ult, ant, _ = comparable_days(df)
        alertas = build_alerts(ult, ant, cob)
        textos = " | ".join(a["texto"] for a in alertas)
        assert "Magalu: Midea perdeu 40.0 pp" in textos
        assert "Magalu: Midea saiu do top 3 em 1 busca(s)" in textos
        assert "Mercado Livre: coleta parcial em Tarde" in textos
        # Plataforma parcial NÃO gera alerta de share ("silêncio não é mercado").
        assert "Mercado Livre: Midea ganhou" not in textos
        assert alertas[0]["nivel"] == "alta"

    def test_sem_dia_anterior_so_ressalvas(self):
        df = pd.DataFrame(_serp(D2, "Abertura", "Magalu", "k", ["LG"]))
        assert build_alerts(df, df.iloc[0:0]) == []
