"""
scripts/fix_turno.py — Limpeza pontual de registros com turno invertido.

Antes do fix de timezone (TZ=UTC no GitHub Actions), os turnos ficaram invertidos:
  - Coleta manhã (13:00 UTC) → gravado como "Fechamento"  (errado)
  - Coleta noite  (00:00 UTC) → gravado como "Abertura"   (errado)

Execute UMA VEZ para limpar o histórico contaminado:

    # Primeiro, dry-run para ver quantos registros serão afetados:
    python scripts/fix_turno.py

    # Depois, confirme a deleção:
    python scripts/fix_turno.py --confirm
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from utils.supabase_client import fix_inverted_turno_in_supabase

if __name__ == "__main__":
    confirm = "--confirm" in sys.argv

    if not confirm:
        print("=" * 60)
        print("DRY-RUN — contando registros com turno invertido...")
        print("Use --confirm para deletar de verdade.")
        print("=" * 60)

    result = fix_inverted_turno_in_supabase(dry_run=not confirm)

    print()
    print("=" * 60)
    print(f"  Fechamento com horario 12:30-14:59 : {result.get('fechamento_wrong', '?')}")
    print(f"  Abertura  com horario 00:00-01:30  : {result.get('abertura_wrong', '?')}")
    if not confirm:
        print()
        print("  Nenhum registro deletado (dry-run).")
        print("  Para deletar: python scripts/fix_turno.py --confirm")
    else:
        print(f"  Total deletados : {result.get('deleted', 0)}")
        print(f"  Erros           : {result.get('errors', 0)}")
    print("=" * 60)
