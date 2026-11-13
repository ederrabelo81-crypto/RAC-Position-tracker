"""Extração das fontes da Nubimetrics (uma função por fonte do MAPPING.md).

Toda função recebe um `NubiClient` aberto e devolve o payload bruto da tool
(dict). A normalização fica em ``transform.py`` — aqui só se pede e se pagina.

Regras:
- categoria fixa ``MLB1646`` (Ares Condicionados);
- ranking é de **mês fechado**: pedir o mês corrente cai no anterior
  (``period_fallback``) — por isso o ETL pede o último mês fechado explícito;
- pagina até o fim (``total``), nunca trunca no top 10.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from nubimetrics.client import NubiClient, NubiToolError

CATEGORIA = "MLB1646"
DIMENSOES = ("brands", "seller", "catalog", "items", "official_store")
#: Buscas do explorador. Na 1ª rodada de produção (13/11/2026) "ar condicionado"
#: em MLB1646 já devolveu os 4.057 anúncios da categoria (busca aproximada); a
#: 2ª busca ("split") só dobrava as chamadas e estourou o limite de 100/min.
TERMOS_EXPLORADOR: Sequence[str] = ("ar condicionado",)
PAGE_SIZE = 50


def primeiro_dia(d: dt.date) -> dt.date:
    return d.replace(day=1)


def ultimo_mes_fechado(hoje: dt.date) -> dt.date:
    """1º dia do último mês fechado (em 13/11/2026 → 2026-10-01)."""
    return primeiro_dia(primeiro_dia(hoje) - dt.timedelta(days=1))


def meses_para_tras(fim: dt.date, n: int) -> List[dt.date]:
    """Os ``n`` meses terminando em ``fim`` (inclusive), do mais antigo ao mais novo."""
    out = [primeiro_dia(fim)]
    while len(out) < n:
        out.insert(0, primeiro_dia(out[0] - dt.timedelta(days=1)))
    return out


async def mercado(nubi: NubiClient, inicio: dt.date, fim: dt.date) -> Dict[str, Any]:
    """Série mensal da categoria: receita, unidades, ticket (+ estrutura de sellers)."""
    args = {"inp": {"category_id": CATEGORIA, "date_from": inicio.isoformat(), "date_to": fim.isoformat()}}
    tamanho = await nubi.call("nubi_category_market_size", args)
    estrutura = await nubi.call("nubi_category_market_structure", args)
    return {"tamanho": tamanho, "estrutura": estrutura}


async def previsao(nubi: NubiClient, mes_fechado: dt.date) -> Dict[str, Any]:
    return await nubi.call("nubi_category_forecast",
                           {"inp": {"category_id": CATEGORIA, "date": mes_fechado.isoformat()}})


async def detalhe_categoria(nubi: NubiClient, mes: dt.date) -> Dict[str, Any]:
    return await nubi.call("meli_category_detail",
                           {"inp": {"category_id": CATEGORIA, "date": mes.isoformat()}})


async def ranking(nubi: NubiClient, mes: dt.date, dimensao: str) -> Dict[str, Any]:
    """Top 100 de uma dimensão num mês fechado (uma chamada: limit=100)."""
    return await nubi.call("meli_market_rankings", {"inp": {
        "category_id": CATEGORIA, "date": mes.isoformat(), "topic": dimensao,
        "ranking_type": "gmv", "limit": 100, "offset": 0,
    }})


async def buscas(nubi: NubiClient, mes: dt.date) -> Dict[str, Any]:
    return await nubi.call("meli_search_trends_rankings", {"inp": {
        "category_id": CATEGORIA, "date": mes.isoformat(), "limit": 100, "offset": 0,
    }})


async def anuncios(nubi: NubiClient, termos: Sequence[str] = TERMOS_EXPLORADOR,
                   max_paginas: Optional[int] = None) -> List[Dict[str, Any]]:
    """Todos os anúncios da categoria pelo explorador, deduplicados por ``item_id``.

    Ordena por receita (``sort_by='gmv'``) para que um corte por
    ``max_paginas`` (só em teste/diagnóstico) fique com o topo do mercado.
    """
    vistos: Dict[str, Dict[str, Any]] = {}
    for termo in termos:
        pagina = 1
        while True:
            payload = await nubi.call("meli_publication_explorer", {"inp": {
                "title": termo, "category_id": CATEGORIA, "sort_by": "gmv", "order_by": "desc",
                "page": pagina, "page_size": PAGE_SIZE,
            }})
            linhas = payload.get("publications") or []
            for linha in linhas:
                if linha.get("item_id"):
                    vistos.setdefault(linha["item_id"], linha)
            total = int(payload.get("total") or 0)
            if not linhas or pagina * PAGE_SIZE >= total or (max_paginas and pagina >= max_paginas):
                logger.info(f"[Nubimetrics] explorador '{termo}': {pagina} página(s), "
                            f"{total} no total, {len(vistos)} únicos até aqui")
                break
            pagina += 1
    return list(vistos.values())


async def _paginar(nubi: NubiClient, tool: str, base: Dict[str, Any], chave: str,
                   limite: int = 50) -> List[Dict[str, Any]]:
    """Lista paginada por ``limit``/``offset`` até ``has_more`` falso."""
    linhas: List[Dict[str, Any]] = []
    offset = 0
    while True:
        payload = await nubi.call(tool, {"inp": {**base, "limit": limite, "offset": offset}})
        lote = payload.get(chave) or []
        linhas.extend(lote)
        if not payload.get("has_more") or not lote:
            return linhas
        offset += len(lote)


async def concorrente(nubi: NubiClient, dias: int = 30) -> List[Dict[str, Any]]:
    """Série diária do(s) concorrente(s) no(s) grupo(s) de sellers seguidos.

    A conta tem 1 slot de concorrência: na prática, 1 seller. ``is_self`` é
    rejeitado pela tool e fica de fora.
    """
    saida: List[Dict[str, Any]] = []
    vistos: set = set()
    grupos = await _paginar(nubi, "nubi_competition_seller_groups", {}, "groups")
    for grupo in grupos:
        membros = await _paginar(nubi, "nubi_competition_seller_group_members",
                                 {"group_id": grupo["group_id"]}, "members")
        for m in membros:
            # O mesmo concorrente em dois grupos daria (data, concorrente) duplicado.
            if m.get("is_self") or not m.get("member_code") or m["member_code"] in vistos:
                continue
            vistos.add(m["member_code"])
            try:
                serie = await nubi.call("nubi_competition_seller_evolution", {"inp": {
                    "seller_id_competitor": m["member_code"], "days": dias, "category_id": CATEGORIA,
                }})
            except NubiToolError as exc:
                logger.warning(f"[Nubimetrics] concorrente ignorado: {exc}")
                continue
            saida.append({"concorrente": m["member_code"], "serie": serie})
    return saida
