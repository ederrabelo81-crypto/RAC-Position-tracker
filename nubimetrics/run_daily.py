"""ETL diário Nubimetrics → Supabase (+ Parquet). Job ``gh_nubimetrics``.

Roda no GitHub Actions (``nubimetrics_daily.yml``), fora do PC coletor: não
disputa CPU, rede nem janela com a coleta de oferta/posição.

A Nubimetrics atualiza 1×/dia (último dia fechado = D-1/D-2); o workflow roda
depois das 10h BRT.

Uso::

    python -m nubimetrics.run_daily                    # rodada diária (2 meses de ranking)
    python -m nubimetrics.run_daily --backfill         # 13 meses de ranking/brandshare
    python -m nubimetrics.run_daily --dry-run          # extrai e transforma, não grava
    python -m nubimetrics.run_daily --sem-anuncios     # pula o explorador (~80 chamadas)

Saída: 0 = ok; 2 = gravou com ressalva (consistência fora de ±2%, concorrente
falhou); 1 = falhou.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from nubimetrics import extract, load, transform

EXIT_OK, EXIT_FALHA, EXIT_RESSALVA = 0, 1, 2


def _bater(status: str, linhas: Optional[int] = None, detalhe: str = "") -> None:
    """Livro-razão (pipeline_heartbeat) quando ``RAC_JOB_ID`` existe. Nunca levanta."""
    job_id = os.getenv("RAC_JOB_ID", "").strip()
    if not job_id:
        return
    try:
        from utils.heartbeat import bater

        bater(job_id, status, rows=linhas, detail=detalhe[:900])
    except Exception as exc:  # pragma: no cover — best effort
        logger.debug(f"[Heartbeat] '{status}' não registrada: {exc}")


async def extrair(meses: int, com_anuncios: bool, hoje: dt.date) -> Dict[str, Any]:
    """Todas as fontes, numa sessão MCP só."""
    from nubimetrics.client import NubiClient

    fechado = extract.ultimo_mes_fechado(hoje)
    inicio_serie = extract.primeiro_dia(hoje).replace(year=hoje.year - 1)
    bruto: Dict[str, Any] = {"rankings": [], "avisos": []}
    async with NubiClient() as nubi:
        bruto["mercado"] = await extract.mercado(nubi, inicio_serie, extract.primeiro_dia(hoje))
        bruto["previsao"] = await extract.previsao(nubi, fechado)
        bruto["detalhe"] = (fechado, await extract.detalhe_categoria(nubi, fechado))
        for mes in extract.meses_para_tras(fechado, meses):
            for dim in extract.DIMENSOES:
                bruto["rankings"].append((mes, dim, await extract.ranking(nubi, mes, dim)))
            bruto["rankings"].append((mes, "search_terms", await extract.buscas(nubi, mes)))
        bruto["anuncios"] = await extract.anuncios(nubi) if com_anuncios else []
        try:
            bruto["concorrente"] = await extract.concorrente(nubi)
        except Exception as exc:  # noqa: BLE001 — slot de concorrência é acessório
            bruto["concorrente"] = []
            bruto["avisos"].append(f"concorrente: {exc}")
        logger.info(f"[Nubimetrics] {nubi.chamadas} chamada(s) à API, {nubi.do_cache} do cache")
    return bruto


def transformar(bruto: Dict[str, Any], hoje: dt.date) -> Dict[str, Any]:
    real = transform.mercado_mensal(bruto["mercado"], hoje)
    if bruto.get("detalhe"):
        transform.detalhe_categoria(bruto["detalhe"][1], real, bruto["detalhe"][0])
    mercado = real + transform.previsao_mensal(bruto["previsao"])
    rankings: List[Dict[str, Any]] = []
    avisos = list(bruto.get("avisos") or [])
    for mes, dim, payload in bruto["rankings"]:
        linhas = transform.ranking(payload, mes, dim)
        if not linhas:
            avisos.append(f"{dim} {mes:%m/%Y}: mês ainda sem dado (fallback) — não gravado")
        rankings += linhas
        if dim == "brands":
            desvio = transform.checar_consistencia(linhas, mercado)
            if desvio:
                avisos.append(desvio)
    return {
        "mercado": load.mercado_sem_rebaixar_real(mercado),
        "rankings": rankings,
        "anuncios": transform.anuncios(bruto["anuncios"], hoje),
        # Parquet guarda o bruto do explorador + a classificação: campo que o
        # transform não modela (installments, oem…) não se perde no histórico.
        "anuncios_historico": [{**{f"raw_{k}": v for k, v in raw.items()}, **norm}
                               for raw, norm in zip(bruto["anuncios"],
                                                    transform.anuncios(bruto["anuncios"], hoje))],
        "concorrente": transform.concorrente(bruto["concorrente"]),
        "avisos": avisos,
    }


def gravar(dados: Dict[str, Any], hoje: dt.date) -> int:
    """Grava tudo. O histórico (Parquet) vem ANTES da foto no banco: a foto
    troca a anterior, então um histórico que falhasse depois deixaria um buraco
    irrecuperável. Falha do Parquet vira ressalva (PARTIAL), não silêncio."""
    historico = dados.get("anuncios_historico") or dados["anuncios"]
    if dados["anuncios"] and not load.parquet(historico, hoje, run_id=os.getenv("GITHUB_RUN_ID")):
        dados["avisos"].append("histórico Parquet dos anúncios NÃO gravado (ver log)")
    client = load.cliente()
    total = load.upsert(client, "nubi_mercado_mensal", dados["mercado"])
    total += load.upsert(client, "nubi_ranking_mensal", dados["rankings"])
    total += load.upsert(client, "nubi_concorrente_diario", dados["concorrente"])
    total += load.substituir_anuncios(client, dados["anuncios"])
    return total


def resumo(dados: Dict[str, Any]) -> str:
    a = dados["anuncios"]
    por_regex = sum(1 for x in a if x["match_source"] == "title_default")
    return (f"mercado {len(dados['mercado'])} · rankings {len(dados['rankings'])} · "
            f"anúncios {len(a)} ({por_regex} inferidos) · concorrente {len(dados['concorrente'])}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meses", type=int, default=2, help="meses fechados de ranking (padrão 2)")
    ap.add_argument("--backfill", action="store_true", help="13 meses de ranking (brandshare)")
    ap.add_argument("--sem-anuncios", action="store_true", help="pula o explorador de anúncios")
    ap.add_argument("--dry-run", action="store_true", help="não grava nada")
    a = ap.parse_args(argv)
    meses = 13 if a.backfill else max(1, a.meses)
    from utils.text import now_brt

    hoje = now_brt().date()   # convenção do projeto: dia em BRT, nunca o do host
    inicio = time.monotonic()
    if not a.dry_run:          # dry-run não abre batida (ficaria STARTED para sempre)
        _bater("STARTED")
    try:
        bruto = asyncio.run(extrair(meses, not a.sem_anuncios, hoje))
        dados = transformar(bruto, hoje)
        if not a.sem_anuncios and not dados["anuncios"]:
            # Explorador pedido e vazio ≠ rodada saudável: a foto de ontem fica,
            # mas o livro-razão precisa saber.
            dados["avisos"].append("explorador devolveu 0 anúncios — foto anterior mantida")
        logger.info(f"[Nubimetrics] {resumo(dados)}")
        for aviso in dados["avisos"]:
            logger.warning(f"[Nubimetrics] {aviso}")
        if a.dry_run:
            logger.info("[Nubimetrics] --dry-run: nada gravado.")
            return EXIT_OK
        gravadas = gravar(dados, hoje)
    except Exception as exc:  # noqa: BLE001 — o livro-razão precisa do FAILED
        logger.error(f"[Nubimetrics] ETL falhou: {exc}")
        _bater("FAILED", detalhe=str(exc))
        return EXIT_FALHA
    ressalvas = [x for x in dados["avisos"] if "fallback" not in x]
    status = "PARTIAL" if ressalvas else "SUCCESS"
    _bater(status, linhas=gravadas, detalhe=f"{resumo(dados)} · {time.monotonic() - inicio:.0f}s"
           + (f" · {'; '.join(ressalvas)}" if ressalvas else ""))
    return EXIT_RESSALVA if ressalvas else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
