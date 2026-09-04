#!/usr/bin/env python3
"""
scripts/build_seller_offer_daily.py — Materializa o fato com sujeito seller.

Fase 1 do Track Position Seller (`docs/TRACK_POSITION_SELLER.md`). Chama a
função `refresh_seller_offer_daily(data)` no servidor: nenhuma linha de
`coletas` sai do banco, e a execução é idempotente por data.

Antes do fato, sincroniza as duas tabelas de referência que a SQL precisa e
cuja fonte de verdade é Python:

  * `plataforma_superficie` ← `utils.seller_surface.mapa_superficies()`
  * `seller_depara`         ← `utils.seller_names.SELLER_GROUPS`

Sem a segunda, Web Continental vira cinco sellers e o share mente sobre quem
lidera. Sem a primeira, loja própria entra no denominador de buy box e o
dealer aparece ganhando 100% de um campeonato que joga sozinho.

Uso:
    python scripts/build_seller_offer_daily.py                  # ontem e hoje
    python scripts/build_seller_offer_daily.py --data 2026-09-03
    python scripts/build_seller_offer_daily.py --desde 2026-08-28
    python scripts/build_seller_offer_daily.py --so-sync        # só referências

Requer `SUPABASE_URL` e uma chave `service_role` em `SUPABASE_KEY`: a escrita
nestas tabelas é negada por ausência de policy (migração 016), então a chave
`anon` grava nada em silêncio — o mesmo modo de falha da migração 012.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger  # noqa: E402

from utils.seller_names import SELLER_GROUPS, seller_key  # noqa: E402
from utils.seller_surface import mapa_superficies, validar_registro  # noqa: E402


def _cliente():
    """Client do Supabase, exigindo chave de escrita."""
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL/SUPABASE_KEY ausentes no ambiente.")
    return create_client(url, key)


def sincronizar_referencias(client) -> None:
    """Reescreve `plataforma_superficie` e `seller_depara` a partir do Python."""
    validar_registro()

    superficies = [
        {"plataforma": p, "superficie": s} for p, s in sorted(mapa_superficies().items())
    ]
    client.table("plataforma_superficie").upsert(
        superficies, on_conflict="plataforma"
    ).execute()
    logger.success(f"[superfície] {len(superficies)} plataformas sincronizadas")

    # Uma linha por grafia observada, mais o próprio canônico — a chave é
    # normalizada, então grafias que só diferem em caixa/acento colapsam aqui
    # mesmo e não geram linha a mais.
    pares = {}
    for canonico, variantes in SELLER_GROUPS.items():
        for grafia in list(variantes) + [canonico]:
            chave = seller_key(grafia)
            if chave:
                pares[chave] = canonico
    depara = [{"variante_key": k, "canonical": v} for k, v in sorted(pares.items())]
    client.table("seller_depara").upsert(depara, on_conflict="variante_key").execute()
    logger.success(f"[de-para] {len(depara)} variantes de seller sincronizadas")


def materializar(client, dia: date) -> None:
    """Roda a transformação para um dia e loga o que ela devolveu."""
    resp = client.rpc("refresh_seller_offer_daily", {"p_data": dia.isoformat()}).execute()
    linha = (resp.data or [{}])[0] if isinstance(resp.data, list) else (resp.data or {})
    ofertas = linha.get("ofertas", 0)
    suspeitas = linha.get("suspeitas", 0)
    coberturas = linha.get("coberturas", 0)

    logger.success(
        f"[{dia}] {ofertas} ofertas · {coberturas} coberturas "
        f"· {suspeitas} com identidade suspeita"
    )
    if suspeitas:
        # WARNING, não INFO: chave colapsada é dado que NÃO entra em nenhum KPI,
        # e um volume grande aqui significa que o produto está enxergando menos
        # mercado do que parece. Silenciar isso é o modo de falha do §2.9.
        pct = 100.0 * suspeitas / ofertas if ofertas else 0.0
        logger.warning(
            f"[{dia}] {suspeitas} ofertas ({pct:.1f}%) com offer_key colapsada — "
            "fora de win rate e de share. Causa conhecida: canonical_url de "
            "rodapé no Google Shopping e em parte do Mercado Livre."
        )


def _dias(args) -> List[date]:
    if args.data:
        return [date.fromisoformat(args.data)]
    if args.desde:
        inicio = date.fromisoformat(args.desde)
        return [inicio + timedelta(days=i) for i in range((date.today() - inicio).days + 1)]
    return [date.today() - timedelta(days=1), date.today()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", help="Um dia (YYYY-MM-DD)")
    parser.add_argument("--desde", help="De um dia até hoje (YYYY-MM-DD)")
    parser.add_argument("--so-sync", action="store_true",
                        help="Só sincroniza as tabelas de referência")
    args = parser.parse_args()

    client = _cliente()
    sincronizar_referencias(client)
    if args.so_sync:
        return
    for dia in _dias(args):
        materializar(client, dia)


if __name__ == "__main__":
    main()
