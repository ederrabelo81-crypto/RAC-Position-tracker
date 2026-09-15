"""
tests/test_db_adapter.py — Provas do adaptador Postgres (`utils/db.py`).

O adaptador imita a API fluente do `supabase-py` sobre SQL puro. Ele é a peça
de MAIOR risco da migração: se traduzir um filtro errado, devolve um recorte
plausível e errado — e recorte errado com cara de certo é o modo de falha que
este projeto inteiro tenta evitar. Por isso os testes de tradução rodam contra
um Postgres DE VERDADE, não contra asserção de string.

Dois blocos:

* **hermético** — parsing do `or_()` e recusa de filtro desconhecido. Roda
  sempre, inclusive no CI (`.github/workflows/tests.yml`), sem banco.
* **integração** — round-trip real. Só roda com `RAC_TEST_PG_DSN` apontando
  para um Postgres descartável; sem ele, auto-pula (mesma convenção dos
  testes de Supabase).

Subir um Postgres local para rodar o bloco de integração::

    initdb -D /tmp/pgdata -U racadmin --auth=trust
    pg_ctl -D /tmp/pgdata -o "-p 5433" start
    export RAC_TEST_PG_DSN="postgresql://racadmin@localhost:5433/postgres"
"""

import os

import pytest

from utils.db import (
    DBError,
    PostgresClient,
    UnsupportedFilterError,
    _cond_to_sql,
    _split_top_level,
    resolve_backend_name,
)

_DSN = os.getenv("RAC_TEST_PG_DSN", "").strip()
_precisa_pg = pytest.mark.skipif(not _DSN, reason="sem RAC_TEST_PG_DSN")


# --------------------------------------------------------------------------- #
# Hermético — parsing do or_()
# --------------------------------------------------------------------------- #


class TestSplitTopLevel:
    def test_virgula_simples(self):
        assert _split_top_level("a.eq.1,b.eq.2") == ["a.eq.1", "b.eq.2"]

    def test_respeita_parenteses_do_grupo_and(self):
        # O keyset composto do app.py: a vírgula DENTRO do and(...) não pode
        # quebrar a condição em duas.
        expr = "data.lt.2026-09-01,and(data.eq.2026-09-01,id.lt.99)"
        assert _split_top_level(expr) == [
            "data.lt.2026-09-01",
            "and(data.eq.2026-09-01,id.lt.99)",
        ]

    def test_ignora_vazios(self):
        assert _split_top_level("a.eq.1,,") == ["a.eq.1"]

    def test_string_vazia(self):
        assert _split_top_level("") == []


class TestCondToSql:
    def test_is_null(self):
        frag, params = _cond_to_sql("price_basis.is.null")
        assert params == []

    def test_valor_com_ponto_nao_quebra(self):
        # Preço e URL têm ponto; o split só pode consumir os dois primeiros.
        _, params = _cond_to_sql("preco.gte.1994.91")
        assert params == ["1994.91"]

    def test_ilike_preserva_curinga(self):
        _, params = _cond_to_sql("produto.ilike.%12.000%")
        assert params == ["%12.000%"]

    def test_operador_desconhecido_falha_alto(self):
        with pytest.raises(UnsupportedFilterError):
            _cond_to_sql("produto.fts.ar condicionado")

    def test_negacao_nao_suportada_falha_alto(self):
        with pytest.raises(UnsupportedFilterError):
            _cond_to_sql("produto.not.eq.x")

    def test_sintaxe_incompleta_falha_alto(self):
        with pytest.raises(UnsupportedFilterError):
            _cond_to_sql("produto")


