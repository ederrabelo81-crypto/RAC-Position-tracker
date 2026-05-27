"""
scripts/descobrir_nomes_novos.py — Lista nomes coletados sem correspondência
no de-para `produtos_depara_nome` (fila de revisão).

Exporta CSV `nomes_novos_para_classificar.csv` para Eder classificar
manualmente e reimportar via scripts/montar_depara.py.

USO:
    python scripts/descobrir_nomes_novos.py
    python scripts/descobrir_nomes_novos.py --tabela coletas
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

try:
    from supabase import create_client, Client
except ImportError:
    logger.error("Falta `supabase`. Instale com: pip install supabase python-dotenv")
    sys.exit(1)


TABELAS = {
    "rac_monitoramento": ("produto_sku", "marca_monitorada"),
    "coletas":           ("produto",     "marca"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabela", choices=["rac_monitoramento", "coletas", "ambas"], default="ambas")
    ap.add_argument("--out", default="nomes_novos_para_classificar.csv")
    args = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL/SUPABASE_KEY não configurados no .env")
        sys.exit(1)

    client: Client = create_client(url, key)
    tabelas = ["rac_monitoramento", "coletas"] if args.tabela == "ambas" else [args.tabela]

    novos: dict[str, dict] = {}

    for tbl in tabelas:
        name_col, brand_col = TABELAS[tbl]
        logger.info(f"Buscando nomes novos em {tbl}…")
        # Filtra estado_match IS NULL (não há entrada no de-para)
        offset, page = 0, 1000
        while True:
            resp = (client.table(tbl)
                    .select(f"{name_col},{brand_col}")
                    .is_("estado_match", "null")
                    .not_.is_(name_col, "null")
                    .range(offset, offset + page - 1)
                    .execute())
            if not resp.data:
                break
            for row in resp.data:
                n = row.get(name_col)
                if not n:
                    continue
                entry = novos.setdefault(n, {
                    "nome_coletado": n,
                    "marca_raw": row.get(brand_col),
                    "ocorrencias": 0,
                    "origem_tabela": tbl,
                })
                entry["ocorrencias"] += 1
            if len(resp.data) < page:
                break
            offset += page

    if not novos:
        logger.success("Nenhum nome novo a classificar — fila limpa.")
        return

    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["nome_coletado", "marca_raw", "ocorrencias", "origem_tabela",
                           "estado_sugerido", "familia_sugerida", "sku_sugerido"],
            delimiter=";",
        )
        w.writeheader()
        for r in sorted(novos.values(), key=lambda x: -x["ocorrencias"]):
            w.writerow({**r, "estado_sugerido": "", "familia_sugerida": "", "sku_sugerido": ""})

    logger.success(f"{len(novos)} nomes novos exportados para {out_path}")
    logger.info("Preencha as colunas estado_sugerido/familia_sugerida/sku_sugerido e reimporte.")


if __name__ == "__main__":
    main()
