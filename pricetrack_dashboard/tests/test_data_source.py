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
