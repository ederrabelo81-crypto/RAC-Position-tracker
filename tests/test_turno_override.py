"""
tests/test_turno_override.py — RAC_TURNO crava o turno de uma execução.

Por que existe: o cron do GitHub Actions é best effort (atrasos rotineiros de
35–80 min, às vezes horas). `get_turno()` decide o turno pela HORA, então um run
das 8h (Abertura) que só arranca às 12h gravaria "Tarde" e bateria ponto como o
job errado — o mesmo incidente de turno trocado que o collect.yml já cercava. O
coletor Amazon-only na nuvem crava RAC_TURNO por cron; o PC coletor não define a
variável e segue no relógio (as janelas do local_scheduled_collect.bat o
protegem).

Rode: pytest tests/test_turno_override.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.text import get_turno  # noqa: E402

# 20h BRT → sem override cairia em "Fechamento" pelo relógio; é o contraste que
# prova que o override venceu quando pedimos outro turno.
_NOITE = datetime(2026, 9, 6, 20, 0)
_MANHA = datetime(2026, 9, 6, 8, 0)


class TestSemOverride:
    def test_relogio_manda_quando_env_ausente(self, monkeypatch):
        monkeypatch.delenv("RAC_TURNO", raising=False)
        assert get_turno(_MANHA) == "Abertura"
        assert get_turno(_NOITE) == "Fechamento"

    @pytest.mark.parametrize("valor", ["", "   "])
    def test_env_vazia_e_ignorada(self, monkeypatch, valor):
        monkeypatch.setenv("RAC_TURNO", valor)
        assert get_turno(_NOITE) == "Fechamento"


class TestComOverride:
    @pytest.mark.parametrize("turno", ["Abertura", "Tarde", "Fechamento"])
    def test_env_valida_vence_o_relogio(self, monkeypatch, turno):
        # Hora das 20h (Fechamento pelo relógio), mas o override manda.
        monkeypatch.setenv("RAC_TURNO", turno)
        assert get_turno(_NOITE) == turno

    @pytest.mark.parametrize("bruto,esperado", [
        ("abertura", "Abertura"),
        ("TARDE", "Tarde"),
        ("  Fechamento  ", "Fechamento"),
    ])
    def test_case_e_espacos_sao_tolerados(self, monkeypatch, bruto, esperado):
        monkeypatch.setenv("RAC_TURNO", bruto)
        assert get_turno(_MANHA) == esperado

    def test_valor_invalido_cai_no_relogio(self, monkeypatch):
        """Turno irreconhecível não vira dado errado — volta para a hora."""
        monkeypatch.setenv("RAC_TURNO", "Madrugada")
        assert get_turno(_MANHA) == "Abertura"
        assert get_turno(_NOITE) == "Fechamento"
