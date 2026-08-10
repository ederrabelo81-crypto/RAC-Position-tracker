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
        # O poll vai junto: o default é 7200s (2h), e um orçamento menor que
        # ele é rejeitado de propósito (ver TestConfigProibeOrcamentoMenorQuePoll).
        s = PriceTrackSettings.from_env({
            "PRICETRACK_API_KEY": "x",
            "PRICETRACK_POLL_TIMEOUT_SECONDS": "2700",
            "PRICETRACK_RUN_BUDGET_SECONDS": "3000",
        })
        assert s.run_budget_seconds == 3000.0

    def test_env_com_orcamento_abaixo_do_poll_falha_alto(self):
        """Config errada no workflow morre na carga, não em silêncio."""
        from pricetrack_api.config import PriceTrackConfigError
        with pytest.raises(PriceTrackConfigError):
            PriceTrackSettings.from_env({
                "PRICETRACK_API_KEY": "x",
                "PRICETRACK_POLL_TIMEOUT_SECONDS": "2700",
                "PRICETRACK_RUN_BUDGET_SECONDS": "2400",
            })

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


class TestConfigProibeOrcamentoMenorQuePoll:
    """
    A armadilha que quase entrou em produção.

    `_budget_exhausted()` recusa submeter quando o tempo restante não comporta
    um export inteiro. Se o orçamento TOTAL já for menor que o timeout de UM
    export, isso é verdade na primeira iteração: o manager rejeita a fila
    inteira e o passo termina sem criar nenhum export — em silêncio, com o run
    verde. Foi exatamente o que o workflow diário teria feito com budget=2400 e
    poll=2700, e os testes não pegaram porque usavam valores de brinquedo em
    vez da configuração real. Agora a combinação é rejeitada na carga.
    """

    def test_orcamento_menor_que_poll_e_rejeitado(self):
        from pricetrack_api.config import PriceTrackConfigError
        with pytest.raises(PriceTrackConfigError, match="run_budget_seconds"):
            PriceTrackSettings(
                api_key="x", poll_timeout_seconds=2700, run_budget_seconds=2400,
            )

    def test_orcamento_igual_ao_poll_e_aceito(self):
        s = PriceTrackSettings(
            api_key="x", poll_timeout_seconds=2700, run_budget_seconds=2700,
        )
        assert s.run_budget_seconds == 2700

    def test_orcamento_zero_continua_significando_sem_limite(self):
        s = PriceTrackSettings(
            api_key="x", poll_timeout_seconds=2700, run_budget_seconds=0,
        )
        assert s.run_budget_seconds == 0

    @pytest.mark.parametrize("poll,budget", [(2400, 2700), (2700, 3000)])
    def test_valores_reais_do_workflow_submetem(self, poll, budget):
        """Os pares de fato usados em pricetrack_daily.yml (import e heal)."""
        mgr = _manager(budget=budget, poll=poll, agora=lambda: 0.0)
        assert mgr._budget_exhausted() is False, (
            "com estes valores o passo não submeteria nenhum export"
        )
