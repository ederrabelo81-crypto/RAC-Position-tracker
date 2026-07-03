"""Paginação real: iterar via meta.hasNextPage, nunca por take fixo."""
from pricetrack_api.client import PriceTrackClient
from pricetrack_api.http import HttpTransport
from pricetrack_api.models import CollectQuery

from .conftest import FakeResponse, FakeSession, offer_payload, paged_payload


def _client(settings, session) -> PriceTrackClient:
    transport = HttpTransport(settings, session=session,
                              sleep_fn=lambda s: None, rng=lambda: 0.5)
    return PriceTrackClient(settings, transport=transport)


class TestIterOffers:
    def test_percorre_todas_as_paginas_via_has_next_page(self, settings):
        offers = [offer_payload(oid=f"of-{i}") for i in range(5)]
        session = FakeSession(responses=[
            FakeResponse(json_data=paged_payload(offers[0:2], page=1, take=2, total=5)),
            FakeResponse(json_data=paged_payload(offers[2:4], page=2, take=2, total=5)),
            FakeResponse(json_data=paged_payload(offers[4:5], page=3, take=2, total=5)),
        ])
        client = _client(settings, session)

        collected = list(client.iter_offers(CollectQuery("2026-07-01")))

        assert [o.id for o in collected] == ["of-0", "of-1", "of-2", "of-3", "of-4"]
        assert len(session.calls) == 3
        # page incrementa a cada chamada; collectionDate viaja em todas
        assert [c.params["page"] for c in session.calls] == [1, 2, 3]
        assert all(c.params["collectionDate"] == "2026-07-01"
                   for c in session.calls)

    def test_para_na_primeira_pagina_sem_next(self, settings):
        session = FakeSession(responses=[
            FakeResponse(json_data=paged_payload(
                [offer_payload()], page=1, take=2, total=1)),
        ])
        client = _client(settings, session)
        assert len(list(client.iter_offers(CollectQuery("2026-07-01")))) == 1
        assert len(session.calls) == 1

    def test_pagina_vazia_sem_next_encerra(self, settings):
        session = FakeSession(responses=[
            FakeResponse(json_data={"data": [], "meta": {
                "page": 1, "take": 2, "pageCount": 0,
                "hasNextPage": False, "hasPreviousPage": False}}),
        ])
        client = _client(settings, session)
        assert list(client.iter_offers(CollectQuery("2026-07-01"))) == []

    def test_guarda_anti_loop_quando_api_repete_has_next(self, settings):
        # API defeituosa: sempre hasNextPage=True com pageCount=2. O guarda
        # interrompe após pageCount+1 páginas em vez de iterar para sempre.
        def handler(call):
            return FakeResponse(json_data={
                "data": [offer_payload(oid=f"of-{call.params['page']}")],
                "meta": {"page": call.params["page"], "take": 2, "pageCount": 2,
                         "hasNextPage": True, "hasPreviousPage": False},
            })
        session = FakeSession(handler=handler)
        client = _client(settings, session)

        collected = list(client.iter_offers(CollectQuery("2026-07-01")))
        assert len(collected) == 3          # pageCount(2) + 1 e para
        assert len(session.calls) == 3

    def test_guarda_anti_loop_quando_api_ignora_param_page(self, settings):
        # API defeituosa: devolve SEMPRE a mesma página (mesmo primeiro id)
        # com hasNextPage=True e pageCount alto. A detecção de conteúdo
        # repetido interrompe na 2ª página.
        def handler(call):
            return FakeResponse(json_data={
                "data": [offer_payload(oid="sempre-o-mesmo")],
                "meta": {"page": call.params["page"], "take": 2, "pageCount": 50,
                         "hasNextPage": True, "hasPreviousPage": False},
            })
        session = FakeSession(handler=handler)
        client = _client(settings, session)

        collected = list(client.iter_offers(CollectQuery("2026-07-01")))
        assert len(session.calls) == 2
        assert len(collected) == 2

    def test_take_default_vem_dos_settings(self, settings):
        session = FakeSession(responses=[
            FakeResponse(json_data=paged_payload([], page=1, take=2, total=0)),
        ])
        client = _client(settings, session)
        list(client.iter_offers(CollectQuery("2026-07-01", take=0)))
        assert session.calls[0].params["take"] == settings.page_take


class TestCountOffers:
    def test_sonda_com_take_1_devolve_page_count(self, settings):
        # Com take=1, pageCount == total de linhas do filtro.
        session = FakeSession(responses=[
            FakeResponse(json_data=paged_payload(
                [offer_payload()], page=1, take=1, total=123_456)),
        ])
        client = _client(settings, session)
        assert client.count_offers(CollectQuery("2026-07-01")) == 123_456
        assert session.calls[0].params["take"] == 1
        assert session.calls[0].params["page"] == 1


class TestShippingPagination:
    def test_itera_fretes(self, settings):
        ship = {
            "id": "sh-1", "sku": "S", "marketplace": "MAGALU",
            "seller": "MAGALU", "cep": "01310-100", "shippingCost": 30.0,
            "deadline": 5, "transporterType": "TRANSPORTADORA",
            "status": "AVAILABLE", "collectionDate": "2026-07-01",
        }
        session = FakeSession(responses=[
            FakeResponse(json_data=paged_payload([ship], page=1, take=2, total=1)),
        ])
        client = _client(settings, session)
        result = list(client.iter_shipping(CollectQuery("2026-07-01")))
        assert len(result) == 1
        assert result[0].shipping_cost == 30.0
        assert "/collects-shipping-external" in session.calls[0].url
