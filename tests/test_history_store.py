"""
tests/test_history_store.py — Histórico frio em Parquet (utils/history).

Cobre o que quebraria em silêncio na produção: nome de partição, ida e volta
dos tipos pelo Parquet, particionamento por dia, união frio+quente com
precedência, e a degradação quando o Supabase está restrito por cota.

O backend do Drive é exercitado com um duplo em memória — a suíte não fala com
a rede.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.history import (  # noqa: E402
    DATASET_COLETAS,
    HistoryBackend,
    HistoryBackendError,
    HistoryStore,
    LocalBackend,
    hot_window_start,
    parse_key,
    partition_key,
    read_coletas,
    write_records,
)

pytest.importorskip("pyarrow", reason="histórico em Parquet exige pyarrow")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path) -> HistoryStore:
    """Store apontando para um diretório temporário."""
    return HistoryStore(LocalBackend(tmp_path / "history"))


def _row(dia: str, produto: str = "Ar Condicionado Midea 12000", **extra) -> dict:
    """Linha no schema do banco, com os campos que o dashboard usa."""
    base = {
        "data": dia,
        "turno": "Abertura",
        "plataforma": "Mercado Livre",
        "marca": "Midea",
        "produto": produto,
        "posicao_geral": 1,
        "preco": 1994.91,
        "qtd_sellers": 3,
        "patrocinado": False,
        "seller": "Loja Oficial Midea",
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Nomes de partição
# ---------------------------------------------------------------------------
class TestPartitionKey:
    def test_formato(self):
        key = partition_key("coletas", date(2026, 7, 25), "36abc8e6-ea19-4d58")
        assert key == "coletas/data=2026-07-25__run-36abc8e6.parquet"

    def test_sem_run_id_vira_manual(self):
        key = partition_key("coletas", date(2026, 7, 25))
        assert key.endswith("__run-manual.parquet")

    def test_run_id_sanitizado(self):
        """Barra no run_id criaria uma subpasta fantasma no Drive."""
        key = partition_key("coletas", date(2026, 7, 25), "a/b c")
        assert parse_key(key) is not None
        assert "/" not in key.split("/", 1)[1]

    def test_roundtrip(self):
        key = partition_key("coletas", date(2026, 7, 25), "run1")
        parsed = parse_key(key)
        assert parsed["dataset"] == "coletas"
        assert parsed["day"] == date(2026, 7, 25)

    def test_chave_estranha_nao_parseia(self):
        assert parse_key("coletas/lixo.parquet") is None
        assert parse_key("coletas/data=2026-13-99__run-x.parquet") is None


# ---------------------------------------------------------------------------
# Escrita e leitura
# ---------------------------------------------------------------------------
class TestWriteRead:
    def test_ida_e_volta(self, store):
        store.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")])
        df = store.read(DATASET_COLETAS)
        assert len(df) == 1
        assert df.iloc[0]["produto"] == "Ar Condicionado Midea 12000"
        assert df.iloc[0]["data"] == date(2026, 7, 25)

    def test_tipos_sobrevivem_ao_parquet(self, store):
        store.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")])
        row = store.read(DATASET_COLETAS).iloc[0]
        assert row["preco"] == pytest.approx(1994.91)
        assert int(row["qtd_sellers"]) == 3
        assert bool(row["patrocinado"]) is False

    def test_particiona_por_dia(self, store):
        store.write_records(
            [_row("2026-07-24"), _row("2026-07-25"), _row("2026-07-25")],
            dataset=DATASET_COLETAS,
        )
        assert store.days(DATASET_COLETAS) == [date(2026, 7, 24), date(2026, 7, 25)]
        assert len(store.read(DATASET_COLETAS)) == 3

    def test_intervalo_recorta(self, store):
        for dia in ("2026-07-23", "2026-07-24", "2026-07-25"):
            store.write_records([_row(dia)], dataset=DATASET_COLETAS)
        df = store.read(DATASET_COLETAS, start=date(2026, 7, 24), end=date(2026, 7, 24))
        assert len(df) == 1
        assert df.iloc[0]["data"] == date(2026, 7, 24)

    def test_vazio_nao_grava(self, store):
        assert store.write_day(DATASET_COLETAS, date(2026, 7, 25), []) is None
        assert store.days(DATASET_COLETAS) == []

    def test_linha_sem_data_e_ignorada(self, store):
        """Uma linha corrompida não pode derrubar a gravação do dia inteiro."""
        keys = store.write_records(
            [_row("2026-07-25"), {"produto": "sem data"}], dataset=DATASET_COLETAS
        )
        assert len(keys) == 1
        assert len(store.read(DATASET_COLETAS)) == 1

    def test_colunas_extras_passam(self, store):
        """Colunas que o banco ganhou depois (de-para) não exigem migração."""
        store.write_day(
            DATASET_COLETAS, date(2026, 7, 25),
            [_row("2026-07-25", estado_match="MAPEADO", sku_resolvido="42EZVCA09M5")],
        )
        df = store.read(DATASET_COLETAS)
        assert df.iloc[0]["estado_match"] == "MAPEADO"
        assert df.iloc[0]["sku_resolvido"] == "42EZVCA09M5"

    def test_runs_diferentes_coexistem_no_mesmo_dia(self, store):
        """Duas coletas no mesmo dia (Abertura/Fechamento) são 2 partições."""
        store.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")], run_id="manha")
        store.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")], run_id="noite")
        assert len(store.backend.list(DATASET_COLETAS)) == 2
        assert len(store.read(DATASET_COLETAS)) == 2

    def test_regravar_mesma_run_substitui(self, store):
        """Reprocessar a mesma run não pode duplicar linhas."""
        for _ in range(2):
            store.write_day(
                DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")], run_id="r1"
            )
        assert len(store.read(DATASET_COLETAS)) == 1

    def test_drop_day(self, store):
        store.write_records([_row("2026-07-24"), _row("2026-07-25")], dataset=DATASET_COLETAS)
        assert store.drop_day(DATASET_COLETAS, date(2026, 7, 24)) == 1
        assert store.days(DATASET_COLETAS) == [date(2026, 7, 25)]

    def test_read_sem_particao_devolve_vazio(self, store):
        assert store.read(DATASET_COLETAS).empty


class TestWriteRecordsMapeamento:
    def test_mapeia_do_formato_interno(self, store):
        """`write_records` aplica o mesmo mapeamento do upload ao Supabase."""
        interno = {
            "Data": "2026-07-25",
            "Plataforma": "Mercado Livre",
            "Produto / SKU": "Ar Condicionado Midea 12000 BTUs",
            "Marca Monitorada": "Midea",
            "Preço (R$)": "1994.91",
            "Posição Geral": "1",
        }
        keys = write_records([interno], run_id="abc12345", store=store)
        assert len(keys) == 1
        df = store.read(DATASET_COLETAS)
        assert df.iloc[0]["plataforma"] == "Mercado Livre"
        assert df.iloc[0]["preco"] == pytest.approx(1994.91)
        assert df.iloc[0]["run_id"] == "abc12345"

    def test_lista_vazia(self, store):
        assert write_records([], store=store) == []


# ---------------------------------------------------------------------------
# União frio + quente
# ---------------------------------------------------------------------------
class _FakeQuery:
    """Duplo do query-builder do PostgREST, o bastante para `_read_hot`."""

    def __init__(self, rows, raise_exc=None):
        self._rows = rows
        self._raise = raise_exc

    def select(self, *_a, **_k): return self
    def gte(self, *_a, **_k): return self
    def lte(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self

    def range(self, start, end):
        self._slice = (start, end)
        return self

    def execute(self):
        if self._raise:
            raise self._raise
        start, end = getattr(self, "_slice", (0, len(self._rows)))
        return type("Resp", (), {"data": self._rows[start:end + 1]})()


class _FakeClient:
    def __init__(self, rows, raise_exc=None):
        self._rows = rows
        self._raise = raise_exc

    def table(self, _name):
        return _FakeQuery(self._rows, self._raise)


class TestReadColetasUniao:
    def test_so_frio_quando_nao_ha_cliente(self, store):
        store.write_records([_row("2026-01-15")], dataset=DATASET_COLETAS)
        df = read_coletas(date(2026, 1, 1), date(2026, 1, 31), store=store)
        assert len(df) == 1

    def test_costura_frio_e_quente(self, store):
        hoje = date.today()
        antigo = hoje - timedelta(days=90)
        store.write_records([_row(antigo.isoformat())], dataset=DATASET_COLETAS)
        quente = [_row(hoje.isoformat(), produto="Do Supabase")]

        df = read_coletas(
            antigo, hoje, store=store, supabase_client=_FakeClient(quente)
        )
        assert len(df) == 2
        assert set(df["produto"]) == {"Ar Condicionado Midea 12000", "Do Supabase"}

    def test_quente_tem_precedencia_no_dia_duplicado(self, store):
        """Dia presente nos dois lados (durante a migração) não duplica."""
        hoje = date.today()
        store.write_records(
            [_row(hoje.isoformat(), produto="Versao Fria")], dataset=DATASET_COLETAS
        )
        quente = [_row(hoje.isoformat(), produto="Versao Quente")]

        df = read_coletas(hoje, hoje, store=store, supabase_client=_FakeClient(quente))
        assert len(df) == 1
        assert df.iloc[0]["produto"] == "Versao Quente"

    def test_cota_restrita_degrada_para_o_frio(self, store):
        """HTTP 402 na API: o relatório sai só com o histórico, sem levantar."""
        hoje = date.today()
        store.write_records([_row(hoje.isoformat())], dataset=DATASET_COLETAS)
        cliente = _FakeClient([], raise_exc=Exception("exceed_db_size_quota"))

        df = read_coletas(hoje, hoje, store=store, supabase_client=cliente)
        assert len(df) == 1

    def test_include_hot_false_ignora_o_banco(self, store):
        hoje = date.today()
        store.write_records([_row(hoje.isoformat())], dataset=DATASET_COLETAS)
        quente = [_row(hoje.isoformat(), produto="Do Supabase")]

        df = read_coletas(
            hoje, hoje, store=store,
            supabase_client=_FakeClient(quente), include_hot=False,
        )
        assert df.iloc[0]["produto"] == "Ar Condicionado Midea 12000"

    def test_intervalo_invertido_e_corrigido(self, store):
        store.write_records([_row("2026-07-25")], dataset=DATASET_COLETAS)
        df = read_coletas(date(2026, 7, 26), date(2026, 7, 24), store=store)
        assert len(df) == 1

    def test_sem_dado_nenhum(self, store):
        assert read_coletas(date(2026, 1, 1), date(2026, 1, 2), store=store).empty

    def test_projecao_sem_data_ainda_desduplica(self, store):
        """`columns` sem "data" não pode desligar a precedência do quente.

        Sem a coluna a desduplicação seria pulada em silêncio e o dia
        sobreposto voltaria em dobro.
        """
        hoje = date.today()
        store.write_records(
            [_row(hoje.isoformat(), produto="Versao Fria")], dataset=DATASET_COLETAS
        )
        quente = [_row(hoje.isoformat(), produto="Versao Quente")]

        df = read_coletas(
            hoje, hoje, store=store, supabase_client=_FakeClient(quente),
            columns=["produto", "plataforma"],
        )
        assert len(df) == 1
        assert df.iloc[0]["produto"] == "Versao Quente"
        # `data` foi acrescentada só para desduplicar — não volta ao chamador.
        assert "data" not in df.columns

    def test_projecao_com_data_mantem_a_coluna(self, store):
        hoje = date.today()
        store.write_records([_row(hoje.isoformat())], dataset=DATASET_COLETAS)
        df = read_coletas(hoje, hoje, store=store, columns=["data", "produto"])
        assert "data" in df.columns


class TestJanelaQuente:
    def test_inclui_hoje_e_os_anteriores(self, monkeypatch):
        monkeypatch.setenv("RAC_HOT_WINDOW_DAYS", "15")
        assert hot_window_start(date(2026, 7, 25)) == date(2026, 7, 11)

    def test_env_sobrepoe(self, monkeypatch):
        monkeypatch.setenv("RAC_HOT_WINDOW_DAYS", "7")
        assert hot_window_start(date(2026, 7, 25)) == date(2026, 7, 19)

    def test_env_invalida_cai_no_default(self, monkeypatch):
        monkeypatch.setenv("RAC_HOT_WINDOW_DAYS", "zero")
        assert hot_window_start(date(2026, 7, 25)) == date(2026, 7, 11)


# ---------------------------------------------------------------------------
# Backend do Drive (duplo em memória — sem rede)
# ---------------------------------------------------------------------------
class _FakeDriveBackend(HistoryBackend):
    """Backend "remoto" que delega ao disco, contando os downloads.

    Delega em vez de herdar de `LocalBackend` de propósito: o store trata
    `LocalBackend` como caso especial (lê o arquivo direto, sem cache), então
    herdar faria o duplo pular justamente o caminho sob teste.
    """

    def __init__(self, root):
        self._inner = LocalBackend(root)
        self.gets = 0

    def put(self, key: str, payload: bytes) -> None:
        self._inner.put(key, payload)

    def get(self, key: str) -> bytes:
        self.gets += 1
        return self._inner.get(key)

    def list(self, dataset: str):
        return self._inner.list(dataset)

    def delete(self, key: str) -> None:
        self._inner.delete(key)

    def fingerprint(self, key: str):
        # O GoogleDriveBackend real expõe o md5Checksum que a Drive API
        # devolve na listagem; aqui o disco faz o mesmo papel.
        return self._inner.fingerprint(key)


class TestCacheDeBackendRemoto:
    def test_particao_baixada_uma_vez_so(self, tmp_path):
        remoto = _FakeDriveBackend(tmp_path / "remoto")
        cache = LocalBackend(tmp_path / "cache")
        store = HistoryStore(remoto, cache=cache)
        store.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")])

        # A escrita já povoa o cache; leituras seguintes não voltam ao remoto.
        store.read(DATASET_COLETAS)
        store.read(DATASET_COLETAS)
        assert remoto.gets == 0

    def test_cache_frio_busca_no_remoto(self, tmp_path):
        remoto = _FakeDriveBackend(tmp_path / "remoto")
        store_escrita = HistoryStore(remoto, cache=LocalBackend(tmp_path / "c1"))
        store_escrita.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")])

        # Outra máquina: cache vazio, precisa baixar.
        store_leitura = HistoryStore(remoto, cache=LocalBackend(tmp_path / "c2"))
        assert len(store_leitura.read(DATASET_COLETAS)) == 1
        assert remoto.gets == 1

    def test_cache_obsoleto_e_rebaixado(self, tmp_path):
        """Reprocessar uma run sobrescreve o remoto — o cache de OUTRO host
        não pode continuar servindo a versão velha para sempre."""
        remoto = _FakeDriveBackend(tmp_path / "remoto")
        escritor = HistoryStore(remoto, cache=LocalBackend(tmp_path / "c1"))
        leitor = HistoryStore(remoto, cache=LocalBackend(tmp_path / "c2"))

        escritor.write_day(
            DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")], run_id="r1"
        )
        assert len(leitor.read(DATASET_COLETAS)) == 1
        assert remoto.gets == 1

        # Mesma run, agora com 3 linhas: o conteúdo remoto muda.
        escritor.write_day(
            DATASET_COLETAS, date(2026, 7, 25),
            [_row("2026-07-25") for _ in range(3)], run_id="r1",
        )
        assert len(leitor.read(DATASET_COLETAS)) == 3
        assert remoto.gets == 2

    def test_cache_valido_nao_rebaixa(self, tmp_path):
        """Sem mudança no remoto, nenhuma leitura extra."""
        remoto = _FakeDriveBackend(tmp_path / "remoto")
        escritor = HistoryStore(remoto, cache=LocalBackend(tmp_path / "c1"))
        leitor = HistoryStore(remoto, cache=LocalBackend(tmp_path / "c2"))
        escritor.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")])

        leitor.read(DATASET_COLETAS)
        leitor.read(DATASET_COLETAS)
        assert remoto.gets == 1


class TestSupersessaoDeParticoes:
    """A migração substitui as partições que a coleta gravou no mesmo dia."""

    def test_drop_day_preserva_a_excecao(self, store):
        store.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")],
                        run_id="coleta")
        chave_tier = store.write_day(
            DATASET_COLETAS, date(2026, 7, 25),
            [_row("2026-07-25"), _row("2026-07-25")], run_id="tier0725",
        )
        assert len(store.read(DATASET_COLETAS)) == 3  # dia duplicado

        removidas = store.drop_day(DATASET_COLETAS, date(2026, 7, 25), exceto=chave_tier)
        assert removidas == 1
        assert len(store.read(DATASET_COLETAS)) == 2  # só a versão resolvida

    def test_drop_day_sem_excecao_limpa_tudo(self, store):
        store.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")],
                        run_id="a")
        store.write_day(DATASET_COLETAS, date(2026, 7, 25), [_row("2026-07-25")],
                        run_id="b")
        assert store.drop_day(DATASET_COLETAS, date(2026, 7, 25)) == 2
        assert store.read(DATASET_COLETAS).empty


class TestFallbackLocalQuandoODriveFalha:
    """Falha de credencial/API do Drive só aparece na primeira escrita real —
    depois da construção do store. Sem rede de segurança, o dia se perderia
    exatamente no cenário que o histórico existe para cobrir."""

    def test_fallback_grava_em_disco(self, tmp_path, monkeypatch):
        class _DriveQuebrado(HistoryBackend):
            def put(self, key, payload):
                raise HistoryBackendError("credencial inválida")

            def get(self, key):
                raise HistoryBackendError("credencial inválida")

            def list(self, dataset):
                return []

            def delete(self, key):
                pass

            @property
            def describe(self):
                return "drive:quebrado"

        monkeypatch.setenv("RAC_HISTORY_DIR", str(tmp_path / "hist"))
        quebrado = HistoryStore(_DriveQuebrado(), cache=LocalBackend(tmp_path / "c"))

        # `store=None` para que o fallback possa resolver o store local.
        import utils.history as hist
        monkeypatch.setattr(
            hist, "get_store",
            lambda name=None: HistoryStore(LocalBackend(tmp_path / "hist"))
            if name == "local" else quebrado,
        )

        keys = hist.write_records([_row("2026-07-25")], run_id="r1", already_mapped=True)
        assert keys, "o fallback local deveria ter gravado a partição"
        local = HistoryStore(LocalBackend(tmp_path / "hist"))
        assert len(local.read(DATASET_COLETAS)) == 1


class TestFiltroACNaEscrita:
    """O histórico aplica o mesmo corte do upload — senão os dois lados da
    união divergem em conteúdo."""

    def test_descarta_nao_ac(self, store):
        registros = [
            {"Data": "2026-07-25", "Plataforma": "Mercado Livre",
             "Produto / SKU": "Ar Condicionado Split Midea 12000 BTUs"},
            {"Data": "2026-07-25", "Plataforma": "Mercado Livre",
             "Produto / SKU": "Cadeira Gamer ThunderX3"},
        ]
        write_records(registros, run_id="r1", store=store)
        df = store.read(DATASET_COLETAS)
        assert len(df) == 1
        assert "Midea" in df.iloc[0]["produto"]
