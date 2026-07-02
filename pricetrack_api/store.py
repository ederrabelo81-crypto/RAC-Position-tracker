"""
NdjsonStore — camada raw local, particionada por collectionDate.

Layout em disco (uma partição por dia, por dataset):

    {root}/{dataset}/collection_date=YYYY-MM-DD/data.ndjson.gz
    {root}/{dataset}/collection_date=YYYY-MM-DD/manifest.json

Garantias:
  * Idempotência: reprocessar o mesmo dia N vezes converge para o mesmo
    conteúdo — o upsert deduplica pelo ``id`` da oferta (último snapshot
    vence) e a escrita é atômica (tmp + ``os.replace``).
  * Nunca perde dados de dias com múltiplas coletas: cada ``collectionHour``
    gera ofertas com ``id`` próprio, então a união por ``id`` preserva todas
    as passadas do dia.
  * O manifest registra contagem, horas de coleta vistas e fontes — base
    para métricas e auditoria.
"""
from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

from loguru import logger

from .models import pick, record_id, to_date, to_hour


@dataclass(frozen=True, slots=True)
class UpsertStats:
    """Resultado de um upsert na partição."""

    received: int        # registros recebidos neste lote
    new: int             # ids que não existiam na partição
    updated: int         # ids já existentes, sobrescritos (último vence)
    total: int           # total na partição após o upsert


class NdjsonStore:
    """Armazena registros crus (dicts) deduplicados por ``id``."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    # ── Caminhos ─────────────────────────────────────────────────────────

    def partition_dir(self, dataset: str, collection_date: date | str) -> Path:
        cd = _iso(collection_date)
        return self.root / dataset / f"collection_date={cd}"

    def data_path(self, dataset: str, collection_date: date | str) -> Path:
        return self.partition_dir(dataset, collection_date) / "data.ndjson.gz"

    def manifest_path(self, dataset: str, collection_date: date | str) -> Path:
        return self.partition_dir(dataset, collection_date) / "manifest.json"

    # ── Leitura ──────────────────────────────────────────────────────────

    def read(self, dataset: str, collection_date: date | str) -> Iterator[Dict[str, Any]]:
        """Itera os registros crus da partição (vazio se não existe)."""
        path = self.data_path(dataset, collection_date)
        if not path.exists():
            return
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def count(self, dataset: str, collection_date: date | str) -> int:
        manifest = self.manifest(dataset, collection_date)
        if manifest is not None:
            return int(manifest.get("row_count", 0))
        return sum(1 for _ in self.read(dataset, collection_date))

    def manifest(self, dataset: str, collection_date: date | str) -> Optional[Dict]:
        path = self.manifest_path(dataset, collection_date)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # ── Escrita ──────────────────────────────────────────────────────────

    def upsert(
        self,
        dataset: str,
        collection_date: date | str,
        records: Iterable[Dict[str, Any]],
        source: str = "",
    ) -> UpsertStats:
        """Mescla ``records`` na partição, deduplicando por ``id``.

        Registro com ``id`` já presente substitui o anterior (último snapshot
        vence — relevante para o dia corrente, cujo export cresce ao longo do
        dia). Registros de horas distintas têm ids distintos e coexistem.
        """
        cd = _iso(collection_date)
        existing: Dict[str, Dict[str, Any]] = {
            record_id(raw): raw for raw in self.read(dataset, cd)
        }
        total_before = len(existing)

        received = new = updated = 0
        for raw in records:
            received += 1
            key = record_id(raw)
            if key in existing:
                updated += 1
            else:
                new += 1
            existing[key] = raw

        if received == 0 and total_before == 0:
            return UpsertStats(received=0, new=0, updated=0, total=0)

        self._write_atomic(dataset, cd, existing.values())
        self._write_manifest(dataset, cd, existing, source)

        stats = UpsertStats(
            received=received, new=new, updated=updated, total=len(existing)
        )
        logger.info(
            f"Store {dataset}/{cd}: upsert de {received:,} registro(s) — "
            f"{new:,} novo(s), {updated:,} atualizado(s), "
            f"{stats.total:,} total na partição"
        )
        return stats

    def _write_atomic(self, dataset: str, cd: str,
                      records: Iterable[Dict[str, Any]]) -> None:
        dest = self.data_path(dataset, cd)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(f".tmp-{os.getpid()}")
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                for raw in records:
                    fh.write(json.dumps(raw, ensure_ascii=False, default=str))
                    fh.write("\n")
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def _write_manifest(self, dataset: str, cd: str,
                        by_id: Dict[str, Dict[str, Any]], source: str) -> None:
        hours = sorted({
            h for raw in by_id.values()
            if (h := to_hour(pick(raw, "collectionHour"))) is not None
        })
        previous = self.manifest(dataset, cd) or {}
        sources = list(dict.fromkeys(previous.get("sources", []) + ([source] if source else [])))
        manifest = {
            "dataset": dataset,
            "collection_date": cd,
            "row_count": len(by_id),
            "collection_hours": hours,
            "sources": sources,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self.manifest_path(dataset, cd)
        tmp = path.with_suffix(f".tmp-{os.getpid()}")
        tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)


def _iso(collection_date: date | str) -> str:
    cd = to_date(collection_date)
    if cd is None:
        raise ValueError(f"collection_date inválida: {collection_date!r}")
    return cd.isoformat()
