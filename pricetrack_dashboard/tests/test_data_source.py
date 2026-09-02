"""Testes da fonte de dados — sonda leve (filtro de marca) e timeout."""
from __future__ import annotations

from datetime import date

from pricetrack_dashboard import data_source as ds


class _FakeClient:
    """Client dublê: registra as queries de ``count_offers`` e devolve totais."""

    def __init__(self, totals):
        self.totals = totals            # {iso_date: total}
        self.queries = []

    def count_offers(self, query):
        self.queries.append(query)
        return self.totals.get(query.collection_date.isoformat(), 0)


class TestFindLatestDate:
    def test_probe_forwards_brand_filter(self):
        """A sonda conta só as marcas do peer — nunca a coleta inteira do dia."""
        fake = _FakeClient({"2026-08-31": 12})
        found = ds.find_latest_date(
            fake, reference=date(2026, 9, 1), brands=["MIDEA", "LG"]
        )
        assert found == ("2026-08-31", 1)
        assert fake.queries[0].product_brand == ["MIDEA", "LG"]
        assert fake.queries[0].take == 1

    def test_walks_back_to_most_recent_with_data(self):
        fake = _FakeClient({"2026-08-29": 3})
        found = ds.find_latest_date(fake, reference=date(2026, 9, 1), brands=["MIDEA"])
        assert found == ("2026-08-29", 3)

    def test_none_when_no_data_in_window(self):
        fake = _FakeClient({})
        assert ds.find_latest_date(
            fake, reference=date(2026, 9, 1), max_days_back=3, brands=["MIDEA"]
        ) is None

    def test_no_brands_probes_without_filter(self):
        fake = _FakeClient({"2026-09-01": 1})
        ds.find_latest_date(fake, reference=date(2026, 9, 1), brands=None)
        assert fake.queries[0].product_brand is None


class TestReadTimeoutFloor:
    def test_floor_constant_is_generous(self):
        # A API de coleta pode responder devagar; piso ≥ 60s evita o loop de
        # retries por read timeout (o default do cliente é 30s).
        assert ds._MIN_READ_TIMEOUT_SECONDS >= 60.0


# ── Fonte Supabase ────────────────────────────────────────────────────────────

class _FakeQuery:
    """Query builder dublê: encadeia .eq/.gte/.lte/.in_/.range/.order/.limit e
    .execute() — os filtros REALMENTE recortam `self._rows` (não só registram
    a chamada), para os testes comprovarem que o filtro é aplicado, não só
    que o método foi chamado."""

    def __init__(self, rows):
        self._rows = list(rows)

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

    def in_(self, col, vals):
        vals = list(vals)
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def range(self, lo, hi):
        self._slice = (lo, hi)
        return self

    def execute(self):
        lo, hi = getattr(self, "_slice", (0, len(self._rows) - 1))
        return type("Resp", (), {"data": self._rows[lo:hi + 1]})()


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "pricetrack_daily"
        return _FakeQuery(list(self.rows))


def _daily_row(sku, title, brand, min_price, **extra):
    row = {
        "id": extra.get("id", f"{sku}-{min_price}"),
        "collection_date": "2026-08-31", "turno": "Diário",
        "brand": brand, "sku": sku, "title": title,
        "marketplace": extra.get("marketplace", "MERCADO LIVRE"),
        "seller": extra.get("seller", "seller"),
        "min_price": min_price, "avg_price": extra.get("avg_price"),
        "mode_price": extra.get("mode_price"), "max_price": extra.get("max_price"),
    }
    return row


class TestRowToOffer:
    def test_uses_min_price_as_spot(self):
        o = ds._row_to_offer(_daily_row("42EBVCA09M5", "Split Midea 9000", "MIDEA", 1700))
        assert o is not None and o.spot_price == 1700 and o.brand == "MIDEA"

    def test_falls_back_to_mode_then_avg(self):
        o = ds._row_to_offer(_daily_row("X", "t", "MIDEA", None, mode_price=1800))
        assert o.spot_price == 1800
        o2 = ds._row_to_offer(_daily_row("X", "t", "MIDEA", None, avg_price=1900))
        assert o2.spot_price == 1900

    def test_drops_implausible_prices(self):
        assert ds._row_to_offer(_daily_row("X", "t", "MIDEA", 9999999)) is None
        assert ds._row_to_offer(_daily_row("X", "t", "MIDEA", 10)) is None


