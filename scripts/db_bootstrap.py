#!/usr/bin/env python3
"""
scripts/db_bootstrap.py — Levanta o schema do RAC num Postgres novo.

Aplica, em ordem e uma única vez cada, as migrações versionadas do projeto:

1. ``docs/migrations/019_schema_base_portavel.sql`` — SEMPRE primeiro. É o DDL
   das tabelas que nasceram à mão no painel do Supabase e nunca tiveram
   arquivo (``coletas`` entre elas). Sem ele, a 001 faz ALTER numa tabela que
   não existe.
2. ``docs/migrations/0*.sql`` — 001 a 018, na ordem numérica.
3. ``migrations/0*.sql`` — PriceTrack. Entram ANTES de 001, porque a
   ``009_sku_resolucao_v2`` referencia ``pricetrack_daily``. As tabelas nascem
   VAZIAS e não custam espaço: o que a migração tira da frente são as 995 mil
   linhas de dado, não o schema.

O que já rodou fica registrado em ``schema_migrations``, então rodar de novo é
seguro e barato — aplica só o que falta.

USO::

    export RAC_DB_DSN="postgresql://avnadmin:...@pg-xxx.aivencloud.com:12345/defaultdb?sslmode=require"
    python scripts/db_bootstrap.py --dry-run     # mostra o que falta
    python scripts/db_bootstrap.py               # aplica

O provedor PRECISA permitir ``CREATE ROLE``: o schema depende de
``anon``/``authenticated``/``service_role`` em mais de um ponto — a 007 faz
``ALTER ROLE service_role``, e a 015 e a 016 criam ``POLICY ... TO anon`` no
mesmo arquivo em que criam tabelas essenciais. Não existe, portanto, "pular as
migrações de permissão": a 019 aborta com o motivo na tela se não conseguir
criar os papéis. Na Aiven o usuário ``avnadmin`` tem essa permissão.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger  # noqa: E402

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None

RAIZ = Path(__file__).resolve().parent.parent
BASE_PORTAVEL = RAIZ / "docs" / "migrations" / "019_schema_base_portavel.sql"
DIR_PRINCIPAL = RAIZ / "docs" / "migrations"
DIR_PRICETRACK = RAIZ / "migrations"

def dividir_statements(sql: str) -> List[str]:
    """Quebra um arquivo .sql em instruções, respeitando o que não é código.

    Existe por um motivo específico: `CREATE INDEX CONCURRENTLY` é proibido
    dentro de transação, e mandar o arquivo inteiro num só `execute()` cria um
    bloco de transação IMPLÍCITO no servidor — mesmo com autocommit ligado no
    driver. Então esses arquivos precisam ir instrução a instrução.

    O divisor precisa ignorar o ponto e vírgula que aparece dentro de corpo de
    função (`$$ ... $$`), de string (`'...'`), de identificador com aspas e de
    comentário. Quebrar por `;` cru partiria `refresh_filter_options()` no meio
    e o erro apareceria como sintaxe inválida, longe da causa.
    """
    instrucoes: List[str] = []
    atual: List[str] = []
    i, n = 0, len(sql)
    tag_dolar: Optional[str] = None

    while i < n:
        ch = sql[i]

        if tag_dolar:
            if sql.startswith(tag_dolar, i):
                atual.append(tag_dolar)
                i += len(tag_dolar)
                tag_dolar = None
                continue
            atual.append(ch)
            i += 1
            continue

        # Início de bloco $$ ou $tag$
        if ch == "$":
            m = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[i:])
            if m:
                tag_dolar = m.group(0)
                atual.append(tag_dolar)
                i += len(tag_dolar)
                continue

        if ch == "'":
            fim = i + 1
            while fim < n:
                if sql[fim] == "'":
                    if fim + 1 < n and sql[fim + 1] == "'":  # aspa escapada
                        fim += 2
                        continue
                    break
                fim += 1
            atual.append(sql[i : fim + 1])
            i = fim + 1
            continue

        if ch == '"':
            fim = sql.find('"', i + 1)
            fim = n - 1 if fim == -1 else fim
            atual.append(sql[i : fim + 1])
            i = fim + 1
            continue

        if sql.startswith("--", i):
            fim = sql.find("\n", i)
            fim = n if fim == -1 else fim
            atual.append(sql[i:fim])
            i = fim
            continue

        if sql.startswith("/*", i):
            fim = sql.find("*/", i + 2)
            fim = n if fim == -1 else fim + 2
            atual.append(sql[i:fim])
            i = fim
            continue

        if ch == ";":
            texto = "".join(atual).strip()
            if texto:
                instrucoes.append(texto)
            atual = []
            i += 1
            continue

        atual.append(ch)
        i += 1

    resto = "".join(atual).strip()
    if resto:
        instrucoes.append(resto)

    # Um trecho só de comentário (o cabeçalho entre duas instruções, por
    # exemplo) vira uma "instrução" vazia para o servidor, que responde
    # "can't execute an empty query" e derruba a migração inteira por nada.
    return [s for s in instrucoes if _tem_codigo(s)]


def _tem_codigo(instrucao: str) -> bool:
    """True se sobra SQL depois de tirar comentários e espaço."""
    sem_bloco = re.sub(r"/\*.*?\*/", "", instrucao, flags=re.DOTALL)
    sem_linha = re.sub(r"--[^\n]*", "", sem_bloco)
    return bool(sem_linha.strip())


def _ordem(caminho: Path) -> Tuple[int, str]:
    """Ordena 001, 014, 014b, 015… tratando o sufixo de letra."""
    m = re.match(r"^(\d+)([a-z]?)", caminho.name)
    if not m:
        return (9999, caminho.name)
    return (int(m.group(1)), m.group(2))


def migracoes(sem_pricetrack: bool) -> List[Path]:
    """Lista os arquivos .sql a aplicar, na ordem de aplicação.

    A ordem NÃO é a numérica ingênua:

    1. ``019_schema_base_portavel.sql`` — as tabelas que nunca tiveram DDL;
    2. ``migrations/*.sql`` (PriceTrack) — porque a ``009_sku_resolucao_v2``
       referencia ``pricetrack_daily``. Criar essas tabelas VAZIAS não custa
       espaço: o que a migração está tirando da frente são as 995 mil linhas,
       não o schema;
    3. ``docs/migrations/001..018`` — na ordem numérica.
    """
    if not BASE_PORTAVEL.exists():
        raise SystemExit(f"base portátil ausente: {BASE_PORTAVEL}")

    lista = [BASE_PORTAVEL]

    if not sem_pricetrack and DIR_PRICETRACK.is_dir():
        lista.extend(sorted(DIR_PRICETRACK.glob("[0-9]*.sql"), key=_ordem))

    demais = [
        p
        for p in sorted(DIR_PRINCIPAL.glob("[0-9]*.sql"), key=_ordem)
        if p != BASE_PORTAVEL
    ]
    lista.extend(demais)

    return lista


def _garantir_registro(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            arquivo     text PRIMARY KEY,
            aplicado_em timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def _ja_aplicadas(cur) -> set:
    cur.execute("SELECT arquivo FROM schema_migrations")
    return {linha[0] for linha in cur.fetchall()}


def aplicar(dsn: str, arquivos: List[Path], dry_run: bool) -> int:
    """Aplica os arquivos que ainda não rodaram. Devolve quantos aplicou."""
    if psycopg2 is None:
        raise SystemExit("psycopg2 não instalado. Rode: pip install psycopg2-binary")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    aplicados = 0
    try:
        with conn.cursor() as cur:
            _garantir_registro(cur)
            conn.commit()
            feitas = _ja_aplicadas(cur)

        for arquivo in arquivos:
            if arquivo.name in feitas:
                logger.debug(f"[bootstrap] ✓ já aplicada: {arquivo.name}")
                continue

            if dry_run:
                logger.info(f"[bootstrap] (dry-run) aplicaria: {arquivo.name}")
                aplicados += 1
                continue

            sql = arquivo.read_text(encoding="utf-8")
            # `CREATE INDEX CONCURRENTLY` é proibido dentro de transação — o
            # Postgres recusa com "cannot run inside a transaction block".
            # Essas migrações rodam em autocommit. O preço é que uma falha no
            # meio deixa o arquivo aplicado pela metade; por isso o registro só
            # entra no fim, e a re-execução refaz o arquivo inteiro (todas as
            # migrações do projeto usam IF NOT EXISTS).
            concorrente = re.search(r"\bCONCURRENTLY\b", sql, re.IGNORECASE)

            # Cada migração é uma transação própria: se a 017 falhar, as 16
            # anteriores continuam aplicadas e registradas. Assim o operador
            # corrige uma e retoma, em vez de recomeçar do zero.
            try:
                if concorrente:
                    conn.commit()  # não dá para trocar autocommit com transação aberta
                    conn.autocommit = True
                    try:
                        with conn.cursor() as cur:
                            for instrucao in dividir_statements(sql):
                                cur.execute(instrucao)
                            cur.execute(
                                "INSERT INTO schema_migrations (arquivo) VALUES (%s)",
                                (arquivo.name,),
                            )
                    finally:
                        conn.autocommit = False
                    logger.success(f"[bootstrap] ✓ aplicada: {arquivo.name}")
                    aplicados += 1
                    continue

                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (arquivo) VALUES (%s)",
                        (arquivo.name,),
                    )
                conn.commit()
                logger.success(f"[bootstrap] ✓ aplicada: {arquivo.name}")
                aplicados += 1
            except Exception as exc:
                conn.rollback()
                logger.error(f"[bootstrap] ❌ FALHOU em {arquivo.name}: {exc}")
                logger.error(
                    "[bootstrap] as migrações anteriores seguem aplicadas; "
                    "corrija esta e rode de novo — o registro retoma daqui."
                )
                raise SystemExit(1)
    finally:
        conn.close()

    return aplicados


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Levanta o schema do RAC Position Tracker num Postgres novo."
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("RAC_DB_DSN", "").strip(),
        help="DSN do Postgres destino (padrão: $RAC_DB_DSN).",
    )
    parser.add_argument(
        "--sem-pricetrack",
        action="store_true",
        help=(
            "Não cria as tabelas do PriceTrack. ATENÇÃO: a migração 009 "
            "referencia pricetrack_daily e vai falhar sem elas."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Só mostra o plano.")
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit(
            "DSN ausente. Defina RAC_DB_DSN ou passe --dsn.\n"
            "Na Aiven: Service → Connection information → Service URI "
            "(mantenha o ?sslmode=require)."
        )

    arquivos = migracoes(args.sem_pricetrack)
    logger.info(f"[bootstrap] {len(arquivos)} migrações candidatas")

    n = aplicar(args.dsn, arquivos, args.dry_run)
    if args.dry_run:
        logger.info(f"[bootstrap] (dry-run) {n} aplicaria(m)")
    else:
        logger.success(f"[bootstrap] pronto — {n} migração(ões) aplicada(s)")


if __name__ == "__main__":
    main()
