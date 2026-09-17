#!/usr/bin/env python3
"""
scripts/pricetrack_capacity_audit.py — confere presença de 9K/12K em
`pricetrack_daily` pelo método CORRETO: casamento por código de modelo do
peer (`pricetrack_dashboard.peer.match_haystack`), o mesmo mecanismo testado
que o dashboard Streamlit já usa.

Por quê este script existe: um achado de briefing apontou "0 linhas de 9.000
BTU em pricetrack_daily entre 11 e 16/09" e cogitou que o rastreamento de 9K
nunca tivesse existido na tabela. Investigando, a checagem original partia de
um schema que não é o desta tabela — `capacidade_btu`/`preco`/`marca` são
colunas de `coletas`/`produtos_catalogo`, não de `pricetrack_daily`, que
nunca teve coluna de capacidade nenhuma (ver docs/PRICETRACK_FIDELIDADE.md e
migrations/001..006_pricetrack_price_basis.sql). A capacidade só existe
DERIVADA, casando `sku`+`title` contra os códigos de modelo do fabricante em
`pricetrack_dashboard/peer.py` — texto livre tipo "9000 BTU" no `title` não é
confiável aqui (o `title` é o nome bruto do anúncio no marketplace).

Este script refaz a contagem com esse método, **sem filtro de marca** (o
filtro por marcas do peer que os outros leitores usam esconderia SKU 9K de
marca fora do peer, o que pareceria "não existe" sem ser o caso), para
separar dois cenários bem diferentes:

    a) linhas 9K existem e o achado original era um falso-negativo de método;
    b) mesmo com o método certo, zero linha 9K no período — aí sim é lacuna
       real de coleta, e o caminho para corrigir é o de sempre:
       `scripts/pricetrack_csv_import.py` com o export manual do painel.

Uso:
    python scripts/pricetrack_capacity_audit.py --desde 2026-09-11 --ate 2026-09-16
    python scripts/pricetrack_capacity_audit.py --desde 2026-09-11 --ate 2026-09-16 --listar-nao-classificados
    python scripts/pricetrack_capacity_audit.py --desde 2026-09-11 --ate 2026-09-16 --json logs/pricetrack_capacity_audit.json

Requer o mesmo backend do dashboard: `RAC_DB_DSN` (Postgres/Aiven) ou
`SUPABASE_URL`+`SUPABASE_KEY`. Sem nenhum dos dois, aborta — nunca imprime
"0 linhas" de um banco que não conseguiu nem consultar.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from loguru import logger

from pricetrack_dashboard.data_source import (
    _paged_select,
    _supabase_client,
    supabase_configured,
)
from pricetrack_dashboard.peer import CAP_ORDER, match_haystack

UNCLASSIFIED = "não classificado"


@dataclass
class DayCounts:
    """Contagem de linhas por capacidade num único `collection_date`."""

    total: int = 0
    by_capacity: Counter = field(default_factory=Counter)  # "9K" | "12K" | UNCLASSIFIED


@dataclass
class CapacityReport:
    start: str
    end: str
    turno: str
    by_date: Dict[str, DayCounts] = field(default_factory=dict)
    #: amostra de linhas não classificadas, para decidir se é lacuna do peer
    #: (código novo não catalogado) ou lacuna real de coleta.
    unclassified_samples: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def totals(self) -> Counter:
        out: Counter = Counter()
        for day in self.by_date.values():
            out.update(day.by_capacity)
        return out

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "turno": self.turno,
            "by_date": {
                d: {"total": c.total, **c.by_capacity} for d, c in sorted(self.by_date.items())
            },
            "totals": dict(self.totals),
            "unclassified_samples": self.unclassified_samples,
        }


def fetch_rows(
    start_date: str,
    end_date: str,
    turno: str = "Diário",
    client=None,
) -> List[Dict[str, Any]]:
    """Lê `pricetrack_daily` cru no intervalo, sem filtro de marca.

    Sem filtro de marca de propósito: o objetivo é responder "existe QUALQUER
    linha 9K no período", e um filtro pelas marcas do peer esconderia um SKU
    9K de marca fora do peer atrás de "zero resultado" — o mesmo tipo de
    falso-negativo que motivou este script.
    """
    client = client or _supabase_client()
    if client is None:
        raise RuntimeError(
            "Nenhum backend configurado — defina RAC_DB_DSN (Aiven/Postgres) "
            "ou SUPABASE_URL+SUPABASE_KEY."
        )

    def _build(select_cols: str):
        return (
            client.table("pricetrack_daily")
            .select(select_cols)
            .gte("collection_date", start_date)
            .lte("collection_date", end_date)
            .eq("turno", turno)
        )

    rows, truncated = _paged_select(_build, max_rows=300_000)
    if truncated:
        logger.warning(
            f"pricetrack_capacity_audit: teto de linhas atingido para "
            f"{start_date}..{end_date} — a contagem pode estar incompleta."
        )
    return rows


def classify_rows(
    rows: Sequence[Dict[str, Any]],
    max_unclassified_samples: int = 20,
) -> CapacityReport:
    """Classifica cada linha em (data, capacidade) via `match_haystack`.

    Não usa nenhuma coluna de capacidade — não existe. Casa `sku`+`title`
    contra os códigos de modelo do peer (`pricetrack_dashboard/peer.py`).
    """
    by_date: Dict[str, DayCounts] = {}
    seen_unclassified = set()
    samples: List[Dict[str, Any]] = []

    for row in rows:
        d = str(row.get("collection_date") or "")[:10]
        if not d:
            continue
        day = by_date.setdefault(d, DayCounts())
        day.total += 1

        hit = match_haystack(row.get("sku"), row.get("title"))
        if hit is not None and hit.capacity in CAP_ORDER:
            day.by_capacity[hit.capacity] += 1
        else:
            day.by_capacity[UNCLASSIFIED] += 1
            key = (row.get("brand"), row.get("sku"), row.get("title"))
            if key not in seen_unclassified and len(samples) < max_unclassified_samples:
                seen_unclassified.add(key)
                samples.append({
                    "collection_date": d,
                    "brand": row.get("brand"),
                    "sku": row.get("sku"),
                    "title": row.get("title"),
                    "marketplace": row.get("marketplace"),
                })

    return CapacityReport(
        start=min(by_date) if by_date else "", end=max(by_date) if by_date else "",
        turno="", by_date=by_date, unclassified_samples=samples,
    )


def render_report(report: CapacityReport, list_unclassified: bool = False) -> str:
    lines = []
    header = f"{'Data':<12}" + "".join(f"{cap:>8}" for cap in CAP_ORDER) + f"{'Não class.':>12}{'Total':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for d in sorted(report.by_date):
        day = report.by_date[d]
        row = f"{d:<12}"
        for cap in CAP_ORDER:
            row += f"{day.by_capacity.get(cap, 0):>8}"
        row += f"{day.by_capacity.get(UNCLASSIFIED, 0):>12}{day.total:>8}"
        lines.append(row)

    totals = report.totals
    total_all = sum(totals.values())
    lines.append("-" * len(header))
    row = f"{'TOTAL':<12}"
    for cap in CAP_ORDER:
        row += f"{totals.get(cap, 0):>8}"
    row += f"{totals.get(UNCLASSIFIED, 0):>12}{total_all:>8}"
    lines.append(row)
    lines.append("")

    for cap in CAP_ORDER:
        n = totals.get(cap, 0)
        if n == 0:
            lines.append(
                f"⚠️  ZERO linhas casadas em {cap} no período — mesmo com o método "
                f"correto (match_haystack). Se isso persistir, é lacuna real de "
                f"coleta: backfill via `scripts/pricetrack_csv_import.py` com o "
                f"export manual do painel."
            )
        else:
            lines.append(f"✅ {cap}: {n} linha(s) casada(s) no período — rastreamento existe.")

    if report.unclassified_samples:
        lines.append("")
        lines.append(
            f"{len(report.unclassified_samples)} amostra(s) não classificada(s) "
            f"(código de modelo fora do peer, ou peer desatualizado):"
        )
        if list_unclassified:
            for s in report.unclassified_samples:
                lines.append(
                    f"  - {s['collection_date']} | {s['brand']} | {s['sku']} | "
                    f"{s['marketplace']} | {s['title']}"
                )
        else:
            lines.append("  (rode com --listar-nao-classificados para ver as linhas)")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Confere presença de 9K/12K em pricetrack_daily por código de "
            "modelo do peer, não por coluna de capacidade (que não existe)."
        )
    )
    parser.add_argument("--desde", required=True, help="Data inicial ISO (YYYY-MM-DD)")
    parser.add_argument("--ate", required=True, help="Data final ISO (YYYY-MM-DD), inclusive")
    parser.add_argument(
        "--turno", default="Diário",
        help="Turno de pricetrack_daily (Diário/Manhã/Tarde). Padrão: Diário (dia inteiro).",
    )
    parser.add_argument(
        "--listar-nao-classificados", action="store_true",
        help="Imprime as linhas cujo sku/title não casou com nenhum código do peer.",
    )
    parser.add_argument("--json", type=Path, help="Grava o relatório também em JSON neste caminho")
    args = parser.parse_args()

    if not supabase_configured():
        raise SystemExit(
            "❌ nenhum backend configurado: defina RAC_DB_DSN (Aiven/Postgres) "
            "ou SUPABASE_URL+SUPABASE_KEY antes de rodar este script."
        )

    logger.remove()
    logger.add(
        sys.stderr, level="INFO", colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )

    logger.info(f"Lendo pricetrack_daily de {args.desde} a {args.ate} (turno={args.turno})...")
    rows = fetch_rows(args.desde, args.ate, turno=args.turno)
    logger.info(f"{len(rows):,} linha(s) lida(s). Classificando por código de modelo...")

    report = classify_rows(rows)
    report.start, report.end, report.turno = args.desde, args.ate, args.turno

    print()
    print(render_report(report, list_unclassified=args.listar_nao_classificados))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        logger.success(f"Relatório JSON gravado em {args.json}")


if __name__ == "__main__":
    main()
