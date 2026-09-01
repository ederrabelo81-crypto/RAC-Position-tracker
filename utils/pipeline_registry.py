"""
utils/pipeline_registry.py — Inventário canônico dos touch points de dado.

Este módulo responde, em código e num lugar só, à pergunta que estava
espalhada por seis arquivos de agendamento, três workflows e a cabeça do
analista: **o que precisa rodar, onde, a que horas, trazendo o quê, e o que
fazer quando não roda.**

Por que existe
--------------
Em agosto/2026 três falhas simultâneas passaram despercebidas por dias, e as
três compartilhavam a mesma causa estrutural — não havia contrato explícito de
execução:

* O import do PriceTrack (cron 09:00 UTC) começou às 19:18 UTC de 27/08. O
  briefing das 07:00 BRT leu preço de D-2 sem que nada avisasse: o job não
  falhou, ele simplesmente ainda não tinha rodado. Cron do GitHub Actions é
  *best effort* — a série de agosto mostra atrasos rotineiros de 15min a 3h e,
  naquele dia, de mais de 10h.
* Google Shopping vem zerado há ~1 mês com o `collect.yml` verde em todos os
  runs, porque plataforma sem resultado não derruba o job.
* A coleta da VM Oracle parou e nenhum arquivo do repositório sabia que aquela
  máquina existia.

Com o registro, "esperado" deixa de ser folclore e vira dado: o supervisor
(`scripts/pipeline_watch.py`) compara esperado × observado, o portão do
briefing (`scripts/briefing_gate.py`) sabe de quem depende, e a documentação
(`docs/MAPA_COLETAS.md`) é gerada da mesma fonte em vez de divergir dela.

Regra dura
----------
Toda plataforma coletada precisa estar em EXATAMENTE UM job como `dona`
(``plataformas``) e pode aparecer em quantos quiser como redundância
(``plataformas_redundantes``). Sem isso duas coisas quebram em silêncio: a
plataforma sem dono nunca é cobrada de ninguém (foi o caso dos dealers), e a
plataforma com dois donos gera alerta duplicado todo dia até o alerta virar
ruído. ``validar_registro()`` falha alto se a regra for violada, e o teste
``tests/test_pipeline_registry.py`` roda essa validação no CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Executores — QUEM roda. Cada um falha de um jeito diferente e se conserta
# de um jeito diferente; misturá-los num alerta só foi o que fez o watchdog
# gritar "4 plataformas críticas caíram" todo fim de semana em que o notebook
# ficava desligado.
# ---------------------------------------------------------------------------

EXEC_ACTIONS = "github_actions"
EXEC_VM = "oracle_vm"
EXEC_LOCAL = "pc_local"
EXEC_EXTERNO = "externo"


@dataclass(frozen=True)
class Executor:
    """Uma máquina (ou serviço) que executa jobs da pipeline.

    Attributes:
        id: Identificador estável usado no heartbeat.
        nome: Nome legível para alertas e documentação.
        onde_agendar: Onde o agendamento vive de fato.
        diagnostico: Comando que o analista roda para ver o que houve.
        ip: Natureza do IP — decide o que a máquina consegue coletar.
    """

    id: str
    nome: str
    onde_agendar: str
    diagnostico: str
    ip: str


EXECUTORES: Dict[str, Executor] = {
    EXEC_ACTIONS: Executor(
        id=EXEC_ACTIONS,
        nome="GitHub Actions",
        onde_agendar=".github/workflows/*.yml (bloco `on: schedule`)",
        diagnostico="Aba Actions do repositório → workflow → último run",
        ip="datacenter (bloqueado por ML, Magalu, Shopee e Casas Bahia)",
    ),
    EXEC_VM: Executor(
        id=EXEC_VM,
        nome="VM Oracle (Brazil East)",
        onde_agendar="crontab do usuário ubuntu (scripts/oracle_setup.sh)",
        diagnostico="ssh ubuntu@<vm> 'crontab -l; tail -50 ~/rac-position-tracker/logs/cron.log'",
        ip="datacenter BR (bloqueado por ML; Casas Bahia destrava com warm-up)",
    ),
    EXEC_LOCAL: Executor(
        id=EXEC_LOCAL,
        nome="PC coletor (Windows, IP residencial)",
        onde_agendar="Task Scheduler (scripts/setup_local_scheduler.ps1)",
        diagnostico="PowerShell -ExecutionPolicy Bypass -File scripts\\check_local_scheduler.ps1",
        ip="residencial + Chrome logado (único caminho para ML/Magalu/Shopee)",
    ),
    EXEC_EXTERNO: Executor(
        id=EXEC_EXTERNO,
        nome="Consumidor externo",
        onde_agendar="fora deste repositório",
        diagnostico="—",
        ip="—",
    ),
}


# ---------------------------------------------------------------------------
# Severidade — o que o alerta faz com a falha
# ---------------------------------------------------------------------------

#: Falha derruba o exit code do supervisor e vira alerta vermelho.
SEV_CRITICO = "critico"
#: Falha entra no relatório como aviso; só escala virando crônica.
SEV_IMPORTANTE = "importante"
#: Best-effort conhecido (ex.: Shopee sem proxy BR). Nunca vira vermelho sozinho.
SEV_TOLERANTE = "tolerante"


@dataclass(frozen=True)
class JobSpec:
    """Contrato de execução de um job da pipeline.

    Attributes:
        id: Identificador estável — é a chave do heartbeat, nunca renomeie sem
            migrar `pipeline_heartbeat.job_id`.
        nome: Nome legível.
        executor: Id em ``EXECUTORES``.
        gatilho: Arquivo/tarefa que dispara o job (workflow, cron, task).
        comando: O que ele executa, para o analista reproduzir na mão.
        horario_brt: (hora, minuto) BRT em que o job DEVERIA começar.
        dias: Dias da semana em que é esperado (0=segunda … 6=domingo).
        tolerancia_min: Minutos de atraso aceitos antes de reportar ATRASADO.
        deadline_min: Minutos após o horário em que a ausência vira NÃO
            EXECUTOU. Precisa caber antes de quem consome o dado — o do
            PriceTrack é curto de propósito: o briefing das 07:00 depende dele.
        destino: Tabela/artefato onde o dado deve aparecer.
        turno: Turno gravado em `coletas.turno` por este job ("Abertura" /
            "Fechamento"), ou None para jobs que não escrevem em `coletas`. A
            posse de uma plataforma é por (plataforma, turno): a mesma Amazon
            tem dono de manhã e dono à noite, e um buraco só de manhã precisa
            cobrar exatamente um job.
        plataformas: Plataformas de que este job é DONO (cobradas dele).
        plataformas_redundantes: Plataformas que ele também coleta, mas cuja
            ausência é cobrada de outro job.
        severidade: Ver SEV_*.
        remediacao: A frase que o alerta imprime — o comando exato, não "veja
            os logs".
        auto_heal: Comando idempotente que `scripts/pipeline_heal.py` pode
            executar sozinho. None = só humano resolve (ex.: VM desligada).
        workflow: Arquivo do workflow no Actions, quando o job é de lá. É o que
            `pipeline_heal.py` re-dispara — e só consegue com um PAT: eventos
            disparados pelo `GITHUB_TOKEN` do próprio run não criam novos runs
            (proteção anti-recursão do GitHub), então a cura por dispatch é
            opcional por desenho, nunca o único caminho.
        depende_de: Jobs cujo dado este job consome.
        observacao: Contexto que evita que alguém "conserte" o desenho errado.
    """

    id: str
    nome: str
    executor: str
    gatilho: str
    comando: str
    horario_brt: Tuple[int, int]
    dias: Tuple[int, ...]
    tolerancia_min: int
    deadline_min: int
    destino: str
    turno: Optional[str] = None
    plataformas: Tuple[str, ...] = ()
    plataformas_redundantes: Tuple[str, ...] = ()
    severidade: str = SEV_IMPORTANTE
    remediacao: str = ""
    auto_heal: Optional[str] = None
    workflow: Optional[str] = None
    depende_de: Tuple[str, ...] = ()
    observacao: str = ""

    @property
    def executor_nome(self) -> str:
        """Nome legível do executor (fallback para o id, se desconhecido)."""
        ex = EXECUTORES.get(self.executor)
        return ex.nome if ex else self.executor

    def esperado_em(self, dia: date) -> bool:
        """True se o job deveria rodar no dia informado."""
        return dia.weekday() in self.dias

    def inicio_esperado(self, dia: date) -> datetime:
        """Instante BRT em que o job deveria começar naquele dia."""
        hora, minuto = self.horario_brt
        return datetime(dia.year, dia.month, dia.day, hora, minuto)

    def limite_atraso(self, dia: date) -> datetime:
        """A partir daqui o job está ATRASADO."""
        return self.inicio_esperado(dia) + timedelta(minutes=self.tolerancia_min)

    def deadline(self, dia: date) -> datetime:
        """A partir daqui a ausência de batida é NÃO EXECUTOU."""
        return self.inicio_esperado(dia) + timedelta(minutes=self.deadline_min)


TODOS_OS_DIAS: Tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
DIAS_UTEIS: Tuple[int, ...] = (0, 1, 2, 3, 4)


# ---------------------------------------------------------------------------
# O registro
# ---------------------------------------------------------------------------
# Divisão de trabalho (o "porquê" de cada linha está em docs/MAPA_COLETAS.md):
#
#   PC local  → coletor ÚNICO de OFERTA/POSIÇÃO desde Set/2026. Roda TODAS as
#               plataformas (ML, Amazon, Magalu, Casas Bahia, Google Shopping,
#               Leroy, Shopee e os dealers) em três turnos: 8h (Abertura),
#               14h (Tarde) e 20h (Fechamento). É o único caminho com IP
#               residencial e Chrome logado, que ML/Magalu/Shopee exigem — e
#               concentrar tudo nele evita o dado fantasma que a coleta de
#               marketplace em IP de datacenter produzia (0 linha com cara de
#               sucesso).
#   Actions   → NÃO coleta mais marketplace (o cron do collect.yml foi
#               desligado). Segue dono apenas do import do PriceTrack e do
#               watchdog de dado.
#   VM Oracle → desativada como coletora (o cron foi removido). O executor
#               permanece documentado em EXECUTORES caso volte a ser usado.
#
# Um turno = um dono. Cada (plataforma, turno) tem exatamente um job cobrado,
# e os três turnos locais não se sobrepõem, então não há redundância a
# declarar aqui — a resiliência agora vem dos três horários do próprio PC e do
# gatilho de catch-up no logon, não de uma segunda máquina.
# ---------------------------------------------------------------------------

JOBS: Tuple[JobSpec, ...] = (
    # ── GitHub Actions ─────────────────────────────────────────────────────
    JobSpec(
        id="gh_pricetrack_d1",
        nome="Import PriceTrack D-1 → Supabase",
        executor=EXEC_ACTIONS,
        workflow="pricetrack_daily.yml",
        gatilho=".github/workflows/pricetrack_daily.yml",
        comando="python scripts/pricetrack_api_import.py --start D-1 --end D-1",
        horario_brt=(3, 20),
        dias=TODOS_OS_DIAS,
        # Tolerância larga porque o cron do Actions atrasa por desenho; o
        # deadline é o que importa e ele é CURTO: 06:30 BRT, meia hora antes
        # do briefing. Foi exatamente esta folga que faltou em 27/08.
        tolerancia_min=90,
        deadline_min=190,
        destino="pricetrack_daily",
        severidade=SEV_CRITICO,
        remediacao=(
            "Actions → PriceTrack Daily Import → Run workflow "
            "(ou: python scripts/pricetrack_api_import.py --start D-1 --end D-1 --force)"
        ),
        auto_heal="python scripts/pricetrack_api_import.py --start {d1} --end {d1}",
        observacao=(
            "O briefing das 07:00 BRT lê D-1 desta tabela. Cron do Actions é "
            "best effort: por isso a escada de 3 tentativas (03:20/04:20/05:20) "
            "e o portão pré-briefing às 06:30."
        ),
    ),
    JobSpec(
        id="gh_watchdog",
        nome="Watchdog diário (daily_status_check)",
        executor=EXEC_ACTIONS,
        workflow="watchdog.yml",
        gatilho=".github/workflows/watchdog.yml (cron 23:30 UTC)",
        comando="python scripts/daily_status_check.py",
        horario_brt=(20, 30),
        dias=TODOS_OS_DIAS,
        tolerancia_min=120,
        deadline_min=300,
        destino="alerta Telegram",
        severidade=SEV_IMPORTANTE,
        remediacao="Actions → Watchdog de Coleta → Run workflow",
        observacao=(
            "O vigia também bate ponto: um monitor que morre em silêncio é "
            "pior que monitor nenhum, porque ninguém desconfia do silêncio."
        ),
    ),
    # ── PC local (Windows) — coletor único de oferta/posição ───────────────
    # As três coletas do dia rodam a MESMA varredura completa (todas as
    # plataformas, todas as keywords, 2 páginas). Elas se distinguem só pelo
    # turno gravado, o que dá três fotos por dia da mesma competição de buy box.
    JobSpec(
        id="local_manha",
        nome="Coleta local — turno Abertura (08:00)",
        executor=EXEC_LOCAL,
        gatilho="Task Scheduler: RAC_Local_Manha (08:00 + catch-up no logon)",
        comando="scripts\\run_local_scheduled.bat manha",
        horario_brt=(8, 0),
        dias=TODOS_OS_DIAS,
        # Janela larga: a tarefa tem gatilho de logon justamente porque o
        # notebook pode estar desligado às 08:00. O deadline (11:00) fecha junto
        # com a janela do próprio local_scheduled_collect.bat (8–11h).
        tolerancia_min=120,
        deadline_min=180,
        destino="coletas (turno Abertura)",
        turno="Abertura",
        plataformas=(
            "Mercado Livre", "Amazon", "Magalu", "Casas Bahia",
            "Google Shopping", "Leroy Merlin", "Shopee", "dealers",
        ),
        severidade=SEV_CRITICO,
        remediacao=(
            "PowerShell -ExecutionPolicy Bypass -File scripts\\check_local_scheduler.ps1 "
            "(sessão vencida? python scripts/setup_local_profile.py --site magalu)"
        ),
        observacao=(
            "PC coletor é o ÚNICO dono de oferta/posição desde Set/2026. "
            "Notebook desligado é causa legítima e frequente: o supervisor "
            "reporta 'executor offline' (um alerta), não N plataformas críticas "
            "caídas (N alertas idênticos que ninguém mais lê)."
        ),
    ),
    JobSpec(
        id="local_tarde",
        nome="Coleta local — turno Tarde (14:00)",
        executor=EXEC_LOCAL,
        gatilho="Task Scheduler: RAC_Local_Tarde (14:00 + catch-up no logon)",
        comando="scripts\\run_local_scheduled.bat tarde",
        horario_brt=(14, 0),
        dias=TODOS_OS_DIAS,
        # Janela 12–17h (ver local_scheduled_collect.bat). Deadline às 17:00.
        tolerancia_min=120,
        deadline_min=180,
        destino="coletas (turno Tarde)",
        turno="Tarde",
        plataformas=(
            "Mercado Livre", "Amazon", "Magalu", "Casas Bahia",
            "Google Shopping", "Leroy Merlin", "Shopee", "dealers",
        ),
        severidade=SEV_IMPORTANTE,
        remediacao="PowerShell -ExecutionPolicy Bypass -File scripts\\check_local_scheduler.ps1",
    ),
    JobSpec(
        id="local_noite",
        nome="Coleta local — turno Fechamento (20:00)",
        executor=EXEC_LOCAL,
        gatilho="Task Scheduler: RAC_Local_Noite (20:00 + catch-up no logon)",
        comando="scripts\\run_local_scheduled.bat noite",
        horario_brt=(20, 0),
        dias=TODOS_OS_DIAS,
        tolerancia_min=120,
        deadline_min=180,
        destino="coletas (turno Fechamento)",
        turno="Fechamento",
        plataformas=(
            "Mercado Livre", "Amazon", "Magalu", "Casas Bahia",
            "Google Shopping", "Leroy Merlin", "Shopee", "dealers",
        ),
        severidade=SEV_IMPORTANTE,
        remediacao="PowerShell -ExecutionPolicy Bypass -File scripts\\check_local_scheduler.ps1",
    ),
    # ── Consumidor externo ─────────────────────────────────────────────────
    JobSpec(
        id="briefing_0700",
        nome="Briefing das 07:00 (cenário de preço e performance)",
        executor=EXEC_EXTERNO,
        gatilho="agendamento fora deste repositório",
        comando="scripts/briefing_gate.py (portão de frescor antes de publicar)",
        horario_brt=(7, 0),
        dias=TODOS_OS_DIAS,
        tolerancia_min=60,
        deadline_min=180,
        destino="briefing publicado",
        severidade=SEV_CRITICO,
        depende_de=("gh_pricetrack_d1", "local_noite"),
        remediacao="python scripts/briefing_gate.py --json (diz se o dado de D-1 está fresco e o que falta)",
        observacao=(
            "Não é coletor: é o consumidor que sofreu o incidente de 27/08. "
            "Está no registro para que suas dependências sejam explícitas e "
            "verificáveis ANTES da publicação, e não descobertas no slide."
        ),
    ),
)


#: Índice por id — evita varrer a tupla em todo lookup.
JOBS_POR_ID: Dict[str, JobSpec] = {j.id: j for j in JOBS}

#: Plataformas conhecidas do `coletas` que NÃO são dealers. Usado para contar
#: dealers por exclusão, sem importar DEALER_CONFIGS (que arrasta Playwright).
MARKETPLACES: Tuple[str, ...] = (
    "Mercado Livre",
    "Amazon",
    "Magalu",
    "Casas Bahia",
    "Google Shopping",
    "Leroy Merlin",
    "Shopee",
    "Fast Shop",
)

#: Marcador de "o dono é o grupo de dealers", não uma plataforma nomeada.
PLATAFORMA_DEALERS = "dealers"

#: Chave usada em `config.ACTIVE_PLATFORMS` / `--platforms` → nome como o dado
#: é gravado em `coletas.plataforma`. Mora aqui (e não em
#: `scripts/daily_status_check.py`, onde nasceu) porque agora tem dois leitores:
#: o watchdog de dado e o supervisor de execução. Duas cópias divergiriam na
#: primeira plataforma nova, e a divergência seria silenciosa.
PLATFORM_KEY_TO_NOME: Dict[str, str] = {
    "ml":              "Mercado Livre",
    "amazon":          "Amazon",
    "magalu":          "Magalu",
    "google_shopping": "Google Shopping",
    "leroy":           "Leroy Merlin",
    "casasbahia":      "Casas Bahia",
    "shopee":          "Shopee",
    "fast":            "Fast Shop",
}


# ---------------------------------------------------------------------------
# Consultas sobre o registro
# ---------------------------------------------------------------------------

def jobs_do_dia(dia: date, incluir_externos: bool = False) -> List[JobSpec]:
    """Jobs esperados no dia informado.

    Args:
        dia: Dia BRT avaliado.
        incluir_externos: Se True, inclui consumidores (ex.: o briefing), que
            não batem ponto e por isso não entram na varredura padrão.

    Returns:
        Lista de jobs, na ordem do registro.
    """
    return [
        j for j in JOBS
        if j.esperado_em(dia)
        and (incluir_externos or j.executor != EXEC_EXTERNO)
    ]


def jobs_do_executor(executor: str) -> List[JobSpec]:
    """Todos os jobs de um executor, independentemente do dia."""
    return [j for j in JOBS if j.executor == executor]


def dono_da_plataforma(plataforma: str, turno: Optional[str] = None) -> Optional[JobSpec]:
    """Job cobrado quando uma plataforma some.

    Args:
        plataforma: Nome como gravado em ``coletas.plataforma``.
        turno: "Abertura"/"Fechamento". None devolve o primeiro dono
            encontrado — útil para mensagens genéricas, não para alertas.

    Returns:
        O JobSpec dono, ou None se a plataforma é órfã (o que
        ``validar_registro()`` reprova).
    """
    for job in JOBS:
        if plataforma in job.plataformas and (turno is None or job.turno == turno):
            return job
    return None


def plataformas_com_dono() -> Dict[Tuple[str, Optional[str]], str]:
    """Mapa (plataforma, turno) → id do job dono."""
    return {(p, j.turno): j.id for j in JOBS for p in j.plataformas}


def validar_registro() -> List[str]:
    """Verifica as invariantes do registro.

    Returns:
        Lista de problemas encontrados; vazia quando o registro está coerente.
        Chamado pelo teste do CI — invariante quebrada não deve chegar a
        produção, onde o sintoma seria alerta duplicado ou plataforma órfã.
    """
    problemas: List[str] = []

    vistos: Dict[str, str] = {}
    for job in JOBS:
        if job.id in vistos:
            problemas.append(f"id duplicado: {job.id}")
        vistos[job.id] = job.id

        if job.executor not in EXECUTORES:
            problemas.append(f"{job.id}: executor desconhecido '{job.executor}'")

        if job.severidade not in (SEV_CRITICO, SEV_IMPORTANTE, SEV_TOLERANTE):
            problemas.append(f"{job.id}: severidade inválida '{job.severidade}'")

        if job.deadline_min <= job.tolerancia_min:
            problemas.append(
                f"{job.id}: deadline_min ({job.deadline_min}) precisa ser maior "
                f"que tolerancia_min ({job.tolerancia_min}) — senão ATRASADO "
                "nunca é observável e todo atraso vira 'não executou'"
            )

        if not job.remediacao and job.executor != EXEC_EXTERNO:
            problemas.append(f"{job.id}: sem remediação — alerta sem saída é ruído")

        for dep in job.depende_de:
            if dep not in vistos and dep not in {j.id for j in JOBS}:
                problemas.append(f"{job.id}: depende de job inexistente '{dep}'")

        sobreposicao = set(job.plataformas) & set(job.plataformas_redundantes)
        if sobreposicao:
            problemas.append(
                f"{job.id}: plataforma em `plataformas` e `plataformas_redundantes` "
                f"ao mesmo tempo: {sorted(sobreposicao)}"
            )

    # Uma plataforma, um dono POR TURNO. A mesma Amazon é cobrada do job da
    # manhã no turno Abertura e do job da noite no Fechamento; o que não pode
    # existir é dois jobs cobrando o mesmo (plataforma, turno).
    donos: Dict[Tuple[str, Optional[str]], List[str]] = {}
    for job in JOBS:
        for plataforma in job.plataformas:
            donos.setdefault((plataforma, job.turno), []).append(job.id)
    for (plataforma, turno), ids in donos.items():
        if len(ids) > 1:
            problemas.append(
                f"plataforma '{plataforma}' (turno {turno or '—'}) tem "
                f"{len(ids)} donos ({', '.join(ids)}) — o buraco geraria um "
                "alerta por dono"
            )

    # Toda plataforma ativa em config.ACTIVE_PLATFORMS precisa de dono. É a
    # invariante que os dealers violavam: ligados no script da VM, ausentes de
    # qualquer expectativa, logo nunca cobrados de ninguém.
    try:
        from config import ACTIVE_PLATFORMS  # import tardio: evita ciclo
    except Exception:  # pragma: no cover - config sempre existe em produção
        ACTIVE_PLATFORMS = {}  # type: ignore[assignment]

    for chave, ativa in (ACTIVE_PLATFORMS or {}).items():
        if not ativa:
            continue
        nome = PLATFORM_KEY_TO_NOME.get(chave)
        com_dono = {p for p, _ in donos}
        if nome and nome not in com_dono:
            problemas.append(
                f"plataforma ativa '{nome}' não é cobrada de nenhum job — "
                "ninguém percebe quando ela some"
            )

    return problemas