class TestFetchSupabase:
    def test_maps_rows_and_finds_latest(self):
        rows = [
            _daily_row("42EBVCA09M5", "Split Midea Inverter 9000 BTU", "MIDEA", 1700),
            _daily_row("S3-Q09AAQAK", "Split LG 9000 BTU S3-Q09AAQAK", "LG", 1900),
        ]
        fake = _FakeSupabase(rows)
        res = ds.fetch_supabase(collection_date="2026-08-31", client=fake)
        assert res.collection_date == "2026-08-31"
        assert len(res.offers) == 2

    def test_requires_client(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        import pytest
        with pytest.raises(RuntimeError):
            ds.fetch_supabase(collection_date="2026-08-31")

    def test_latest_date_helper(self):
        fake = _FakeSupabase([{"collection_date": "2026-08-31", "turno": "Diário"}])
        assert ds.supabase_latest_date(fake) == "2026-08-31"


class TestFetchSupabaseRange:
    def test_groups_offers_by_collection_date(self):
        rows = [
            {**_daily_row("42EBVCA09M5", "t", "MIDEA", 1700), "collection_date": "2026-08-25"},
            {**_daily_row("42EBVCA09M5", "t", "MIDEA", 1720), "collection_date": "2026-08-25"},
            {**_daily_row("42EBVCA09M5", "t", "MIDEA", 1750), "collection_date": "2026-08-26"},
        ]
        fake = _FakeSupabase(rows)
        by_date = ds.fetch_supabase_range("2026-08-25", "2026-08-26", client=fake)
        assert set(by_date.keys()) == {"2026-08-25", "2026-08-26"}
        assert len(by_date["2026-08-25"]) == 2
        assert len(by_date["2026-08-26"]) == 1

    def test_excludes_rows_outside_the_requested_date_range(self):
        """Regressão: o fake antigo não filtrava de verdade por .gte/.lte —
        um regression que removesse esses filtros de fetch_supabase_range
        ainda passaria. Aqui a linha de fora do intervalo tem que sumir."""
        rows = [
            {**_daily_row("42EBVCA09M5", "t", "MIDEA", 1700), "collection_date": "2026-08-25"},
            {**_daily_row("42EBVCA09M5", "t", "MIDEA", 1800), "collection_date": "2026-08-10"},
            {**_daily_row("42EBVCA09M5", "t", "MIDEA", 1900), "collection_date": "2026-09-05"},
        ]
        fake = _FakeSupabase(rows)
        by_date = ds.fetch_supabase_range("2026-08-20", "2026-08-31", client=fake)
        assert set(by_date.keys()) == {"2026-08-25"}
        assert "2026-08-10" not in by_date and "2026-09-05" not in by_date

    def test_drops_offers_without_plausible_price(self):
        rows = [{**_daily_row("X", "t", "MIDEA", 9999999), "collection_date": "2026-08-25"}]
        fake = _FakeSupabase(rows)
        by_date = ds.fetch_supabase_range("2026-08-25", "2026-08-25", client=fake)
        assert by_date == {}

    def test_requires_client(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        import pytest
        with pytest.raises(RuntimeError):
            ds.fetch_supabase_range("2026-08-25", "2026-08-26")

    def test_paginates_past_a_single_page_without_truncating(self, monkeypatch):
        """Mais linhas do que 1 página, mas dentro do teto — nada truncado."""
        monkeypatch.setattr(ds, "_SUPABASE_PAGE", 2)
        monkeypatch.setattr(ds, "_SUPABASE_RANGE_MAX_ROWS", 100)
        rows = [
            {**_daily_row(f"X{i}", "t", "MIDEA", 1000 + i), "collection_date": "2026-08-25"}
            for i in range(5)
        ]
        fake = _FakeSupabase(rows)
        warnings = []
        monkeypatch.setattr(ds.logger, "warning", lambda msg: warnings.append(msg))
        by_date = ds.fetch_supabase_range("2026-08-25", "2026-08-25", client=fake)
        assert len(by_date["2026-08-25"]) == 5
        assert warnings == []

    def test_warns_when_row_cap_is_reached(self, monkeypatch):
        """Regressão do achado do cubic: teto atingido não pode ficar em
        silêncio — tem que logar um aviso, não só devolver dado incompleto."""
        monkeypatch.setattr(ds, "_SUPABASE_PAGE", 2)
        monkeypatch.setattr(ds, "_SUPABASE_RANGE_MAX_ROWS", 4)
        rows = [
            {**_daily_row(f"X{i}", "t", "MIDEA", 1000 + i), "collection_date": "2026-08-25"}
            for i in range(6)
        ]
        fake = _FakeSupabase(rows)
        warnings = []
        monkeypatch.setattr(ds.logger, "warning", lambda msg: warnings.append(msg))
        by_date = ds.fetch_supabase_range("2026-08-25", "2026-08-25", client=fake)
        assert len(by_date["2026-08-25"]) == 4   # truncado no teto, não nas 6 reais
        assert len(warnings) == 1 and "teto" in warnings[0]
