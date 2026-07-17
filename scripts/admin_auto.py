#!/usr/bin/env python3
"""
scripts/admin_auto.py — Executa a automação da área ADMIN via linha de comando.

Roda a mesma pipeline disparada após cada coleta (main.py) e pela página
"🤖 Automação" do dashboard: limpeza de registros não-AC, validação de preços,
normalizações (produto/marca/plataforma), seed + auto-resolução do de-para
(Família & SKU) e refresh do cache de filtros. Nenhuma interação humana.

USO:
    python scripts/admin_auto.py                       # run incremental completo
    python scripts/admin_auto.py --dry-run             # simula, não grava nada
    python scripts/admin_auto.py --full                # ignora watermark (histórico)
    python scripts/admin_auto.py --steps data_cleanup price_validation
    python scripts/admin_auto.py --no-notify --no-llm  # sem Telegram / sem LLM

CRON (sugestão — manutenção semanal completa, domingo 03:00 BRT):
    0 3 * * 0  cd /home/ubuntu/rac-position-tracker && \
               .venv/bin/python scripts/admin_auto.py --full >> logs/cron.log 2>&1

Requer no .env: SUPABASE_URL + SUPABASE_KEY (service_role).
Opcional: ANTHROPIC_API_KEY (camada LLM da fila REVISAR).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from loguru import logger

from utils.admin_automation import STEP_ORDER, run_admin_automation


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Automação ADMIN (limpeza, normalização e de-para) sem interação humana"
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Simula a pipeline — conta tudo, não grava nada")
    ap.add_argument("--full", action="store_true",
                    help="Ignora o watermark incremental e varre o histórico inteiro")
    ap.add_argument("--steps", nargs="+", choices=STEP_ORDER, default=None,
                    metavar="ETAPA", help=f"Subconjunto de etapas ({', '.join(STEP_ORDER)})")
    ap.add_argument("--trigger", default="cron",
                    help="Identificação da origem do run no histórico (default: cron)")
    ap.add_argument("--no-notify", action="store_true",
                    help="Não envia resumo ao Telegram")
    ap.add_argument("--no-llm", action="store_true",
                    help="Desativa a camada LLM da fila REVISAR neste run")
    args = ap.parse_args()

    if args.no_llm:
        os.environ["ADMIN_AUTO_LLM"] = "off"

    report = run_admin_automation(
        trigger=args.trigger,
        dry_run=args.dry_run,
        steps=args.steps,
        notify=not args.no_notify,
        full_scan=args.full,
    )

    if report["status"] == "skipped":
        reason = report.get("skip_reason")
        if reason == "quota_restricted":
            logger.error(
                "Automação pulada — banco Supabase RESTRITO por cota "
                "(exceed_db_size_quota). Libere espaço (pricetrack_daily/coletas) "
                "ou faça upgrade do plano; não é problema de .env."
            )
            sys.exit(1)
        if reason == "locked":
            logger.info("Automação pulada — outra execução em andamento (mutex).")
            sys.exit(0)
        logger.error("Automação pulada — verifique SUPABASE_URL/SUPABASE_KEY no .env")
        sys.exit(1)
    sys.exit(0 if report["errors"] == 0 else 2)


if __name__ == "__main__":
    main()
