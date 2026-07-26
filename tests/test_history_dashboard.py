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
    """Amostra no schema de `coletas`, variada o bastante para os filtros.

    Inclui `estado_match` porque é o estado de uma partição vinda da migração
    (`tier`), que lê do Supabase já resolvido — o caso normal de leitura.
    """
    return pd.DataFrame([
        {
            "data": date(2026, 3, 10), "plataforma": "Mercado Livre",
            "tipo": "Marketplace", "marca": "Midea", "seller": "Loja Midea",
            "keyword": "ar condicionado", "produto": "Split Midea 12000 BTUs Inverter",
            "posicao_geral": 1, "preco": 2199.90, "estado_match": "MAPEADO",
        },
        {
            "data": date(2026, 3, 10), "plataforma": "Amazon",
            "tipo": "Marketplace", "marca": "LG", "seller": "AmazonBR",
            "keyword": "split 9000", "produto": "Split LG 9000 BTUs Janela",
            "posicao_geral": 12, "preco": 1899.00, "estado_match": "MAPEADO",
        },
        {
            "data": date(2026, 3, 11), "plataforma": "Magalu",
            "tipo": "Marketplace", "marca": "Gree", "seller": "Magalu",
            "keyword": "ar condicionado", "produto": "Split Gree 18.000 BTUs On/Off",
            "posicao_geral": 3, "preco": 2799.00, "estado_match": "MAPEADO",
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

    def test_estado_match_filtra(self, linhas):
        out = app._filter_history_coletas(linhas, estados_match=["MAPEADO"])
        assert len(out) == 3
        assert app._filter_history_coletas(linhas, estados_match=["REVISAR"]).empty

    def test_dataframe_vazio(self):
        assert app._filter_history_coletas(pd.DataFrame()).empty

    def test_nao_muta_a_entrada(self, linhas):
        antes = len(linhas)
        app._filter_history_coletas(linhas, platforms=["Amazon"])
        assert len(linhas) == antes


class TestFiltrosDeResolucaoSaoFailClosed:
    """Coluna de resolução ausente ⇒ nenhuma linha passa.

    `estado_match`, `familia_resolvida`, `sku_resolvido` e
    `voltagem_resolvida` são preenchidos pela automação Admin **depois** do
    upload, então partições gravadas direto pela coleta não os têm. No
    PostgREST uma linha com esses campos NULL não casaria com `.in_(...)` —
    incluí-las aqui misturaria REVISAR/NAO_AC nas visões curadas.
    """

    @pytest.fixture
    def sem_resolucao(self) -> pd.DataFrame:
        """Como uma partição gravada por `main.py` no momento da coleta."""
        return pd.DataFrame([{
            "data": date(2026, 3, 10), "plataforma": "Mercado Livre",
            "marca": "Midea", "produto": "Split Midea 12000", "posicao_geral": 1,
        }])

    def test_estado_match_ausente_nao_passa(self, sem_resolucao):
        assert app._filter_history_coletas(
            sem_resolucao, estados_match=["MAPEADO"]
        ).empty

    def test_familia_ausente_nao_passa(self, sem_resolucao):
        assert app._filter_history_coletas(
            sem_resolucao, familias_resolvidas=["MIDEA-12000-F"]
        ).empty

    def test_sem_filtro_de_resolucao_passa_normalmente(self, sem_resolucao):
        """Sem filtro pedido, a partição não resolvida continua legível."""
        out = app._filter_history_coletas(
            sem_resolucao, platforms=["Mercado Livre"], estados_match=[]
        )
        assert len(out) == 1

    def test_filtros_de_coleta_seguem_fail_open(self, sem_resolucao):
        """Coluna de COLETA ausente é ignorada — só resolução é fail-closed."""
        out = app._filter_history_coletas(
            sem_resolucao, platform_types=["Marketplace"], estados_match=[]
        )
        assert len(out) == 1


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
                    "estado_match": "MAPEADO",
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

    def test_respeita_o_limite(self, tmp_path, monkeypatch):
        """Sem teto, um intervalo histórico longo estouraria o cap da página."""
        store = self._store_com(tmp_path, [f"2026-03-{d:02d}" for d in range(1, 11)])
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)

        out = app._history_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set(), limit=4)
        assert len(out) == 4
        # Mantém os dias mais recentes, como o keyset (data desc) do Supabase.
        assert out["data"].max() == date(2026, 3, 10)

    def test_limite_zero_nao_le_nada(self, tmp_path, monkeypatch):
        store = self._store_com(tmp_path, ["2026-03-10"])
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)
        assert app._history_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), limit=0
        ).empty

    def test_falha_do_backend_nao_derruba_a_pagina(self, monkeypatch):
        def _explode(*_a, **_k):
            raise RuntimeError("Drive fora do ar")

        monkeypatch.setattr("utils.history.get_store", _explode)
        assert app._history_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set()).empty
