"""
Persistência no Supabase para o PriceTrack.

Suporta dois backends:

1. **psycopg2 (DSN direto)** — escolhido se `SUPABASE_DSN` estiver setado.
   Usa `execute_values` com `ON CONFLICT DO UPDATE` (rápido, devolve
   contagem real de inserts vs updates via flag `xmax`).

2. **supabase-py (REST)** — fallback automático quando `SUPABASE_DSN` está
   vazio mas `SUPABASE_URL` + `SUPABASE_KEY` estão. Reusa as credenciais
   que o restante do projeto já usa. A API REST não distingue insert de
   update no PostgREST, então devolvemos o total como `inserted` e
   `updated=0` por padrão (não é número exato, mas evita zerar tudo).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:  # pragma: no cover - dependência opcional
    psycopg2 = None  # type: ignore
    execute_values = None  # type: ignore


# Colunas inseridas (na ordem do tuple usado pelo psycopg2)
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

# Chaves que compõem o ON CONFLICT
_CONFLICT_KEYS = ("collection_date", "brand", "sku", "marketplace", "seller")

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


# --------------------------------------------------------------------------- #
# Repository — escolhe o backend conforme as env vars disponíveis
# --------------------------------------------------------------------------- #


def Repository(dsn: Optional[str] = None, batch_size: int = 1000):
    """
    Factory que devolve o backend apropriado:

    - Se `dsn` ou `SUPABASE_DSN` definido → `PsycopgRepository` (rápido,
      preciso, recomendado).
    - Senão, se `SUPABASE_URL` + `SUPABASE_KEY` definidos →
      `SupabasePyRepository` (REST, reusa credenciais do projeto).
    - Senão → RuntimeError com mensagem clara.
    """
    dsn = dsn or os.getenv("SUPABASE_DSN", "").strip() or None
    if dsn:
        return PsycopgRepository(dsn=dsn, batch_size=batch_size)

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if url and key:
        return SupabasePyRepository(url=url, key=key, batch_size=batch_size)

    raise RuntimeError(
        "Nenhuma credencial Supabase configurada. Defina ao menos um:\n"
        "  - SUPABASE_DSN  (psycopg2 direto, mais performático)\n"
        "  - SUPABASE_URL + SUPABASE_KEY  (REST via supabase-py, reusa o "
        "mesmo .env do restante do projeto)"
    )


# --------------------------------------------------------------------------- #
# Backend psycopg2 — preserva contagem exata de inserts vs updates
# --------------------------------------------------------------------------- #


class PsycopgRepository:
    """Backend psycopg2 direto via DSN. Devolve contagens exatas."""

    def __init__(self, dsn: str, batch_size: int = 1000) -> None:
        if psycopg2 is None:
            raise RuntimeError(
                "psycopg2 não está instalado. Rode `pip install psycopg2-binary`."
            )
        self.dsn = dsn
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
        if not rows:
            return UpsertResult()
        with conn.cursor() as cur:
            results = execute_values(cur, _UPSERT_SQL, rows, fetch=True)
        inserted = sum(1 for r in results if r[0])
        updated = len(results) - inserted
        return UpsertResult(inserted=inserted, updated=updated)

    def upsert_rows(self, rows: Iterable[Dict[str, Any]]) -> UpsertResult:
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


# --------------------------------------------------------------------------- #
# Backend supabase-py — usa REST (PostgREST) com on_conflict no .upsert()
# --------------------------------------------------------------------------- #


class SupabasePyRepository:
    """
    Backend que usa supabase-py (REST). Reusa SUPABASE_URL/KEY do projeto.

    Limitação: PostgREST não devolve flag de "foi insert ou update", então
    o `UpsertResult` traz `inserted = total_processado, updated = 0`. Isso
    é por design — não é dado real, mas evita métricas zeradas na UI. Para
    contagem exata, configure `SUPABASE_DSN`.
    """

    def __init__(self, url: str, key: str, batch_size: int = 1000) -> None:
        try:
            from supabase import create_client
        except ImportError as e:
            raise RuntimeError(
                "supabase-py não está instalado. Rode `pip install supabase`."
            ) from e
        self._client = create_client(url, key)
        self.batch_size = batch_size

    def _row_to_dict(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Filtra apenas as colunas conhecidas e ignora extras."""
        return {c: row.get(c) for c in _INSERT_COLUMNS}

    def upsert_rows(self, rows: Iterable[Dict[str, Any]]) -> UpsertResult:
        agg = UpsertResult()
        batch: List[Dict[str, Any]] = []
        for row in rows:
            batch.append(self._row_to_dict(row))
            if len(batch) >= self.batch_size:
                agg.merge(self._flush(batch))
                batch = []
        if batch:
            agg.merge(self._flush(batch))
        return agg

    def _flush(self, batch: List[Dict[str, Any]]) -> UpsertResult:
        if not batch:
            return UpsertResult()
        # PostgREST: on_conflict aceita lista de colunas separadas por vírgula
        on_conflict = ",".join(_CONFLICT_KEYS)
        res = (
            self._client.table("pricetrack_daily")
            .upsert(batch, on_conflict=on_conflict)
            .execute()
        )
        # supabase-py devolve as linhas afetadas em res.data — usamos como
        # proxy do total upsertado. Não conseguimos distinguir insert/update
        # pela REST, então atribuímos tudo a `inserted`.
        n = len(getattr(res, "data", None) or batch)
        return UpsertResult(inserted=n, updated=0)

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
        import json as _json

        rejection = (
            _json.loads(rejection_log_json) if rejection_log_json else []
        )
        payload = {
            "source_file": source_file,
            "import_started": started_iso,
            "import_finished": finished_iso,
            "rows_total": rows_total,
            "rows_inserted": rows_inserted,
            "rows_updated": rows_updated,
            "rows_rejected": rows_rejected,
            "rejection_log": rejection,
            "status": status,
        }
        try:
            res = (
                self._client.table("pricetrack_import_log").insert(payload).execute()
            )
            data = getattr(res, "data", None)
            if data and isinstance(data, list) and data:
                return data[0].get("id")
        except Exception:
            # Não falhar a importação por causa do log
            pass
        return None

    def file_already_imported(self, source_file: str, expected_rows: int) -> bool:
        try:
            res = (
                self._client.table("pricetrack_import_log")
                .select("rows_inserted,rows_updated")
                .eq("source_file", source_file)
                .eq("status", "SUCCESS")
                .order("import_finished", desc=True)
                .limit(1)
                .execute()
            )
            data = getattr(res, "data", None) or []
            if not data:
                return False
            r = data[0]
            return (
                (r.get("rows_inserted") or 0) + (r.get("rows_updated") or 0)
                >= expected_rows
            )
        except Exception:
            return False


INSERT_COLUMNS = _INSERT_COLUMNS
