"""
CLI do importador PriceTrack.

Uso:
    python -m pricetrack_importer arquivo.md
    python -m pricetrack_importer --dir imports/pricetrack/
    python -m pricetrack_importer arquivo.md --dry-run
    python -m pricetrack_importer arquivo.md --force
    python -m pricetrack_importer arquivo.md --verbose
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from dotenv import load_dotenv

from . import seller_map
from .logger import (
    ExecutionLog,
    create_execution_log,
    log,
    now_brt_iso,
    write_execution_log,
)
from .normalizer import (
    iso_date,
    normalize_text,
    parse_decimal,
    parse_pricetrack_date,
)
from .parser import parse_file
from .seller_map import normalize_seller
from .validator import validate_row


SUPPORTED_EXT = {".md", ".xlsx", ".xlsm"}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pricetrack_importer",
        description="Importa exports diários do PriceTrack para o Supabase.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("file", nargs="?", help="Arquivo .md ou .xlsx a importar.")
    g.add_argument(
        "--dir",
        dest="directory",
        help="Diretório com múltiplos arquivos a importar (modo batch idempotente).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parseia e valida mas NÃO escreve no DB.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Reimporta mesmo se o arquivo já tiver sido processado.",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Log debug no stderr.")
    p.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("PRICETRACK_BATCH_SIZE", "1000")),
        help="Tamanho do batch de upsert (default 1000).",
    )
    return p


def _setup_logging(verbose: bool) -> None:
    log.remove()
    level = "DEBUG" if verbose else "INFO"
    log.add(sys.stderr, level=level, format="<level>{level: <8}</level> | {message}")


def _process_rows(
    raw_rows: Iterable[Dict[str, str]],
    source_file: str,
    exec_log: ExecutionLog,
) -> List[Dict[str, object]]:
    """
    Aplica validação e normalização. Devolve lista de dicts prontos pro
    `Repository.upsert_rows`.
    """
    normalized: List[Dict[str, object]] = []

    for row in raw_rows:
        exec_log.rows.total_parsed += 1
        line_no_raw = row.get("_line_no", "0")
        try:
            line_no = int(line_no_raw)
        except (TypeError, ValueError):
            line_no = 0

        # Cabeçalho / linha não-parseável da tabela → metadata silenciosa
        if row.get("_is_header") or row.get("_unparseable"):
            exec_log.rows.metadata_skipped += 1
            continue

        result = validate_row(row)
        if not result.valid:
            if result.reason == "METADATA":
                exec_log.rows.metadata_skipped += 1
            elif result.reason == "INVALID_SELLER":
                exec_log.rows.invalid_seller += 1
                exec_log.add_rejection(
                    line=line_no,
                    reason="INVALID_SELLER",
                    seller_raw=row.get("seller", ""),
                    detail=result.detail or "",
                )
            else:
                exec_log.rows.invalid_other += 1
                exec_log.add_rejection(
                    line=line_no,
                    reason=result.reason or "UNKNOWN",
                    detail=result.detail or "",
                )
            continue

        coll_date = parse_pricetrack_date(row["collectionDate"])
        if coll_date is None:
            exec_log.rows.invalid_other += 1
            exec_log.add_rejection(
                line=line_no, reason="INVALID_DATE", value=row.get("collectionDate", "")
            )
            continue

        seller_raw = normalize_text(row.get("seller", ""))
        normalized.append(
            {
                "collection_date": iso_date(coll_date),
                "brand": normalize_text(row.get("brand", "")).upper(),
                "sku": normalize_text(row.get("sku", "")),
                "title": normalize_text(row.get("title", "")),
                "marketplace": normalize_text(row.get("marketplace", "")).upper(),
                "seller": seller_raw,
                "seller_canonical": normalize_seller(seller_raw),
                "min_price": parse_decimal(row.get("MIN PRICE", "")),
                "avg_price": parse_decimal(row.get("AVG PRICE", "")),
                "mode_price": parse_decimal(row.get("MODE PRICE", "")),
                "max_price": parse_decimal(row.get("MAX PRICE", "")),
                "source_file": source_file,
            }
        )
        exec_log.rows.valid += 1

    return normalized


def _process_one(
    path: Path,
    *,
    dry_run: bool,
    force: bool,
    batch_size: int,
    log_dir: Path,
) -> ExecutionLog:
    """Processa um arquivo e devolve o ExecutionLog."""
    source_file = str(path)
    exec_log = create_execution_log(source_file)
    log.info(f"[{exec_log.execution_id}] Iniciando importação: {source_file}")

    seller_map.set_unknown_sellers_log_path(log_dir / "unknown_sellers.log")
    unknown_before = _count_lines(log_dir / "unknown_sellers.log")

    try:
        raw_rows = list(parse_file(path))
        normalized = _process_rows(raw_rows, source_file, exec_log)

        if dry_run:
            log.info(
                f"DRY-RUN: parseou={exec_log.rows.total_parsed} "
                f"validas={exec_log.rows.valid} rejeitadas={exec_log.rows.invalid_seller + exec_log.rows.invalid_other}"
            )
            exec_log.rows.rejected = (
                exec_log.rows.invalid_seller + exec_log.rows.invalid_other
            )
            exec_log.finalize(status="SUCCESS")
        else:
            from .repository import Repository

            repo = Repository(batch_size=batch_size)

            if not force:
                if repo.file_already_imported(source_file, len(normalized)):
                    log.info(
                        f"Arquivo {source_file} já importado anteriormente — pulando "
                        f"(use --force para reimportar)."
                    )
                    exec_log.finalize(status="SUCCESS")
                    exec_log.error = "ALREADY_IMPORTED_SKIPPED"
                    return exec_log

            res = repo.upsert_rows(normalized)
            exec_log.rows.inserted = res.inserted
            exec_log.rows.updated = res.updated
            exec_log.rows.rejected = (
                exec_log.rows.invalid_seller + exec_log.rows.invalid_other
            )
            exec_log.finalize(status="SUCCESS")

            repo.write_import_log(
                source_file=source_file,
                started_iso=exec_log.started_at,
                finished_iso=exec_log.finished_at or now_brt_iso(),
                rows_total=exec_log.rows.total_parsed,
                rows_inserted=res.inserted,
                rows_updated=res.updated,
                rows_rejected=exec_log.rows.rejected,
                rejection_log_json=json.dumps(
                    exec_log.rejection_samples, ensure_ascii=False
                ),
                status="SUCCESS",
            )

    except Exception as e:  # noqa: BLE001 — top-level, registra e segue
        log.exception(f"Falha importando {source_file}: {e}")
        exec_log.finalize(status="FAILED", error=str(e))

    unknown_after = _count_lines(log_dir / "unknown_sellers.log")
    exec_log.unknown_sellers_count = max(0, unknown_after - unknown_before)

    out_path = write_execution_log(exec_log, log_dir)
    log.info(
        f"[{exec_log.execution_id}] {exec_log.status} | "
        f"inseridas={exec_log.rows.inserted} atualizadas={exec_log.rows.updated} "
        f"rejeitadas={exec_log.rows.rejected} | log={out_path}"
    )
    return exec_log


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _discover_files(directory: Path) -> List[Path]:
    files = []
    for ext in SUPPORTED_EXT:
        files.extend(directory.glob(f"*{ext}"))
    return sorted(files)


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv()
    args = _build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    log_dir = Path(os.getenv("PRICETRACK_LOG_DIR", "./logs/pricetrack"))
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.directory:
        directory = Path(args.directory)
        if not directory.is_dir():
            log.error(f"Diretório não encontrado: {directory}")
            return 2
        files = _discover_files(directory)
        if not files:
            log.warning(f"Nenhum arquivo .md/.xlsx em {directory}")
            return 0
        log.info(f"Encontrados {len(files)} arquivos em {directory}")
        any_failed = False
        for f in files:
            res = _process_one(
                f,
                dry_run=args.dry_run,
                force=args.force,
                batch_size=args.batch_size,
                log_dir=log_dir,
            )
            if res.status == "FAILED":
                any_failed = True
        return 1 if any_failed else 0

    path = Path(args.file)
    if not path.exists():
        log.error(f"Arquivo não encontrado: {path}")
        return 2

    res = _process_one(
        path,
        dry_run=args.dry_run,
        force=args.force,
        batch_size=args.batch_size,
        log_dir=log_dir,
    )
    return 0 if res.status == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
