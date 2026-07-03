"""SmartCollector: threshold paginado × export, 409, filtros e métricas."""
import gzip
import json

import pytest

from pricetrack_api.client import PriceTrackClient
from pricetrack_api.collector import (
    STRATEGY_EXPORT,
    STRATEGY_PAGINATED,
    SmartCollector,
)
from pricetrack_api.http import HttpTransport
from pricetrack_api.models import CollectQuery
from pricetrack_api.store import NdjsonStore

from .conftest import (
    FakeClock,
    FakeResponse,
    FakeSession,
    offer_payload,
    paged_payload,
)


def _collector(settings, session, tmp_path, clock=None):
    clock = clock or FakeClock()
    transport = HttpTransport(settings, session=session,
                              sleep_fn=clock.sleep, rng=lambda: 1.0)
    client = PriceTrackClient(settings, transport=transport, clock=clock)
    store = NdjsonStore(tmp_path / "partitions")
    collector = SmartCollector(client, store=store,
                               sleep_fn=clock.sleep, clock=clock)
    return collector, store


def _export_handler(total_offers: int):
    """Handler que atende sonda, export assíncrono e download."""
    ndjson = "\n".join(
        json.dumps(offer_payload(
            oid=f"of-{i}",
            brand="MIDEA" if i % 2 == 0 else "GREE",
            marketplace="AMAZON" if i % 3 == 0 else "MERCADO LIVRE",
        ))
        for i in range(total_offers)
    ) + "\n"
    body = gzip.compress(ndjson.encode("utf-8"))
    state = {"polls": 0}

    def handler(call):
        if call.method == "GET" and "/collects-offers-external" in call.url:
            # sonda take=1: pageCount == total de linhas
            return FakeResponse(json_data=paged_payload(
                [offer_payload()], page=1, take=1, total=total_offers))
        if call.method == "POST":
            return FakeResponse(json_data={
                "exportId": "exp-1", "status": "pending",
                "statusUrl": "/exports-external/exp-1"})
        if call.stream:
            return FakeResponse(content=body)
        state["polls"] += 1
        if state["polls"] >= 2:
            return FakeResponse(json_data={
                "exportId": "exp-1", "status": "DONE", "progress": 100,
                "downloadUrl": "https://s3.example/exp-1",
                "format": "ndjson.gz", "rowCount": total_offers,
                "fileSizeBytes": len(body)})
        return FakeResponse(json_data={
            "exportId": "exp-1", "status": "processing", "progress": 50})

    return handler


class TestEstrategiaAuto:
    def test_volume_pequeno_usa_paginado(self, settings, tmp_path):
        # threshold=100 (fixture); 3 ofertas → paginado, nenhum POST de export
        offers = [offer_payload(oid=f"of-{i}") for i in range(3)]
        session = FakeSession(responses=[
            # sonda take=1
            FakeResponse(json_data=paged_payload(
                [offers[0]], page=1, take=1, total=3)),
            # páginas take=2 (settings.page_take)
            FakeResponse(json_data=paged_payload(offers[:2], page=1, take=2, total=3)),
            FakeResponse(json_data=paged_payload(offers[2:], page=2, take=2, total=3)),
        ])
        collector, store = _collector(settings, session, tmp_path)

        result = collector.collect_offers("2026-07-01")

        assert result.metrics.strategy == STRATEGY_PAGINATED
        assert result.metrics.status == "SUCCESS"
        assert result.metrics.rows_fetched == 3
        assert result.metrics.pages_fetched == 2
        assert result.total_available == 3
        assert store.count("offers", "2026-07-01") == 3
        assert not any(c.method == "POST" for c in session.calls)

    def test_volume_grande_usa_export(self, settings, tmp_path):
        settings.export_threshold_rows = 5
        session = FakeSession(handler=_export_handler(total_offers=8))
        collector, store = _collector(settings, session, tmp_path)

        result = collector.collect_offers("2026-07-01")

        assert result.metrics.strategy == STRATEGY_EXPORT
        assert result.metrics.rows_fetched == 8
        assert result.metrics.export_duration_seconds is not None
        assert result.metrics.export_row_count == 8
        assert store.count("offers", "2026-07-01") == 8
        assert any(c.method == "POST" for c in session.calls)

    def test_409_na_sonda_vira_no_data(self, settings, tmp_path):
        session = FakeSession(responses=[
            FakeResponse(status_code=409, json_data={"message": "no table"}),
        ])
        collector, store = _collector(settings, session, tmp_path)
        result = collector.collect_offers("2026-07-01")
        assert result.metrics.status == "NO_DATA"
        assert store.count("offers", "2026-07-01") == 0

    def test_estrategia_forcada_ignora_threshold(self, settings, tmp_path):
        settings.export_threshold_rows = 1_000_000
        session = FakeSession(handler=_export_handler(total_offers=4))
        collector, _ = _collector(settings, session, tmp_path)
        result = collector.collect_offers("2026-07-01",
                                          strategy=STRATEGY_EXPORT)
        assert result.metrics.strategy == STRATEGY_EXPORT


