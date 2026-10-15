"""
utils/volume_drop.py — Queda brusca de volume por plataforma (lógica pura).

O supervisor (`scripts/pipeline_watch.py`) só enxergava plataforma **zerada**:
`_dias_zerados` conta dias sem NENHUMA linha e `main.py::_bater_ponto` lista
`ZERADAS:` no detail da batida. Entre 09/10 e 14/10/2026 o Mercado Livre veio
com 0–136 linhas por turno, contra ~5.000 normais. Como quase nunca chegava a
zero, nenhum dos dois disparou durante cinco dias. Uma plataforma a 1% do
volume é tão quebrada quanto uma a 0%, mas passava como saudável.

Este módulo responde à pergunta que faltava: **o turno veio muito abaixo do
normal DESTA plataforma NESTE turno?**

Fonte: o `detail` das batidas de ponto (`pipeline_heartbeat`), não `coletas`.
* É UMA consulta para a janela inteira, contra uma contagem por (dia, turno,
  plataforma) em `coletas` (~150 consultas para 7 dias).
* A batida conta o que o RUN coletou. Em `coletas`, cada turno do PC coletor
  entrou duas vezes até 30/09/2026 (`utils/run_sidecar.py`), o que inflaria a
  mediana de referência dos dias antigos e faria um dia normal parecer queda.

Regras (alinhadas a `docs/MAPA_COLETAS.md`):
* **Um dono por (plataforma, turno).** Só se avalia a plataforma no job que é
  dono dela em `utils/pipeline_registry.JOBS`. A Amazon que o PC ainda traga
  não é cobrada dele, e sim dos jobs `gh_amazon_*`.
* **Silêncio não é queda.** Turno sem batida, ou com batida que não diz a
  contagem, fica fora: quem cobra a ausência é o `NAO_EXECUTOU`. Batida
  FAILED/PARTIAL ou com 0 linhas gravadas também fica fora, porque o
  diagnóstico do job já alerta (`FALHOU`/`SEM_DADO`) e o mesmo buraco não pode
  gerar dois alertas.
* **A referência não aprende a quebra.** A mediana só usa turnos aceitos como
  normais. Senão, depois de alguns dias a 68 linhas, 68 virava o "normal" e o
  alarme se calava sozinho com o defeito ainda lá.

Puro: nada aqui toca rede nem banco (`tests/test_volume_drop.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: Fração da mediana abaixo da qual o turno conta como magro. O ML do
#: incidente ficou a 0–3% do normal. A variação legítima entre turnos (ex.:
#: keyword que some no fim de semana) não passa de ±40%.
FRACAO_QUEDA = 0.20

#: Turnos magros no mesmo dia para a plataforma ser DEGRADADA. Um turno
#: isolado pode ser só um bloqueio momentâneo que a repescagem resolve. Dois no
#: mesmo dia já são padrão.
MIN_TURNOS_MAGROS = 2

#: Quantos turnos aceitos (dias anteriores, mesmo turno) entram na mediana.
JANELA_REFERENCIA = 7

#: Abaixo disto a referência ainda não existe: plataforma nova, ou histórico
#: curto demais para dizer o que é normal.
MIN_AMOSTRAS = 3

#: Mediana mínima para avaliar. Com mediana de 10 linhas, 20% são 2: o ruído
#: de uma keyword vira alarme. Quem vive com volume tão baixo é coberto pela
#: detecção de zeradas.
MIN_MEDIANA = 50

#: Quantos dias de livro-razão buscar. Precisa cobrir a janela de referência e
#: ainda sobrar para uma quebra longa: com 7 dias de queda, os 7 anteriores
#: ainda precisam estar no alcance para a mediana não sumir.
DIAS_HISTORICO = 21

#: A partir de quantos dias seguidos a queda é crônica. É o mesmo limiar das
#: zeradas (`pipeline_watch.DIAS_PARA_CRONICO`), mantido igual de propósito.
DIAS_PARA_CRONICO = 3

#: Marcador do grupo de dealers em `JobSpec.plataformas`. Repetido aqui para o
#: módulo continuar puro (sem importar o registro).
PLATAFORMA_DEALERS = "dealers"

#: Prefixo da linha de diagnóstico de um scraper no detail → plataforma. O
#: `MLScraper.run_diagnostics()` grava `ML: 2/40 kw ok, 38 gate [...]`, e esse
#: é o motivo da queda que antes só existia no log do PC coletor.
PREFIXO_EVIDENCIA: Dict[str, str] = {
    "ML": "Mercado Livre",
}

#: `detail` era cortado em 500 caracteres. Um detail desse tamanho sem
#: separador pode ter perdido o fim da lista de contagens.
_LIMITES_DETAIL = (500, 1000)

#: Ordem cronológica dos turnos (a alfabética poria Fechamento antes de Tarde).
_ORDEM_TURNO = {"Abertura": 0, "Tarde": 1, "Fechamento": 2}

_SEP = " | "
_TOKEN = re.compile(r"^\s*([^=|]+?)\s*=\s*(\d+)\s*$")


# ---------------------------------------------------------------------------
# Leitura do detail da batida
# ---------------------------------------------------------------------------

@dataclass
class DetalheBatida:
    """O que o `detail` de uma batida diz sobre as plataformas.

    Attributes:
        contagens: Linhas por plataforma, como gravadas em `coletas.plataforma`.
        zeradas: Plataformas que o run listou em ``ZERADAS:``.
        evidencias: Linha de diagnóstico do scraper, por plataforma.
        tem_contagem: True se o detail trouxe o segmento de contagens.
        completo: False se o segmento de contagens pode ter sido cortado no
            fim (detail do tamanho do limite e sem separador depois dele).
    """

    contagens: Dict[str, int] = field(default_factory=dict)
    zeradas: Set[str] = field(default_factory=set)
    evidencias: Dict[str, str] = field(default_factory=dict)
    tem_contagem: bool = False
    completo: bool = True


def _segmento_de_contagem(segmento: str) -> Optional[Dict[str, int]]:
    """``"A=1, B=2"`` → ``{"A": 1, "B": 2}``; None se não for desse formato."""
    tokens = [t for t in segmento.split(",") if t.strip()]
    if not tokens:
        return None
    contagens: Dict[str, int] = {}
    for token in tokens:
        casou = _TOKEN.match(token)
        if not casou:
            return None
        contagens[casou.group(1)] = int(casou.group(2))
    return contagens


def ler_detail(detail: Optional[str]) -> DetalheBatida:
    """Interpreta o `detail` gravado por `main.py::_bater_ponto`.

    Formato: segmentos separados por ``" | "``. O primeiro traz
    ``Plataforma=N`` separados por vírgula, depois pode vir
    ``ZERADAS: A, B`` e depois o texto livre (o PR #400 acrescenta ali a linha
    ``ML: x/y kw ok, ...``).

    Args:
        detail: Texto da batida (pode ser None).

    Returns:
        O que foi possível extrair. Segmento desconhecido é ignorado, nunca
        lido como contagem.
    """
    resultado = DetalheBatida()
    if not detail:
        return resultado

    segmentos = detail.split(_SEP)
    for indice, segmento in enumerate(segmentos):
        texto = segmento.strip()
        if not texto:
            continue
        if texto.startswith("ZERADAS:"):
            nomes = texto[len("ZERADAS:"):].split(",")
            resultado.zeradas.update(n.strip() for n in nomes if n.strip())
            continue
        prefixo = texto.split(":", 1)[0].strip()
        if ":" in texto and prefixo in PREFIXO_EVIDENCIA:
            resultado.evidencias[PREFIXO_EVIDENCIA[prefixo]] = texto
            continue
        if indice == 0 and not resultado.tem_contagem:
            contagens = _segmento_de_contagem(texto)
            if contagens is None and len(detail) in _LIMITES_DETAIL:
                # Cortado no meio de um token ("Shopee=12" → "Shop"). O que
                # veio antes do corte ainda vale, mas o resto é desconhecido.
                contagens = _segmento_de_contagem(texto.rsplit(",", 1)[0])
            if contagens is not None:
                resultado.contagens = contagens
                resultado.tem_contagem = True
                resultado.completo = not (
                    len(segmentos) == 1 and len(detail) in _LIMITES_DETAIL
                )
    return resultado


def linhas_por_dono(
    detalhe: DetalheBatida,
    plataformas_dono: Sequence[str],
    marketplaces: Iterable[str],
) -> Dict[str, Optional[int]]:
    """Linhas que o run trouxe para cada plataforma de que o job é dono.

    Args:
        detalhe: Detail já interpretado.
        plataformas_dono: `JobSpec.plataformas` (pode conter ``dealers``).
        marketplaces: Nomes de marketplace. O grupo ``dealers`` é tudo o que
            NÃO está aqui, o mesmo critério de `pipeline_watch._contar_coletas`.

    Returns:
        Plataforma → linhas, ou None quando o detail não permite afirmar
        (sem contagem, ou lista cortada e a plataforma não aparece).
    """
    conhecidos = set(marketplaces)
    resultado: Dict[str, Optional[int]] = {}
    for plataforma in plataformas_dono:
        if plataforma == PLATAFORMA_DEALERS:
            if not detalhe.tem_contagem or not detalhe.completo:
                resultado[plataforma] = None
                continue
            resultado[plataforma] = sum(
                n for nome, n in detalhe.contagens.items() if nome not in conhecidos
            )
            continue
        if plataforma in detalhe.contagens:
            resultado[plataforma] = detalhe.contagens[plataforma]
        elif plataforma in detalhe.zeradas:
            resultado[plataforma] = 0
        elif detalhe.tem_contagem and detalhe.completo:
            # Lista inteira e a plataforma não está nela: o run não trouxe nada.
            resultado[plataforma] = 0
        else:
            resultado[plataforma] = None
    return resultado


# ---------------------------------------------------------------------------
# Observações: batidas → (dia, turno, plataforma, linhas)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JobDeColeta:
    """O mínimo de `JobSpec` que a classificação precisa (mantém o módulo puro)."""

    id: str
    turno: str
    plataformas: Tuple[str, ...]


@dataclass(frozen=True)
class Observacao:
    """Linhas de uma plataforma num turno, segundo a batida do job dono."""

    dia: date
    turno: str
    plataforma: str
    linhas: int
    job_id: str
    evidencia: Optional[str] = None


def observacoes_de_batidas(
    batidas: Iterable[Dict[str, Any]],
    jobs: Sequence[JobDeColeta],
    marketplaces: Iterable[str],
) -> List[Observacao]:
    """Converte as batidas do livro-razão em observações por plataforma.

    Usa a ÚLTIMA batida de cada (job, dia), como `heartbeat.ultimas_batidas`.
    Só vale batida ``SUCCESS`` com linhas gravadas. FAILED, PARTIAL, STARTED e
    0 linhas já são cobrados no nível do job, e repetir aqui seria alerta
    duplicado.

    Args:
        batidas: Linhas de `pipeline_heartbeat` (job_id, data_ref, status,
            rows_written, detail, id).
        jobs: Jobs de coleta com turno.
        marketplaces: Ver `linhas_por_dono`.

    Returns:
        Observações, uma por (dia, turno, plataforma dona) que o detail permite
        afirmar.
    """
    marketplaces = tuple(marketplaces)
    por_id = {j.id: j for j in jobs}

    ultimas: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for batida in batidas:
        job_id = batida.get("job_id")
        data_ref = batida.get("data_ref")
        if job_id not in por_id or not data_ref:
            continue
        chave = (job_id, str(data_ref)[:10])
        atual = ultimas.get(chave)
        if atual is None or (batida.get("id") or 0) > (atual.get("id") or 0):
            ultimas[chave] = batida

    observacoes: List[Observacao] = []
    for (job_id, data_txt), batida in ultimas.items():
        if batida.get("status") != "SUCCESS":
            continue
        if batida.get("rows_written") == 0:
            continue
        job = por_id[job_id]
        try:
            dia = date.fromisoformat(data_txt)
        except ValueError:
            continue
        detalhe = ler_detail(batida.get("detail"))
        for plataforma, linhas in linhas_por_dono(
            detalhe, job.plataformas, marketplaces
        ).items():
            if linhas is None:
                continue
            observacoes.append(
                Observacao(
                    dia=dia,
                    turno=job.turno,
                    plataforma=plataforma,
                    linhas=linhas,
                    job_id=job_id,
                    evidencia=detalhe.evidencias.get(plataforma),
                )
            )
    return observacoes


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TurnoMagro:
    """Um turno abaixo de `FRACAO_QUEDA` da sua mediana."""

    turno: str
    linhas: int
    mediana: float
    job_id: str
    evidencia: Optional[str] = None

    @property
    def percentual(self) -> float:
        """Linhas do turno como % da mediana."""
        return 100.0 * self.linhas / self.mediana if self.mediana else 0.0


@dataclass
class QuedaVolume:
    """Plataforma DEGRADADA: volume muito abaixo do normal em ≥2 turnos.

    Attributes:
        plataforma: Nome como em `coletas.plataforma` (ou ``dealers``).
        dia: Último dia avaliável, que é o que está degradado.
        dias: Dias avaliáveis seguidos degradados, terminando em ``dia``.
        turnos: Os turnos magros de ``dia``.
    """

    plataforma: str
    dia: date
    dias: int
    turnos: List[TurnoMagro]

    @property
    def cronica(self) -> bool:
        """True a partir de `DIAS_PARA_CRONICO` dias seguidos."""
        return self.dias >= DIAS_PARA_CRONICO

    @property
    def evidencia(self) -> Optional[str]:
        """Diagnóstico do scraper no turno magro mais recente que tiver um."""
        for turno in reversed(self.turnos):
            if turno.evidencia:
                return turno.evidencia
        return None

    @property
    def jobs(self) -> List[str]:
        """Jobs donos dos turnos magros, sem repetição."""
        return list(dict.fromkeys(t.job_id for t in self.turnos))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plataforma": self.plataforma,
            "dia": self.dia.isoformat(),
            "dias_degradada": self.dias,
            "cronica": self.cronica,
            "jobs": self.jobs,
            "turnos": [
                {
                    "turno": t.turno,
                    "linhas": t.linhas,
                    "mediana": round(t.mediana, 1),
                    "percentual": round(t.percentual, 1),
                    "job": t.job_id,
                }
                for t in self.turnos
            ],
            "evidencia": self.evidencia,
        }


_DEGRADADO = "DEGRADADO"
_NORMAL = "NORMAL"
_INDEFINIDO = "INDEFINIDO"


def _avaliar_plataforma(
    obs: List[Observacao],
) -> List[Tuple[date, str, List[TurnoMagro]]]:
    """Caminha os dias em ordem e classifica cada um contra o histórico aceito.

    Returns:
        (dia, estado, turnos magros) em ordem cronológica.
    """
    por_dia: Dict[date, List[Observacao]] = {}
    for o in obs:
        por_dia.setdefault(o.dia, []).append(o)

    aceitos: Dict[str, List[int]] = {}
    resultado: List[Tuple[date, str, List[TurnoMagro]]] = []

    for dia in sorted(por_dia):
        magros: List[TurnoMagro] = []
        avaliados = 0
        normais: List[Observacao] = []
        for o in sorted(por_dia[dia], key=lambda x: _ORDEM_TURNO.get(x.turno, 9)):
            historico = aceitos.get(o.turno, [])[-JANELA_REFERENCIA:]
            ref = median(historico) if len(historico) >= MIN_AMOSTRAS else None
            if ref is None or ref < MIN_MEDIANA:
                normais.append(o)
                continue
            avaliados += 1
            if o.linhas < FRACAO_QUEDA * ref:
                magros.append(TurnoMagro(o.turno, o.linhas, ref, o.job_id, o.evidencia))
            else:
                normais.append(o)

        if len(magros) >= MIN_TURNOS_MAGROS:
            estado = _DEGRADADO
        elif avaliados >= MIN_TURNOS_MAGROS:
            estado = _NORMAL
        else:
            estado = _INDEFINIDO

        # Dia degradado não ensina nada à referência: é isso que impede a
        # mediana de "se acostumar" com a quebra e o alarme de se calar sozinho.
        if estado != _DEGRADADO:
            for o in normais:
                aceitos.setdefault(o.turno, []).append(o.linhas)
        resultado.append((dia, estado, magros))
    return resultado


def classificar_quedas(
    observacoes: Iterable[Observacao], ate: date
) -> List[QuedaVolume]:
    """Plataformas cujo último dia avaliável está degradado.

    Um dia só é avaliável com ≥ `MIN_TURNOS_MAGROS` turnos com referência. Por
    isso o dia corrente entra assim que dois turnos fecharem: dá para alarmar
    às 14h, não só no dia seguinte. Dia em que a plataforma não tem observação
    (PC desligado, por exemplo) é pulado, e não interrompe nem conta na
    sequência, porque silêncio não é mercado.

    Args:
        observacoes: Saída de `observacoes_de_batidas`.
        ate: Dia de referência (hoje). Dias depois dele são ignorados. Uma
            queda cujo último dia avaliável é anterior a ``ate - 1`` não é
            reportada: o retrato está velho demais para ser o estado atual.

    Returns:
        Uma `QuedaVolume` por plataforma degradada, a pior sequência primeiro.
    """
    por_plataforma: Dict[str, List[Observacao]] = {}
    for o in observacoes:
        if o.dia <= ate:
            por_plataforma.setdefault(o.plataforma, []).append(o)

    quedas: List[QuedaVolume] = []
    for plataforma, obs in por_plataforma.items():
        avaliados = [d for d in _avaliar_plataforma(obs) if d[1] != _INDEFINIDO]
        if not avaliados:
            continue
        ultimo_dia, ultimo_estado, magros = avaliados[-1]
        if ultimo_estado != _DEGRADADO or ultimo_dia < ate - timedelta(days=1):
            continue
        seguidos = 0
        for _dia, estado, _m in reversed(avaliados):
            if estado != _DEGRADADO:
                break
            seguidos += 1
        quedas.append(QuedaVolume(plataforma, ultimo_dia, seguidos, magros))

    quedas.sort(key=lambda q: (-q.dias, q.plataforma))
    return quedas
