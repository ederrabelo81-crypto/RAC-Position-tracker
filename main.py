"""
main.py — Ponto de entrada do bot de monitoramento de preços RAC.

Fluxo principal:
  1. Lê keywords e configurações de config.py
  2. Instancia os scrapers selecionados (via argparse ou padrão)
  3. Executa buscas página a página com delays anti-bot
  4. Agrega todos os registros em um único DataFrame Pandas
  5. Exporta CSV datado em output/

Uso rápido (demo Mercado Livre):
    python main.py

Uso completo (todos os sites, todas as keywords):
    python main.py --platforms all --pages 3

Uso específico:
    python main.py --platforms ml magalu --keywords "ar condicionado inverter 12000"
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import pandas as pd
from loguru import logger

# Adiciona raiz ao path para imports relativos funcionarem
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    KEYWORDS, KEYWORDS_LIST, MAX_PAGES, OUTPUT_DIR, LOGS_DIR,
    ACTIVE_PLATFORMS, PRIORITY_FILTER,
)
from scrapers.base import BaseScraper
from scrapers.mercado_livre import MLScraper
from scrapers.magalu import MagaluScraper
from scrapers.amazon import AmazonScraper
from scrapers.shopee import ShopeeScraper
from scrapers.casas_bahia import CasasBahiaScraper
from scrapers.google_shopping import GoogleShoppingScraper
from scrapers.leroy_merlin import LeroyMerlinScraper
from scrapers.fast_shop import FastShopScraper
from scrapers.dealers import DealerScraper, DEALER_CONFIGS

# ---------------------------------------------------------------------------
# Mapeamento de apelidos de linha de comando para classes de scraper
# ---------------------------------------------------------------------------
SCRAPER_REGISTRY: Dict[str, Type[BaseScraper]] = {
    "ml":             MLScraper,
    "magalu":         MagaluScraper,
    "amazon":         AmazonScraper,
    "shopee":         ShopeeScraper,
    "casasbahia":     CasasBahiaScraper,
    "google_shopping": GoogleShoppingScraper,
    "leroy":          LeroyMerlinScraper,
    "fast":           FastShopScraper,
    "dealers":        DealerScraper,
}

# Keywords map para DealerScraper: cada dealer name vira a "keyword".
# Dealers marcados como "on_hold" são excluídos da coleta.
_DEALER_KEYWORDS_MAP: Dict[str, List[str]] = {
    "Dealers": [
        name for name, cfg in DEALER_CONFIGS.items() if not cfg.get("on_hold")
    ]
}

# Colunas na ordem exata do DataFrame de saída
COLUMN_ORDER = [
    "Data",
    "Turno",
    "Horário",
    "Analista",
    "Plataforma",
    "Tipo Plataforma",
    "Keyword Buscada",
    "Categoria Keyword",
    "Marca Monitorada",
    "Produto / SKU",
    "Posição Orgânica",
    "Posição Patrocinada",
    "Posição Geral",
    "Preço (R$)",
    "Seller / Vendedor",
    "Fulfillment?",
    "Avaliação",
    "Qtd Avaliações",
    "Tag Destaque",
]


# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------

def _setup_logging(log_dir: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logger.remove()  # remove handler padrão do stderr
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="INFO",
        colorize=True,
    )
    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
        level="DEBUG",
        rotation="50 MB",
        retention="7 days",
    )
    logger.info(f"Log salvo em: {log_file}")


# ---------------------------------------------------------------------------
# Exportação do CSV
# ---------------------------------------------------------------------------

def _export_csv(records: List[Dict[str, Any]], output_dir: str) -> Path:
    """
    Converte a lista de registros em DataFrame e exporta como CSV UTF-8 com BOM
    (compatível com Excel PT-BR sem problemas de encoding).

    Args:
        records:    lista de dicts coletados pelos scrapers
        output_dir: diretório de saída

    Returns:
        Path do arquivo CSV gerado.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename  = f"rac_monitoramento_{timestamp}.csv"
    filepath  = Path(output_dir) / filename

    df = pd.DataFrame(records)

    # Garante que todas as colunas existam (mesmo que vazias)
    for col in COLUMN_ORDER:
        if col not in df.columns:
            df[col] = None

    df = df[COLUMN_ORDER]  # reordena colunas

    # Força tipos corretos
    df["Preço (R$)"]     = pd.to_numeric(df["Preço (R$)"], errors="coerce")
    df["Avaliação"]      = pd.to_numeric(df["Avaliação"], errors="coerce")
    df["Qtd Avaliações"] = pd.to_numeric(df["Qtd Avaliações"], errors="coerce").astype("Int64")

    # encoding utf-8-sig → Excel abre corretamente no Brasil
    df.to_csv(filepath, index=False, encoding="utf-8-sig", sep=";")

    logger.success(f"CSV exportado: {filepath} ({len(df)} linhas)")
    return filepath


