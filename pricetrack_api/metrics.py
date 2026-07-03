"""
Observabilidade da coleta PriceTrack: métricas estruturadas + alertas.

``CollectionMetrics`` acumula contadores durante a coleta (linhas, cobertura
por marketplace/marca, tempo de export) e emite um log estruturado ao final
(loguru ``bind``, serializável no sink JSON). Falhas disparam um ``AlertSink``
— por padrão log de erro; com Telegram configurado (TELEGRAM_BOT_TOKEN +
N8N_TELEGRAM_CHAT_ID), reusa o notificador do projeto.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol

from loguru import logger

STATUS_SUCCESS = "SUCCESS"
STATUS_NO_DATA = "NO_DATA"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"


@dataclass(slots=True)
class CollectionMetrics:
    """Métricas de uma coleta (um dataset × uma collectionDate)."""

    dataset: str
    collection_date: str
    strategy: str = ""                 # paginated | export
    status: str = STATUS_SUCCESS
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    rows_fetched: int = 0              # linhas lidas da API
    rows_stored_new: int = 0           # novas na partição (dedup por id)
    rows_updated: int = 0              # ids reprocessados (último vence)
    rows_invalid: int = 0              # linhas NDJSON ilegíveis
    rows_filtered_out: int = 0         # descartadas por filtro client-side
    pages_fetched: int = 0             # páginas (estratégia paginada)

    export_duration_seconds: Optional[float] = None
    export_row_count: Optional[int] = None
    export_file_size_bytes: Optional[int] = None

    by_marketplace: Counter = field(default_factory=Counter)
    by_brand: Counter = field(default_factory=Counter)
    errors: list = field(default_factory=list)

    # ── acumulação ───────────────────────────────────────────────────────

    def observe(self, marketplace: str, brand: str) -> None:
        """Registra uma linha coletada na cobertura por marketplace/marca."""
        self.rows_fetched += 1
        if marketplace:
            self.by_marketplace[marketplace] += 1
        if brand:
            self.by_brand[brand] += 1

    def fail(self, error: str) -> None:
        self.status = STATUS_FAILED
        self.errors.append(error)

    def finish(self, status: Optional[str] = None) -> None:
        self.finished_at = time.time()
        if status:
            self.status = status

    # ── saída ────────────────────────────────────────────────────────────

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 2)

    def to_dict(self) -> Dict:
        return {
            "dataset": self.dataset,
            "collection_date": self.collection_date,
            "strategy": self.strategy,
            "status": self.status,
            "duration_seconds": self.duration_seconds,
            "rows_fetched": self.rows_fetched,
            "rows_stored_new": self.rows_stored_new,
            "rows_updated": self.rows_updated,
            "rows_invalid": self.rows_invalid,
            "rows_filtered_out": self.rows_filtered_out,
            "pages_fetched": self.pages_fetched,
            "export_duration_seconds": self.export_duration_seconds,
            "export_row_count": self.export_row_count,
            "export_file_size_bytes": self.export_file_size_bytes,
            "coverage_marketplaces": dict(self.by_marketplace.most_common()),
            "coverage_brands": dict(self.by_brand.most_common(20)),
            "errors": list(self.errors),
        }

    def log(self) -> None:
        """Emite o resumo como log estruturado (extra= vai no sink JSON)."""
        payload = self.to_dict()
        line = (
            f"Coleta PriceTrack {self.dataset} {self.collection_date} "
            f"[{self.strategy or '-'}] → {self.status} — "
            f"{self.rows_fetched:,} linha(s) em {self.duration_seconds:.0f}s, "
            f"{len(self.by_marketplace)} marketplace(s), "
            f"{len(self.by_brand)} marca(s)"
        )
        bound = logger.bind(pricetrack_metrics=payload)
        if self.status == STATUS_FAILED:
            bound.error(line)
        elif self.status in (STATUS_NO_DATA, STATUS_PARTIAL):
            bound.warning(line)
        else:
            bound.success(line)


# ── Alertas ──────────────────────────────────────────────────────────────────


class AlertSink(Protocol):
    """Destino de alertas de falha de coleta."""

    def send(self, subject: str, message: str) -> bool: ...


class LogAlertSink:
    """Fallback sempre disponível: alerta vira log de erro."""

    def send(self, subject: str, message: str) -> bool:
        logger.error(f"[ALERTA] {subject}\n{message}")
        return True


class TelegramAlertSink:
    """Envia o alerta via Telegram, reusando o notificador do projeto.

    Requer TELEGRAM_BOT_TOKEN + N8N_TELEGRAM_CHAT_ID no ambiente. Sem eles
    (ou sem o módulo), degrada silenciosamente para log.
    """

    def send(self, subject: str, message: str) -> bool:
        try:
            from utils.n8n_notify import _send_direct_telegram
        except ImportError:
            return LogAlertSink().send(subject, message)
        # parse_mode=HTML: escapa o conteúdo (erros podem conter <>&)
        import html
        text = f"🚨 <b>{html.escape(subject)}</b>\n\n{html.escape(message)}"
        sent = _send_direct_telegram(text)
        if not sent:
            return LogAlertSink().send(subject, message)
        return True


def alert_if_failed(metrics: CollectionMetrics,
                    sink: Optional[AlertSink] = None) -> None:
    """Dispara alerta quando a coleta terminou FAILED."""
    if metrics.status != STATUS_FAILED:
        return
    sink = sink or LogAlertSink()
    errors = "\n".join(f"• {e}" for e in metrics.errors[-5:]) or "sem detalhe"
    sink.send(
        subject=(
            f"Coleta PriceTrack FALHOU — {metrics.dataset} "
            f"{metrics.collection_date}"
        ),
        message=(
            f"Estratégia: {metrics.strategy or '-'}\n"
            f"Linhas coletadas antes da falha: {metrics.rows_fetched:,}\n"
            f"Erros:\n{errors}"
        ),
    )
