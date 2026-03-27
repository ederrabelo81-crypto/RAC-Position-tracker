"""
teste.py — Script de teste rápido do bot RAC.

Executa 1 keyword × 1 página em todos os sites (ou nos selecionados) e
exibe uma tabela de diagnóstico no terminal + CSV em output/.

Uso:
    python teste.py
    python teste.py --keyword "ar condicionado split"
    python teste.py --keyword "midea inverter" --platforms ml amazon magalu
    python teste.py --platforms leroy fast --no-headless
    python teste.py --all-platforms
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Type

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))

from config import KEYWORDS, OUTPUT_DIR, LOGS_DIR
from scrapers.base import BaseScraper
from scrapers.mercado_livre import MLScraper
from scrapers.magalu import MagaluScraper
from scrapers.amazon import AmazonScraper
from scrapers.shopee import ShopeeScraper
from scrapers.casas_bahia import CasasBahiaScraper
from scrapers.google_shopping import GoogleShoppingScraper
from scrapers.leroy_merlin import LeroyMerlinScraper
from scrapers.fast_shop import FastShopScraper

# ---------------------------------------------------------------------------
# Registro de scrapers (apelido → classe)
# ---------------------------------------------------------------------------
SCRAPER_REGISTRY: Dict[str, Type[BaseScraper]] = {
    "ml":              MLScraper,
    "magalu":          MagaluScraper,
    "amazon":          AmazonScraper,
    "shopee":          ShopeeScraper,
    "casasbahia":      CasasBahiaScraper,
    "google_shopping": GoogleShoppingScraper,
    "leroy":           LeroyMerlinScraper,
    "fast":            FastShopScraper,
}

# Plataformas habilitadas por padrão no teste (sem Leroy/Fast que precisam de configs extras)
DEFAULT_PLATFORMS = ["ml", "magalu", "amazon", "shopee", "casasbahia", "google_shopping", "leroy", "fast"]

COLUMN_ORDER = [
    "Data", "Turno", "Horário", "Analista",
    "Plataforma", "Tipo Plataforma", "Keyword Buscada", "Categoria Keyword",
    "Marca Monitorada", "Produto / SKU",
    "Posição Orgânica", "Posição Patrocinada", "Posição Geral",
    "Preço (R$)", "Seller / Vendedor", "Fulfillment?",
    "Avaliação", "Qtd Avaliações", "Tag Destaque",
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(LOGS_DIR) / f"teste_{ts}.log"

    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="INFO",
        colorize=True,
    )
    logger.add(log_file, level="DEBUG", rotation="20 MB")
    logger.info(f"Log de teste salvo em: {log_file}")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _export_csv(records: List[Dict[str, Any]], prefix: str = "teste") -> Path:
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = Path(OUTPUT_DIR) / f"{prefix}_{ts}.csv"

    df = pd.DataFrame(records)
    for col in COLUMN_ORDER:
        if col not in df.columns:
            df[col] = None
    df = df[COLUMN_ORDER]
    df["Preço (R$)"]     = pd.to_numeric(df["Preço (R$)"], errors="coerce")
    df["Avaliação"]      = pd.to_numeric(df["Avaliação"],  errors="coerce")
    df["Qtd Avaliações"] = pd.to_numeric(df["Qtd Avaliações"], errors="coerce").astype("Int64")
    df.to_csv(path, index=False, encoding="utf-8-sig", sep=";")
    return path


# ---------------------------------------------------------------------------
# Sumário diagnóstico
# ---------------------------------------------------------------------------

def _print_summary(results: Dict[str, List[Dict[str, Any]]], keyword: str, elapsed: float) -> None:
    """Imprime tabela diagnóstico por plataforma."""
    SEP = "─" * 80

    print(f"\n{'═' * 80}")
    print(f"  RESULTADO DO TESTE — keyword: \"{keyword}\"")
    print(f"  Duração total: {elapsed:.0f}s")
    print(f"{'═' * 80}")
    print(f"  {'Plataforma':<20} {'Itens':>6}  {'Com Título':>10}  {'Com Preço':>9}  {'Com Marca':>9}  Status")
    print(SEP)

    total_items = 0
    for platform, records in results.items():
        n = len(records)
        total_items += n

        if n == 0:
            status = "❌ FALHOU"
            print(f"  {platform:<20} {n:>6}  {'—':>10}  {'—':>9}  {'—':>9}  {status}")
            continue

        df = pd.DataFrame(records)

        # Conta campos preenchidos
        has_title  = df["Produto / SKU"].notna() & (df["Produto / SKU"] != "")
        has_price  = df["Preço (R$)"].notna()
        has_brand  = df["Marca Monitorada"].notna() & (df["Marca Monitorada"] != "Desconhecida")

        n_title = has_title.sum()
        n_price = has_price.sum()
        n_brand = has_brand.sum()

        pct_title = n_title / n * 100
        pct_price = n_price / n * 100
        pct_brand = n_brand / n * 100

        if pct_title >= 80 and pct_price >= 50:
            status = "✅ OK"
        elif n_title == 0:
            status = "⚠️  SEM TÍTULO"
        elif pct_price < 20:
            status = "⚠️  SEM PREÇO"
        else:
            status = "⚠️  PARCIAL"

        print(
            f"  {platform:<20} {n:>6}  "
            f"{n_title:>4} ({pct_title:3.0f}%)  "
            f"{n_price:>4} ({pct_price:3.0f}%)  "
            f"{n_brand:>4} ({pct_brand:3.0f}%)  "
            f"{status}"
        )

    print(SEP)
    print(f"  {'TOTAL':<20} {total_items:>6}")
    print(f"{'═' * 80}\n")

    # Amostra dos primeiros 3 produtos de cada plataforma
    for platform, records in results.items():
        if not records:
            continue
        df = pd.DataFrame(records)
        cols = ["Plataforma", "Posição Geral", "Marca Monitorada", "Produto / SKU", "Preço (R$)", "Seller / Vendedor"]
        cols = [c for c in cols if c in df.columns]
        sample = df[cols].head(3)
        print(f"  ── Amostra: {platform} (top 3) ──")
        # Trunca títulos longos para caber no terminal
        if "Produto / SKU" in sample.columns:
            sample = sample.copy()
            sample["Produto / SKU"] = sample["Produto / SKU"].str[:55] if sample["Produto / SKU"].notna().any() else sample["Produto / SKU"]
        print(sample.to_string(index=False))
        print()


# ---------------------------------------------------------------------------
# Execução de um scraper
# ---------------------------------------------------------------------------

def _run_one(
    key: str,
    scraper_cls: Type[BaseScraper],
    keyword: str,
    headless: bool,
) -> List[Dict[str, Any]]:
    keywords_map = {"Teste": [keyword]}
    t0 = time.time()
    try:
        with scraper_cls(headless=headless) as scraper:
            records = scraper.search(
                keyword=keyword,
                keyword_category_map=keywords_map,
                page_limit=1,
            )
        elapsed = time.time() - t0
        logger.info(f"[{key}] {len(records)} itens em {elapsed:.1f}s")
        return records
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error(f"[{key}] ERRO em {elapsed:.1f}s: {exc}")
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Teste rápido: 1 keyword × 1 página em todos os sites",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--keyword", "-k",
        default="ar condicionado split",
        help='Keyword a buscar (padrão: "ar condicionado split")',
    )
    parser.add_argument(
        "--platforms", "-p",
        nargs="+",
        choices=list(SCRAPER_REGISTRY.keys()),
        default=None,
        metavar="PLATAFORMA",
        help=(
            "Plataformas a testar. Se omitido, testa todas.\n"
            "Opções: " + " ".join(SCRAPER_REGISTRY.keys())
        ),
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        default=True,
        help="Exibir browser (útil para diagnóstico visual)",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        default=False,
        help="Rodar plataformas uma por vez (padrão: paralelo quando possível)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
    _setup_logging()

    keyword  = args.keyword
    platform_keys = args.platforms if args.platforms else DEFAULT_PLATFORMS

    logger.info(f"{'='*60}")
    logger.info(f"TESTE — keyword: \"{keyword}\"")
    logger.info(f"Plataformas: {', '.join(platform_keys)}")
    logger.info(f"Headless: {args.headless}")
    logger.info(f"{'='*60}")

    results: Dict[str, List[Dict[str, Any]]] = {}
    all_records: List[Dict[str, Any]] = []
    t_total = time.time()

    for key in platform_keys:
        cls = SCRAPER_REGISTRY[key]
        platform_label = cls.platform_name
        logger.info(f"\n{'─'*50}")
        logger.info(f"Testando: {platform_label}")
        logger.info(f"{'─'*50}")

        records = _run_one(key, cls, keyword, args.headless)
        results[platform_label] = records
        all_records.extend(records)

    elapsed = time.time() - t_total

    # Exibe sumário diagnóstico
    _print_summary(results, keyword, elapsed)

    # Exporta CSV
    if all_records:
        safe_kw = keyword[:30].replace(" ", "_")
        csv_path = _export_csv(all_records, prefix=f"teste_{safe_kw}")
        logger.success(f"CSV exportado: {csv_path}  ({len(all_records)} linhas)")
    else:
        logger.warning("Nenhum registro coletado em nenhuma plataforma.")


if __name__ == "__main__":
    main()
