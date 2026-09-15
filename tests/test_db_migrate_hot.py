"""
tests/test_db_migrate_hot.py — Provas de `scripts/db_migrate_hot.py::copiar_referencias`.

Achado em campo (Set/2026, PC coletor real, carga real do Passo 4 da
migração Aiven): `produtos_depara_nome` copiou só 923 de 3.923 linhas lidas
da Supabase; `produtos_aliases`, 135 de 2.135. As duas têm UNIQUE em
(nome_coletado)/(titulo_norm) — a Supabase real acumulou linha duplicada na
mesma chave ao longo do tempo, e `inserir()` (ON CONFLICT DO NOTHING)
descarta o excesso.

O bug: a leitura da origem não tinha ORDER BY, então qual duplicata
"vence" o conflito era arbitrário (ordem física de armazenamento do
Postgres). Se duas linhas duplicadas discordam — nome reclassificado depois
— a versão VELHA podia vencer a mais nova, e o painel passaria a mostrar a
classificação errada para um nome que já tinha sido corrigido.

Estes testes provam que, com `ORDEM_REFERENCIA`, a linha mais recente
(por `revisado_em`/`created_at`) sempre vence — nunca uma arbitrária.

Só roda com RAC_TEST_PG_DSN; sem ele, auto-pula (mesma convenção do resto
da suíte de adaptador).
"""
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest

_DSN = os.getenv("RAC_TEST_PG_DSN", "").strip()
_precisa_pg = pytest.mark.skipif(not _DSN, reason="sem RAC_TEST_PG_DSN")

_SPEC = importlib.util.spec_from_file_location(
    "db_migrate_hot",
    Path(__file__).resolve().parent.parent / "scripts" / "db_migrate_hot.py",
)
dmh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dmh)

_AGORA = datetime.now(timezone.utc)


def _dsn_schema(schema: str) -> str:
    """DSN de teste com `search_path` fixado no schema — simula origem/destino
    isoladas sem precisar de dois bancos físicos."""
    return f"{_DSN}?options=-csearch_path%3D{schema}"


# A origem (Supabase real) tolera nome_coletado/titulo_norm duplicado — é
# EXATAMENTE o que o achado em campo mostrou (3.923 lidas, só 923 distintas).
# A UNIQUE só existe no destino (Aiven, schema do 019), então ela é quem
# aciona o ON CONFLICT DO NOTHING de `inserir()`. Reproduzir a origem COM a
# mesma UNIQUE tornaria o cenário do bug impossível de montar — e é
# precisamente esse formato solto que a origem real tem hoje.
_DDL_COMUM = """
CREATE TABLE {s}.produtos_catalogo (sku text PRIMARY KEY);

CREATE TABLE {s}.produtos_depara_nome (
    id bigserial PRIMARY KEY,
    nome_coletado text NOT NULL,
    estado text NOT NULL,
    familia text,
    sku text,
    marca_norm text,
    origem text DEFAULT 'seed' NOT NULL,
    revisado_em timestamptz,
    created_at timestamptz DEFAULT now() NOT NULL,
    voltagem text
    {unique_depara}
);

CREATE TABLE {s}.produtos_aliases (
    id bigserial PRIMARY KEY,
    titulo_norm text NOT NULL,
    sku text,
    titulo_exemplo text,
    origem text DEFAULT 'seed' NOT NULL,
    created_at timestamptz DEFAULT now() NOT NULL
    {unique_aliases}
);

CREATE TABLE {s}.plataforma_superficie (plataforma text PRIMARY KEY);
CREATE TABLE {s}.seller_depara (seller text PRIMARY KEY);
"""