class TestResolveBackend:
    def test_valor_invalido_falha_alto(self, monkeypatch):
        monkeypatch.setenv("RAC_DB_BACKEND", "mysql")
        with pytest.raises(DBError):
            resolve_backend_name()

    def test_auto_sem_dsn_cai_no_supabase(self, monkeypatch):
        monkeypatch.setenv("RAC_DB_BACKEND", "auto")
        monkeypatch.setenv("RAC_DB_DSN", "")
        monkeypatch.setenv("SUPABASE_DSN", "")
        assert resolve_backend_name() == "supabase"

    def test_auto_com_dsn_vai_para_postgres(self, monkeypatch):
        monkeypatch.setenv("RAC_DB_BACKEND", "auto")
        monkeypatch.setenv("RAC_DB_DSN", "postgresql://x/y")
        assert resolve_backend_name() == "postgres"

    def test_dsn_invalido_falha_no_get_client(self, monkeypatch):
        # HERMÉTICO de propósito: o DSN aponta para uma porta morta (127.0.0.1:1),
        # recusado JÁ no connect — não precisa de Postgres de teste. Este é o
        # exato caminho fail-fast que `verificar_conexao()` protege, e ele tem
        # que rodar no CI (sem RAC_TEST_PG_DSN), não só quando há banco.
        from utils.db import DBError, get_client

        monkeypatch.setenv("RAC_DB_DSN", "postgresql://ninguem@127.0.0.1:1/naoexiste")
        with pytest.raises(DBError):
            get_client("postgres")

    def test_postgres_sem_dsn_falha_com_mensagem_util(self, monkeypatch):
        from utils.db import get_client

        monkeypatch.setenv("RAC_DB_DSN", "")
        monkeypatch.setenv("SUPABASE_DSN", "")
        with pytest.raises(DBError, match="RAC_DB_DSN"):
            get_client("postgres")


# --------------------------------------------------------------------------- #
# Integração — round-trip contra Postgres real
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client():
    c = PostgresClient(_DSN)
    c._fetch_raw = None
    with c._connection().cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS t_coletas")
        cur.execute(
            """
            CREATE TABLE t_coletas (
                id          serial PRIMARY KEY,
                data        date,
                turno       text,
                plataforma  text,
                produto     text,
                preco       numeric,
                qtd         int,
                UNIQUE (data, turno, plataforma)
            )
            """
        )
        cur.execute(
            """
            INSERT INTO t_coletas (data, turno, plataforma, produto, preco, qtd)
            VALUES
              ('2026-09-10','Abertura','Amazon','Midea 12000', 1994.91, 3),
              ('2026-09-10','Tarde','Amazon','LG 9000', 2500.00, 1),
              ('2026-09-11','Abertura','Magalu','Midea 18000', NULL, 7),
              ('2026-09-12','Fechamento','Shopee','Gree 12000', 1500.50, NULL)
            """
        )
    yield c
    with c._connection().cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS t_coletas")
    c.close()


@_precisa_pg
class TestSelect:
    def test_select_tudo(self, client):
        r = client.table("t_coletas").select("*").execute()
        assert len(r.data) == 4
        assert "plataforma" in r.data[0]

    def test_projecao_de_colunas(self, client):
        r = client.table("t_coletas").select("id, plataforma").execute()
        assert set(r.data[0].keys()) == {"id", "plataforma"}

    def test_eq(self, client):
        r = client.table("t_coletas").select("*").eq("plataforma", "Amazon").execute()
        assert len(r.data) == 2

    def test_in_(self, client):
        r = (
            client.table("t_coletas")
            .select("*")
            .in_("plataforma", ["Magalu", "Shopee"])
            .execute()
        )
        assert {x["plataforma"] for x in r.data} == {"Magalu", "Shopee"}

    def test_in_com_lista_vazia_devolve_nada(self, client):
        r = client.table("t_coletas").select("*").in_("plataforma", []).execute()
        assert r.data == []

    def test_gte_lte_faixa_de_datas(self, client):
        r = (
            client.table("t_coletas")
            .select("*")
            .gte("data", "2026-09-11")
            .lte("data", "2026-09-12")
            .execute()
        )
        assert len(r.data) == 2

    def test_is_null(self, client):
        r = client.table("t_coletas").select("*").is_("preco", "null").execute()
        assert len(r.data) == 1
        assert r.data[0]["plataforma"] == "Magalu"

    def test_match_vira_and_de_eq(self, client):
        r = (
            client.table("t_coletas")
            .select("*")
            .match({"plataforma": "Amazon", "turno": "Tarde"})
            .execute()
        )
        assert len(r.data) == 1
        assert r.data[0]["produto"] == "LG 9000"

    def test_order_e_limit(self, client):
        r = client.table("t_coletas").select("*").order("id", desc=True).limit(2).execute()
        assert [x["id"] for x in r.data] == sorted(
            [x["id"] for x in r.data], reverse=True
        )
        assert len(r.data) == 2

    def test_range_e_inclusivo_nos_dois_extremos(self, client):
        # PostgREST: range(0,1) devolve DUAS linhas, não uma.
        r = client.table("t_coletas").select("*").order("id").range(0, 1).execute()
        assert len(r.data) == 2

    def test_count_exact_com_head_nao_traz_linhas(self, client):
        r = (
            client.table("t_coletas")
            .select("id", count="exact", head=True)
            .eq("plataforma", "Amazon")
            .execute()
        )
        assert r.count == 2
        assert r.data == []

    def test_count_exact_respeita_o_filtro_e_nao_o_limit(self, client):
        # O count do PostgREST é do RECORTE, não da página.
        r = (
            client.table("t_coletas")
            .select("*", count="exact")
            .gte("data", "2026-09-10")
            .limit(1)
            .execute()
        )
        assert r.count == 4
        assert len(r.data) == 1

    def test_projecao_com_relacionamento_falha_alto(self, client):
        with pytest.raises(UnsupportedFilterError):
            client.table("t_coletas").select("id, outra(nome)").execute()


