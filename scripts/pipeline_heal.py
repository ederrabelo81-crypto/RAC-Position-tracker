"""
scripts/pipeline_heal.py — Contenção e autocorreção da pipeline.

O supervisor (`pipeline_watch.py`) diz o que quebrou. Este tenta consertar,
sozinho e sem interação, o que é seguro consertar sozinho — e é explícito sobre
o que não é.

O que ele conserta, e por que só isso
-------------------------------------
Toda cura automática aqui obedece a três regras:

1. **Idempotente.** Rodar duas vezes não duplica dado. O import do PriceTrack
   faz upsert por data; recoletar um turno substitui a entrada do dia.
2. **Barata e determinística.** Reimportar um dia do PriceTrack custa alguns
   minutos e não depende de browser, sessão logada ou IP residencial.
3. **Do lado certo da rede.** Nada aqui alcança a VM Oracle nem o notebook do
   analista. Fingir que alcança seria pior que não tentar: o alerta diria
   "curado" e o buraco continuaria lá.

Por isso a coleta de marketplace **não** é auto-disparada. Ela precisa da
máquina certa (Chrome logado, IP residencial), leva mais de uma hora e, no
executor errado, devolve zero linha com aparência de sucesso — exatamente o
modo de falha que estamos tentando eliminar. Para ela, a contenção correta é o
alerta acionável com o comando exato, e o registro de degradação crônica que
transforma repetição em issue rastreada.

Uso::

    python scripts/pipeline_heal.py                 # cura o que der, hoje
    python scripts/pipeline_heal.py --dry-run       # só diz o que faria
    python scripts/pipeline_heal.py --dispatch      # também re-dispara workflows (exige PAT)

Exit codes:
    0 — nada a curar, ou tudo que dava para curar foi curado
    1 — sobrou problema que exige intervenção humana
    3 — supervisor cego (não deu para avaliar o estado)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts.pipeline_watch import (  # noqa: E402  (script como módulo)
    Diagnostico,
    SupervisorCego,
    _PROBLEMAS,
    varrer,
)
from utils.text import now_brt

#: Teto de parede de uma cura. O reimport do PriceTrack tem orçamento próprio
#: (PRICETRACK_RUN_BUDGET_SECONDS); este é o cinto de segurança de fora.
TIMEOUT_CURA_SEG = 3000


def _dispatch_workflow(workflow: str, ref: str = "main") -> bool:
    """Re-dispara um workflow do Actions pela API REST.

    Requer um PAT em ``RAC_GH_PAT`` (ou ``GH_PAT``) com escopo ``actions:write``.
    O ``GITHUB_TOKEN`` do próprio run **não serve**: por proteção anti-recursão,
    eventos disparados por ele não criam novos runs. Sem PAT a função apenas
    registra o motivo e devolve False — jamais finge ter disparado.

    Args:
        workflow: Nome do arquivo (ex.: "pricetrack_daily.yml").
        ref: Branch de referência.

    Returns:
        True se o GitHub aceitou o dispatch (HTTP 204).
    """
    token = (os.getenv("RAC_GH_PAT") or os.getenv("GH_PAT") or "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token:
        logger.info(
            f"[Cura] Sem RAC_GH_PAT — não re-disparo {workflow}. "
            "(O GITHUB_TOKEN do run não dispara workflows: anti-recursão.)"
        )
        return False
    if not repo:
        logger.warning("[Cura] GITHUB_REPOSITORY ausente — dispatch impossível.")
        return False

    try:
        import requests

        resp = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": ref},
            timeout=30,
        )
        if resp.status_code == 204:
            logger.success(f"[Cura] {workflow} re-disparado.")
            return True
        logger.error(f"[Cura] Dispatch de {workflow}: HTTP {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        logger.error(f"[Cura] Dispatch de {workflow} falhou: {exc}")
    return False


def _executar(comando: str, dry_run: bool) -> bool:
    """Executa uma cura local.

    Args:
        comando: Linha de comando já formatada.
        dry_run: Se True, só registra o que faria.

    Returns:
        True se o comando terminou com exit 0 (ou se é dry-run).
    """
    logger.warning(f"[Cura] {'(dry-run) ' if dry_run else ''}$ {comando}")
    if dry_run:
        return True
    try:
        resultado = subprocess.run(
            comando, shell=True, timeout=TIMEOUT_CURA_SEG, check=False,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if resultado.returncode == 0:
            logger.success("[Cura] Comando concluído.")
            return True
        logger.error(f"[Cura] Comando falhou (exit {resultado.returncode}).")
    except subprocess.TimeoutExpired:
        logger.error(f"[Cura] Comando estourou {TIMEOUT_CURA_SEG}s.")
    except Exception as exc:
        logger.error(f"[Cura] Comando não executou: {exc}")
    return False


def curar(
    diagnosticos: List[Diagnostico],
    dia: date,
    dry_run: bool = False,
    dispatch: bool = False,
) -> Dict[str, Any]:
    """Aplica a escada de remediação sobre os problemas encontrados.

    Args:
        diagnosticos: Vereditos do supervisor.
        dia: Dia avaliado (usado para formatar ``{dia}``/``{d1}`` nos comandos).
        dry_run: Não executa nada, só relata.
        dispatch: Também tenta re-disparar workflows do Actions (exige PAT).

    Returns:
        Dict com ``curados``, ``pendentes`` e ``acoes`` — o material do alerta
        e da issue de degradação crônica.
    """
    d1 = (dia - timedelta(days=1)).isoformat()
    curados: List[str] = []
    pendentes: List[Dict[str, str]] = []
    acoes: List[str] = []

    for diag in diagnosticos:
        if diag.estado not in _PROBLEMAS:
            continue
        job = diag.job

        if job.auto_heal:
            comando = job.auto_heal.format(dia=dia.isoformat(), d1=d1)
            acoes.append(comando)
            if _executar(comando, dry_run):
                curados.append(job.id)
                continue

        if dispatch and job.workflow:
            acoes.append(f"workflow_dispatch:{job.workflow}")
            if not dry_run and _dispatch_workflow(job.workflow):
                curados.append(job.id)
                continue

        pendentes.append({
            "job": job.id,
            "nome": job.nome,
            "executor": job.executor_nome,
            "estado": diag.estado,
            "detalhe": diag.detalhe,
            "remediacao": job.remediacao,
        })

    return {"curados": curados, "pendentes": pendentes, "acoes": acoes}


def main(argv: Optional[List[str]] = None) -> int:
    """Ponto de entrada da CLI."""
    parser = argparse.ArgumentParser(
        description="Contenção e autocorreção da pipeline RAC.",
    )
    parser.add_argument("--data", default=None, help="dia a avaliar (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="não executa, só relata")
    parser.add_argument(
        "--dispatch", action="store_true",
        help="também re-dispara workflows do Actions (exige RAC_GH_PAT)",
    )
    parser.add_argument("--json", dest="json_path", default=None, help="grava o resultado aqui")
    args = parser.parse_args(argv)

    agora = now_brt()
    dia = datetime.strptime(args.data, "%Y-%m-%d").date() if args.data else agora.date()

    try:
        diagnosticos, degradacoes = varrer(dia, agora)
    except SupervisorCego as exc:
        logger.error(f"[Cura] Supervisor cego: {exc}")
        return 3

    resultado = curar(diagnosticos, dia, args.dry_run, args.dispatch)
    resultado["degradacoes_cronicas"] = [
        g.to_dict() for g in degradacoes if g.cronica
    ]

    if resultado["curados"]:
        logger.success(f"[Cura] Curados: {', '.join(resultado['curados'])}")
    for pendente in resultado["pendentes"]:
        logger.error(
            f"[Cura] PENDENTE {pendente['job']} ({pendente['executor']}): "
            f"{pendente['detalhe']} → {pendente['remediacao']}"
        )

    if args.json_path:
        caminho = Path(args.json_path)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return 1 if resultado["pendentes"] or resultado["degradacoes_cronicas"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
