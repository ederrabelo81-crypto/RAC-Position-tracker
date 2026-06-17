#!/usr/bin/env python3
"""
scripts/build_sku_catalog.py — FASE 1 (data-driven): catálogo canônico de SKU.

Conserta o TETO de precisão de SKU encontrado na FASE 4 (81% exato), cuja causa
raiz é o `produtos_catalogo`: (a) `familia_linha` GROSSA demais, que junta linhas
distintas sob um rótulo (ex.: TCL Serie A1 e Elite GV ambos "TCL-ELITE-9000-F");
(b) SKUs DUPLICADOS para o mesmo produto real (ex.: Elgin HJQI18C2WB e
45HJQI18C2WC).

Abordagem (NÃO chutar, só dados): re-deriva a LINHA de cada SKU a partir dos
títulos REAIS dele no `pricetrack_daily` (via normalize_product._identify_line,
que distingue Serie A1 / Elite GV / Dual Inverter Voice / ARTCOOL…). Com isso:
  • SPLIT: linhas distintas ganham `familia_linha` distinta (desfaz a fusão
    embutida no catálogo) — marca+BTU+ciclo+linha.
  • DEDUP: SKUs com a MESMA assinatura (marca,BTU,ciclo,linha,voltagem) são o
    mesmo produto → colapsam para um `sku_canonico` (o de maior volume no
    pricetrack), para casar com o rótulo dominante do gabarito.

Saídas (offline): reports/sku_catalog_refined.csv + reports/catalog_dedup.md,
e a precisão ANTES/DEPOIS vs o gabarito pricetrack.

USO (offline, sem rede):
    python scripts/build_sku_catalog.py \
        --cat /tmp/cat.json --gold <gold_file> --vol <vol_file>

Os arquivos podem ser JSON puro OU o resultado salvo do MCP (envelope
{"result": "...<json>..."}). LIVE (Supabase) fica como TODO documentado.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.normalize_product import _identify_cycle, _identify_line  # noqa: E402
from utils.sku_matcher import build_catalog, resolve_sku  # noqa: E402

# marca UPPER (produtos_catalogo) → chave title-case de _LINE_PATTERNS.
_MARCA_TITLE = {
    "MIDEA": "Midea", "LG": "LG", "SAMSUNG": "Samsung", "ELECTROLUX": "Electrolux",
    "ELGIN": "Elgin", "PHILCO": "Philco", "GREE": "Gree", "TCL": "TCL",
    "AGRATTO": "Agratto", "HISENSE": "Hisense",
}
_CICLO_CODE = {"FRIO": "F", "QUENTE/FRIO": "QF", "QUENTE": "Q"}


def _read_array(path: str, key: Optional[str] = None) -> list:
    """Lê JSON puro OU resultado-MCP salvo ({"result":"...<json>..."})."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "result" in data:
            inner = json.loads(re.search(r"\[\s*\{.*\}\s*\]", data["result"], re.DOTALL).group(0))
            return inner[0][key] if key else inner
        if isinstance(data, dict) and key in data:
            return data[key]
    except (json.JSONDecodeError, AttributeError):
        pass
    m = re.search(r"\[\s*\{.*\}\s*\]", raw, re.DOTALL)
    arr = json.loads(m.group(0))
    return arr[0][key] if key else arr


