"""
normalize_supabase.py — Renormaliza o campo `produto` de toda a base no Supabase.

Aplica normalize_product_name() a cada registro da tabela `coletas` e atualiza
apenas as linhas cujo nome mudou. Registros sem marca ou BTUs identificáveis
ficam inalterados (fallback gracioso — nunca há perda de dados).

USO:
    # Pré-visualiza mudanças sem gravar (dry-run)
    python normalize_supabase.py --dry-run

    # Executa a normalização
    python normalize_supabase.py

    # Limita exemplos exibidos no dry-run (padrão: 20)
    python normalize_supabase.py --dry-run --preview 50
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")


def main():
    parser = argparse.ArgumentParser(
        description="Renormaliza nomes de produto na tabela coletas do Supabase"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra quais registros mudariam, sem gravar no banco",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=20,
        metavar="N",
        help="Número máximo de exemplos exibidos no dry-run (padrão: 20)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  RAC — Normalização de Nomes de Produto no Supabase")
    print("=" * 70)

    from utils.supabase_client import normalize_all_products_in_supabase

    if args.dry_run:
        print("\n[dry-run] Varrendo sem gravar…\n")
    else:
        print("\nVarrendo tabela coletas…\n")

    result = normalize_all_products_in_supabase(
        dry_run=args.dry_run,
        preview_limit=args.preview,
    )

    scanned   = result["scanned"]
    changed   = result["changed"]
    unchanged = result["unchanged"]
    updated   = result["updated"]
    errors    = result["errors"]
    preview   = result["preview"]

    print()
    print("=" * 70)
    print(f"  Registros analisados  : {scanned:,}")
    print(f"  Já normalizados       : {unchanged:,}")
    print(f"  Precisam atualizar    : {changed:,}")

    if args.dry_run:
        if changed == 0:
            print()
            print("  ✓ Toda a base já está normalizada. Nada a fazer.")
        else:
            pct = changed / scanned * 100 if scanned else 0
            print()
            print(f"  ⚠  {changed:,} registros ({pct:.1f}%) seriam renormalizados.")

            if preview:
                print()
                print(f"  Exemplos de mudanças (até {args.preview}):")
                print()
                for ex in preview:
                    print(f"  ID {ex['id']}")
                    print(f"    ANTES : {ex['before']}")
                    print(f"    DEPOIS: {ex['after']}")
                    print()

            try:
                confirm = input("  Confirmar normalização? [s/N] ").strip().lower()
            except KeyboardInterrupt:
                print("\n  Cancelado.")
                print("=" * 70)
                return

            if confirm not in ("s", "sim", "y", "yes"):
                print("  Cancelado.")
                print("=" * 70)
                return

            print()
            print("  Normalizando registros…")
            result2 = normalize_all_products_in_supabase(dry_run=False)
            updated = result2["updated"]
            errors  = result2["errors"]

            print()
            if errors == 0:
                print(f"  ✓ {updated:,} registros atualizados com sucesso.")
            else:
                print(f"  ⚠  {updated:,} atualizados, {errors:,} com erro.")
    else:
        print(f"  Atualizados           : {updated:,}")
        if errors:
            print(f"  Com erro              : {errors:,}")
        print()
        if errors == 0:
            print("  ✓ Normalização concluída com sucesso.")
        else:
            print("  ⚠  Normalização parcial — verifique os logs acima.")

    print("=" * 70)


if __name__ == "__main__":
    main()
