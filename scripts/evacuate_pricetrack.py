#!/usr/bin/env python3
"""
scripts/evacuate_pricetrack.py — Tira `pricetrack_daily` do Supabase para
destravar a cota, guardando tudo em Parquet antes.

O PROBLEMA
----------
Em 14/09/2026 o banco estava com 1052 MB contra os 500 MB do free tier. A API
REST (PostgREST) responde 402 em TODAS as operações — leitura e escrita — com
`exceed_db_size_quota`. Não é uma janela de tempo que expira: a restrição sai
quando o banco encolhe abaixo da cota.

Duas tabelas são 90% do disco: `coletas` (482 MB) e `pricetrack_daily`
(451 MB). `pricetrack_daily` é a escolha óbvia para sair primeiro porque
(a) não é o fato da coleta, (b) tem destino natural no histórico frio e
(c) o CLAUDE.md já a marca como PENDENTE DE REIMPORT — os 36 dias foram
gravados com a base de preço errada (colapso spot/PIX, ~10% alto onde há PIX).
O dado que sai daqui já ia ser reescrito de qualquer jeito.

A ORDEM IMPORTA
---------------
Exporta → CONFERE → só então apaga. A conferência relê o Parquet gravado e
compara a contagem por dia com a do banco. Se um único dia não bater, nada é
apagado. `--confirmar-delete` é obrigatório para a etapa destrutiva: sem ele o
script só exporta.

Por que TRUNCATE e não DELETE: `DELETE` marca as linhas como mortas mas NÃO
devolve o disco ao sistema — o banco continuaria com 1052 MB e a restrição
continuaria de pé. `VACUUM FULL` devolveria, mas reescreve a tabela inteira e
precisa de espaço livre para isso, que é justamente o que não há. `TRUNCATE`
libera na hora.

USO::

    # A API REST está 402; a conexão Postgres direta continua funcionando.
    # Supabase → Project Settings → Database → Connection string → URI
    export SUPABASE_DSN="postgresql://postgres:SENHA@db.ailbsczkrympslpjwwko.supabase.co:5432/postgres"

    python scripts/evacuate_pricetrack.py --dry-run       # o que sairia
    python scripts/evacuate_pricetrack.py                 # exporta e confere
    python scripts/evacuate_pricetrack.py --confirmar-delete   # exporta, confere e apaga
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger  # noqa: E402

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None

TABELA = "pricetrack_daily"
COLUNA_DATA = "collection_date"


def _conectar(dsn: str):
    if psycopg2 is None:
        raise SystemExit("psycopg2 não instalado. Rode: pip install psycopg2-binary")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def contagem_por_dia(conn) -> Dict[date, int]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {COLUNA_DATA}, count(*) FROM {TABELA} "
            f"GROUP BY 1 ORDER BY 1"
        )
        return {linha[0]: linha[1] for linha in cur.fetchall()}


def tamanho(conn) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_size_pretty(pg_total_relation_size(%s::regclass)), "
            "pg_size_pretty(pg_database_size(current_database()))",
            (TABELA,),
        )
        tab, banco = cur.fetchone()
    return f"{TABELA}={tab} · banco={banco}"


def exportar(conn, dias: List[date]) -> Dict[date, int]:
    """Grava um Parquet por dia no histórico frio. Devolve o que gravou."""
    from utils.history import DATASET_PRICETRACK, write_records

    gravados: Dict[date, int] = {}
    for dia in dias:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {TABELA} WHERE {COLUNA_DATA} = %s", (dia,))
            linhas = [dict(r) for r in cur.fetchall()]

        if not linhas:
            continue

        chaves = write_records(
            linhas,
            dataset=DATASET_PRICETRACK,
            date_column=COLUNA_DATA,
            already_mapped=True,
        )
        if not chaves:
            # write_records absorve falha de backend e devolve [] — tratar
            # isso como sucesso apagaria dado que nunca chegou ao Drive.
            raise SystemExit(
                f"[evacuar] ❌ {dia}: o histórico não confirmou a gravação. "
                "NADA foi apagado. Confira GDRIVE_* no .env."
            )
        gravados[dia] = len(linhas)
        logger.success(f"[evacuar] {dia}: {len(linhas)} linha(s) → Parquet")

    return gravados


def conferir(no_banco: Dict[date, int]) -> bool:
    """Relê o Parquet e compara com o banco, dia a dia.

    Esta é a trava de segurança do script: sem ela, um backend do Drive que
    falha em silêncio viraria perda de dado definitiva no TRUNCATE.
    """
    from utils.history import DATASET_PRICETRACK, get_store

    store = get_store()
    ok = True
    for dia, esperado in sorted(no_banco.items()):
        try:
            df = store.read(DATASET_PRICETRACK, start=dia, end=dia)
        except Exception as exc:
            logger.error(f"[conferir] {dia}: releitura do Parquet falhou — {exc}")
            ok = False
            continue

        if len(df) != esperado:
            logger.error(
                f"[conferir] {dia}: banco tem {esperado}, Parquet tem {len(df)}"
            )
            ok = False
        else:
            logger.info(f"[conferir] ✓ {dia}: {esperado} linha(s) conferem")
    return ok


def apagar(conn) -> None:
    """TRUNCATE — libera o disco na hora (DELETE não liberaria)."""
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {TABELA}")
    logger.success(f"[evacuar] {TABELA} truncada — disco liberado")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evacua pricetrack_daily do Supabase para o Parquet."
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("SUPABASE_DSN", "").strip(),
        help="DSN do Postgres do Supabase (padrão: $SUPABASE_DSN).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Só mostra o plano.")
    parser.add_argument(
        "--confirmar-delete",
        action="store_true",
        help="Apaga do banco DEPOIS de exportar e conferir.",
    )
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit(
            "DSN ausente. Defina SUPABASE_DSN ou use --dsn.\n"
            "Supabase → Project Settings → Database → Connection string → URI.\n"
            "(A restrição de cota derruba a API REST, não a conexão Postgres.)"
        )

    conn = _conectar(args.dsn)
    try:
        logger.info(f"[evacuar] antes: {tamanho(conn)}")
        no_banco = contagem_por_dia(conn)
        total = sum(no_banco.values())
        logger.info(f"[evacuar] {len(no_banco)} dia(s), {total} linha(s) em {TABELA}")

        if args.dry_run:
            for dia, n in sorted(no_banco.items()):
                logger.info(f"[evacuar] (dry-run) {dia}: {n}")
            return

        if not no_banco:
            logger.info("[evacuar] tabela já vazia — nada a fazer")
            return

        gravados = exportar(conn, sorted(no_banco))
        if not conferir(gravados):
            raise SystemExit(
                "[evacuar] ❌ conferência FALHOU — nada foi apagado. "
                "Corrija o histórico e rode de novo."
            )

        logger.success(f"[evacuar] ✓ {total} linha(s) conferidas no Parquet")

        if not args.confirmar_delete:
            logger.warning(
                "[evacuar] exportação pronta e conferida, mas NADA foi apagado. "
                "Para liberar o disco rode de novo com --confirmar-delete."
            )
            return

        apagar(conn)
        logger.info(f"[evacuar] depois: {tamanho(conn)}")
        logger.info(
            "[evacuar] a restrição do Supabase sai sozinha quando a plataforma "
            "reavalia o tamanho (minutos a algumas horas)."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
