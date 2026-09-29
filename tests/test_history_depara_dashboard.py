"""
tests/test_history_depara_dashboard.py — Histórico do Drive visível no painel.

Com a janela quente do Supabase em 2 dias, tudo o que é mais antigo só existe
em Parquet no Drive. Três falhas faziam esse histórico sumir sem erro:

1. Deploy sem `GDRIVE_*`: o store caía no disco vazio do container e a leitura
   devolvia vazio, indistinguível de "período sem dado".
2. Partições gravadas pela coleta não têm `sku_resolvido`/`estado_match` (quem
   preenche é o gatilho do banco) — o gráfico agrupado por SKU as descartava.
3. Conta de serviço cadastrada como tabela TOML virava repr do Python, não JSON.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402 — importável sem renderizar

pytest.importorskip("pyarrow", reason="histórico em Parquet exige pyarrow")

NOME = "Ar Condicionado Midea AI Ecomaster 12.000 BTUs Inverter Frio"


@pytest.fixture
def depara(monkeypatch):
    """De-para mínimo, no formato de `produtos_depara_nome`."""
    df = pd.DataFrame([{
        "nome_coletado": NOME, "estado": "MAPEADO",
        "familia": "MIDEA-ECOMASTER-12000-F", "sku": "42EZVCA12M5",
        "marca_norm": "MIDEA",
    }, {
        "nome_coletado": "Ventilador de Teto", "estado": "NAO_AC",
        "familia": None, "sku": None, "marca_norm": None,
    }])
    monkeypatch.setattr(app, "get_depara", lambda: df)
    return df


def _linha(produto: str, **extra) -> dict:
    base = {"data": date(2026, 9, 24), "plataforma": "Amazon", "marca": "Midea",
            "produto": produto, "preco": 2499.0, "run_id": "run-a"}
    base.update(extra)
    return base


class TestResolverDepara:
    def test_preenche_colunas_da_particao_crua(self, depara):
        out = app._resolver_depara_historico(pd.DataFrame([_linha(NOME)]))
        assert out.iloc[0]["sku_resolvido"] == "42EZVCA12M5"
        assert out.iloc[0]["familia_resolvida"] == "MIDEA-ECOMASTER-12000-F"
        assert out.iloc[0]["estado_match"] == "MAPEADO"

    def test_nome_desconhecido_fica_nulo(self, depara):
        out = app._resolver_depara_historico(pd.DataFrame([_linha("Split XPTO")]))
        assert pd.isna(out.iloc[0]["estado_match"])
        assert pd.isna(out.iloc[0]["sku_resolvido"])

    def test_nao_sobrescreve_linha_ja_classificada(self, depara):
        df = pd.DataFrame([_linha(NOME, estado_match="REVISAR",
                                  sku_resolvido=None, familia_resolvida=None)])
        out = app._resolver_depara_historico(df)
        assert out.iloc[0]["estado_match"] == "REVISAR"
        assert pd.isna(out.iloc[0]["sku_resolvido"])

    def test_sem_depara_disponivel_devolve_intacto(self, monkeypatch):
        monkeypatch.setattr(app, "get_depara", lambda: pd.DataFrame())
        df = pd.DataFrame([_linha(NOME)])
        assert "estado_match" not in app._resolver_depara_historico(df).columns


class TestFiltroPorLinha:
    """Mistura de linhas resolvidas e não resolvidas numa mesma leitura."""

    @pytest.fixture
    def mistura(self, depara) -> pd.DataFrame:
        return app._resolver_depara_historico(pd.DataFrame([
            _linha(NOME), _linha("Ventilador de Teto"), _linha("Split XPTO"),
        ]))

    def test_classificada_segue_filtro_estrito(self, mistura):
        out = app._filter_history_coletas(
            mistura, estados_match=["MAPEADO"], sem_depara=True
        )
        # NAO_AC sai; a desconhecida entra pelo interruptor.
        assert set(out["produto"]) == {NOME, "Split XPTO"}

    def test_estrito_so_deixa_a_mapeada(self, mistura):
        out = app._filter_history_coletas(
            mistura, estados_match=["MAPEADO"], sem_depara=False
        )
        assert list(out["produto"]) == [NOME]


class TestDedupParticoes:
    def test_tier_vence_a_particao_crua_da_mesma_run(self):
        df = pd.DataFrame([
            _linha(NOME, _particao="coletas/data=2026-09-24__run-run-a.parquet"),
            _linha(NOME, _particao="coletas/data=2026-09-24__run-tier0924.parquet",
                   estado_match="MAPEADO"),
        ])
        out = app._dedup_particoes_historico(df)
        assert len(out) == 1
        assert out.iloc[0]["estado_match"] == "MAPEADO"
        assert "_particao" not in out.columns

    def test_run_que_nao_chegou_ao_banco_sobrevive(self):
        df = pd.DataFrame([
            _linha(NOME, run_id="run-b",
                   _particao="coletas/data=2026-09-24__run-run-b.parquet"),
            _linha(NOME, _particao="coletas/data=2026-09-24__run-tier0924.parquet"),
        ])
        assert len(app._dedup_particoes_historico(df)) == 2

    def test_tier_sem_run_id_nao_apaga_run_que_nao_chegou_ao_banco(self):
        """Linha do tier sem run_id (histórico pré-feature) não cobre run nenhuma."""
        df = pd.DataFrame([
            _linha(NOME, run_id="run-b",
                   _particao="coletas/data=2026-09-24__run-run-b.parquet"),
            _linha(NOME, run_id=None,
                   _particao="coletas/data=2026-09-24__run-tier0924.parquet"),
        ])
        assert len(app._dedup_particoes_historico(df)) == 2

    def test_dia_sem_tier_nao_e_tocado(self):
        df = pd.DataFrame([
            _linha(NOME, _particao="coletas/data=2026-09-24__run-run-a.parquet"),
            _linha(NOME, run_id="run-c",
                   _particao="coletas/data=2026-09-24__run-run-c.parquet"),
        ])
        assert len(app._dedup_particoes_historico(df)) == 2


class TestGapFillResolvido:
    def test_particao_crua_chega_com_sku(self, tmp_path, monkeypatch, depara):
        """O gráfico agrupado por SKU precisa de `sku_resolvido` no frio."""
        from utils.history import HistoryStore, LocalBackend
        store = HistoryStore(LocalBackend(tmp_path / "h"))
        store.write_records(
            [{"data": "2026-09-24", "plataforma": "Amazon", "marca": "Midea",
              "produto": NOME, "preco": 2499.0}],
            dataset="coletas", run_id="abc12345",
        )
        monkeypatch.setattr("utils.history.get_store", lambda *a, **k: store)
        out = app._history_gap_fill(
            date(2026, 9, 1), date(2026, 9, 30), set(), estados_match=["MAPEADO"]
        )
        assert list(out["sku_resolvido"]) == ["42EZVCA12M5"]
        assert list(out["_origem"]) == ["historico"]


class TestFiltroPorSkuComVoltagemNula:
    """`voltagem_resolvida` é nula em toda a base: não pode zerar o filtro."""

    @pytest.fixture
    def catalogo(self, monkeypatch):
        cat = pd.DataFrame([{
            "sku": "42EZVCA12M5", "familia": "MIDEA-ECOMASTER-12000-F",
            "familia_linha": "MIDEA-ECOMASTER-12000-F", "voltagem": "220V",
            "capacidade_btu": 12000, "marca": "MIDEA",
        }])
        monkeypatch.setattr(app, "get_catalogo", lambda: cat)

    def test_linha_resolvida_sem_voltagem_passa(self, depara, catalogo):
        df = app._resolver_depara_historico(pd.DataFrame([_linha(NOME)]))
        out = app._filter_history_coletas(
            df, skus_resolvidos=["42EZVCA12M5"], estados_match=["MAPEADO"],
            sem_depara=False,
        )
        assert list(out["produto"]) == [NOME]

    def test_voltagem_conhecida_diferente_sai(self, depara, catalogo):
        df = app._resolver_depara_historico(
            pd.DataFrame([_linha(NOME, voltagem_resolvida="110V")])
        )
        out = app._filter_history_coletas(
            df, skus_resolvidos=["42EZVCA12M5"], estados_match=["MAPEADO"],
            sem_depara=False,
        )
        assert out.empty


class TestStatusDoHistorico:
    def test_disco_vazio_e_ausente(self, tmp_path):
        from utils.history import HistoryStore, LocalBackend
        store = HistoryStore(LocalBackend(tmp_path / "vazio"))
        assert app._historico_status(store) == "ausente"

    def test_disco_com_particao_e_local(self, tmp_path):
        from utils.history import HistoryStore, LocalBackend
        store = HistoryStore(LocalBackend(tmp_path / "h"))
        store.write_records([{"data": "2026-09-24", "produto": "x"}], dataset="coletas")
        assert app._historico_status(store) == "local"

    def test_drive_sem_credencial(self, monkeypatch):
        from utils.history import GoogleDriveBackend, HistoryStore, LocalBackend
        for k in ("GDRIVE_SERVICE_ACCOUNT_JSON", "GDRIVE_CLIENT_ID",
                  "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        store = HistoryStore(GoogleDriveBackend("pasta"), cache=LocalBackend(Path("/tmp")))
        assert app._historico_status(store) == "drive_sem_credencial"

    def test_drive_com_oauth(self, monkeypatch):
        pytest.importorskip("google.oauth2.credentials")
        from utils.history import GoogleDriveBackend, HistoryStore, LocalBackend
        monkeypatch.delenv("GDRIVE_SERVICE_ACCOUNT_JSON", raising=False)
        monkeypatch.setenv("GDRIVE_CLIENT_ID", "id")
        monkeypatch.setenv("GDRIVE_CLIENT_SECRET", "segredo")
        monkeypatch.setenv("GDRIVE_REFRESH_TOKEN", "token")
        store = HistoryStore(GoogleDriveBackend("pasta"), cache=LocalBackend(Path("/tmp")))
        assert app._historico_status(store) == "drive"

    def test_backend_remoto_e_drive(self):
        class _Remoto:
            backend = object()
        assert app._historico_status(_Remoto()) == "drive"


class TestSecretTabelaToml:
    def test_tabela_vira_json(self):
        tabela = {"type": "service_account", "private_key": "-----BEGIN\\n",
                  "client_email": "x@y.iam.gserviceaccount.com"}
        texto = app._secret_para_texto(tabela)
        assert json.loads(texto) == tabela

    def test_string_passa_intacta(self):
        assert app._secret_para_texto("  abc  ") == "abc"
