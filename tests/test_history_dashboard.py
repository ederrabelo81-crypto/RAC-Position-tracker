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


class TestHistoricoSemDepara:
    """Partição sem as colunas de resolução: não classificada ≠ rejeitada.

    `estado_match`, `familia_resolvida`, `sku_resolvido` e
    `voltagem_resolvida` são preenchidos pela automação Admin **no Supabase**,
    depois do upload. Partições gravadas pela coleta ou importadas de CSV não
    os têm, e o filtro padrão (`estado_match = MAPEADO`) as esconderia por
    inteiro — foi o que fez 97 mil linhas recuperadas do Drive sumirem do
    painel em 26/07/2026.

    O interruptor `gf_historico_sem_depara` decide: ligado (padrão) elas
    aparecem; desligado, vale a paridade estrita com o PostgREST.
    """

    @pytest.fixture
    def sem_resolucao(self) -> pd.DataFrame:
        """Como uma partição gravada por `main.py` ou vinda de `import-csv`."""
        return pd.DataFrame([{
            "data": date(2026, 3, 10), "plataforma": "Mercado Livre",
            "marca": "Midea", "produto": "Split Midea 12000", "posicao_geral": 1,
        }])

    @pytest.fixture
    def desligado(self, monkeypatch):
        """Força o modo estrito (fail-closed)."""
        monkeypatch.setattr(app, "_gf_historico_sem_depara", lambda: False)

    @pytest.fixture
    def ligado(self, monkeypatch):
        monkeypatch.setattr(app, "_gf_historico_sem_depara", lambda: True)

    # -- padrão: aparece -----------------------------------------------------
    def test_estado_match_ausente_passa_por_padrao(self, sem_resolucao, ligado):
        out = app._filter_history_coletas(sem_resolucao, estados_match=["MAPEADO"])
        assert len(out) == 1

    def test_familia_ausente_passa_por_padrao(self, sem_resolucao, ligado):
        out = app._filter_history_coletas(
            sem_resolucao, familias_resolvidas=["MIDEA-12000-F"]
        )
        assert len(out) == 1

    def test_filtros_de_coleta_continuam_valendo(self, sem_resolucao, ligado):
        """Admitir a linha não é ignorar os demais filtros."""
        assert app._filter_history_coletas(
            sem_resolucao, platforms=["Amazon"], estados_match=["MAPEADO"]
        ).empty
        assert len(app._filter_history_coletas(
            sem_resolucao, platforms=["Mercado Livre"], estados_match=["MAPEADO"]
        )) == 1

    # -- modo estrito: some --------------------------------------------------
    def test_estado_match_ausente_nao_passa_no_estrito(self, sem_resolucao, desligado):
        assert app._filter_history_coletas(
            sem_resolucao, estados_match=["MAPEADO"]
        ).empty

    def test_familia_ausente_nao_passa_no_estrito(self, sem_resolucao, desligado):
        assert app._filter_history_coletas(
            sem_resolucao, familias_resolvidas=["MIDEA-12000-F"]
        ).empty

    # -- partição resolvida não é afetada pelo interruptor -------------------
    def test_particao_resolvida_respeita_o_filtro(self, linhas, ligado):
        """Onde a coluna existe, o filtro vale de verdade nos dois modos."""
        assert app._filter_history_coletas(linhas, estados_match=["REVISAR"]).empty
        assert len(app._filter_history_coletas(linhas, estados_match=["MAPEADO"])) == 3

    def test_sem_filtro_de_resolucao_passa_normalmente(self, sem_resolucao, desligado):
        """Sem filtro pedido, a partição não resolvida continua legível."""
        out = app._filter_history_coletas(
            sem_resolucao, platforms=["Mercado Livre"], estados_match=[]
        )
        assert len(out) == 1

    def test_filtros_de_coleta_seguem_fail_open(self, sem_resolucao, desligado):
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

    def test_marca_a_procedencia(self, tmp_path, monkeypatch):
        """A coluna `_origem` deixa o painel avisar de onde vieram os números."""
        store = self._store_com(tmp_path, ["2026-03-10"])
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)
        out = app._history_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set())
        assert set(out["_origem"]) == {"historico"}

    def test_procedencia_distingue_sem_depara(self, tmp_path, monkeypatch):
        from utils.history import HistoryStore, LocalBackend
        store = HistoryStore(LocalBackend(tmp_path / "h"))
        store.write_records(
            [{"data": "2026-03-10", "plataforma": "Mercado Livre",
              "marca": "Midea", "produto": "Split Midea 12000"}],
            dataset="coletas",
        )
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)
        out = app._history_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set())
        assert set(out["_origem"]) == {"historico_sem_depara"}

    def test_overview_le_do_historico_sem_supabase(self, tmp_path, monkeypatch):
        """`_overview_data` tem query própria e não passa por `query_coletas`.

        Regressão de 26/07/2026: a Overview ficava em branco com o Supabase
        fora, mesmo com o Drive cheio — o banner dizia "exibindo 15.000 linhas
        do histórico frio" e logo abaixo "Nenhum dado encontrado", porque quem
        alimenta a página é esta função, não o `query_coletas`.
        """
        store = self._store_com(tmp_path, ["2026-03-10", "2026-03-11"])
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)
        monkeypatch.setattr(app, "_get_supabase", lambda: None)

        df = app._overview_data(
            "2026-03-01", "2026-03-31", (), (),
            estados_tuple=("MAPEADO",), sem_depara_flag=True,
        )
        assert len(df) == 2

    def test_overview_respeita_o_modo_estrito(self, tmp_path, monkeypatch):
        from utils.history import HistoryStore, LocalBackend
        store = HistoryStore(LocalBackend(tmp_path / "h"))
        store.write_records(
            [{"data": "2026-03-10", "plataforma": "Mercado Livre",
              "marca": "Midea", "produto": "Split Midea 12000"}],
            dataset="coletas",
        )
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)
        monkeypatch.setattr(app, "_get_supabase", lambda: None)

        assert app._overview_data(
            "2026-03-01", "2026-03-31", (), (),
            estados_tuple=("MAPEADO",), sem_depara_flag=False,
        ).empty

    def test_opcoes_de_filtro_saem_do_historico(self, tmp_path, monkeypatch):
        """Sem isso os dropdowns da barra lateral ficam vazios e o usuário não
        consegue filtrar os dados que ESTÃO no Drive."""
        from utils.history import HistoryStore, LocalBackend
        store = HistoryStore(LocalBackend(tmp_path / "h"))
        store.write_records(
            [{"data": date.today().isoformat(), "plataforma": "Amazon",
              "tipo": "Marketplace", "marca": "Midea", "keyword": "ar condicionado",
              "seller": "Loja X", "produto": "Split Midea 12000"}],
            dataset="coletas",
        )
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)

        opts = app._filter_options_do_historico()
        assert opts["platforms"] == ["Amazon"]
        assert "Midea" in opts["brands"]
        assert opts["keywords"] == ["ar condicionado"]

    def test_opcoes_sem_historico_devolve_vazio(self, tmp_path, monkeypatch):
        from utils.history import HistoryStore, LocalBackend
        store = HistoryStore(LocalBackend(tmp_path / "vazio"))
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)
        assert app._filter_options_do_historico() == {}

    def test_falha_do_backend_nao_derruba_a_pagina(self, monkeypatch):
        def _explode(*_a, **_k):
            raise RuntimeError("Drive fora do ar")

        monkeypatch.setattr("utils.history.get_store", _explode)
        assert app._history_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set()).empty