@pytest.fixture
def schemas():
    """Dois schemas descartáveis nas cinco tabelas de `TABELAS_REFERENCIA`,
    sem FK (irrelevante para o bug testado: ordem de resolução de conflito,
    não integridade referencial). A UNIQUE só existe no destino — ver nota
    acima de `_DDL_COMUM`."""
    origem, destino = "t_dmh_origem", "t_dmh_destino"
    conn = psycopg2.connect(_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS t_dmh_origem CASCADE")
        cur.execute("CREATE SCHEMA t_dmh_origem")
        cur.execute(_DDL_COMUM.format(
            s=origem, unique_depara="", unique_aliases=""))

        cur.execute("DROP SCHEMA IF EXISTS t_dmh_destino CASCADE")
        cur.execute("CREATE SCHEMA t_dmh_destino")
        cur.execute(_DDL_COMUM.format(
            s=destino,
            unique_depara=", CONSTRAINT ux_depara_nome UNIQUE (nome_coletado)",
            unique_aliases=", CONSTRAINT ux_aliases_titulo UNIQUE (titulo_norm)",
        ))
    conn.close()

    yield origem, destino

    conn = psycopg2.connect(_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        for s in (origem, destino):
            cur.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
    conn.close()


@_precisa_pg
class TestCopiarReferenciasOrdemDeterministica:
    def test_depara_nome_mantem_versao_mais_recente_por_revisado_em(self, schemas):
        origem, destino = schemas
        conn = psycopg2.connect(_dsn_schema(origem))
        conn.autocommit = True
        antiga = _AGORA - timedelta(days=30)
        recente = _AGORA
        with conn.cursor() as cur:
            # Duas linhas para o MESMO nome_coletado, discordando de estado —
            # a "velha" diz REVISAR, a nova (revisado_em mais recente) diz
            # MAPEADO. Sem ORDER BY, qual delas sobrevive é arbitrário.
            cur.execute(
                "INSERT INTO produtos_depara_nome "
                "(nome_coletado, estado, familia, revisado_em, created_at) "
                "VALUES (%s, 'REVISAR', 'família_velha', %s, %s)",
                ("Ar Condicionado Duplicado 9000", antiga, antiga),
            )
            cur.execute(
                "INSERT INTO produtos_depara_nome "
                "(nome_coletado, estado, familia, revisado_em, created_at) "
                "VALUES (%s, 'MAPEADO', 'família_nova', %s, %s)",
                ("Ar Condicionado Duplicado 9000", recente, recente),
            )
        conn.close()

        dmh.copiar_referencias(
            _dsn_schema(origem), _dsn_schema(destino), dry_run=False)

        conn = psycopg2.connect(_dsn_schema(destino))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT estado, familia FROM produtos_depara_nome "
                "WHERE nome_coletado = %s",
                ("Ar Condicionado Duplicado 9000",),
            )
            linhas = cur.fetchall()
        conn.close()

        # Só UMA linha sobreviveu (ON CONFLICT DO NOTHING) — e é a mais
        # recente por revisado_em, nunca a antiga.
        assert len(linhas) == 1
        assert linhas[0] == ("MAPEADO", "família_nova")

    def test_aliases_mantem_versao_mais_recente_por_created_at(self, schemas):
        origem, destino = schemas
        conn = psycopg2.connect(_dsn_schema(origem))
        conn.autocommit = True
        antiga = _AGORA - timedelta(days=30)
        recente = _AGORA
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO produtos_aliases "
                "(titulo_norm, titulo_exemplo, created_at) "
                "VALUES (%s, 'Exemplo Velho', %s)",
                ("ar condicionado duplicado 9000", antiga),
            )
            cur.execute(
                "INSERT INTO produtos_aliases "
                "(titulo_norm, titulo_exemplo, created_at) "
                "VALUES (%s, 'Exemplo Novo', %s)",
                ("ar condicionado duplicado 9000", recente),
            )
        conn.close()

        dmh.copiar_referencias(
            _dsn_schema(origem), _dsn_schema(destino), dry_run=False)

        conn = psycopg2.connect(_dsn_schema(destino))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT titulo_exemplo FROM produtos_aliases "
                "WHERE titulo_norm = %s",
                ("ar condicionado duplicado 9000",),
            )
            linhas = cur.fetchall()
        conn.close()

        assert len(linhas) == 1
        assert linhas[0] == ("Exemplo Novo",)

    def test_revisado_em_nulo_perde_para_qualquer_revisado(self, schemas):
        """`revisado_em` é nullable — NULLS LAST garante que uma linha nunca
        revisada não vence uma que já foi, mesmo se `created_at` for mais
        recente (o dado revisado é o que importa, não o dado só coletado)."""
        origem, destino = schemas
        conn = psycopg2.connect(_dsn_schema(origem))
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO produtos_depara_nome "
                "(nome_coletado, estado, familia, revisado_em, created_at) "
                "VALUES (%s, 'MAPEADO', 'família_revisada', %s, %s)",
                ("Nome Só Coletado Depois", _AGORA - timedelta(days=10),
                 _AGORA - timedelta(days=10)),
            )
            cur.execute(
                "INSERT INTO produtos_depara_nome "
                "(nome_coletado, estado, familia, revisado_em, created_at) "
                "VALUES (%s, 'REVISAR', 'família_nao_revisada', NULL, %s)",
                ("Nome Só Coletado Depois", _AGORA),
            )
        conn.close()

        dmh.copiar_referencias(
            _dsn_schema(origem), _dsn_schema(destino), dry_run=False)

        conn = psycopg2.connect(_dsn_schema(destino))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT estado, familia FROM produtos_depara_nome "
                "WHERE nome_coletado = %s",
                ("Nome Só Coletado Depois",),
            )
            linhas = cur.fetchall()
        conn.close()

        assert len(linhas) == 1
        assert linhas[0] == ("MAPEADO", "família_revisada")

    def test_dry_run_nao_grava_nada(self, schemas):
        origem, destino = schemas
        conn = psycopg2.connect(_dsn_schema(origem))
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO produtos_depara_nome (nome_coletado, estado) "
                "VALUES ('X', 'MAPEADO')"
            )
        conn.close()

        dmh.copiar_referencias(
            _dsn_schema(origem), _dsn_schema(destino), dry_run=True)

        conn = psycopg2.connect(_dsn_schema(destino))
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM produtos_depara_nome")
            (n,) = cur.fetchone()
        conn.close()
        assert n == 0
