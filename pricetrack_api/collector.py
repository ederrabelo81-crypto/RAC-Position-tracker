"""
SmartCollector — estratégia de coleta inteligente por volume.

Decisão (estratégia ``auto``):
  1. Sonda o total exato de linhas do dia com UMA chamada barata
     (``take=1`` → ``meta.pageCount`` = nº de linhas).
  2. Volume ≤ ``export_threshold_rows``  → endpoints PAGINADOS (síncrono,
     latência baixa, sem gastar slot de export).
  3. Volume  > threshold                 → EXPORT EM MASSA (NDJSON.gz +
     polling assíncrono) — dias com centenas de milhares/milhões de linhas.

Nos dois caminhos o destino é o mesmo: partição raw por ``collectionDate``
no ``NdjsonStore`` (dedup por ``id``; coletas múltiplas do dia coexistem).

Limitação do export: o POST só aceita ``marketplaces`` e
``collectionHourExecutionRange`` como filtro. Os demais filtros da query
(marca, sku, status...) são aplicados client-side após o download, para que
as duas estratégias tenham a MESMA semântica de resultado.
"""
from __future__ import annotations

import gzip
import time
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional

from loguru import logger

from .client import PriceTrackClient
from .exceptions import PriceTrackError, PriceTrackNoCollectionError
from .exports import ExportManager
from .metrics import (
    STATUS_NO_DATA,
    STATUS_SUCCESS,
    AlertSink,
    CollectionMetrics,
    alert_if_failed,
)
from .models import (
    CollectQuery,
    ExportRequest,
    Offer,
    Shipping,
    iter_ndjson_records,
    pick,
    to_str,
)
from .store import NdjsonStore, UpsertStats

STRATEGY_AUTO = "auto"
STRATEGY_PAGINATED = "paginated"
STRATEGY_EXPORT = "export"

_DATASET_OFFERS = "offers"
_DATASET_SHIPPING = "shipping"


@dataclass(slots=True)
class CollectionResult:
    """Resultado de uma coleta: métricas + onde os dados foram parar."""

    metrics: CollectionMetrics
    partition_path: Optional[Path] = None
    upsert: Optional[UpsertStats] = None
    total_available: Optional[int] = None   # linhas no dia, segundo a sonda


