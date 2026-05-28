"""
Logger estruturado em JSON para execuções do importador PriceTrack.

Cada execução gera um arquivo `logs/pricetrack/YYYY-MM-DD_HHMMSS.json`
com contadores, amostras de rejeição e status final. O logger também
emite eventos textuais via loguru para acompanhamento no terminal.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger as _loguru

# Timezone fixo: America/Sao_Paulo (BRT, UTC-3, sem horário de verão desde 2019)
BRT = timezone(timedelta(hours=-3), name="America/Sao_Paulo")


def now_brt() -> datetime:
    """Devolve datetime atual em America/Sao_Paulo."""
    return datetime.now(BRT)


def now_brt_iso() -> str:
    """ISO 8601 com timezone BRT (ex: 2026-05-27T14:30:22-03:00)."""
    return now_brt().isoformat(timespec="seconds")


@dataclass
class RowCounters:
    total_parsed: int = 0
    metadata_skipped: int = 0
    invalid_seller: int = 0
    invalid_other: int = 0
    valid: int = 0
    inserted: int = 0
    updated: int = 0
    rejected: int = 0


@dataclass
class ExecutionLog:
    """Coleta métricas de uma execução do importador."""

    execution_id: str
    source_file: str
    started_at: str
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    rows: RowCounters = field(default_factory=RowCounters)
    rejection_samples: List[Dict[str, Any]] = field(default_factory=list)
    unknown_sellers_count: int = 0
    status: str = "RUNNING"
    error: Optional[str] = None

    _start_dt: datetime = field(default_factory=now_brt, repr=False)

    def add_rejection(self, line: int, reason: str, **extra: Any) -> None:
        """Adiciona uma amostra de rejeição ao log (limita a 50 amostras)."""
        if len(self.rejection_samples) < 50:
            sample: Dict[str, Any] = {"line": line, "reason": reason}
            sample.update(extra)
            self.rejection_samples.append(sample)

    def finalize(self, status: str = "SUCCESS", error: Optional[str] = None) -> None:
        end = now_brt()
        self.finished_at = end.isoformat(timespec="seconds")
        self.duration_seconds = round((end - self._start_dt).total_seconds(), 2)
        self.status = status
        if error:
            self.error = error

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("_start_dt", None)
        return d


def create_execution_log(source_file: str) -> ExecutionLog:
    """Cria um ExecutionLog com execution_id baseado em timestamp BRT."""
    start = now_brt()
    exec_id = start.strftime("%Y-%m-%d_%H%M%S")
    return ExecutionLog(
        execution_id=exec_id,
        source_file=source_file,
        started_at=start.isoformat(timespec="seconds"),
    )


def write_execution_log(log: ExecutionLog, log_dir: str | Path) -> Path:
    """Persiste o ExecutionLog em disco como JSON."""
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    out_path = log_dir_path / f"{log.execution_id}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(log.to_dict(), fh, ensure_ascii=False, indent=2)
    return out_path


# Re-exporta o loguru pra ser usado nos outros módulos
log = _loguru
