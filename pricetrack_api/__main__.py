"""
CLI do pricetrack_api.

    # Sonda o volume de um dia (decisão paginado × export)
    python -m pricetrack_api probe --date 2026-07-01

    # Coleta ofertas de um dia para a partição local (estratégia automática)
    python -m pricetrack_api collect --date 2026-07-01

    # Força estratégia e filtra marketplaces
    python -m pricetrack_api collect --date 2026-07-01 \
        --strategy export --marketplace "MERCADO LIVRE" AMAZON

    # Fretes
    python -m pricetrack_api collect --date 2026-07-01 --dataset shipping

    # Lista exports da organização (data[0] = mais recente)
    python -m pricetrack_api exports
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Roda também de dentro do repo sem instalar o pacote
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from loguru import logger

from pricetrack_api import (
    CollectQuery,
    PriceTrackClient,
    PriceTrackError,
    PriceTrackNoCollectionError,
    PriceTrackSettings,
    SmartCollector,
    TelegramAlertSink,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pricetrack_api",
        description="Coleta de ofertas/fretes da API PriceTrack",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="Conta as linhas de um dia (1 chamada)")
    probe.add_argument("--date", required=True, help="collectionDate YYYY-MM-DD")
    probe.add_argument("--dataset", choices=["offers", "shipping"],
                       default="offers")

    collect = sub.add_parser("collect", help="Coleta um dia para a partição local")
    collect.add_argument("--date", required=True, help="collectionDate YYYY-MM-DD")
    collect.add_argument("--dataset", choices=["offers", "shipping"],
                         default="offers")
    collect.add_argument("--strategy", choices=["auto", "paginated", "export"],
                         default="auto")
    collect.add_argument("--marketplace", nargs="+", default=None,
                         metavar="NOME", help="Filtro de marketplaces")
    collect.add_argument("--brand", nargs="+", default=None,
                         metavar="MARCA", help="Filtro de marcas (productBrand)")
    collect.add_argument("--status", choices=["AVAILABLE", "UNAVAILABLE"],
                         default=None)
    collect.add_argument("--threshold", type=int, default=None,
                         help="Sobrescreve PRICETRACK_EXPORT_THRESHOLD_ROWS")

    sub.add_parser("exports", help="Lista exports da organização")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", colorize=True,
               format="<green>{time:HH:mm:ss}</green> | "
                      "<level>{level: <8}</level> | {message}")

    try:
        settings = PriceTrackSettings.from_env()
    except PriceTrackError as e:
        logger.error(str(e))
        return 2

    if getattr(args, "threshold", None) is not None:
        settings.export_threshold_rows = args.threshold

    client = PriceTrackClient(settings)

    try:
        if args.command == "probe":
            query = CollectQuery(collection_date=args.date)
            count_fn = (client.count_offers if args.dataset == "offers"
                        else client.count_shipping)
            try:
                total = count_fn(query)
            except PriceTrackNoCollectionError:
                print(json.dumps({"date": args.date, "dataset": args.dataset,
                                  "total": 0, "status": "NO_DATA"}))
                return 0
            strategy = ("paginated" if total <= settings.export_threshold_rows
                        else "export")
            print(json.dumps({
                "date": args.date, "dataset": args.dataset, "total": total,
                "threshold": settings.export_threshold_rows,
                "auto_strategy": strategy,
            }))
            return 0

        if args.command == "collect":
            query = CollectQuery(
                collection_date=args.date,
                marketplace=args.marketplace,
                product_brand=args.brand,
                status=args.status,
            )
            collector = SmartCollector(client, alert_sink=TelegramAlertSink())
            result = collector.collect_offers(
                args.date, query=query, strategy=args.strategy
            ) if args.dataset == "offers" else collector.collect_shipping(
                args.date, query=query, strategy=args.strategy
            )
            print(json.dumps(result.metrics.to_dict(), ensure_ascii=False,
                             indent=2))
            return 0 if result.metrics.status != "FAILED" else 1

        if args.command == "exports":
            jobs = client.list_exports()
            for job in jobs:
                print(json.dumps({
                    "exportId": job.export_id, "status": job.status,
                    "progress": job.progress, "rowCount": job.row_count,
                    "fileSizeBytes": job.file_size_bytes,
                }, ensure_ascii=False))
            if not jobs:
                logger.info("Nenhum export encontrado.")
            return 0

    except ValueError as e:
        # Data/filtro inválido no CollectQuery: falha de input, sem traceback
        logger.error(f"Parâmetros inválidos: {e}")
        return 2
    except PriceTrackError as e:
        logger.error(f"Falha: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