@_precisa_pg
class TestOrFilter:
    def test_or_simples_is_null_ou_eq(self, client):
        r = (
            client.table("t_coletas")
            .select("*")
            .or_("preco.is.null,plataforma.eq.Shopee")
            .execute()
        )
        assert {x["plataforma"] for x in r.data} == {"Magalu", "Shopee"}

    def test_or_com_grupo_and_keyset_composto(self, client):
        # O padrão exato de paginação por keyset do app.py.
        todas = client.table("t_coletas").select("*").order("id").execute().data
        corte = todas[2]
        r = (
            client.table("t_coletas")
            .select("*")
            .or_(
                f"data.lt.{corte['data']},"
                f"and(data.eq.{corte['data']},id.lt.{corte['id']})"
            )
            .execute()
        )
        ids = {x["id"] for x in r.data}
        assert corte["id"] not in ids
        assert todas[0]["id"] in ids

    def test_or_combina_com_outros_filtros_como_and(self, client):
        # PostgREST: or_() é UM termo do AND geral, não substitui os demais.
        r = (
            client.table("t_coletas")
            .select("*")
            .eq("plataforma", "Amazon")
            .or_("turno.eq.Tarde,turno.eq.Fechamento")
            .execute()
        )
        assert len(r.data) == 1
        assert r.data[0]["produto"] == "LG 9000"

    def test_or_com_ilike(self, client):
        r = (
            client.table("t_coletas")
            .select("*")
            .or_("produto.ilike.%midea%,produto.ilike.%gree%")
            .execute()
        )
        assert len(r.data) == 3


@_precisa_pg
class TestEscrita:
    def test_insert_devolve_linhas_gravadas(self, client):
        r = (
            client.table("t_coletas")
            .insert([{"data": "2026-09-13", "turno": "Tarde", "plataforma": "Leroy"}])
            .execute()
        )
        assert len(r.data) == 1
        assert r.data[0]["id"] > 0
        assert r.data[0]["plataforma"] == "Leroy"

    def test_insert_em_lote(self, client):
        linhas = [
            {"data": "2026-09-13", "turno": "Abertura", "plataforma": f"P{i}"}
            for i in range(5)
        ]
        r = client.table("t_coletas").insert(linhas).execute()
        assert len(r.data) == 5

    def test_upsert_atualiza_no_conflito(self, client):
        linha = {
            "data": "2026-09-10",
            "turno": "Abertura",
            "plataforma": "Amazon",
            "produto": "REESCRITO",
        }
        client.table("t_coletas").upsert(
            [linha], on_conflict="data,turno,plataforma"
        ).execute()
        r = (
            client.table("t_coletas")
            .select("produto")
            .match({"data": "2026-09-10", "turno": "Abertura"})
            .execute()
        )
        assert r.data[0]["produto"] == "REESCRITO"

    def test_upsert_nao_duplica(self, client):
        linha = {"data": "2026-09-10", "turno": "Abertura", "plataforma": "Amazon"}
        client.table("t_coletas").upsert(
            [linha], on_conflict="data,turno,plataforma"
        ).execute()
        r = client.table("t_coletas").select("id", count="exact", head=True).execute()
        assert r.count == 4

    def test_update_com_filtro(self, client):
        client.table("t_coletas").update({"qtd": 99}).eq(
            "plataforma", "Amazon"
        ).execute()
        r = client.table("t_coletas").select("qtd").eq("plataforma", "Amazon").execute()
        assert all(x["qtd"] == 99 for x in r.data)

    def test_update_sem_filtro_atinge_tudo(self, client):
        # Espelha o PostgREST: sem filtro, o UPDATE é geral. O teste existe
        # para que isso seja uma escolha registrada, não uma surpresa.
        r = client.table("t_coletas").update({"qtd": 1}).execute()
        assert len(r.data) == 4

    def test_delete_com_filtro_devolve_o_que_saiu(self, client):
        r = client.table("t_coletas").delete().eq("plataforma", "Shopee").execute()
        assert len(r.data) == 1
        resto = client.table("t_coletas").select("id", count="exact", head=True).execute()
        assert resto.count == 3

    def test_delete_com_or_(self, client):
        r = (
            client.table("t_coletas")
            .delete()
            .or_("plataforma.eq.Shopee,plataforma.eq.Magalu")
            .execute()
        )
        assert len(r.data) == 2


