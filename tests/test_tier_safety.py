"""
tests/test_tier_safety.py — Guardas de segurança da poda automática (tier).

A poda noturna (`scripts/local_scheduled_collect.bat`, estágio D) roda
`history_cli.py tier --dataset all --confirm --heartbeat`. Duas coisas não
podem quebrar nesse caminho automatizado, e ambas foram apontadas em revisão
do PR que o introduziu:

* **P1 — perda de dado.** `--confirm` apaga do Supabase o que já foi verificado
  no histórico. Se o histórico cair em backend LOCAL (sem `GDRIVE_FOLDER_ID`),
  a verificação seria só no disco da máquina — que "some com o host". Apagar aí
  troca perda de cota por perda de dado. O tier tem que RECUSAR o delete nesse
  estado (e falhar, para o supervisor cobrar), preservando as linhas no banco.
* **P2 — alarme falso.** O job de manutenção não produz linha em `coletas`;
  seu `destino` não pode começar com um prefixo que `pipeline_watch` sonda como
  tabela de dado, senão uma noite sem nada a migrar vira SEM_DADO falso.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

pytest.importorskip("pyarrow", reason="histórico em Parquet exige pyarrow")

import scripts.history_cli as h  # noqa: E402
from utils.history import HistoryStore, LocalBackend  # noqa: E402
from utils.pipeline_registry import JOBS_POR_ID  # noqa: E402


def _row(dia: str = "2026-08-01") -> dict:
    return {
        "data": dia,
        "turno": "Abertura",
        "plataforma": "Mercado Livre",
        "marca": "Midea",
        "produto": "Ar Condicionado Midea 12000 BTU",
        "posicao_geral": 1,
        "preco": 1994.91,
    }


class _FakeExec:
    """Registra a operação chamada e nunca fala com rede."""

    def __init__(self, chamadas: list, kind: str):
        self._chamadas = chamadas
        self._kind = kind

    def eq(self, *a, **k):
        return self

    def execute(self):
        self._chamadas.append(self._kind)
        return mock.Mock(data=[])


class _FakeTable:
    def __init__(self, chamadas: list):
        self._chamadas = chamadas

    def delete(self):
        return _FakeExec(self._chamadas, "delete")


class _FakeClient:
    def __init__(self, chamadas: list):
        self._chamadas = chamadas

    def table(self, *a, **k):
        return _FakeTable(self._chamadas)


def _args(**over) -> argparse.Namespace:
    base = dict(dataset=h.DATASET_COLETAS, cutoff=None, confirm=True,
                dry_run=False, heartbeat=False)
    base.update(over)
    return argparse.Namespace(**base)


class TestP1BackendLocalNaoApaga:
    def test_confirm_com_backend_local_migra_mas_nao_apaga(self, tmp_path, monkeypatch):
        chamadas: list = []
        client = _FakeClient(chamadas)
        store = HistoryStore(LocalBackend(tmp_path / "history"))  # backend LOCAL
        spec = h._TIER_SPECS[h.DATASET_COLETAS]
        day = date(2026, 8, 1)

        monkeypatch.setattr(h, "get_store", lambda *a, **k: store)
        monkeypatch.setattr(h, "_distinct_days_before", lambda c, cut, sp: [day])
        monkeypatch.setattr(h, "_fetch_day", lambda c, d, sp: [_row()])

        rc = h._tier_one(client, spec, _args(confirm=True), date(2026, 8, 15))

        # NUNCA apagou do Supabase, mesmo com --confirm.
        assert "delete" not in chamadas
        # Falhou, para o agendador/pipeline_watch cobrarem o Drive ausente.
        assert rc == 1
        # Mas preservou a cópia no histórico (migrou para o disco).
        conferido = store.read(h.DATASET_COLETAS, start=day, end=day)
        assert len(conferido) == 1

    def test_sem_confirm_backend_local_e_ok(self, tmp_path, monkeypatch):
        """Sem --confirm não há delete a proteger: migra e sai limpo (rc=0)."""
        chamadas: list = []
        client = _FakeClient(chamadas)
        store = HistoryStore(LocalBackend(tmp_path / "history"))
        spec = h._TIER_SPECS[h.DATASET_COLETAS]
        day = date(2026, 8, 1)

        monkeypatch.setattr(h, "get_store", lambda *a, **k: store)
        monkeypatch.setattr(h, "_distinct_days_before", lambda c, cut, sp: [day])
        monkeypatch.setattr(h, "_fetch_day", lambda c, d, sp: [_row()])

        rc = h._tier_one(client, spec, _args(confirm=False), date(2026, 8, 15))
        assert "delete" not in chamadas
        assert rc == 0


class TestP2DestinoNaoSondavel:
    def test_destino_do_tier_nao_e_confundido_com_produtor_de_dado(self):
        """`pipeline_watch._linhas_do_job` sonda `coletas`/`pricetrack_daily`/
        `bestsellers` pelo prefixo do `destino`. O job de poda não produz linha
        nessas tabelas — o prefixo dele não pode casar, ou uma noite sem nada a
        migrar viraria SEM_DADO falso."""
        destino = JOBS_POR_ID["local_tier_migration"].destino
        for prefixo in ("coletas", "pricetrack_daily", "bestsellers"):
            assert not destino.startswith(prefixo), (
                f"destino '{destino}' começa com '{prefixo}' e seria sondado "
                "como tabela de dado por pipeline_watch"
            )
