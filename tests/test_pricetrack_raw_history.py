"""
tests/test_pricetrack_raw_history.py — Costura frio das páginas que leem
`pricetrack_daily` no schema CRU.

Contexto: com a janela quente do Supabase em 2 dias
(``docs/RETORNO_SUPABASE.md``), as páginas 🛡️ Price Compliance e 🚨 Top Movers
mostravam "sem dados" para qualquer janela maior que isso — elas liam
`pricetrack_daily` direto, sem a costura do histórico frio (Parquet no Drive)
que o resto do painel já tinha via `_history_gap_fill`/`_pricetrack_gap_fill`.

`_pricetrack_raw_gap_fill` é a contraparte que devolve as colunas CRUAS que
essas duas páginas consomem (`collection_date`, `min_price`, `avg_price`,
`mode_price`, `seller_canonical`, `is_midea_group`). Estes testes fixam:

* o gap-fill cru (dias fora da janela, filtros, `midea_only`, `seller_canonical`
  derivado);
* que `_query_pt_compliance` e `_pt_top_movers_data` puxam do Drive quando o
  Supabase só tem (ou não tem) a janela quente — sem duplicar o dia que as duas
  bases compartilham.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path, monkeypatch):
    """Store local isolado, plugado como o histórico frio do dashboard."""
    from utils.history.backends import LocalBackend
    from utils.history.store import HistoryStore

    s = HistoryStore(LocalBackend(tmp_path / "history"))
    monkeypatch.setattr(app, "_history_store", lambda: s)
    # Fora do runtime do Streamlit `_avisar_particoes_ilegiveis` não tem sessão;
    # o aviso não é o objeto do teste.
    monkeypatch.setattr(app, "_avisar_particoes_ilegiveis", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_avisar_uma_vez", lambda *_a, **_k: None)
    return s


def _grava(store, linhas: pd.DataFrame) -> None:
    """Grava uma amostra crua de `pricetrack_daily` no histórico frio."""
    store.write_records(
        linhas.to_dict("records"),
        dataset="pricetrack",
        run_id="teste01",
        date_column="collection_date",
    )


@pytest.fixture
def linhas() -> pd.DataFrame:
    """Amostra crua de `pricetrack_daily` em dois dias.

    Caixa alta em `marketplace`/`brand` de propósito — é como o PriceTrack
    publica, e força o filtro a ser case-insensitive.
    """
    return pd.DataFrame([
        {
            "collection_date": date(2026, 3, 10), "turno": "Diário",
            "brand": "MIDEA", "sku": "SKU-9K",
            "title": "SPLIT MIDEA 9000 BTU FRIO", "marketplace": "MERCADO LIVRE",
            "seller": "friopecas", "seller_canonical": "Frio Peças",
            "min_price": 1999.0, "avg_price": 2050.0, "mode_price": 2020.0,
            "max_price": 2100.0, "is_midea_group": True, "id": 1,
        },
        {
            "collection_date": date(2026, 3, 10), "turno": "Diário",
            "brand": "LG", "sku": "SKU-12K",
            "title": "SPLIT LG 12000 BTU INVERTER", "marketplace": "AMAZON",
            "seller": "amazonbr", "seller_canonical": "AmazonBR",
            "min_price": 2599.0, "avg_price": 2650.0, "mode_price": 2620.0,
            "max_price": 2700.0, "is_midea_group": False, "id": 2,
        },
        {
            "collection_date": date(2026, 3, 11), "turno": "Diário",
            "brand": "MIDEA", "sku": "SKU-9K",
            "title": "SPLIT MIDEA 9000 BTU FRIO", "marketplace": "MAGAZINE LUIZA",
            "seller": "magalu", "seller_canonical": "Magalu",
            "min_price": 1950.0, "avg_price": 1980.0, "mode_price": 1960.0,
            "max_price": 2010.0, "is_midea_group": True, "id": 3,
        },
    ])


# ---------------------------------------------------------------------------
# _pricetrack_raw_gap_fill — unidade
# ---------------------------------------------------------------------------
class TestRawGapFill:
    def test_le_o_periodo_no_schema_cru(self, store, linhas):
        _grava(store, linhas)
        out = app._pricetrack_raw_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set())
        assert not out.empty
        for col in ("collection_date", "sku", "brand", "marketplace",
                    "seller", "seller_canonical", "min_price", "avg_price",
                    "mode_price"):
            assert col in out.columns, col
        # Todos os turnos (não só "Diário"): as páginas leem tudo.
        assert set(out["_origem"]) == {"historico"}

    def test_todos_os_turnos_voltam(self, store):
        """Diferente do gap-fill do schema de coletas, não filtra por turno."""
        df = pd.DataFrame([
            {"collection_date": date(2026, 3, 10), "turno": "Manhã",
             "brand": "MIDEA", "sku": "S1", "title": "t", "marketplace": "AMAZON",
             "seller": "a", "min_price": 10.0, "is_midea_group": True, "id": 1},
            {"collection_date": date(2026, 3, 10), "turno": "Tarde",
             "brand": "MIDEA", "sku": "S2", "title": "t", "marketplace": "AMAZON",
             "seller": "b", "min_price": 11.0, "is_midea_group": True, "id": 2},
        ])
        _grava(store, df)
        out = app._pricetrack_raw_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set())
        assert len(out) == 2

    def test_dias_ja_presentes_nao_voltam(self, store, linhas):
        _grava(store, linhas)
        out = app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), {date(2026, 3, 10)}
        )
        assert set(out["collection_date"].unique()) == {date(2026, 3, 11)}

    def test_midea_only_pela_coluna(self, store, linhas):
        _grava(store, linhas)
        out = app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), midea_only=True
        )
        assert set(out["sku"].unique()) == {"SKU-9K"}  # só as linhas Midea

    def test_midea_only_flag_nula_com_marca_midea_e_mantida(self, store):
        """Mix na coluna: flag nula + marca Midea NÃO pode sumir do MCJV.

        Sem derivar do brand para os nulos, o Price Compliance subcontaria
        sellers e dias (achado cubic P2).
        """
        df = pd.DataFrame([
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "MIDEA", "sku": "S1", "title": "t", "marketplace": "AMAZON",
             "seller": "a", "min_price": 10.0, "is_midea_group": True, "id": 1},
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "MIDEA", "sku": "S2", "title": "t", "marketplace": "AMAZON",
             "seller": "b", "min_price": 11.0, "is_midea_group": None, "id": 2},
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "LG", "sku": "S3", "title": "t", "marketplace": "AMAZON",
             "seller": "c", "min_price": 12.0, "is_midea_group": None, "id": 3},
        ])
        _grava(store, df)
        out = app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), midea_only=True
        )
        # S1 (flag True) e S2 (flag nula + marca Midea) entram; S3 (LG) não.
        assert set(out["sku"].unique()) == {"S1", "S2"}

    def test_midea_only_flag_false_explicita_nao_e_resgatada(self, store):
        """Flag False explícita fica de fora mesmo com marca Midea — a coluna,
        quando presente e não-nula, é a autoridade."""
        df = pd.DataFrame([
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "MIDEA", "sku": "S1", "title": "t", "marketplace": "AMAZON",
             "seller": "a", "min_price": 10.0, "is_midea_group": False, "id": 1},
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "MIDEA", "sku": "S2", "title": "t", "marketplace": "AMAZON",
             "seller": "b", "min_price": 11.0, "is_midea_group": True, "id": 2},
        ])
        _grava(store, df)
        out = app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), midea_only=True
        )
        assert set(out["sku"].unique()) == {"S2"}

    def test_midea_only_derivado_da_marca_sem_coluna(self, store):
        """Partição antiga sem `is_midea_group`: deriva do brand canônico."""
        df = pd.DataFrame([
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "Midea", "sku": "S1", "title": "t", "marketplace": "AMAZON",
             "seller": "a", "min_price": 10.0, "id": 1},
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "LG", "sku": "S2", "title": "t", "marketplace": "AMAZON",
             "seller": "b", "min_price": 11.0, "id": 2},
        ])
        _grava(store, df)
        out = app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), midea_only=True
        )
        assert set(out["sku"].unique()) == {"S1"}

    def test_drop_empty_sku(self, store):
        df = pd.DataFrame([
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "MIDEA", "sku": "S1", "title": "t", "marketplace": "AMAZON",
             "seller": "a", "min_price": 10.0, "is_midea_group": True, "id": 1},
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "MIDEA", "sku": "", "title": "t", "marketplace": "AMAZON",
             "seller": "b", "min_price": 11.0, "is_midea_group": True, "id": 2},
        ])
        _grava(store, df)
        out = app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), drop_empty_sku=True
        )
        assert set(out["sku"].unique()) == {"S1"}

    def test_seller_canonical_derivado_quando_falta(self, store):
        """Sem a coluna, deriva de `seller` pelo mesmo de-para do quente."""
        df = pd.DataFrame([
            {"collection_date": date(2026, 3, 10), "turno": "Diário",
             "brand": "MIDEA", "sku": "S1", "title": "t",
             "marketplace": "MERCADO LIVRE", "seller": "friopecas",
             "min_price": 10.0, "is_midea_group": True, "id": 1},
        ])
        _grava(store, df)
        out = app._pricetrack_raw_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set())
        assert "seller_canonical" in out.columns
        # `friopecas` colapsa para o nome canônico do lojista.
        assert out["seller_canonical"].iloc[0] == app._canonical_seller("friopecas")

    def test_filtro_de_marca_e_plataforma(self, store, linhas):
        _grava(store, linhas)
        out = app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(),
            brands=["Midea"], platforms=["Mercado Livre"],
        )
        assert set(out["sku"].unique()) == {"SKU-9K"}
        assert set(out["collection_date"].unique()) == {date(2026, 3, 10)}

    def test_sku_set(self, store, linhas):
        _grava(store, linhas)
        out = app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), sku_set={"SKU-12K"}
        )
        assert set(out["sku"].unique()) == {"SKU-12K"}

    def test_limite_zero_nao_le(self, store, linhas):
        _grava(store, linhas)
        assert app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set(), limit=0
        ).empty

    def test_sem_historico_devolve_vazio(self, store):
        assert app._pricetrack_raw_gap_fill(
            date(2026, 3, 1), date(2026, 3, 31), set()
        ).empty

    def test_historico_indisponivel_degrada_sem_quebrar(self, monkeypatch):
        # A falha real de pyarrow acontece em `store.read()` (via
        # `_require_pyarrow()`), não em `_history_store()` — o mock espelha isso.
        class _StoreQuebrado:
            def read(self, *a, **k):
                raise RuntimeError("pyarrow ausente")

        monkeypatch.setattr(app, "_history_store", lambda: _StoreQuebrado())
        monkeypatch.setattr(app, "_avisar_particoes_ilegiveis", lambda *_a, **_k: None)
        monkeypatch.setattr(app, "_avisar_uma_vez", lambda *_a, **_k: None)
        out = app._pricetrack_raw_gap_fill(date(2026, 3, 1), date(2026, 3, 31), set())
        assert out.empty


# ---------------------------------------------------------------------------
# Integração — as páginas puxam do Drive quando o Supabase só tem a janela quente
# ---------------------------------------------------------------------------
class TestComplianceCosturaFrio:
    """`_query_pt_compliance` usa `date.today()` internamente, então a amostra
    precisa cair na janela de 30 dias — datas relativas a hoje."""

    @staticmethod
    def _amostra_recente() -> tuple[pd.DataFrame, date, date]:
        from datetime import timedelta
        d_velho = date.today() - timedelta(days=6)   # fora da janela quente (2d)
        d_novo = date.today() - timedelta(days=3)
        df = pd.DataFrame([
            {"collection_date": d_velho, "turno": "Diário", "brand": "MIDEA",
             "sku": "SKU-9K", "title": "SPLIT MIDEA 9000", "marketplace": "AMAZON",
             "seller": "amazonbr", "seller_canonical": "AmazonBR",
             "min_price": 1999.0, "is_midea_group": True, "id": 1},
            {"collection_date": d_velho, "turno": "Diário", "brand": "LG",
             "sku": "SKU-12K", "title": "SPLIT LG 12000", "marketplace": "AMAZON",
             "seller": "amazonbr", "seller_canonical": "AmazonBR",
             "min_price": 2599.0, "is_midea_group": False, "id": 2},
            {"collection_date": d_novo, "turno": "Diário", "brand": "MIDEA",
             "sku": "SKU-9K", "title": "SPLIT MIDEA 9000", "marketplace": "MAGAZINE LUIZA",
             "seller": "magalu", "seller_canonical": "Magalu",
             "min_price": 1950.0, "is_midea_group": True, "id": 3},
        ])
        return df, d_velho, d_novo

    def test_sem_banco_usa_o_frio(self, store, monkeypatch):
        """Requisito do usuário: janela > 2 dias vem do Parquet, não 'sem dados'."""
        df, d_velho, d_novo = self._amostra_recente()
        _grava(store, df)
        monkeypatch.setattr(app, "_get_supabase", lambda: None)
        monkeypatch.setattr(app, "st", _StStub())
        out = app._query_pt_compliance(30)
        assert not out.empty
        # Só linhas MCJV (Midea), com preço válido.
        assert set(out["sku"].unique()) == {"SKU-9K"}
        assert (out["min_price"] > 0).all()
        assert set(out["collection_date"].unique()) == {d_velho, d_novo}

    def test_costura_sem_duplicar_o_dia_quente(self, store, monkeypatch):
        """Banco entrega o dia novo; o frio completa só o velho (dedup por dia)."""
        df, d_velho, d_novo = self._amostra_recente()
        _grava(store, df)
        quente = [{
            "collection_date": d_novo.isoformat(), "sku": "SKU-9K",
            "brand": "Midea", "marketplace": "MAGAZINE LUIZA",
            "seller_canonical": "Magalu", "min_price": 1950.0,
        }]
        monkeypatch.setattr(app, "_get_supabase", lambda: _FakeClient(quente))
        monkeypatch.setattr(app, "st", _StStub())
        out = app._query_pt_compliance(30)
        # O dia novo aparece uma vez só (não duplicado pelo frio).
        assert len(out[out["collection_date"] == d_novo]) == 1
        # O dia velho (fora da janela quente) veio do frio.
        assert d_velho in set(out["collection_date"])


class TestTopMoversCosturaFrio:
    def test_sem_banco_usa_o_frio(self, store, linhas, monkeypatch):
        _grava(store, linhas)
        monkeypatch.setattr(app, "_get_supabase", lambda: None)
        monkeypatch.setattr(app, "st", _StStub())
        # Range half-open [start, end): 01/03 a 01/04 cobre 10 e 11/03.
        out = app._pt_top_movers_data(
            "2026-03-01", "2026-04-01", (), (), (), (),
        )
        assert not out.empty
        assert set(out["collection_date"].unique()) == {date(2026, 3, 10), date(2026, 3, 11)}
        # Top Movers preserva a granularidade — inclui não-Midea também.
        assert set(out["sku"].unique()) == {"SKU-9K", "SKU-12K"}

    def test_frio_respeita_o_teto(self, store, linhas, monkeypatch):
        """O combinado nunca ultrapassa `limit` (achado cubic P2)."""
        _grava(store, linhas)
        monkeypatch.setattr(app, "_get_supabase", lambda: None)
        monkeypatch.setattr(app, "st", _StStub())
        out = app._pt_top_movers_data(
            "2026-03-01", "2026-04-01", (), (), (), (), limit=1,
        )
        assert len(out) <= 1

    def test_frio_respeita_filtro_de_marca(self, store, linhas, monkeypatch):
        _grava(store, linhas)
        monkeypatch.setattr(app, "_get_supabase", lambda: None)
        monkeypatch.setattr(app, "st", _StStub())
        out = app._pt_top_movers_data(
            "2026-03-01", "2026-04-01", (), ("Midea",), (), (),
        )
        assert set(out["sku"].unique()) == {"SKU-9K"}


# ---------------------------------------------------------------------------
# Stubs mínimos
# ---------------------------------------------------------------------------
class _StStub:
    """Só absorve as chamadas de UI que as funções fazem no caminho de erro."""

    def error(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass


class _FakeExec:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Encadeamento do postgrest-py que ignora filtros e devolve `linhas` 1x.

    Suficiente para o laço de paginação das duas funções: a 1ª página traz as
    linhas (< page-size ⇒ o laço encerra), as seguintes vêm vazias.
    """

    def __init__(self, linhas):
        self._linhas = linhas
        self._served = False

    def _self(self, *a, **k):
        return self

    select = eq = gte = lte = neq = order = or_ = in_ = limit = range = _self

    def execute(self):
        if self._served:
            return _FakeExec([])
        self._served = True
        return _FakeExec(list(self._linhas))


class _FakeClient:
    def __init__(self, linhas):
        self._linhas = linhas

    def table(self, _nome):
        return _FakeQuery(self._linhas)
