"""
tests/test_heartbeat.py — Batida de ponto: garantias de não-interferência.

O livro-razão existe para observar a coleta. As três garantias cobertas aqui
são o que impede que ele passe a ATRAPALHAR a coleta:

1. **Sem RAC_JOB_ID, ninguém bate ponto.** Rodar `python main.py` na mão não
   pode registrar uma promessa que nenhum agendador fez — senão o supervisor
   cobra amanhã um job que só existiu no terminal de alguém.
2. **Supabase fora não derruba nada.** É exatamente quando o banco está
   restrito por cota que a pipeline mais quebra; um livro-razão que só existe
   lá dentro fica cego junto, e um que LEVANTA exceção leva a coleta junto.
3. **Zero linha não é SUCCESS.** É o modo de falha do Google Shopping — run
   verde, nenhuma linha, um mês de silêncio.

Rode: pytest tests/test_heartbeat.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import heartbeat  # noqa: E402


@pytest.fixture
def sem_supabase(monkeypatch, tmp_path):
    """Isola o módulo: sem banco e com o JSONL num diretório temporário."""
    monkeypatch.setattr(heartbeat, "ARQUIVO_LOCAL", tmp_path / "heartbeat.jsonl")
    monkeypatch.setattr(heartbeat, "_gravar_supabase", lambda registro: False)
    return tmp_path / "heartbeat.jsonl"


def _linhas(arquivo: Path):
    return [json.loads(l) for l in arquivo.read_text(encoding="utf-8").splitlines()]


class TestNaoInterferencia:
    def test_supabase_fora_nao_levanta_e_grava_local(self, sem_supabase):
        assert heartbeat.bater("gh_pricetrack_d1", "SUCCESS", rows=10) is False
        registros = _linhas(sem_supabase)
        assert registros[-1]["job_id"] == "gh_pricetrack_d1"
        assert registros[-1]["rows_written"] == 10

    def test_falha_de_escrita_local_tambem_e_absorvida(self, monkeypatch, tmp_path):
        """Disco cheio não pode derrubar a coleta que ele deveria observar."""
        monkeypatch.setattr(heartbeat, "_gravar_supabase", lambda r: False)
        monkeypatch.setattr(
            heartbeat, "ARQUIVO_LOCAL", tmp_path / "nao" / "existe" / "hb.jsonl"
        )
        monkeypatch.setattr(
            Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio"))
        )
        assert heartbeat.bater("gh_pricetrack_d1", "SUCCESS") is False

    def test_status_invalido_e_erro_de_programacao(self, sem_supabase):
        """Único caso que recusa: vocabulário errado é bug, não ambiente."""
        with pytest.raises(ValueError):
            heartbeat.bater("gh_pricetrack_d1", "TALVEZ")

    def test_job_desconhecido_ainda_e_gravado(self, sem_supabase):
        """Perder a batida de um job novo é pior que gravar um id órfão."""
        heartbeat.bater("job_que_nao_existe", "SUCCESS")
        assert _linhas(sem_supabase)[-1]["executor"] == "desconhecido"


class TestContextoDeExecucao:
    def test_sucesso_registra_inicio_e_fim(self, sem_supabase):
        with heartbeat.batida("local_bestsellers") as ctx:
            ctx["rows"] = 60
        registros = _linhas(sem_supabase)
        assert [r["status"] for r in registros] == ["STARTED", "SUCCESS"]
        assert registros[-1]["rows_written"] == 60
        assert registros[-1]["duration_seconds"] is not None

    def test_zero_linha_vira_partial(self, sem_supabase):
        """Execução sem resultado ≠ execução bem-sucedida."""
        with heartbeat.batida("local_bestsellers") as ctx:
            ctx["rows"] = 0
        assert _linhas(sem_supabase)[-1]["status"] == "PARTIAL"

    def test_excecao_registra_failed_e_repropaga(self, sem_supabase):
        with pytest.raises(RuntimeError):
            with heartbeat.batida("local_bestsellers"):
                raise RuntimeError("Akamai bloqueou")
        ultimo = _linhas(sem_supabase)[-1]
        assert ultimo["status"] == "FAILED"
        assert "Akamai" in ultimo["detail"]

    def test_data_ref_explicita_e_respeitada(self, sem_supabase):
        """O import das 03:20 se refere ao dia corrente, não a `now()::date` do banco."""
        heartbeat.bater("gh_pricetrack_d1", "SUCCESS", data_ref=date(2026, 8, 27))
        assert _linhas(sem_supabase)[-1]["data_ref"] == "2026-08-27"


class TestCli:
    def test_cli_sempre_sai_zero(self, sem_supabase, monkeypatch):
        """Exit != 0 aqui derrubaria o passo do workflow que ela só observa."""
        assert heartbeat.main(["--job", "gh_watchdog", "--status", "SUCCESS"]) == 0
