#!/usr/bin/env python3
"""
pricetrack_price_audit.py — o que a API do PriceTrack entrega, oferta a oferta.

Existe para responder, sem inferência, à pergunta "qual é o valor de coleta que
a API entrega?" — e para conferir se o que está em ``pricetrack_daily`` é fiel
a ele. A API não devolve UM preço por oferta: devolve quatro campos por coleta
(``spotPrice``, ``pixPrice``, ``forwardPrice``, ``priceFrom``), mais ``status``
e ``collectionHour``. Quem colapsa isso num número é o nosso importador — e foi
exatamente aí que a base ficou ~10% acima do painel em Set/2026 (gravava o
``spotPrice`` em vez do menor entre à vista e PIX).

Este script NÃO escreve nada. Lê o NDJSON.gz bruto que o import já baixa e
imprime:

  1. **Ofertas** — uma linha por observação: hora, status e os quatro preços,
     com o à vista efetivo (menor entre spot e PIX) destacado.
  2. **Resumo da base** — em quantas ofertas o PIX é menor que o spot e de
     quanto é o desconto; quantas só têm preço a prazo; quantas estão
     indisponíveis. É a medida direta do erro da base antiga.
  3. **Confronto com o banco** (``--comparar``, precisa de SUPABASE_URL/KEY) —
     o que ``pricetrack_daily`` guardou para as mesmas chaves, lado a lado.

Uso:
    # o dia inteiro, resumo por marketplace
    python scripts/pricetrack_price_audit.py --data 2026-09-01

    # o caso concreto que abriu a investigação
    python scripts/pricetrack_price_audit.py --data 2026-09-01 \
        --sku 42EZVCA12M5 --marketplace "MAGAZINE LUIZA" --comparar

    # sem o arquivo em disco? o import baixa (e este script diz como)
    python scripts/pricetrack_api_import.py --start 2026-09-01 --end 2026-09-01 --no-upload

Saída é tabela de texto no stdout — feita para colar num ticket.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

_RAW_DIR = _PROJECT_ROOT / "imports" / "pricetrack" / "api" / "raw"

# Grafias aceitas por campo (o NDJSON usa snake_case, o OpenAPI camelCase).
_FIELDS = {
    "spot": ("spot_price", "spotPrice"),
    "pix": ("pix_price", "pixPrice"),
    "forward": ("forward_price", "forwardPrice"),
    "rrp": ("price_from", "priceFrom"),
    "status": ("status",),
    "hour": ("collection_hour", "collectionHour"),
    "sku": ("sku", "product_sku", "productSku"),
    "brand": ("brand", "product_brand", "productBrand"),
    "marketplace": ("marketplace",),
    "seller": ("seller",),
    "category": ("category", "product_category", "productCategory"),
    "title": ("product_name", "productName", "title"),
}


def _pick(raw: Dict[str, Any], key: str) -> Any:
    for name in _FIELDS[key]:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    return None


def _price(raw: Dict[str, Any], key: str) -> Optional[float]:
    """Preço saneado: não-numérico ou ≤ 0 vira None (nunca 0.0)."""
    value = _pick(raw, key)
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _cash(spot: Optional[float], pix: Optional[float]) -> Optional[float]:
    """Menor à vista — a base `best_cash`, o que o painel exibe e o cliente paga."""
    candidates = [p for p in (spot, pix) if p is not None]
    return min(candidates) if candidates else None


def _brl(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def read_raw(path: Path) -> Iterator[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def _matches(raw: Dict[str, Any], args) -> bool:
    def _has(key: str, wanted: Optional[str]) -> bool:
        if not wanted:
            return True
        got = str(_pick(raw, key) or "").upper()
        return wanted.upper() in got

    return (
        _has("sku", args.sku)
        and _has("marketplace", args.marketplace)
        and _has("seller", args.seller)
        and _has("brand", args.brand)
        and _has("category", args.categoria)
    )


def print_offers(rows: List[Dict[str, Any]], limit: int) -> None:
    """Uma linha por observação crua — o dado como a API entrega."""
    if not rows:
        print("Nenhuma oferta casou os filtros.")
        return

    print(f"\n{'═' * 118}")
    print("OFERTAS CRUAS DA API — uma linha por coleta (não por listagem)")
    print("═" * 118)
    header = (f"{'hora':>4} {'status':<12} {'marketplace':<18} {'seller':<20} "
              f"{'spot':>11} {'pix':>11} {'à vista*':>11} {'a prazo':>11} {'de':>11}")
    print(header)
    print("-" * 118)

    ordered = sorted(
        rows,
        key=lambda r: (
            str(_pick(r, "marketplace") or ""), str(_pick(r, "seller") or ""),
            _pick(r, "hour") if _pick(r, "hour") is not None else -1,
        ),
    )
    for raw in ordered[:limit]:
        spot, pix = _price(raw, "spot"), _price(raw, "pix")
        hour = _pick(raw, "hour")
        available = str(_pick(raw, "status") or "").upper() != "UNAVAILABLE"
        # Indisponível conserva o preço no histórico mas NÃO tem à vista
        # efetivo — não compete no piso. Marcar aqui evita ler a coluna como
        # se a oferta estivesse comprável.
        cash = _brl(_cash(spot, pix)) if available else "(indisp.)"
        print(
            f"{(hour if hour is not None else '—'):>4} "
            f"{str(_pick(raw, 'status') or '—'):<12} "
            f"{str(_pick(raw, 'marketplace') or '—')[:18]:<18} "
            f"{str(_pick(raw, 'seller') or '—')[:20]:<20} "
            f"{_brl(spot):>11} {_brl(pix):>11} {cash:>11} "
            f"{_brl(_price(raw, 'forward')):>11} {_brl(_price(raw, 'rrp')):>11}"
        )
    if len(ordered) > limit:
        print(f"… e mais {len(ordered) - limit:,} (use --limite para ver mais)")
    print("\n* à vista = MENOR entre spot e PIX. É o que o painel do PriceTrack "
          "mostra com\n  À Vista + PIX + \"Menor\", e é a base que `pricetrack_daily` "
          "grava como `best_cash`.")


def print_summary(rows: List[Dict[str, Any]]) -> None:
    """Quantifica a diferença entre gravar o spot e gravar o menor à vista."""
    if not rows:
        return

    per_mkt: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "pix_wins": 0, "sem_pix": 0, "gaps": [],
                 "forward_only": 0, "unavailable": 0}
    )
    for raw in rows:
        mkt = str(_pick(raw, "marketplace") or "—")
        bucket = per_mkt[mkt]
        bucket["n"] += 1
        if str(_pick(raw, "status") or "").upper() == "UNAVAILABLE":
            bucket["unavailable"] += 1
        spot, pix = _price(raw, "spot"), _price(raw, "pix")
        if pix is None:
            bucket["sem_pix"] += 1
        elif spot is not None and pix < spot:
            bucket["pix_wins"] += 1
            bucket["gaps"].append((spot - pix) / spot * 100)
        if _cash(spot, pix) is None and _price(raw, "forward") is not None:
            bucket["forward_only"] += 1

    print(f"\n{'═' * 100}")
    print("RESUMO POR MARKETPLACE — o tamanho do erro da base antiga (`spot_legacy`)")
    print("═" * 100)
    print(f"{'marketplace':<24} {'ofertas':>8} {'PIX < spot':>14} {'desc.médio':>11} "
          f"{'sem PIX':>9} {'só a prazo':>11} {'indisp.':>9}")
    print("-" * 100)
    for mkt, b in sorted(per_mkt.items(), key=lambda kv: -kv[1]["n"]):
        gap = sum(b["gaps"]) / len(b["gaps"]) if b["gaps"] else None
        pct_pix = f"{b['pix_wins'] / b['n'] * 100:.0f}%" if b["n"] else "—"
        pix_col = f"{b['pix_wins']:,} ({pct_pix})"
        gap_col = f"{gap:.1f}%" if gap is not None else "—"
        print(
            f"{mkt[:24]:<24} {b['n']:>8,} {pix_col:>14} {gap_col:>11} "
            f"{b['sem_pix']:>9,} {b['forward_only']:>11,} {b['unavailable']:>9,}"
        )

    total = sum(b["n"] for b in per_mkt.values())
    pix_wins = sum(b["pix_wins"] for b in per_mkt.values())
    all_gaps = [g for b in per_mkt.values() for g in b["gaps"]]
    print("-" * 100)
    total_col = f"{pix_wins:,} ({pix_wins / total * 100:.0f}%)" if total else "—"
    print(f"{'TOTAL':<24} {total:>8,} {total_col:>14}")
    if all_gaps:
        print(
            f"\n→ Em {pix_wins:,} de {total:,} ofertas ({pix_wins / total * 100:.1f}%) "
            f"o PIX é menor que o spot, com desconto médio de "
            f"{sum(all_gaps) / len(all_gaps):.1f}%.\n"
            f"  Essa é exatamente a margem que a base antiga gravava a mais: "
            f"ela usava o spot,\n  o painel mostra o PIX."
        )
    else:
        print("\n→ Nenhuma oferta com PIX menor que o spot neste recorte: aqui as "
              "duas bases coincidem.")


def compare_db(rows: List[Dict[str, Any]], collection_date: str, turno: str) -> None:
    """Confronta o bruto com o que ``pricetrack_daily`` guardou."""
    try:
        from supabase import create_client
    except ImportError:
        print("\n[--comparar] supabase-py não instalado — pulei o confronto.")
        return
    import os
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        print("\n[--comparar] SUPABASE_URL/SUPABASE_KEY ausentes — pulei o confronto.")
        return

    # Chaves observadas no bruto (o que a API entregou).
    esperado: Dict[tuple, Dict[str, Any]] = {}
    for raw in rows:
        if str(_pick(raw, "status") or "").upper() == "UNAVAILABLE":
            continue
        cash = _cash(_price(raw, "spot"), _price(raw, "pix"))
        if cash is None:
            continue
        k = (str(_pick(raw, "sku") or ""), str(_pick(raw, "marketplace") or ""),
             str(_pick(raw, "seller") or ""))
        slot = esperado.setdefault(k, {"min": cash, "obs": 0})
        slot["min"] = min(slot["min"], cash)
        slot["obs"] += 1

    client = create_client(url, key)
    resp = (
        client.table("pricetrack_daily")
        .select("sku,marketplace,seller,min_price,last_price,price_basis,obs_count")
        .eq("collection_date", collection_date)
        .eq("turno", turno)
        .execute()
    )
    banco = {
        (str(r.get("sku") or ""), str(r.get("marketplace") or ""),
         str(r.get("seller") or "")): r
        for r in (getattr(resp, "data", None) or [])
    }

    print(f"\n{'═' * 104}")
    print(f"BRUTO DA API × pricetrack_daily — {collection_date} · turno {turno}")
    print("═" * 104)
    print(f"{'seller':<22} {'marketplace':<18} {'piso bruto':>12} {'min_price':>12} "
          f"{'last_price':>12} {'base':>13} {'Δ':>8}")
    print("-" * 104)
    divergentes = 0
    for k, exp in sorted(esperado.items(), key=lambda kv: kv[0][2]):
        row = banco.get(k)
        if row is None:
            print(f"{k[2][:22]:<22} {k[1][:18]:<18} {_brl(exp['min']):>12} "
                  f"{'AUSENTE':>12} {'—':>12} {'—':>13} {'—':>8}")
            divergentes += 1
            continue
        db_min = float(row["min_price"]) if row.get("min_price") is not None else None
        delta = (db_min - exp["min"]) / exp["min"] * 100 if db_min else None
        if delta is not None and abs(delta) >= 0.01:
            divergentes += 1
        print(
            f"{k[2][:22]:<22} {k[1][:18]:<18} {_brl(exp['min']):>12} "
            f"{_brl(db_min):>12} "
            f"{_brl(float(row['last_price']) if row.get('last_price') is not None else None):>12} "
            f"{str(row.get('price_basis') or 'sem carimbo'):>13} "
            f"{(f'{delta:+.1f}%' if delta is not None else '—'):>8}"
        )
    print("-" * 104)
    if divergentes:
        print(f"\n⛔ {divergentes} de {len(esperado)} chaves divergem do bruto. "
              f"Δ positivo = o banco está ACIMA\n   do preço real. Reimporte o dia:\n"
              f"   python scripts/pricetrack_api_import.py --force "
              f"--start {collection_date} --end {collection_date}")
    else:
        print(f"\n✅ As {len(esperado)} chaves batem com o bruto da API.")


def backfill_status() -> int:
    """Dia a dia: qual base de preço está no banco e se o bruto está em disco.

    É o painel do backfill — responde "quanto ainda está errado?" e "o reimport
    vai reaproveitar o cache ou re-baixar da API?" numa tela só.
    """
    try:
        from supabase import create_client
    except ImportError:
        print("supabase-py não instalado — `pip install supabase`.")
        return 1
    import os
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        print("SUPABASE_URL/SUPABASE_KEY ausentes no ambiente/.env.")
        return 1

    client = create_client(url, key)
    # Uma linha por (data, base): o agregado é pequeno, mas o PostgREST não faz
    # GROUP BY — então contamos por data com `count='exact'` e head (sem corpo).
    resp = (
        client.table("pricetrack_daily")
        .select("collection_date")
        .order("collection_date", desc=False)
        .limit(1)
        .execute()
    )
    data = getattr(resp, "data", None) or []
    if not data:
        print("pricetrack_daily está vazia.")
        return 0
    first = str(data[0]["collection_date"])[:10]
    resp = (
        client.table("pricetrack_daily")
        .select("collection_date")
        .order("collection_date", desc=True)
        .limit(1)
        .execute()
    )
    last = str((getattr(resp, "data", None) or [{}])[0].get("collection_date"))[:10]

    print(f"\n{'═' * 74}")
    print("STATUS DO BACKFILL — base de preço por dia")
    print("═" * 74)
    print(f"{'data':<12} {'best_cash':>12} {'spot_legacy':>13} {'bruto em disco':>16}")
    print("-" * 74)

    from datetime import date as _date, timedelta as _td
    cur, end = _date.fromisoformat(first), _date.fromisoformat(last)
    pend_dias = pend_linhas = ok_linhas = 0
    sem_bruto: List[str] = []
    while cur <= end:
        ds = cur.isoformat()
        counts = {}
        for basis in ("best_cash", "spot_legacy"):
            r = (
                client.table("pricetrack_daily")
                .select("id", count="exact", head=True)
                .eq("collection_date", ds)
                .eq("price_basis", basis)
                .execute()
            )
            counts[basis] = getattr(r, "count", 0) or 0
        if counts["best_cash"] or counts["spot_legacy"]:
            cached = (_RAW_DIR / f"offers-{ds}.ndjson.gz").exists()
            if counts["spot_legacy"]:
                pend_dias += 1
                pend_linhas += counts["spot_legacy"]
                if not cached:
                    sem_bruto.append(ds)
            ok_linhas += counts["best_cash"]
            print(f"{ds:<12} {counts['best_cash']:>12,} {counts['spot_legacy']:>13,} "
                  f"{('cache ✓' if cached else 'baixar da API'):>16}")
        cur += _td(days=1)

    print("-" * 74)
    print(f"{'TOTAL':<12} {ok_linhas:>12,} {pend_linhas:>13,}")
    if pend_dias:
        print(f"\n⛔ {pend_dias} dia(s) / {pend_linhas:,} linha(s) ainda na base "
              f"antiga (preço ~10% alto onde há PIX).")
        if sem_bruto:
            print(f"   {len(sem_bruto)} desses dias NÃO têm o NDJSON em disco e "
                  f"serão re-baixados da API (lento, 3 exports em voo).")
        print(f"\n   Reimporte tudo de uma vez:\n"
              f"   python scripts/pricetrack_api_import.py --force "
              f"--start {first} --end {last}")
    else:
        print("\n✅ Nenhum dia na base antiga — o histórico inteiro está em "
              "`best_cash`.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audita o preço bruto da API do PriceTrack contra a base.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--status-backfill", action="store_true",
                        help="Painel do backfill: base de preço por dia no "
                             "banco + se o NDJSON bruto está em cache. "
                             "Dispensa --data.")
    parser.add_argument("--data", help="Data ISO (YYYY-MM-DD)")
    parser.add_argument("--arquivo", help="NDJSON.gz alternativo (default: imports/…)")
    parser.add_argument("--sku", help="Filtro por SKU (substring, case-insensitive)")
    parser.add_argument("--marketplace", help="Filtro por marketplace")
    parser.add_argument("--seller", help="Filtro por seller")
    parser.add_argument("--brand", help="Filtro por marca")
    parser.add_argument("--categoria", default="AR CONDICIONADO",
                        help="Filtro de categoria (default: AR CONDICIONADO; "
                             "passe '' para não filtrar)")
    parser.add_argument("--turno", default="Diário", help="Turno no confronto com o banco")
    parser.add_argument("--limite", type=int, default=60,
                        help="Máx. de ofertas listadas (default: 60)")
    parser.add_argument("--comparar", action="store_true",
                        help="Confronta com pricetrack_daily (precisa de Supabase)")
    args = parser.parse_args()

    if args.status_backfill:
        return backfill_status()
    if not args.data:
        parser.error("--data é obrigatório (ou use --status-backfill)")

    path = Path(args.arquivo) if args.arquivo else _RAW_DIR / f"offers-{args.data}.ndjson.gz"
    if not path.exists():
        print(f"❌ Export bruto não encontrado: {path}\n\n"
              f"Baixe primeiro (não sobe nada para o banco):\n"
              f"   python scripts/pricetrack_api_import.py "
              f"--start {args.data} --end {args.data} --no-upload")
        return 1

    rows = [raw for raw in read_raw(path) if _matches(raw, args)]
    print(f"Arquivo: {path}")
    print(f"Ofertas após filtros: {len(rows):,}")

    if rows:
        campos = sorted(rows[0].keys())
        print(f"Campos entregues pela API ({len(campos)}): {', '.join(campos)}")

    print_offers(rows, args.limite)
    print_summary(rows)
    if args.comparar:
        compare_db(rows, args.data, args.turno)
    return 0


if __name__ == "__main__":
    sys.exit(main())
