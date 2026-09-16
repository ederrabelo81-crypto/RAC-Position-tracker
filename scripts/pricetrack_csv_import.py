#!/usr/bin/env python3
"""
pricetrack_csv_import.py — Importação do PriceTrack via export MANUAL (CSV do painel).

Complementa `scripts/pricetrack_api_import.py` para os dias em que o export
assíncrono da API está indisponível (fila de exports travada por horas/dias no
lado do PriceTrack, sem endpoint de cancelamento — ver `pricetrack_api/exports.py`).

O CSV "collects" baixado à mão do painel (pricetrack.com.br) já vem em
granularidade por OFERTA, com Preço À Vista e Preço Pix em colunas separadas —
o mesmo nível de detalhe do NDJSON da API. Por isso este script reaproveita
literalmente o pipeline de `pricetrack_api_import.py`
(`aggregate_offers`/`insert_rows`/`purge_stale_basis`/`log_import`/
`write_history`) em vez de duplicá-lo: o resultado grava `price_basis`
`best_cash` (menor entre à vista e PIX), exatamente como o caminho da API.

**Por que não usar `pricetrack_importer/` (o importador manual mais antigo):**
aquele pacote espera um schema JÁ agregado (MIN/AVG/MODE/MAX PRICE, sem PIX) e
não popula `turno`/`price_basis`/`spot_min_price`/`pix_min_price` — a linha
cairia em `price_basis=spot_legacy`, reintroduzindo o mesmo viés de ~10% (preço
sem o desconto do PIX) que a migração 006 existe para corrigir. Este script
evita isso lendo o export por-oferta e passando pela MESMA agregação da API.

Uso:
    python scripts/pricetrack_csv_import.py --file export.csv
    python scripts/pricetrack_csv_import.py --dir imports\\pricetrack\\manual
    python scripts/pricetrack_csv_import.py --file export.csv --dry-run
    python scripts/pricetrack_csv_import.py --file export.csv --force
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

import pandas as pd
from loguru import logger

from pricetrack_api_import import (
    DEFAULT_CATEGORIES,
    aggregate_offers,
    date_exists,
    insert_rows,
    log_import,
    purge_stale_basis,
    write_history,
)

# Colunas do export "collects" do painel do PriceTrack (pricetrack.com.br).
_COL_DATA = "Data de Coleta"
_COL_HORA_EXECUCAO = "Hora de Execução"


def _extract_hour(value: object) -> Optional[int]:
    """'05:22' -> 5. O export não traz um `collection_hour` numérico pronto —
    só o horário de execução em texto ('HH:MM'). Valor ilegível vira None: a
    oferta some do recorte Manhã/Tarde mas continua no agregado Diário (mesmo
    comportamento de hora ausente no caminho da API)."""
    try:
        return int(str(value).split(":")[0])
    except (ValueError, IndexError, AttributeError):
        return None


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Deriva `collection_hour` (nome que `aggregate_offers` já reconhece via
    `_HOUR_FIELDS`) a partir de `Hora de Execução`."""
    df = df.copy()
    if _COL_HORA_EXECUCAO in df.columns:
        df["collection_hour"] = df[_COL_HORA_EXECUCAO].apply(_extract_hour)
    return df


def process_csv(
    path: Path,
    dry_run: bool = False,
    force: bool = False,
    categories: Optional[List[str]] = None,
) -> None:
    """Importa um CSV manual: um bloco por `collection_date` presente nele
    (normalmente um só, mas um export que junte vários dias é suportado)."""
    logger.info(f"Lendo {path} ...")
    df_raw = pd.read_csv(path)
    if df_raw.empty:
        logger.warning(f"{path} — arquivo vazio, nada a importar")
        return
    if _COL_DATA not in df_raw.columns:
        logger.error(
            f"{path} — coluna '{_COL_DATA}' não encontrada. Este não parece "
            f"ser um export \"collects\" do painel do PriceTrack. Colunas "
            f"presentes: {list(df_raw.columns)}"
        )
        return

    df_raw = _prepare(df_raw)

    for collection_date, group in df_raw.groupby(_COL_DATA):
        collection_date = str(collection_date)
        if not force and date_exists(collection_date, dry_run=dry_run):
            logger.info(
                f"{collection_date} — já está no banco, pulando "
                f"(use --force para reimportar)"
            )
            continue

        rows_raw = len(group)
        logger.info(f"{collection_date} — {rows_raw:,} ofertas brutas (de {path.name})")

        df_agg, rejections = aggregate_offers(group, collection_date, categories=categories)
        rejection_log = [
            {"reason": reason, "rows": count} for reason, count in sorted(rejections.items())
        ]

        if df_agg.empty:
            logger.warning(f"{collection_date} — zero linhas após filtro+agregação")
            log_import(
                f"csv-{path.name}", rows_raw, 0, rows_raw, "PARTIAL",
                rejection_log=rejection_log, dry_run=dry_run,
            )
            continue

        rows_rejected = int(
            sum(v for k, v in rejections.items() if k != "FORWARD_PRICE_ONLY")
        )
        logger.info(
            f"{collection_date} — {len(df_agg):,} linhas AC agregadas "
            f"({rows_rejected:,} ofertas cruas descartadas pelos filtros)"
        )

        records = df_agg.where(pd.notnull(df_agg), None).to_dict("records")
        for r in records:
            for k in ("min_price", "avg_price", "mode_price", "max_price",
                      "last_price", "spot_min_price", "pix_min_price"):
                v = r.get(k)
                r[k] = None if v is None or pd.isna(v) else round(float(v), 2)
            for k in ("last_hour", "obs_count", "unavailable_count"):
                v = r.get(k)
                r[k] = None if v is None or pd.isna(v) else int(v)
            r["source_file"] = f"csv-{path.name}"

        write_history(records, collection_date, dry_run=dry_run)
        inserted = insert_rows(records, dry_run=dry_run)
        if inserted >= len(records):
            purge_stale_basis(collection_date, dry_run=dry_run)

        log_import(
            source_file=f"csv-{path.name}",
            rows_total=rows_raw,
            rows_inserted=inserted,
            rows_rejected=rows_rejected,
            status="SUCCESS" if inserted > 0 else "PARTIAL",
            rejection_log=rejection_log,
            dry_run=dry_run,
        )
        logger.success(f"{collection_date} — {inserted:,} linhas inseridas (via CSV manual)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa export manual (CSV) do painel do PriceTrack"
    )
    parser.add_argument("--file", type=Path, help="Caminho de um CSV")
    parser.add_argument("--dir", type=Path, help="Diretório com vários CSVs (*.csv)")
    parser.add_argument("--dry-run", action="store_true", help="Simula sem gravar nada")
    parser.add_argument(
        "--force", action="store_true",
        help="Reimporta datas já presentes no banco (troca spot_legacy por best_cash)",
    )
    parser.add_argument(
        "--categories", nargs="+", default=DEFAULT_CATEGORIES,
        help=f"Categorias a importar. Padrão: {DEFAULT_CATEGORIES}",
    )
    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.error("informe --file ou --dir")

    logger.remove()
    logger.add(
        sys.stderr, level="INFO", colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )

    categories = [c.upper().strip() for c in args.categories]
    files = [args.file] if args.file else sorted(Path(args.dir).glob("*.csv"))
    if not files:
        logger.error(f"Nenhum CSV encontrado em {args.dir}")
        sys.exit(1)

    for f in files:
        process_csv(f, dry_run=args.dry_run, force=args.force, categories=categories)


if __name__ == "__main__":
    main()
