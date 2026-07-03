"""
PriceTrackClient — cliente tipado da API Externa do PriceTrack (v1.2.0).

Endpoints cobertos:
  * GET  /collects-offers-external      (ofertas paginadas)
  * GET  /collects-shipping-external    (fretes paginados)
  * POST /exports-external/collects-offers
  * POST /exports-external/collects-shipping
  * GET  /exports-external              (lista; data[0] = mais recente)
  * GET  /exports-external/{exportId}   (status + downloadUrl quando DONE)

A paginação SEMPRE avança via ``meta.hasNextPage`` — nunca assume nº fixo de
páginas. A downloadUrl é tratada como efêmera (TTL 1h): ``download_export``
renova via GET de status quando o snapshot está velho ou o download volta
403/404.
"""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional

from loguru import logger

from .config import PriceTrackSettings
from .exceptions import DownloadUrlExpiredError, PriceTrackError
from .http import HttpTransport
from .models import (
    EXPORT_DONE,
    CollectQuery,
    ExportJob,
    ExportRequest,
    Offer,
    Page,
    PageMeta,
    Shipping,
    pick,
)

OFFERS_PATH = "/collects-offers-external"
SHIPPING_PATH = "/collects-shipping-external"
EXPORT_OFFERS_PATH = "/exports-external/collects-offers"
EXPORT_SHIPPING_PATH = "/exports-external/collects-shipping"
EXPORTS_PATH = "/exports-external"