@_precisa_pg
class TestRPC:
    def test_rpc_escalar_devolve_valor_cru(self, client):
        with client._connection().cursor() as cur:
            cur.execute(
                "CREATE OR REPLACE FUNCTION t_dobro(p_n int) RETURNS int "
                "LANGUAGE sql AS $$ SELECT p_n * 2 $$"
            )
        r = client.rpc("t_dobro", {"p_n": 21}).execute()
        assert r.data == 42

    def test_rpc_sem_parametro(self, client):
        with client._connection().cursor() as cur:
            cur.execute(
                "CREATE OR REPLACE FUNCTION t_total() RETURNS bigint "
                "LANGUAGE sql AS $$ SELECT count(*) FROM t_coletas $$"
            )
        assert client.rpc("t_total").execute().data == 4

    def test_rpc_tabular_devolve_lista_de_dicts(self, client):
        with client._connection().cursor() as cur:
            cur.execute(
                "CREATE OR REPLACE FUNCTION t_por_plataforma() "
                "RETURNS TABLE(plataforma text, n bigint) LANGUAGE sql AS "
                "$$ SELECT plataforma, count(*) FROM t_coletas GROUP BY 1 $$"
            )
        r = client.rpc("t_por_plataforma").execute()
        assert isinstance(r.data, list)
        assert {x["plataforma"] for x in r.data} == {
            "Amazon",
            "Magalu",
            "Shopee",
        }


@_precisa_pg
class TestResiliencia:
    def test_reconecta_se_a_conexao_cair(self, client):
        client.table("t_coletas").select("id").limit(1).execute()
        client._conn.close()  # simula queda de rede/idle timeout do provedor
        r = client.table("t_coletas").select("id", count="exact", head=True).execute()
        assert r.count == 4

    def test_injecao_em_valor_nao_executa(self, client):
        malicioso = "'; DROP TABLE t_coletas; --"
        r = client.table("t_coletas").select("*").eq("plataforma", malicioso).execute()
        assert r.data == []
        # A tabela continua de pé: o valor foi parametrizado, não concatenado.
        assert (
            client.table("t_coletas").select("id", count="exact", head=True).execute().count
            == 4
        )


