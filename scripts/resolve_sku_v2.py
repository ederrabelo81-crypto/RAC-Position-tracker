#!/usr/bin/env python3
"""
scripts/resolve_sku_v2.py — Resolução de SKU v2 (de-para por atributos).

Implementa FASE 2/3 do plano: re-deriva atributos de cada `coletas.produto`
(com `utils.sku_matcher`, que reusa o resolver de família testado + attr_parser),
crava SKU quando há 1 único candidato no catálogo e manda o resto para
pendências — SEM tocar `produto`, `sku_resolvido` ou `familia_resolvida` legados.

Idempotente e incremental: a resolução é por TÍTULO DISTINTO (`produto`), chave
natural do de-para; re-rodar só reprocessa títulos novos/alterados. O resultado
vai para `public.coletas_sku_resolucao` (uma linha por título) e a fila de
revisão para `public.depara_pendencias` (view) — ver migração
docs/migrations/009_sku_resolucao_v2.sql. A promoção (view canônica que o
dashboard consome) é passo SEPARADO e só após aprovação do dry-run.

MODOS
  OFFLINE (sem rede; usado para o dry-run versionado):
    python scripts/resolve_sku_v2.py \
        --offline-cat /tmp/cat.json --offline-prod /tmp/prod.json \
        --out reports/depara_resolucao_v2.csv

  LIVE (Supabase; requer SUPABASE_URL/SUPABASE_KEY no .env):
    python scripts/resolve_sku_v2.py            # dry-run: resolve e resume, não grava
    python scripts/resolve_sku_v2.py --apply    # grava em coletas_sku_resolucao
    python scripts/resolve_sku_v2.py --full      # reprocessa todos os títulos

Formatos JSON offline:
  cat:  [[sku, marca, btu, ciclo, familia_linha, voltagem], ...]
  prod: [[produto, marca_raw], ...]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.sku_matcher import build_catalog, resolve_sku  # noqa: E402

_PAGE = 1000
_CSV_FIELDS = [
    "produto", "marca_raw", "estado", "familia_v2", "sku_v2",
    "confianca", "metodo", "motivo", "candidatos", "atributos",
]


def _cat_rows_from_arrays(arr: List[list]) -> List[dict]:
    """[[sku,marca,btu,ciclo,fam,volt], ...] → dicts p/ build_catalog."""
    out = []
    for r in arr or []:
        out.append({
            "sku": r[0], "marca": r[1], "capacidade_btu": r[2],
            "ciclo": r[3], "familia_linha": r[4], "voltagem": r[5],
            "ativo": True,
        })
    return out


def resolve_rows(cat_rows: List[dict], prod_rows: List[tuple]) -> List[dict]:
    """Núcleo puro: resolve cada (produto, marca_raw) contra o catálogo.

    Retorna lista de dicts prontos para CSV / upsert.
    """
    catalog = build_catalog(cat_rows)
    results = []
    for produto, marca_raw in prod_rows:
        res = resolve_sku(produto, marca_raw, catalog)
        results.append({
            "produto": produto,
            "marca_raw": marca_raw,
            "estado": res.estado,
            "familia_v2": res.familia_v2,
            "sku_v2": res.sku_v2,
            "confianca": res.confianca,
            "metodo": res.metodo,
            "motivo": res.motivo,
            "candidatos": "|".join(res.candidatos),
            "atributos": json.dumps(res.atributos, ensure_ascii=False),
        })
    return results


def summarize(results: List[dict]) -> dict:
    """Resumo agregado para log / dry-run."""
    by_estado = Counter(r["estado"] for r in results)
    by_conf = Counter(r["confianca"] for r in results)
    by_metodo = Counter(r["metodo"] for r in results)
    sku_cravado = sum(1 for r in results if r["sku_v2"])
    fam_only = sum(1 for r in results if r["estado"] == "MAPEADO" and not r["sku_v2"])
    skus_distintos = len({r["sku_v2"] for r in results if r["sku_v2"]})
    return {
        "titulos": len(results),
        "sku_cravado": sku_cravado,
        "familia_only": fam_only,
        "skus_distintos_v2": skus_distintos,
        "por_estado": dict(by_estado),
        "por_confianca": dict(by_conf),
        "por_metodo": dict(by_metodo),
    }


def write_csv(results: List[dict], out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS, delimiter=";")
        w.writeheader()
        w.writerows(sorted(results, key=lambda r: (r["estado"], r["familia_v2"] or "", r["produto"])))


# ─────────────────────────────────────────────────────────── modo OFFLINE
def run_offline(args) -> None:
    cat_arr = json.loads(Path(args.offline_cat).read_text(encoding="utf-8"))
    prod_arr = json.loads(Path(args.offline_prod).read_text(encoding="utf-8"))
    cat_rows = _cat_rows_from_arrays(cat_arr)
    prod_rows = [(p[0], p[1] if len(p) > 1 else None) for p in prod_arr]
    results = resolve_rows(cat_rows, prod_rows)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(results, out)
    print(json.dumps(summarize(results), ensure_ascii=False, indent=2))
    print(f"\nCSV: {out}  ({len(results)} títulos)")


# ─────────────────────────────────────────────────────────────── modo LIVE
def _client():
    try:
        from supabase import create_client
    except ImportError:
        sys.exit("Falta `supabase`. pip install supabase python-dotenv")
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        sys.exit("SUPABASE_URL/SUPABASE_KEY não configurados no .env")
    return create_client(url, key)


def _paged(client, table: str, select: str) -> List[dict]:
    rows, off = [], 0
    while True:
        resp = client.table(table).select(select).range(off, off + _PAGE - 1).execute()
        if not resp.data:
            break
        rows.extend(resp.data)
        if len(resp.data) < _PAGE:
            break
        off += _PAGE
    return rows


def _fetch_catalog(client) -> List[dict]:
    """Catálogo REFINADO de `public.sku_catalog` (familia_linha já split a partir
    do pricetrack + `sku_canonico` da deduplicação). Mantém só a linha CANÔNICA
    de cada grupo (`sku_canonico == sku`), para o matcher cravar o SKU canônico —
    senão o `--apply` reproduziria o catálogo legado (famílias grossas + SKUs
    duplicados) que a FASE 1 corrige.

    Fallback no legado `produtos_catalogo` se `sku_catalog` ainda não existir/
    estiver vazio (antes de aplicar a migração 009 + carregar o CSV refinado).
    """
    try:
        rows = _paged(client, "sku_catalog",
                      "sku,marca,capacidade_btu,ciclo,familia_linha,voltagem,sku_canonico")
    except Exception as exc:  # noqa: BLE001
        # Só faz fallback se a tabela ainda não existe; erros reais (rede,
        # permissão, SQL) são re-levantados, não mascarados.
        msg = f"{getattr(exc, 'message', '')} {getattr(exc, 'code', '')} {exc}".lower()
        if not any(s in msg for s in ("does not exist", "could not find the table",
                                      "42p01", "pgrst205", "pgrst202")):
            raise
        rows = []
    if rows:
        out = []
        for r in rows:
            canon = r.get("sku_canonico") or r.get("sku")
            if canon != r.get("sku"):
                continue   # SKU absorvido por um canônico — descarta a duplicata
            out.append({"sku": r.get("sku"), "marca": r.get("marca"),
                        "capacidade_btu": r.get("capacidade_btu"), "ciclo": r.get("ciclo"),
                        "familia_linha": r.get("familia_linha"),
                        "voltagem": r.get("voltagem"), "ativo": True})
        return out
    print("[resolve_sku_v2] sku_catalog vazio — fallback p/ produtos_catalogo "
          "(catálogo legado, sem refino/dedup). Aplique a migração 009 e carregue "
          "reports/sku_catalog_refined.csv para usar o catálogo refinado.",
          file=sys.stderr)
    legacy = _paged(client, "produtos_catalogo",
                    "sku,marca,capacidade_btu,ciclo,familia_linha,voltagem,ativo")
    return [r for r in legacy if r.get("ativo", True)]


def _fetch_prod(client, full: bool) -> List[tuple]:
    """Títulos DISTINTOS a resolver. Incremental: pula os já resolvidos."""
    done = set()
    if not full:
        off = 0
        while True:
            resp = (client.table("coletas_sku_resolucao")
                    .select("produto").range(off, off + _PAGE - 1).execute())
            if not resp.data:
                break
            done.update(r["produto"] for r in resp.data)
            if len(resp.data) < _PAGE:
                break
            off += _PAGE
    seen, out, off = {}, [], 0
    while True:
        resp = (client.table("coletas").select("produto,marca")
                .not_.is_("produto", "null")
                .range(off, off + _PAGE - 1).execute())
        if not resp.data:
            break
        for r in resp.data:
            p = r["produto"]
            if p and p not in seen and p not in done:
                seen[p] = r.get("marca")
                out.append((p, r.get("marca")))
        if len(resp.data) < _PAGE:
            break
        off += _PAGE
    return out


def run_live(args) -> None:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
    client = _client()
    cat_rows = _fetch_catalog(client)
    prod_rows = _fetch_prod(client, full=args.full)
    results = resolve_rows(cat_rows, prod_rows)
    print(json.dumps(summarize(results), ensure_ascii=False, indent=2))
    if not args.apply:
        print("\nDRY-RUN — nada gravado. Use --apply para gravar em coletas_sku_resolucao.")
        return
    payload = [{
        "produto": r["produto"], "sku_v2": r["sku_v2"], "familia_v2": r["familia_v2"],
        "estado": r["estado"], "confianca": r["confianca"], "metodo": r["metodo"],
        "motivo": r["motivo"], "candidatos": r["candidatos"] or None,
        "atributos": json.loads(r["atributos"]),
    } for r in results]
    for i in range(0, len(payload), 500):
        client.table("coletas_sku_resolucao").upsert(
            payload[i:i + 500], on_conflict="produto").execute()
    print(f"✓ {len(payload)} resoluções gravadas em coletas_sku_resolucao.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Resolução de SKU v2 (de-para por atributos)")
    ap.add_argument("--offline-cat", help="JSON do catálogo (modo offline)")
    ap.add_argument("--offline-prod", help="JSON dos títulos (modo offline)")
    ap.add_argument("--out", default="reports/depara_resolucao_v2.csv",
                    help="CSV de saída (modo offline)")
    ap.add_argument("--apply", action="store_true", help="LIVE: grava no Supabase")
    ap.add_argument("--full", action="store_true", help="LIVE: reprocessa todos os títulos")
    args = ap.parse_args()
    if args.offline_cat and args.offline_prod:
        run_offline(args)
    else:
        run_live(args)


if __name__ == "__main__":
    main()
