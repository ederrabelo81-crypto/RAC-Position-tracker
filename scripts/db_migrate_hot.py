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

A ORDEM IMPORTA: referências ANTES das coletas
----------------------------------------------
O Parquet **não carrega** ``familia_resolvida``/``sku_resolvido``/
``estado_match``: o mapeamento da coleta (``utils.supabase_client.map_record``)
não inclui essas colunas, porque quem as preenche é o gatilho
``trg_resolve_familia_coletas`` no banco, consultando ``produtos_depara_nome``.

Daí a ordem obrigatória: **copiar as referências primeiro** e carregar as
coletas com o gatilho **LIGADO**. Ao contrário — coletas primeiro — as linhas
entram com os três campos NULOS e copiar as referências depois não volta atrás:
`UPDATE` nenhum acontece sobre linha já gravada, e o painel passaria a mostrar
"não mapeado" para produto que está mapeado.

Por isso o script se recusa a carregar coletas com ``produtos_depara_nome``
vazia (use ``--sem-referencias`` para forçar, se souber o que está fazendo).

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


def _depara_populado(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM produtos_depara_nome")
        return int(cur.fetchone()[0])


def carregar_coletas(
    dsn_destino: str,
    inicio: date,
    fim: date,
    dry_run: bool,
    sem_referencias: bool = False,
) -> None:
    """Copia `coletas` do histórico frio para a base nova, dia a dia."""
    from utils.history import DATASET_COLETAS, get_store

    store = get_store()
    logger.info(f"[carga] histórico: {store.backend.describe}")

    dias_disponiveis = [d for d in store.days(DATASET_COLETAS) if inicio <= d <= fim]
    if not dias_disponiveis:
        # Sair com 0 aqui deixaria o operador seguir para a virada com a base
        # VAZIA achando que a carga correu bem. O código de saída é o que o
        # agendador e o `pipeline_watch` leem.
        raise SystemExit(
            f"[carga] ❌ nenhum dia entre {inicio} e {fim} no histórico frio. "
            "Confira GDRIVE_FOLDER_ID/RAC_HISTORY_DIR no .env."
        )

    logger.info(
        f"[carga] {len(dias_disponiveis)} dia(s) no frio: "
        f"{dias_disponiveis[0]} → {dias_disponiveis[-1]}"
    )

    if dry_run:
        for dia in dias_disponiveis:
            df = store.read(DATASET_COLETAS, start=dia, end=dia)
            if store.last_read_errors:
                logger.error(f"[carga] (dry-run) {dia}: partição ILEGÍVEL")
            logger.info(f"[carga] (dry-run) {dia}: {len(df)} linha(s)")
        return

    conn = _conectar(dsn_destino)
    try:
        # O Parquet não traz familia/sku/estado — quem resolve é o gatilho,
        # lendo `produtos_depara_nome`. Com ela vazia, tudo entraria NULO e
        # copiar as referências depois NÃO corrigiria as linhas já gravadas.
        n_depara = _depara_populado(conn)
        if n_depara == 0 and not sem_referencias:
            raise SystemExit(
                "[carga] ❌ `produtos_depara_nome` está VAZIA no destino.\n"
                "O Parquet não carrega familia_resolvida/sku_resolvido/"
                "estado_match — quem preenche é o gatilho, a partir dessa "
                "tabela. Carregar agora gravaria tudo NULO e copiar as "
                "referências depois não volta atrás.\n"
                "Rode primeiro:\n"
                "  python scripts/db_migrate_hot.py --referencias "
                '--dsn-origem "<DSN do Supabase>"\n'
                "(ou --sem-referencias para forçar, se souber o que faz)."
            )
        logger.info(f"[carga] de-para com {n_depara} linha(s) — gatilho resolverá")

        colunas = colunas_da_tabela(conn, "coletas")
        # `id` sai fora: o histórico carrega o id do Supabase e reaproveitá-lo
        # amarraria a base nova à sequência da velha — na primeira coleta o
        # nextval colidiria com um id já ocupado.
        colunas = [c for c in colunas if c != "id"]

        total = 0
        for dia in dias_disponiveis:
            df = store.read(DATASET_COLETAS, start=dia, end=dia)
            if store.last_read_errors:
                # `store.read` NÃO levanta em partição ilegível: devolve o que
                # conseguiu ler. Seguir daqui gravaria um recorte parcial com
                # cara de dia completo — e ninguém saberia que faltou linha.
                raise SystemExit(
                    f"[carga] ❌ {dia}: partição ilegível no histórico "
                    f"({store.last_read_errors}). Nada mais foi carregado."
                )
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

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), min(data)::text, max(data)::text, "
                "count(*) FILTER (WHERE estado_match IS NULL) FROM coletas"
            )
            n, dmin, dmax, sem_estado = cur.fetchone()
        logger.success(
            f"[carga] destino agora com {n} linha(s), de {dmin} a {dmax} "
            f"({total} inserida(s) nesta execução)"
        )
        if sem_estado:
            logger.warning(
                f"[carga] ⚠️ {sem_estado} linha(s) sem estado_match — são "
                "produtos que o de-para ainda não conhece. Rode "
                "`python scripts/resolver_diario.py` depois de completar o "
                "de-para."
            )
    finally:
        conn.close()


