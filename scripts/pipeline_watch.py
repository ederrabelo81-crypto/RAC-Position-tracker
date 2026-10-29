"""
scripts/pipeline_watch.py — Supervisor de EXECUÇÃO da pipeline.

O `daily_status_check.py` pergunta "o dado chegou?". Este pergunta a outra
metade, que ninguém perguntava: **"quem prometeu rodar hoje e não rodou?"**

As duas perguntas não são a mesma, e agosto/2026 mostrou os três jeitos de a
segunda passar em branco:

* **Não disparou.** O import do PriceTrack (cron 09:00 UTC) só começou às 19:18
  UTC de 27/08 — o briefing das 07:00 saiu com preço de D-2 e nada avisou,
  porque o job não falhou: ele ainda não tinha acontecido. Cron do Actions é
  *best effort*; a série de agosto tem atrasos rotineiros de 15min a 3h.
* **Rodou verde e trouxe zero.** Google Shopping está zerada há ~1 mês com 221
  runs seguidos de `success` no `collect.yml`, porque uma plataforma vazia não
  derruba o job — e, sendo `critical=False` no watchdog, no máximo produzia um
  WARN idêntico por dia, que virou paisagem.
* **A máquina sumiu.** A coleta de dealers da VM Oracle parou sem que nada
  percebesse: não havia canal "Oracle VM" em lugar nenhum, e o watchdog nem
  espera dealers (`ACTIVE_PLATFORMS['dealers']=False` faz
  `_expected_platforms()` pulá-los, embora o script da VM os colete
  explicitamente).

Os três viram evento aqui, contra o contrato de `utils/pipeline_registry.py`.

Uso::

    python scripts/pipeline_watch.py                 # hoje, alerta se houver problema
    python scripts/pipeline_watch.py --data 2026-08-27 --no-notify
    python scripts/pipeline_watch.py --json logs/pipeline_status.json
    python scripts/pipeline_watch.py --sempre-notificar   # manda mesmo tudo OK
    python scripts/pipeline_watch.py --sem-queda-volume   # pula a checagem de volume
    python scripts/pipeline_watch.py --sem-fontes-bestsellers  # pula a cobrança por fonte

Além de "não rodou" e "zerada", cobra a **queda brusca de volume**: plataforma
abaixo de 20% da sua mediana (mesmo turno, últimos 7 dias normais) em ≥2
turnos no mesmo dia. Foi o caso do Mercado Livre entre 09 e 14/10/2026, com
0–136 linhas por turno contra ~5.000. Como quase nunca chegava a zero, nada
alarmou durante cinco dias. Lógica pura em `utils/volume_drop.py`.

E cobra **fonte por fonte de Mais Vendidos**: lista que sumiu enquanto as
outras seguiam gravando (a Leroy Merlin parou em 07/10/2026 e o job do dia
inteiro seguiu verde). Lógica pura em `bestsellers/cobertura.py`.

Exit codes:
    0 — tudo dentro do contrato (ou só coisas ainda dentro da janela)
    1 — algum job crítico não executou, falhou ou executou sem dado; ou
        plataforma zerada/degradada há ≥3 dias
    2 — problemas não-críticos (atrasos, degradação de plataforma tolerante,
        queda de volume há menos de 3 dias, fonte de Mais Vendidos parada)
    3 — supervisor CEGO: Supabase indisponível, não deu para olhar

    O 3 é deliberadamente distinto de 1: "não consegui olhar" nunca deve ser
    lido como "olhei e estava tudo bem" — nem como "a coleta caiu".
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from bestsellers.cobertura import (
    JANELA_DIAS as JANELA_FONTES_BS,
    FonteMuda,
    RelatorioCobertura,
    classificar_fontes,
)
from utils.heartbeat import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_STARTED,
    LivroRazaoIndisponivel,
    batidas_periodo,
    ultimas_batidas,
)
from utils.pipeline_registry import (
    EXECUTORES,
    MARKETPLACES,
    PLATAFORMA_DEALERS,
    SEV_CRITICO,
    JOBS,
    JobSpec,
    jobs_do_dia,
)
from utils.text import now_brt
from utils.volume_drop import (
    DIAS_HISTORICO,
    FRACAO_QUEDA,
    JobDeColeta,
    QuedaVolume,
    classificar_quedas,
    observacoes_de_batidas,
)

# ---------------------------------------------------------------------------
# Vocabulário de estado
# ---------------------------------------------------------------------------

OK = "OK"
EM_JANELA = "EM_JANELA"          # ainda não era para ter rodado
ATRASADO = "ATRASADO"            # passou da tolerância, não do deadline
NAO_EXECUTOU = "NAO_EXECUTOU"    # passou do deadline sem batida nem dado
TRAVADO = "TRAVADO"              # bateu STARTED e nunca fechou
FALHOU = "FALHOU"                # bateu FAILED
SEM_DADO = "SEM_DADO"            # rodou, terminou, não gravou nada
EXECUTOR_OFFLINE = "EXECUTOR_OFFLINE"

#: Estados que significam "há trabalho a fazer agora".
_PROBLEMAS = (NAO_EXECUTOU, TRAVADO, FALHOU, SEM_DADO, EXECUTOR_OFFLINE)

_ICONE = {
    OK: "✅",
    EM_JANELA: "🕐",
    ATRASADO: "🟡",
    NAO_EXECUTOU: "🔴",
    TRAVADO: "🟠",
    FALHOU: "🔴",
    SEM_DADO: "🟠",
    EXECUTOR_OFFLINE: "🔌",
}

#: A partir de quantos dias seguidos zerada uma plataforma deixa de ser
#: "oscilação" e vira defeito crônico que merece issue, não linha de alerta.
#: Três dias porque dois já acontecem por fim de semana com o notebook
#: desligado; a partir do terceiro não há explicação inocente.
DIAS_PARA_CRONICO = 3

#: Teto da busca para trás. Passar de 30 dias não muda a conclusão ("está
#: quebrada há muito tempo") e custa uma consulta por dia.
MAX_DIAS_ZERADOS = 30


@dataclass
class Diagnostico:
    """Veredito sobre um job num dia.

    Attributes:
        job: O contrato avaliado.
        estado: Um dos estados acima.
        detalhe: Frase curta com o que foi observado.
        linhas: Linhas encontradas no destino (None = não consultado).
        batida: Última batida de ponto, se houver.
    """

    job: JobSpec
    estado: str
    detalhe: str
    linhas: Optional[int] = None
    batida: Optional[Dict[str, Any]] = None

    @property
    def critico(self) -> bool:
        """True se este veredito deve derrubar o exit code."""
        return self.estado in _PROBLEMAS and self.job.severidade == SEV_CRITICO

    def to_dict(self) -> Dict[str, Any]:
        """Forma serializável — é o que o portão do briefing lê."""
        return {
            "job": self.job.id,
            "nome": self.job.nome,
            "executor": self.job.executor,
            "executor_nome": self.job.executor_nome,
            "severidade": self.job.severidade,
            "estado": self.estado,
            "detalhe": self.detalhe,
            "linhas": self.linhas,
            "remediacao": self.job.remediacao,
        }


@dataclass
class Degradacao:
    """Plataforma que vem zerada há dias seguidos.

    Attributes:
        plataforma: Nome como gravado em `coletas.plataforma`.
        dias: Dias consecutivos sem nenhuma linha, contados de trás para frente.
        truncado: True se a contagem bateu no teto (`MAX_DIAS_ZERADOS`) — o
            número real é "pelo menos isso".
        job_id: Job cobrado por ela.
        evidencia: Diagnóstico do scraper (ex.: ``ML: 0/4 kw ok ... relogar``),
            herdado da queda de volume que esta zerada absorveu. Sem isso, a
            partir do 3º dia o alerta dizia "zerada" e perdia o porquê.
    """

    plataforma: str
    dias: int
    truncado: bool
    job_id: str
    evidencia: Optional[str] = None

    @property
    def cronica(self) -> bool:
        """True se já passou do limiar de defeito crônico."""
        return self.dias >= DIAS_PARA_CRONICO

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plataforma": self.plataforma,
            "dias_zerada": self.dias,
            "truncado": self.truncado,
            "job": self.job_id,
            "cronica": self.cronica,
            "evidencia": self.evidencia,
        }


class SupervisorCego(RuntimeError):
    """O supervisor não conseguiu consultar o estado — não é falha de coleta."""


# ---------------------------------------------------------------------------
# Sondagem do destino
# ---------------------------------------------------------------------------

def _client():
    """Client Supabase, ou levanta SupervisorCego."""
    from utils.supabase_client import _get_client

    client = _get_client()
    if client is None:
        raise SupervisorCego(
            "Supabase não configurado (SUPABASE_URL/SUPABASE_KEY) — "
            "sem isso não dá para saber o que rodou."
        )
    return client


def _contar_coletas(
    client, dia: date, turno: Optional[str], plataformas: Tuple[str, ...]
) -> int:
    """Conta linhas em `coletas` para o recorte do job.

    Args:
        client: Client Supabase.
        dia: Dia BRT.
        turno: "Abertura"/"Fechamento" ou None para o dia inteiro.
        plataformas: Nomes cobrados do job. O marcador ``dealers`` conta por
            EXCLUSÃO dos marketplaces conhecidos — assim um dealer novo entra
            na conta sem precisar tocar aqui, e sem importar `DEALER_CONFIGS`
            (que arrasta Playwright para dentro do supervisor).

    Returns:
        Número de linhas.
    """
    consulta = client.table("coletas").select("id", count="exact").eq("data", dia.isoformat())
    if turno:
        consulta = consulta.eq("turno", turno)

    if PLATAFORMA_DEALERS in plataformas:
        consulta = consulta.not_.in_("plataforma", list(MARKETPLACES))
    elif plataformas:
        consulta = consulta.in_("plataforma", list(plataformas))

    return consulta.limit(1).execute().count or 0


def _contar_pricetrack(client, dia: date) -> int:
    """Linhas de `pricetrack_daily` para o dia (o job importa D-1)."""
    resp = (
        client.table("pricetrack_daily")
        .select("id", count="exact")
        .eq("collection_date", dia.isoformat())
        .limit(1)
        .execute()
    )
    return resp.count or 0


def _contar_bestsellers(client, dia: date) -> int:
    """Linhas de `bestsellers` para o dia."""
    resp = (
        client.table("bestsellers")
        .select("id", count="exact")
        .eq("data", dia.isoformat())
        .limit(1)
        .execute()
    )
    return resp.count or 0


def _contar_clima(client, dia: date) -> int:
    """Linhas de `clima_diario` gravadas no dia (a rodada regrava D-10..D+15)."""
    resp = (
        client.table("clima_diario")
        .select("data", count="exact")
        .gte("atualizado_em", dia.isoformat())
        .lt("atualizado_em", (dia + timedelta(days=1)).isoformat())
        .limit(1)
        .execute()
    )
    return resp.count or 0


def _linhas_do_job(client, job: JobSpec, dia: date) -> Optional[int]:
    """Quantas linhas o job deveria ter produzido e produziu.

    Returns:
        Contagem, ou None quando o job não escreve numa tabela sondável (o
        watchdog escreve alerta; o briefing publica texto).
    """
    if job.destino.startswith("coletas"):
        return _contar_coletas(client, dia, job.turno, job.plataformas)
    if job.destino == "pricetrack_daily":
        # Importa D-1: a pergunta certa é sobre a véspera, não sobre o dia.
        return _contar_pricetrack(client, dia - timedelta(days=1))
    if job.destino == "bestsellers":
        return _contar_bestsellers(client, dia)
    if job.destino == "clima_diario":
        # Sondagem adicional: tabela ausente (migração 021 não aplicada) ou
        # consulta quebrada não pode cegar o supervisor inteiro.
        try:
            return _contar_clima(client, dia)
        except Exception as exc:
            logger.warning(f"[Supervisor] clima_diario não sondável: {exc}")
            return None
    return None


def _dias_zerados(
    client, plataforma: str, ate: date, limite: int = MAX_DIAS_ZERADOS
) -> Tuple[int, bool]:
    """Conta dias consecutivos sem nenhuma linha, caminhando para trás.

    Anda de trás para frente e para no primeiro dia com dado. Numa plataforma
    saudável isso custa **uma** consulta; só a plataforma quebrada paga o preço
    de varrer o mês. Contar o mês inteiro de todas as plataformas de uma vez
    custaria centenas de consultas por execução para responder a mesma coisa.

    Args:
        client: Client Supabase.
        plataforma: Nome em `coletas.plataforma`, ou o marcador ``dealers``.
        ate: Último dia considerado (normalmente D-1).
        limite: Teto de dias a inspecionar.

    Returns:
        (dias zerados, truncado). ``truncado=True`` significa "pelo menos
        `limite` dias" — a série pode ser mais antiga.
    """
    dias = 0
    cursor = ate
    while dias < limite:
        total = _contar_coletas(client, cursor, None, (plataforma,))
        if total > 0:
            return dias, False
        dias += 1
        cursor -= timedelta(days=1)
    return dias, True


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------

def _classificar(
    job: JobSpec,
    dia: date,
    agora: datetime,
    batida: Optional[Dict[str, Any]],
    linhas: Optional[int],
) -> Diagnostico:
    """Aplica o contrato do job ao que foi observado.

    Args:
        job: Contrato.
        dia: Dia avaliado.
        agora: Instante BRT de referência (injetável em teste).
        batida: Última batida de ponto do job naquele dia, se houver.
        linhas: Linhas encontradas no destino, ou None se não sondável.

    Returns:
        O veredito.
    """
    esperado = job.inicio_esperado(dia)
    limite = job.limite_atraso(dia)
    deadline = job.deadline(dia)

    if batida is None:
        # Sem batida, mas COM dado, é um executor que ainda não foi atualizado
        # para bater ponto — e não uma falha. Tratar como falha aqui faria o
        # supervisor gritar durante toda a adoção, que é como monitor novo
        # morre: desligado no terceiro dia por falso positivo.
        if linhas:
            return Diagnostico(
                job, OK,
                f"{linhas:,} linhas no destino (sem batida de ponto — "
                "executor ainda não instrumentado)".replace(",", "."),
                linhas, None,
            )
        if agora < limite:
            return Diagnostico(
                job, EM_JANELA,
                f"previsto {esperado:%H:%M}, tolerância até {limite:%H:%M}",
                linhas, None,
            )
        if agora < deadline:
            atraso = int((agora - esperado).total_seconds() // 60)
            return Diagnostico(
                job, ATRASADO,
                f"{atraso} min de atraso (deadline {deadline:%H:%M})",
                linhas, None,
            )
        return Diagnostico(
            job, NAO_EXECUTOU,
            f"nenhuma execução até {agora:%H:%M} (previsto {esperado:%H:%M}, "
            f"deadline {deadline:%H:%M})",
            linhas, None,
        )

    status = batida.get("status")

    if status == STATUS_FAILED:
        return Diagnostico(
            job, FALHOU,
            batida.get("detail") or "execução terminou em erro",
            linhas, batida,
        )

    if status == STATUS_STARTED:
        if agora >= deadline:
            return Diagnostico(
                job, TRAVADO,
                f"começou {batida.get('started_at', '?')} e nunca fechou "
                f"(deadline {deadline:%H:%M})",
                linhas, batida,
            )
        return Diagnostico(job, EM_JANELA, "em execução", linhas, batida)

    # SUCCESS ou PARTIAL: rodou até o fim. A pergunta que sobra é se trouxe algo.
    #
    # A contagem do destino é AGREGADA por (dia, turno, plataformas) e vários
    # executores escrevem no mesmo recorte — a VM e o PC coletam Amazon e Leroy
    # além do Actions. Então "há linhas no destino" NÃO prova que este job
    # trouxe alguma: um run que gravou zero ficaria verde às custas do vizinho
    # que gravou. Por isso o que a própria execução declara vem primeiro.
    gravadas = batida.get("rows_written")

    if status == STATUS_PARTIAL or gravadas == 0:
        return Diagnostico(
            job, SEM_DADO,
            f"execução concluída sem gravar nenhuma linha em {job.destino}"
            + (f" (destino tem {linhas:,} linhas de outro executor)".replace(",", ".")
               if linhas else ""),
            linhas, batida,
        )

    if linhas == 0:
        return Diagnostico(
            job, SEM_DADO,
            f"execução concluída e {job.destino} está vazio para o recorte do job",
            linhas, batida,
        )

    total = linhas if linhas is not None else gravadas
    detalhe = f"{total:,} linhas".replace(",", ".") if total is not None else "concluído"
    return Diagnostico(job, OK, detalhe, linhas, batida)


def _colapsar_executores(diagnosticos: List[Diagnostico]) -> List[Diagnostico]:
    """Converte "todos os jobs da máquina X faltaram" em UM alerta de máquina.

    Sem isto, o notebook desligado num sábado produz quatro linhas vermelhas
    idênticas — e foi assim que dezesseis execuções seguidas do watchdog
    ficaram vermelhas até o alerta parar de ser lido. A máquina offline é uma
    causa; as plataformas são só o sintoma dela.

    Args:
        diagnosticos: Vereditos individuais.

    Returns:
        Nova lista, com os jobs de um executor totalmente ausente substituídos
        por um único diagnóstico ``EXECUTOR_OFFLINE``.
    """
    por_executor: Dict[str, List[Diagnostico]] = {}
    for diag in diagnosticos:
        por_executor.setdefault(diag.job.executor, []).append(diag)

    resultado: List[Diagnostico] = []
    for executor, itens in por_executor.items():
        cobrados = [d for d in itens if d.estado != EM_JANELA]
        todos_ausentes = bool(cobrados) and all(
            d.estado == NAO_EXECUTOU for d in cobrados
        )
        if todos_ausentes and len(cobrados) > 1:
            info = EXECUTORES.get(executor)
            pior = max(cobrados, key=lambda d: d.job.severidade == SEV_CRITICO)
            resultado.append(
                Diagnostico(
                    pior.job,
                    EXECUTOR_OFFLINE,
                    f"{info.nome if info else executor} não executou NENHUM job hoje "
                    f"({', '.join(d.job.id for d in cobrados)}) — "
                    f"diagnóstico: {info.diagnostico if info else '—'}",
                    None,
                    None,
                )
            )
            resultado.extend(d for d in itens if d.estado == EM_JANELA)
        else:
            resultado.extend(itens)
    return resultado


# ---------------------------------------------------------------------------
# Varredura
# ---------------------------------------------------------------------------

def _pendencias_de_ontem(
    client, ontem: date, agora: datetime
) -> List[Diagnostico]:
    """Cobra os jobs de ONTEM cujo deadline só venceu depois da meia-noite.

    O turno Fechamento começa 21:00 BRT e seu deadline cai às 02:00 do dia
    seguinte. A varredura das 22:35 é cedo demais (ali ele ainda é só
    ATRASADO) e a das 06:35 já avalia o dia novo — então uma coleta noturna que
    nunca rodou passava despercebida entre as duas. Esta passagem fecha a
    janela, e o faz no disparo da manhã, que é quando importa: o briefing das
    07:00 consome justamente o dado de ontem.

    Args:
        client: Client Supabase.
        ontem: Dia BRT anterior ao avaliado.
        agora: Instante de referência.

    Returns:
        Só os vereditos de ontem que já são problema — job OK ou ainda em
        janela não volta, para o relatório de hoje não sair duplicado.

    Raises:
        SupervisorCego: Supabase ou livro-razão indisponível.
    """
    try:
        batidas = ultimas_batidas(ontem, [j.id for j in jobs_do_dia(ontem)])
    except LivroRazaoIndisponivel as exc:
        raise SupervisorCego(str(exc)) from exc

    pendentes: List[Diagnostico] = []
    for job in jobs_do_dia(ontem):
        if agora < job.deadline(ontem):
            continue  # ainda dentro do prazo: quem cobra é a varredura de hoje
        try:
            linhas = _linhas_do_job(client, job, ontem)
        except Exception as exc:
            raise SupervisorCego(f"consulta ao destino de {job.id} falhou: {exc}") from exc
        diag = _classificar(job, ontem, agora, batidas.get(job.id), linhas)
        if diag.estado in _PROBLEMAS:
            diag.detalhe = f"[ontem, {ontem:%d/%m}] {diag.detalhe}"
            pendentes.append(diag)
    return pendentes


def varrer(
    dia: Optional[date] = None,
    agora: Optional[datetime] = None,
    checar_degradacao: bool = True,
    incluir_ontem: bool = False,
) -> Tuple[List[Diagnostico], List[Degradacao]]:
    """Executa a varredura completa.

    Args:
        dia: Dia BRT avaliado (default: hoje).
        agora: Instante de referência (default: agora BRT) — injetável em teste.
        checar_degradacao: Se False, pula a busca por plataformas cronicamente
            zeradas (a parte cara da varredura).
        incluir_ontem: Também cobra os jobs de ontem cujo deadline venceu depois
            da meia-noite (ver `_pendencias_de_ontem`).

    Returns:
        (diagnósticos por job, degradações por plataforma).

    Raises:
        SupervisorCego: Supabase indisponível ou livro-razão ilegível.
    """
    agora = agora or now_brt()
    dia = dia or agora.date()

    client = _client()
    jobs = jobs_do_dia(dia)
    try:
        batidas = ultimas_batidas(dia, [j.id for j in jobs])
    except LivroRazaoIndisponivel as exc:
        # Livro ilegível ≠ livro vazio. Tratar como vazio faria uma queda do
        # banco ser relatada como "ninguém rodou" (falso alarme em massa) ou,
        # pior, como execução saudável quando houvesse dado de outro executor.
        raise SupervisorCego(str(exc)) from exc

    diagnosticos: List[Diagnostico] = []
    for job in jobs:
        try:
            linhas = _linhas_do_job(client, job, dia)
        except Exception as exc:
            raise SupervisorCego(f"consulta ao destino de {job.id} falhou: {exc}") from exc
        diagnosticos.append(_classificar(job, dia, agora, batidas.get(job.id), linhas))

    diagnosticos = _colapsar_executores(diagnosticos)

    if incluir_ontem:
        diagnosticos.extend(_pendencias_de_ontem(client, dia - timedelta(days=1), agora))

    degradacoes: List[Degradacao] = []
    if checar_degradacao:
        ontem = dia - timedelta(days=1)
        vistas: set = set()
        for job in jobs:
            for plataforma in job.plataformas:
                if plataforma in vistas:
                    continue
                vistas.add(plataforma)
                try:
                    dias, truncado = _dias_zerados(client, plataforma, ontem)
                except Exception as exc:
                    logger.warning(f"[Supervisor] Degradação de {plataforma}: {exc}")
                    continue
                if dias:
                    degradacoes.append(Degradacao(plataforma, dias, truncado, job.id))

    return diagnosticos, degradacoes


def varrer_quedas(
    dia: date, degradacoes: Optional[List[Degradacao]] = None
) -> List[QuedaVolume]:
    """Plataformas com queda brusca de volume (ver `utils/volume_drop.py`).

    Atalho de `varrer_quedas_avaliado` para quem só quer a lista.
    """
    return varrer_quedas_avaliado(dia, degradacoes)[0]


def varrer_quedas_avaliado(
    dia: date, degradacoes: Optional[List[Degradacao]] = None
) -> Tuple[List[QuedaVolume], bool]:
    """Plataformas com queda brusca de volume (ver `utils/volume_drop.py`).

    Lê o `detail` das batidas dos jobs de coleta (uma consulta para os últimos
    `DIAS_HISTORICO` dias) e compara cada turno com a mediana do mesmo turno.

    É uma checagem ADICIONAL: se falhar, registra um aviso e devolve lista
    vazia, sem cegar o supervisor inteiro. Os vereditos de execução valem por
    si e não podem se perder por causa dela.

    Args:
        dia: Dia avaliado (o dia corrente entra a partir de 2 turnos fechados).
        degradacoes: Plataformas zeradas há dias. Só as CRÔNICAS saem daqui
            (o mesmo buraco não gera dois alertas), e a evidência da queda
            passa para elas. A zerada de 1–2 dias FICA: ela não aparece no
            Telegram nem no exit code, então tirá-la daqui deixava o buraco
            sem alerta nenhum até o 3º dia (ML de 25–27/10/2026, sessão
            expirada, só pego pelo briefing).

    Returns:
        (quedas, avaliado). Quedas vêm com a pior sequência primeiro;
        ``avaliado`` é False quando a checagem falhou ou não leu nenhuma
        batida — lista vazia por falta de dado não é "volume normal", e o
        `pipeline_guard.yml` não pode fechar as issues de volume degradado
        por causa dela.
    """
    jobs = [
        JobDeColeta(j.id, j.turno, tuple(j.plataformas))
        for j in JOBS
        if j.destino.startswith("coletas") and j.turno and j.plataformas
    ]
    if not jobs:
        return [], False
    try:
        batidas = batidas_periodo(
            dia - timedelta(days=DIAS_HISTORICO), dia, [j.id for j in jobs]
        )
        quedas = classificar_quedas(
            observacoes_de_batidas(batidas, jobs, MARKETPLACES), dia
        )
    except Exception as exc:
        logger.warning(f"[Supervisor] Checagem de queda de volume falhou: {exc}")
        return [], False

    cronicas = {g.plataforma: g for g in (degradacoes or []) if g.cronica}
    for queda in quedas:
        deg = cronicas.get(queda.plataforma)
        if deg is not None and deg.evidencia is None:
            deg.evidencia = queda.evidencia
    # Nenhuma batida lida (migração 015 ausente, livro-razão vazio) não é
    # prova de volume normal: sem dado, a checagem não foi de fato avaliada.
    return [q for q in quedas if q.plataforma not in cronicas], bool(batidas)


def varrer_fontes_bestsellers(dia: date) -> Optional[RelatorioCobertura]:
    """Fontes de Mais Vendidos que pararam (ver `bestsellers/cobertura.py`).

    Lê só o topo de cada lista (``rank <= 3``) — basta para saber se a fonte
    gravou no dia, e mantém a leitura em poucas centenas de linhas. Fonte sem
    linha na janela ganha uma consulta própria pelo último dia da história,
    para separar "parou há mais de um mês" de "nunca coletou".

    Checagem ADICIONAL, como a de volume: falha vira aviso e None, sem cegar
    o supervisor.

    Args:
        dia: Último dia avaliado (inclusive).

    Returns:
        O relatório, ou None quando não deu para checar.
    """
    try:
        from bestsellers.config import sources_ativos

        fontes = {k: spec.nome for k, spec in sources_ativos().items()}
        client = _client()
        inicio = (dia - timedelta(days=JANELA_FONTES_BS)).isoformat()
        leituras: List[Tuple[date, str]] = []
        passo = 1000
        for offset in range(0, 50_000, passo):
            resp = (
                client.table("bestsellers")
                .select("data,plataforma")
                .gte("data", inicio)
                .lte("data", dia.isoformat())
                .lte("rank", 3)
                .order("data")
                .order("id")  # chave total: paginação sem pular nem repetir
                .range(offset, offset + passo - 1)
                .execute()
            )
            lote = resp.data or []
            leituras.extend(
                (date.fromisoformat(str(r["data"])[:10]), str(r["plataforma"]))
                for r in lote
                if r.get("data") and r.get("plataforma")
            )
            if len(lote) < passo:
                break

        presentes = {p for _, p in leituras}
        ultimos: Dict[str, Optional[date]] = {}
        for chave in fontes:
            if chave in presentes:
                continue
            resp = (
                client.table("bestsellers")
                .select("data")
                .eq("plataforma", chave)
                .lte("data", dia.isoformat())
                .order("data", desc=True)
                .limit(1)
                .execute()
            )
            linhas = resp.data or []
            ultimos[chave] = (
                date.fromisoformat(str(linhas[0]["data"])[:10]) if linhas else None
            )
        return classificar_fontes(leituras, fontes, dia, ultimos)
    except Exception as exc:
        logger.warning(f"[Supervisor] Checagem de fontes de Mais Vendidos falhou: {exc}")
        return None


def _descrever_fonte(fonte: FonteMuda) -> str:
    """``Leroy Merlin: 14 dia(s) de coleta sem a lista (último dado 07/10)``."""
    ultimo = f"{fonte.ultimo_dia:%d/%m}" if fonte.ultimo_dia else "?"
    # Último dado antes da janela: a contagem é só da janela, um piso.
    qtd = f"≥ {fonte.dias_sem_dado}" if fonte.minimo else str(fonte.dias_sem_dado)
    return (
        f"{fonte.nome}: {qtd} dia(s) de coleta sem a lista "
        f"(último dado {ultimo})"
    )


def _descrever_queda(queda: QuedaVolume) -> str:
    """``Abertura 68 (1% de 5.012), Tarde 0 (0% de 4.870)``."""
    return ", ".join(
        f"{t.turno} {t.linhas:,} ({t.percentual:.0f}% de {t.mediana:,.0f})".replace(",", ".")
        for t in queda.turnos
    )


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------

def imprimir(
    diagnosticos: List[Diagnostico],
    degradacoes: List[Degradacao],
    dia: date,
    quedas: Optional[List[QuedaVolume]] = None,
    fontes_bs: Optional[RelatorioCobertura] = None,
) -> None:
    """Imprime o relatório no terminal."""
    linha = "=" * 72
    print(f"\n{linha}")
    print(f"  SUPERVISOR DE EXECUÇÃO — {dia:%d/%m/%Y}")
    print(linha)

    por_executor: Dict[str, List[Diagnostico]] = {}
    for diag in diagnosticos:
        por_executor.setdefault(diag.job.executor_nome, []).append(diag)

    for executor, itens in por_executor.items():
        print(f"\n▸ {executor}")
        for diag in itens:
            print(f"   {_ICONE.get(diag.estado, '?')} {diag.job.nome:<42} {diag.estado}")
            print(f"      {diag.detalhe}")
            if diag.estado in _PROBLEMAS and diag.job.remediacao:
                print(f"      ↳ {diag.job.remediacao}")

    if degradacoes:
        print("\n▸ Plataformas sem dado (dias consecutivos)")
        for deg in sorted(degradacoes, key=lambda d: -d.dias):
            marcador = "🔴 CRÔNICO" if deg.cronica else "🟡"
            sufixo = "+" if deg.truncado else ""
            print(f"   {marcador} {deg.plataforma:<24} {deg.dias}{sufixo} dia(s) zerada")
            if deg.evidencia:
                print(f"      ↳ {deg.evidencia}")

    if quedas:
        print(f"\n▸ Plataformas DEGRADADAS (< {FRACAO_QUEDA:.0%} da mediana em ≥2 turnos)")
        for queda in quedas:
            marcador = "🔴 CRÔNICO" if queda.cronica else "🟠"
            print(
                f"   {marcador} {queda.plataforma:<24} {queda.dias} dia(s) — "
                f"{queda.dia:%d/%m}: {_descrever_queda(queda)}"
            )
            if queda.evidencia:
                print(f"      ↳ {queda.evidencia}")

    if fontes_bs is not None and (fontes_bs.mudas or fontes_bs.nunca_coletaram):
        print("\n▸ Mais Vendidos — fontes fora da coleta")
        for fonte in fontes_bs.mudas:
            marcador = "🔴 CRÔNICO" if fonte.cronica else "🟠"
            print(f"   {marcador} {_descrever_fonte(fonte)}")
        if fontes_bs.nunca_coletaram:
            print(f"   ⚪ nunca coletaram: {', '.join(fontes_bs.nunca_coletaram)}")

    print(f"\n{linha}\n")


def _formatar_telegram(
    diagnosticos: List[Diagnostico],
    degradacoes: List[Degradacao],
    dia: date,
    quedas: Optional[List[QuedaVolume]] = None,
    fontes_mudas: Optional[List[FonteMuda]] = None,
) -> str:
    """Monta o alerta. Curto e acionável: o que quebrou e o comando que resolve."""
    problemas = [d for d in diagnosticos if d.estado in _PROBLEMAS]
    atrasos = [d for d in diagnosticos if d.estado == ATRASADO]
    cronicas = [g for g in degradacoes if g.cronica]
    quedas = quedas or []
    fontes_mudas = fontes_mudas or []

    if problemas or cronicas or any(q.cronica for q in quedas):
        cabecalho = "🚨 <b>Pipeline RAC — falha de execução</b>"
    elif quedas:
        cabecalho = "🟠 <b>Pipeline RAC — queda de volume</b>"
    elif fontes_mudas:
        cabecalho = "🟠 <b>Pipeline RAC — fonte de Mais Vendidos parada</b>"
    elif atrasos:
        cabecalho = "🟡 <b>Pipeline RAC — atrasos</b>"
    else:
        cabecalho = "✅ <b>Pipeline RAC — tudo no contrato</b>"

    linhas = [cabecalho, f"📅 {dia:%d/%m/%Y}", ""]

    for diag in problemas:
        linhas.append(
            f"{_ICONE.get(diag.estado, '?')} <b>{html.escape(diag.job.nome)}</b> "
            f"({html.escape(diag.job.executor_nome)})"
        )
        linhas.append(f"   {html.escape(diag.detalhe)}")
        if diag.job.remediacao:
            linhas.append(f"   ↳ <code>{html.escape(diag.job.remediacao)}</code>")
        linhas.append("")

    if atrasos:
        nomes = ", ".join(html.escape(d.job.nome) for d in atrasos)
        linhas.append(f"🟡 <b>Atrasados:</b> {nomes}")
        linhas.append("")

    if cronicas:
        linhas.append("🩺 <b>Degradação crônica</b> (nenhuma linha há dias)")
        for deg in sorted(cronicas, key=lambda d: -d.dias):
            sufixo = "+" if deg.truncado else ""
            linhas.append(
                f"   • {html.escape(deg.plataforma)}: <b>{deg.dias}{sufixo} dias</b> zerada"
            )
            if deg.evidencia:
                linhas.append(f"      ↳ <code>{html.escape(deg.evidencia)}</code>")
        linhas.append("")

    if quedas:
        linhas.append(
            f"📉 <b>Plataforma DEGRADADA</b> (&lt; {FRACAO_QUEDA:.0%} da mediana "
            "em ≥2 turnos)"
        )
        for queda in quedas:
            marcador = "🔴" if queda.cronica else "🟠"
            linhas.append(
                f"   {marcador} {html.escape(queda.plataforma)}: "
                f"<b>{queda.dias} dia(s)</b> — {queda.dia:%d/%m}: "
                f"{html.escape(_descrever_queda(queda))}"
            )
            if queda.evidencia:
                linhas.append(f"      ↳ <code>{html.escape(queda.evidencia)}</code>")
        linhas.append("")

    if fontes_mudas:
        linhas.append("🥇 <b>Mais Vendidos — lista parada</b> (as outras seguem gravando)")
        for fonte in fontes_mudas:
            marcador = "🔴" if fonte.cronica else "🟠"
            linhas.append(f"   {marcador} {html.escape(_descrever_fonte(fonte))}")
        linhas.append(
            "   ↳ <code>python scripts/collect_bestsellers.py --plataformas "
            + html.escape(" ".join(f.plataforma for f in fontes_mudas))
            + " --no-headless</code> (o log diz por que a lista veio vazia)"
        )
        linhas.append("")

    if not problemas and not atrasos and not cronicas and not quedas and not fontes_mudas:
        ok = sum(1 for d in diagnosticos if d.estado == OK)
        linhas.append(f"{ok} job(s) executados dentro do contrato.")

    return "\n".join(linhas).strip()


def _enviar_telegram(mensagem: str) -> bool:
    """Envia o alerta pela Bot API.

    Envio direto, sem passar pelo n8n: o alerta que diz "a pipeline caiu" não
    pode depender de mais um serviço da própria pipeline.

    Returns:
        True se o Telegram aceitou a mensagem.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("N8N_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning(
            "[Supervisor] TELEGRAM_BOT_TOKEN/N8N_TELEGRAM_CHAT_ID ausentes — "
            "alerta não enviado."
        )
        return False
    try:
        import requests

        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "parse_mode": "HTML", "text": mensagem},
            timeout=30,
        )
        if resp.status_code == 200:
            logger.success("[Supervisor] Alerta enviado.")
            return True
        logger.error(f"[Supervisor] Telegram HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        logger.error(f"[Supervisor] Falha ao enviar alerta: {exc}")
    return False


def montar_json(
    diagnosticos: List[Diagnostico],
    degradacoes: List[Degradacao],
    dia: date,
    agora: datetime,
    quedas: Optional[List[QuedaVolume]] = None,
    quedas_avaliadas: bool = True,
    fontes_bs: Optional[RelatorioCobertura] = None,
) -> Dict[str, Any]:
    """Estado da pipeline em forma de dado.

    É o que `scripts/briefing_gate.py` lê e o que o dashboard pode exibir —
    para que "a pipeline está saudável" deixe de ser opinião.

    ``quedas_volume_avaliadas`` separa "nenhuma queda" de "não deu para
    checar" (falha ou `--sem-queda-volume`): o `pipeline_guard.yml` só fecha
    issue de volume degradado quando a checagem de fato rodou.
    """
    problemas = [d for d in diagnosticos if d.estado in _PROBLEMAS]
    return {
        "gerado_em": agora.isoformat(),
        "data_referencia": dia.isoformat(),
        "saudavel": not problemas,
        "criticos": sum(1 for d in problemas if d.critico),
        "jobs": [d.to_dict() for d in diagnosticos],
        "degradacoes": [g.to_dict() for g in degradacoes],
        "quedas_volume": [q.to_dict() for q in (quedas or [])],
        "quedas_volume_avaliadas": quedas_avaliadas,
        # None = não avaliado (falha ou --sem-fontes-bestsellers), nunca "ok".
        "bestsellers_fontes": fontes_bs.to_dict() if fontes_bs is not None else None,
    }


def _exit_code(
    diagnosticos: List[Diagnostico],
    degradacoes: List[Degradacao],
    quedas: Optional[List[QuedaVolume]] = None,
    fontes_mudas: Optional[List[FonteMuda]] = None,
) -> int:
    """Traduz o relatório em exit code (ver docstring do módulo)."""
    problemas = [d for d in diagnosticos if d.estado in _PROBLEMAS]
    quedas = quedas or []
    fontes_mudas = fontes_mudas or []
    if any(d.critico for d in problemas):
        return 1
    # Fonte de Mais Vendidos parada fica em 2 mesmo crônica: o job é
    # SEV_IMPORTANTE, e exit 1 pinta o pipeline_guard de vermelho todo dia —
    # "run vermelho todo dia é run que ninguém abre". O Telegram sai igual.
    if any(g.cronica for g in degradacoes) or any(q.cronica for q in quedas):
        return 1
    if (
        problemas or quedas or fontes_mudas
        or any(d.estado == ATRASADO for d in diagnosticos)
    ):
        return 2
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Ponto de entrada da CLI."""
    parser = argparse.ArgumentParser(
        description="Supervisor de execução da pipeline RAC.",
    )
    parser.add_argument("--data", default=None, help="dia a avaliar (YYYY-MM-DD, default: hoje BRT)")
    parser.add_argument("--no-notify", action="store_true", help="não envia Telegram")
    parser.add_argument(
        "--sempre-notificar",
        action="store_true",
        help="envia o resumo mesmo quando está tudo certo (útil para provar que o vigia está vivo)",
    )
    parser.add_argument("--json", dest="json_path", default=None, help="grava o estado neste arquivo")
    parser.add_argument(
        "--tambem-ontem",
        action="store_true",
        help=(
            "também cobra os jobs de ontem cujo deadline venceu depois da "
            "meia-noite (o Fechamento das 21:00 vence às 02:00)"
        ),
    )
    parser.add_argument(
        "--sem-degradacao",
        action="store_true",
        help="pula a varredura de plataformas cronicamente zeradas (mais rápido)",
    )
    parser.add_argument(
        "--sem-queda-volume",
        action="store_true",
        help="pula a checagem de queda brusca de volume por plataforma",
    )
    parser.add_argument(
        "--sem-fontes-bestsellers",
        action="store_true",
        help="pula a cobrança fonte a fonte das listas de Mais Vendidos",
    )
    args = parser.parse_args(argv)

    agora = now_brt()
    dia = datetime.strptime(args.data, "%Y-%m-%d").date() if args.data else agora.date()

    try:
        diagnosticos, degradacoes = varrer(
            dia,
            agora,
            checar_degradacao=not args.sem_degradacao,
            incluir_ontem=args.tambem_ontem,
        )
    except SupervisorCego as exc:
        logger.error(f"[Supervisor] CEGO: {exc}")
        if not args.no_notify:
            _enviar_telegram(
                "⚠️ <b>Supervisor da pipeline CEGO</b>\n"
                f"{html.escape(str(exc))}\n\n"
                "Isto NÃO significa que a coleta caiu — significa que ninguém "
                "está conseguindo verificar se ela rodou."
            )
        return 3

    if args.sem_queda_volume:
        quedas, quedas_avaliadas = [], False
    else:
        quedas, quedas_avaliadas = varrer_quedas_avaliado(dia, degradacoes)

    fontes_bs = None if args.sem_fontes_bestsellers else varrer_fontes_bestsellers(dia)
    fontes_mudas = fontes_bs.mudas if fontes_bs is not None else []

    imprimir(diagnosticos, degradacoes, dia, quedas, fontes_bs)

    if args.json_path:
        caminho = Path(args.json_path)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(
            json.dumps(
                montar_json(diagnosticos, degradacoes, dia, agora, quedas,
                            quedas_avaliadas, fontes_bs),
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(f"[Supervisor] Estado gravado em {caminho}")

    codigo = _exit_code(diagnosticos, degradacoes, quedas, fontes_mudas)
    if not args.no_notify and (codigo != 0 or args.sempre_notificar):
        _enviar_telegram(
            _formatar_telegram(diagnosticos, degradacoes, dia, quedas, fontes_mudas)
        )

    return codigo


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
