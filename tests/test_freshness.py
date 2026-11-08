"""Selo de frescor (utils/freshness.py) — livro-razão × contrato, numa linha."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from utils import freshness as fr

DIA = datetime(2026, 11, 9)  # segunda


def _job(id_, turno, hora, deadline_min=120, executor_nome="PC coletor (Windows, IP residencial)"):
    def inicio(d):
        return datetime(d.year, d.month, d.day, hora, 0)
    return SimpleNamespace(
        id=id_, turno=turno, executor_nome=executor_nome,
        inicio_esperado=inicio,
        deadline=lambda d: inicio(d) + timedelta(minutes=deadline_min),
    )


JOBS = [
    _job("local_manha", "Abertura", 8),
    _job("local_tarde", "Tarde", 14),
    _job("local_noite", "Fechamento", 20),
    SimpleNamespace(id="pricetrack", turno=None),  # não grava em coletas → ignorado
]


def test_turno_concluido_e_proximo_ainda_no_futuro() -> None:
    agora = DIA.replace(hour=13)
    batidas = {"local_manha": {"status": "SUCCESS", "finished_at": "2026-11-09T11:40:00+00:00"}}
    res = fr.resumir(JOBS, batidas, agora)
    assert res.nivel == "ok"
    assert (res.esperados, res.concluidos) == (1, 1)
    assert res.ultimo_turno == "Abertura"
    assert res.ultima_coleta == datetime(2026, 11, 9, 8, 40)  # UTC → BRT
    assert "1/1 jobs de coleta OK" in fr.rotulo(res)


def test_ausencia_apos_deadline_e_critico() -> None:
    agora = DIA.replace(hour=17)
    batidas = {"local_manha": {"status": "SUCCESS", "finished_at": "2026-11-09T09:00:00"}}
    res = fr.resumir(JOBS, batidas, agora)
    assert res.nivel == "critico"
    assert any("Tarde" in p and "não executou" in p for p in res.problemas)
    assert "PC coletor" in fr.rotulo(res)


def test_dentro_do_prazo_sem_batida_nao_e_alarme() -> None:
    agora = DIA.replace(hour=14, minute=30)
    batidas = {"local_manha": {"status": "SUCCESS", "finished_at": "2026-11-09T09:00:00"}}
    res = fr.resumir(JOBS, batidas, agora)
    assert res.nivel == "ok" and res.problemas == []
    assert res.esperados == 2 and res.concluidos == 1


def test_parcial_e_atencao_e_rodando_aparece() -> None:
    agora = DIA.replace(hour=15)
    batidas = {
        "local_manha": {"status": "PARTIAL", "finished_at": "2026-11-09T09:00:00"},
        "local_tarde": {"status": "STARTED", "started_at": "2026-11-09T14:00:00"},
    }
    res = fr.resumir(JOBS, batidas, agora)
    assert res.nivel == "atencao"
    assert res.rodando == ["Tarde"]
    assert "rodando: Tarde" in fr.rotulo(res)


def test_antes_do_primeiro_turno_e_desconhecido() -> None:
    res = fr.resumir(JOBS, {}, DIA.replace(hour=7))
    assert res.nivel == "desconhecido"
    assert "Nenhum turno" in fr.rotulo(res)
