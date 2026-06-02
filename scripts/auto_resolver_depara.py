#!/usr/bin/env python3
"""
scripts/auto_resolver_depara.py — Auto-resolve a fila REVISAR do de-para.

Varre `produtos_depara_nome` (por padrão, estado=REVISAR), reclassifica cada
nome com `utils.depara_resolver.resolve_depara` (primitivas fortes de marca/
BTU/ciclo + catálogo) e, para os que saem da fila humana, grava a resolução
via RPC `admin_normalizar_nome` — que já propaga para `coletas` e
`rac_monitoramento`.

Guardas `NAO_AC` e `FORA_TIPO` (janela/cassete/portátil/36k+) de
`scripts/montar_depara.py` rodam ANTES do matcher forte, para que um título de
marca catalogada que seja, p.ex., um modelo de janela não vire MAPEADO Hi-Wall.

USO:
    python scripts/auto_resolver_depara.py                 # dry-run (default)
    python scripts/auto_resolver_depara.py --apply         # grava via RPC
    python scripts/auto_resolver_depara.py --estado REVISAR FORA_ESCOPO
    python scripts/auto_resolver_depara.py --limit 50      # amostra
    python scripts/auto_resolver_depara.py --export-csv proposta.csv

Requer no .env: SUPABASE_URL + SUPABASE_KEY (service_role).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

try:
    from supabase import create_client, Client
except ImportError:
    logger.error("Falta `supabase`. Instale: pip install supabase python-dotenv")
    sys.exit(1)

from scripts.montar_depara import (
    FORA_TIPO_REGEX,
    NAO_AC_REGEX,
    load_catalog_btus,
    load_catalog_familias,
)
from utils.depara_resolver import DeParaResult, resolve_depara

_PAGE = 1000


def _client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL/SUPABASE_KEY não configurados no .env")
        sys.exit(1)
    return create_client(url, key)


def _fetch_depara(client: Client, estados: list[str]) -> list[dict]:
    """Carrega linhas do de-para nos estados pedidos (paginado)."""
    rows: list[dict] = []
    offset = 0
    while True:
        resp = (
            client.table("produtos_depara_nome")
            .select("nome_coletado,estado,familia,sku,marca_norm")
            .in_("estado", estados)
            .range(offset, offset + _PAGE - 1)
            .execute()
        )
        if not resp.data:
            break
        rows.extend(resp.data)
        if len(resp.data) < _PAGE:
            break
        offset += _PAGE
    return rows


def _resolve_one(
    nome: str, marca_raw: Optional[str], catalog_familias, catalog_btus
) -> DeParaResult:
    """Aplica guardas NAO_AC/FORA_TIPO e, no resto, o matcher forte."""
    if any(p.search(nome) for p in NAO_AC_REGEX):
        return DeParaResult("NAO_AC", None, None, None, "alta", "padrão não-AC")
    if any(p.search(nome) for p in FORA_TIPO_REGEX):
        return DeParaResult(
            "FORA_ESCOPO", None, None, None, "alta", "tipo fora do escopo"
        )
    return resolve_depara(nome, marca_raw, catalog_familias, catalog_btus)


def _apply(client: Client, nome: str, res: DeParaResult) -> dict:
    """Grava a resolução via RPC admin_normalizar_nome (propaga p/ coletas)."""
    resp = client.rpc(
        "admin_normalizar_nome",
        {
            "p_nome": nome,
            "p_estado": res.estado,
            "p_familia": res.familia,
            "p_sku": res.sku,
            "p_marca": res.marca_norm,
        },
    ).execute()
    return resp.data if isinstance(resp.data, dict) else {}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Auto-resolve a fila REVISAR do de-para (dry-run por padrão)"
    )
    ap.add_argument(
        "--apply", action="store_true",
        help="Grava as resoluções no Supabase (default: dry-run, não grava)",
    )
    ap.add_argument(
        "--estado", nargs="+", default=["REVISAR"],
        help="Estados a reprocessar (default: REVISAR)",
    )
    ap.add_argument("--limit", type=int, default=None, help="Processa no máximo N nomes")
    ap.add_argument(
        "--export-csv", default="depara_auto_proposta.csv",
        help="CSV com a proposta de resolução (default: depara_auto_proposta.csv)",
    )
    args = ap.parse_args()

    client = _client()

    logger.info("Carregando catálogo (famílias + capacidades)…")
    catalog_familias = load_catalog_familias(client)
    catalog_btus = load_catalog_btus(client)
    logger.info(
        f"Catálogo: {len(catalog_familias)} combos (marca, BTU, ciclo); "
        f"{len(catalog_btus)} capacidades."
    )

    logger.info(f"Carregando de-para nos estados {args.estado}…")
    rows = _fetch_depara(client, args.estado)
    if args.limit:
        rows = rows[: args.limit]
    logger.info(f"{len(rows)} nomes para reprocessar.")

    proposals: list[dict] = []
    for r in rows:
        nome = r["nome_coletado"]
        res = _resolve_one(nome, r.get("marca_norm"), catalog_familias, catalog_btus)
        # Só interessa quem MUDA de estado/família (sai da fila ou refina).
        if res.estado == r.get("estado") and res.familia == r.get("familia"):
            continue
        proposals.append({
            "nome_coletado": nome,
            "estado_antes": r.get("estado"),
            "estado_depois": res.estado,
            "familia_depois": res.familia,
            "marca_norm": res.marca_norm,
            "confidence": res.confidence,
            "reason": res.reason,
        })

    # Resumo por transição
    by_dest: dict[str, int] = {}
    for p in proposals:
        by_dest[p["estado_depois"]] = by_dest.get(p["estado_depois"], 0) + 1
    sai_da_fila = sum(
        1 for p in proposals
        if p["estado_antes"] == "REVISAR" and p["estado_depois"] != "REVISAR"
    )

    # CSV de auditoria
    out_path = Path(args.export_csv)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "nome_coletado", "estado_antes", "estado_depois",
                "familia_depois", "marca_norm", "confidence", "reason",
            ],
            delimiter=";",
        )
        w.writeheader()
        w.writerows(sorted(proposals, key=lambda x: (x["estado_depois"], x["nome_coletado"])))

    logger.info(f"Proposta: {len(proposals)} mudanças → {by_dest}")
    logger.success(f"{sai_da_fila} nomes sairiam de REVISAR.")
    logger.info(f"CSV de auditoria: {out_path}")

    if not args.apply:
        logger.warning("DRY-RUN — nada gravado. Use --apply para efetivar.")
        return

    logger.info("Aplicando via RPC admin_normalizar_nome…")
    ok = errors = coletas_tot = 0
    for p in proposals:
        res = DeParaResult(
            estado=p["estado_depois"], familia=p["familia_depois"], sku=None,
            marca_norm=p["marca_norm"], confidence=p["confidence"], reason=p["reason"],
        )
        try:
            payload = _apply(client, p["nome_coletado"], res)
            coletas_tot += int(payload.get("coletas_atualizadas", 0) or 0)
            ok += 1
        except Exception as exc:  # noqa: BLE001 — log e segue
            errors += 1
            logger.error(f"Falha em '{p['nome_coletado'][:60]}': {exc}")

    if errors == 0:
        logger.success(f"✓ {ok} resoluções aplicadas · coletas atualizadas: {coletas_tot:,}")
    else:
        logger.warning(
            f"⚠ {ok} aplicadas, {errors} com erro · coletas atualizadas: {coletas_tot:,}"
        )


if __name__ == "__main__":
    main()