class PriceTrackClient:
    """Fachada tipada e resiliente sobre a API do PriceTrack."""

    def __init__(
        self,
        settings: PriceTrackSettings,
        transport: Optional[HttpTransport] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self._transport = transport or HttpTransport(settings)
        self._clock = clock

    # ── Endpoints paginados ──────────────────────────────────────────────

    def offers_page(self, query: CollectQuery) -> Page[Offer]:
        """Uma página de ofertas (GET /collects-offers-external)."""
        return self._collect_page(OFFERS_PATH, query, Offer.from_api)

    def shipping_page(self, query: CollectQuery) -> Page[Shipping]:
        """Uma página de fretes (GET /collects-shipping-external)."""
        return self._collect_page(SHIPPING_PATH, query, Shipping.from_api)

    def iter_offers(self, query: CollectQuery) -> Iterator[Offer]:
        """Itera TODAS as ofertas do filtro, página a página via hasNextPage."""
        for page in self.iter_offer_pages(query):
            yield from page.data

    def iter_shipping(self, query: CollectQuery) -> Iterator[Shipping]:
        for page in self.iter_shipping_pages(query):
            yield from page.data

    def iter_offer_pages(self, query: CollectQuery) -> Iterator[Page[Offer]]:
        return self._iter_pages(OFFERS_PATH, query, Offer.from_api)

    def iter_shipping_pages(self, query: CollectQuery) -> Iterator[Page[Shipping]]:
        return self._iter_pages(SHIPPING_PATH, query, Shipping.from_api)

    def count_offers(self, query: CollectQuery) -> int:
        """Total EXATO de ofertas do filtro, com uma única chamada barata.

        Com ``take=1`` o ``meta.pageCount`` é o próprio número de linhas —
        é a sonda usada pela estratégia paginado × export.

        Raises:
            PriceTrackNoCollectionError: 409 — sem coleta para a data.
        """
        probe = self.offers_page(replace_query(query, page=1, take=1))
        return probe.meta.page_count

    def count_shipping(self, query: CollectQuery) -> int:
        probe = self.shipping_page(replace_query(query, page=1, take=1))
        return probe.meta.page_count

    def _collect_page(self, path: str, query: CollectQuery, parse) -> Page:
        payload = self._transport.request_json("GET", path, params=query.to_params())
        raw_items = payload.get("data") or []
        meta = PageMeta.from_api(payload.get("meta") or {})
        return Page(
            data=[parse(item) for item in raw_items],
            raw=list(raw_items),
            meta=meta,
        )

    # Teto absoluto de páginas quando a API não informa pageCount — evita
    # loop infinito com metadata malformada (hasNextPage=true perpétuo).
    _HARD_PAGE_CAP = 100_000

    def _iter_pages(self, path: str, query: CollectQuery, parse) -> Iterator[Page]:
        """Loop de paginação guiado por ``meta.hasNextPage``.

        Nunca confia em ``take``/``pageCount`` para decidir parada; guardas
        de segurança interrompem caso a API repita páginas indefinidamente:
        pageCount excedido, mesmo primeiro ``id`` em páginas consecutivas
        (API ignorando o param ``page``) ou teto absoluto de páginas.
        """
        take = query.take if query.take and query.take > 0 else self.settings.page_take
        current = replace_query(query, page=max(1, query.page), take=take)
        pages_seen = 0
        prev_first_id: Optional[str] = None
        while True:
            page = self._collect_page(path, current, parse)
            pages_seen += 1
            yield page
            if not page.meta.has_next_page:
                return
            # Guarda anti-loop 1: hasNextPage nunca deveria exceder pageCount
            # (teto absoluto quando a API não informa pageCount).
            limit = page.meta.page_count or self._HARD_PAGE_CAP
            if pages_seen > limit:
                logger.warning(
                    f"PriceTrack {path}: hasNextPage após {pages_seen} páginas "
                    f"com pageCount={page.meta.page_count} — interrompendo."
                )
                return
            # Guarda anti-loop 2: mesma primeira oferta em páginas
            # consecutivas = API ignorando o param `page`.
            first_id = pick(page.raw[0], "id") if page.raw else None
            if first_id is not None and first_id == prev_first_id:
                logger.warning(
                    f"PriceTrack {path}: página {page.meta.page} repetiu o "
                    f"conteúdo da anterior (id={first_id}) — interrompendo."
                )
                return
            prev_first_id = first_id
            next_page = (page.meta.page or current.page) + 1
            current = replace_query(current, page=next_page)

    # ── Exports assíncronos ──────────────────────────────────────────────

    def create_offers_export(self, request: ExportRequest) -> ExportJob:
        """POST /exports-external/collects-offers → job em status pending.

        Raises:
            PriceTrackExportLimitError: 429 — 3 exports concorrentes em uso.
            PriceTrackNoCollectionError: 409 — sem coleta para a data.
        """
        return self._create_export(EXPORT_OFFERS_PATH, request)

    def create_shipping_export(self, request: ExportRequest) -> ExportJob:
        return self._create_export(EXPORT_SHIPPING_PATH, request)

    def _create_export(self, path: str, request: ExportRequest) -> ExportJob:
        payload = self._transport.request_json(
            "POST", path, json_body=request.to_body()
        )
        job = ExportJob.from_api(payload, fetched_at=self._clock())
        logger.info(
            f"PriceTrack export criado: {job.export_id} "
            f"({request.collection_date}) status={job.status}"
        )
        return job

    def get_export(self, export_id: str) -> ExportJob:
        """GET /exports-external/{exportId} — status atual do job.

        Quando DONE, o payload traz uma downloadUrl FRESCA (TTL 1h) — este é
        também o mecanismo de renovação de URLs expiradas.
        """
        payload = self._transport.request_json("GET", f"{EXPORTS_PATH}/{export_id}")
        return ExportJob.from_api(payload, fetched_at=self._clock())

    def list_exports(self) -> List[ExportJob]:
        """GET /exports-external — exports da organização (data[0] = recente)."""
        payload = self._transport.request_json("GET", EXPORTS_PATH)
        items = payload.get("data") or []
        now = self._clock()
        return [ExportJob.from_api(item, fetched_at=now) for item in items]

    def count_active_exports(self) -> int:
        """Exports pending/processing em andamento (limite: 3 por organização)."""
        return sum(1 for job in self.list_exports() if job.is_active)

    # ── Download com renovação de URL ────────────────────────────────────

    def download_export(self, job: ExportJob, dest: Path) -> ExportJob:
        """Baixa o NDJSON.gz de um export DONE, renovando a URL se expirada.

        A URL pré-assinada expira em 1h. Renovamos proativamente quando o
        snapshot é mais velho que ``download_url_ttl_seconds`` e reativamente
        quando o storage responde 403/404.

        Returns:
            O ExportJob mais recente (pós-renovação, se houve).

        Raises:
            PriceTrackError: job não-DONE ou sem downloadUrl após renovação.
        """
        if job.status != EXPORT_DONE:
            raise PriceTrackError(
                f"Export {job.export_id} não está DONE (status={job.status})"
            )
        now = self._clock()
        if not job.download_url or job.download_url_stale(
            self.settings.download_url_ttl_seconds, now
        ):
            logger.info(
                f"PriceTrack export {job.export_id}: renovando downloadUrl "
                f"(snapshot com {now - job.fetched_at:.0f}s)"
            )
            job = self.get_export(job.export_id)

        if not job.download_url:
            raise PriceTrackError(
                f"Export {job.export_id} está DONE mas sem downloadUrl"
            )
        try:
            size = self._transport.stream_download(job.download_url, dest)
        except DownloadUrlExpiredError:
            logger.warning(
                f"PriceTrack export {job.export_id}: downloadUrl expirada "
                f"durante o download — renovando via status e tentando de novo."
            )
            job = self.get_export(job.export_id)
            if not job.download_url:
                raise PriceTrackError(
                    f"Export {job.export_id}: renovação não trouxe downloadUrl"
                )
            size = self._transport.stream_download(job.download_url, dest)

        logger.success(
            f"PriceTrack export {job.export_id}: download OK "
            f"({size / 1024:.0f} KB → {dest})"
        )
        return job


def replace_query(query: CollectQuery, **changes) -> CollectQuery:
    """``dataclasses.replace`` para CollectQuery (revalida no __post_init__)."""
    return replace(query, **changes)
