"""
tests/test_price_series.py — "uma série de preço, uma fonte" (utils/price_series.py).

Regressão fixada aqui (validação do dashboard contra o banco, 30/09/2026): a
série de preço da Philco caía de ~R$ 13 mil para ~R$ 2 mil no último dia sem o
mercado mexer. `pricetrack_daily` não tinha 25/09, 26/09 e 29/09 e a
precedência por (data, SKU) deixava as coletas preencherem esses dias — a linha
trocava de fonte no meio. Aqui: cada série fica numa fonte só, a outra fonte
nunca tapa o buraco e a legenda lista os dias em branco.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.price_series import (  # noqa: E402
    capacity_btu,
    coverage_caption,
    days_by_source,
    format_btu,
    format_day_list,
    fora_hiwall_mask,
    pick_series_sources,
    single_source_per_series,
    source_label,
)
from utils.shelf_insights import is_fora_hiwall  # noqa: E402


def d(day: int, month: int = 9) -> date:
    return date(2026, month, day)


def _rows(serie: str, fonte: str, dias, preco: float, col: str = "marca") -> list[dict]:
    return [{"data": d(x), col: serie, "source": fonte, "preco": preco} for x in dias]


class TestUmaFontePorSerie:
    def test_caso_philco_fica_no_pricetrack_e_nao_tapa_buraco(self):
        """PT sem 25, 26 e 29/09: a série fica no PT e esses dias ficam em branco."""
        pt_dias = [22, 23, 24, 27, 28, 30]
        df = pd.DataFrame(
            _rows("Philco", "pricetrack", pt_dias, 13000.0)
            + _rows("Philco", "coletas", range(22, 31), 2000.0))
        kept, rep = single_source_per_series(df, ["marca"])
        assert set(kept["source"]) == {"pricetrack"}
        assert set(kept["preco"]) == {13000.0}, "preço das coletas vazou para a série"
        r = rep.iloc[0]
        assert r["fonte"] == "pricetrack"
        assert r["dias_fonte"] == len(pt_dias)
        assert r["datas_descartadas"] == (d(25), d(26), d(29))
        assert r["dias_descartados"] == 3
        assert r["linhas_descartadas"] == 9
        assert r["fontes_descartadas"] == ("coletas",)

    def test_outra_fonte_sai_de_todos_os_dias(self):
        """A escolha é da série inteira, não do dia: nem nos dias em que só a
        outra fonte existe ela volta."""
        df = pd.DataFrame(
            _rows("LG", "pricetrack", [1, 2, 3, 4], 3000.0)
            + _rows("LG", "coletas", [4, 5], 2800.0))
        kept, _ = single_source_per_series(df, ["marca"])
        assert kept["source"].unique().tolist() == ["pricetrack"]
        assert d(5) not in set(kept["data"])

    def test_hoje_sem_pricetrack_nao_vira_coletas(self):
        """Janela que termina hoje: PT tem 6 dias, coletas 7. A regra pura
        "mais dias vence" passaria TODA série para as coletas durante o dia."""
        df = pd.DataFrame(
            _rows("Midea", "pricetrack", range(24, 30), 2000.0)
            + _rows("Midea", "coletas", range(24, 31), 1800.0))
        _, rep = single_source_per_series(df, ["marca"])
        assert rep.iloc[0]["fonte"] == "pricetrack"
        assert rep.iloc[0]["datas_descartadas"] == (d(30),)

    def test_pricetrack_ralo_passa_para_coletas(self):
        """PT cobre menos da metade dos dias das coletas → coletas (série inteira)."""
        df = pd.DataFrame(
            _rows("Gree", "pricetrack", [1, 2], 2500.0)
            + _rows("Gree", "coletas", range(1, 8), 2300.0))
        kept, rep = single_source_per_series(df, ["marca"])
        assert rep.iloc[0]["fonte"] == "coletas"
        assert set(kept["source"]) == {"coletas"}
        assert kept["data"].nunique() == 7
        assert rep.iloc[0]["dias_descartados"] == 0  # PT não tinha dia exclusivo

    def test_empate_vai_para_pricetrack(self):
        df = pd.DataFrame(
            _rows("TCL", "coletas", [1, 2], 1.0)
            + _rows("TCL", "pricetrack", [3, 4], 2.0))
        _, rep = single_source_per_series(df, ["marca"])
        assert rep.iloc[0]["fonte"] == "pricetrack"

    def test_limiar_configuravel(self):
        df = pd.DataFrame(
            _rows("X", "pricetrack", [1, 2, 3], 1.0)
            + _rows("X", "coletas", [1, 2, 3, 4], 2.0))
        assert pick_series_sources(df, ["marca"], min_prefer_share=1.0)["fonte"].iloc[0] == "coletas"
        assert pick_series_sources(df, ["marca"])["fonte"].iloc[0] == "pricetrack"
        ralo = pd.DataFrame(
            _rows("X", "pricetrack", [1], 1.0) + _rows("X", "coletas", range(1, 30), 2.0))
        assert pick_series_sources(ralo, ["marca"], min_prefer_share=0)["fonte"].iloc[0] == "pricetrack"

    def test_series_independentes(self):
        """Cada série escolhe a própria fonte — uma marca no PT, outra nas coletas."""
        df = pd.DataFrame(
            _rows("Midea", "pricetrack", range(1, 8), 2000.0)
            + _rows("Midea", "coletas", range(1, 8), 1800.0)
            + _rows("Agratto", "coletas", range(1, 8), 1500.0))
        kept, rep = single_source_per_series(df, ["marca"])
        fontes = kept.groupby("marca")["source"].unique().map(list).to_dict()
        assert fontes == {"Midea": ["pricetrack"], "Agratto": ["coletas"]}
        assert rep.set_index("marca")["fonte"].to_dict() == {
            "Midea": "pricetrack", "Agratto": "coletas"}

    def test_sem_colunas_de_serie_o_recorte_e_uma_serie(self):
        df = pd.DataFrame(
            _rows("A", "pricetrack", [1, 2, 3], 1.0)
            + _rows("B", "coletas", [4], 2.0))
        kept, rep = single_source_per_series(df, [])
        assert set(kept["source"]) == {"pricetrack"}
        assert len(rep) == 1 and rep.iloc[0]["datas_descartadas"] == (d(4),)

    def test_chave_nula_e_serie_propria(self):
        df = pd.DataFrame(
            _rows("A", "pricetrack", [1], 1.0) + _rows(None, "coletas", [1], 2.0))
        kept, rep = single_source_per_series(df, ["marca"])
        assert len(kept) == 2 and len(rep) == 2

    def test_serie_com_duas_colunas(self):
        df = pd.DataFrame(
            [{"data": d(1), "marca": "Midea", "btu": 9000, "source": "coletas", "preco": 1.0},
             {"data": d(1), "marca": "Midea", "btu": 12000, "source": "pricetrack", "preco": 2.0},
             {"data": d(2), "marca": "Midea", "btu": 12000, "source": "coletas", "preco": 3.0}])
        kept, rep = single_source_per_series(df, ["marca", "btu"])
        assert rep.set_index("btu")["fonte"].to_dict() == {9000: "coletas", 12000: "pricetrack"}
        assert sorted(kept["preco"]) == [1.0, 2.0]

    def test_indice_e_linhas_preservados(self):
        df = pd.DataFrame(
            _rows("A", "pricetrack", [1, 2], 1.0) + _rows("A", "coletas", [1], 2.0),
            index=[10, 20, 30])
        kept, _ = single_source_per_series(df, ["marca"])
        assert kept.index.tolist() == [10, 20]

    def test_sem_coluna_de_fonte_devolve_tudo(self):
        df = pd.DataFrame([{"data": d(1), "marca": "A", "preco": 1.0}])
        kept, rep = single_source_per_series(df, ["marca"])
        assert len(kept) == 1 and rep.empty

    def test_df_vazio(self):
        kept, rep = single_source_per_series(pd.DataFrame(), ["marca"])
        assert kept.empty and rep.empty


class TestLegenda:
    def test_format_day_list_faixas(self):
        dias = [d(25), d(26), d(29), d(30), d(1, 10)]
        assert format_day_list(dias) == "25–26/09, 29/09–01/10"

    def test_format_day_list_aceita_timestamp_e_texto(self):
        assert format_day_list([pd.Timestamp("2026-09-25"), "2026-09-26"]) == "25–26/09"

    def test_format_day_list_vazio_e_corte(self):
        assert format_day_list([]) == "—"
        muitos = [d(x) for x in range(1, 30, 2)]  # 15 dias isolados
        assert format_day_list(muitos, max_runs=3).endswith("+12")

    def test_coverage_caption(self):
        df = pd.DataFrame(
            _rows("Philco", "pricetrack", [24, 27], 1.0)
            + _rows("Philco", "coletas", [24, 25, 26, 27], 2.0)
            + _rows("Midea", "pricetrack", [24, 25, 26, 27], 1.0))
        _, rep = single_source_per_series(df, ["marca"])
        assert coverage_caption(rep, "marca") == "Philco (PriceTrack) sem 25–26/09"

    def test_coverage_caption_serie_unica_sem_nome(self):
        df = pd.DataFrame(
            _rows("A", "pricetrack", [24, 27], 1.0) + _rows("B", "coletas", [25], 2.0))
        _, rep = single_source_per_series(df, [])
        assert coverage_caption(rep) == "PriceTrack sem 25/09"

    def test_coverage_caption_sem_lacuna(self):
        df = pd.DataFrame(_rows("Midea", "pricetrack", [1, 2], 1.0))
        _, rep = single_source_per_series(df, ["marca"])
        assert coverage_caption(rep, "marca") == ""

    def test_days_by_source(self):
        df = pd.DataFrame(
            _rows("A", "pricetrack", [2, 1], 1.0) + _rows("A", "coletas", [3], 1.0))
        assert days_by_source(df) == {"pricetrack": [d(1), d(2)], "coletas": [d(3)]}

    def test_source_label(self):
        assert source_label("pricetrack") == "PriceTrack"
        assert source_label("coletas") == "Coletas"
        assert source_label(None) == "desconhecida"


class TestCapacidade:
    def test_catalogo_vence_titulo(self):
        df = pd.DataFrame({
            "sku": ["SKU-A", None, "SKU-X", None],
            "produto": ["Split 9000 BTUs", "Ar LG 12.000 BTUs Inverter",
                        "Ar Gree 24000btu", "Ventilador de teto"],
        })
        out = capacity_btu(df, {"SKU-A": 18000})
        assert out.tolist()[:3] == [18000, 12000, 24000]
        assert pd.isna(out.iloc[3])
        assert str(out.dtype) == "Int64"

    def test_titulo_do_pricetrack_quando_produto_nao_diz(self):
        df = pd.DataFrame({"sku": ["S"], "produto": ["AR SPLIT MIDEA"],
                           "title": ["SPLIT MIDEA 12000 BTU FRIO"]})
        assert capacity_btu(df).tolist() == [12000]

    def test_fora_hiwall(self):
        df = pd.DataFrame({
            "sku": [None, None, "CAT-1", None],
            "produto": ["Ar Condicionado Portátil 12000 BTUs", "Split Hi-Wall 12000",
                        "Cassete 24000 BTUs", "Ar de Janela 7500 BTUs"],
        })
        assert fora_hiwall_mask(df).tolist() == [True, False, True, True]
        # SKU do catálogo (só RAC High Wall) é hi-wall por construção.
        assert fora_hiwall_mask(df, hiwall_skus={"CAT-1"}).tolist() == [True, False, False, True]

    def test_is_fora_hiwall(self):
        assert is_fora_hiwall("Ar Condicionado Piso-Teto 36000")
        assert not is_fora_hiwall("Split Inverter 12000 BTUs")
        assert not is_fora_hiwall(None)

    @pytest.mark.parametrize("btu,txt", [(12000, "12.000 BTU"), ("9000", "9.000 BTU"),
                                         (None, "BTU ?")])
    def test_format_btu(self, btu, txt):
        assert format_btu(btu) == txt