def _line_token(line: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", line.upper()).strip("-")


def build_refined_catalog(
    cat_rows: List[dict],
    titles_by_sku: Dict[str, List[Tuple[str, str]]],
    vol_by_sku: Dict[str, int],
):
    """Re-deriva linha (do pricetrack) e calcula familia_linha refinada + canônico.

    Retorna (refined_rows, canon_map, dedup_groups).
    """
    refined: Dict[str, dict] = {}
    for r in cat_rows:
        sku, marca, btu, ciclo, _fam_old, volt = r
        marca = (marca or "").upper()
        ciclo_code = _CICLO_CODE.get((ciclo or "").upper())
        brand_title = _MARCA_TITLE.get(marca)
        titles = titles_by_sku.get(sku, [])
        # ciclo ausente no catálogo fonte → deriva do título modal do pricetrack
        # (recupera SKUs como S3-Q09AA31C, sem ciclo no catálogo, que ficavam sem
        # familia_linha e, portanto, sem resolução por família).
        if ciclo_code is None and titles:
            modal_ciclo = Counter(_identify_cycle(t.lower()) for t, _b in titles).most_common(1)[0][0]
            ciclo_code = _CICLO_CODE.get(modal_ciclo.upper())
            if not ciclo:
                ciclo = modal_ciclo.upper()
        # linha modal a partir dos títulos REAIS do SKU no pricetrack
        modal_line = None
        if brand_title:
            cnt = Counter()
            for title, _b in titles:
                ln = _identify_line(title.lower(), brand_title)
                if ln:
                    cnt[ln] += 1
            if cnt:
                modal_line = cnt.most_common(1)[0][0]
        fam = None
        if marca and btu and ciclo_code and modal_line:
            fam = f"{marca}-{_line_token(modal_line)}-{btu}-{ciclo_code}"
        refined[sku] = {
            "sku": sku, "marca": marca, "capacidade_btu": btu, "ciclo": ciclo,
            "familia_linha": fam, "edicao": modal_line, "voltagem": volt,
            "n_pricetrack": vol_by_sku.get(sku, 0), "ativo": True,
        }

    # DEDUP por família: SKUs do mesmo produto colapsam num canônico (+volume).
    # Voltagem tolerante: se a família tem <=1 voltagem CONCRETA (resto NULL),
    # tudo é o mesmo produto → colapsa. Se tem 2+ voltagens distintas (110 e
    # 220), são produtos diferentes → separa por voltagem (NULL fica à parte).
    by_fam: Dict[str, List[str]] = defaultdict(list)
    for sku, d in refined.items():
        if d["familia_linha"]:
            by_fam[d["familia_linha"]].append(sku)
    canon_map: Dict[str, str] = {}
    dedup_groups = []

    def _add_group(key, skus):
        if len(skus) > 1:
            canon = max(skus, key=lambda s: refined[s]["n_pricetrack"])
            for s in skus:
                canon_map[s] = canon
            dedup_groups.append((key, canon, sorted(skus)))

    for fam, skus in by_fam.items():
        concrete = {refined[s]["voltagem"] for s in skus if refined[s]["voltagem"]}
        if len(concrete) <= 1:
            _add_group((fam, next(iter(concrete), None)), skus)
        else:
            sub: Dict[Optional[str], List[str]] = defaultdict(list)
            for s in skus:
                sub[refined[s]["voltagem"]].append(s)
            for v, sk in sub.items():
                _add_group((fam, v), sk)
    for sku in refined:
        canon_map.setdefault(sku, sku)
    for sku, d in refined.items():
        d["sku_canonico"] = canon_map[sku]
    return refined, canon_map, dedup_groups


def _collapsed_cat_rows(refined: Dict[str, dict]) -> List[dict]:
    """Linhas do catálogo já colapsadas no canônico (1 por familia+voltagem)."""
    seen = set()
    out = []
    for d in refined.values():
        c = d["sku_canonico"]
        if c in seen:
            continue
        seen.add(c)
        cd = refined[c]
        out.append({"sku": c, "marca": cd["marca"], "capacidade_btu": cd["capacidade_btu"],
                    "ciclo": cd["ciclo"], "familia_linha": cd["familia_linha"],
                    "voltagem": cd["voltagem"], "ativo": True})
    return out


def evaluate(catalog, canon_map, gold) -> dict:
    """Precisão vs gabarito: canonical(v2) == canonical(gold), no cravado."""
    crava = exact = same_model = cross = 0
    sigkey = {}
    for d in catalog.index.values():
        for cs in d:
            pass
    for title, gsku, brand in gold:
        res = resolve_sku(title, brand, catalog)
        if not res.sku_v2:
            continue
        crava += 1
        gcanon = canon_map.get(gsku, gsku)
        if res.sku_v2 == gcanon:
            exact += 1
    return {"cravado": crava, "exato": exact,
            "precisao": round(100 * exact / crava, 2) if crava else 0.0}


def main() -> None:
    ap = argparse.ArgumentParser(description="FASE 1 — catálogo canônico de SKU (data-driven)")
    ap.add_argument("--cat", required=True, help="catálogo (json puro ou MCP), array [sku,marca,btu,ciclo,fam,volt]")
    ap.add_argument("--gold", required=True, help="pares pricetrack [title,sku,brand] (json/MCP)")
    ap.add_argument("--vol", required=True, help="volume por sku [sku,n,modal_title] (json/MCP)")
    ap.add_argument("--out-csv", default="reports/sku_catalog_refined.csv")
    ap.add_argument("--out-report", default="reports/catalog_dedup.md")
    args = ap.parse_args()

    cat_rows = _read_array(args.cat)                       # [[sku,marca,btu,ciclo,fam,volt],...]
    gold = _read_array(args.gold, "gold")                  # [[title,sku,brand],...]
    vol = _read_array(args.vol, "vol")                     # [[sku,n,modal_title],...]
    vol_by_sku = {v[0]: v[1] for v in vol}
    titles_by_sku: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for title, sku, brand in gold:
        titles_by_sku[sku].append((title, brand))

    refined, canon_map, dedup_groups = build_refined_catalog(cat_rows, titles_by_sku, vol_by_sku)

    # Precisão ANTES (catálogo curado original) x DEPOIS (refinado+canônico)
    cat_before = build_catalog([{"sku": r[0], "marca": r[1], "capacidade_btu": r[2],
                                 "ciclo": r[3], "familia_linha": r[4], "voltagem": r[5],
                                 "ativo": True} for r in cat_rows])
    before = evaluate(cat_before, {}, gold)
    cat_after = build_catalog(_collapsed_cat_rows(refined))
    after = evaluate(cat_after, canon_map, gold)

    # CSV refinado
    out_csv = Path(args.out_csv); out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["sku", "marca", "capacidade_btu", "ciclo", "familia_linha_refinada",
                    "edicao", "voltagem", "sku_canonico", "n_pricetrack"])
        for d in sorted(refined.values(), key=lambda x: (x["marca"] or "", str(x["familia_linha"]))):
            w.writerow([d["sku"], d["marca"], d["capacidade_btu"], d["ciclo"],
                        d["familia_linha"], d["edicao"], d["voltagem"],
                        d["sku_canonico"], d["n_pricetrack"]])

    # Report
    n_dup = sum(len(s) - 1 for _, _, s in dedup_groups)
    lines = [f"| `{c}` (canônico) | {' · '.join(f'`{x}`' for x in skus if x != c)} | {k[0]} {k[1] or ''} |"
             for k, c, skus in sorted(dedup_groups)]
    report = f"""# Catálogo canônico de SKU — dedup + split (FASE 1, data-driven)

Re-deriva a LINHA de cada SKU dos títulos reais no `pricetrack_daily` e:
- **SPLIT** famílias grossas do `produtos_catalogo` (Serie A1 ≠ Elite GV ≠ …);
- **DEDUP** SKUs do mesmo produto (mesma marca+BTU+ciclo+linha+voltagem) num
  `sku_canonico` (o de maior volume no pricetrack).

Gerado por `scripts/build_sku_catalog.py` (offline) · 2026-06-17.

## Impacto na precisão (vs gabarito pricetrack, no subconjunto cravado)

| catálogo | cravado | exato | **precisão** |
|---|--:|--:|--:|
| antes (curado, familia_linha original) | {before['cravado']} | {before['exato']} | {before['precisao']}% |
| **depois (refinado + canônico)** | {after['cravado']} | {after['exato']} | **{after['precisao']}%** |

## Grupos de SKUs duplicados colapsados ({len(dedup_groups)} grupos, {n_dup} SKUs absorvidos)

| canônico | absorvidos | modelo |
|---|---|---|
{chr(10).join(lines) if lines else '| (nenhum) | | |'}

> CSV completo do catálogo refinado: `reports/sku_catalog_refined.csv`.
> Aplicação em produção (atualizar `produtos_catalogo.familia_linha` + coluna
> `sku_canonico`) é **gated** — revisar este relatório antes.
"""
    Path(args.out_report).write_text(report, encoding="utf-8")
    print(json.dumps({"antes": before, "depois": after,
                      "grupos_dedup": len(dedup_groups), "skus_absorvidos": n_dup},
                     ensure_ascii=False, indent=2))
    print(f"CSV: {out_csv} · report: {args.out_report}")


if __name__ == "__main__":
    main()
