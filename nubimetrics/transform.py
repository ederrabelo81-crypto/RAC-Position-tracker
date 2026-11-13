"""Payload bruto da Nubimetrics → linhas das tabelas da migração 023.

Puro (sem rede): testado com as amostras reais de ``catalog/samples/``.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Optional

from nubimetrics.filters import classificar, normalizar_marca

CATEGORIA = "MLB1646"


def _mes(valor: Any) -> Optional[str]:
    """``2026-11-01T00:00:00.000+00:00`` → ``2026-11-01``."""
    if not valor:
        return None
    return str(valor)[:7] + "-01"


def _num(valor: Any) -> Optional[float]:
    """Número de verdade, ou None. As tools de concorrência devolvem strings
    arredondadas (``"1.3K"``, ``"3M"``): convertidas aqui."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    txt = str(valor).strip().replace(",", ".")
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(txt[-1:].upper(), 1.0)
    if mult != 1.0:
        txt = txt[:-1]
    try:
        return float(txt) * mult
    except ValueError:
        return None


def mercado_mensal(bruto: Dict[str, Any], hoje: dt.date) -> List[Dict[str, Any]]:
    """`nubi_category_market_size` + `_structure` → `nubi_mercado_mensal` (tipo real)."""
    tam = bruto.get("tamanho") or {}
    est = bruto.get("estrutura") or {}
    if not tam.get("data_available", True):
        return []
    linhas: Dict[str, Dict[str, Any]] = {}

    def _serie(fonte: Dict[str, Any], chave: str, coluna: str, coluna_aa: Optional[str] = None) -> None:
        for p in fonte.get(chave) or []:
            mes = _mes(p.get("date"))
            if not mes:
                continue
            linha = linhas.setdefault(mes, {"categoria_id": CATEGORIA, "mes": mes, "tipo": "real",
                                             "parcial": mes == hoje.replace(day=1).isoformat()})
            linha[coluna] = _num(p.get("value"))
            if coluna_aa:
                linha[coluna_aa] = _num(p.get("previous_period_value"))

    _serie(tam, "gmv_local", "receita_brl", "receita_brl_aa")
    _serie(tam, "successful_items", "unidades", "unidades_aa")
    _serie(tam, "avg_ticket_local", "ticket_medio")
    _serie(est, "sellers", "sellers")
    _serie(est, "sellers_professional", "sellers_prof")
    _serie(est, "listings", "anuncios")
    return sorted(linhas.values(), key=lambda r: r["mes"])


def detalhe_categoria(bruto: Dict[str, Any], linhas: List[Dict[str, Any]], mes: dt.date) -> None:
    """`meli_category_detail` → colunas de saturação na linha real do ``mes`` (in place).

    Ignora quando o MCP caiu noutro mês (``period_applied`` ≠ ``mes``).
    """
    dado = bruto.get("data") or {}
    if not dado or str(bruto.get("period_applied", ""))[:10] != mes.isoformat():
        return
    nivel = {"High": "Alta", "Medium": "Média", "Low": "Baixa"}
    for linha in linhas:
        if linha["mes"] == mes.isoformat() and linha["tipo"] == "real":
            linha.update({
                "saturacao": nivel.get(dado.get("saturation"), dado.get("saturation")),
                "oportunidade": nivel.get(dado.get("opportunity"), dado.get("opportunity")),
                "pct_catalogo": _num(dado.get("siCatalogPercentage")),
                "pct_desconto": _num(dado.get("itemsDiscountPercentage")),
                "pct_frete_gratis": _num(dado.get("siFreeShippingPercentage")),
            })


