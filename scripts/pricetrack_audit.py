#!/usr/bin/env python3
"""
scripts/pricetrack_audit.py — auditoria do PriceTrack contra as coletas Python.

Responde, para um dia, as quatro perguntas da conferência de 16/10/2026
(ver `utils/pricetrack_audit.py`):

  1. O volume do PriceTrack despencou sem a nossa coleta cair junto?
  2. Há quantos dias o PriceTrack não marca NENHUMA oferta como indisponível?
  3. Que marketplace coletamos e ele deixou de cobrir?
  4. No mesmo SKU/plataforma/dia, o preço dele bate com o nosso? (só anúncio
     de UNIDADE — kit, peça, meia unidade e "kit 2" saem antes)

Gera `logs/pricetrack_audit_<data>.json` e `.md` (este pronto para colar em
e-mail ao fornecedor). Não grava nada no banco.

Uso:
    python scripts/pricetrack_audit.py                    # ontem (BRT)
    python scripts/pricetrack_audit.py --data 2026-10-15
    python scripts/pricetrack_audit.py --data 2026-10-15 --dias 21 --strict

Rodar DEPOIS do import do PriceTrack de D-1 (06:00 BRT) e com a janela quente
de `coletas` ainda contendo o dia (RAC_HOT_WINDOW_DAYS=2: rode até D+1).

Requer o backend do dashboard: `RAC_DB_DSN` ou `SUPABASE_URL`+`SUPABASE_KEY`.
Sem nenhum dos dois, aborta — nunca relata "zero" de um banco que não leu.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from loguru import logger

from pricetrack_dashboard.data_source import _supabase_client
from utils.pricetrack_audit import (
    AuditConfig,
    compare_prices,
    coverage_gaps,
    unavailability_streak,
    volume_steps,
)
from utils.text import now_brt

_PAGE = 1000          # teto de linhas por resposta do PostgREST
_MAX_ROWS = 400_000   # guarda contra loop; acima disso vira alerta no relatório
_RE_DATA_LOG = re.compile(r"api-(\d{4}-\d{2}-\d{2})")


def _paged(build: Callable[[], Any], nome: str, truncados: List[str]) -> List[Dict[str, Any]]:
    """Lê todas as páginas de um select (PostgREST corta em 1.000 linhas).

    ``build`` precisa trazer um ``.order`` determinístico (chave única): sem
    ordem, offset/limit não garante página estável e linhas podem pular ou
    repetir entre páginas. Bater no teto registra ``nome`` em ``truncados``,
    que vira alerta no relatório — leitura parcial nunca passa por completa.
    """
    rows: List[Dict[str, Any]] = []
    offset = 0
    while offset < _MAX_ROWS:
        page = build().range(offset, offset + _PAGE - 1).execute().data or []
        rows.extend(page)
        if len(page) < _PAGE:
            return rows
        offset += _PAGE
    logger.warning(f"pricetrack_audit: {nome} bateu no teto de {_MAX_ROWS} linhas — leitura parcial")
    truncados.append(nome)
    return rows


def coletar(client, dia: str, inicio: str) -> Dict[str, Any]:
    """Lê do banco tudo que a auditoria precisa (somente leitura)."""
    truncados: List[str] = []
    # 1a. Volume do PriceTrack: última importação de cada data no log.
    log = _paged(lambda: client.table("pricetrack_import_log")
                 .select("id,source_file,rows_total,rows_inserted,import_started")
                 .like("source_file", "api-%")
                 .gte("import_started", inicio)
                 .order("import_started").order("id"), "pricetrack_import_log", truncados)
    pt_volume: Dict[str, float] = {}
    pt_gravou: Dict[str, bool] = {}
    for row in log:  # ordenado por import_started: a última sobrescreve
        m = _RE_DATA_LOG.search(str(row.get("source_file") or ""))
        if m and inicio <= m.group(1) <= dia and row.get("rows_total") is not None:
            pt_volume[m.group(1)] = float(row["rows_total"])
            pt_gravou[m.group(1)] = bool(row.get("rows_inserted"))

    # 1b. Volume nosso: ofertas distintas por dia (fato do seller_app).
    sod = _paged(lambda: client.table("seller_offer_daily")
                 .select("data,plataforma,offer_key")
                 .gte("data", inicio).lte("data", dia)
                 # ordem = chave primária (data, turno, plataforma, offer_key, seller)
                 .order("data").order("turno").order("plataforma")
                 .order("offer_key").order("seller_canonical"),
                 "seller_offer_daily", truncados)
    chaves: Dict[str, set] = defaultdict(set)
    plataformas_dia: set = set()
    for row in sod:
        d = str(row.get("data"))[:10]
        chaves[d].add((row.get("plataforma"), row.get("offer_key")))
        if d == dia:
            plataformas_dia.add(row.get("plataforma"))
    nosso_volume = {d: float(len(v)) for d, v in chaves.items()}

    # 2. Indisponíveis: só as linhas com > 0 (barato). Zero só vale para dia
    # que o import de fato gravou: import falho ou 100% rejeitado não foi
    # observado e fica de fora — senão emendaria a sequência de "zero".
    indisp_rows = _paged(lambda: client.table("pricetrack_daily")
                         .select("id,collection_date,unavailable_count")
                         .eq("turno", "Diário")
                         .gte("collection_date", inicio).lte("collection_date", dia)
                         .gt("unavailable_count", 0)
                         .order("id"), "pricetrack_daily (indisponíveis)", truncados)
    indisp = {d: 0 for d, gravou in pt_gravou.items() if gravou}
    for row in indisp_rows:
        d = str(row.get("collection_date"))[:10]
        indisp[d] = indisp.get(d, 0) + int(row.get("unavailable_count") or 0)

    # 3 + 4. PriceTrack do dia e as nossas ofertas com SKU resolvido.
    pt_dia = _paged(lambda: client.table("pricetrack_daily")
                    .select("id,marketplace,sku,seller,title,min_price")
                    .eq("turno", "Diário").eq("collection_date", dia)
                    .order("id"), "pricetrack_daily", truncados)
    nossos = _paged(lambda: client.table("coletas")
                    .select("plataforma,sku_resolvido,preco,produto,url_produto,seller,buy_box_seller")
                    .eq("data", dia)
                    .not_.is_("sku_resolvido", "null")
                    .order("id"), "coletas", truncados)
    return {
        "pt_volume": pt_volume, "nosso_volume": nosso_volume, "indisp": indisp,
        "pt_dia": pt_dia, "nossos": nossos, "plataformas_dia": plataformas_dia,
        "truncados": truncados,
    }


def auditar(dados: Dict[str, Any], dia: str, cfg: AuditConfig) -> Dict[str, Any]:
    """Aplica a lógica pura e monta o relatório (dict serializável)."""
    precos = compare_prices(dados["nossos"], dados["pt_dia"], cfg)
    sem_indisp = unavailability_streak(dados["indisp"])
    lacunas = coverage_gaps(dados["plataformas_dia"],
                            {r.get("marketplace") for r in dados["pt_dia"]})
    degraus = volume_steps(dados["pt_volume"], dados["nosso_volume"], cfg)
    alertas: List[str] = []
    if dados.get("truncados"):
        alertas.append("LEITURA PARCIAL (teto de linhas) em: " + ", ".join(dados["truncados"])
                       + " — números abaixo incompletos")
    if degraus:
        alertas.append(f"degrau de volume do PriceTrack em {', '.join(d['data'] for d in degraus)}")
    if sem_indisp >= cfg.dias_sem_indisp:
        alertas.append(f"{sem_indisp} dias seguidos sem nenhuma oferta indisponível")
    if lacunas:
        alertas.append(f"sem cobertura: {', '.join(lacunas)}")
    if dados["nossos"] and not dados["pt_dia"]:
        alertas.append(f"PriceTrack sem linhas em {dia}")
    return {
        "data": dia,
        "alertas": alertas,
        "volume": {"degraus": degraus,
                   "pricetrack": dados["pt_volume"], "nosso": dados["nosso_volume"]},
        "indisponiveis": {"dias_seguidos_sem": sem_indisp, "por_dia": dados["indisp"]},
        "cobertura": {"sem_pricetrack": lacunas},
        "precos": asdict(precos),
    }


def _md(rel: Dict[str, Any]) -> str:
    """Relatório em Markdown (para e-mail ou anexo)."""
    p = rel["precos"]
    linhas = [f"# Auditoria PriceTrack × coletas Python — {rel['data']}", ""]
    linhas += ["## Alertas", ""] + ([f"- {a}" for a in rel["alertas"]] or ["- nenhum"]) + [""]
    if rel["volume"]["degraus"]:
        linhas += ["## Degrau de volume", "",
                   "| Data | PriceTrack | Base PT | Nossa coleta | Base nossa |",
                   "|---|---:|---:|---:|---:|"]
        for d in rel["volume"]["degraus"]:
            linhas.append(f"| {d['data']} | {d['pricetrack']:,} ({d['pricetrack_razao']:.0%}) | "
                          f"{d['pricetrack_base']:,} | {d['nosso']:,} ({d['nosso_razao']:.0%}) | {d['nosso_base']:,} |")
        linhas.append("")
    if p["mesmo_vendedor"]:
        linhas += ["## Preço no MESMO vendedor (o preço coletado está certo?)", "",
                   "| Marketplace | Pares | Bate (≤5%) | Diverge (>15%) |", "|---|---:|---:|---:|"]
        for mkt, v in p["mesmo_vendedor"].items():
            linhas.append(f"| {mkt} | {v['pares']} | {v['pct_bate']}% | {v['pct_divergente']}% |")
        linhas.append("")
    linhas += ["## Menor preço do SKU (o PriceTrack vê o mercado?) — só anúncio de unidade", "",
               f"{p['pares']} pares comparados. Fora da comparação por não serem uma "
               f"unidade: {p['excluidos_por_tipo'] or 'nenhum'}.", "",
               "| Marketplace | Pares | Bate (≤5%) | Diverge (>15%) | PT mais caro | Nosso mais barato fora do PT |",
               "|---|---:|---:|---:|---:|---:|"]
    for mkt, v in p["por_plataforma"].items():
        fora = v.get("pct_mais_barato_fora_do_pt")
        linhas.append(f"| {mkt} | {v['pares']} | {v['pct_bate']}% | {v['pct_divergente']}% | "
                      f"{v['pct_pt_mais_caro']}% | {'—' if fora is None else f'{fora}%'} |")
    if p["divergencias"]:
        linhas += ["", "### Maiores divergências (conferir na tela)", "",
                   "| Marketplace | SKU | Nosso | PriceTrack | Dif. | Anúncio |",
                   "|---|---|---:|---:|---:|---|"]
        for d in p["divergencias"]:
            linhas.append(f"| {d['marketplace']} | {d['sku']} | {d['nosso']:.2f} | "
                          f"{d['pricetrack']:.2f} | {d['dif_pct']:+.0f}% | {d['url']} |")
    return "\n".join(linhas) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", help="dia auditado (padrão: ontem, BRT)")
    parser.add_argument("--dias", type=int, default=14, help="janela do volume (padrão 14)")
    parser.add_argument("--saida", default=str(_PROJECT_ROOT / "logs"))
    parser.add_argument("--strict", action="store_true", help="exit 1 se houver alerta")
    args = parser.parse_args()

    dia = args.data or (now_brt().date() - timedelta(days=1)).isoformat()
    inicio = (date.fromisoformat(dia) - timedelta(days=args.dias)).isoformat()
    client = _supabase_client()
    if client is None:
        logger.error("Nenhum backend configurado (RAC_DB_DSN ou SUPABASE_URL+SUPABASE_KEY) — "
                     "auditoria abortada para não relatar 'zero' de um banco não lido.")
        return 2

    rel = auditar(coletar(client, dia, inicio), dia, AuditConfig())
    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    (saida / f"pricetrack_audit_{dia}.json").write_text(
        json.dumps(rel, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (saida / f"pricetrack_audit_{dia}.md").write_text(_md(rel), encoding="utf-8")

    for a in rel["alertas"]:
        logger.warning(f"PriceTrack: {a}")
    for mkt, v in rel["precos"]["por_plataforma"].items():
        logger.info(f"{mkt}: {v['pares']} pares, {v['pct_bate']}% batem, {v['pct_divergente']}% divergem")
    logger.success(f"Auditoria salva em {saida}/pricetrack_audit_{dia}.md")
    return 1 if (args.strict and rel["alertas"]) else 0


if __name__ == "__main__":
    sys.exit(main())
