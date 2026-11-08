"""
utils/freshness.py — selo de frescor dos painéis (Nov/2026).

O rodapé da barra lateral dizia "Supabase 🟢" e a data de hoje: nada sobre o
DADO. Quem abre o painel às 15h não sabia se o turno da Tarde já tinha entrado,
nem que o Mercado Livre veio degradado. Este módulo resume o livro-razão
(``pipeline_heartbeat``) contra o contrato (``utils.pipeline_registry.JOBS``)
numa linha só, exibida no topo de toda página.

Só entram jobs que gravam em ``coletas`` (``turno`` preenchido): o selo fala de
"o que a tela mostra", não da saúde de toda a pipeline — essa é a página
🩺 Saúde do dado e o ``pipeline_watch.py``.

Módulo puro: recebe os jobs, as batidas e o "agora"; não consulta nada.
Testado em ``tests/test_freshness.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

# Estados por job, do melhor para o pior.
OK = "ok"
RODANDO = "rodando"
AGUARDANDO = "aguardando"   # ainda dentro do prazo, sem batida
PARCIAL = "parcial"
FALHOU = "falhou"
NAO_EXECUTOU = "nao_executou"

_RUINS = (PARCIAL, FALHOU, NAO_EXECUTOU)


@dataclass
class Freshness:
    """Resumo do frescor do dia para o selo.

    Attributes:
        nivel: ``ok`` | ``atencao`` | ``critico`` | ``desconhecido``.
        ultima_coleta: Fim da última coleta concluída (SUCCESS/PARTIAL), BRT.
        ultimo_turno: Turno dessa coleta.
        esperados: Jobs de coleta cujo horário já chegou.
        concluidos: Quantos desses terminaram em SUCCESS.
        problemas: Frases curtas ("Tarde (PC coletor): não executou").
        rodando: Jobs com STARTED sem fim.
    """

    nivel: str
    ultima_coleta: Optional[datetime] = None
    ultimo_turno: Optional[str] = None
    esperados: int = 0
    concluidos: int = 0
    problemas: List[str] = field(default_factory=list)
    rodando: List[str] = field(default_factory=list)


def _parse_ts(valor: Any) -> Optional[datetime]:
    """ISO do PostgREST (com ou sem fuso) → datetime ingênuo em BRT."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        ts = valor
    else:
        try:
            ts = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except ValueError:
            return None
    if ts.tzinfo is not None:
        from zoneinfo import ZoneInfo
        ts = ts.astimezone(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None)
    return ts


def estado_job(job: Any, batida: Optional[Mapping[str, Any]], agora: datetime) -> str:
    """Estado de um job de coleta no dia de ``agora``."""
    if batida:
        status = str(batida.get("status") or "").upper()
        if status == "SUCCESS":
            return OK
        if status == "PARTIAL":
            return PARCIAL
        if status == "FAILED":
            return FALHOU
        if status == "STARTED":
            return RODANDO
    if agora > job.deadline(agora.date()):
        return NAO_EXECUTOU
    return AGUARDANDO


_ROTULO = {
    PARCIAL: "parcial",
    FALHOU: "falhou",
    NAO_EXECUTOU: "não executou",
}


def resumir(jobs: Sequence[Any], batidas: Mapping[str, Mapping[str, Any]],
            agora: datetime) -> Freshness:
    """Resume os jobs de coleta do dia de ``agora`` num ``Freshness``.

    Args:
        jobs: ``JobSpec`` esperados no dia (os sem ``turno`` são ignorados).
        batidas: ``job_id`` → última batida do dia (``heartbeat.ultimas_batidas``).
        agora: Instante BRT ingênuo.
    """
    coleta = [j for j in jobs if getattr(j, "turno", None)
              and j.inicio_esperado(agora.date()) <= agora]
    if not coleta:
        return Freshness(nivel="desconhecido")

    res = Freshness(nivel=OK, esperados=len(coleta))
    for job in coleta:
        b = batidas.get(job.id)
        estado = estado_job(job, b, agora)
        if estado == OK:
            res.concluidos += 1
        elif estado == RODANDO:
            res.rodando.append(job.turno)
        elif estado in _RUINS:
            res.problemas.append(f"{job.turno} ({job.executor_nome.split(' (')[0]}): "
                                 f"{_ROTULO[estado]}")
        if b and str(b.get("status") or "").upper() in ("SUCCESS", "PARTIAL"):
            fim = _parse_ts(b.get("finished_at")) or _parse_ts(b.get("started_at"))
            if fim and (res.ultima_coleta is None or fim > res.ultima_coleta):
                res.ultima_coleta, res.ultimo_turno = fim, job.turno

    if any("não executou" in p or "falhou" in p for p in res.problemas):
        res.nivel = "critico"
    elif res.problemas:
        res.nivel = "atencao"
    return res


def rotulo(res: Freshness) -> str:
    """Texto de uma linha do selo (sem a bolinha de cor)."""
    if res.nivel == "desconhecido":
        return "Nenhum turno de coleta esperado até agora"
    partes = []
    if res.ultima_coleta:
        partes.append(f"Última coleta {res.ultima_coleta:%d/%m %H:%M}"
                      + (f" ({res.ultimo_turno})" if res.ultimo_turno else ""))
    else:
        partes.append("Nenhuma coleta concluída hoje")
    partes.append(f"{res.concluidos}/{res.esperados} jobs de coleta OK")
    if res.rodando:
        partes.append("rodando: " + ", ".join(sorted(set(res.rodando))))
    if res.problemas:
        partes.append("; ".join(res.problemas[:3])
                      + (f" (+{len(res.problemas) - 3})" if len(res.problemas) > 3 else ""))
    return " · ".join(partes)
