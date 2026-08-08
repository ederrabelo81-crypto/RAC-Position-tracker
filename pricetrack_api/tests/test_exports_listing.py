"""
Paginação de `list_exports` — e por que ela decide o 429.

O limite da API é de 3 exports concorrentes por organização. `ExportManager`
consulta `count_active_exports()` para saber se há slot; enquanto
`list_exports` lia só a primeira página, um export travado fora dela era
invisível. O cliente concluía "há slot", criava o export e tomava 429 — o
sintoma que travou toda a importação em 08/08/2026, com dois zumbis
(um PENDING que nunca começou, um PROCESSING parado em 0% por 23h).
"""
from __future__ import annotations

from typing import Dict, List

from pricetrack_api.client import PriceTrackClient
from pricetrack_api.http import HttpTransport

from .conftest import FakeResponse, FakeSession


def _job(export_id: str, status: str) -> Dict:
    return {
        "exportId": export_id,
        "status": status,
        "progress": 0,
        "type": "EXTERNAL_COLLECTS_OFFERS",
        "format": "ndjson.gz",
    }


def _pagina(items: List[Dict], has_next: bool) -> FakeResponse:
    return FakeResponse(json_data={"data": items, "meta": {"hasNextPage": has_next}})


def _client(settings, session) -> PriceTrackClient:
    return PriceTrackClient(settings, transport=HttpTransport(settings, session=session))


class TestListExportsPaginado:
    def test_segue_has_next_page(self, settings):
        session = FakeSession(responses=[
            _pagina([_job("e1", "DONE"), _job("e2", "DONE")], has_next=True),
            _pagina([_job("e3", "PENDING")], has_next=False),
        ])
        jobs = _client(settings, session).list_exports()
        assert [j.export_id for j in jobs] == ["e1", "e2", "e3"]

    def test_para_quando_nao_ha_proxima(self, settings):
        session = FakeSession(responses=[
            _pagina([_job("e1", "DONE")], has_next=False),
        ])
        assert len(_client(settings, session).list_exports()) == 1
        assert len(session.calls) == 1, "não deveria pedir uma segunda página"

    def test_sem_meta_nao_pagina(self, settings):
        """API que não expõe paginação: o que veio é tudo — sem loop."""
        session = FakeSession(responses=[
            FakeResponse(json_data={"data": [_job("e1", "DONE")]}),
        ])
        assert len(_client(settings, session).list_exports()) == 1
        assert len(session.calls) == 1

    def test_pagina_vazia_encerra(self, settings):
        """hasNextPage=True com data=[] não pode virar loop infinito."""
        session = FakeSession(responses=[
            _pagina([_job("e1", "DONE")], has_next=True),
            _pagina([], has_next=True),
        ])
        assert len(_client(settings, session).list_exports()) == 1

    def test_all_pages_false_le_so_a_primeira(self, settings):
        session = FakeSession(responses=[
            _pagina([_job("e1", "DONE")], has_next=True),
        ])
        jobs = _client(settings, session).list_exports(all_pages=False)
        assert len(jobs) == 1
        assert len(session.calls) == 1

    def test_envia_page_e_take(self, settings):
        session = FakeSession(responses=[
            _pagina([_job("e1", "DONE")], has_next=False),
        ])
        _client(settings, session).list_exports()
        params = session.calls[0].params
        assert params["page"] == 1
        assert params["take"] == settings.page_take


class TestCountActiveExports:
    def test_conta_ativos_de_todas_as_paginas(self, settings):
        """O caso que gerava o 429: o travado estava na segunda página."""
        session = FakeSession(responses=[
            _pagina([_job("e1", "DONE"), _job("e2", "DONE")], has_next=True),
            _pagina([_job("e3", "PENDING"), _job("e4", "PROCESSING")], has_next=False),
        ])
        assert _client(settings, session).count_active_exports() == 2

    def test_done_e_failed_nao_contam(self, settings):
        session = FakeSession(responses=[
            _pagina([_job("e1", "DONE"), _job("e2", "FAILED")], has_next=False),
        ])
        assert _client(settings, session).count_active_exports() == 0

    def test_status_em_minusculas_conta(self, settings):
        """A API devolve 'pending'/'processing' minúsculo na listagem."""
        session = FakeSession(responses=[
            _pagina([_job("e1", "pending"), _job("e2", "processing")], has_next=False),
        ])
        assert _client(settings, session).count_active_exports() == 2
