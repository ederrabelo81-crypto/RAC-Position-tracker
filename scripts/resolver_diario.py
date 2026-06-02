#!/usr/bin/env python3
"""
scripts/resolver_diario.py — Resolução/normalização diária pós-coleta.

Roda após cada coleta (chamado pelos collect_*.sh) para manter o histórico
atualizado (task 4). Faz, em ordem:

  1. resolver_coletas_pendentes() (RPC SQL, migration 004):
     - de-para → coletas (estado_match/familia_resolvida/sku_resolvido) p/ linhas novas
     - Tier A: linhas com SKU → produto_normalizado montado do catálogo
     - Tier B: MAPEADO sem SKU → produto_normalizado descritivo da família

  2. Fill Python (normalize_product_name_v2): preenche produto_normalizado das
     linhas que o SQL não cobre — marcas FORA do catálogo (Daikin, Consul,
     Carrier, etc.), REVISAR e casos de borda. Garante que TODAS as ~30 marcas
     fiquem normalizadas no histórico, não só as 10 do catálogo PriceTrack.

USO:
    python scripts/resolver_diario.py              # incremental (linhas sem normalizado)
    python scripts/resolver_diario.py --full       # varre todo o histórico
    python scripts/resolver_diario.py --no-python   # só a RPC SQL (rápido)

Requer no .env: SUPABASE_URL + SUPABASE_KEY (service_role).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

import os

from loguru import logger

try:
    from supabase import create_client
except ImportError:
    logger.error("Falta `supabase`. Instale: pip install supabase python-dotenv")
    sys.exit(1)

from utils.normalize_product import normalize_product_name_v2

_PAGE = 1000
_BATCH = 500
# NAO_AC nunca é AC → não normaliza. Os demais estados podem ter marca/BTU.
_SKIP_ESTADOS = {"NAO_AC"}


def _client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL/SUPABASE_KEY não configurados no .env")
        sys.exit(1)
    return create_client(url, key)


def run_rpc(client) -> None:
    """Chama a função SQL resolver_coletas_pendentes() (migration 004)."""
    try:
        resp = client.rpc("resolver_coletas_pendentes").execute()
        data = resp.data
        if isinstance(data, list) and data:
            row = data[0]
        elif isinstance(data, dict):
            row = data
        else:
            row = {}
        logger.success(
            f"RPC resolver_coletas_pendentes: "
            f"resolvidas={row.get('resolvidas', '?')} "
            f"tier_a={row.get('tier_a', '?')} tier_b={row.get('tier_b', '?')}"
        )
    except Exception as exc:  # função pode não existir em banco não-migrado
        logger.warning(
            f"RPC resolver_coletas_pendentes falhou ({exc}). "
            "Aplique docs/migrations/004_produto_normalizado.sql."
        )


def fill_python(client, full: bool) -> None:
    """
    Preenche produto_normalizado das linhas que o SQL não cobriu, usando o
    normalizador Python (cobre marcas fora do catálogo). Paginação por id.
    """
    last_id = 0
    total_seen = total_upd = 0
    while True:
        q = (
            client.table("coletas")
            .select("id,produto,marca,estado_match")
            .order("id")
            .gt("id", last_id)
            .limit(_PAGE)
        )
        if not full:
            q = q.is_("produto_normalizado", "null")
        resp = q.execute()
        rows = resp.data or []
        if not rows:
            break

        updates = []
        for r in rows:
            last_id = r["id"]
            total_seen += 1
            if (r.get("estado_match") or "") in _SKIP_ESTADOS:
                continue
            base = r.get("produto")
            if not base:
                continue
            v2 = normalize_product_name_v2(base, r.get("marca"))
            if v2:
                updates.append({"id": r["id"], "produto_normalizado": v2[:500]})

        for i in range(0, len(updates), _BATCH):
            client.table("coletas").upsert(
                updates[i:i + _BATCH], on_conflict="id"
            ).execute()
        total_upd += len(updates)
        logger.info(f"Python fill: vistos={total_seen:,} | atualizados={total_upd:,}")

        if len(rows) < _PAGE:
            break

    logger.success(f"Python fill concluído: {total_upd:,} linhas normalizadas.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Resolução/normalização diária pós-coleta")
    ap.add_argument("--full", action="store_true",
                    help="Varre todo o histórico (default: só linhas sem normalizado)")
    ap.add_argument("--no-python", action="store_true",
                    help="Pula o fill Python (só a RPC SQL)")
    args = ap.parse_args()

    client = _client()
    logger.info("Resolução diária — RPC SQL...")
    run_rpc(client)
    if not args.no_python:
        logger.info("Resolução diária — fill Python (marcas fora do catálogo)...")
        fill_python(client, full=args.full)
    logger.success("Resolução diária concluída.")


if __name__ == "__main__":
    main()
