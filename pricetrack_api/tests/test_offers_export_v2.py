"""
Export V2 de ofertas (POST /exports-external/collects-offers-v2, via Redis).

Cobre o roteamento V2 × V1: V2 por padrão dentro da retenção de 90 dias
(contando hoje), V1 para data mais antiga, V1 quando configurado, queda para
a V1 no 404, `proofFilter=ALL` explícito (o padrão WITH_PROOF da API descarta
oferta sem print) e a chave do diário inalterada pela V2.
"""
from datetime import date

import pytest

from pricetrack_api.client import (
    EXPORT_OFFERS_PATH,
    EXPORT_OFFERS_V2_PATH,
    PriceTrackClient,
)
from pricetrack_api.config import PriceTrackSettings
from pricetrack_api.exceptions import PriceTrackConfigError, PriceTrackServerError
from pricetrack_api.http import HttpTransport
from pricetrack_api.journal import journal_key
from pricetrack_api.models import ExportRequest, Offer

from .conftest import FakeResponse, FakeSession, offer_payload

HOJE = date(2026, 10, 16)
_CRIADO = {"exportId": "exp-1", "status": "pending",
           "statusUrl": "/exports-external/exp-1"}


def _client(settings, session, clock):
    transport = HttpTransport(settings, session=session,
                              sleep_fn=clock.sleep, rng=lambda: 1.0)
    return PriceTrackClient(settings, transport=transport, clock=clock,
                            today=lambda: HOJE)


class TestRoteamento:
    def test_data_recente_vai_para_v2_com_proof_all(self, settings, clock):
        session = FakeSession(responses=[FakeResponse(json_data=_CRIADO)])
        _client(settings, session, clock).create_offers_export(
            ExportRequest("2026-10-15"))
        call = session.calls[0]
        assert call.url.endswith(EXPORT_OFFERS_V2_PATH)
        assert call.json == {"collectionDate": "2026-10-15", "proofFilter": "ALL"}

    def test_limite_da_retencao_conta_hoje(self, settings, clock):
        client = _client(settings, FakeSession(responses=[]), clock)
        # 90 dias contando hoje: 16/10 é o 1º, 19/07 é o 90º.
        assert client.offers_v2_window_start() == date(2026, 7, 19)

    @pytest.mark.parametrize("dia,path", [
        ("2026-07-19", EXPORT_OFFERS_V2_PATH),
        ("2026-07-18", EXPORT_OFFERS_PATH),
    ])
    def test_fora_da_retencao_vai_para_v1(self, settings, clock, dia, path):
        session = FakeSession(responses=[FakeResponse(json_data=_CRIADO)])
        _client(settings, session, clock).create_offers_export(ExportRequest(dia))
        assert session.calls[0].url.endswith(path)

    def test_v1_nao_recebe_proof_filter(self, settings, clock):
        settings.offers_export_version = "v1"
        session = FakeSession(responses=[FakeResponse(json_data=_CRIADO)])
        _client(settings, session, clock).create_offers_export(
            ExportRequest("2026-10-15"))
        assert session.calls[0].url.endswith(EXPORT_OFFERS_PATH)
        assert "proofFilter" not in session.calls[0].json

    def test_proof_filter_do_chamador_e_respeitado(self, settings, clock):
        session = FakeSession(responses=[FakeResponse(json_data=_CRIADO)])
        _client(settings, session, clock).create_offers_export(
            ExportRequest("2026-10-15", proof_filter="with_proof",
                          skus=["42AFVCI12S5"]))
        assert session.calls[0].json["proofFilter"] == "WITH_PROOF"
        assert session.calls[0].json["skus"] == ["42AFVCI12S5"]

    def test_404_na_v2_cai_para_v1_e_lembra(self, settings, clock):
        session = FakeSession(responses=[
            FakeResponse(status_code=404, json_data={"message": "Not Found"}),
            FakeResponse(json_data=_CRIADO),
            FakeResponse(json_data={**_CRIADO, "exportId": "exp-2"}),
        ])
        client = _client(settings, session, clock)
        assert client.create_offers_export(ExportRequest("2026-10-15")).export_id == "exp-1"
        client.create_offers_export(ExportRequest("2026-10-14"))
        urls = [c.url for c in session.calls]
        assert urls[0].endswith(EXPORT_OFFERS_V2_PATH)
        assert urls[1].endswith(EXPORT_OFFERS_PATH)
        # Segundo export já vai direto à V1, sem repetir o 404.
        assert urls[2].endswith(EXPORT_OFFERS_PATH)

    def test_outros_erros_da_v2_nao_mascaram(self, settings, clock):
        settings.max_retries = 1
        session = FakeSession(handler=lambda call: FakeResponse(
            status_code=500, json_data={"message": "boom"}))
        with pytest.raises(PriceTrackServerError):
            _client(settings, session, clock).create_offers_export(
                ExportRequest("2026-10-15"))
        assert all(c.url.endswith(EXPORT_OFFERS_V2_PATH) for c in session.calls)


class TestContrato:
    def test_chave_do_diario_nao_muda_com_a_v2(self):
        # Um export criado pela V1 antes do deploy continua adotável.
        req = ExportRequest("2026-10-15")
        assert journal_key("offers", req) == journal_key(
            "offers", ExportRequest(date(2026, 10, 15)))
        assert "proofFilter" not in req.to_body()

    def test_skus_acima_do_teto(self):
        with pytest.raises(ValueError):
            ExportRequest("2026-10-15", skus=[f"S{i}" for i in range(1001)])

    def test_proof_filter_invalido(self):
        with pytest.raises(ValueError):
            ExportRequest("2026-10-15", proof_filter="TODOS")

    def test_versao_invalida_na_config(self, tmp_path):
        with pytest.raises(PriceTrackConfigError):
            PriceTrackSettings(api_key="k", offers_export_version="v3",
                               data_dir=tmp_path)

    def test_versao_por_env(self, tmp_path):
        s = PriceTrackSettings.from_env(env={
            "PRICETRACK_API_KEY": "k",
            "PRICETRACK_OFFERS_EXPORT_VERSION": "V1",
        })
        assert s.offers_export_version == "v1"

    def test_offer_v2_recupera_hora_da_execucao(self):
        raw = offer_payload(collectionHour=None, title=None,
                            collectionHourExecution="14:37")
        assert Offer.from_api(raw).collection_hour == 14
