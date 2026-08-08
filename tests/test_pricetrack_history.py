"""
tests/test_pricetrack_history.py — PriceTrack no histórico frio.

`pricetrack_daily` é a maior tabela do banco e, até Ago/2026, a única grande
sem caminho para o Drive: a migração era hardcoded em `coletas` e
`query_pricetrack_daily` lia só do Supabase. Um dia migrado sumia do painel.

Estes testes fixam as duas pontas da correção:

* **CLI** — `tier` aceita `--dataset pricetrack|all` e usa a tabela e a coluna
  de data certas (`pricetrack_daily` / `collection_date`), sem regredir o
  comportamento default de `coletas`.
* **Dashboard** — `_filter_history_pricetrack` reproduz em pandas os
  predicados que o PostgREST aplica no servidor, e o resultado do frio sai no
  mesmo schema do quente (`_pt_raw_to_df`).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402 — importável sem renderizar
from scripts import history_cli  # noqa: E402

pytest.importorskip("pyarrow", reason="histórico em Parquet exige pyarrow")


@pytest.fixture
def linhas() -> pd.DataFrame:
    """Amostra no schema cru de `pricetrack_daily`.

    Valores em caixa alta em `marketplace`/`brand` de propósito: é assim que o
    PriceTrack publica, e é o que obriga o filtro a ser case-insensitive.
    """
    return pd.DataFrame([
        {
            "collection_date": date(2026, 3, 10), "turno": "Diário",
            "brand": "MIDEA", "sku": "SKU-9K", "title": "SPLIT MIDEA 9000 BTU FRIO",
            "marketplace": "MERCADO LIVRE", "seller": "Loja Midea",
            "min_price": 1999.0, "avg_price": 2050.0, "mode_price": 2020.0,
            "max_price": 2100.0,
        },
        {
            "collection_date": date(2026, 3, 10), "turno": "Manhã",
            "brand": "LG", "sku": "SKU-12K", "title": "SPLIT LG 12.000 BTU INVERTER",
            "marketplace": "AMAZON", "seller": "AmazonBR",
            "min_price": 2599.0, "avg_price": 2650.0, "mode_price": None,
            "max_price": 2700.0,
        },
        {
            "collection_date": date(2026, 3, 11), "turno": "Diário",
            "brand": "GREE", "sku": "SKU-18K", "title": "SPLIT GREE 18000 BTU ON/OFF",
            "marketplace": "MAGAZINE LUIZA", "seller": "Magalu",
            "min_price": 3199.0, "avg_price": 3250.0, "mode_price": 3220.0,
            "max_price": 3300.0,
        },
    ])


class TestFiltroDoHistoricoPricetrack:
    """Paridade entre os predicados do PostgREST e os do pandas."""

    def test_default_e_so_diario(self, linhas):
        """Sem `turnos`, o banco faz `.eq("turno", "Diário")` — o frio idem."""
        out = app._filter_history_pricetrack(linhas)
        assert len(out) == 2
        assert set(out["turno"]) == {"Diário"}

    def test_turnos_explicitos(self, linhas):
        out = app._filter_history_pricetrack(linhas, turnos=["Diário", "Manhã"])
        assert len(out) == 3

    def test_marca_case_insensitive(self, linhas):
        """O usuário escolhe 'Midea'; o dado está 'MIDEA'."""
        out = app._filter_history_pricetrack(linhas, brands=["Midea"])
        assert list(out["sku"]) == ["SKU-9K"]

    def test_plataforma_usa_nome_canonico(self, linhas):
        """'Mercado Livre' precisa casar com 'MERCADO LIVRE'."""
        out = app._filter_history_pricetrack(linhas, platforms=["Mercado Livre"])
        assert list(out["sku"]) == ["SKU-9K"]

    def test_seller(self, linhas):
        out = app._filter_history_pricetrack(linhas, turnos=["Manhã"], sellers=["amazonbr"])
        assert list(out["sku"]) == ["SKU-12K"]

    def test_sku_set(self, linhas):
        out = app._filter_history_pricetrack(linhas, sku_set={"SKU-18K"})
        assert list(out["sku"]) == ["SKU-18K"]

    def test_btu_com_separador_de_milhar(self, linhas):
        """`12000` tem de achar o título escrito '12.000'."""
        out = app._filter_history_pricetrack(
            linhas, turnos=["Manhã"], btu_filter=["12000"]
        )
        assert list(out["sku"]) == ["SKU-12K"]

    def test_filtro_vazio_devolve_vazio_sem_estourar(self, linhas):
        out = app._filter_history_pricetrack(linhas, brands=["Marca Inexistente"])
        assert out.empty

    def test_dataframe_vazio_e_tolerado(self):
        assert app._filter_history_pricetrack(pd.DataFrame()).empty


class TestSchemaDoFrio:
    """O frio precisa sair idêntico ao quente — é a mesma função de transporte."""

    def test_converte_para_o_schema_de_coletas(self, linhas):
        out = app._pt_raw_to_df(linhas)
        for col in ("data", "turno", "plataforma", "marca", "produto", "preco",
                    "seller", "source"):
            assert col in out.columns, col
        assert set(out["source"]) == {"pricetrack"}

    def test_diario_vira_sentinela_pricetrack(self, linhas):
        """Linhas 'Diário' saem como 'PriceTrack'; os turnos mantêm o rótulo."""
        out = app._pt_raw_to_df(linhas)
        assert set(out["turno"]) == {"PriceTrack", "Manhã"}

    def test_preco_cai_para_avg_quando_falta_a_moda(self, linhas):
        """SKU-12K não tem `mode_price` — a cadeia usa `avg_price`."""
        out = app._pt_raw_to_df(linhas)
        preco = dict(zip(out["sku"].astype(str), out["preco"]))
        assert preco["SKU-12K"] == 2650.0

    def test_vazio_devolve_vazio(self):
        assert app._pt_raw_to_df(pd.DataFrame()).empty


class _FakeQuery:
    """Encadeamento mínimo do postgrest-py, registrando a tabela consultada."""

    def __init__(self, registro: dict, tabela: str, linhas: list[dict]):
        self._registro = registro
        self._tabela = tabela
        self._linhas = linhas
        self.count = len(linhas)
        self.data = linhas

    def select(self, *a, **kw):
        self.count = len(self._linhas)
        return self

    def eq(self, coluna, valor):
        self._registro.setdefault("eq", []).append((self._tabela, coluna, valor))
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def range(self, *a, **kw):
        return self

    def delete(self):
        self._registro.setdefault("delete", []).append(self._tabela)
        return self

    def execute(self):
        return self


class _FakeClient:
    """Cliente Supabase falso: só registra qual tabela/coluna foi usada."""

    def __init__(self, linhas_por_tabela: dict[str, list[dict]]):
        self.registro: dict = {}
        self._linhas = linhas_por_tabela

    def table(self, nome: str):
        return _FakeQuery(self.registro, nome, self._linhas.get(nome, []))


class TestTierPorDataset:
    """A migração precisa acertar tabela e coluna de data de cada dataset."""

    def test_specs_apontam_para_as_tabelas_certas(self):
        coletas = history_cli._TIER_SPECS["coletas"]
        pricetrack = history_cli._TIER_SPECS["pricetrack"]
        assert (coletas.table, coletas.date_col) == ("coletas", "data")
        assert (pricetrack.table, pricetrack.date_col) == (
            "pricetrack_daily", "collection_date",
        )

    def test_default_continua_sendo_coletas(self):
        """Namespace sem `--dataset` (ex.: chamada antiga) não pode mudar."""
        specs = history_cli._tier_datasets(argparse.Namespace())
        assert [s.dataset for s in specs] == ["coletas"]

    def test_all_migra_os_dois(self):
        specs = history_cli._tier_datasets(argparse.Namespace(dataset="all"))
        assert [s.dataset for s in specs] == ["coletas", "pricetrack"]

    def test_fetch_day_usa_a_coluna_de_data_do_dataset(self):
        client = _FakeClient({"pricetrack_daily": []})
        spec = history_cli._TIER_SPECS["pricetrack"]
        history_cli._fetch_day(client, date(2026, 3, 10), spec)
        assert ("pricetrack_daily", "collection_date", "2026-03-10") in client.registro["eq"]

    def test_fetch_day_default_continua_em_coletas(self):
        client = _FakeClient({"coletas": []})
        history_cli._fetch_day(client, date(2026, 3, 10))
        assert ("coletas", "data", "2026-03-10") in client.registro["eq"]

    def test_parser_aceita_dataset(self):
        args = history_cli.build_parser().parse_args(
            ["tier", "--dataset", "pricetrack", "--confirm"]
        )
        assert args.dataset == "pricetrack"
        assert args.confirm is True

    def test_parser_rejeita_dataset_desconhecido(self):
        with pytest.raises(SystemExit):
            history_cli.build_parser().parse_args(["tier", "--dataset", "nao_existe"])


class TestCosturaDeVerdade:
    """Ida e volta real: grava Parquet, lê pelo gap-fill, confere o schema.

    Os testes acima isolam filtro e transporte; este prova que a peça inteira
    funciona sobre um store de verdade — que era exatamente o elo faltando
    (partição íntegra no Drive, invisível no painel).
    """

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        from utils.history.backends import LocalBackend
        from utils.history.store import HistoryStore

        s = HistoryStore(LocalBackend(tmp_path / "history"))
        monkeypatch.setattr(app, "_history_store", lambda: s)
        # `_avisar_particoes_ilegiveis` toca a sessão do Streamlit; fora do
        # runtime ela não existe, e o aviso não é o objeto do teste.
        monkeypatch.setattr(app, "_avisar_particoes_ilegiveis", lambda *_a, **_k: None)
        return s

    def _grava(self, store, linhas):
        # `date_column` é obrigatório aqui: o default do store é `data`, a
        # coluna de `coletas`. PriceTrack particiona por `collection_date`.
        store.write_records(
            linhas.to_dict("records"),
            dataset="pricetrack",
            run_id="teste01",
            date_column="collection_date",
        )

    def test_dia_migrado_volta_pelo_gap_fill(self, store, linhas):
        self._grava(store, linhas)
        out = app._pricetrack_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set())
        assert not out.empty
        # Só os 'Diário' — o default do filtro, igual ao banco.
        assert set(out["turno"]) == {"PriceTrack"}
        assert set(out["source"]) == {"pricetrack"}
        assert set(out["_origem"]) == {"historico"}

    def test_dias_ja_no_supabase_nao_sao_relidos(self, store, linhas):
        """Sem isso o dia sairia duplicado ao aparecer nas duas bases."""
        self._grava(store, linhas)
        out = app._pricetrack_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), {date(2026, 3, 10)}
        )
        assert list(out["data"].unique()) == [date(2026, 3, 11)]

    def test_filtros_valem_no_frio(self, store, linhas):
        self._grava(store, linhas)
        out = app._pricetrack_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), brands=["Gree"]
        )
        # A marca sai canonicalizada ('GREE' → 'Gree'), igual ao lado quente —
        # por isso a comparação é case-insensitive e não pelo valor cru.
        assert [str(m).casefold() for m in out["marca"]] == ["gree"]
        assert list(out["data"].unique()) == [date(2026, 3, 11)]

    def test_limite_zero_nao_le_nada(self, store, linhas):
        self._grava(store, linhas)
        assert app._pricetrack_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), limit=0
        ).empty

    def test_historico_indisponivel_degrada_sem_quebrar(self, monkeypatch):
        """Deploy sem pyarrow/libs do Drive não pode derrubar a página."""
        def _explode():
            raise RuntimeError("pyarrow ausente")

        monkeypatch.setattr(app, "_history_store", _explode)
        monkeypatch.setattr(app, "_avisar_uma_vez", lambda *_a, **_k: None)
        out = app._pricetrack_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set())
        assert out.empty