# ---------------------------------------------------------------------------
# Execução de um scraper para múltiplas keywords
# ---------------------------------------------------------------------------

def _run_scraper(
    scraper_cls: Type[BaseScraper],
    keywords_map: Dict[str, List[str]],
    page_limit: int,
    headless: bool,
    priority_filter: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Instancia o scraper (como context manager) e itera por todas as keywords.

    O context manager garante que o browser seja fechado corretamente mesmo
    em caso de exceção.

    Args:
        scraper_cls:  classe do scraper (ex: MLScraper)
        keywords_map: dict {categoria: [keywords]} do config
        page_limit:   páginas por keyword
        headless:     modo headless do browser

    Returns:
        Lista agregada de todos os registros.
    """
    records: List[Dict[str, Any]] = []

    # Priority filter aplica só a scrapers de marketplace.
    # DealerScraper usa nomes de sites como "keywords" — nunca estão em
    # KEYWORDS_LIST, então o filtro zeraria a coleta de dealers.
    effective_filter = (
        None if scraper_cls is DealerScraper
        else (priority_filter if priority_filter is not None else PRIORITY_FILTER)
    )

    active_keywords = KEYWORDS_LIST
    if effective_filter:
        active_keywords = [k for k in KEYWORDS_LIST if k.priority in effective_filter]

    with scraper_cls(headless=headless) as scraper:
        for category, kws in keywords_map.items():
            filtered_kws = [
                kw for kw in kws
                if not effective_filter or any(
                    ak.term == kw and ak.priority in effective_filter
                    for ak in active_keywords
                )
            ] if effective_filter else kws

            for keyword in filtered_kws:
                logger.info(
                    f"[{scraper.platform_name}] Iniciando keyword: '{keyword}' "
                    f"(categoria: {category})"
                )
                try:
                    result = scraper.search(
                        keyword=keyword,
                        keyword_category_map=keywords_map,
                        page_limit=page_limit,
                    )
                    records.extend(result)
                except Exception as exc:
                    logger.error(
                        f"[{scraper.platform_name}] Falha em '{keyword}': {exc}. "
                        "Continuando para a próxima keyword."
                    )

    return records


# ---------------------------------------------------------------------------
# CLI — argparse
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bot de monitoramento de preços de ar condicionado",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "--platforms",
        nargs="+",
        choices=list(SCRAPER_REGISTRY.keys()) + ["all"],
        default=None,  # None = usa ACTIVE_PLATFORMS do config.py
        metavar="PLATFORM",
        help=(
            "Plataformas a monitorar. Opções: "
            + ", ".join(SCRAPER_REGISTRY.keys())
            + ', all\n'
            "Padrão: plataformas ativas em config.py (ACTIVE_PLATFORMS)"
        ),
    )

    parser.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        metavar="KEYWORD",
        help=(
            "Keywords customizadas (substitui as do config.py).\n"
            'Ex: --keywords "ar condicionado inverter 12000"'
        ),
    )

    parser.add_argument(
        "--pages",
        type=int,
        default=MAX_PAGES,
        metavar="N",
        help=f"Máximo de páginas por keyword (padrão: {MAX_PAGES})",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Executar browser em modo headless (padrão: True)",
    )

    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Exibir browser (útil para depuração visual)",
    )

    parser.add_argument(
        "--priority",
        nargs="+",
        choices=["alta", "media", "baixa"],
        default=None,
        metavar="PRIORITY",
        help=(
            "Filtrar keywords por prioridade (sobrepõe PRIORITY_FILTER do config.py).\n"
            "Opções: alta, media, baixa. Ex: --priority alta media\n"
            "Padrão: todas as prioridades"
        ),
    )

    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        metavar="DIR",
        help=f"Diretório de saída dos CSVs (padrão: {OUTPUT_DIR}/)",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
    _setup_logging(LOGS_DIR)

    # --- Resolve plataformas ---
    if args.platforms is None:
        # Padrão: usa ACTIVE_PLATFORMS do config.py
        platform_names = [p for p, active in ACTIVE_PLATFORMS.items() if active]
    elif "all" in args.platforms:
        platform_names = list(SCRAPER_REGISTRY.keys())
    else:
        platform_names = args.platforms

    selected_scrapers = [
        SCRAPER_REGISTRY[p] for p in platform_names if p in SCRAPER_REGISTRY
    ]

    if not selected_scrapers:
        logger.error("Nenhuma plataforma ativa. Verifique ACTIVE_PLATFORMS em config.py.")
        return

    logger.info(
        f"Plataformas: {', '.join(platform_names)} | "
        f"Páginas/keyword: {args.pages} | "
        f"Headless: {args.headless}"
    )

    # --- Resolve keywords ---
    if args.keywords:
        # keywords passadas via CLI → agrupa como categoria "CLI"
        keywords_map = {"CLI": args.keywords}
    else:
        keywords_map = KEYWORDS

    total_keywords = sum(len(v) for v in keywords_map.values())
    logger.info(f"Total de keywords: {total_keywords}")

    # --- Executa scrapers ---
    all_records: List[Dict[str, Any]] = []

    for scraper_cls in selected_scrapers:
        logger.info(f"{'='*60}")
        logger.info(f"Iniciando scraper: {scraper_cls.platform_name}")
        logger.info(f"{'='*60}")

        # DealerScraper usa mapa próprio de dealers, não keywords de mercado
        effective_map = (
            _DEALER_KEYWORDS_MAP if scraper_cls is DealerScraper else keywords_map
        )

        try:
            records = _run_scraper(
                scraper_cls=scraper_cls,
                keywords_map=effective_map,
                page_limit=args.pages,
                headless=args.headless,
                priority_filter=args.priority,
            )
            all_records.extend(records)
            logger.info(
                f"{scraper_cls.platform_name}: {len(records)} registros coletados"
            )
        except Exception as exc:
            logger.error(
                f"{scraper_cls.platform_name}: falhou com erro inesperado — {exc}. "
                "Continuando para o próximo scraper."
            )

    # --- Exporta resultados ---
    if all_records:
        csv_path = _export_csv(all_records, args.output_dir)
        logger.success(
            f"\nColeta finalizada! {len(all_records)} registros totais.\n"
            f"Arquivo: {csv_path}"
        )

        # --- Upload para Supabase (não bloqueia — CSV já está salvo) ---
        import os
        _supabase_url = os.getenv("SUPABASE_URL", "").strip()
        _supabase_key = os.getenv("SUPABASE_KEY", "").strip()
        if not _supabase_url or not _supabase_key:
            logger.warning(
                "Supabase upload IGNORADO — SUPABASE_URL ou SUPABASE_KEY não configuradas. "
                "Defina as variáveis de ambiente (ou secrets no GitHub Actions)."
            )
        else:
            try:
                from utils.supabase_client import upload_to_supabase
                ok = upload_to_supabase(all_records)
                if not ok:
                    logger.error(
                        "Supabase upload retornou falha — verifique os logs acima para detalhes."
                    )
            except Exception as exc:
                logger.error(f"Supabase upload lançou exceção: {exc}")

    else:
        logger.warning(
            "Nenhum registro coletado. "
            "Verifique os logs para erros ou bloqueios anti-bot."
        )


# ---------------------------------------------------------------------------
# Demo: executa Mercado Livre com 1 keyword, 1 página (teste rápido)
# ---------------------------------------------------------------------------

def demo() -> None:
    """
    Demonstração rápida: busca 1 keyword no Mercado Livre e salva CSV.
    Executado quando o script é chamado diretamente sem argumentos de demo.
    """
    _setup_logging(LOGS_DIR)

    logger.info("=== MODO DEMO: Mercado Livre, 1 keyword, 1 página ===")

    demo_keyword  = "ar condicionado split 12000 btus"
    keywords_map  = {"Capacidade": [demo_keyword]}

    records = _run_scraper(
        scraper_cls=MLScraper,
        keywords_map=keywords_map,
        page_limit=1,
        headless=True,
    )

    if records:
        csv_path = _export_csv(records, OUTPUT_DIR)
        logger.success(f"Demo concluído! {len(records)} produtos salvos em {csv_path}")

        # Exibe primeiras linhas no terminal
        df = pd.DataFrame(records)[[
            "Marca Monitorada", "Produto / SKU", "Preço (R$)",
            "Posição Orgânica", "Posição Patrocinada", "Fulfillment?",
        ]]
        logger.info(f"\n{df.head(10).to_string(index=False)}")
    else:
        logger.warning("Demo não retornou dados. Verifique conexão e seletores CSS.")


if __name__ == "__main__":
    # Se nenhum argumento for passado, executa modo demo (Mercado Livre)
    if len(sys.argv) == 1:
        demo()
    else:
        main()
