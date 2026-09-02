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
# `generic` espelha `_GENERIC_PRICE_FIELDS` do importador: sem ele a auditoria
# ignoraria um preço que o import de fato grava, e o confronto acusaria a chave
# como AUSENTE. Auditor que não replica a regra do auditado audita outra coisa.
_FIELDS = {
    "spot": ("spot_price", "spotPrice"),
    "pix": ("pix_price", "pixPrice"),
    "forward": ("forward_price", "forwardPrice"),
    "generic": ("price", "sale_price", "salePrice", "preco", "valor"),
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


def _cash(spot: Optional[float], pix: Optional[float],
          generic: Optional[float] = None) -> Optional[float]:
    """Menor à vista — a base `best_cash`, o que o painel exibe e o cliente paga.

    ``generic`` só entra onde spot E pix faltam, exatamente como no importador.
    """
    candidates = [p for p in (spot, pix) if p is not None]
    if candidates:
        return min(candidates)
    return generic


def _is_available(raw: Dict[str, Any]) -> bool:
    """True só para `status` EXATAMENTE AVAILABLE (mesma régua do importador).

    Um status novo ou inesperado não é "disponível": tratar desconhecido como
    comprável é o mesmo erro de base que esta auditoria existe para achar.
    """
    return str(_pick(raw, "status") or "").strip().upper() == "AVAILABLE"


def _hour(raw: Dict[str, Any]) -> Optional[int]:
    """`collection_hour` normalizada para int (o NDJSON já trouxe string)."""
    value = _pick(raw, "hour")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _in_turno(raw: Dict[str, Any], turno: str) -> bool:
    """A observação cai na janela do turno? (mesmas horas do importador.)"""
    if turno == "Diário":
        return True
    hour = _hour(raw)
    if hour is None:
        return False           # sem hora, a oferta só entra no Diário
    if turno == "Manhã":
        return 8 <= hour <= 12
    if turno == "Tarde":
        return 18 <= hour <= 22
    return True


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

    # A hora vai normalizada para int na chave: o NDJSON pode trazê-la como
    # string, e ordenar tipos misturados estoura TypeError (ou, se todas forem
    # string, ordena "10" antes de "9").
    ordered = sorted(
        rows,
        key=lambda r: (
            str(_pick(r, "marketplace") or ""), str(_pick(r, "seller") or ""),
            _hour(r) if _hour(r) is not None else -1,
        ),
    )
    for raw in ordered[:limit]:
        spot, pix = _price(raw, "spot"), _price(raw, "pix")
        hour = _hour(raw)
        # Só AVAILABLE tem à vista efetivo — status desconhecido não é
        # "comprável". Mesma régua do importador.
        cash = (_brl(_cash(spot, pix, _price(raw, "generic")))
                if _is_available(raw) else "(indisp.)")
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
        lambda: {"n": 0, "avail": 0, "pix_wins": 0, "sem_pix": 0, "gaps": [],
                 "forward_only": 0, "unavailable": 0}
    )
    for raw in rows:
        mkt = str(_pick(raw, "marketplace") or "—")
        bucket = per_mkt[mkt]
        bucket["n"] += 1
        if not _is_available(raw):
            # Indisponível não entra no preço importado, então também não entra
            # na conta do erro: somá-la superestimaria a margem atribuída à
            # base antiga, que é justamente o número que este resumo defende.
            bucket["unavailable"] += 1
            continue
        bucket["avail"] += 1
        spot, pix = _price(raw, "spot"), _price(raw, "pix")
        if pix is None:
            bucket["sem_pix"] += 1
        elif spot is not None and pix < spot:
            bucket["pix_wins"] += 1
            bucket["gaps"].append((spot - pix) / spot * 100)
        if _cash(spot, pix, _price(raw, "generic")) is None and _price(raw, "forward") is not None:
            bucket["forward_only"] += 1

    print(f"\n{'═' * 100}")
    print("RESUMO POR MARKETPLACE — o tamanho do erro da base antiga (`spot_legacy`)")
    print("═" * 100)
    # Todo percentual é sobre `avail`, não sobre `n`: o preço importado só olha
    # AVAILABLE, então diluir a conta com indisponível descreveria um erro que
    # a base não comete.
    print(f"{'marketplace':<24} {'dispon.':>8} {'PIX < spot':>14} {'desc.médio':>11} "
          f"{'sem PIX':>9} {'só a prazo':>11} {'indisp.':>9}")
    print("-" * 100)
    for mkt, b in sorted(per_mkt.items(), key=lambda kv: -kv[1]["n"]):
        gap = sum(b["gaps"]) / len(b["gaps"]) if b["gaps"] else None
        pct_pix = f"{b['pix_wins'] / b['avail'] * 100:.0f}%" if b["avail"] else "—"
        pix_col = f"{b['pix_wins']:,} ({pct_pix})"
        gap_col = f"{gap:.1f}%" if gap is not None else "—"
        print(
            f"{mkt[:24]:<24} {b['avail']:>8,} {pix_col:>14} {gap_col:>11} "
            f"{b['sem_pix']:>9,} {b['forward_only']:>11,} {b['unavailable']:>9,}"
        )

    avail = sum(b["avail"] for b in per_mkt.values())
    pix_wins = sum(b["pix_wins"] for b in per_mkt.values())
    all_gaps = [g for b in per_mkt.values() for g in b["gaps"]]
    print("-" * 100)
    total_col = f"{pix_wins:,} ({pix_wins / avail * 100:.0f}%)" if avail else "—"
    print(f"{'TOTAL':<24} {avail:>8,} {total_col:>14}")
    if all_gaps and avail:
        print(
            f"\n→ Em {pix_wins:,} de {avail:,} ofertas disponíveis "
            f"({pix_wins / avail * 100:.1f}%) o PIX é menor que o spot, com "
            f"desconto médio de {sum(all_gaps) / len(all_gaps):.1f}%.\n"
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

    # ── O que a API entregou, recortado pela MESMA janela do banco ──────────
    # Sem `_in_turno` o bruto varria o dia inteiro enquanto o banco vinha
    # filtrado por turno: o menor de outro turno gerava divergência falsa.
    esperado: Dict[tuple, Dict[str, Any]] = {}
    for raw in rows:
        if not _is_available(raw) or not _in_turno(raw, turno):
            continue
        cash = _cash(_price(raw, "spot"), _price(raw, "pix"), _price(raw, "generic"))
        if cash is None:
            continue
        # A chave é a UNIQUE da tabela — inclui `brand`. Sem ela, dois grupos
        # do banco (marcas distintas no mesmo sku/marketplace/seller) colapsam
        # num só e o piso esperado mistura linhas que nunca foram agregadas
        # juntas.
        k = (str(_pick(raw, "brand") or "").upper(), str(_pick(raw, "sku") or ""),
             str(_pick(raw, "marketplace") or ""), str(_pick(raw, "seller") or ""))
        hour = _hour(raw)
        slot = esperado.setdefault(
            k, {"min": cash, "obs": 0, "last": cash, "last_hour": -1}
        )
        slot["min"] = min(slot["min"], cash)
        slot["obs"] += 1
        # `last` = observação da hora mais alta (o que o painel exibe); empate
        # de hora fica com a última do arquivo, como no importador.
        if hour is None or hour >= slot["last_hour"]:
            slot["last"] = cash
            slot["last_hour"] = hour if hour is not None else slot["last_hour"]

    # ── O que o banco guardou (paginado: um dia passa de 1.000 linhas) ──────
    client = create_client(url, key)
    banco: Dict[tuple, Dict[str, Any]] = {}
    offset, page_size = 0, 1000
    while True:
        resp = (
            client.table("pricetrack_daily")
            .select("brand,sku,marketplace,seller,min_price,last_price,"
                    "price_basis,obs_count")
            .eq("collection_date", collection_date)
            .eq("turno", turno)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = getattr(resp, "data", None) or []
        for r in page:
            banco[(str(r.get("brand") or "").upper(), str(r.get("sku") or ""),
                   str(r.get("marketplace") or ""), str(r.get("seller") or ""))] = r
        if len(page) < page_size:
            break
        offset += page_size

    print(f"\n{'═' * 116}")
    print(f"BRUTO DA API × pricetrack_daily — {collection_date} · turno {turno}")
    print("═" * 116)
    print(f"{'seller':<20} {'marketplace':<16} {'piso bruto':>11} {'min_price':>11} "
          f"{'últ. bruto':>11} {'last_price':>11} {'base':>13} {'Δ piso':>8} {'Δ últ.':>8}")
    print("-" * 116)

    def _delta(esperado_v: Optional[float], banco_v: Optional[float]):
        if not esperado_v or banco_v is None:
            return None
        return (banco_v - esperado_v) / esperado_v * 100

    divergentes = 0
    for k, exp in sorted(esperado.items(), key=lambda kv: kv[0][3]):
        seller, mkt = k[3], k[2]
        row = banco.get(k)
        if row is None:
            print(f"{seller[:20]:<20} {mkt[:16]:<16} {_brl(exp['min']):>11} "
                  f"{'AUSENTE':>11} {_brl(exp['last']):>11} {'—':>11} "
                  f"{'—':>13} {'—':>8} {'—':>8}")
            divergentes += 1
            continue
        db_min = float(row["min_price"]) if row.get("min_price") is not None else None
        db_last = float(row["last_price"]) if row.get("last_price") is not None else None
        d_min = _delta(exp["min"], db_min)
        # `last_price` só existe depois da migração 006 — em linha legada não há
        # o que comparar, e cobrar isso marcaria o histórico inteiro como
        # divergente por um motivo que não é o erro que procuramos.
        d_last = _delta(exp["last"], db_last) if db_last is not None else None
        if ((d_min is not None and abs(d_min) >= 0.01)
                or (d_last is not None and abs(d_last) >= 0.01)):
            divergentes += 1
        print(
            f"{seller[:20]:<20} {mkt[:16]:<16} {_brl(exp['min']):>11} "
            f"{_brl(db_min):>11} {_brl(exp['last']):>11} {_brl(db_last):>11} "
            f"{str(row.get('price_basis') or 'sem carimbo'):>13} "
            f"{(f'{d_min:+.1f}%' if d_min is not None else '—'):>8} "
            f"{(f'{d_last:+.1f}%' if d_last is not None else '—'):>8}"
        )
    print("-" * 116)
    if divergentes:
        print(f"\n⛔ {divergentes} de {len(esperado)} chaves divergem do bruto. "
              f"Δ positivo = o banco está ACIMA\n   do preço real. Reimporte o dia:\n"
              f"   python scripts/pricetrack_api_import.py --force "
              f"--start {collection_date} --end {collection_date}")
    else:
        print(f"\n✅ As {len(esperado)} chaves batem com o bruto da API "
              f"(piso e última coleta).")


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
