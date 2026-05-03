"""
scripts/smoke_test.py — Teste rápido de sanidade para todas as plataformas ativas.

Executa 1 keyword / 1 página por plataforma e reporta PASS/FAIL com métricas básicas.
Útil para validar se scrapers estão funcionais antes de coleta completa.

Uso:
    python scripts/smoke_test.py                  # testa todas as plataformas
    python scripts/smoke_test.py --only ml        # testa apenas Mercado Livre
    python scripts/smoke_test.py --only dealers   # testa apenas DealerScraper

Plataformas suportadas: ml, amazon, magalu, google_shopping, leroy, dealers
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

# Garante que o root do projeto está no sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scrapers.amazon import AmazonScraper
from scrapers.dealers import DealerScraper
from scrapers.google_shopping import GoogleShoppingScraper
from scrapers.leroy_merlin import LeroyMerlinScraper
from scrapers.magalu import MagaluScraper
from scrapers.mercado_livre import MLScraper

# ---------------------------------------------------------------------------
# Configuração do smoke test
# ---------------------------------------------------------------------------

SMOKE_KEYWORD = "ar condicionado split inverter 12000"
SMOKE_KEYWORD_MAP: Dict[str, str] = {SMOKE_KEYWORD: "Capacidade + Tipo"}

SMOKE_DEALER = "Frigelar"  # primeiro dealer ativo em DEALER_CONFIGS

PLATFORMS = {
    "ml":             (MLScraper,             SMOKE_KEYWORD,   SMOKE_KEYWORD_MAP),
    "amazon":         (AmazonScraper,         SMOKE_KEYWORD,   SMOKE_KEYWORD_MAP),
    "magalu":         (MagaluScraper,         SMOKE_KEYWORD,   SMOKE_KEYWORD_MAP),
    "google_shopping":(GoogleShoppingScraper, SMOKE_KEYWORD,   SMOKE_KEYWORD_MAP),
    "leroy":          (LeroyMerlinScraper,    SMOKE_KEYWORD,   SMOKE_KEYWORD_MAP),
    "dealers":        (DealerScraper,         SMOKE_DEALER,    {}),
}

MIN_ITEMS = 5  # mínimo de itens para considerar PASS


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_platform(platform_key: str) -> Dict:
    scraper_cls, keyword, keyword_category_map = PLATFORMS[platform_key]
    result = {
        "platform": platform_key,
        "status": "FAIL",
        "items": 0,
        "sellers": 0,
        "elapsed_s": 0.0,
        "error": None,
    }

    t0 = time.time()
    try:
        with scraper_cls(headless=True) as scraper:
            records = scraper.search(
                keyword=keyword,
                keyword_category_map=keyword_category_map,
                page_limit=1,
            )

        result["items"] = len(records)
        result["sellers"] = len({r.get("Seller/Vendedor") for r in records if r.get("Seller/Vendedor")})
        result["status"] = "PASS" if result["items"] >= MIN_ITEMS else "FAIL"

    except Exception as exc:
        result["error"] = str(exc)[:120]
        result["status"] = "FAIL"

    result["elapsed_s"] = round(time.time() - t0, 1)
    return result


def print_summary(results: List[Dict]) -> None:
    print("\n" + "=" * 72)
    print(f"{'SMOKE TEST RESULTS':^72}")
    print("=" * 72)
    fmt = "{:<20} {:<6} {:>7} {:>8} {:>8}  {}"
    print(fmt.format("Platform", "Status", "Items", "Sellers", "Time(s)", "Error"))
    print("-" * 72)
    for r in results:
        icon = "✅" if r["status"] == "PASS" else "❌"
        err = r["error"] or ""
        print(fmt.format(
            r["platform"],
            f"{icon} {r['status']}",
            r["items"],
            r["sellers"],
            r["elapsed_s"],
            err[:45],
        ))
    print("=" * 72)
    passed = sum(1 for r in results if r["status"] == "PASS")
    print(f"Result: {passed}/{len(results)} platforms passed\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test para scrapers RAC")
    parser.add_argument(
        "--only",
        metavar="PLATFORM",
        help=f"Testa somente esta plataforma. Opções: {', '.join(PLATFORMS)}",
    )
    args = parser.parse_args()

    if args.only:
        if args.only not in PLATFORMS:
            logger.error(f"Plataforma desconhecida: '{args.only}'. Opções: {list(PLATFORMS)}")
            sys.exit(1)
        platform_keys = [args.only]
    else:
        platform_keys = list(PLATFORMS)

    results = []
    for key in platform_keys:
        logger.info(f"[smoke_test] Testando plataforma: {key}")
        r = run_platform(key)
        results.append(r)
        status_icon = "✅" if r["status"] == "PASS" else "❌"
        logger.info(
            f"[smoke_test] {key}: {status_icon} {r['status']} "
            f"| {r['items']} itens | {r['elapsed_s']}s"
        )

    print_summary(results)

    any_fail = any(r["status"] == "FAIL" for r in results)
    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
