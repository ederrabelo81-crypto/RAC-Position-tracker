"""
cleanup_supabase.py — Remove registros não relacionados a ar-condicionado do Supabase.

Varre a tabela `coletas` e aplica o mesmo filtro AC usado no upload:
  - Mantém registros com termos fortes: BTU, ar condicionado, evaporadora...
  - Mantém registros com 2+ termos fracos: split + inverter
  - Remove registros com blocklist: iPhone, fralda, notebook, geladeira...

USO:
    # Pré-visualiza sem deletar (dry-run)
    python cleanup_supabase.py --dry-run

    # Executa a limpeza
    python cleanup_supabase.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")


def main():
    parser = argparse.ArgumentParser(
        description="Remove registros não-AC da tabela coletas no Supabase"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas conta os registros inválidos, sem deletar",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  RAC — Limpeza de Dados no Supabase")
    print("=" * 60)

    from utils.supabase_client import delete_invalid_from_supabase

    if args.dry_run:
        print("\n[dry-run] Varrendo sem deletar...\n")
    else:
        print("\nVarrendo tabela coletas...\n")

    result = delete_invalid_from_supabase(dry_run=args.dry_run)

    scanned = result["scanned"]
    invalid = result["invalid"]
    deleted = result["deleted"]
    errors  = result["errors"]
    valid   = scanned - invalid

    print()
    print("=" * 60)
    print(f"  Registros analisados : {scanned:,}")
    print(f"  Registros válidos    : {valid:,}")
    print(f"  Registros inválidos  : {invalid:,}")

    if args.dry_run:
        print()
        if invalid == 0:
            print("  ✓ Nenhum registro inválido encontrado.")
        else:
            pct = invalid / scanned * 100 if scanned else 0
            print(f"  ⚠  {invalid:,} registros ({pct:.1f}%) seriam deletados.")
            print()
            try:
                confirm = input("  Confirmar exclusão? [s/N] ").strip().lower()
            except KeyboardInterrupt:
                print("\n  Cancelado.")
                print("=" * 60)
                return

            if confirm not in ("s", "sim", "y", "yes"):
                print("  Cancelado.")
                print("=" * 60)
                return

            print()
            print("  Deletando registros inválidos...")
            result2 = delete_invalid_from_supabase(dry_run=False)
            deleted = result2["deleted"]
            errors  = result2["errors"]

            if errors == 0:
                print(f"  ✓ {deleted:,} registros removidos com sucesso.")
            else:
                print(f"  ⚠  {deleted:,} removidos, {errors:,} com erro.")
    else:
        if errors == 0:
            print(f"  Deletados            : {deleted:,}")
            print()
            print(f"  ✓ Limpeza concluída com sucesso.")
        else:
            print(f"  Deletados            : {deleted:,}")
            print(f"  Com erro             : {errors:,}")
            print()
            print(f"  ⚠  Limpeza parcial — verifique os logs acima.")

    print("=" * 60)


if __name__ == "__main__":
    main()