class SmartCollector:
    """Coleta um dia inteiro (ou um filtro) escolhendo a melhor estratégia."""

    def __init__(
        self,
        client: PriceTrackClient,
        store: Optional[NdjsonStore] = None,
        alert_sink: Optional[AlertSink] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._store = store or NdjsonStore(client.settings.data_dir / "partitions")
        self._alert_sink = alert_sink
        self._sleep = sleep_fn
        self._clock = clock

    # ── API pública ──────────────────────────────────────────────────────

    def collect_offers(
        self,
        collection_date: date | str,
        query: Optional[CollectQuery] = None,
        strategy: str = STRATEGY_AUTO,
    ) -> CollectionResult:
        """Coleta as ofertas de um dia para a partição local."""
        return self._collect(_DATASET_OFFERS, collection_date, query, strategy)

    def collect_shipping(
        self,
        collection_date: date | str,
        query: Optional[CollectQuery] = None,
        strategy: str = STRATEGY_AUTO,
    ) -> CollectionResult:
        """Coleta os fretes de um dia para a partição local."""
        return self._collect(_DATASET_SHIPPING, collection_date, query, strategy)

    # ── Núcleo ───────────────────────────────────────────────────────────

    def _collect(self, dataset: str, collection_date, query, strategy) -> CollectionResult:
        if strategy not in (STRATEGY_AUTO, STRATEGY_PAGINATED, STRATEGY_EXPORT):
            raise ValueError(f"Estratégia inválida: {strategy!r}")
        # O argumento collection_date é a autoridade: sobrescreve a data da
        # query para evitar coletar silenciosamente um dia diferente do pedido.
        if query is None:
            query = CollectQuery(collection_date=collection_date)
        else:
            query = replace(query, collection_date=collection_date)
        cd = query.collection_date.isoformat()

        metrics = CollectionMetrics(dataset=dataset, collection_date=cd)
        result = CollectionResult(metrics=metrics)

        try:
            total = self._probe_total(dataset, query, metrics)
            if total is None:  # 409 — dia sem coleta
                metrics.finish(STATUS_NO_DATA)
                metrics.log()
                return result
            result.total_available = total

            chosen = strategy
            if strategy == STRATEGY_AUTO:
                threshold = self._client.settings.export_threshold_rows
                chosen = (
                    STRATEGY_PAGINATED if total <= threshold else STRATEGY_EXPORT
                )
                logger.info(
                    f"{dataset}/{cd}: {total:,} linha(s) na API — estratégia "
                    f"{chosen} (threshold: {threshold:,})"
                )
            metrics.strategy = chosen

            if chosen == STRATEGY_PAGINATED:
                stats = self._collect_paginated(dataset, query, metrics)
            else:
                stats = self._collect_via_export(dataset, query, metrics)

            result.upsert = stats
            result.partition_path = self._store.data_path(dataset, cd)
            metrics.rows_stored_new = stats.new
            metrics.rows_updated = stats.updated
            metrics.finish(STATUS_SUCCESS if metrics.rows_fetched else STATUS_NO_DATA)
            metrics.log()
            return result

        except PriceTrackError as e:
            metrics.fail(str(e))
            metrics.finish()
            metrics.log()
            alert_if_failed(metrics, self._alert_sink)
            raise

    def _probe_total(self, dataset: str, query: CollectQuery,
                     metrics: CollectionMetrics) -> Optional[int]:
        """Total exato de linhas do filtro; None quando o dia não tem coleta."""
        count_fn = (
            self._client.count_offers
            if dataset == _DATASET_OFFERS
            else self._client.count_shipping
        )
        try:
            return count_fn(query)
        except PriceTrackNoCollectionError:
            logger.warning(
                f"{dataset}/{query.collection_date}: sem tabela de coleta (409)"
            )
            return None

    # ── Estratégia paginada ──────────────────────────────────────────────

    def _collect_paginated(self, dataset: str, query: CollectQuery,
                           metrics: CollectionMetrics) -> UpsertStats:
        pages_iter = (
            self._client.iter_offer_pages(query)
            if dataset == _DATASET_OFFERS
            else self._client.iter_shipping_pages(query)
        )

        def raw_records():
            for page in pages_iter:
                metrics.pages_fetched += 1
                for parsed, raw in zip(page.data, page.raw):
                    metrics.observe(parsed.marketplace, parsed.brand)
                    yield raw

        return self._store.upsert(
            dataset, query.collection_date, raw_records(),
            source=f"paginated:{query.collection_date}",
        )

    # ── Estratégia export em massa ───────────────────────────────────────

    def _collect_via_export(self, dataset: str, query: CollectQuery,
                            metrics: CollectionMetrics) -> UpsertStats:
        request = ExportRequest(
            collection_date=query.collection_date,
            marketplaces=query.marketplace,          # único filtro com pushdown
            collection_hour_execution_range=query.collection_hour_range,
        )
        manager = ExportManager(self._client, dataset=dataset,
                                sleep_fn=self._sleep, clock=self._clock)
        outcome = manager.run(request)  # levanta exceção tipada em falha
        job = outcome.job
        metrics.export_duration_seconds = round(outcome.duration_seconds, 2)
        if job is not None:
            metrics.export_row_count = job.row_count
            metrics.export_file_size_bytes = job.file_size_bytes

        predicate = _client_side_predicate(dataset, query)
        parse = Offer.from_api if dataset == _DATASET_OFFERS else Shipping.from_api

        def raw_records():
            with gzip.open(outcome.path, "rt", encoding="utf-8") as fh:
                for raw, bad_line in iter_ndjson_records(fh):
                    if raw is None:
                        metrics.rows_invalid += 1
                        continue
                    parsed = parse(raw)
                    if predicate is not None and not predicate(parsed):
                        metrics.rows_filtered_out += 1
                        continue
                    metrics.observe(parsed.marketplace, parsed.brand)
                    yield raw

        return self._store.upsert(
            dataset, query.collection_date, raw_records(),
            source=f"export:{job.export_id if job else '?'}",
        )


def _client_side_predicate(dataset: str, query: CollectQuery) -> Optional[Callable]:
    """Replica client-side os filtros que o export bulk não aceita.

    O marketplace já teve pushdown no POST; aqui entram seller, marca,
    categoria, subcategoria, família, sku e status. Filtros de busca textual
    (searchTitle/productsName) têm semântica da API que não dá para replicar
    com fidelidade — geram warning e são ignorados no caminho export.
    """
    checks: List[Callable] = []

    def _norm_set(values) -> set:
        return {to_str(v).upper() for v in values}

    # bind por default arg (a=...): evita late-binding entre os blocos abaixo
    if query.seller:
        checks.append(lambda r, a=_norm_set(query.seller): r.seller.upper() in a)
    if query.product_brand:
        checks.append(lambda r, a=_norm_set(query.product_brand): r.brand.upper() in a)
    if query.product_category:
        checks.append(
            lambda r, a=_norm_set(query.product_category): r.category.upper() in a
        )
    if query.product_subcategory:
        checks.append(
            lambda r, a=_norm_set(query.product_subcategory): r.subcategory.upper() in a
        )
    if query.product_family:
        checks.append(
            lambda r, a=_norm_set(query.product_family): r.family.upper() in a
        )
    if query.product_sku:
        checks.append(lambda r, a=_norm_set(query.product_sku): r.sku.upper() in a)
    if query.status:
        checks.append(lambda r, w=query.status.upper(): r.status == w)
    if dataset == _DATASET_OFFERS:
        if query.color:
            checks.append(
                lambda r, a=_norm_set(query.color): (r.color or "").upper() in a
            )
        if query.spot_price_min is not None:
            checks.append(
                lambda r: r.spot_price is not None
                and r.spot_price >= query.spot_price_min
            )
        if query.spot_price_max is not None:
            checks.append(
                lambda r: r.spot_price is not None
                and r.spot_price <= query.spot_price_max
            )
        if query.forward_price_min is not None:
            checks.append(
                lambda r: r.forward_price is not None
                and r.forward_price >= query.forward_price_min
            )
        if query.forward_price_max is not None:
            checks.append(
                lambda r: r.forward_price is not None
                and r.forward_price <= query.forward_price_max
            )
    if query.search_title or query.products_name:
        logger.warning(
            "Filtros textuais (searchTitle/productsName) não são aplicáveis "
            "no caminho export — ignorados. Use strategy=paginated para "
            "semântica exata."
        )

    if not checks:
        return None
    return lambda record: all(check(record) for check in checks)
