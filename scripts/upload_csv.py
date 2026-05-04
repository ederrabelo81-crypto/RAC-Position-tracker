"""
scripts/upload_csv.py — Envia um CSV de coleta para o Supabase.

Uso:
    python scripts/upload_csv.py output/rac_monitoramento_20260504_1037.csv
    python scripts/upload_csv.py output/*.csv          # vários arquivos
    python scripts/upload_csv.py arquivo.csv --dry-run # só valida, não envia
    python scripts/upload_csv.py arquivo.csv --run-id <uuid>  # run_id manual
    python scripts/upload_csv.py arquivo.csv --no-run-id      # import histórico (NULL)

O run_id padrão é gerado deterministicamente a partir do nome do arquivo,
então re-importar o mesmo CSV é sempre idempotente (duplicatas ignoradas).
"""

import argparse
import sys
import uuid
from pathlib import Path

import pandas as pd
from loguru import logger

# Garante imports do projeto independente de onde o script é chamado
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from utils.supabase_client import upload_to_supabase, log_auditoria_run


def _derive_run_id(csv_path: Path) -> str:
    """Gera UUID v5 determinístico a partir do nome do arquivo."""
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # URL namespace
    return str(uuid.uuid5(namespace, csv_path.name))


def _load_csv(csv_path: Path) -> list[dict]:
    """Lê o CSV e retorna lista de dicts no formato interno do bot."""
    try:
        df = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig", dtype=str)
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, sep=";", encoding="latin-1", dtype=str)

    # Normaliza nome de coluna para compatibilidade com variantes históricas
    df.columns = [c.strip() for c in df.columns]
    col_aliases = {
        "Produto/SKU":   "Produto / SKU",
        "Seller":        "Seller / Vendedor",
        "Tipo":          "Tipo Plataforma",
        "Keyword":       "Keyword Buscada",
        "Categoria":     "Categoria Keyword",
        "Marca":         "Marca Monitorada",
        "Pos. Orgânica": "Posição Orgânica",
        "Pos. Patrocinada": "Posição Patrocinada",
        "Pos. Geral":    "Posição Geral",
        "Preço":         "Preço (R$)",
        "Fulfillment":   "Fulfillment?",
        "Qtd. Avaliações": "Qtd Avaliações",
        "Tag":           "Tag Destaque",
    }
    df.rename(columns=col_aliases, inplace=True)

    records = df.where(pd.notna(df), None).to_dict(orient="records")
    return records


def upload_csv(csv_path: Path, run_id: str | None, dry_run: bool) -> bool:
    """Carrega um CSV e envia para o Supabase. Retorna True em sucesso."""
    if not csv_path.exists():
        logger.error(f"Arquivo não encontrado: {csv_path}")
        return False

    logger.info(f"Lendo: {csv_path}")
    records = _load_csv(csv_path)
    logger.info(f"{len(records)} registros carregados do CSV.")

    if dry_run:
        logger.info(f"[DRY-RUN] Nenhum dado enviado. run_id seria: {run_id or 'NULL'}")
        return True

    ok = upload_to_supabase(records, run_id=run_id)

    if ok and run_id:
        log_auditoria_run(run_id, str(csv_path))

    return ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Envia CSV(s) de coleta para o Supabase."
    )
    parser.add_argument(
        "csv_files",
        nargs="+",
        metavar="CSV",
        help="Um ou mais arquivos CSV para enviar.",
    )
    parser.add_argument(
        "--run-id",
        metavar="UUID",
        help="run_id manual (UUID). Por padrão é derivado do nome do arquivo.",
    )
    parser.add_argument(
        "--no-run-id",
        action="store_true",
        help="Usa run_id=NULL (import histórico). Re-importar o mesmo CSV pode duplicar dados.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida e loga sem enviar dados ao Supabase.",
    )
    args = parser.parse_args()

    paths = [Path(f) for f in args.csv_files]
    results: dict[str, bool] = {}

    for csv_path in paths:
        if args.no_run_id:
            run_id = None
        elif args.run_id:
            run_id = args.run_id
        else:
            run_id = _derive_run_id(csv_path)
            logger.info(f"run_id derivado do arquivo: {run_id}")

        ok = upload_csv(csv_path, run_id=run_id, dry_run=args.dry_run)
        results[csv_path.name] = ok

    # Resumo final (útil quando há múltiplos arquivos)
    if len(paths) > 1:
        ok_count = sum(results.values())
        logger.info(f"\nResumo: {ok_count}/{len(paths)} arquivos enviados com sucesso.")
        for name, ok in results.items():
            status = "✓" if ok else "✗"
            logger.info(f"  {status} {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
