"""
tests/test_pricetrack_dual_write.py — Escrita dupla do import do PriceTrack.

Desde Ago/2026 o import diário grava o dia no histórico frio **antes** e
independentemente do Supabase, como a coleta já fazia para `coletas`. O que
estes testes protegem:

* a gravação acontece mesmo com o banco fora — é o motivo de ela existir;
* ela usa `collection_date` como coluna de partição (errar isso descarta
  100% das linhas em silêncio, porque o store não acha o dia);
* reimportar a mesma data **sobrescreve** em vez de duplicar (o import
  intra-dia roda de hora em hora sobre o mesmo dia);
* nenhuma falha do histórico derruba o import.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("pyarrow", reason="histórico em Parquet exige pyarrow")

from scripts import pricetrack_api_import as pti  # noqa: E402
from utils.history import write_records  # noqa: E402
from utils.history.backends import LocalBackend  # noqa: E402
from utils.history.store import HistoryStore  # noqa: E402


def _registros(dia: str = "2026-08-07", n: int = 3) -> list[dict]:
    """Linhas no schema de `pricetrack_daily`, como o import as monta."""
    return [
        {
            "collection_date": dia,
            "turno": "Diário",
            "brand": "MIDEA",
            "sku": f"SKU-{i}",
            "title": f"SPLIT MIDEA {9 + i}000 BTU",
            "marketplace": "MERCADO LIVRE",
            "seller": "Loja Midea",
            "seller_canonical": "loja midea",
            "min_price": 1900.0 + i,
            "avg_price": 1950.0 + i,
            "mode_price": 1920.0 + i,
            "max_price": 2000.0 + i,
            "spread_pct": 5.0,
            "is_midea_group": True,
            "source_file": f"api-{dia}",
        }
        for i in range(n)
    ]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Store local isolado, injetado no lugar do resolvido por ambiente."""
    s = HistoryStore(LocalBackend(tmp_path / "history"))
    import utils.history as hist

    monkeypatch.setattr(hist, "get_store", lambda name=None: s)
    monkeypatch.delenv("RAC_HISTORY", raising=False)
    return s


class TestWriteHistory:
    """O contrato do `write_history` do import."""

    def test_grava_a_particao_do_dia(self, store):
        keys = pti.write_history(_registros(), "2026-08-07")
        assert len(keys) == 1
        assert "pricetrack/data=2026-08-07" in keys[0]

    def test_linhas_voltam_na_leitura(self, store):
        pti.write_history(_registros(n=3), "2026-08-07")
        df = store.read("pricetrack", start=date(2026, 8, 7), end=date(2026, 8, 7))
        assert len(df) == 3
        assert set(df["sku"]) == {"SKU-0", "SKU-1", "SKU-2"}

    def test_particiona_por_collection_date(self, store):
        """Com a coluna errada o store não acha o dia e descarta tudo."""
        registros = _registros(dia="2026-08-05", n=2) + _registros(dia="2026-08-06", n=1)
        keys = pti.write_history(registros, "2026-08-06")
        assert len(keys) == 2, "esperava uma partição por dia distinto"
        assert len(store.read(
            "pricetrack", start=date(2026, 8, 5), end=date(2026, 8, 5)
        )) == 2

    def test_reimportar_sobrescreve_em_vez_de_duplicar(self, store):
        """O intra-dia reimporta o mesmo dia de hora em hora."""
        pti.write_history(_registros(n=3), "2026-08-07")
        pti.write_history(_registros(n=3), "2026-08-07")
        df = store.read("pricetrack", start=date(2026, 8, 7), end=date(2026, 8, 7))
        assert len(df) == 3, "o dia foi duplicado — run_id deixou de ser estável"

    def test_dry_run_nao_grava(self, store):
        assert pti.write_history(_registros(), "2026-08-07", dry_run=True) == []
        assert store.read(
            "pricetrack", start=date(2026, 8, 7), end=date(2026, 8, 7)
        ).empty

    def test_sem_registros_nao_grava(self, store):
        assert pti.write_history([], "2026-08-07") == []

    def test_desligavel_por_env(self, store, monkeypatch):
        monkeypatch.setenv("RAC_HISTORY", "off")
        assert pti.write_history(_registros(), "2026-08-07") == []

    def test_falha_do_historico_nao_derruba_o_import(self, monkeypatch):
        """O Supabase ainda pode receber o dia — o import não pode morrer."""
        import utils.history as hist

        def _explode(*_a, **_kw):
            raise RuntimeError("Drive fora do ar")

        monkeypatch.setattr(hist, "write_records", _explode)
        assert pti.write_history(_registros(), "2026-08-07") == []


class TestIndependenciaDoSupabase:
    """A razão de existir da escrita dupla: o banco pode estar fora."""

    def test_grava_mesmo_com_o_supabase_recusando(self, store, monkeypatch):
        chamou = {"insert": False}

        def _insert_falha(records, dry_run=False):
            chamou["insert"] = True
            raise RuntimeError("HTTP 402 exceed_db_size_quota")

        monkeypatch.setattr(pti, "insert_rows", _insert_falha)

        registros = _registros(n=2)
        pti.write_history(registros, "2026-08-07")
        with pytest.raises(RuntimeError):
            pti.insert_rows(registros)

        assert chamou["insert"], "o teste precisa exercitar o caminho do banco"
        df = store.read("pricetrack", start=date(2026, 8, 7), end=date(2026, 8, 7))
        assert len(df) == 2, "o dia tem de sobreviver ao 402"


class TestDateColumnNoWriteRecords:
    """`write_records` precisa repassar `date_column` ao store."""

    def test_default_continua_sendo_data(self, tmp_path):
        s = HistoryStore(LocalBackend(tmp_path / "h"))
        keys = write_records(
            [{"data": "2026-08-07", "plataforma": "Amazon"}],
            run_id="r1", store=s, already_mapped=True,
        )
        assert len(keys) == 1

    def test_collection_date_e_respeitado(self, tmp_path):
        s = HistoryStore(LocalBackend(tmp_path / "h"))
        keys = write_records(
            _registros(n=2),
            run_id="r1", dataset="pricetrack", store=s,
            already_mapped=True, date_column="collection_date",
        )
        assert len(keys) == 1
        assert "pricetrack/data=2026-08-07" in keys[0]

    def test_coluna_errada_nao_grava_nada(self, tmp_path):
        """Documenta o modo de falha que o parâmetro existe para evitar."""
        s = HistoryStore(LocalBackend(tmp_path / "h"))
        assert write_records(
            _registros(n=2),
            run_id="r1", dataset="pricetrack", store=s, already_mapped=True,
        ) == []
