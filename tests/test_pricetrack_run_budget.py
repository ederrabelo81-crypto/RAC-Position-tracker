"""
tests/test_pricetrack_run_budget.py — orçamento total da execução.

`poll_timeout_seconds` limita UM export, não o lote. Com
`max_concurrent_exports = 3`, um passo com N exports custa ceil(N/3) x
poll_timeout — e o `Heal recent gaps` varre até 14 datas. O risco concreto:
o primeiro lote esgota o poll, o manager submete um segundo, e o teto de
parede do runner mata o processo NO MEIO desse segundo lote. Export morto no
meio fica órfão segurando um dos 3 slots da organização, e é isso que faz o
import seguinte levar 429 — exatamente a falha que o PR quer conter.

`run_budget_seconds` faz o manager parar de SUBMETER o que não cabe, saindo
limpo e deixando os buracos para o run seguinte.

Rode: pytest tests/test_pricetrack_run_budget.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pricetrack_api.config import PriceTrackSettings  # noqa: E402
from pricetrack_api.exports import ExportManager  # noqa: E402


class _FakeClient:
    def __init__(self, settings):
        self.settings = settings


def _manager(budget, poll=100.0, agora=None):
    settings = PriceTrackSettings(
        api_key="x", poll_timeout_seconds=poll, run_budget_seconds=budget,
    )
    mgr = ExportManager(_FakeClient(settings), clock=agora or (lambda: 0.0))
    mgr._run_started = 0.0
    return mgr


class TestBudgetExhausted:
    def test_sem_orcamento_nunca_esgota(self):
        """Default 0 = comportamento anterior, sem limite."""
        mgr = _manager(budget=0.0, agora=lambda: 10_000.0)
        assert mgr._budget_exhausted() is False

    def test_tempo_de_sobra_permite_submeter(self):
        mgr = _manager(budget=1000.0, poll=100.0, agora=lambda: 0.0)
        assert mgr._budget_exhausted() is False

    def test_para_quando_nao_cabe_mais_um_export(self):
        """Restam 50s e um export pode levar 100s: não submete."""
        mgr = _manager(budget=1000.0, poll=100.0, agora=lambda: 950.0)
        assert mgr._budget_exhausted() is True

    def test_limite_exato_nao_submete(self):
        mgr = _manager(budget=1000.0, poll=100.0, agora=lambda: 900.5)
        assert mgr._budget_exhausted() is True

    def test_orcamento_estourado(self):
        mgr = _manager(budget=1000.0, poll=100.0, agora=lambda: 1200.0)
        assert mgr._budget_exhausted() is True


class TestSettingsDoAmbiente:
    def test_le_run_budget_do_env(self):
        s = PriceTrackSettings.from_env({
            "PRICETRACK_API_KEY": "x",
            "PRICETRACK_RUN_BUDGET_SECONDS": "3000",
        })
        assert s.run_budget_seconds == 3000.0

    def test_default_e_zero_sem_limite(self):
        s = PriceTrackSettings.from_env({"PRICETRACK_API_KEY": "x"})
        assert s.run_budget_seconds == 0.0

    def test_valor_invalido_e_rejeitado(self):
        from pricetrack_api.config import PriceTrackConfigError
        with pytest.raises(PriceTrackConfigError):
            PriceTrackSettings.from_env({
                "PRICETRACK_API_KEY": "x",
                "PRICETRACK_RUN_BUDGET_SECONDS": "abc",
            })
