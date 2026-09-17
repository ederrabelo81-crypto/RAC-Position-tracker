"""
tests/test_pricetrack_capacity_audit.py — cobre scripts/pricetrack_capacity_audit.py

Contexto: um achado de briefing apontou "0 linhas de 9K BTU em
pricetrack_daily" partindo de uma coluna `capacidade_btu` que a tabela nunca
teve. Este script confere capacidade pelo método correto (casamento por
código de modelo do peer, `pricetrack_dashboard.peer.match_haystack`) — estes
testes garantem que a classificação e a leitura paginada continuam corretas,
sem depender de banco real.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_SPEC = importlib.util.spec_from_file_location(
    "pricetrack_capacity_audit", _ROOT / "scripts" / "pricetrack_capacity_audit.py",
)
pca = importlib.util.module_from_spec(_SPEC)
# Registrar em sys.modules ANTES de exec_module: com `from __future__ import
# annotations`, os dataclasses do módulo resolvem anotações via
# `sys.modules.get(cls.__module__)` — sem o registro isso devolve None e
# `@dataclass` explode em AttributeError na coleta do teste.
sys.modules[_SPEC.name] = pca
_SPEC.loader.exec_module(pca)

# Códigos reais do peer (pricetrack_dashboard/peer.py) — Low/9K e Low/12K
# Midea, e um concorrente 9K, para não depender de um código inventado que
# pareça arbitrário.
MIDEA_9K_SKU = "42EBVCA09M5"
MIDEA_12K_SKU = "42EBVCA12M5"
LG_9K_SKU = "S3-Q09AAQAK"
CODIGO_FORA_DO_PEER = "ZZZ999NAOEXISTE"


def _row(collection_date, sku, title, brand="MIDEA", marketplace="MERCADO LIVRE"):
    return {
        "collection_date": collection_date,
        "turno": "Diário",
        "brand": brand,
        "sku": sku,
        "title": title,
        "marketplace": marketplace,
        "seller": "seller",
        "min_price": 1999.0,
    }


class TestClassifyRows:
    def test_casa_9k_e_12k_por_codigo_de_modelo(self):
        rows = [
            _row("2026-09-11", MIDEA_9K_SKU, "Ar Condicionado Split Midea 9000 BTU"),
            _row("2026-09-11", MIDEA_12K_SKU, "Ar Condicionado Split Midea 12000 BTU"),
            _row("2026-09-11", LG_9K_SKU, "Split LG 9000 BTU", brand="LG"),
        ]
        report = pca.classify_rows(rows)
        day = report.by_date["2026-09-11"]
        assert day.by_capacity["9K"] == 2
        assert day.by_capacity["12K"] == 1
        assert day.total == 3

    def test_titulo_9000_btu_sem_codigo_de_modelo_nao_e_classificado(self):
        """O ponto central do bug original: texto livre "9000 BTU" no título
        NÃO é garantia de casamento — sem um código de modelo do peer no
        sku/title, a linha cai em não-classificado, nunca em "9K" por
        adivinhação textual."""
        rows = [_row("2026-09-11", "SKU-QUALQUER-123", "Split 9000 BTU genérico")]
        report = pca.classify_rows(rows)
        day = report.by_date["2026-09-11"]
        assert day.by_capacity.get("9K", 0) == 0
        assert day.by_capacity[pca.UNCLASSIFIED] == 1

    def test_zero_linhas_9k_no_periodo_fica_visivel_no_total(self):
        rows = [
            _row("2026-09-11", MIDEA_12K_SKU, "Split Midea 12000 BTU"),
            _row("2026-09-12", CODIGO_FORA_DO_PEER, "Peça avulsa qualquer"),
        ]
        report = pca.classify_rows(rows)
        totals = report.totals
        assert totals.get("9K", 0) == 0
        assert totals.get("12K", 0) == 1

    def test_amostras_nao_classificadas_dedupadas_e_limitadas(self):
        rows = [_row("2026-09-11", CODIGO_FORA_DO_PEER, "Peça avulsa")] * 5
        report = pca.classify_rows(rows, max_unclassified_samples=1)
        assert len(report.unclassified_samples) == 1

    def test_linha_sem_collection_date_e_ignorada_sem_quebrar(self):
        rows = [{"brand": "MIDEA", "sku": MIDEA_9K_SKU, "title": "x"}]
        report = pca.classify_rows(rows)
        assert report.by_date == {}


class TestRenderReport:
    def test_alerta_zero_quando_capacidade_ausente(self):
        rows = [_row("2026-09-11", MIDEA_12K_SKU, "Split Midea 12000 BTU")]
        report = pca.classify_rows(rows)
        report.start, report.end, report.turno = "2026-09-11", "2026-09-11", "Diário"
        text = pca.render_report(report)
        assert "ZERO linhas casadas em 9K" in text
        assert "✅ 12K:" in text

    def test_sem_flag_esconde_linhas_nao_classificadas(self):
        rows = [_row("2026-09-11", CODIGO_FORA_DO_PEER, "Peça avulsa")]
        report = pca.classify_rows(rows)
        report.start, report.end, report.turno = "2026-09-11", "2026-09-11", "Diário"
        text = pca.render_report(report, list_unclassified=False)
        assert "rode com --listar-nao-classificados" in text
        assert "Peça avulsa" not in text

    def test_com_flag_mostra_linhas_nao_classificadas(self):
        rows = [_row("2026-09-11", CODIGO_FORA_DO_PEER, "Peça avulsa rara")]
        report = pca.classify_rows(rows)
        report.start, report.end, report.turno = "2026-09-11", "2026-09-11", "Diário"
        text = pca.render_report(report, list_unclassified=True)
        assert "Peça avulsa rara" in text


# ── fetch_rows: leitura paginada sem filtro de marca ────────────────────────

class _FakeQuery:
    """Query builder dublê: os filtros recortam `self._rows` de verdade."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._slice = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def gte(self, col, val):
        self._rows = [r for r in self._rows if (r.get(col) or "") >= val]
        return self

    def lte(self, col, val):
        self._rows = [r for r in self._rows if (r.get(col) or "") <= val]
        return self

    def range(self, lo, hi):
        self._slice = (lo, hi)
        return self

    def execute(self):
        lo, hi = self._slice or (0, len(self._rows) - 1)
        return type("Resp", (), {"data": self._rows[lo:hi + 1]})()


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "pricetrack_daily"
        return _FakeQuery(list(self.rows))


