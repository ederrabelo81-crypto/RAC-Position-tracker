"""
Fluxo assíncrono do export em massa, com estados mockados:
pending → processing → DONE (download) | FAILED | timeout, limite de 3
exports concorrentes e renovação da downloadUrl expirada (TTL 1h).
"""
import gzip
import json

import pytest

from pricetrack_api.client import PriceTrackClient
from pricetrack_api.exceptions import ExportFailedError, ExportTimeoutError
from pricetrack_api.exports import (
    OUTCOME_NO_DATA,
    OUTCOME_OK,
    ExportManager,
)
from pricetrack_api.http import HttpTransport
from pricetrack_api.models import ExportRequest

from .conftest import FakeClock, FakeResponse, FakeSession, offer_payload

NDJSON_BODY = gzip.compress(
    ("\n".join(json.dumps(offer_payload(oid=f"of-{i}")) for i in range(3)) + "\n")
    .encode("utf-8")
)


def _status(export_id, status, progress=0, with_url=True, url_suffix=""):
    payload = {
        "exportId": export_id, "status": status, "progress": progress,
        "statusUrl": f"/exports-external/{export_id}",
    }
    if status == "DONE":
        payload.update({
            "format": "ndjson.gz", "rowCount": 3, "fileSizeBytes": len(NDJSON_BODY),
            "progress": 100,
        })
        if with_url:
            payload["downloadUrl"] = f"https://s3.example/{export_id}{url_suffix}"
    return payload


def _manager(settings, session, clock: FakeClock, **kwargs) -> ExportManager:
    transport = HttpTransport(settings, session=session,
                              sleep_fn=clock.sleep, rng=lambda: 1.0)
    client = PriceTrackClient(settings, transport=transport, clock=clock)
    return ExportManager(client, sleep_fn=clock.sleep, clock=clock, **kwargs)


class TestExportLifecycle:
    def test_pending_processing_done_download(self, settings, clock, tmp_path):
        dest = tmp_path / "out.ndjson.gz"
        session = FakeSession(responses=[
            # POST cria
            FakeResponse(json_data=_status("exp-1", "pending")),
            # polls
            FakeResponse(json_data=_status("exp-1", "pending", 0)),
            FakeResponse(json_data=_status("exp-1", "processing", 40)),
            FakeResponse(json_data=_status("exp-1", "DONE")),
            # download da URL pré-assinada
            FakeResponse(content=NDJSON_BODY),
        ])
        manager = _manager(settings, session, clock)

        outcome = manager.run(ExportRequest("2026-07-01"), dest=dest)

        assert outcome.ok
        assert outcome.path == dest
        assert dest.exists()
        with gzip.open(dest, "rt") as fh:
            assert sum(1 for _ in fh) == 3
        assert outcome.job.row_count == 3
        assert outcome.duration_seconds > 0
        # POST + 3 GETs de status + 1 GET de download
        methods = [(c.method, c.url) for c in session.calls]
        assert methods[0][0] == "POST"
        assert methods[0][1].endswith("/exports-external/collects-offers")
        assert all(u.endswith("/exports-external/exp-1") for _, u in methods[1:4])
        # download não leva header de autenticação (URL pré-assinada)
        assert session.calls[-1].headers is None

    def test_failed_levanta_export_failed(self, settings, clock, tmp_path):
        session = FakeSession(responses=[
            FakeResponse(json_data=_status("exp-2", "pending")),
            FakeResponse(json_data=_status("exp-2", "processing", 10)),
            FakeResponse(json_data=_status("exp-2", "FAILED")),
        ])
        manager = _manager(settings, session, clock)
        with pytest.raises(ExportFailedError):
            manager.run(ExportRequest("2026-07-01"), dest=tmp_path / "x.gz")

    def test_timeout_de_polling(self, settings, clock, tmp_path):
        settings.poll_timeout_seconds = 5.0
        settings.poll_interval_seconds = 2.0
        session = FakeSession(handler=lambda call: FakeResponse(
            json_data=_status("exp-3", "pending")
            if call.method == "GET" else _status("exp-3", "pending"),
        ))
        manager = _manager(settings, session, clock)
        with pytest.raises(ExportTimeoutError):
            manager.run(ExportRequest("2026-07-01"), dest=tmp_path / "x.gz")

    def test_409_no_create_vira_no_data(self, settings, clock, tmp_path):
        session = FakeSession(responses=[
            FakeResponse(status_code=409, json_data={"message": "no collect"}),
        ])
        manager = _manager(settings, session, clock)
        outcomes = manager.run_many([ExportRequest("2026-07-01")])
        assert outcomes[0].status == OUTCOME_NO_DATA


class TestDefaultDest:
    def test_filtros_diferentes_nao_sobrescrevem_arquivo(self, settings, clock):
        manager = _manager(settings, FakeSession(responses=[]), clock)
        plain = manager._default_dest(ExportRequest("2026-07-01"))
        by_mp = manager._default_dest(
            ExportRequest("2026-07-01", marketplaces=["AMAZON"]))
        by_other_mp = manager._default_dest(
            ExportRequest("2026-07-01", marketplaces=["SHOPEE"]))
        # sem filtro mantém o nome limpo (compat com cache existente)
        assert plain.name == "offers-2026-07-01.ndjson.gz"
        assert len({plain, by_mp, by_other_mp}) == 3


