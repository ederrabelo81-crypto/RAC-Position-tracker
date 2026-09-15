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
Exporta → CONFERE → só então apaga. E o apagamento exige o histórico no
**Drive**: com o backend local, a "cópia de segurança" ficaria só no disco
desta máquina enquanto o TRUNCATE é definitivo. A conferência relê o Parquet gravado e
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


#: Run id fixo da evacuação. Com um id estável, reexecutar sobrescreve a MESMA
#: partição em vez de acumular uma nova a cada tentativa.
RUN_ID_EVACUACAO = "evacuacao"


def exportar(conn, dias: List[date], store) -> Dict[date, List[str]]:
    """Grava um Parquet por dia no histórico frio.

    Recebe o `store` já resolvido (o MESMO que a conferência vai reler e que a
    trava de segurança já confirmou ser o Drive). Resolver um store novo aqui
    dentro poderia, num Drive intermitente, escrever num backend e conferir
    noutro.

    Returns:
        Mapa dia → chaves das partições gravadas. São essas chaves, e só elas,
        que a conferência relê: contar "tudo o que existe no frio naquele dia"
        misturaria partições antigas de outras importações e a conferência
        passaria (ou falharia) por motivo errado.
    """
    from utils.history import DATASET_PRICETRACK
    from utils.history import write_records as _wr

    def write_records(linhas, **kw):
        return _wr(linhas, store=store, **kw)

    gravados: Dict[date, List[str]] = {}
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
            run_id=RUN_ID_EVACUACAO,
            already_mapped=True,
        )
        if not chaves:
            # write_records absorve falha de backend e devolve [] — tratar
            # isso como sucesso apagaria dado que nunca chegou ao Drive.
            raise SystemExit(
                f"[evacuar] ❌ {dia}: o histórico não confirmou a gravação. "
                "NADA foi apagado. Confira GDRIVE_* no .env."
            )
        gravados[dia] = chaves
        logger.success(f"[evacuar] {dia}: {len(linhas)} linha(s) → Parquet")

    return gravados


def _linhas_na_particao(store, chave: str) -> int:
    """Conta as linhas relendo os BYTES da partição gravada."""
    import io

    import pyarrow.parquet as pq

    dados = store.backend.get(chave)
    return pq.read_table(io.BytesIO(dados)).num_rows


def conferir(no_banco: Dict[date, int], gravados: Dict[date, List[str]], store) -> bool:
    """Relê as partições gravadas e compara com o banco, dia a dia.

    Usa o MESMO `store` da exportação (passado por parâmetro) — não um resolvido
    aqui. Esta é a trava de segurança do script: relê as CHAVES que `exportar`
    acabou de escrever — não o intervalo de datas — porque um dia pode já ter
    partição antiga no frio, vinda de outra importação: contar o intervalo
    somaria as duas e a conferência viraria ruído.
    """
    ok = True
    for dia, esperado in sorted(no_banco.items()):
        chaves = gravados.get(dia, [])
        if not chaves:
            logger.error(f"[conferir] {dia}: nenhuma partição gravada")
            ok = False
            continue
        try:
            lidas = sum(_linhas_na_particao(store, c) for c in chaves)
        except Exception as exc:
            logger.error(f"[conferir] {dia}: releitura do Parquet falhou — {exc}")
            ok = False
            continue

        if lidas != esperado:
            logger.error(
                f"[conferir] {dia}: banco tem {esperado}, Parquet tem {lidas}"
            )
            ok = False
        else:
            logger.info(f"[conferir] ✓ {dia}: {esperado} linha(s) conferem")
    return ok


def evacuar_e_truncar(conn, store) -> int:
    """Exporta e trunca DENTRO de UMA transação que segura a trava o tempo todo.

    A trava `ACCESS EXCLUSIVE` é tomada ANTES do snapshot e só sai no commit,
    depois do `TRUNCATE`. Esse é o ponto: comparar contagem antes/depois não
    bastava — um import pode ATUALIZAR uma linha (mesma contagem, valor novo) no
    intervalo entre exportar e apagar, e o TRUNCATE levaria o valor novo que não
    está no Parquet. Segurando a trava desde o snapshot, nada muda no meio: o
    que a exportação leu é exatamente o que o TRUNCATE apaga.

    Usa `DELETE`? Não — `TRUNCATE`, porque `DELETE` não devolve o disco, e
    devolver o disco é o objetivo.

    Returns:
        Número de linhas evacuadas.
    """
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(f"LOCK TABLE {TABELA} IN ACCESS EXCLUSIVE MODE")
            cur.execute(f"SELECT {COLUNA_DATA}, count(*) FROM {TABELA} GROUP BY 1")
            sob_trava = {linha[0]: linha[1] for linha in cur.fetchall()}

        if not sob_trava:
            conn.rollback()
            logger.info("[evacuar] tabela já vazia sob a trava — nada a fazer")
            return 0

        # A exportação lê pela MESMA conexão, sob a mesma trava: o Parquet é
        # byte a byte o que será truncado.
        gravados = exportar(conn, sorted(sob_trava), store)
        if not conferir(sob_trava, gravados, store):
            conn.rollback()
            raise SystemExit(
                "[evacuar] ❌ conferência FALHOU sob a trava — nada foi apagado."
            )

        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {TABELA}")
        conn.commit()
        total = sum(sob_trava.values())
        logger.success(
            f"[evacuar] {TABELA} truncada ({total} linha(s)) — disco liberado"
        )
        return total
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.autocommit = True


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

        from utils.history import get_store
        from utils.history.backends import GoogleDriveBackend

        # Um único store para exportar E conferir — resolver dois poderia, num
        # Drive intermitente, escrever num backend e conferir noutro.
        store = get_store()

        if not args.confirmar_delete:
            # Modo exportação-só: sem trava, sem apagar nada.
            gravados = exportar(conn, sorted(no_banco), store)
            if not conferir(no_banco, gravados, store):
                raise SystemExit(
                    "[evacuar] ❌ conferência FALHOU — nada foi apagado. "
                    "Corrija o histórico e rode de novo."
                )
            logger.success(f"[evacuar] ✓ {total} linha(s) conferidas no Parquet")
            logger.warning(
                "[evacuar] exportação pronta e conferida, mas NADA foi apagado. "
                "Para liberar o disco rode de novo com --confirmar-delete."
            )
            return

        # TRAVA DE SEGURANÇA (destrutivo): a cópia PRECISA estar no Drive.
        # Checagem POSITIVA — `not LocalBackend` deixaria passar qualquer
        # backend degradado; exigir `GoogleDriveBackend` fecha esse buraco.
        if not isinstance(store.backend, GoogleDriveBackend):
            raise SystemExit(
                "[evacuar] ❌ o histórico NÃO está no Google Drive "
                f"({store.backend.describe}): a cópia ficaria só nesta máquina "
                "e o TRUNCATE é definitivo. NÃO vou apagar.\n"
                "Configure o Drive (python scripts/gdrive_setup.py --check) "
                "ou rode sem --confirmar-delete para só exportar."
            )

        evacuar_e_truncar(conn, store)
        logger.info(f"[evacuar] depois: {tamanho(conn)}")
        logger.info(
            "[evacuar] a restrição do Supabase sai sozinha quando a plataforma "
            "reavalia o tamanho (minutos a algumas horas)."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
