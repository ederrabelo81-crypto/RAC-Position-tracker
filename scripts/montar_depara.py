"""
scripts/montar_depara.py — Constrói/atualiza public.produtos_depara_nome.

Para cada nome distinto coletado em rac_monitoramento.produto_sku e
coletas.produto, classifica em:
    MAPEADO       — bate com família (real ou genérica) do catálogo RAC High Wall
    FORA_ESCOPO   — é ar-condicionado mas fora do catálogo (janela, portátil,
                    cassete, piso-teto, multi-split, marcas não-catalogadas)
    NAO_AC        — não é ar-condicionado (peças, geladeira, climatizador, etc.)
    REVISAR       — não foi possível classificar com confiança → fila humana

Famílias genéricas (quando marca catalogada mas linha comercial não detectada)
têm o formato <MARCA>-<BTU>-<CICLO>, ex.: MIDEA-12000-F, LG-9000-QF.

Nunca inventa SKU: 'sku' só é preenchido quando a linha comercial detectada
casa unicamente com 1 SKU do catálogo.

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


# ---------------------------------------------------------------------------
# Normalização de marca (DB raw value → canonical UPPER)
# ---------------------------------------------------------------------------
MARCA_NORM = {
    "springer midea": "MIDEA", "midea carrier": "MIDEA",
    "springer": "MIDEA",       "midea": "MIDEA",
    "lg": "LG", "samsung": "SAMSUNG", "electrolux": "ELECTROLUX",
    "elgin": "ELGIN", "philco": "PHILCO", "gree": "GREE", "tcl": "TCL",
    "agratto": "AGRATTO", "hisense": "HISENSE",
    # Fora do catálogo:
    "carrier": "CARRIER", "consul": "CONSUL", "daikin": "DAIKIN",
    "fujitsu": "FUJITSU", "hitachi": "HITACHI", "haier": "HAIER",
    "york": "YORK", "eos": "EOS", "hq": "HQ", "aiwa": "AIWA",
    "vix": "VIX", "rheem": "RHEEM", "kian": "KIAN",
    "britânia": "BRITANIA", "britania": "BRITANIA",
    "equation": "EQUATION", "delonghi": "DELONGHI",
    "aufit": "AUFIT", "komeco": "KOMECO",
}

MARCAS_CATALOGO = {
    "MIDEA","LG","SAMSUNG","ELECTROLUX","ELGIN",
    "PHILCO","GREE","TCL","AGRATTO","HISENSE",
}

MARCAS_FORA_ESCOPO = {
    "DAIKIN","FUJITSU","HITACHI","HAIER","YORK","EOS","HQ","AIWA","CONSUL",
    "VIX","RHEEM","KIAN","BRITANIA","EQUATION","DELONGHI","CARRIER",
    "AUFIT","KOMECO","CHIGO","FONTAINE",
}

# Padrões para detectar marca pelo nome (quando marca_monitorada=Desconhecida)
BRAND_FROM_NAME = [
    (r"\baiwa\b",     "AIWA"),
    (r"\bdaikin\b",   "DAIKIN"),
    (r"\bfujitsu\b",  "FUJITSU"),
    (r"\bhitachi\b",  "HITACHI"),
    (r"\bhaier\b",    "HAIER"),
    (r"\bequation\b", "EQUATION"),
    (r"\bfontaine\b", "FONTAINE"),
    (r"brit[âa]nia",  "BRITANIA"),
    (r"\bdelonghi\b", "DELONGHI"),
    (r"\bcarrier\b",  "CARRIER"),
    (r"\bconsul\b",   "CONSUL"),
    (r"\bchigo\b",    "CHIGO"),
    (r"\bkomeco\b",   "KOMECO"),
    (r"\bhq\b",       "HQ"),
    (r"\bvix\b",      "VIX"),
    (r"\bkian\b",     "KIAN"),
    (r"\beos\b",      "EOS"),
    (r"\baufit\b",    "AUFIT"),
    (r"\brheem\b",    "RHEEM"),
    (r"\byork\b",     "YORK"),
]

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

BTU_REGEX = re.compile(
    r"\b(9\.000|9000|9k|12\.000|12000|12k|18\.000|18000|18k|22\.000|22000|22k|24\.000|24000|24k|30\.000|30000|30k)\b",
    re.IGNORECASE,
)
BTU_MAP = {
    "9.000": 9000, "9000": 9000, "9k": 9000,
    "12.000": 12000, "12000": 12000, "12k": 12000,
    "18.000": 18000, "18000": 18000, "18k": 18000,
    "22.000": 22000, "22000": 22000, "22k": 22000,
    "24.000": 24000, "24000": 24000, "24k": 24000,
    "30.000": 30000, "30000": 30000, "30k": 30000,
}

# Linhas comerciais por marca (linha mais específica primeiro)
LINHAS = [
    ("MIDEA",    "AI ECOMASTER",        r"\bai\s+ecomaster\b|\bi[\- ]ecomaster\b"),
    ("MIDEA",    "AIRVOLUTION CONNECT", r"airvolution\s+connect"),
    ("MIDEA",    "AIRVOLUTION LITE",    r"airvolution\s+lite"),
    ("MIDEA",    "AI AIRVOLUTION",      r"ai\s+airvolution"),
    ("MIDEA",    "AIRVOLUTION",         r"airvolution"),
    ("MIDEA",    "XTREME SAVE CONNECT", r"xtreme\s+save\s+connect"),
    ("MIDEA",    "XTREME SAVE",         r"xtreme\s+save"),
    ("MIDEA",    "BLACK EDITION",       r"black\s+edition"),
    ("LG",       "DUAL INVERTER VOICE", r"dual\s*inverter.*\bvoice\b|\bai\s+voice\b"),
    ("LG",       "DUAL INVERTER COMPACT", r"dual\s*inverter.*\bcompact\b|compact\s*\+?ai|\+ai"),
    ("LG",       "DUAL INVERTER ARTCOOL", r"dual\s*inverter.*artcool|\bartcool\b"),
    ("LG",       "DUAL INVERTER",       r"dual\s*inverter"),
    ("SAMSUNG",  "WINDFREE AI",         r"wind\s*free\s*ai|windfree\s*ai|wfree\s*ai"),
    ("SAMSUNG",  "WINDFREE BLACK",      r"wind\s*free.*black|wfree.*black"),
    ("SAMSUNG",  "WINDFREE",            r"wind\s*free|windfree|wfree"),
    ("ELECTROLUX","COLOR ADAPT",        r"color\s*adapt|colour\s*adapt"),
    ("ELECTROLUX","TRIPLE PROTECTION",  r"triple\s*protection"),
    ("ELGIN",    "ECO INVERTER II",     r"eco\s*inverter\s*(ii|2)"),
    ("ELGIN",    "ECO INVERTER",        r"eco\s*inverter"),
    ("ELGIN",    "ECO DREAM",           r"eco\s*dream"),
    ("TCL",      "T-PRO 2.0",           r"t[\s-]?pro\s*2"),
    ("TCL",      "ELITE GV",            r"elite\s*gv"),
    ("TCL",      "ELITE",               r"\belite\b"),
    ("TCL",      "FREE COOLER",         r"free\s*cooler"),
    ("TCL",      "FRESHIN",             r"fresh\s*in|freshin"),
    ("GREE",     "G-TOP",               r"g[\s-]?top"),
    ("GREE",     "G-DIAMOND",           r"g[\s-]?diamond"),
    ("GREE",     "G-CLASSIC",           r"g[\s-]?classic"),
    ("GREE",     "G-PRIME",             r"g[\s-]?prime"),
    ("PHILCO",   "INVERTER PLUS",       r"inverter\s*plus"),
    ("AGRATTO",  "LIV TOP",             r"liv\s*top"),
    ("AGRATTO",  "FIT TOP",             r"fit\s*top"),
    ("AGRATTO",  "ZEN TOP",             r"zen\s*top"),
    ("HISENSE",  "CONNECT",             r"hisense.*connect|connect.*hisense"),
]
LINHAS_COMPILED = [(m, l, re.compile(r, re.IGNORECASE)) for m, l, r in LINHAS]


def normalize_marca(raw: Optional[str], nome: str) -> Optional[str]:
    if raw:
        norm = MARCA_NORM.get(raw.strip().lower())
        if norm:
            return norm
    for pat, brand in BRAND_FROM_NAME:
        if re.search(pat, nome, re.IGNORECASE):
            return brand
    return None


def classify(nome: str, marca_raw: Optional[str]) -> dict:
    """Retorna dict com keys: estado, familia, sku, marca_norm."""
    marca = normalize_marca(marca_raw, nome)
    out = {"estado": "REVISAR", "familia": None, "sku": None, "marca_norm": marca}

    if any(p.search(nome) for p in NAO_AC_REGEX):
        out["estado"] = "NAO_AC"
        return out

    if any(p.search(nome) for p in FORA_TIPO_REGEX):
        out["estado"] = "FORA_ESCOPO"
        return out

    if marca in MARCAS_FORA_ESCOPO:
        out["estado"] = "FORA_ESCOPO"
        return out

    if marca not in MARCAS_CATALOGO:
        # marca desconhecida e nenhum padrão fora-escopo bateu → REVISAR
        return out

    btu_m = BTU_REGEX.search(nome)
    btu = BTU_MAP.get(btu_m.group(1).lower()) if btu_m else None

    nome_low = nome.lower()
    if re.search(r"quente[\s/]*(e\s*)?frio|quente\s*/\s*frio|\bq\s*/\s*f\b|\bqf\b", nome_low):
        ciclo = "QF"
    elif re.search(r"\bfrio\b", nome_low):
        ciclo = "F"
    elif re.search(r"\bquente\b", nome_low):
        ciclo = "Q"
    else:
        ciclo = None

    if btu is None or ciclo is None:
        return out  # REVISAR

    out["estado"] = "MAPEADO"
    out["familia"] = f"{marca}-{btu}-{ciclo}"
    # A resolução de família específica (linha comercial → catalog.familia)
    # acontece via SQL após inserir, para usar joins eficientes.
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-csv", default="depara_para_revisao.csv",
                    help="Saída CSV de REVISAR + MAPEADO genéricas (default: depara_para_revisao.csv)")
    ap.add_argument("--no-write", action="store_true", help="Não escreve no DB; só classifica e exporta CSV")
    args = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL/SUPABASE_KEY não configurados no .env")
        sys.exit(1)

    client: Client = create_client(url, key)

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
    classificados = [{"nome_coletado": n, **classify(n, b)} for n, b in nomes.items()]

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
