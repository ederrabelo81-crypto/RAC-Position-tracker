"""
tests/test_pipeline_registry.py — Invariantes do contrato de execução.

O registro (`utils/pipeline_registry.py`) só vale enquanto for coerente. As
invariantes cobertas aqui não são estilo: cada uma corresponde a um modo de
falha que já aconteceu neste projeto.

* **Plataforma órfã.** Dealers eram coletados pela VM Oracle e não constavam de
  expectativa nenhuma — quando a VM parou, em Ago/2026, ninguém foi cobrado.
* **Dois donos.** Duas expectativas para o mesmo (plataforma, turno) geram dois
  alertas do mesmo buraco, e alerta duplicado é o primeiro passo para alerta
  ignorado (16 runs vermelhos seguidos do watchdog, 10/08/2026).
* **Deadline antes da tolerância.** Torna o estado ATRASADO inobservável: todo
  atraso vira "não executou" e o alerta perde a graduação.
* **Alerta sem saída.** Job sem remediação produz mensagem que diz o que
  quebrou e não diz o que fazer — ruído com aparência de informação.

Rode: pytest tests/test_pipeline_registry.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.pipeline_registry import (  # noqa: E402
    EXEC_EXTERNO,
    EXEC_LOCAL,
    EXECUTORES,
    JOBS,
    JOBS_POR_ID,
    PLATFORM_KEY_TO_NOME,
    dono_da_plataforma,
    jobs_do_dia,
    validar_registro,
)


class TestRegistroCoerente:
    def test_sem_problemas_de_invariante(self):
        """A validação completa passa — é o portão que roda no CI."""
        assert validar_registro() == []

    def test_todo_executor_tem_diagnostico(self):
        """Alerta que nomeia a máquina precisa dizer como olhar para ela."""
        for executor in EXECUTORES.values():
            if executor.id == EXEC_EXTERNO:
                continue
            assert executor.diagnostico
            assert executor.onde_agendar

    def test_indice_bate_com_a_tupla(self):
        assert set(JOBS_POR_ID) == {j.id for j in JOBS}
        assert len(JOBS_POR_ID) == len(JOBS)


class TestDivisaoDeTrabalho:
    """Desde Set/2026 o PC local é o dono único da coleta de oferta/posição."""

    @pytest.mark.parametrize(
        "plataforma",
        [
            "Mercado Livre", "Amazon", "Magalu", "Casas Bahia",
            "Google Shopping", "Leroy Merlin", "Shopee",
        ],
    )
    @pytest.mark.parametrize("turno", ["Abertura", "Tarde", "Fechamento"])
    def test_toda_plataforma_e_do_pc_local_nos_tres_turnos(self, plataforma, turno):
        """Um coletor só, três turnos: o dono de cada (plataforma, turno) é o PC.

        Cobrada de outra máquina, o alerta apontaria para o lugar errado — e o
        conserto sugerido (re-disparar um workflow que não coleta mais)
        devolveria zero linha com aparência de sucesso.
        """
        dono = dono_da_plataforma(plataforma, turno)
        assert dono is not None
        assert dono.executor == EXEC_LOCAL

    @pytest.mark.parametrize("turno", ["Abertura", "Tarde", "Fechamento"])
    def test_dealers_tem_dono_e_e_o_pc_local(self, turno):
        """Dealers voltaram ao foco e são coletados localmente (IP residencial).

        O ponto cego que motivou o registro (dealers órfãos quando a VM parou)
        continua fechado: agora eles têm dono explícito em cada turno.
        """
        dono = dono_da_plataforma("dealers", turno)
        assert dono is not None
        assert dono.executor == EXEC_LOCAL

    def test_toda_plataforma_ativa_do_config_tem_dono(self):
        """Ligar uma plataforma no config sem dar dono a ela reprova aqui."""
        from config import ACTIVE_PLATFORMS

        for chave, ativa in ACTIVE_PLATFORMS.items():
            nome = PLATFORM_KEY_TO_NOME.get(chave)
            if not ativa or nome is None:
                continue
            assert dono_da_plataforma(nome) is not None, (
                f"'{nome}' está ativa em ACTIVE_PLATFORMS e não é cobrada de "
                "nenhum job — ninguém percebe quando ela some"
            )


class TestJanelasEHorarios:
    def test_deadline_do_pricetrack_cabe_antes_do_briefing(self):
        """A folga que faltou em 27/08/2026.

        O import precisa ser cobrado ANTES das 07:00; um deadline depois disso
        deixaria o briefing publicar dado velho sem que nada tivesse disparado.
        """
        pricetrack = JOBS_POR_ID["gh_pricetrack_d1"]
        briefing = JOBS_POR_ID["briefing_0700"]
        dia = date(2026, 8, 27)
        assert pricetrack.deadline(dia) < briefing.inicio_esperado(dia)

    def test_briefing_declara_a_dependencia_do_pricetrack(self):
        assert "gh_pricetrack_d1" in JOBS_POR_ID["briefing_0700"].depende_de

    def test_coleta_local_roda_todo_dia_nos_tres_turnos(self):
        """As três coletas locais são esperadas todos os dias da semana.

        Sem calendário de dia útil: oferta/posição é foto do dia, não série
        semanal, então fim de semana também conta.
        """
        for jid, horario in (
            ("local_manha", (8, 0)),
            ("local_tarde", (14, 0)),
            ("local_noite", (20, 0)),
        ):
            job = JOBS_POR_ID[jid]
            assert job.horario_brt == horario
            assert job.esperado_em(date(2026, 8, 29))  # sábado
            assert job.esperado_em(date(2026, 8, 28))  # sexta

    def test_jobs_do_dia_exclui_consumidores_por_padrao(self):
        """O briefing não bate ponto: cobrá-lo como coletor seria falso."""
        ids = {j.id for j in jobs_do_dia(date(2026, 8, 28))}
        assert "briefing_0700" not in ids
        com_externos = {j.id for j in jobs_do_dia(date(2026, 8, 28), incluir_externos=True)}
        assert "briefing_0700" in com_externos

    def test_ordem_dos_limites(self):
        """inicio < tolerância < deadline, para todo job e todo dia."""
        dia = date(2026, 8, 28)
        for job in JOBS:
            assert job.inicio_esperado(dia) < job.limite_atraso(dia) < job.deadline(dia)