@_precisa_pg
class TestFidelidadeDeTipo:
    """O adaptador tem que falar a MESMA língua que o PostgREST falava.

    Estes testes existem porque a diferença é silenciosa: `Decimal` no lugar de
    `str` não levanta exceção, só muda o resultado alguns passos adiante — o
    `seller_app` converte numeric→número contando com a string, e há código que
    fatia a data como texto.
    """

    def test_numeric_vira_string(self, client):
        r = client.table("t_coletas").select("preco").eq("plataforma", "Shopee").execute()
        assert r.data[0]["preco"] == "1500.50"
        assert isinstance(r.data[0]["preco"], str)

    def test_date_vira_texto_iso(self, client):
        r = client.table("t_coletas").select("data").eq("plataforma", "Shopee").execute()
        assert r.data[0]["data"] == "2026-09-12"
        assert isinstance(r.data[0]["data"], str)

    def test_int_continua_int(self, client):
        r = client.table("t_coletas").select("qtd").eq("plataforma", "Magalu").execute()
        assert r.data[0]["qtd"] == 7
        assert isinstance(r.data[0]["qtd"], int)

    def test_null_continua_none(self, client):
        r = client.table("t_coletas").select("preco").eq("plataforma", "Magalu").execute()
        assert r.data[0]["preco"] is None

    def test_timestamp_usa_T_como_separador(self, client):
        with client._connection().cursor() as cur:
            cur.execute("ALTER TABLE t_coletas ADD COLUMN criado_em timestamptz")
            cur.execute("UPDATE t_coletas SET criado_em = '2026-09-12 08:30:00+00'")
        r = client.table("t_coletas").select("criado_em").limit(1).execute()
        valor = r.data[0]["criado_em"]
        assert isinstance(valor, str)
        assert valor.startswith("2026-09-12T08:30:00")

    def test_jsonb_vira_objeto_python(self, client):
        # PostgREST entrega jsonb como objeto, não como string.
        with client._connection().cursor() as cur:
            cur.execute("ALTER TABLE t_coletas ADD COLUMN extra jsonb")
            cur.execute("""UPDATE t_coletas SET extra = '{"a": [1, 2]}'::jsonb""")
        r = client.table("t_coletas").select("extra").limit(1).execute()
        assert r.data[0]["extra"] == {"a": [1, 2]}

    def test_registro_de_tipo_nao_vaza_para_outras_conexoes(self, client):
        # O pricetrack_importer usa psycopg2 direto e espera Decimal/date
        # nativos. Se o registro fosse global, ele quebraria junto.
        import psycopg2
        from decimal import Decimal

        client.table("t_coletas").select("preco").limit(1).execute()
        outra = psycopg2.connect(_DSN)
        try:
            with outra.cursor() as cur:
                cur.execute("SELECT preco FROM t_coletas WHERE plataforma='Shopee'")
                assert isinstance(cur.fetchone()[0], Decimal)
        finally:
            outra.close()


@_precisa_pg
class TestRPCFidelidadeComPostgREST:
    """Função escalar → valor cru; função TABLE → lista. Como o PostgREST."""

    def test_jsonb_escalar_volta_como_dict_e_nao_embrulhado(self, client):
        # Regressão: embrulhado em [{'fn': {...}}], o consumidor de app.py
        # (`{k: int(v) for k, v in data.items()}`) estoura e o `except` engole
        # — o banner de cobertura sumiria sem erro visível.
        with client._connection().cursor() as cur:
            cur.execute(
                "CREATE OR REPLACE FUNCTION t_cobertura() RETURNS jsonb "
                "LANGUAGE sql STABLE AS $$ SELECT jsonb_build_object("
                "'total', count(*), 'MAPEADO', 0) FROM t_coletas $$"
            )
        r = client.rpc("t_cobertura").execute()
        assert isinstance(r.data, dict)
        assert r.data["total"] == 4
        assert {k: int(v or 0) for k, v in r.data.items()}["total"] == 4

    def test_funcao_table_continua_lista(self, client):
        with client._connection().cursor() as cur:
            cur.execute(
                "CREATE OR REPLACE FUNCTION t_resolver() "
                "RETURNS TABLE(resolvidas bigint, tier_a bigint) LANGUAGE sql "
                "AS $$ SELECT count(*), 0::bigint FROM t_coletas $$"
            )
        r = client.rpc("t_resolver").execute()
        assert isinstance(r.data, list)
        assert r.data[0]["resolvidas"] == 4

    def test_funcao_escalar_com_varias_linhas_continua_lista(self, client):
        with client._connection().cursor() as cur:
            cur.execute(
                "CREATE OR REPLACE FUNCTION t_ids() RETURNS SETOF int "
                "LANGUAGE sql AS $$ SELECT id FROM t_coletas ORDER BY id $$"
            )
        r = client.rpc("t_ids").execute()
        assert isinstance(r.data, list)
        assert len(r.data) == 4