class TestFetchRows:
    def test_nao_filtra_por_marca(self):
        """Regressão do bug original: um filtro de marca escondia SKU 9K de
        marca fora do peer atrás de "zero resultado". `fetch_rows` não deve
        aplicar `.in_("brand", ...)` nenhum."""
        rows = [
            _row("2026-09-11", MIDEA_9K_SKU, "t", brand="MIDEA"),
            _row("2026-09-11", "OUTRACOISA123456", "t", brand="MARCA_FORA_DO_PEER"),
        ]
        fake = _FakeSupabase(rows)
        out = pca.fetch_rows("2026-09-11", "2026-09-11", client=fake)
        assert len(out) == 2

    def test_filtra_por_intervalo_de_datas_e_turno(self):
        rows = [
            {**_row("2026-09-10", MIDEA_9K_SKU, "t"), "turno": "Diário"},
            {**_row("2026-09-11", MIDEA_9K_SKU, "t"), "turno": "Diário"},
            {**_row("2026-09-11", MIDEA_9K_SKU, "t"), "turno": "Manhã"},
            {**_row("2026-09-16", MIDEA_9K_SKU, "t"), "turno": "Diário"},
            {**_row("2026-09-17", MIDEA_9K_SKU, "t"), "turno": "Diário"},
        ]
        fake = _FakeSupabase(rows)
        out = pca.fetch_rows("2026-09-11", "2026-09-16", turno="Diário", client=fake)
        assert len(out) == 2
        assert all(r["turno"] == "Diário" for r in out)

    def test_sem_client_e_sem_backend_levanta_erro_claro(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        monkeypatch.delenv("RAC_DB_DSN", raising=False)
        monkeypatch.delenv("RAC_DB_BACKEND", raising=False)
        import pytest
        with pytest.raises(RuntimeError):
            pca.fetch_rows("2026-09-11", "2026-09-16")
