"""
tests/test_tier_seller_retention.py — Retenção própria dos fatos do seller_app.

Em 29/10/2026 o banco passou da cota do free tier (561 MB contra 500).
`seller_offer_daily` sozinha tinha 243 MB, 47 dias, sem poda nenhuma, e os
índices das tabelas podadas por DELETE estavam inchados (`pricetrack_daily`:
20 MB de heap, 111 MB de índice). O estágio D (`history_cli.py tier --dataset
all --confirm`) passou a:

* podar `seller_offer_daily`/`seller_coverage_daily` na retenção PRÓPRIA
  (`RAC_SELLER_RETENTION_DAYS`, 62 dias), nunca na janela quente de 2 dias de
  `coletas`;
* exportar → reler → conferir a contagem EXATA da partição → só então apagar;
* compactar os índices ao fim (RPC `compactar_indices`, migração 022).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

pytest.importorskip("pyarrow", reason="histórico em Parquet exige pyarrow")

import scripts.history_cli as h  # noqa: E402
from utils.history import (  # noqa: E402
    DEFAULT_SELLER_RETENTION_DAYS,
    MIN_SELLER_RETENTION_DAYS,
    HistoryStore,
    LocalBackend,
    seller_retention_days,
    seller_retention_start,
)
from utils.history.backends import HistoryBackend, HistoryBackendError  # noqa: E402


# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------
class _MemoriaBackend(HistoryBackend):
    """Backend remoto em memória — NÃO é `LocalBackend`, então a guarda P1
    (não apagar sem cópia no Drive) deixa o delete passar, como no PC coletor."""

    def __init__(self) -> None:
        self.blobs: Dict[str, bytes] = {}

    def put(self, key: str, payload: bytes) -> None:
        self.blobs[key] = payload

    def get(self, key: str) -> bytes:
        try:
            return self.blobs[key]
        except KeyError as exc:
            raise HistoryBackendError(key) from exc

    def list(self, dataset: str) -> List[str]:
        return sorted(k for k in self.blobs if k.startswith(f"{dataset}/"))

    def delete(self, key: str) -> None:
        self.blobs.pop(key, None)


class _Query:
    def __init__(self, cliente: "_Cliente", tabela: str):
        self._c = cliente
        self._tabela = tabela
        self._acao = "select"

    def select(self, coluna, **kw):
        self._c.chamadas.append(("select", self._tabela, coluna))
        return self

    def eq(self, coluna, valor):
        self._c.chamadas.append(("eq", self._tabela, coluna, valor))
        return self

    def order(self, coluna, desc=False):
        self._c.chamadas.append(("order", self._tabela, coluna))
        return self

    def range(self, *a):
        return self

    def limit(self, *a):
        return self

    def delete(self):
        self._acao = "delete"
        return self

    def execute(self):
        if self._acao == "delete":
            self._c.chamadas.append(("delete", self._tabela))
        return type("R", (), {"data": [], "count": 0})()


class _Rpc:
    def __init__(self, cliente: "_Cliente", fn: str, params: dict):
        self._c, self._fn, self._params = cliente, fn, params

    def execute(self):
        self._c.chamadas.append(("rpc", self._fn, self._params.get("p_tabela")))
        if self._c.rpc_falha:
            raise RuntimeError("function compactar_indices does not exist")
        return type("R", (), {"data": {
            "tabela": self._params.get("p_tabela"), "reindexada": False,
            "heap": "1 MB", "indices_antes": "1 MB", "indices_depois": "1 MB",
            "banco": "336 MB",
        }})()


class _Cliente:
    def __init__(self, rpc_falha: bool = False):
        self.chamadas: list = []
        self.rpc_falha = rpc_falha

    def table(self, nome: str):
        return _Query(self, nome)

    def rpc(self, fn: str, params: dict):
        return _Rpc(self, fn, params)


def _linha_seller(dia: str, n: int) -> dict:
    return {
        "data": dia, "turno": "Abertura", "plataforma": "Mercado Livre",
        "seller_canonical": f"Seller {n}", "offer_key": f"v1|ml|MLB{n}",
        "superficie": "marketplace", "detentor_buybox": n % 2 == 0,
        "posicao_melhor": n, "preco": 1999.9, "identidade_suspeita": False,
    }


def _args(**over) -> argparse.Namespace:
    base = dict(dataset="all", cutoff=None, confirm=True, dry_run=False, heartbeat=False)
    base.update(over)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# Retenção configurável
# ---------------------------------------------------------------------------
class TestRetencaoSeller:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("RAC_SELLER_RETENTION_DAYS", raising=False)
        assert seller_retention_days() == DEFAULT_SELLER_RETENTION_DAYS == 62

    def test_env(self, monkeypatch):
        monkeypatch.setenv("RAC_SELLER_RETENTION_DAYS", "90")
        assert seller_retention_days() == 90

    def test_valor_da_janela_quente_por_engano_vai_ao_piso(self, monkeypatch):
        """`2` é o valor de RAC_HOT_WINDOW_DAYS; aqui cortaria o painel a 2 dias."""
        monkeypatch.setenv("RAC_SELLER_RETENTION_DAYS", "2")
        assert seller_retention_days() == MIN_SELLER_RETENTION_DAYS

    @pytest.mark.parametrize("raw", ["", "abc", "0", "-5"])
    def test_invalido_cai_no_default(self, monkeypatch, raw):
        monkeypatch.setenv("RAC_SELLER_RETENTION_DAYS", raw)
        assert seller_retention_days() == DEFAULT_SELLER_RETENTION_DAYS

    def test_inicio_inclui_hoje(self, monkeypatch):
        monkeypatch.delenv("RAC_SELLER_RETENTION_DAYS", raising=False)
        assert seller_retention_start(date(2026, 10, 29)) == date(2026, 8, 29)

    def test_retencao_cobre_o_slider_inteiro_do_seller_app(self, monkeypatch):
        """O slider vai até N dias ancorado no último dia com dado
        (`desde = max_data - dias`), e a coleta costuma estar um dia atrás:
        a janela cheia pede hoje − (N + 1). Se alguém subir o teto do slider
        sem subir a retenção, a metade de cima do slider volta a ser inerte."""
        monkeypatch.delenv("RAC_SELLER_RETENTION_DAYS", raising=False)
        fonte = (_ROOT / "seller_app" / "app.py").read_text(encoding="utf-8")
        teto = re.search(r"return max\(4, min\((\d+), span\)\)", fonte)
        assert teto, "_dias_max mudou de forma — atualize este teste"
        slider_max = int(teto.group(1))
        hoje = date(2026, 10, 29)
        desde_mais_antigo = (hoje - timedelta(days=1)) - timedelta(days=slider_max)
        assert seller_retention_start(hoje) <= desde_mais_antigo

    def test_piso_cobre_a_janela_padrao(self):
        fonte = (_ROOT / "seller_app" / "app.py").read_text(encoding="utf-8")
        padrao = re.search(r'st\.sidebar\.slider\("Janela \(dias\)", 3, dias_max, min\((\d+), dias_max\)\)', fonte)
        assert padrao, "slider da janela mudou de forma — atualize este teste"
        assert MIN_SELLER_RETENTION_DAYS >= int(padrao.group(1)) + 2


# ---------------------------------------------------------------------------
# Especificação dos datasets
# ---------------------------------------------------------------------------
class TestSpecs:
    def test_all_inclui_os_fatos_do_seller(self):
        nomes = [s.table for s in h._tier_datasets(_args(dataset="all"))]
        assert nomes == ["coletas", "pricetrack_daily",
                         "seller_offer_daily", "seller_coverage_daily"]

    def test_grupo_seller(self):
        nomes = [s.table for s in h._tier_datasets(_args(dataset="seller"))]
        assert nomes == ["seller_offer_daily", "seller_coverage_daily"]

    def test_parser_aceita_os_novos_datasets(self):
        for ds in ("seller", "seller_offer", "seller_coverage"):
            args = h.build_parser().parse_args(["tier", "--dataset", ds])
            assert args.dataset == ds

    def test_cutoff_manual_nao_corta_o_seller(self, monkeypatch):
        """`--cutoff` pensado para coletas não pode podar o painel do lojista."""
        monkeypatch.delenv("RAC_SELLER_RETENTION_DAYS", raising=False)
        args = _args(cutoff=date.today())
        seller = h._TIER_SPECS[h.DATASET_SELLER_OFFER]
        coletas = h._TIER_SPECS[h.DATASET_COLETAS]
        assert h._spec_cutoff(seller, args) == seller_retention_start()
        assert h._spec_cutoff(coletas, args) == date.today()

    def test_coletas_e_pricetrack_seguem_na_janela_quente(self):
        for ds in (h.DATASET_COLETAS, h.DATASET_PRICETRACK):
            assert h._TIER_SPECS[ds].retencao_propria is False

    def test_paginacao_do_seller_usa_a_chave_primaria(self):
        """Sem `id`, `order('id')` daria erro no PostgREST; sem ordem total a
        paginação pode repetir/pular linha e a contagem perde o sentido."""
        cli = _Cliente()
        h._fetch_day(cli, date(2026, 8, 28), h._TIER_SPECS[h.DATASET_SELLER_OFFER])
        ordens = [c[2] for c in cli.chamadas if c[0] == "order"]
        assert ordens == ["turno", "plataforma", "offer_key", "seller_canonical"]

    def test_contagem_por_dia_nao_pede_id(self):
        cli = _Cliente()
        h._distinct_days_before(cli, date(2026, 9, 1), h._TIER_SPECS[h.DATASET_SELLER_COVERAGE])
        assert ("select", "seller_coverage_daily", "id") not in cli.chamadas

    def test_lista_de_compactacao_bate_com_a_lista_branca_da_migracao(self):
        sql = (_ROOT / "docs" / "migrations" / "022_compactar_indices.sql").read_text(encoding="utf-8")
        branca = re.search(r"NOT IN \(([^)]*)\)", sql)
        assert branca
        nomes = set(re.findall(r"'([a-z_]+)'", branca.group(1)))
        assert nomes == set(h._TABELAS_COMPACTAR)
        assert {s.table for s in h._TIER_SPECS.values()} <= nomes


# ---------------------------------------------------------------------------
# exporta → confere → apaga
# ---------------------------------------------------------------------------
class TestExportaConfereApaga:
    DIA = date(2026, 8, 28)

    @pytest.fixture(autouse=True)
    def _cache(self, tmp_path):
        # Como o store do Drive no PC coletor: backend remoto + cache em disco.
        self.cache = LocalBackend(tmp_path / "cache")

    def _prepara(self, monkeypatch, linhas):
        store = HistoryStore(_MemoriaBackend(), cache=self.cache)
        monkeypatch.setattr(h, "get_store", lambda *a, **k: store)
        monkeypatch.setattr(h, "_distinct_days_before", lambda c, cut, sp: [self.DIA])
        monkeypatch.setattr(h, "_fetch_day", lambda c, d, sp: list(linhas))
        return store

    def test_dia_conferido_vai_ao_parquet_e_sai_do_banco(self, monkeypatch):
        linhas = [_linha_seller("2026-08-28", n) for n in range(5)]
        store = self._prepara(monkeypatch, linhas)
        cli = _Cliente()
        spec = h._TIER_SPECS[h.DATASET_SELLER_OFFER]

        rc = h._tier_one(cli, spec, _args(), date(2026, 8, 29))

        assert rc == 0
        assert ("delete", "seller_offer_daily") in cli.chamadas
        relido = store.read("seller_offer", start=self.DIA, end=self.DIA)
        assert len(relido) == 5
        assert set(relido["seller_canonical"]) == {f"Seller {n}" for n in range(5)}

    def test_escrita_truncada_nao_apaga(self, monkeypatch):
        linhas = [_linha_seller("2026-08-28", n) for n in range(5)]
        store = self._prepara(monkeypatch, linhas)
        original = store.write_day

        def _trunca(dataset, day, rows, run_id=None):
            return original(dataset, day, list(rows)[:-1], run_id=run_id)

        monkeypatch.setattr(store, "write_day", _trunca)
        cli = _Cliente()
        rc = h._tier_one(cli, h._TIER_SPECS[h.DATASET_SELLER_OFFER], _args(), date(2026, 8, 29))

        assert rc == 1
        assert ("delete", "seller_offer_daily") not in cli.chamadas

    def test_particao_antiga_nao_tapa_escrita_truncada(self, monkeypatch):
        """Antes a conferência contava o DIA inteiro (`>=`): uma partição de
        uma passada anterior somava e escondia a escrita que veio curta."""
        linhas = [_linha_seller("2026-08-28", n) for n in range(5)]
        store = self._prepara(monkeypatch, linhas)
        store.write_day("seller_offer", self.DIA, linhas, run_id="antiga01")
        original = store.write_day

        def _trunca(dataset, day, rows, run_id=None):
            return original(dataset, day, list(rows)[:2], run_id=run_id)

        monkeypatch.setattr(store, "write_day", _trunca)
        cli = _Cliente()
        rc = h._tier_one(cli, h._TIER_SPECS[h.DATASET_SELLER_OFFER], _args(), date(2026, 8, 29))

        assert rc == 1
        assert ("delete", "seller_offer_daily") not in cli.chamadas

    def test_dry_run_nao_grava_nem_apaga(self, monkeypatch):
        linhas = [_linha_seller("2026-08-28", n) for n in range(3)]
        store = self._prepara(monkeypatch, linhas)
        cli = _Cliente()
        rc = h._tier_one(cli, h._TIER_SPECS[h.DATASET_SELLER_OFFER],
                         _args(dry_run=True), date(2026, 8, 29))
        assert rc == 0
        assert ("delete", "seller_offer_daily") not in cli.chamadas
        assert store.days("seller_offer") == []


# ---------------------------------------------------------------------------
# Compactação ao fim da passada
# ---------------------------------------------------------------------------
class TestCompactacao:
    def _roda(self, monkeypatch, cli, rc_dataset=0, **over):
        monkeypatch.setattr("utils.supabase_client._get_client", lambda: cli)
        monkeypatch.setattr(h, "_tier_one", lambda c, sp, a, cut: rc_dataset)
        return h._cmd_tier_run(_args(**over))

    def test_confirm_compacta_uma_tabela_por_chamada(self, monkeypatch):
        cli = _Cliente()
        assert self._roda(monkeypatch, cli) == 0
        rpcs = [c[2] for c in cli.chamadas if c[0] == "rpc"]
        assert rpcs == list(h._TABELAS_COMPACTAR)

    def test_sem_confirm_nao_compacta(self, monkeypatch):
        cli = _Cliente()
        self._roda(monkeypatch, cli, confirm=False)
        assert not [c for c in cli.chamadas if c[0] == "rpc"]

    def test_dry_run_nao_compacta(self, monkeypatch):
        cli = _Cliente()
        self._roda(monkeypatch, cli, dry_run=True)
        assert not [c for c in cli.chamadas if c[0] == "rpc"]

    def test_banco_bloqueado_nao_compacta(self, monkeypatch):
        cli = _Cliente()
        assert self._roda(monkeypatch, cli, rc_dataset=2) == 2
        assert not [c for c in cli.chamadas if c[0] == "rpc"]

    def test_rpc_ausente_falha_a_passada(self, monkeypatch):
        """Sem a compactação o espaço do DELETE fica preso no índice e a cota
        volta a estourar — a batida tem de sair FAILED para o supervisor cobrar."""
        cli = _Cliente(rpc_falha=True)
        assert self._roda(monkeypatch, cli) == 1