@_precisa_pg
class TestUpsertIgnoreDuplicates:
    """`ignore_duplicates` separa os dois upserts — e o projeto usa os dois.

    A coleta (`utils/supabase_client.upload_to_supabase`) chama com True: linha
    já gravada NÃO pode ser reescrita, senão a observação do turno se perde. O
    de-para chama com o padrão False, porque ali a última versão é que vale.
    """

    def test_true_nao_reescreve(self, client):
        linha = {
            "data": "2026-09-10",
            "turno": "Abertura",
            "plataforma": "Amazon",
            "produto": "NAO DEVE ENTRAR",
        }
        client.table("t_coletas").upsert(
            [linha], on_conflict="data,turno,plataforma", ignore_duplicates=True
        ).execute()
        r = (
            client.table("t_coletas")
            .select("produto")
            .match({"data": "2026-09-10", "turno": "Abertura"})
            .execute()
        )
        assert r.data[0]["produto"] == "Midea 12000"

    def test_false_reescreve(self, client):
        linha = {
            "data": "2026-09-10",
            "turno": "Abertura",
            "plataforma": "Amazon",
            "produto": "DEVE ENTRAR",
        }
        client.table("t_coletas").upsert(
            [linha], on_conflict="data,turno,plataforma", ignore_duplicates=False
        ).execute()
        r = (
            client.table("t_coletas")
            .select("produto")
            .match({"data": "2026-09-10", "turno": "Abertura"})
            .execute()
        )
        assert r.data[0]["produto"] == "DEVE ENTRAR"

    def test_linha_nova_entra_mesmo_com_ignore(self, client):
        client.table("t_coletas").upsert(
            [{"data": "2026-09-20", "turno": "Tarde", "plataforma": "Leroy"}],
            on_conflict="data,turno,plataforma",
            ignore_duplicates=True,
        ).execute()
        r = client.table("t_coletas").select("id", count="exact", head=True).execute()
        assert r.count == 5

    def test_insert_aceita_kwargs_do_postgrest(self, client):
        # `count=`/`returning=` aparecem em chamadas do projeto; a assinatura
        # precisa aceitá-los mesmo sem usá-los, senão quebra na hora errada.
        r = (
            client.table("t_coletas")
            .insert(
                [{"data": "2026-09-21", "turno": "Tarde", "plataforma": "X"}],
                count="exact",
            )
            .execute()
        )
        assert len(r.data) == 1


@_precisa_pg
class TestNegacao:
    """`.not_` é uma *property* no postgrest-py, não um método.

    Foi por isso que escapou da primeira varredura da API (feita com grep por
    `.not_(`). São 9 call sites reais; sem a property cada um levantaria
    AttributeError, engolido pelo `except` do chamador — o seletor de produtos
    do dashboard ficaria VAZIO sem erro nenhum.
    """

    def test_not_is_null_vira_is_not_null(self, client):
        r = client.table("t_coletas").select("*").not_.is_("preco", "null").execute()
        assert len(r.data) == 3
        assert all(x["preco"] is not None for x in r.data)

    def test_not_in_exclui(self, client):
        r = (
            client.table("t_coletas")
            .select("*")
            .not_.in_("plataforma", ["Amazon", "Magalu"])
            .execute()
        )
        assert {x["plataforma"] for x in r.data} == {"Shopee"}

    def test_not_eq(self, client):
        r = client.table("t_coletas").select("*").not_.eq("plataforma", "Amazon").execute()
        assert "Amazon" not in {x["plataforma"] for x in r.data}

    def test_negacao_nao_vaza_para_o_filtro_seguinte(self, client):
        # A flag tem que ser consumida pelo filtro que a armou. Se vazasse, o
        # `eq` abaixo viraria `<>` e o recorte sairia errado sem erro nenhum.
        r = (
            client.table("t_coletas")
            .select("*")
            .not_.is_("preco", "null")
            .eq("plataforma", "Amazon")
            .execute()
        )
        assert len(r.data) == 2
        assert all(x["plataforma"] == "Amazon" for x in r.data)

    def test_negacao_combina_com_count(self, client):
        r = (
            client.table("t_coletas")
            .select("id", count="exact", head=True)
            .not_.is_("preco", "null")
            .execute()
        )
        assert r.count == 3


