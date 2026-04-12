"""
import_history.py — Importa CSVs históricos para o Supabase.

Lê todos os arquivos rac_monitoramento_*.csv da pasta output/
e faz upload para a tabela `coletas`. Registros duplicados são ignorados
automaticamente (upsert com ignore_duplicates=True).

USO:
    # Importa todos os CSVs da pasta output/
    python import_history.py

    # Importa um arquivo específico
    python import_history.py --file output/rac_monitoramento_20260401_0600.csv

    # Pré-visualiza sem enviar (dry-run)
    python import_history.py --dry-run
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")


def load_csv(path: Path) -> list[dict]:
    """Lê um CSV do bot e retorna lista de dicts no formato interno."""
    try:
        df = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
        # Remove linhas completamente vazias
        df = df.dropna(how="all")
        records = df.where(pd.notna(df), None).to_dict("records")
        return records
    except Exception as e:
        print(f"  ✗ Erro ao ler {path.name}: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Importa CSVs históricos para o Supabase")
    parser.add_argument("--file",    type=str, default=None, help="Caminho de um CSV específico")
    parser.add_argument("--dry-run", action="store_true",    help="Mostra o que seria importado sem enviar")
    parser.add_argument("--dir",     type=str, default="output", help="Pasta com os CSVs (padrão: output/)")
    args = parser.parse_args()

    print("=" * 60)
    print("  RAC — Importação de Histórico para Supabase")
    print("=" * 60)

    # Localiza arquivos
    if args.file:
        files = [Path(args.file)]
        if not files[0].exists():
            print(f"\n✗ Arquivo não encontrado: {args.file}")
            sys.exit(1)
    else:
        output_dir = Path(args.dir)
        if not output_dir.exists():
            print(f"\n✗ Pasta '{args.dir}' não encontrada.")
            sys.exit(1)
        files = sorted(output_dir.glob("rac_monitoramento_*.csv"))
        if not files:
            print(f"\n✗ Nenhum CSV encontrado em '{args.dir}'.")
            sys.exit(1)

    # Pré-visualização
    print(f"\nArquivos encontrados: {len(files)}\n")
    total_rows = 0
    file_data = []
    for f in files:
        records = load_csv(f)
        total_rows += len(records)
        file_data.append((f, records))
        print(f"  {f.name:<50} {len(records):>6} linhas")

    print(f"\n  Total: {total_rows:,} linhas")

    if args.dry_run:
        print("\n[dry-run] Nenhum dado enviado.")
        return

    if not file_data:
        return

    # Confirmação
    print()
    try:
        confirm = input("Confirmar upload para Supabase? [s/N] ").strip().lower()
    except KeyboardInterrupt:
        print("\nCancelado.")
        return
    if confirm not in ("s", "sim", "y", "yes"):
        print("Cancelado.")
        return

    # Import
    from utils.supabase_client import upload_to_supabase

    total_ok    = 0
    total_fail  = 0
    total_files = len(file_data)

    print()
    for i, (f, records) in enumerate(file_data, 1):
        if not records:
            continue
        print(f"[{i}/{total_files}] {f.name} ({len(records)} linhas)...", end=" ", flush=True)
        ok = upload_to_supabase(records)
        if ok:
            total_ok += len(records)
            print("✓")
        else:
            total_fail += len(records)
            print("✗ (veja erros acima)")

    print()
    print("=" * 60)
    if total_fail == 0:
        print(f"  ✓ Concluído — {total_ok:,} registros importados com sucesso.")
    else:
        print(f"  ⚠  Concluído — {total_ok:,} OK, {total_fail:,} com erro.")
    print("  Registros duplicados foram ignorados automaticamente.")
    print("=" * 60)


if __name__ == "__main__":
    main()
