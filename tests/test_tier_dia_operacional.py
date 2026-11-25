"""Poda da janela quente ancorada no dia do turno, não no relógio.

Incidente de 24/11/2026: o turno das 20:00 terminou às 00:41 e a poda
(estágio D, `history_cli.py tier`) rodou às 00:43. Com `date.today()` já no
dia seguinte, o corte avançou um dia: `coletas` perdeu o D-2 e
`pricetrack_daily` — cujo D-1 só chega de manhã — ficou VAZIA. O briefing das
06:15 leu a tabela vazia e não regravou o Painel Comercial por SKU.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

pytest.importorskip("pyarrow", reason="histórico em Parquet exige pyarrow")

import scripts.history_cli as h  # noqa: E402


class TestDiaOperacional:
    def test_madrugada_pertence_ao_turno_da_noite(self):
        assert h._dia_operacional(datetime(2026, 11, 24, 0, 43)) == date(2026, 11, 23)

    def test_noite_antes_da_meia_noite(self):
        assert h._dia_operacional(datetime(2026, 11, 23, 23, 50)) == date(2026, 11, 23)

    def test_depois_da_virada_e_o_proprio_dia(self):
        assert h._dia_operacional(datetime(2026, 11, 24, 6, 0)) == date(2026, 11, 24)

    def test_corte_da_poda_das_00h43_mantem_d1_e_d2(self, monkeypatch):
        """Janela de 2 dias, poda às 00:43 de 24/11: ficam 22 e 23/11."""
        monkeypatch.setenv("RAC_HOT_WINDOW_DAYS", "2")
        monkeypatch.setattr(h, "_dia_operacional", lambda agora=None: date(2026, 11, 23))
        spec = h._TIER_SPECS[h.DATASET_PRICETRACK]

        class _A:
            cutoff = None

        assert h._spec_cutoff(spec, _A()) == date(2026, 11, 22)


class TestPreservaDiaMaisRecente:
    def test_nunca_esvazia_a_tabela(self):
        """PriceTrack só com 22/11 e corte em 23/11: 22/11 fica."""
        dias = [date(2026, 11, 22)]
        assert h._preserva_dia_mais_recente(dias, date(2026, 11, 22)) == []

    def test_dias_mais_antigos_saem(self):
        dias = [date(2026, 11, 20), date(2026, 11, 21)]
        assert h._preserva_dia_mais_recente(dias, date(2026, 11, 23)) == dias

    def test_tabela_vazia(self):
        assert h._preserva_dia_mais_recente([], None) == []
