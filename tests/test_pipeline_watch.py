"""
tests/test_pipeline_watch.py — Classificação do supervisor de execução.

Cada caso aqui é um dos modos de falha de agosto/2026 reduzido à sua forma
mínima. Nenhum teste toca a rede: a classificação recebe a batida de ponto e a
contagem do destino já resolvidas, justamente para poder ser testada sem banco.

Rode: pytest tests/test_pipeline_watch.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline_watch import (  # noqa: E402
    ATRASADO,
    EM_JANELA,
    EXECUTOR_OFFLINE,
    FALHOU,
    NAO_EXECUTOU,
    OK,
    SEM_DADO,
    TRAVADO,
    Degradacao,
    Diagnostico,
    _classificar,
    _colapsar_executores,
    _exit_code,
    _formatar_telegram,
)
from utils.pipeline_registry import JOBS_POR_ID  # noqa: E402

DIA = date(2026, 8, 27)

PRICETRACK = JOBS_POR_ID["gh_pricetrack_d1"]      # 03:20, crítico
COLETA_GH = JOBS_POR_ID["gh_collect_abertura"]    # 10:00, crítico
LOCAL_MANHA = JOBS_POR_ID["local_manha"]          # 09:00, crítico
LOCAL_NOITE = JOBS_POR_ID["local_noite"]          # 20:00
BESTSELLERS = JOBS_POR_ID["local_bestsellers"]    # 09:30


def _hora(h: int, m: int = 0) -> datetime:
    return datetime(DIA.year, DIA.month, DIA.day, h, m)


def _batida(status: str, rows=None) -> dict:
    return {"status": status, "rows_written": rows, "started_at": "2026-08-27T03:21:00"}


class TestAusenciaDeExecucao:
    """O caso que nenhum monitor do projeto sabia enxergar."""

    def test_antes_da_tolerancia_e_em_janela(self):
        diag = _classificar(PRICETRACK, DIA, _hora(3, 30), None, 0)
        assert diag.estado == EM_JANELA

    def test_depois_da_tolerancia_e_atraso(self):
        # 03:20 + 90min de tolerância = 04:50
        diag = _classificar(PRICETRACK, DIA, _hora(5, 0), None, 0)
        assert diag.estado == ATRASADO

    def test_depois_do_deadline_e_nao_executou(self):
        """O incidente de 27/08: às 06:35 o import ainda não tinha rodado."""
        diag = _classificar(PRICETRACK, DIA, _hora(6, 35), None, 0)
        assert diag.estado == NAO_EXECUTOU
        assert diag.critico
        assert "06:30" in diag.detalhe  # o deadline aparece na mensagem

    def test_dado_presente_sem_batida_nao_e_falha(self):
        """Executor ainda não instrumentado não pode virar alarme falso.

        Monitor novo que grita durante a adoção é monitor desligado no terceiro
        dia — e aí a instrumentação nunca chega a valer nada.
        """
        diag = _classificar(COLETA_GH, DIA, _hora(23, 0), None, 4210)
        assert diag.estado == OK
        assert "sem batida de ponto" in diag.detalhe


class TestExecucaoQueNaoTrouxeDado:
    """Google Shopping: run verde, zero linha, um mês de silêncio."""

    def test_sucesso_com_zero_linha_e_sem_dado(self):
        diag = _classificar(
            COLETA_GH, DIA, _hora(12, 0), _batida("SUCCESS", 0), 0
        )
        assert diag.estado == SEM_DADO
        assert diag.critico

    def test_sucesso_com_linhas_e_ok(self):
        diag = _classificar(
            COLETA_GH, DIA, _hora(12, 0), _batida("SUCCESS", 4210), 4210
        )
        assert diag.estado == OK

    def test_falha_declarada_e_falhou(self):
        diag = _classificar(COLETA_GH, DIA, _hora(12, 0), _batida("FAILED"), 0)
        assert diag.estado == FALHOU

    def test_started_que_nunca_fechou_e_travado(self):
        """Job morto no meio: bateu início e sumiu. Não é 'não rodou'."""
        diag = _classificar(
            PRICETRACK, DIA, _hora(9, 0), _batida("STARTED"), 0
        )
        assert diag.estado == TRAVADO

    def test_started_dentro_da_janela_ainda_e_em_janela(self):
        diag = _classificar(PRICETRACK, DIA, _hora(3, 40), _batida("STARTED"), 0)
        assert diag.estado == EM_JANELA


class TestMaquinaOffline:
    """Notebook desligado é UMA causa, não quatro plataformas quebradas."""

    def test_todos_os_jobs_da_maquina_ausentes_viram_um_alerta(self):
        diagnosticos = [
            Diagnostico(LOCAL_MANHA, NAO_EXECUTOU, "nada"),
            Diagnostico(LOCAL_NOITE, NAO_EXECUTOU, "nada"),
            Diagnostico(BESTSELLERS, NAO_EXECUTOU, "nada"),
        ]
        resultado = _colapsar_executores(diagnosticos)
        assert len(resultado) == 1
        assert resultado[0].estado == EXECUTOR_OFFLINE
        assert "PC coletor" in resultado[0].detalhe

    def test_uma_execucao_bem_sucedida_prova_que_a_maquina_rodou(self):
        """Se algo rodou ali, quem faltou quebrou de verdade."""
        diagnosticos = [
            Diagnostico(LOCAL_MANHA, OK, "3.000 linhas"),
            Diagnostico(LOCAL_NOITE, NAO_EXECUTOU, "nada"),
        ]
        resultado = _colapsar_executores(diagnosticos)
        assert {d.estado for d in resultado} == {OK, NAO_EXECUTOU}

    def test_jobs_ainda_em_janela_nao_contam_como_ausencia(self):
        """Às 12:35 o job da noite ainda nem devia ter rodado."""
        diagnosticos = [
            Diagnostico(LOCAL_MANHA, NAO_EXECUTOU, "nada"),
            Diagnostico(LOCAL_NOITE, EM_JANELA, "previsto 20:00"),
        ]
        resultado = _colapsar_executores(diagnosticos)
        # Um único job cobrado não caracteriza máquina offline.
        assert {d.estado for d in resultado} == {NAO_EXECUTOU, EM_JANELA}


class TestExitCode:
    def test_tudo_ok_sai_zero(self):
        assert _exit_code([Diagnostico(COLETA_GH, OK, "ok")], []) == 0

    def test_critico_ausente_sai_um(self):
        assert _exit_code([Diagnostico(COLETA_GH, NAO_EXECUTOU, "x")], []) == 1

    def test_degradacao_cronica_sai_um(self):
        """Um mês de plataforma zerada não pode ser 'aviso'."""
        deg = Degradacao("Google Shopping", 30, True, "gh_collect_abertura")
        assert _exit_code([Diagnostico(COLETA_GH, OK, "ok")], [deg]) == 1

    def test_atraso_sozinho_sai_dois(self):
        """Atraso é amarelo: run vermelho todo dia é run que ninguém abre."""
        assert _exit_code([Diagnostico(COLETA_GH, ATRASADO, "40 min")], []) == 2

    def test_zerada_por_um_dia_nao_e_cronica(self):
        deg = Degradacao("Shopee", 1, False, "local_manha")
        assert not deg.cronica
        assert _exit_code([Diagnostico(COLETA_GH, OK, "ok")], [deg]) == 0


class TestAlerta:
    def test_alerta_traz_o_comando_de_remediacao(self):
        """Alerta sem saída é ruído com aparência de informação."""
        texto = _formatar_telegram(
            [Diagnostico(PRICETRACK, NAO_EXECUTOU, "nenhuma execução até 06:35")],
            [],
            DIA,
        )
        assert "PriceTrack" in texto
        assert "pricetrack_api_import.py" in texto

    def test_degradacao_cronica_nomeia_os_dias(self):
        texto = _formatar_telegram(
            [],
            [Degradacao("Google Shopping", 30, True, "gh_collect_abertura")],
            DIA,
        )
        assert "Google Shopping" in texto
        assert "30+ dias" in texto

    def test_tudo_certo_produz_mensagem_positiva(self):
        texto = _formatar_telegram([Diagnostico(COLETA_GH, OK, "4.210 linhas")], [], DIA)
        assert "tudo no contrato" in texto

    def test_escapa_html_do_detalhe(self):
        """O detalhe vem de mensagem de erro; `parse_mode=HTML` engasga com <>."""
        diag = Diagnostico(COLETA_GH, FALHOU, "erro <script> & cia")
        texto = _formatar_telegram([diag], [], DIA)
        assert "&lt;script&gt;" in texto
        assert "<script>" not in texto
