"""
utils/heartbeat.py — Batida de ponto dos jobs da pipeline.

Todo executor (GitHub Actions, VM Oracle, PC coletor) grava aqui que começou e
que terminou. O supervisor (`scripts/pipeline_watch.py`) lê essas batidas e,
comparando com `utils/pipeline_registry.py`, consegue afirmar a única coisa que
nenhum monitor do projeto sabia afirmar até 28/08/2026: **"este job não rodou"**.

Por que a ausência precisa ser um evento
----------------------------------------
Os monitores existentes olham o DADO. Isso pega "rodou e veio vazio", mas não
distingue "não rodou" de "rodou e o dado ainda está a caminho" — e não pega de
jeito nenhum um job cujo agendamento simplesmente não disparou. Foi assim que o
import do PriceTrack de 27/08 (cron 09:00 UTC, executado às 19:18 UTC) deixou o
briefing das 07:00 sair com preço de D-2 sem nenhum alerta: nada tinha falhado,
nada tinha acontecido.

Dois destinos, de propósito
---------------------------
A batida vai para o Supabase (`pipeline_heartbeat`) **e** para
`logs/heartbeat.jsonl` no disco do executor. Não é redundância decorativa:
quando o Supabase está fora — restrição de cota, chave revogada, rede — é
exatamente quando a pipeline mais quebra, e um livro-razão que só existe lá
dentro fica cego junto. O JSONL local é o que o analista lê no PC/VM para
provar que a máquina rodou e que quem falhou foi o banco.

**Falhar ao bater ponto NUNCA derruba o job.** Um coletor que morre porque o
livro-razão está indisponível troca um problema de observabilidade por um
problema de coleta — o pior negócio possível.

Uso em Python::

    from utils.heartbeat import batida

    with batida("local_manha", rows_de=lambda: len(registros)):
        registros = coletar()

Uso em shell (workflow, .bat, cron)::

    python -m utils.heartbeat --job gh_pricetrack_d1 --status STARTED
    python -m utils.heartbeat --job gh_pricetrack_d1 --status SUCCESS --rows 4210
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from loguru import logger

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.pipeline_registry import JOBS_POR_ID  # noqa: E402
from utils.text import now_brt  # noqa: E402

#: Tabela do livro-razão (migração 015).
TABELA = "pipeline_heartbeat"

#: Espelho local. Fica em logs/ porque é diagnóstico, não dado de negócio.
ARQUIVO_LOCAL = _REPO_ROOT / "logs" / "heartbeat.jsonl"

STATUS_STARTED = "STARTED"
STATUS_SUCCESS = "SUCCESS"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"

_STATUS_VALIDOS = (STATUS_STARTED, STATUS_SUCCESS, STATUS_PARTIAL, STATUS_FAILED)


def _host() -> str:
    """Identifica a máquina que bateu o ponto.

    No Actions o hostname é um id efêmero e inútil para o analista, então
    preferimos o nome do runner/repositório: saber que a batida veio "do
    Actions" e não "do notebook" é o ponto inteiro do campo.
    """
    if os.getenv("GITHUB_ACTIONS") == "true":
        return f"actions:{os.getenv('GITHUB_REPOSITORY', '?')}"
    try:
        return socket.gethostname()
    except Exception:
        return "?"


def _run_url() -> Optional[str]:
    """URL do run no GitHub Actions, quando a batida vem de lá."""
    servidor = os.getenv("GITHUB_SERVER_URL")
    repo = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if servidor and repo and run_id:
        return f"{servidor}/{repo}/actions/runs/{run_id}"
    return None


def _gravar_local(registro: Dict[str, Any]) -> None:
    """Anexa a batida ao JSONL local (best-effort, nunca levanta)."""
    try:
        ARQUIVO_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        with ARQUIVO_LOCAL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # pragma: no cover - disco cheio/permite seguir
        logger.debug(f"[Heartbeat] Espelho local indisponível: {exc}")


def _gravar_supabase(registro: Dict[str, Any]) -> bool:
    """Insere a batida no Supabase.

    Returns:
        True se gravou; False em qualquer falha (client ausente, cota, rede).
        Nunca levanta: ver a nota sobre não derrubar o job.
    """
    try:
        from utils.supabase_client import _get_client

        client = _get_client()
        if client is None:
            return False
        client.table(TABELA).insert(registro).execute()
        return True
    except Exception as exc:
        logger.warning(f"[Heartbeat] Não gravou em {TABELA}: {exc}")
        return False


def bater(
    job_id: str,
    status: str,
    *,
    data_ref: Optional[date] = None,
    rows: Optional[int] = None,
    detail: str = "",
    duration_seconds: Optional[float] = None,
    started_at: Optional[datetime] = None,
) -> bool:
    """Registra uma batida de ponto.

    Args:
        job_id: Id do job em ``utils.pipeline_registry.JOBS``. Um id fora do
            registro é aceito e logado como aviso — perder a batida de um job
            novo seria pior que registrar um id que o supervisor ainda ignora.
        status: STARTED | SUCCESS | PARTIAL | FAILED.
        data_ref: Dia BRT a que a execução se refere (default: hoje BRT).
        rows: Linhas gravadas no destino, quando o job sabe contar.
        detail: Uma linha de diagnóstico legível no alerta.
        duration_seconds: Duração, quando conhecida.
        started_at: Início real, se diferente de agora.

    Returns:
        True se a batida chegou ao Supabase. O espelho local é gravado sempre.

    Raises:
        ValueError: Status fora do vocabulário — erro de programação, não de
            ambiente, e por isso é a única coisa que esta função recusa.
    """
    if status not in _STATUS_VALIDOS:
        raise ValueError(
            f"status inválido: {status!r} (esperado um de {_STATUS_VALIDOS})"
        )

    spec = JOBS_POR_ID.get(job_id)
    if spec is None:
        logger.warning(
            f"[Heartbeat] job_id '{job_id}' não está em pipeline_registry.JOBS — "
            "a batida é gravada, mas o supervisor não vai cobrá-la."
        )

    agora = now_brt()
    inicio = started_at or agora
    registro: Dict[str, Any] = {
        "job_id": job_id,
        "executor": spec.executor if spec else "desconhecido",
        "data_ref": (data_ref or agora.date()).isoformat(),
        "status": status,
        "started_at": inicio.isoformat(),
        "host": _host(),
    }
    if status != STATUS_STARTED:
        registro["finished_at"] = agora.isoformat()
    if duration_seconds is not None:
        registro["duration_seconds"] = round(float(duration_seconds), 1)
    if rows is not None:
        registro["rows_written"] = int(rows)
    if detail:
        registro["detail"] = detail[:500]
    url = _run_url()
    if url:
        registro["run_url"] = url

    _gravar_local(registro)
    gravou = _gravar_supabase(registro)

    marcador = "✓" if gravou else "⚠ só local"
    logger.info(
        f"[Heartbeat] {marcador} {job_id} {status}"
        + (f" ({rows} linhas)" if rows is not None else "")
    )
    return gravou


@contextmanager
def batida(
    job_id: str,
    *,
    data_ref: Optional[date] = None,
    rows_de: Optional[Callable[[], Optional[int]]] = None,
) -> Iterator[Dict[str, Any]]:
    """Envolve um job: bate STARTED na entrada e SUCCESS/FAILED na saída.

    Args:
        job_id: Id do job no registro.
        data_ref: Dia BRT de referência (default: hoje).
        rows_de: Chamável avaliado no fim para descobrir quantas linhas o job
            gravou. É um callable, e não um número, porque no início da
            execução o total ainda não existe.

    Yields:
        Um dict mutável de contexto: preencha ``ctx["rows"]`` e
        ``ctx["detail"]`` durante a execução para enriquecer a batida final.

    Example:
        >>> with batida("local_bestsellers") as ctx:  # doctest: +SKIP
        ...     ctx["rows"] = coletar()
    """
    contexto: Dict[str, Any] = {"rows": None, "detail": ""}
    inicio_wall = time.monotonic()
    inicio = now_brt()
    bater(job_id, STATUS_STARTED, data_ref=data_ref, started_at=inicio)

    try:
        yield contexto
    except BaseException as exc:  # inclui KeyboardInterrupt/SystemExit
        bater(
            job_id,
            STATUS_FAILED,
            data_ref=data_ref,
            rows=contexto.get("rows"),
            detail=(contexto.get("detail") or f"{type(exc).__name__}: {exc}"),
            duration_seconds=time.monotonic() - inicio_wall,
            started_at=inicio,
        )
        raise
    else:
        linhas = contexto.get("rows")
        if linhas is None and rows_de is not None:
            try:
                linhas = rows_de()
            except Exception:
                linhas = None
        # Zero linha não é sucesso: é o modo de falha do Google Shopping, que
        # passou um mês verde justamente porque ninguém marcava a diferença.
        status = STATUS_PARTIAL if linhas == 0 else STATUS_SUCCESS
        bater(
            job_id,
            status,
            data_ref=data_ref,
            rows=linhas,
            detail=contexto.get("detail", ""),
            duration_seconds=time.monotonic() - inicio_wall,
            started_at=inicio,
        )


def ultimas_batidas(dia: date, job_ids: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Última batida de cada job num dia.

    Args:
        dia: Dia BRT consultado.
        job_ids: Restringe a consulta; None traz todos.

    Returns:
        Mapa job_id → registro da última batida. Dict vazio quando o Supabase
        está indisponível — o chamador decide se isso é "não rodou" ou "não
        consegui olhar" (o supervisor trata como o segundo, e diz isso).
    """
    try:
        from utils.supabase_client import _get_client

        client = _get_client()
        if client is None:
            return {}
        consulta = (
            client.table(TABELA)
            .select("*")
            .eq("data_ref", dia.isoformat())
            .order("id", desc=True)
            .limit(1000)
        )
        if job_ids:
            consulta = consulta.in_("job_id", job_ids)
        linhas = consulta.execute().data or []
    except Exception as exc:
        logger.warning(f"[Heartbeat] Leitura de {TABELA} falhou: {exc}")
        return {}

    # A consulta vem do id maior para o menor: a primeira ocorrência de cada
    # job já é a mais recente. Uma batida SUCCESS nunca deve ser sombreada por
    # um STARTED anterior do mesmo dia.
    ultimas: Dict[str, Dict[str, Any]] = {}
    for linha in linhas:
        ultimas.setdefault(linha["job_id"], linha)
    return ultimas


def main(argv: Optional[List[str]] = None) -> int:
    """CLI para os executores que não são Python (workflow, .bat, cron)."""
    parser = argparse.ArgumentParser(
        description="Registra uma batida de ponto da pipeline RAC.",
    )
    parser.add_argument("--job", required=True, help="id do job (pipeline_registry.JOBS)")
    parser.add_argument("--status", required=True, choices=_STATUS_VALIDOS)
    parser.add_argument("--rows", type=int, default=None, help="linhas gravadas no destino")
    parser.add_argument("--detail", default="", help="uma linha de diagnóstico")
    parser.add_argument("--data", default=None, help="dia de referência YYYY-MM-DD (default: hoje BRT)")
    args = parser.parse_args(argv)

    data_ref = (
        datetime.strptime(args.data, "%Y-%m-%d").date() if args.data else None
    )
    bater(
        args.job,
        args.status,
        data_ref=data_ref,
        rows=args.rows,
        detail=args.detail,
    )
    # Sempre 0: a batida é observabilidade. Um exit != 0 aqui derrubaria o
    # passo do workflow que ela só deveria observar.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
