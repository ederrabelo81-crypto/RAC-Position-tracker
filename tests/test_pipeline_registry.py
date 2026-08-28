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
    DIAS_UTEIS,
    EXEC_ACTIONS,
    EXEC_EXTERNO,
    EXEC_LOCAL,
    EXEC_VM,
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
    """A divisão entre as três máquinas é a regra de negócio central."""

    @pytest.mark.parametrize(
        "plataforma",
        ["Mercado Livre", "Magalu", "Shopee", "Casas Bahia"],
    )
    def test_plataformas_com_antibot_sao_do_pc_local(self, plataforma):
        """ML/Magalu/Shopee/CB exigem IP residencial e Chrome logado.

        Cobradas do Actions, o alerta apontaria para a máquina errada — e o
        conserto sugerido (re-disparar o workflow) devolveria zero linha com
        aparência de sucesso.
        """
        dono = dono_da_plataforma(plataforma, "Abertura")
        assert dono is not None
        assert dono.executor == EXEC_LOCAL

    @pytest.mark.parametrize("plataforma", ["Amazon", "Leroy Merlin", "Google Shopping"])
    def test_plataformas_de_datacenter_sao_do_actions(self, plataforma):
        dono = dono_da_plataforma(plataforma, "Abertura")
        assert dono is not None
        assert dono.executor == EXEC_ACTIONS

    def test_dealers_tem_dono_e_e_a_vm(self):
        """O ponto cego que motivou o registro inteiro."""
        dono = dono_da_plataforma("dealers", "Abertura")
        assert dono is not None
        assert dono.executor == EXEC_VM

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

    def test_bestsellers_so_em_dia_util(self):
        """Sábado e domingo têm calendário promocional próprio.

        Cobrar a lista no fim de semana geraria alerta todo sábado — e o alerta
        que é vermelho todo fim de semana deixa de ser lido.
        """
        bestsellers = JOBS_POR_ID["local_bestsellers"]
        assert bestsellers.dias == DIAS_UTEIS
        assert not bestsellers.esperado_em(date(2026, 8, 29))  # sábado
        assert bestsellers.esperado_em(date(2026, 8, 28))      # sexta

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
