"""
ExportManager — orquestra o ciclo de vida dos exports em massa (NDJSON.gz).

Fluxo por export:  POST cria → polling em GET /{exportId} até DONE|FAILED →
download em streaming (renovando a downloadUrl expirada) → arquivo local.

Política de slots: no máximo ``max_concurrent_exports`` (≤ 3, limite da API
por organização) em voo. Um 429 na criação NÃO é fatal — o manager espera um
slot liberar e tenta de novo; jobs de terceiros na mesma organização também
contam para o limite, então o 429 pode acontecer mesmo com slots locais
livres.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from loguru import logger

from .client import PriceTrackClient
from .exceptions import (
    ExportFailedError,
    ExportTimeoutError,
    PriceTrackError,
    PriceTrackExportLimitError,
    PriceTrackNoCollectionError,
)
from .models import EXPORT_DONE, EXPORT_FAILED, ExportJob, ExportRequest

OUTCOME_OK = "ok"
OUTCOME_NO_DATA = "no_data"        # 409 — sem coleta para a data
OUTCOME_FAILED = "failed"          # export terminou FAILED
OUTCOME_TIMEOUT = "timeout"        # polling excedeu poll_timeout_seconds
OUTCOME_ERROR = "error"            # erro inesperado (rede esgotada, etc.)


@dataclass(slots=True)
class ExportOutcome:
    """Resultado de um export: status final, arquivo baixado e telemetria."""

    request: ExportRequest
    status: str
    path: Optional[Path] = None
    job: Optional[ExportJob] = None
    duration_seconds: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OUTCOME_OK


@dataclass(slots=True)
class _InFlight:
    request: ExportRequest
    job: ExportJob
    dest: Path
    submitted_at: float
    last_progress: float = field(default=-1.0)


class ExportManager:
    """Executa exports respeitando o limite de concorrência da API."""

    def __init__(
        self,
        client: PriceTrackClient,
        dataset: str = "offers",
        max_concurrent: Optional[int] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if dataset not in ("offers", "shipping"):
            raise ValueError(f"dataset deve ser offers|shipping: {dataset!r}")
        self._client = client
        self._dataset = dataset
        settings = client.settings
        limit = max_concurrent or settings.max_concurrent_exports
        self._max_concurrent = max(1, min(limit, settings.max_concurrent_exports))
        self._sleep = sleep_fn
        self._clock = clock

    # ── API pública ──────────────────────────────────────────────────────

    def run(self, request: ExportRequest, dest: Optional[Path] = None) -> ExportOutcome:
        """Executa UM export do início ao fim.

        Raises:
            PriceTrackNoCollectionError: data sem coleta (409).
            ExportFailedError / ExportTimeoutError: falha terminal do job.
            PriceTrackError: erro inesperado (rede esgotada etc.).
        """
        outcome = self.run_many([request], dest_fn=(lambda _: dest) if dest else None)[0]
        if outcome.status == OUTCOME_NO_DATA:
            raise PriceTrackNoCollectionError(
                f"Sem coleta para {request.collection_date} (409)", 409
            )
        if outcome.status == OUTCOME_FAILED:
            export_id = outcome.job.export_id if outcome.job else "?"
            raise ExportFailedError(export_id, outcome.error)
        if outcome.status == OUTCOME_TIMEOUT:
            export_id = outcome.job.export_id if outcome.job else "?"
            raise ExportTimeoutError(export_id, outcome.duration_seconds)
        if outcome.status == OUTCOME_ERROR:
            raise PriceTrackError(outcome.error)
        return outcome

    def run_many(
        self,
        requests_: Sequence[ExportRequest],
        dest_fn: Optional[Callable[[ExportRequest], Path]] = None,
    ) -> List[ExportOutcome]:
        """Executa vários exports com pipeline de até N jobs concorrentes.

        Nunca levanta exceção por falha individual — cada request vira um
        ``ExportOutcome`` (ok / no_data / failed / timeout / error), preservando
        o restante do lote.
        """
        dest_fn = dest_fn or self._default_dest
        pending: deque[tuple[int, ExportRequest]] = deque(enumerate(requests_))
        in_flight: Dict[str, _InFlight] = {}
        self._index: Dict[str, int] = {}
        outcomes: Dict[int, ExportOutcome] = {}
        slot_wait_started: Optional[float] = None
        settings = self._client.settings

        while pending or in_flight:
            hit_limit, suggested_wait = self._fill_slots(
                pending, in_flight, dest_fn, outcomes
            )
            # Retry-After vem do servidor: limita a um teto seguro para um
            # valor inválido/extremo não travar o loop num sleep gigante.
            if suggested_wait and suggested_wait > 0:
                suggested_wait = min(suggested_wait, settings.backoff_max_seconds)
            else:
                suggested_wait = None

            if hit_limit and not in_flight:
                # Slots ocupados por terceiros na organização: espera com
                # guarda de timeout global para não ficar preso para sempre.
                now = self._clock()
                slot_wait_started = slot_wait_started or now
                if now - slot_wait_started > settings.poll_timeout_seconds:
                    while pending:
                        idx, request = pending.popleft()
                        outcomes[idx] = ExportOutcome(
                            request=request, status=OUTCOME_TIMEOUT,
                            error="timeout aguardando slot de export (429)",
                        )
                    break
                self._sleep(suggested_wait or settings.poll_interval_seconds)
                continue
            slot_wait_started = None

            if not in_flight:
                continue
            # 429 com jobs locais em voo: honra o Retry-After sugerido antes
            # de tentar criar de novo (o poll dos jobs acontece junto).
            wait = settings.poll_interval_seconds
            if hit_limit and suggested_wait:
                wait = max(wait, suggested_wait)
            self._sleep(wait)
            self._poll_in_flight(in_flight, outcomes)

        return [outcomes[i] for i in sorted(outcomes)]

    # ── internals ────────────────────────────────────────────────────────

    def _default_dest(self, request: ExportRequest) -> Path:
        """Destino padrão do arquivo. Requests filtrados ganham um hash no
        nome para que dois exports do mesmo dia com filtros diferentes não
        se sobrescrevam em disco."""
        root = self._client.settings.data_dir / "raw"
        suffix = ""
        if request.marketplaces or request.collection_hour_execution_range:
            import hashlib
            import json
            key = json.dumps(request.to_body(), sort_keys=True, ensure_ascii=False)
            suffix = "-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        return root / f"{self._dataset}-{request.collection_date}{suffix}.ndjson.gz"

    def _create(self, request: ExportRequest) -> ExportJob:
        if self._dataset == "offers":
            return self._client.create_offers_export(request)
        return self._client.create_shipping_export(request)

    def _fill_slots(self, pending, in_flight, dest_fn, outcomes):
        """Cria exports até encher os slots. Retorna (hit_429, wait_sugerido)."""
        while pending and len(in_flight) < self._max_concurrent:
            idx, request = pending[0]
            try:
                job = self._create(request)
            except PriceTrackNoCollectionError:
                pending.popleft()
                logger.warning(
                    f"PriceTrack export {request.collection_date}: sem dados (409)"
                )
                outcomes[idx] = ExportOutcome(request=request, status=OUTCOME_NO_DATA)
                continue
            except PriceTrackExportLimitError as e:
                logger.info(
                    f"PriceTrack: limite de exports concorrentes (429) — "
                    f"aguardando slot ({len(in_flight)} job(s) locais em voo)"
                )
                return True, e.retry_after
            except PriceTrackError as e:
                pending.popleft()
                logger.error(
                    f"PriceTrack export {request.collection_date}: "
                    f"falha ao criar — {e}"
                )
                outcomes[idx] = ExportOutcome(
                    request=request, status=OUTCOME_ERROR, error=str(e)
                )
                continue

            pending.popleft()
            self._index[job.export_id] = idx
            in_flight[job.export_id] = _InFlight(
                request=request,
                job=job,
                dest=dest_fn(request),
                submitted_at=self._clock(),
            )
        return False, None

    def _poll_in_flight(self, in_flight, outcomes) -> None:
        for export_id in list(in_flight):
            entry = in_flight[export_id]
            idx = self._index[export_id]
            elapsed = self._clock() - entry.submitted_at
            try:
                job = self._client.get_export(export_id)
            except PriceTrackError as e:
                # Poll com falha transitória além dos retries do transporte:
                # mantém o job em voo até o timeout do export.
                logger.warning(f"PriceTrack export {export_id}: poll falhou ({e})")
                if elapsed > self._client.settings.poll_timeout_seconds:
                    del in_flight[export_id]
                    outcomes[idx] = ExportOutcome(
                        request=entry.request, status=OUTCOME_TIMEOUT,
                        job=entry.job, duration_seconds=elapsed, error=str(e),
                    )
                continue

            entry.job = job
            if job.progress is not None and job.progress != entry.last_progress:
                entry.last_progress = job.progress
                logger.debug(
                    f"PriceTrack export {export_id}: {job.status} "
                    f"({job.progress:.0f}%)"
                )

            if job.status == EXPORT_DONE:
                del in_flight[export_id]
                outcomes[idx] = self._finalize(entry, job)
            elif job.status == EXPORT_FAILED:
                del in_flight[export_id]
                logger.error(f"PriceTrack export {export_id}: FAILED")
                outcomes[idx] = ExportOutcome(
                    request=entry.request, status=OUTCOME_FAILED, job=job,
                    duration_seconds=elapsed, error="export FAILED na API",
                )
            elif elapsed > self._client.settings.poll_timeout_seconds:
                del in_flight[export_id]
                logger.error(
                    f"PriceTrack export {export_id}: timeout após {elapsed:.0f}s"
                )
                outcomes[idx] = ExportOutcome(
                    request=entry.request, status=OUTCOME_TIMEOUT, job=job,
                    duration_seconds=elapsed,
                    error=f"timeout após {elapsed:.0f}s",
                )

    def _finalize(self, entry: _InFlight, job: ExportJob) -> ExportOutcome:
        elapsed = self._clock() - entry.submitted_at
        try:
            job = self._client.download_export(job, entry.dest)
        except PriceTrackError as e:
            logger.error(
                f"PriceTrack export {job.export_id}: download falhou — {e}"
            )
            return ExportOutcome(
                request=entry.request, status=OUTCOME_ERROR, job=job,
                duration_seconds=self._clock() - entry.submitted_at, error=str(e),
            )
        logger.info(
            f"PriceTrack export {job.export_id}: concluído em {elapsed:.0f}s "
            f"(rowCount={job.row_count}, {job.file_size_bytes or 0} bytes)"
        )
        return ExportOutcome(
            request=entry.request, status=OUTCOME_OK, job=job,
            path=entry.dest, duration_seconds=self._clock() - entry.submitted_at,
        )
