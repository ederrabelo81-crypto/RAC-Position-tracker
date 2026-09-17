#!/usr/bin/env python3
"""
scripts/db_vacuum.py — Diagnóstico de espaço e VACUUM do Postgres novo (Aiven).

O PROBLEMA
----------
Em 17/09/2026 o Aiven derrubou a coleta de bestsellers com
`cannot execute INSERT in a read-only transaction`. Causa mais provável: a
proteção automática de disco cheio do Aiven — quando o uso do serviço passa do
limiar de segurança do plano, ele muda `default_transaction_read_only` para
`on` sozinho, para TODA sessão nova (ver `bestsellers/storage.py::
upload_supabase`, ramo `is_aiven_read_only_error`).

REGRA DURA — por que este script não "resolve" sozinho o modo somente-leitura
-------------------------------------------------------------------------
`VACUUM` é ESCRITA: marca tuplas mortas, atualiza o mapa de visibilidade — bate
na MESMA parede que o INSERT, com o mesmo erro. Diferente do caso do Supabase
(`evacuate_pricetrack.py`), onde só a API REST devolvia 402 e a conexão
Postgres direta continuava gravável: aqui o `default_transaction_read_only` é
do PRÓPRIO Postgres, então não existe atalho por conexão direta. Enquanto o
Aiven não sair do somente-leitura, nem `--vacuum` nem `--full` funcionam — o
script detecta isso e para na hora, sem insistir tabela por tabela.

O ÚNICO jeito de sair do somente-leitura por disco cheio é de FORA da conexão:
    1. Painel do Aiven → Service → Overview/Metrics → confira o uso de disco.
    2. Faça upgrade do plano (mais disco). O Aiven detecta a folga e volta
       sozinho para leitura-escrita, tipicamente em minutos — não precisa
       reiniciar nem reconectar nada aqui.
    3. SÓ ENTÃO rode `--vacuum` (ou `--full`) para recuperar espaço e evitar
       bater no mesmo teto de novo.

O diagnóstico (sem `--vacuum`) funciona AGORA, mesmo em somente-leitura — é
só SELECT.

`VACUUM FULL` devolve espaço ao disco de verdade (o `VACUUM` simples só marca
espaço livre para REUSO dentro da própria tabela, não encolhe o arquivo); mas
reescreve a tabela inteira, trava com lock exclusivo e PRECISA de espaço livre
temporário — o que costuma ser exatamente o que falta quando o disco já está
cheio. Prefira liberar espaço por outra via (upgrade de plano, ou apagar dados
que não precisam ficar quentes, como `evacuate_pricetrack.py` fez no
Supabase) antes de tentar `--full`.

USO::

    # Diagnóstico: tamanho de cada tabela, bloat (linhas mortas), tamanho do
    # banco. Funciona mesmo com o Aiven em somente-leitura.
    python scripts/db_vacuum.py

    # VACUUM ANALYZE em todas as tabelas, maior primeiro (só depois de
    # confirmar que o Aiven já voltou a aceitar escrita)
    python scripts/db_vacuum.py --vacuum

    # Só uma tabela
    python scripts/db_vacuum.py --vacuum --tabela coletas

    # VACUUM FULL (mais lento, trava a tabela, devolve espaço de verdade) —
    # exige --confirmar por ser mais invasivo
    python scripts/db_vacuum.py --vacuum --full --confirmar --tabela pricetrack_daily
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(_RAIZ / ".env")
except ImportError:  # pragma: no cover
    pass

from loguru import logger  # noqa: E402

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None

from utils.supabase_client import is_aiven_read_only_error  # noqa: E402

_MENSAGEM_READ_ONLY = (
    "🚫 O Aiven recusou a escrita — sessão em modo somente-leitura (mesma "
    "causa do erro que a coleta relatou).\n"
    "   • Isto NÃO se resolve por aqui: VACUUM é escrita, bate na mesma "
    "parede que o INSERT.\n"
    "   • Confira o painel do Aiven (Service → Overview/Metrics) o uso de "
    "disco e faça upgrade do plano — ele volta para leitura-escrita "
    "sozinho quando a folga aparece, sem precisar reiniciar nada aqui.\n"
    "   • Depois disso, rode este script de novo com --vacuum."
)


def _conectar(dsn: str):
    if psycopg2 is None:
        raise SystemExit("psycopg2 não instalado. Rode: pip install psycopg2-binary")
    conn = psycopg2.connect(dsn)
    # VACUUM não roda dentro de bloco de transação — precisa de autocommit,
    # igual à conexão que utils/db.py usa para o resto do projeto.
    conn.autocommit = True
    return conn


def tamanho_banco(conn) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        )
        (tamanho,) = cur.fetchone()
    return tamanho


def tamanhos_por_tabela(conn) -> List[dict]:
    """Maior primeiro — é nela que o `--vacuum` sem `--tabela` começa."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                schemaname AS schema,
                relname AS tabela,
                pg_size_pretty(pg_total_relation_size(relid)) AS tamanho,
                pg_total_relation_size(relid) AS tamanho_bytes,
                n_live_tup AS linhas_vivas,
                n_dead_tup AS linhas_mortas,
                last_vacuum,
                last_autovacuum
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            """
        )
        return [dict(r) for r in cur.fetchall()]


def _relatorio(conn) -> List[dict]:
    linhas = tamanhos_por_tabela(conn)
    logger.info(f"[db_vacuum] tamanho do banco: {tamanho_banco(conn)}")
    if not linhas:
        logger.info("[db_vacuum] nenhuma tabela em pg_stat_user_tables.")
        return linhas
    logger.info(f"[db_vacuum] {len(linhas)} tabela(s), maior primeiro:")
    for linha in linhas:
        # O mais recente dos dois, não `last_autovacuum` por padrão: um
        # VACUUM manual mais novo que o último autovacuum ficaria escondido
        # atrás do timestamp desatualizado.
        ultimo = max(
            (ts for ts in (linha["last_autovacuum"], linha["last_vacuum"]) if ts),
            default="nunca",
        )
        # `n_live_tup`/`n_dead_tup` não deveriam vir nulos (o coletor de
        # estatísticas inicializa em zero), mas uma tabela recém-criada antes
        # do primeiro ANALYZE é o caso onde isso já foi visto — `or 0` evita
        # que o relatório (que é o único caminho que funciona em somente-
        # leitura) quebre por causa de uma tabela vazia.
        vivas = linha["linhas_vivas"] or 0
        mortas = linha["linhas_mortas"] or 0
        logger.info(
            f"  {linha['tabela']:<32} {linha['tamanho']:>10}  "
            f"{vivas:>10} viva(s)  "
            f"{mortas:>10} morta(s)  "
            f"último vacuum: {ultimo}"
        )
    return linhas


def _vacuum_tabela(conn, tabela: str, schema: str, full: bool) -> Optional[bool]:
    """Roda VACUUM (ou VACUUM FULL) numa tabela.

    Returns:
        True: sucesso. False: falhou (lock, permissão, ...) mas as outras
        tabelas ainda podem seguir. None: bateu no modo somente-leitura —
        sinal para o chamador PARAR, insistir nas próximas não vai ajudar.
    """
    modo = "VACUUM FULL" if full else "VACUUM"
    logger.info(f"[db_vacuum] {modo} (VERBOSE, ANALYZE) {schema}.{tabela} ...")
    try:
        with conn.cursor() as cur:
            verbo = "VACUUM (FULL, VERBOSE, ANALYZE)" if full else "VACUUM (VERBOSE, ANALYZE)"
            cur.execute(f'{verbo} "{schema}"."{tabela}"')
    except Exception as exc:
        if is_aiven_read_only_error(exc):
            logger.error(f"[db_vacuum] {_MENSAGEM_READ_ONLY}")
            return None
        logger.error(f"[db_vacuum] {schema}.{tabela}: falhou — {exc}")
        return False
    logger.success(f"[db_vacuum] {schema}.{tabela}: concluído.")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico de espaço e VACUUM do Postgres direto (Aiven).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("RAC_DB_DSN", "").strip(),
        help="DSN do Postgres novo (padrão: $RAC_DB_DSN).",
    )
    parser.add_argument(
        "--vacuum", action="store_true",
        help="Roda VACUUM ANALYZE de verdade (sem isto, só mostra o diagnóstico).",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Usa VACUUM FULL (devolve espaço ao disco; trava a tabela; exige --confirmar).",
    )
    parser.add_argument(
        "--tabela", default=None,
        help=(
            "Limita a uma tabela (padrão: todas, maior primeiro). Aceita "
            "'tabela' ou 'schema.tabela' — nome ambíguo entre schemas exige "
            "a forma qualificada, para --full nunca travar mais de uma sem "
            "querer."
        ),
    )
    parser.add_argument(
        "--confirmar", action="store_true",
        help="Obrigatório junto de --full — VACUUM FULL trava a tabela inteira.",
    )
    args = parser.parse_args(argv)

    if not args.dsn:
        raise SystemExit(
            "DSN ausente. Defina RAC_DB_DSN no .env ou use --dsn.\n"
            "docs/MIGRACAO_AIVEN.md tem o formato esperado."
        )
    if args.full and not args.vacuum:
        args.vacuum = True
    if args.full and not args.confirmar:
        raise SystemExit(
            "--full exige --confirmar: VACUUM FULL trava a tabela inteira com "
            "lock exclusivo e precisa de espaço livre temporário para "
            "reescrevê-la — não é para rodar sem saber o custo."
        )

    conn = _conectar(args.dsn)
    try:
        linhas = _relatorio(conn)

        if not args.vacuum:
            logger.info(
                "[db_vacuum] só diagnóstico (sem --vacuum, nada foi escrito)."
            )
            return 0

        if args.tabela:
            if "." in args.tabela:
                schema_alvo, tabela_alvo = args.tabela.split(".", 1)
                alvo = [
                    l for l in linhas
                    if l["schema"] == schema_alvo and l["tabela"] == tabela_alvo
                ]
            else:
                alvo = [l for l in linhas if l["tabela"] == args.tabela]
                # Nome sozinho casando mais de um schema é ambíguo — com
                # --full isso travaria e reescreveria tabelas que ninguém
                # pediu. Recusar é mais seguro que adivinhar qual schema.
                if len(alvo) > 1:
                    schemas = ", ".join(f'{l["schema"]}.{l["tabela"]}' for l in alvo)
                    logger.error(
                        f"[db_vacuum] '{args.tabela}' existe em mais de um "
                        f"schema ({schemas}) — use a forma qualificada "
                        "'schema.tabela'."
                    )
                    return 2
            if not alvo:
                logger.error(f"[db_vacuum] tabela não encontrada: {args.tabela}")
                return 2
        else:
            alvo = linhas

        falharam: List[str] = []
        for linha in alvo:
            resultado = _vacuum_tabela(conn, linha["tabela"], linha["schema"], args.full)
            if resultado is None:
                return 1
            if not resultado:
                falharam.append(f'{linha["schema"]}.{linha["tabela"]}')

        logger.info(f"[db_vacuum] tamanho do banco depois: {tamanho_banco(conn)}")
        if falharam:
            logger.error(
                f"[db_vacuum] {len(falharam)}/{len(alvo)} tabela(s) falharam: "
                f"{', '.join(falharam)}"
            )
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
