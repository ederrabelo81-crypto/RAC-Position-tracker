#!/usr/bin/env python3
"""
scripts/db_migrate_hot.py — Carrega a janela quente na base nova.

De onde vem o dado
------------------
Do **histórico frio em Parquet** (Drive ou disco), não do Supabase. Essa é a
parte boa da arquitetura de gravação dupla: desde Jul/2026 toda coleta escreve
PRIMEIRO no histórico e só depois no banco (``main.py``), então os últimos dias
já estão no Drive mesmo com o Supabase restrito por cota devolvendo 402. A
migração não precisa que o Supabase volte.

As tabelas de REFERÊNCIA (``produtos_catalogo``, ``produtos_depara_nome``,
``produtos_aliases``) não vivem no Parquet — são curadas, não coletadas. Elas
são copiadas direto do Postgres do Supabase com ``--referencias``, o que exige
a senha do banco (Supabase → Project Settings → Database → Connection string).
A restrição de cota derruba a API REST, não a conexão Postgres.

O gatilho que apagaria o dado
-----------------------------
``coletas`` tem um ``BEFORE INSERT`` (``trg_resolve_familia_coletas``) que
reescreve ``familia_resolvida``/``sku_resolvido``/``estado_match`` consultando
``produtos_depara_nome``. Num banco novo essa tabela começa VAZIA, e
``SELECT ... INTO`` sem resultado devolve NULO — ou seja, carregar o histórico
com o gatilho ligado ZERARIA a resolução de todas as linhas, silenciosamente.
Por isso a carga desliga o gatilho e o religa no fim: o que estava no Parquet
entra como estava.

USO::

    export RAC_DB_DSN="postgresql://avnadmin:...@pg-xxx.aivencloud.com:12345/defaultdb?sslmode=require"

    python scripts/db_migrate_hot.py --dry-run          # o que existe no frio
    python scripts/db_migrate_hot.py --dias 15          # carrega
    python scripts/db_migrate_hot.py --desde 2026-08-30 --ate 2026-09-14
    python scripts/db_migrate_hot.py --referencias --dsn-origem "postgresql://postgres:SENHA@db.ailbsczkrympslpjwwko.supabase.co:5432/postgres"
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger  # noqa: E402

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:  # pragma: no cover
    psycopg2 = None

# Tabelas de referência, na ordem em que precisam entrar: `produtos_catalogo`
# é alvo de chave estrangeira das outras duas.
TABELAS_REFERENCIA = (
    "produtos_catalogo",
    "produtos_depara_nome",
    "produtos_aliases",
    "plataforma_superficie",
    "seller_depara",
)

LOTE = 1000
GATILHO_COLETAS = "trg_resolve_familia_coletas"


def _conectar(dsn: str):
    if psycopg2 is None:
        raise SystemExit("psycopg2 não instalado. Rode: pip install psycopg2-binary")
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn


def colunas_da_tabela(conn, tabela: str) -> List[str]:
    """Colunas reais do destino — a fonte pode ter colunas a mais ou a menos.

    Colunas GERADAS ficam de fora: o Postgres recusa INSERT que as mencione.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            WHERE a.attrelid = %s::regclass
              AND a.attnum > 0
              AND NOT a.attisdropped
              AND a.attgenerated = ''
            ORDER BY a.attnum
            """,
            (tabela,),
        )
        return [linha[0] for linha in cur.fetchall()]


def inserir(
    conn,
    tabela: str,
    linhas: Sequence[Dict[str, Any]],
    colunas: Sequence[str],
) -> int:
    """INSERT em lote, ignorando o que já existe. Devolve quantas entraram."""
    if not linhas:
        return 0
    valores = [tuple(linha.get(c) for c in colunas) for linha in linhas]
    lista_cols = ", ".join(f'"{c}"' for c in colunas)
    sql = (
        f"INSERT INTO {tabela} ({lista_cols}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )
    with conn.cursor() as cur:
        execute_values(cur, sql, valores, page_size=LOTE)
        return cur.rowcount


def carregar_coletas(
    dsn_destino: str,
    inicio: date,
    fim: date,
    dry_run: bool,
) -> None:
    """Copia `coletas` do histórico frio para a base nova, dia a dia."""
    from utils.history import DATASET_COLETAS, get_store

    store = get_store()
    logger.info(f"[carga] histórico: {store.backend.describe}")

    dias_disponiveis = [d for d in store.days(DATASET_COLETAS) if inicio <= d <= fim]
    if not dias_disponiveis:
        logger.error(
            f"[carga] nenhum dia entre {inicio} e {fim} no histórico frio. "
            "Confira GDRIVE_FOLDER_ID/RAC_HISTORY_DIR no .env."
        )
        return

    logger.info(
        f"[carga] {len(dias_disponiveis)} dia(s) no frio: "
        f"{dias_disponiveis[0]} → {dias_disponiveis[-1]}"
    )

    if dry_run:
        for dia in dias_disponiveis:
            df = store.read(DATASET_COLETAS, start=dia, end=dia)
            logger.info(f"[carga] (dry-run) {dia}: {len(df)} linha(s)")
        return

    conn = _conectar(dsn_destino)
    try:
        colunas = colunas_da_tabela(conn, "coletas")
        # `id` sai fora: o histórico carrega o id do Supabase e reaproveitá-lo
        # amarraria a base nova à sequência da velha — na primeira coleta o
        # nextval colidiria com um id já ocupado.
        colunas = [c for c in colunas if c != "id"]

        with conn.cursor() as cur:
            cur.execute(f"ALTER TABLE coletas DISABLE TRIGGER {GATILHO_COLETAS}")
        conn.commit()
        logger.info(f"[carga] gatilho {GATILHO_COLETAS} desligado para a carga")

        total = 0
        try:
            for dia in dias_disponiveis:
                df = store.read(DATASET_COLETAS, start=dia, end=dia)
                if df.empty:
                    logger.warning(f"[carga] {dia}: nada no frio — pulado")
                    continue

                # NaN do pandas não é NULL do Postgres: sem esta troca, um
                # preço ausente entraria como o texto 'nan' numa coluna
                # numérica e a linha inteira seria rejeitada.
                df = df.astype(object).where(df.notna(), None)
                linhas = df.to_dict("records")

                entraram = inserir(conn, "coletas", linhas, colunas)
                conn.commit()
                total += entraram
                logger.success(
                    f"[carga] {dia}: {entraram} inserida(s) de {len(linhas)} lida(s)"
                )
        finally:
            with conn.cursor() as cur:
                cur.execute(f"ALTER TABLE coletas ENABLE TRIGGER {GATILHO_COLETAS}")
            conn.commit()
            logger.info(f"[carga] gatilho {GATILHO_COLETAS} religado")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), min(data)::text, max(data)::text FROM coletas"
            )
            n, dmin, dmax = cur.fetchone()
        logger.success(
            f"[carga] destino agora com {n} linha(s), de {dmin} a {dmax} "
            f"({total} inserida(s) nesta execução)"
        )
    finally:
        conn.close()


def copiar_referencias(dsn_origem: str, dsn_destino: str, dry_run: bool) -> None:
    """Copia as tabelas curadas do Supabase para a base nova.

    Sem elas o gatilho de `coletas` não resolve nada e toda coleta NOVA entra
    com `estado_match` nulo — o painel passaria a mostrar "não mapeado" para
    produto que está mapeado.
    """
    origem = _conectar(dsn_origem)
    destino = _conectar(dsn_destino)
    try:
        for tabela in TABELAS_REFERENCIA:
            try:
                colunas = colunas_da_tabela(destino, tabela)
            except psycopg2.Error:
                destino.rollback()
                logger.warning(f"[ref] {tabela} não existe no destino — pulada")
                continue

            with origem.cursor() as cur:
                try:
                    lista = ", ".join(f'"{c}"' for c in colunas)
                    cur.execute(f"SELECT {lista} FROM {tabela}")
                    linhas = [dict(zip(colunas, r)) for r in cur.fetchall()]
                except psycopg2.Error as exc:
                    origem.rollback()
                    logger.warning(f"[ref] {tabela}: leitura falhou ({exc}) — pulada")
                    continue

            if dry_run:
                logger.info(f"[ref] (dry-run) {tabela}: {len(linhas)} linha(s)")
                continue

            entraram = inserir(destino, tabela, linhas, colunas)
            destino.commit()
            logger.success(
                f"[ref] {tabela}: {entraram} inserida(s) de {len(linhas)} lida(s)"
            )
    finally:
        origem.close()
        destino.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Carrega a janela quente (e as referências) na base nova."
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("RAC_DB_DSN", "").strip(),
        help="DSN do Postgres DESTINO (padrão: $RAC_DB_DSN).",
    )
    parser.add_argument("--dias", type=int, default=15, help="Janela (padrão: 15).")
    parser.add_argument("--desde", help="Primeiro dia (YYYY-MM-DD).")
    parser.add_argument("--ate", help="Último dia (YYYY-MM-DD).")
    parser.add_argument(
        "--referencias",
        action="store_true",
        help="Copia as tabelas curadas do Supabase (exige --dsn-origem).",
    )
    parser.add_argument(
        "--dsn-origem", help="DSN do Postgres do Supabase (só com --referencias)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Não grava nada.")
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("DSN de destino ausente. Defina RAC_DB_DSN ou use --dsn.")

    if args.referencias:
        if not args.dsn_origem:
            raise SystemExit(
                "--referencias exige --dsn-origem (o Postgres do Supabase).\n"
                "Supabase → Project Settings → Database → Connection string → URI."
            )
        copiar_referencias(args.dsn_origem, args.dsn, args.dry_run)
        return

    fim = date.fromisoformat(args.ate) if args.ate else date.today()
    if args.desde:
        inicio = date.fromisoformat(args.desde)
    else:
        inicio = fim - timedelta(days=args.dias - 1)

    carregar_coletas(args.dsn, inicio, fim, args.dry_run)


if __name__ == "__main__":
    main()