class TestFiltrosClientSideNoExport:
    def test_filtro_de_marca_aplicado_pos_download(self, settings, tmp_path):
        settings.export_threshold_rows = 5
        session = FakeSession(handler=_export_handler(total_offers=8))
        collector, store = _collector(settings, session, tmp_path)

        query = CollectQuery("2026-07-01", product_brand=["MIDEA"])
        result = collector.collect_offers("2026-07-01", query=query)

        # of-0,2,4,6 são MIDEA (i par) → 4 mantidas, 4 filtradas
        assert result.metrics.rows_fetched == 4
        assert result.metrics.rows_filtered_out == 4
        assert store.count("offers", "2026-07-01") == 4
        stored_brands = {r["brand"] for r in store.read("offers", "2026-07-01")}
        assert stored_brands == {"MIDEA"}

    def test_marketplace_tem_pushdown_no_post(self, settings, tmp_path):
        settings.export_threshold_rows = 5
        session = FakeSession(handler=_export_handler(total_offers=8))
        collector, _ = _collector(settings, session, tmp_path)

        query = CollectQuery("2026-07-01", marketplace=["AMAZON"])
        collector.collect_offers("2026-07-01", query=query)

        post = next(c for c in session.calls if c.method == "POST")
        assert post.json["marketplaces"] == ["AMAZON"]


class TestMetricasECobertura:
    def test_cobertura_por_marketplace_e_marca(self, settings, tmp_path):
        offers = [
            offer_payload(oid="a", marketplace="AMAZON", brand="MIDEA"),
            offer_payload(oid="b", marketplace="AMAZON", brand="GREE"),
            offer_payload(oid="c", marketplace="MAGALU", brand="MIDEA"),
        ]
        session = FakeSession(responses=[
            FakeResponse(json_data=paged_payload([offers[0]], page=1, take=1, total=3)),
            FakeResponse(json_data=paged_payload(offers[:2], page=1, take=2, total=3)),
            FakeResponse(json_data=paged_payload(offers[2:], page=2, take=2, total=3)),
        ])
        collector, _ = _collector(settings, session, tmp_path)
        result = collector.collect_offers("2026-07-01")

        metrics = result.metrics.to_dict()
        assert metrics["coverage_marketplaces"] == {"AMAZON": 2, "MAGALU": 1}
        assert metrics["coverage_brands"] == {"MIDEA": 2, "GREE": 1}
        assert result.upsert.new == 3

    def test_recoleta_do_mesmo_dia_e_idempotente(self, settings, tmp_path):
        def responses():
            offers = [offer_payload(oid=f"of-{i}") for i in range(2)]
            return [
                FakeResponse(json_data=paged_payload(
                    [offers[0]], page=1, take=1, total=2)),
                FakeResponse(json_data=paged_payload(
                    offers, page=1, take=2, total=2)),
            ]
        session = FakeSession(responses=responses() + responses())
        collector, store = _collector(settings, session, tmp_path)

        first = collector.collect_offers("2026-07-01")
        second = collector.collect_offers("2026-07-01")

        assert first.upsert.new == 2
        assert second.upsert.new == 0
        assert second.upsert.updated == 2
        assert store.count("offers", "2026-07-01") == 2

    def test_falha_gera_alerta(self, settings, tmp_path):
        alerts = []

        class SpyAlert:
            def send(self, subject, message):
                alerts.append((subject, message))
                return True

        session = FakeSession(responses=[
            FakeResponse(status_code=500, json_data={})
            for _ in range(settings.max_retries + 1)
        ])
        clock = FakeClock()
        transport = HttpTransport(settings, session=session,
                                  sleep_fn=clock.sleep, rng=lambda: 1.0)
        client = PriceTrackClient(settings, transport=transport, clock=clock)
        collector = SmartCollector(client, store=NdjsonStore(tmp_path / "p"),
                                   alert_sink=SpyAlert())

        from pricetrack_api.exceptions import PriceTrackServerError
        with pytest.raises(PriceTrackServerError):
            collector.collect_offers("2026-07-01")
        assert len(alerts) == 1
        assert "FALHOU" in alerts[0][0]