def _ajustar_sequencias(conn, tabela: str) -> None:
    """Empurra a sequência da PK para além do maior id copiado.

    A cópia preserva os `id`s do Supabase, mas a sequência do banco NOVO
    continua em 1. Sem este acerto, o primeiro INSERT automático tentaria um id
    já ocupado e morreria com violação de chave primária — dias depois da
    migração, longe de qualquer pista de que a causa foi a carga.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_serial_sequence(%s, a.attname), a.attname
            FROM pg_attribute a
            WHERE a.attrelid = %s::regclass AND a.attname = 'id'
            """,
            (tabela, tabela),
        )
        linha = cur.fetchone()
        if not linha or not linha[0]:
            return  # tabela sem `id` serial (ex.: produtos_catalogo, PK=sku)
        seq = linha[0]
        cur.execute(f"SELECT setval(%s, COALESCE((SELECT MAX(id) FROM {tabela}), 0) + 1, false)", (seq,))
        cur.execute(f"SELECT last_value FROM {seq}")
        logger.info(f"[ref] {tabela}: sequência ajustada para {cur.fetchone()[0]}")


def copiar_referencias(dsn_origem: str, dsn_destino: str, dry_run: bool) -> None:
    """Copia as tabelas curadas do Supabase para a base nova.

    Sem elas o gatilho de `coletas` não resolve nada e TODA linha carregada
    entra com `estado_match` nulo — o painel passaria a mostrar "não mapeado"
    para produto que está mapeado. Por isso qualquer falha aqui é FATAL: um
    aviso no meio do log seria lido como sucesso e a carga seguiria em cima de
    um de-para incompleto.
    """
    origem = _conectar(dsn_origem)
    destino = _conectar(dsn_destino)
    falhas: List[str] = []
    try:
        for tabela in TABELAS_REFERENCIA:
            try:
                colunas = colunas_da_tabela(destino, tabela)
            except psycopg2.Error as exc:
                destino.rollback()
                falhas.append(f"{tabela}: não existe no destino ({exc})")
                continue

            with origem.cursor() as cur:
                try:
                    lista = ", ".join(f'"{c}"' for c in colunas)
                    cur.execute(f"SELECT {lista} FROM {tabela}")
                    linhas = [dict(zip(colunas, r)) for r in cur.fetchall()]
                except psycopg2.Error as exc:
                    origem.rollback()
                    falhas.append(f"{tabela}: leitura falhou ({exc})")
                    continue

            if dry_run:
                logger.info(f"[ref] (dry-run) {tabela}: {len(linhas)} linha(s)")
                continue

            try:
                entraram = inserir(destino, tabela, linhas, colunas)
                _ajustar_sequencias(destino, tabela)
                destino.commit()
            except psycopg2.Error as exc:
                destino.rollback()
                falhas.append(f"{tabela}: gravação falhou ({exc})")
                continue

            logger.success(
                f"[ref] {tabela}: {entraram} inserida(s) de {len(linhas)} lida(s)"
            )
    finally:
        origem.close()
        destino.close()

    if falhas:
        raise SystemExit(
            "[ref] ❌ as referências NÃO foram copiadas por completo:\n  - "
            + "\n  - ".join(falhas)
            + "\nNÃO carregue as coletas antes de resolver isto: elas "
            "entrariam sem resolução e não há como corrigir depois sem "
            "reprocessar."
        )


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
    parser.add_argument(
        "--sem-referencias",
        action="store_true",
        help=(
            "Carrega coletas mesmo com produtos_depara_nome vazia. As linhas "
            "entram SEM resolução e não há como corrigir sem reprocessar."
        ),
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

    carregar_coletas(args.dsn, inicio, fim, args.dry_run, args.sem_referencias)


if __name__ == "__main__":
    main()
