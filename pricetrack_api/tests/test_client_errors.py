"""Tratamento explícito de 400/401/409/429 e retry com backoff exponencial."""
import pytest
import requests

from pricetrack_api.client import PriceTrackClient
from pricetrack_api.exceptions import (
    PriceTrackAuthError,
    PriceTrackBadRequestError,
    PriceTrackExportLimitError,
    PriceTrackNetworkError,
    PriceTrackNoCollectionError,
    PriceTrackServerError,
)
from pricetrack_api.http import HttpTransport
from pricetrack_api.models import CollectQuery, ExportRequest

from .conftest import FakeResponse, FakeSession, offer_payload, paged_payload


def _client(settings, session, sleeps=None) -> PriceTrackClient:
    transport = HttpTransport(
        settings, session=session,
        sleep_fn=(sleeps.append if sleeps is not None else lambda s: None),
        rng=lambda: 1.0,  # jitter determinístico = fator 1.0
    )
    return PriceTrackClient(settings, transport=transport)


class TestStatusCodes:
    def test_401_vira_auth_error_sem_retry(self, settings):
        session = FakeSession(responses=[
            FakeResponse(status_code=401, json_data={"message": "unauthorized"}),
        ])
        client = _client(settings, session)
        with pytest.raises(PriceTrackAuthError) as err:
            client.offers_page(CollectQuery("2026-07-01"))
        assert len(session.calls) == 1                  # determinístico: 1 tentativa
        assert "PRICETRACK_API_KEY" in str(err.value)
        assert "test-key" not in str(err.value)         # key nunca vaza no erro

    def test_400_filtros_invalidos_sem_retry(self, settings):
        session = FakeSession(responses=[
            FakeResponse(status_code=400,
                         json_data={"message": "invalid collectionDate"}),
        ])
        client = _client(settings, session)
        with pytest.raises(PriceTrackBadRequestError):
            client.offers_page(CollectQuery("2026-07-01"))
        assert len(session.calls) == 1

    def test_409_nenhuma_tabela_de_coleta(self, settings):
        session = FakeSession(responses=[
            FakeResponse(status_code=409, json_data={"message": "no collect table"}),
        ])
        client = _client(settings, session)
        with pytest.raises(PriceTrackNoCollectionError):
            client.count_offers(CollectQuery("2026-07-01"))

    def test_429_limite_de_exports_com_retry_after(self, settings):
        session = FakeSession(responses=[
            FakeResponse(status_code=429, headers={"Retry-After": "42"},
                         json_data={"message": "export limit"}),
        ])
        client = _client(settings, session)
        with pytest.raises(PriceTrackExportLimitError) as err:
            client.create_offers_export(ExportRequest("2026-07-01"))
        assert err.value.retry_after == 42.0


class TestRetryBackoff:
    def test_5xx_retenta_e_recupera(self, settings):
        sleeps = []
        session = FakeSession(responses=[
            FakeResponse(status_code=503, json_data={"message": "unavailable"}),
            FakeResponse(status_code=500, json_data={"message": "boom"}),
            FakeResponse(json_data=paged_payload(
                [offer_payload()], page=1, take=1, total=1)),
        ])
        client = _client(settings, session, sleeps)
        page = client.offers_page(CollectQuery("2026-07-01"))
        assert len(page.data) == 1
        assert len(session.calls) == 3
        # backoff exponencial: base·2^0, base·2^1 (jitter fator 1.0)
        assert sleeps == [
            settings.backoff_base_seconds,
            settings.backoff_base_seconds * 2,
        ]

    def test_falha_de_rede_retenta(self, settings):
        session = FakeSession(responses=[
            requests.exceptions.ConnectionError("reset"),
            requests.exceptions.Timeout("timeout"),
            FakeResponse(json_data=paged_payload([], page=1, take=1, total=0)),
        ])
        client = _client(settings, session)
        page = client.offers_page(CollectQuery("2026-07-01"))
        assert page.meta.page_count == 0

    def test_retries_esgotados_levanta_ultimo_erro(self, settings):
        # max_retries=2 nos settings → 3 tentativas no total
        session = FakeSession(responses=[
            FakeResponse(status_code=500, json_data={}),
            FakeResponse(status_code=500, json_data={}),
            FakeResponse(status_code=500, json_data={}),
        ])
        client = _client(settings, session)
        with pytest.raises(PriceTrackServerError):
            client.offers_page(CollectQuery("2026-07-01"))
        assert len(session.calls) == settings.max_retries + 1

    def test_rede_esgotada_levanta_network_error(self, settings):
        session = FakeSession(responses=[
            requests.exceptions.ConnectionError("x")
            for _ in range(settings.max_retries + 1)
        ])
        client = _client(settings, session)
        with pytest.raises(PriceTrackNetworkError):
            client.offers_page(CollectQuery("2026-07-01"))

    def test_backoff_respeita_teto(self, settings):
        settings.max_retries = 5
        sleeps = []
        session = FakeSession(responses=[
            FakeResponse(status_code=500, json_data={}) for _ in range(5)
        ] + [FakeResponse(json_data=paged_payload([], page=1, take=1, total=0))])
        client = _client(settings, session, sleeps)
        client.offers_page(CollectQuery("2026-07-01"))
        # 0.5, 1, 2, 4, 8→teto 4.0
        assert sleeps == [0.5, 1.0, 2.0, 4.0, 4.0]

    def test_resposta_nao_json_e_retryable(self, settings):
        session = FakeSession(responses=[
            FakeResponse(status_code=200, content=b"<html>gateway error</html>"),
            FakeResponse(json_data=paged_payload([], page=1, take=1, total=0)),
        ])
        client = _client(settings, session)
        page = client.offers_page(CollectQuery("2026-07-01"))
        assert page.meta.page_count == 0


class TestSettingsValidation:
    def test_max_concurrent_fora_do_intervalo_falha_cedo(self, tmp_path):
        from pricetrack_api.config import PriceTrackSettings
        from pricetrack_api.exceptions import PriceTrackConfigError
        for invalid in (0, -1, 4):
            with pytest.raises(PriceTrackConfigError, match="entre 1 e 3"):
                PriceTrackSettings(api_key="k", max_concurrent_exports=invalid,
                                   data_dir=tmp_path)


class TestAuthHeader:
    def test_api_key_viaja_no_header_configurado(self, settings):
        session = FakeSession(responses=[
            FakeResponse(json_data=paged_payload([], page=1, take=1, total=0)),
        ])
        client = _client(settings, session)
        client.offers_page(CollectQuery("2026-07-01"))
        headers = session.calls[0].headers
        assert headers[settings.auth_header] == "test-key-nunca-logar"

    def test_settings_repr_nao_expoe_key(self, settings):
        assert "test-key-nunca-logar" not in repr(settings)