@_precisa_pg
class TestRetryNaoDuplicaEscrita:
    """Repetir uma escrita depois da conexão cair pode gravar duas vezes.

    A conexão é autocommit: se a rede cair depois que o Postgres aplicou o
    INSERT mas antes de a resposta voltar, repetir duplica. `pipeline_heartbeat`
    e `pricetrack_import_log` não têm chave única que segure isso — o
    livro-razão que existe para denunciar execução AUSENTE passaria a inventar
    execução REPETIDA.
    """

    def test_escrita_nao_repete_apos_falha_na_execucao(self, client):
        import psycopg2

        from utils.db import DBError

        chamadas = []

        def _falha(cur):
            chamadas.append(1)
            raise psycopg2.OperationalError("server closed the connection")

        client._connection()  # conexão viva: a falha é na execução, não no connect
        with pytest.raises(DBError, match="ESCRITA"):
            client._run(_falha, escrita=True)
        assert len(chamadas) == 1, "a escrita foi repetida — pode duplicar linha"

    def test_leitura_repete_uma_vez(self, client):
        import psycopg2

        from utils.db import DBError

        chamadas = []

        def _falha(cur):
            chamadas.append(1)
            raise psycopg2.OperationalError("server closed the connection")

        client._connection()
        with pytest.raises(DBError):
            client._run(_falha, escrita=False)
        assert len(chamadas) == 2, "leitura deveria ter sido repetida uma vez"

    def test_reconexao_antes_do_envio_segue_valendo_para_escrita(self, client):
        # Conexão morta ANTES do comando sair: nada chegou ao servidor, então
        # reconectar e gravar é seguro — e precisa continuar funcionando.
        client.table("t_coletas").select("id").limit(1).execute()
        client._conn.close()
        r = (
            client.table("t_coletas")
            .insert([{"data": "2026-09-22", "turno": "Noite", "plataforma": "Z"}])
            .execute()
        )
        assert len(r.data) == 1


@_precisa_pg
class TestConexaoValidadaCedo:
    """`get_client("postgres")` tem que falhar na hora, não na 1ª consulta.

    `PostgresClient.__init__` só guarda o DSN — o psycopg2 conecta preguiçoso.
    Isso fazia o `try/except DBError` em volta de `get_client` parecer proteção
    sem ser: DSN malformado passava batido ali e estourava muitas linhas
    depois, onde o chamador já não sabia explicar o erro.
    """

    def test_dsn_bom_passa(self, client, monkeypatch):
        from utils.db import get_client

        monkeypatch.setenv("RAC_DB_DSN", _DSN)
        c = get_client("postgres")  # a validação roda aqui, sem levantar
        try:
            assert len(c.table("t_coletas").select("id").limit(1).execute().data) == 1
        finally:
            c.close()

    def test_verificar_conexao_e_idempotente(self, client):
        client.verificar_conexao()
        client.verificar_conexao()
        assert client.table("t_coletas").select("id", count="exact", head=True).execute().count == 4


@_precisa_pg
class TestNotProxyCompleto:
    def test_cobre_todos_os_operadores_de_ops(self, client):
        r = client.table("t_coletas").select("*").not_.gte("preco", 2000).execute()
        # NULL em preco não satisfaz NOT (preco >= 2000) — mesma semântica do
        # PostgREST, que também exclui a linha nula aqui.
        assert {x["plataforma"] for x in r.data} == {"Amazon", "Shopee"}

    def test_filtro_negado_desconhecido_falha_alto(self, client):
        from utils.db import UnsupportedFilterError

        with pytest.raises(UnsupportedFilterError, match="not_.match"):
            client.table("t_coletas").select("*").not_.match({"a": 1})

    def test_flag_desarma_quando_o_filtro_levanta(self, client):
        from utils.db import UnsupportedFilterError

        q = client.table("t_coletas").select("*")
        with pytest.raises(UnsupportedFilterError):
            q.not_.is_("preco", "talvez")  # valor inválido para IS
        # A query é reaproveitada — o filtro seguinte NÃO pode sair negado.
        r = q.eq("plataforma", "Amazon").execute()
        assert len(r.data) == 2
        assert all(x["plataforma"] == "Amazon" for x in r.data)