def previsao_mensal(bruto: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`nubi_category_forecast` → `nubi_mercado_mensal` (tipo previsao)."""
    out = []
    for p in bruto.get("projection") or []:
        mes = _mes(p.get("date"))
        if not mes:
            continue
        unid = _num(p.get("successful_items_forecasted"))
        receita = _num(p.get("gmv_local_forecasted"))
        out.append({
            "categoria_id": CATEGORIA, "mes": mes, "tipo": "previsao", "parcial": False,
            "receita_brl": receita, "unidades": unid,
            "ticket_medio": (receita / unid) if receita and unid else None,
            "receita_brl_aa": _num(p.get("gmv_local_before_forecasted")),
        })
    return out


def ranking(bruto: Dict[str, Any], mes: dt.date, dimensao: str) -> List[Dict[str, Any]]:
    """`meli_market_rankings` / `meli_search_trends_rankings` → `nubi_ranking_mensal`.

    Devolve [] quando o MCP caiu no mês anterior (``period_fallback``): gravar
    isso sob o mês pedido duplicaria o mês anterior com outro rótulo.
    """
    if bruto.get("period_fallback"):
        return []
    out = []
    if dimensao == "search_terms":
        for ordem, t in enumerate(bruto.get("terms") or [], start=1):
            out.append({
                "categoria_id": CATEGORIA, "mes": mes.isoformat(), "dimensao": dimensao,
                "posicao": ordem, "nome": str(t.get("search_term") or "").strip(),
                "tendencia": t.get("ranking_trend"),
                "posicao_anterior": int(t["position_before"]) if t.get("position_before") is not None else None,
            })
        return out
    # Posição = ordem da lista (1..N), não o `position` da API: o ranking de
    # catálogo traz empates (duas posições 40 em out/26), e a posição é a chave.
    for ordem, r in enumerate(bruto.get("items") or [], start=1):
        nome = str(r.get("name") or "").strip()
        if dimensao == "brands":
            nome = normalizar_marca(nome)
        out.append({
            "categoria_id": CATEGORIA, "mes": mes.isoformat(), "dimensao": dimensao,
            "posicao": ordem, "nome": nome,
            "receita_brl": _num(r.get("gmv")), "unidades": _num(r.get("units")),
            "share_brl": _num(r.get("share")),
            "cresc_receita_pct": _num(r.get("growth_gmv")), "cresc_unid_pct": _num(r.get("growth_si")),
            "tendencia": r.get("ranking_trend"),
            # `is not None`: zero explícito é dado (0 sellers), não ausência.
            "qtd_sellers": int(r["sellers_quantity"]) if r.get("sellers_quantity") is not None else None,
            "qtd_anuncios": int(r["items_quantity"]) if r.get("items_quantity") is not None else None,
            "catalogo_pct": _num(r.get("catalog_percentage")),
        })
    return out


def anuncios(brutos: Iterable[Dict[str, Any]], snapshot: dt.date) -> List[Dict[str, Any]]:
    """`meli_publication_explorer` → `nubi_anuncios` (foto do dia, classificada)."""
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    out = []
    for r in brutos:
        c = classificar(r.get("title"), r.get("model"))
        loja = r.get("official_store_alias")
        out.append({
            "item_hash": r["item_id"], "snapshot_date": snapshot.isoformat(),
            "titulo": r.get("title"), "marca": normalizar_marca(r.get("brand")) or None,
            "modelo": r.get("model"), "sku": r.get("sku"), "gtin": r.get("gtin"),
            "seller": r.get("seller"), "seller_hash": r.get("seller_id_hash"),
            "loja_oficial": loja if loja and loja != "noOS" else None,
            "preco": _num(r.get("price")), "receita_brl": _num(r.get("gmv")),
            "unidades": _num(r.get("units")), "unidades_total": _num(r.get("cum_units")),
            "catalogo": bool(r.get("is_catalog")), "full_": bool(r.get("full")),
            "flex": bool(r.get("flex")), "frete_gratis": bool(r.get("free_shipping")),
            "exposicao": r.get("exposition"),
            "segmento": c.segmento, "btu": c.btu, "faixa_btu": c.faixa_btu,
            "voltagem": c.voltagem, "inverter": c.inverter, "match_source": c.match_source,
            "criado_em_ml": r.get("creation_date"), "atualizado_em": agora,
        })
    return out


def concorrente(brutos: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`nubi_competition_seller_evolution` → `nubi_concorrente_diario`.

    Pontos vêm como ``"AAAA-MM-DD valor"``, uma série por métrica.
    """
    colunas = {"visits": "visitas", "si": "unidades", "gmv": "receita_brl",
               "items": "anuncios", "convertion": "conversao_pct"}
    out: List[Dict[str, Any]] = []
    for c in brutos:
        por_dia: Dict[str, Dict[str, Any]] = {}
        for metrica in (c.get("serie") or {}).get("metrics") or []:
            col = colunas.get(metrica.get("name"))
            if not col:
                continue
            for ponto in metrica.get("points") or []:
                partes = str(ponto).split(" ", 1)
                if len(partes) != 2:
                    continue
                linha = por_dia.setdefault(partes[0], {"data": partes[0], "concorrente": c["concorrente"]})
                valor = _num(partes[1])
                linha[col] = int(valor) if col == "anuncios" and valor is not None else valor
        out.extend(por_dia.values())
    return out


def checar_consistencia(ranking_marcas: List[Dict[str, Any]], mercado: List[Dict[str, Any]],
                        tolerancia: float = 0.02) -> Optional[str]:
    """Soma das marcas do mês × total da categoria no mesmo mês.

    Returns:
        ``None`` se bate (± ``tolerancia``), ou a frase do desvio. Em
        out/2026: R$ 223,5 mi nas duas pontas.
    """
    if not ranking_marcas:
        return None
    mes = ranking_marcas[0]["mes"]
    soma = sum(r.get("receita_brl") or 0 for r in ranking_marcas)
    total = next((m.get("receita_brl") for m in mercado if m["mes"] == mes and m["tipo"] == "real"), None)
    if not total:
        return f"sem total da categoria para {mes} — consistência não verificada"
    desvio = soma / total - 1
    if abs(desvio) > tolerancia:
        return (f"soma das marcas {mes} = R$ {soma / 1e6:.1f} mi vs categoria "
                f"R$ {total / 1e6:.1f} mi ({desvio:+.1%})")
    return None
