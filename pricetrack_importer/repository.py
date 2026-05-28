"""
Persistência no Supabase via psycopg2.

Usa `execute_values` para batch insert/upsert (1000 linhas por batch), com
`ON CONFLICT (collection_date, brand, sku, marketplace, seller) DO UPDATE`
para garantir idempotência em reimports.

Conexão via DSN no .env (`SUPABASE_DSN`).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:  # pragma: no cover - dependência opcional em testes
    psycopg2 = None  # type: ignore
    execute_values = None  # type: ignore


# Colunas inseridas no INSERT (na ordem do tuple)
_INSERT_COLUMNS = (
    "collection_date",
    "brand",
    "sku",
    "title",
    "marketplace",
    "seller",
    "seller_canonical",
    "min_price",
    "avg_price",
    "mode_price",
    "max_price",
    "source_file",
)

_UPSERT_SQL = """
INSERT INTO pricetrack_daily
    (collection_date, brand, sku, title, marketplace, seller, seller_canonical,
     min_price, avg_price, mode_price, max_price, source_file)
VALUES %s
ON CONFLICT (collection_date, brand, sku, marketplace, seller)
DO UPDATE SET
    title = EXCLUDED.title,
    seller_canonical = EXCLUDED.seller_canonical,
    min_price = EXCLUDED.min_price,
    avg_price = EXCLUDED.avg_price,
    mode_price = EXCLUDED.mode_price,
    max_price = EXCLUDED.max_price,
    source_file = EXCLUDED.source_file,
    imported_at = NOW()
RETURNING (xmax = 0) AS inserted;
"""

_IMPORT_LOG_SQL = """
INSERT INTO pricetrack_import_log
    (source_file, import_started, import_finished, rows_total,
     rows_inserted, rows_updated, rows_rejected, rejection_log, status)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
RETURNING id;
"""


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0

    def merge(self, other: "UpsertResult") -> None:
        self.inserted += other.inserted
        self.updated += other.updated


class Repository:
    """Wrapper psycopg2 para a tabela `pricetrack_daily`."""

    def __init__(self, dsn: Optional[str] = None, batch_size: int = 1000) -> None:
        self.dsn = dsn or os.getenv("SUPABASE_DSN")
        if not self.dsn:
            raise RuntimeError(
                "SUPABASE_DSN não configurado. Defina via .env ou variável de ambiente."
            )
        if psycopg2 is None:
            raise RuntimeError(
                "psycopg2 não está instalado. Rode `pip install psycopg2-binary`."
            )
        self.batch_size = batch_size

    @contextmanager
    def connect(self) -> Iterator[Any]:
        conn = psycopg2.connect(self.dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_batch(
        self, conn: Any, rows: Sequence[Tuple[Any, ...]]
    ) -> UpsertResult:
        """
        Faz upsert de um batch. Devolve contagem de inserts vs updates via
        flag `xmax = 0` (true = INSERT, false = UPDATE).
        """
        if not rows:
            return UpsertResult()
        with conn.cursor() as cur:
            results = execute_values(cur, _UPSERT_SQL, rows, fetch=True)
        inserted = sum(1 for r in results if r[0])
        updated = len(results) - inserted
        return UpsertResult(inserted=inserted, updated=updated)

    def upsert_rows(self, rows: Iterable[Dict[str, Any]]) -> UpsertResult:
        """
        Faz upsert idempotente de uma sequência de dicts em batches.

        Cada dict deve conter exatamente as chaves de `_INSERT_COLUMNS`.
        """
        agg = UpsertResult()
        with self.connect() as conn:
            batch: List[Tuple[Any, ...]] = []
            for row in rows:
                batch.append(tuple(row[c] for c in _INSERT_COLUMNS))
                if len(batch) >= self.batch_size:
                    agg.merge(self.upsert_batch(conn, batch))
                    batch = []
            if batch:
                agg.merge(self.upsert_batch(conn, batch))
        return agg

    def write_import_log(
        self,
        *,
        source_file: str,
        started_iso: str,
        finished_iso: str,
        rows_total: int,
        rows_inserted: int,
        rows_updated: int,
        rows_rejected: int,
        rejection_log_json: str,
        status: str,
    ) -> Optional[int]:
        """Persiste o resumo da execução em `pricetrack_import_log`."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _IMPORT_LOG_SQL,
                    (
                        source_file,
                        started_iso,
                        finished_iso,
                        rows_total,
                        rows_inserted,
                        rows_updated,
                        rows_rejected,
                        rejection_log_json,
                        status,
                    ),
                )
                result = cur.fetchone()
                return result[0] if result else None

    def file_already_imported(self, source_file: str, expected_rows: int) -> bool:
        """
        Retorna True se o arquivo já foi importado com status SUCCESS e
        número de linhas inseridas+updated >= expected_rows.

        Usado pelo modo `--dir` pra pular arquivos já processados.
        """
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rows_inserted + rows_updated
                    FROM pricetrack_import_log
                    WHERE source_file = %s AND status = 'SUCCESS'
                    ORDER BY import_finished DESC
                    LIMIT 1
                    """,
                    (source_file,),
                )
                row = cur.fetchone()
        return row is not None and row[0] >= expected_rows


INSERT_COLUMNS = _INSERT_COLUMNS
