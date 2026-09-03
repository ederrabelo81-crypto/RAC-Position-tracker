"""
ExportManager — orquestra o ciclo de vida dos exports em massa (NDJSON.gz).

Fluxo por export:  adota um export já criado por execução anterior (diário) ou
POST cria → polling em GET /{exportId} até DONE|FAILED → download em streaming
(renovando a downloadUrl expirada) → arquivo local.

Política de slots: no máximo ``max_concurrent_exports`` (≤ 3, limite da API
por organização) em voo. Um 429 na criação NÃO é fatal — o manager espera um
slot liberar e tenta de novo; jobs de terceiros na mesma organização também
contam para o limite, então o 429 pode acontecer mesmo com slots locais
livres.

**Por que existe o diário (`journal.py`).** A API não tem endpoint de
cancelamento: export criado roda até o fim, e enquanto roda segura um dos 3
slots. Sem registrar o id em disco, qualquer Ctrl+C largava um export órfão —
e a execução seguinte, sem saber que aquele export era dela, criava outro para
a mesma data e tomava 429. Duas ou três interrupções ocupavam os 3 slots e
travavam todo import seguinte em "aguardando slot". Agora o id é gravado assim
que o POST volta, e a execução seguinte **adota** o export em vez de duplicá-lo.

**Por que o log fala sozinho.** O sintoma anterior era um console que só
repetia a linha do 429 a cada 30s: o polling do job em voo era DEBUG, então a
execução parecia travada mesmo progredindo — e o operador matava o processo,
criando mais um órfão. O ciclo se fechava. Agora todo job em voo bate um
heartbeat em INFO, a linha do 429 é throttled, e depois de
``_CENSUS_AFTER_SECONDS`` preso o manager LISTA os exports da organização e diz
**quem** está segurando cada slot.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from .client import PriceTrackClient
from .exceptions import (
    ExportFailedError,
    ExportTimeoutError,
    PriceTrackError,
    PriceTrackExportLimitError,
    PriceTrackNoCollectionError,
)
from .journal import JOURNAL_FILENAME, ExportJournal
from .models import EXPORT_DONE, EXPORT_FAILED, ExportJob, ExportRequest

OUTCOME_OK = "ok"
OUTCOME_NO_DATA = "no_data"        # 409 — sem coleta para a data
OUTCOME_FAILED = "failed"          # export terminou FAILED
OUTCOME_TIMEOUT = "timeout"        # polling excedeu poll_timeout_seconds
OUTCOME_ERROR = "error"            # erro inesperado (rede esgotada, etc.)

#: Intervalo mínimo entre duas linhas de "ainda sem slot" (evita spam de 429).
_BLOCK_LOG_INTERVAL = 60.0
#: Só depois deste tempo preso em 429 vale gastar uma listagem de exports —
#: um 429 de segundos é disputa normal por slot, não um zumbi.
_CENSUS_AFTER_SECONDS = 120.0
#: E, depois disso, no máximo um censo a cada 5 min.
_CENSUS_MIN_INTERVAL = 300.0
#: Cadência do heartbeat de um job em voo, para a execução não parecer travada.
_HEARTBEAT_SECONDS = 120.0


@dataclass(slots=True)
class ExportOutcome:
    """Resultado de um export: status final, arquivo baixado e telemetria."""

    request: ExportRequest
    status: str
    path: Optional[Path] = None
    job: Optional[ExportJob] = None
    duration_seconds: float = 0.0
    error: str = ""
    adopted: bool = False          # veio do diário, não foi criado nesta execução

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
    last_status: str = field(default="")
    last_log: float = field(default=0.0)
    adopted: bool = field(default=False)


class ExportManager:
    """Executa exports respeitando o limite de concorrência da API."""

    def __init__(
        self,
        client: PriceTrackClient,
        dataset: str = "offers",
        max_concurrent: Optional[int] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        journal: Optional[ExportJournal] = None,
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
        self._journal = journal or ExportJournal(settings.data_dir / JOURNAL_FILENAME)
        self._blocked_since: Optional[float] = None
        self._last_block_log: float = 0.0
        self._last_census: float = 0.0

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
        # Marco zero do orçamento total (ver `run_budget_seconds`): a partir
        # daqui o manager recusa SUBMETER export que não caberia no tempo
        # restante, para não ser interrompido no meio de um lote em voo.
        self._run_started = self._clock()
        self._blocked_since = None
        self._last_block_log = 0.0
        self._last_census = 0.0
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
            if hit_limit:
                self._note_blocked(in_flight)
            else:
                self._blocked_since = None
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
            job = self._client.create_offers_export(request)
        else:
            job = self._client.create_shipping_export(request)
        # Gravado ANTES de qualquer espera: se o processo morrer no próximo
        # segundo, a execução seguinte adota este export em vez de criar outro
        # para a mesma data — que é o que enchia os 3 slots de órfãos.
        self._journal.record(self._dataset, request, job.export_id)
        return job

    def _budget_exhausted(self) -> bool:
        """
        True se não há tempo para mais um export dentro do orçamento.

        Submeter um export que o relógio de parede não deixa terminar é o pior
        dos mundos: o processo morre no meio, e o export criado fica órfão
        segurando um dos 3 slots da organização — que é o que faz o import
        seguinte levar 429. Melhor devolver o lote incompleto: os buracos
        restantes o próximo run tenta de novo.
        """
        budget = getattr(self._client.settings, "run_budget_seconds", 0.0) or 0.0
        if budget <= 0:
            return False
        decorrido = self._clock() - getattr(self, "_run_started", self._clock())
        restante = budget - decorrido
        return restante < self._client.settings.poll_timeout_seconds

    def _recorded_job(
        self, request: ExportRequest
    ) -> Optional[Tuple[ExportJob, float]]:
        """Export deste pedido já criado por execução anterior, se ainda serve.

        Returns:
            ``(job, idade_em_segundos)`` quando há um export adotável, ou
            ``None`` quando é preciso criar um novo.
        """
        try:
            entry = self._journal.get(self._dataset, request)
        except Exception as e:                      # diário nunca derruba import
            logger.warning(f"PriceTrack: diário de exports indisponível ({e})")
            return None
        if entry is None:
            return None

        idade = self._journal.age_of(entry)
        limite = self._client.settings.poll_timeout_seconds
        if idade > limite:
            # Export que nasceu antes da janela de vida: do lado da API ou já
            # expirou, ou está travado. Nos dois casos não vale esperar por
            # ele — mas vale dizer que provavelmente é ele no 429.
            logger.warning(
                f"PriceTrack export {entry.export_id} ({entry.collection_date}): "
                f"criado há {idade / 60:.0f}min e nunca concluiu — descartado do "
                f"diário. Se o 429 persistir, é provável que ainda segure um slot."
            )
            self._journal.forget(entry.export_id)
            return None

        try:
            job = self._client.get_export(entry.export_id)
        except PriceTrackError as e:
            # Export sumiu (404) ou a API não respondeu: seguir para o POST é
            # o caminho que devolve dado. Custa, no pior caso, um export
            # duplicado; parar aqui custaria o dia inteiro de importação.
            logger.warning(
                f"PriceTrack export {entry.export_id} ({entry.collection_date}): "
                f"não deu para consultar ({e}) — criando um export novo."
            )
            self._journal.forget(entry.export_id)
            return None

        if job.status != EXPORT_DONE and not job.is_active:
            # FAILED — ou um status que este cliente não conhece. Nos dois
            # casos não há o que esperar: adotar um job em estado desconhecido
            # gastaria o polling inteiro até o timeout para nada.
            logger.info(
                f"PriceTrack export {job.export_id} ({entry.collection_date}): "
                f"execução anterior terminou como {job.status or '?'} — "
                f"criando um novo."
            )
            self._journal.forget(job.export_id)
            return None
        return job, idade

    def _fill_slots(self, pending, in_flight, dest_fn, outcomes):
        """Cria (ou adota) exports até encher os slots.

        Returns:
            ``(hit_429, wait_sugerido)``.
        """
        while pending and len(in_flight) < self._max_concurrent:
            if self._budget_exhausted():
                logger.warning(
                    f"PriceTrack: orçamento da execução esgotado — "
                    f"{len(pending)} export(s) não submetido(s) para não "
                    f"morrerem órfãos. O próximo run retoma."
                )
                while pending:
                    idx, request = pending.popleft()
                    outcomes[idx] = ExportOutcome(
                        request=request, status=OUTCOME_ERROR,
                        error="não submetido: orçamento da execução esgotado",
                    )
                return False, None
            idx, request = pending[0]

            # 1) Já existe export nosso para este pedido? Adota — não gasta
            #    slot novo nem paga de novo o tempo de processamento.
            recorded = self._recorded_job(request)
            if recorded is not None:
                job, idade = recorded
                pending.popleft()
                dest = dest_fn(request)
                if job.status == EXPORT_DONE:
                    logger.info(
                        f"PriceTrack export {job.export_id} "
                        f"({request.collection_date}): já estava DONE de uma "
                        f"execução anterior — só baixando (nenhum slot gasto)."
                    )
                    entry = _InFlight(
                        request=request, job=job, dest=dest,
                        submitted_at=self._clock(), adopted=True,
                    )
                    outcome = self._finalize(entry, job)
                    outcomes[idx] = outcome
                    continue
                logger.info(
                    f"PriceTrack export {job.export_id} "
                    f"({request.collection_date}): adotado de uma execução "
                    f"anterior (status={job.status}, criado há {idade / 60:.0f}min)"
                    f" — nenhum export novo criado."
                )
                self._index[job.export_id] = idx
                # submitted_at reflete a idade REAL: `poll_timeout_seconds` é a
                # vida máxima do export, não a paciência desta execução.
                in_flight[job.export_id] = _InFlight(
                    request=request, job=job, dest=dest,
                    submitted_at=self._clock() - idade, adopted=True,
                )
                continue

            # 2) Não há o que adotar: cria.
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

    # ── diagnóstico do 429 ───────────────────────────────────────────────

    def _note_blocked(self, in_flight: Dict[str, _InFlight]) -> None:
        """Loga o 429 sem spam e, se a espera durar, diz QUEM segura os slots."""
        now = self._clock()
        if self._blocked_since is None:
            self._blocked_since = now
            self._last_block_log = now
            logger.info(
                f"PriceTrack: limite de exports concorrentes (429) — aguardando "
                f"slot ({len(in_flight)} job(s) desta execução em voo)"
            )
            return

        esperando = now - self._blocked_since
        if now - self._last_block_log >= _BLOCK_LOG_INTERVAL:
            self._last_block_log = now
            logger.info(
                f"PriceTrack: ainda sem slot de export há {esperando / 60:.0f}min "
                f"({len(in_flight)} job(s) desta execução em voo)"
            )
        if (
            esperando >= _CENSUS_AFTER_SECONDS
            and now - self._last_census >= _CENSUS_MIN_INTERVAL
        ):
            self._last_census = now
            self._census(in_flight)

    def _census(self, in_flight: Dict[str, _InFlight]) -> None:
        """Lista os exports ativos da organização e nomeia o dono de cada slot.

        Sem isto o 429 é indistinguível de um bug do import: o console dizia
        "aguardando slot" com um job local em voo e nada explicava os outros
        dois. Nomear o ocupante é o que torna a espera uma decisão do operador.
        """
        try:
            ativos = [job for job in self._client.list_exports() if job.is_active]
        except PriceTrackError as e:
            logger.warning(
                f"PriceTrack: não deu para listar os exports da organização ({e})"
            )
            return
        if not ativos:
            logger.warning(
                "PriceTrack: a API recusa novos exports (429) mas não lista "
                "nenhum export ativo na organização — o limite atingido pode "
                "ser outro (cota diária, por exemplo). Vale abrir com o suporte."
            )
            return

        conhecidos = self._journal.by_export_id()
        linhas = []
        for job in ativos:
            if job.export_id in in_flight:
                dono = "desta execução"
            elif job.export_id in conhecidos:
                dono = f"órfão deste projeto ({conhecidos[job.export_id].collection_date})"
            else:
                dono = "de fora deste import"
            progresso = f" {job.progress:.0f}%" if job.progress is not None else ""
            linhas.append(f"{job.export_id} {job.status}{progresso} — {dono}")
        logger.warning(
            f"PriceTrack: {len(ativos)}/"
            f"{self._client.settings.max_concurrent_exports} slot(s) de export "
            f"ocupados na organização: {'; '.join(linhas)}. A API não expõe "
            f"cancelamento — ou concluem, ou expiram do lado dela."
        )

    # ── polling ──────────────────────────────────────────────────────────

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
                        adopted=entry.adopted,
                    )
                continue

            entry.job = job
            self._heartbeat(entry, job, elapsed)

            if job.status == EXPORT_DONE:
                del in_flight[export_id]
                outcomes[idx] = self._finalize(entry, job)
            elif job.status == EXPORT_FAILED:
                del in_flight[export_id]
                logger.error(f"PriceTrack export {export_id}: FAILED")
                self._journal.forget(export_id)
                outcomes[idx] = ExportOutcome(
                    request=entry.request, status=OUTCOME_FAILED, job=job,
                    duration_seconds=elapsed, error="export FAILED na API",
                    adopted=entry.adopted,
                )
            elif elapsed > self._client.settings.poll_timeout_seconds:
                del in_flight[export_id]
                logger.error(
                    f"PriceTrack export {export_id}: timeout após {elapsed:.0f}s"
                )
                # A entrada do diário FICA: o export continua vivo do lado da
                # API segurando um slot, e é o diário que permite ao próximo
                # run reconhecê-lo — como dono do 429 — em vez de estranhá-lo.
                outcomes[idx] = ExportOutcome(
                    request=entry.request, status=OUTCOME_TIMEOUT, job=job,
                    duration_seconds=elapsed,
                    error=f"timeout após {elapsed:.0f}s",
                    adopted=entry.adopted,
                )

    def _heartbeat(self, entry: _InFlight, job: ExportJob, elapsed: float) -> None:
        """Sinal de vida do job em voo, em INFO.

        O progresso fino continua em DEBUG; o que sobe para INFO é mudança de
        status ou o batimento a cada ``_HEARTBEAT_SECONDS``. Sem isso o console
        só mostrava a linha do 429 e a execução parecia travada — foi assim que
        runs saudáveis foram mortos no meio, deixando exports órfãos.
        """
        now = self._clock()
        if job.progress is not None and job.progress != entry.last_progress:
            entry.last_progress = job.progress
            logger.debug(
                f"PriceTrack export {job.export_id}: {job.status} "
                f"({job.progress:.0f}%)"
            )
        mudou_status = job.status != entry.last_status
        if mudou_status or now - entry.last_log >= _HEARTBEAT_SECONDS:
            entry.last_status = job.status
            entry.last_log = now
            progresso = f" {job.progress:.0f}%" if job.progress is not None else ""
            logger.info(
                f"PriceTrack export {job.export_id} "
                f"({entry.request.collection_date}): {job.status}{progresso} "
                f"— {elapsed / 60:.0f}min em voo"
            )

    def _finalize(self, entry: _InFlight, job: ExportJob) -> ExportOutcome:
        elapsed = self._clock() - entry.submitted_at
        try:
            job = self._client.download_export(job, entry.dest)
        except PriceTrackError as e:
            logger.error(
                f"PriceTrack export {job.export_id}: download falhou — {e}"
            )
            # Entrada do diário PRESERVADA: o export está DONE do lado da API,
            # então a próxima execução baixa esse mesmo arquivo em vez de pagar
            # um export novo só porque a rede caiu no meio do download.
            return ExportOutcome(
                request=entry.request, status=OUTCOME_ERROR, job=job,
                duration_seconds=self._clock() - entry.submitted_at, error=str(e),
                adopted=entry.adopted,
            )
        logger.info(
            f"PriceTrack export {job.export_id}: concluído em {elapsed:.0f}s "
            f"(rowCount={job.row_count}, {job.file_size_bytes or 0} bytes)"
        )
        self._journal.forget(job.export_id)
        return ExportOutcome(
            request=entry.request, status=OUTCOME_OK, job=job,
            path=entry.dest, duration_seconds=self._clock() - entry.submitted_at,
            adopted=entry.adopted,
        )
