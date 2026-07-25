"""
tests/test_history_dashboard.py — Costura do histórico frio no dashboard.

`query_coletas` passou a completar com o histórico em Parquet os dias que o
Supabase não devolve (migrados pela janela quente, ou banco restrito por cota).
O risco dessa mudança é o filtro: o PostgREST filtrava no servidor, o histórico
precisa filtrar em pandas. Estes testes fixam a paridade.
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

import app  # noqa: E402 — importável sem renderizar

pytest.importorskip("pyarrow", reason="histórico em Parquet exige pyarrow")


@pytest.fixture
def linhas() -> pd.DataFrame:
    """Amostra no schema de `coletas`, variada o bastante para os filtros."""
    return pd.DataFrame([
        {
            "data": date(2026, 3, 10), "plataforma": "Mercado Livre",
            "tipo": "Marketplace", "marca": "Midea", "seller": "Loja Midea",
            "keyword": "ar condicionado", "produto": "Split Midea 12000 BTUs Inverter",
            "posicao_geral": 1, "preco": 2199.90,
        },
        {
            "data": date(2026, 3, 10), "plataforma": "Amazon",
            "tipo": "Marketplace", "marca": "LG", "seller": "AmazonBR",
            "keyword": "split 9000", "produto": "Split LG 9000 BTUs Janela",
            "posicao_geral": 12, "preco": 1899.00,
        },
        {
            "data": date(2026, 3, 11), "plataforma": "Magalu",
            "tipo": "Marketplace", "marca": "Gree", "seller": "Magalu",
            "keyword": "ar condicionado", "produto": "Split Gree 18.000 BTUs On/Off",
            "posicao_geral": 3, "preco": 2799.00,
        },
    ])


class TestFiltroDoHistorico:
    """Paridade entre os predicados do PostgREST e os do pandas."""

    def test_sem_filtro_devolve_tudo(self, linhas):
        assert len(app._filter_history_coletas(linhas)) == 3

    def test_plataforma(self, linhas):
        out = app._filter_history_coletas(linhas, platforms=["Amazon"])
        assert list(out["plataforma"]) == ["Amazon"]

    def test_marca(self, linhas):
        out = app._filter_history_coletas(linhas, brands=["Midea"])
        assert list(out["marca"]) == ["Midea"]

    def test_seller_e_keyword(self, linhas):
        assert len(app._filter_history_coletas(linhas, sellers=["Magalu"])) == 1
        assert len(app._filter_history_coletas(linhas, keywords=["ar condicionado"])) == 2

    def test_posicao_maxima(self, linhas):
        out = app._filter_history_coletas(linhas, max_position=3)
        assert sorted(out["posicao_geral"]) == [1, 3]

    def test_btu_aceita_ponto_de_milhar(self, linhas):
        """"18000" no filtro precisa achar "18.000" no texto do produto."""
        out = app._filter_history_coletas(linhas, btu_filter=["18000"])
        assert len(out) == 1
        assert out.iloc[0]["marca"] == "Gree"

    def test_btu_sem_ponto(self, linhas):
        out = app._filter_history_coletas(linhas, btu_filter=["12000"])
        assert list(out["marca"]) == ["Midea"]

    def test_tipo_de_produto(self, linhas):
        assert len(app._filter_history_coletas(linhas, product_types=["Inverter"])) == 1
        assert len(app._filter_history_coletas(linhas, product_types=["Janela"])) == 1

    def test_filtros_combinam_em_e(self, linhas):
        out = app._filter_history_coletas(
            linhas, platforms=["Mercado Livre"], brands=["LG"]
        )
        assert out.empty

    def test_multiplos_valores_combinam_em_ou(self, linhas):
        out = app._filter_history_coletas(linhas, platforms=["Amazon", "Magalu"])
        assert len(out) == 2

    def test_coluna_ausente_ignora_o_filtro(self, linhas):
        """Partição antiga sem `estado_match` não pode sumir do relatório."""
        out = app._filter_history_coletas(linhas, estados_match=["MAPEADO"])
        assert len(out) == 3

    def test_dataframe_vazio(self):
        assert app._filter_history_coletas(pd.DataFrame()).empty

    def test_nao_muta_a_entrada(self, linhas):
        antes = len(linhas)
        app._filter_history_coletas(linhas, platforms=["Amazon"])
        assert len(linhas) == antes


class TestGapFill:
    """`_history_gap_fill` só traz os dias que o Supabase não entregou."""

    def _store_com(self, tmp_path, dias):
        from utils.history import HistoryStore, LocalBackend
        store = HistoryStore(LocalBackend(tmp_path / "h"))
        for dia in dias:
            store.write_records(
                [{
                    "data": dia, "plataforma": "Mercado Livre", "marca": "Midea",
                    "produto": "Split Midea 12000", "posicao_geral": 1,
                }],
                dataset="coletas",
            )
        return store

    def test_exclui_dias_ja_presentes(self, tmp_path, monkeypatch):
        store = self._store_com(tmp_path, ["2026-03-10", "2026-03-11"])
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)

        out = app._history_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), {date(2026, 3, 10)}
        )
        assert list(out["data"].unique()) == [date(2026, 3, 11)]

    def test_sem_historico_devolve_vazio(self, tmp_path, monkeypatch):
        store = self._store_com(tmp_path, [])
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)
        assert app._history_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set()).empty

    def test_falha_do_backend_nao_derruba_a_pagina(self, monkeypatch):
        def _explode(*_a, **_k):
            raise RuntimeError("Drive fora do ar")

        monkeypatch.setattr("utils.history.get_store", _explode)
        assert app._history_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set()).empty