class TestDownloadUrlRenewal:
    def test_url_expirada_no_download_renova_via_status(self, settings, clock,
                                                        tmp_path):
        dest = tmp_path / "renewed.ndjson.gz"
        session = FakeSession(responses=[
            FakeResponse(json_data=_status("exp-4", "pending")),
            FakeResponse(json_data=_status("exp-4", "DONE", url_suffix="-old")),
            # download 1: URL pré-assinada expirou no meio do caminho (403)
            FakeResponse(status_code=403, content=b"AccessDenied"),
            # renovação: novo GET de status traz URL fresca
            FakeResponse(json_data=_status("exp-4", "DONE", url_suffix="-fresh")),
            # download 2 funciona
            FakeResponse(content=NDJSON_BODY),
        ])
        manager = _manager(settings, session, clock)
        outcome = manager.run(ExportRequest("2026-07-01"), dest=dest)
        assert outcome.ok and dest.exists()
        urls = [c.url for c in session.calls if c.stream]
        assert urls == ["https://s3.example/exp-4-old",
                        "https://s3.example/exp-4-fresh"]

    def test_snapshot_velho_renova_proativamente(self, settings, clock, tmp_path):
        # Snapshot DONE obtido há mais que o TTL → renova ANTES de baixar.
        dest = tmp_path / "proactive.ndjson.gz"
        transport = HttpTransport(settings, session=FakeSession(responses=[
            FakeResponse(json_data=_status("exp-5", "DONE", url_suffix="-fresh")),
            FakeResponse(content=NDJSON_BODY),
        ]), sleep_fn=clock.sleep, rng=lambda: 1.0)
        client = PriceTrackClient(settings, transport=transport, clock=clock)

        from pricetrack_api.models import ExportJob
        stale_job = ExportJob.from_api(
            _status("exp-5", "DONE", url_suffix="-old"),
            fetched_at=clock() - settings.download_url_ttl_seconds - 1,
        )
        client.download_export(stale_job, dest)
        assert dest.exists()


class TestConcurrencyLimit:
    def test_no_maximo_3_exports_em_voo(self, settings, clock, tmp_path):
        """5 datas: nunca mais que 3 jobs pending/processing simultâneos."""
        state = {"active": {}, "created": 0, "max_active": 0}

        def handler(call):
            if call.method == "POST":
                state["created"] += 1
                eid = f"exp-{state['created']}"
                state["active"][eid] = 0
                state["max_active"] = max(state["max_active"], len(state["active"]))
                return FakeResponse(json_data=_status(eid, "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            eid = call.url.rsplit("/", 1)[-1]
            state["active"][eid] += 1
            if state["active"][eid] >= 2:          # DONE no 2º poll
                state["active"].pop(eid)
                return FakeResponse(json_data=_status(eid, "DONE"))
            return FakeResponse(json_data=_status(eid, "processing", 50))

        manager = _manager(settings, FakeSession(handler=handler), clock)
        requests_ = [ExportRequest(f"2026-07-0{d}") for d in range(1, 6)]
        outcomes = manager.run_many(
            requests_, dest_fn=lambda r: tmp_path / f"{r.collection_date}.gz"
        )

        assert [o.status for o in outcomes] == [OUTCOME_OK] * 5
        assert state["created"] == 5
        assert state["max_active"] <= 3            # limite da API respeitado
        # resultados na ordem dos requests
        assert [o.request.collection_date.isoformat() for o in outcomes] == [
            "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05",
        ]

    def test_429_espera_slot_e_completa(self, settings, clock, tmp_path):
        """1º create leva 429 (slots de terceiros); o retry seguinte entra."""
        state = {"creates": 0, "polls": 0}

        def handler(call):
            if call.method == "POST":
                state["creates"] += 1
                if state["creates"] == 1:
                    return FakeResponse(status_code=429, headers={"Retry-After": "7"},
                                        json_data={"message": "limit"})
                return FakeResponse(json_data=_status("exp-9", "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            state["polls"] += 1
            status = "DONE" if state["polls"] >= 2 else "processing"
            return FakeResponse(json_data=_status("exp-9", status))

        manager = _manager(settings, FakeSession(handler=handler), clock)
        outcome = manager.run(ExportRequest("2026-07-01"),
                              dest=tmp_path / "w.gz")
        assert outcome.ok
        assert state["creates"] == 2
        assert 7.0 in clock.sleeps                 # honrou o Retry-After

    def test_respeita_max_concurrent_customizado(self, settings, clock, tmp_path):
        state = {"active": {}, "max_active": 0, "created": 0}

        def handler(call):
            if call.method == "POST":
                state["created"] += 1
                eid = f"exp-{state['created']}"
                state["active"][eid] = True
                state["max_active"] = max(state["max_active"], len(state["active"]))
                return FakeResponse(json_data=_status(eid, "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            eid = call.url.rsplit("/", 1)[-1]
            state["active"].pop(eid, None)
            return FakeResponse(json_data=_status(eid, "DONE"))

        manager = _manager(settings, FakeSession(handler=handler), clock,
                           max_concurrent=1)
        outcomes = manager.run_many(
            [ExportRequest("2026-07-01"), ExportRequest("2026-07-02")],
            dest_fn=lambda r: tmp_path / f"{r.collection_date}.gz",
        )
        assert all(o.ok for o in outcomes)
        assert state["max_active"] == 1
