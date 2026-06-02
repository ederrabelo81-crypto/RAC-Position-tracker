"""
scripts/montar_depara.py — Constrói/atualiza public.produtos_depara_nome.

Para cada nome distinto coletado em rac_monitoramento.produto_sku e
coletas.produto, classifica em:
    MAPEADO       — bate com família (real ou genérica) do catálogo RAC High Wall
    FORA_ESCOPO   — é ar-condicionado mas fora do catálogo (janela, portátil,
                    cassete, piso-teto, multi-split, marcas não-catalogadas)
    NAO_AC        — não é ar-condicionado (peças, geladeira, climatizador, etc.)
    REVISAR       — não foi possível classificar com confiança → fila humana

A classificação de marca/BTU/ciclo é delegada a `utils.depara_resolver`
(primitivas robustas de `utils.normalize_product`); este script mantém apenas
os guardas NAO_AC (não é AC) e FORA_TIPO (janela/cassete/portátil/36k+), que
rodam ANTES do matcher forte.

Famílias genéricas (quando marca catalogada mas linha comercial não detectada)
têm o formato <MARCA>-<BTU>-<CICLO>, ex.: MIDEA-12000-F, LG-9000-QF. Quando o
catálogo tem uma única linha para (marca, BTU, ciclo), a família genérica é
promovida à `familia_linha` exata. Nunca inventa SKU (mantém-se NULL).

REQUISITOS:
    pip install supabase python-dotenv

USO:
    python scripts/montar_depara.py
    python scripts/montar_depara.py --export-csv depara_para_revisao.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
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

from utils.depara_resolver import CatalogFamilias, resolve_depara

# `supabase` é importado preguiçosamente em main()/load_catalog_familias: as
# funções puras deste módulo (classify, guardas regex) não dependem de rede e
# precisam ser importáveis sem o pacote instalado (ex.: testes, reuso).


# Não-AC: peças automotivas, eletrodomésticos, acessórios, climatizadores
NAO_AC_REGEX = [
    re.compile(p, re.IGNORECASE) for p in [
        r"report a violation",
        r"\bfiltro\b.*(palio|corolla|sedan|automotiv|veicular|fiat|toyota|honda|gm|chevrolet|ford|volkswagen|vw|kombi|stilo|craftsman)",
        r"\bradiador\b",
        r"\bevaporador\b(?!.*split)",
        r"\bcondensador\b.*(stilo|acquaflex|libell|original)",
        r"\bcompressor\b.*(delphi|sanden|valvula|válvula|torneira)",
        r"\b(polia|válvula|valvula|torneira|porca|cooler intel|injetora|garrafa)\b",
        r"\bshampoo\b|\bcondicionador\b.*(hidratante|capilar|antiqueda|aminoacid|aminoácid)",
        r"\bcolch[aã]o\b|\bbarraca\b|\binfl[aá]vel\b",
        r"\bgeladeira\b|\bfrigobar\b|\bfreezer\b|\bair fryer\b|\bfritadeira\b",
        r"\bumidificador\b|\bclimatizador\b|\bventilador\b|\baromatizador\b",
        r"\bmini ar condicionado\b|\busb\b.*ar condicionado|ar condicionado.*\busb\b",
        r"\bcontrole remoto\b|\bcapa para\b|\bsuporte para ar\b",
        r"\bmelhor (mini )?ar condicionado 20\d\d\b",
        r"\bcortina de ar\b",
        r"\bcervejeira\b|\blavadora\b|\bsecadora\b|\bmicro-?ondas\b|\bfog[aã]o\b",
        r"\bserpentina\b|\bcontrole\b|\bh[aá]lite\b|\borganizador\b|\bcarregador\b",
        r"\bprotetor\b|\bcapa\b|\bsuporte\b",
        r"\bunidade condensadora\b|\bhigienizador\b|\bmanifold\b|\bmangueira\b",
        # Componentes/peças avulsas que carregam marca+BTU (não são a unidade):
        r"\bcondensadora?\b|\bevaporadora\b|\bcompressor\b|\bturbina\b",
        r"\bplaca\b|\bgabinete\b|\bbobina\b|\bfus[íi]vel\b|\btermistor\b",
        r"\bjunta\b|\btransformador\b|\bpressostato\b|\bdifusor\b|\bdefletor\b",
        r"\binversor\b|\bbomba\b\s*dreno|\bdreno\b|\bman[ôo]metro\b|\bcoxim",
        r"\bauto[\s-]?transformador\b|\bcaixa\b.*\bpassagem\b",
    ]
]

# Fora-de-escopo por tipo (mesmo que marca seja catalogada)
FORA_TIPO_REGEX = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(janela|janeleiro|window)\b",
        r"\bport[aá]til\b",
        r"\bcassete\b|\bcassette\b",
        r"piso[ \-]teto",
        r"multi[ \-]?split|multisplit",
        r"bi[\s-]?split|\b2x9\b|\b2x12\b|\b2x18\b",
        r"\b(36|32|34|48|57|60)\.?000\s*btu",
        r"\b(36|32|34|48|57|60)k\s*btu",
        r"\b(7|7\.500|16)\.?000?\s*btu",
        r"\bsplit[aã]o\b|\btrif[aá]sico\b|\b7,?5\s*tr\b",
    ]
]

def load_catalog_familias(client: "Client") -> CatalogFamilias:
    """
    Carrega do catálogo o mapa (marca, BTU, ciclo_code) → {familia_linha}.

    Usado por `resolve_depara` para promover a família genérica à linha exata
    do catálogo quando há apenas uma linha possível para o trio. `ciclo` do
    catálogo ("FRIO"/"QUENTE/FRIO") é reduzido ao código curto ("F"/"QF").
    """
    _CICLO_CODE = {"FRIO": "F", "QUENTE/FRIO": "QF", "QUENTE": "Q"}
    familias: CatalogFamilias = {}
    offset, page = 0, 1000
    while True:
        resp = (client.table("produtos_catalogo")
                .select("marca,capacidade_btu,ciclo,familia_linha")
                .not_.is_("familia_linha", "null")
                .range(offset, offset + page - 1)
                .execute())
        if not resp.data:
            break
        for row in resp.data:
            marca = row.get("marca")
            btu = row.get("capacidade_btu")
            ciclo = _CICLO_CODE.get((row.get("ciclo") or "").upper())
            fam = row.get("familia_linha")
            if not (marca and btu and ciclo and fam):
                continue
            familias.setdefault((marca, int(btu), ciclo), set()).add(fam)
        if len(resp.data) < page:
            break
        offset += page
    return familias


def load_catalog_btus(client: "Client") -> set[int]:
    """Conjunto de capacidades (BTU) presentes no catálogo (para gate de escopo)."""
    btus: set[int] = set()
    offset, page = 0, 1000
    while True:
        resp = (client.table("produtos_catalogo")
                .select("capacidade_btu")
                .not_.is_("capacidade_btu", "null")
                .range(offset, offset + page - 1)
                .execute())
        if not resp.data:
            break
        for row in resp.data:
            try:
                btus.add(int(row["capacidade_btu"]))
            except (TypeError, ValueError, KeyError):
                continue
        if len(resp.data) < page:
            break
        offset += page
    return btus


def classify(
    nome: str,
    marca_raw: Optional[str],
    catalog_familias: Optional[CatalogFamilias] = None,
    catalog_btus: Optional[set[int]] = None,
) -> dict:
    """
    Classifica um nome coletado em estado/familia/sku/marca_norm.

    Guardas NAO_AC e FORA_TIPO (janela/cassete/portátil/36k+) rodam primeiro;
    o restante (marca/BTU/ciclo + catálogo) é delegado a `resolve_depara`.
    """
    if any(p.search(nome) for p in NAO_AC_REGEX):
        return {"estado": "NAO_AC", "familia": None, "sku": None, "marca_norm": None}

    if any(p.search(nome) for p in FORA_TIPO_REGEX):
        return {"estado": "FORA_ESCOPO", "familia": None, "sku": None, "marca_norm": None}

    res = resolve_depara(nome, marca_raw, catalog_familias, catalog_btus)
    return {
        "estado": res.estado,
        "familia": res.familia,
        "sku": res.sku,
        "marca_norm": res.marca_norm,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-csv", default="depara_para_revisao.csv",
                    help="Saída CSV de REVISAR + MAPEADO genéricas (default: depara_para_revisao.csv)")
    ap.add_argument("--no-write", action="store_true", help="Não escreve no DB; só classifica e exporta CSV")
    args = ap.parse_args()

    try:
        from supabase import create_client
    except ImportError:
        logger.error("Falta `supabase`. Instale com: pip install supabase python-dotenv")
        sys.exit(1)

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL/SUPABASE_KEY não configurados no .env")
        sys.exit(1)

    client = create_client(url, key)

    logger.info("Carregando catálogo (famílias + capacidades)…")
    catalog_familias = load_catalog_familias(client)
    catalog_btus = load_catalog_btus(client)
    logger.info(
        f"Catálogo: {len(catalog_familias)} combos (marca, BTU, ciclo); "
        f"{len(catalog_btus)} capacidades."
    )

    # Coleta nomes distintos das duas tabelas
    logger.info("Buscando nomes distintos em rac_monitoramento e coletas…")
    nomes: dict[str, str | None] = {}

    for tbl, name_col, brand_col in [
        ("rac_monitoramento", "produto_sku", "marca_monitorada"),
        ("coletas",           "produto",     "marca"),
    ]:
        offset, page = 0, 1000
        while True:
            resp = (client.table(tbl)
                    .select(f"{name_col},{brand_col}")
                    .not_.is_(name_col, "null")
                    .range(offset, offset + page - 1)
                    .execute())
            if not resp.data:
                break
            for row in resp.data:
                n = row.get(name_col)
                if n and n not in nomes:
                    nomes[n] = row.get(brand_col)
            if len(resp.data) < page:
                break
            offset += page

    logger.info(f"Coletados {len(nomes)} nomes distintos. Classificando…")
    classificados = [
        {"nome_coletado": n, **classify(n, b, catalog_familias, catalog_btus)}
        for n, b in nomes.items()
    ]

    if not args.no_write:
        logger.info("Fazendo UPSERT em produtos_depara_nome (lotes de 500)…")
        for i in range(0, len(classificados), 500):
            batch = [{
                "nome_coletado": r["nome_coletado"],
                "estado": r["estado"],
                "familia": r["familia"],
                "sku": r["sku"],
                "marca_norm": r["marca_norm"],
                "origem": "seed_generic" if r["estado"] == "MAPEADO" else "seed",
            } for r in classificados[i:i+500]]
            client.table("produtos_depara_nome").upsert(batch, on_conflict="nome_coletado").execute()

    # Exporta CSV para revisão humana (REVISAR + MAPEADO genéricas)
    rows_csv = [r for r in classificados
                if r["estado"] == "REVISAR" or (r["estado"] == "MAPEADO" and r["sku"] is None)]
    out_path = Path(args.export_csv)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["nome_coletado", "marca_norm", "estado", "familia", "sku"],
                           delimiter=";")
        w.writeheader()
        for r in sorted(rows_csv, key=lambda x: (x["estado"], x["marca_norm"] or "", x["nome_coletado"])):
            w.writerow({k: r.get(k) for k in w.fieldnames})

    by_estado: dict = {}
    for r in classificados:
        by_estado[r["estado"]] = by_estado.get(r["estado"], 0) + 1
    logger.success(f"Distribuição: {by_estado}")
    logger.success(f"CSV de revisão: {out_path} ({len(rows_csv)} linhas)")


if __name__ == "__main__":
    main()
