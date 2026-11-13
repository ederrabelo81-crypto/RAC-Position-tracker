"""Contas do painel 🛒 Sell-out ML — puras (pandas, sem Streamlit, sem rede).

Testadas em ``tests/test_nubimetrics_analytics.py``. A página
(``nubimetrics/page.py``) só lê, filtra e desenha.

Convenções:
- marca sempre normalizada (``MIDEA``); exibição via ``filters.marca_exibicao``;
- "Premium" = exposição ``gold_pro`` (o explorador não diz Clássico/Premium,
  mas a exposição é o tier do ML: ``gold_pro`` = Premium, ``gold_special`` =
  Clássico);
- receita/unidades do explorador são da **janela do Nubimetrics** (sem data
  explícita — ver MAPPING.md §4.4), não de um mês civil.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from nubimetrics.filters import (
    SEG_HW_INVERTER,
    faixa_btu,
    is_midea,
    marca_exibicao,
    normalizar_marca,
    parse_btu,
    parse_voltagem,
)

MIDEA = "MIDEA"
LG = "LG"
MARCAS_PADRAO = ("MIDEA", "LG", "PHILCO", "GREE", "SAMSUNG", "ELGIN", "TCL", "ELECTROLUX", "AGRATTO")
PREMIUM = "gold_pro"
LIMIAR_PENETRACAO = 0.8
LIMIAR_GAP_PRECO = 0.10
UPLIFT_EXPOSICAO = 0.15


# ── formatação pt-BR ────────────────────────────────────────────────────────

def _br(numero: str) -> str:
    return numero.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_brl(v: Optional[float]) -> str:
    """``33_970_000`` → ``R$ 34,0 mi``; ``345_000`` → ``R$ 345 mil``."""
    if v is None or pd.isna(v):
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"R$ {_br(f'{v / 1e9:,.1f}')} bi"
    if a >= 1e6:
        return f"R$ {_br(f'{v / 1e6:,.1f}')} mi"
    if a >= 1e4:
        return f"R$ {_br(f'{v / 1e3:,.0f}')} mil"
    return f"R$ {_br(f'{v:,.0f}')}"


def fmt_int(v: Optional[float], sufixo: str = "") -> str:
    """``15567`` → ``15.567`` (+ sufixo, ex. ``" un"``)."""
    if v is None or pd.isna(v):
        return "—"
    return _br(f"{v:,.0f}") + sufixo


def fmt_pct(v: Optional[float], casas: int = 1) -> str:
    """Percentual já em % (``19.6`` → ``19,6%``)."""
    if v is None or pd.isna(v):
        return "—"
    return _br(f"{v:,.{casas}f}") + "%"


def fmt_pp(v: Optional[float]) -> str:
    """Pontos percentuais com sinal (``1.23`` → ``+1,2 p.p.``)."""
    if v is None or pd.isna(v):
        return "—"
    return _br(f"{v:+.1f}") + " p.p."


# ── mercado e marcas ────────────────────────────────────────────────────────

def canonizar_marcas(ranking: pd.DataFrame) -> pd.DataFrame:
    """Agrega as sub-linhas Midea do ranking de marcas numa linha ``MIDEA``.

    Em out/2026 a Nubimetrics listou MIDEA (13,3%), SPRINGER MIDEA (4,2%),
    MIDEA BRANCA e "CARRIER | MIDEA" separados — o share da Midea no topo do
    painel saía subestimado. Soma receita, unidades e share; crescimento e %
    catálogo ponderados pela receita; reordena as posições do mês.
    """
    if ranking.empty or "dimensao" not in ranking:
        return ranking
    b = ranking[ranking["dimensao"] == "brands"].copy()
    if b.empty:
        return ranking
    b["nome"] = b["nome"].map(lambda n: MIDEA if is_midea(n) else n)
    for c in ("receita_brl", "unidades", "share_brl", "cresc_receita_pct", "cresc_unid_pct", "catalogo_pct"):
        if c in b:
            b[c] = pd.to_numeric(b[c], errors="coerce")
    # Receita/unidades do mês anterior implícitas no crescimento → recompõe o Δ do grupo.
    b["_rec_ant"] = b["receita_brl"] / (1 + b["cresc_receita_pct"] / 100)
    b["_uni_ant"] = b["unidades"] / (1 + b["cresc_unid_pct"] / 100)
    b["_cat_w"] = b["catalogo_pct"] * b["receita_brl"]
    chave = ["categoria_id", "mes", "dimensao", "nome"]
    # Soma "estrita": um componente sem o mês anterior (crescimento nulo, como
    # a MIDEA BRANCA de out/26) deixa o Δ do grupo indisponível, em vez de
    # entrar como zero e o painel mostrar um Δ parcial como se fosse completo.
    def _soma_estrita(s: pd.Series) -> float:
        return s.sum(min_count=len(s))

    # Nº de sellers não soma (o mesmo seller vende MIDEA e SPRINGER MIDEA) e a
    # união não está no payload: grupo de 2+ grafias fica sem o número.
    def _sellers(s: pd.Series) -> float:
        return s.iloc[0] if len(s) == 1 else np.nan

    g = b.sort_values("receita_brl", ascending=False).groupby(chave, as_index=False, sort=False).agg(
        receita_brl=("receita_brl", "sum"), unidades=("unidades", "sum"), share_brl=("share_brl", "sum"),
        _rec_ant=("_rec_ant", _soma_estrita), _uni_ant=("_uni_ant", _soma_estrita), _cat_w=("_cat_w", "sum"),
        tendencia=("tendencia", "first"), qtd_sellers=("qtd_sellers", _sellers),
        **({"atualizado_em": ("atualizado_em", "max")} if "atualizado_em" in b else {}),
    )
    g["cresc_receita_pct"] = (g["receita_brl"] / g["_rec_ant"].replace(0, np.nan) - 1) * 100
    g["cresc_unid_pct"] = (g["unidades"] / g["_uni_ant"].replace(0, np.nan) - 1) * 100
    g["catalogo_pct"] = g["_cat_w"] / g["receita_brl"].replace(0, np.nan)
    g = g.drop(columns=["_rec_ant", "_uni_ant", "_cat_w"])
    g["posicao"] = g.groupby("mes")["receita_brl"].rank(ascending=False, method="first").astype(int)
    resto = ranking[ranking["dimensao"] != "brands"]
    return pd.concat([resto, g], ignore_index=True)


def meses_disponiveis(ranking: pd.DataFrame, dimensao: str = "brands") -> List[pd.Timestamp]:
    """Meses com ranking da dimensão, do mais novo ao mais antigo."""
    if ranking.empty:
        return []
    m = ranking.loc[ranking["dimensao"] == dimensao, "mes"]
    return sorted(pd.to_datetime(m).unique(), reverse=True)


def ranking_mes(ranking: pd.DataFrame, dimensao: str, mes: pd.Timestamp) -> pd.DataFrame:
    """Linhas de uma dimensão num mês, por posição."""
    r = ranking[(ranking["dimensao"] == dimensao) & (pd.to_datetime(ranking["mes"]) == pd.Timestamp(mes))]
    return r.sort_values("posicao").reset_index(drop=True)


def kpis_marca(ranking: pd.DataFrame, mes: pd.Timestamp, marca: str = MIDEA,
               rival: str = LG) -> Dict[str, Optional[float]]:
    """KPIs de uma marca no mês fechado, com Δ vs mês anterior e vs rival."""
    meses = meses_disponiveis(ranking)
    atual = ranking_mes(ranking, "brands", mes)
    anterior_mes = next((m for m in meses if m < pd.Timestamp(mes)), None)
    anterior = ranking_mes(ranking, "brands", anterior_mes) if anterior_mes is not None else pd.DataFrame()

    def _linha(df: pd.DataFrame, nome: str) -> Optional[pd.Series]:
        if df.empty:
            return None
        hit = df[df["nome"] == nome]
        return hit.iloc[0] if len(hit) else None

    a, r, p = _linha(atual, marca), _linha(atual, rival), _linha(anterior, marca)
    total = float(atual["receita_brl"].sum()) if not atual.empty else None
    unid_total = float(atual["unidades"].sum()) if not atual.empty else None
    share = float(a["share_brl"]) if a is not None else None
    share_unid = float(a["unidades"]) / unid_total * 100 if a is not None and unid_total else None
    return {
        "mercado_receita": total,
        "mercado_unidades": unid_total,
        "ticket_medio": total / unid_total if total and unid_total else None,
        "receita": float(a["receita_brl"]) if a is not None else None,
        "unidades": float(a["unidades"]) if a is not None else None,
        "share": share,
        "share_unid": share_unid,
        "posicao": int(a["posicao"]) if a is not None else None,
        "delta_share_pp": share - float(p["share_brl"]) if a is not None and p is not None else None,
        "rival_share": float(r["share_brl"]) if r is not None else None,
        "gap_rival_pp": share - float(r["share_brl"]) if a is not None and r is not None else None,
        "mes_anterior": anterior_mes,
    }


# ── período: mês, ano (YTD) ou dia ──────────────────────────────────────────

VISAO_MES, VISAO_ANO, VISAO_DIA = "Mês", "Ano (YTD)", "Dia"


@dataclass(frozen=True)
class Periodo:
    """Recorte de tempo do painel.

    Attributes:
        visao: ``Mês`` | ``Ano (YTD)`` | ``Dia``.
        meses: meses FECHADOS cobertos (rankings e mercado são mensais).
        comp: meses da comparação (mês anterior; no YTD, os mesmos meses do
            ano anterior). Vazio = sem comparação.
        rotulo / rotulo_comp: texto para o painel ("10/2026", "YTD 2026 (jan–out)").
        comp_completo: a comparação tem TODOS os meses equivalentes. Sem isso o
            Δ de receita não é calculado (seria parcial contra completo).
        dia: o dia escolhido na visão ``Dia`` (foto de anúncios/concorrente).
    """

    visao: str
    meses: Tuple[pd.Timestamp, ...]
    comp: Tuple[pd.Timestamp, ...] = ()
    rotulo: str = ""
    rotulo_comp: str = ""
    comp_completo: bool = False
    dia: Optional[date] = None


_MESES_PT = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def anos_disponiveis(ranking: pd.DataFrame) -> List[int]:
    """Anos com ao menos um mês de ranking de marcas, do mais novo ao mais antigo."""
    return sorted({m.year for m in meses_disponiveis(ranking)}, reverse=True)


def periodo_mes(mes: pd.Timestamp, ranking: pd.DataFrame) -> Periodo:
    """Um mês fechado, comparado ao mês anterior (se houver)."""
    mes = pd.Timestamp(mes)
    disp = set(meses_disponiveis(ranking))
    ant = mes - pd.DateOffset(months=1)
    comp = (ant,) if ant in disp else ()
    return Periodo(VISAO_MES, (mes,), comp, mes.strftime("%m/%Y"),
                   ant.strftime("%m/%Y") if comp else "", bool(comp))


def periodo_ano(ano: int, ranking: pd.DataFrame) -> Periodo:
    """YTD: os meses fechados do ano que existem no banco, contra os MESMOS meses
    do ano anterior."""
    disp = sorted(m for m in meses_disponiveis(ranking) if m.year == ano)
    if not disp:
        return Periodo(VISAO_ANO, (), (), f"YTD {ano}")
    disp_set = set(meses_disponiveis(ranking))
    comp_ideal = [m - pd.DateOffset(years=1) for m in disp]
    comp = tuple(m for m in comp_ideal if m in disp_set)
    faixa = f"{_MESES_PT[disp[0].month - 1]}–{_MESES_PT[disp[-1].month - 1]}"
    return Periodo(VISAO_ANO, tuple(disp), comp, f"YTD {ano} ({faixa})",
                   f"YTD {ano - 1} ({faixa})" if comp else "", len(comp) == len(comp_ideal))


def periodo_dia(dia: date, ranking: pd.DataFrame) -> Periodo:
    """Dia: anúncios/concorrente do dia; o mensal usa o mês FECHADO de referência
    (o do dia, se já fechado; senão o último fechado disponível)."""
    disp = meses_disponiveis(ranking)
    alvo = pd.Timestamp(dia).replace(day=1)
    ref = next((m for m in disp if m <= alvo), disp[0] if disp else None)
    if ref is None:
        return Periodo(VISAO_DIA, (), (), dia.strftime("%d/%m/%Y"), dia=dia)
    base = periodo_mes(ref, ranking)
    return Periodo(VISAO_DIA, base.meses, base.comp, f"{dia:%d/%m/%Y} (mensal: {base.rotulo})",
                   base.rotulo_comp, base.comp_completo, dia)


def ranking_periodo(ranking: pd.DataFrame, dimensao: str, per: Periodo) -> pd.DataFrame:
    """Ranking de uma dimensão no período: o mês como veio, ou a soma de vários meses.

    Em mais de um mês: receita e unidades somadas; share recalculado sobre o
    mercado do período (soma das marcas); Δ contra os meses de comparação só se a
    comparação estiver completa; % catálogo ponderado pela receita; nº de sellers
    vazio (não soma entre meses); ``meses_presente`` = em quantos meses o nome
    apareceu no top 100 (o top 100 por mês deixa cauda de fora — ver MAPPING).
    """
    if not per.meses:
        return pd.DataFrame()
    if len(per.meses) == 1:
        out = ranking_mes(ranking, dimensao, per.meses[0]).copy()
        if not out.empty:
            out["meses_presente"] = 1
        return out
    return _agregar_meses(ranking, dimensao, per.meses, per.comp if per.comp_completo else ())


def _agregar_meses(ranking: pd.DataFrame, dimensao: str, meses: Sequence[pd.Timestamp],
                   comp: Sequence[pd.Timestamp] = ()) -> pd.DataFrame:
    r = ranking[ranking["dimensao"] == dimensao].copy()
    r["mes"] = pd.to_datetime(r["mes"])
    atual = r[r["mes"].isin(list(meses))]
    if atual.empty:
        return pd.DataFrame()
    if dimensao == "search_terms":
        g = atual.groupby("nome", as_index=False).agg(
            posicao_media=("posicao", "mean"), melhor_posicao=("posicao", "min"),
            meses_presente=("mes", "nunique"))
        g = g.sort_values(["posicao_media", "melhor_posicao"]).reset_index(drop=True)
        g["posicao"] = range(1, len(g) + 1)
        ult = atual[atual["mes"] == atual["mes"].max()].set_index("nome")["tendencia"]
        g["tendencia"] = g["nome"].map(ult)
        g["posicao_anterior"] = np.nan
        return g
    for c in ("receita_brl", "unidades", "catalogo_pct"):
        if c in atual:
            atual[c] = pd.to_numeric(atual[c], errors="coerce")
    atual["_cat_w"] = atual.get("catalogo_pct", np.nan) * atual["receita_brl"]
    g = atual.groupby("nome", as_index=False).agg(
        receita_brl=("receita_brl", "sum"), unidades=("unidades", "sum"), _cat_w=("_cat_w", "sum"),
        meses_presente=("mes", "nunique"))
    # Mercado do período = soma das marcas (62 marcas em out/26 = 100% do total).
    mercado = pd.to_numeric(ranking.loc[(ranking["dimensao"] == "brands")
                                        & pd.to_datetime(ranking["mes"]).isin(list(meses)), "receita_brl"],
                            errors="coerce").sum() if dimensao != "brands" else g["receita_brl"].sum()
    g["share_brl"] = g["receita_brl"] / mercado * 100 if mercado else np.nan
    g["catalogo_pct"] = g["_cat_w"] / g["receita_brl"].replace(0, np.nan)
    g = g.drop(columns=["_cat_w"])
    if comp:
        ant = r[r["mes"].isin(list(comp))].copy()
        for c in ("receita_brl", "unidades"):
            ant[c] = pd.to_numeric(ant[c], errors="coerce")
        a = ant.groupby("nome")[["receita_brl", "unidades"]].sum()
        g["cresc_receita_pct"] = (g["receita_brl"] / g["nome"].map(a["receita_brl"]).replace(0, np.nan) - 1) * 100
        g["cresc_unid_pct"] = (g["unidades"] / g["nome"].map(a["unidades"]).replace(0, np.nan) - 1) * 100
    else:
        g["cresc_receita_pct"] = np.nan
        g["cresc_unid_pct"] = np.nan
    ult = atual[atual["mes"] == atual["mes"].max()].set_index("nome")["tendencia"]
    g["tendencia"] = g["nome"].map(ult)
    g["qtd_sellers"] = np.nan
    g = g.sort_values("receita_brl", ascending=False).reset_index(drop=True)
    g["posicao"] = range(1, len(g) + 1)
    g["dimensao"] = dimensao
    return g


def kpis_periodo(ranking: pd.DataFrame, per: Periodo, marca: str = MIDEA,
                 rival: str = LG) -> Dict[str, Optional[float]]:
    """KPIs de uma marca no período (mês ou YTD), com Δ de share vs a comparação."""
    atual = ranking_periodo(ranking, "brands", per)
    # Comparação só se completa: YTD de 10 meses contra 1 mês do ano anterior
    # mediria a falta de histórico, não o mercado.
    if per.comp and per.comp_completo:
        base = Periodo(per.visao, per.comp)
        anterior = ranking_periodo(ranking, "brands", base)
    else:
        anterior = pd.DataFrame()

    def _linha(df: pd.DataFrame, nome: str) -> Optional[pd.Series]:
        if df.empty:
            return None
        hit = df[df["nome"] == nome]
        return hit.iloc[0] if len(hit) else None

    a, r, p = _linha(atual, marca), _linha(atual, rival), _linha(anterior, marca)
    total = float(atual["receita_brl"].sum()) if not atual.empty else None
    unid_total = float(atual["unidades"].sum()) if not atual.empty else None
    total_ant = float(anterior["receita_brl"].sum()) if not anterior.empty else None
    share = float(a["share_brl"]) if a is not None else None
    return {
        "mercado_receita": total,
        "mercado_unidades": unid_total,
        "ticket_medio": total / unid_total if total and unid_total else None,
        "receita": float(a["receita_brl"]) if a is not None else None,
        "unidades": float(a["unidades"]) if a is not None else None,
        "share": share,
        "share_unid": float(a["unidades"]) / unid_total * 100 if a is not None and unid_total else None,
        "posicao": int(a["posicao"]) if a is not None else None,
        "delta_share_pp": share - float(p["share_brl"]) if a is not None and p is not None else None,
        "rival_share": float(r["share_brl"]) if r is not None else None,
        "gap_rival_pp": share - float(r["share_brl"]) if a is not None and r is not None else None,
        # Δ de receita do mercado só com a comparação completa (parcial × completo engana).
        "delta_mercado_pct": (total / total_ant - 1) * 100
        if total and total_ant and per.comp_completo else None,
        "mes_anterior": per.comp[-1] if per.comp else None,
        "rotulo": per.rotulo,
        "rotulo_comp": per.rotulo_comp,
    }


def mercado_periodo(mercado: pd.DataFrame, per: Periodo) -> Dict[str, Optional[float]]:
    """Total da categoria (série `nubi_mercado_mensal`, tipo real) no período,
    com o mesmo período do ano anterior (colunas *_aa da própria Nubimetrics)."""
    if mercado.empty or not per.meses:
        return {}
    m = mercado[mercado["tipo"] == "real"].copy()
    m["mes"] = pd.to_datetime(m["mes"])
    sel = m[m["mes"].isin(list(per.meses))]
    if sel.empty:
        return {}
    rec = pd.to_numeric(sel["receita_brl"], errors="coerce").sum()
    aa = pd.to_numeric(sel["receita_brl_aa"], errors="coerce")
    return {
        "receita": rec,
        "unidades": pd.to_numeric(sel["unidades"], errors="coerce").sum(),
        "receita_aa": aa.sum() if aa.notna().all() else None,
        "yoy_pct": (rec / aa.sum() - 1) * 100 if aa.notna().all() and aa.sum() else None,
        "meses": len(sel),
    }


def brandshare(ranking: pd.DataFrame, top: int = 4, foco: str = MIDEA) -> pd.DataFrame:
    """Série mensal de share (R$) da marca foco + as ``top`` maiores do último mês."""
    b = ranking[ranking["dimensao"] == "brands"].copy()
    if b.empty:
        return b
    b["mes"] = pd.to_datetime(b["mes"])
    ultimo = b[b["mes"] == b["mes"].max()].sort_values("receita_brl", ascending=False)
    marcas = [foco] + [m for m in ultimo["nome"] if m != foco][:top]
    out = b[b["nome"].isin(marcas)][["mes", "nome", "share_brl", "receita_brl", "unidades"]]
    out = out.assign(marca=out["nome"].map(marca_exibicao))
    return out.sort_values(["mes", "nome"]).reset_index(drop=True)


# ── anúncios (foto do explorador) ───────────────────────────────────────────

def preparar_anuncios(df: pd.DataFrame, parceiros: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Tipos, marca de exibição, flags e nome de seller para o painel."""
    if df.empty:
        return df
    out = df.copy()
    for col in ("preco", "receita_brl", "unidades", "unidades_total", "btu"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["marca"] = out["marca"].fillna("").map(normalizar_marca)
    # Sub-linhas (MIDEA AI ECOMASTER, SPRINGER MIDEA…) contam como MIDEA em
    # toda conta do painel.
    out.loc[out["marca"].map(is_midea), "marca"] = MIDEA
    out["marca_exib"] = out["marca"].map(marca_exibicao)
    out["is_midea"] = out["marca"].map(is_midea)
    out["premium"] = out["exposicao"].eq(PREMIUM)
    nomes = {}
    if parceiros is not None and not parceiros.empty:
        nomes = dict(zip(parceiros["nickname"], parceiros["display_name"]))
    out["seller_exib"] = out["seller"].map(lambda s: nomes.get(s, s))
    out["parceiro"] = out["seller"].isin(set(nomes)) if nomes else False
    if parceiros is not None and not parceiros.empty and "is_midea_partner" in parceiros:
        ativos = set(parceiros.loc[parceiros["is_midea_partner"].astype(bool), "nickname"])
        out["parceiro"] = out["seller"].isin(ativos)
    return out


def filtrar(df: pd.DataFrame, segmentos: Sequence[str] = (SEG_HW_INVERTER,),
            faixas: Sequence[str] = (), voltagens: Sequence[str] = (),
            marcas: Sequence[str] = (), sellers: Sequence[str] = (),
            so_catalogo: bool = False, so_full: bool = False) -> pd.DataFrame:
    """Recorte do painel. Lista vazia = sem filtro naquela dimensão."""
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)
    if segmentos:
        m &= df["segmento"].isin(segmentos)
    if faixas:
        m &= df["faixa_btu"].isin(faixas)
    if voltagens:
        m &= df["voltagem"].isin(voltagens)
    if marcas:
        m &= df["marca"].isin(marcas)
    if sellers:
        m &= df["seller"].isin(sellers)
    if so_catalogo:
        m &= df["catalogo"].astype(bool)
    if so_full:
        m &= df["full_"].astype(bool)
    return df[m]


def origem_classificacao(df: pd.DataFrame) -> Dict[str, float]:
    """% da receita por origem da classificação (auditoria do recorte)."""
    if df.empty or df["receita_brl"].sum() == 0:
        return {}
    s = df.groupby("match_source")["receita_brl"].sum()
    return (s / s.sum() * 100).round(1).to_dict()


def share_marcas(df: pd.DataFrame) -> pd.DataFrame:
    """Receita, unidades e share por marca no recorte."""
    if df.empty:
        return pd.DataFrame(columns=["marca", "marca_exib", "receita_brl", "unidades", "share", "preco_medio"])
    g = df.groupby(["marca", "marca_exib"], as_index=False).agg(
        receita_brl=("receita_brl", "sum"), unidades=("unidades", "sum"))
    g["share"] = g["receita_brl"] / g["receita_brl"].sum() * 100
    g["preco_medio"] = g["receita_brl"] / g["unidades"].replace(0, np.nan)
    return g.sort_values("receita_brl", ascending=False).reset_index(drop=True)


def heatmap_btu_marca(df: pd.DataFrame, marcas: Sequence[str] = MARCAS_PADRAO) -> pd.DataFrame:
    """Share (%) de cada marca dentro de cada faixa de BTU."""
    d = df.dropna(subset=["faixa_btu"])
    if d.empty:
        return pd.DataFrame()
    piv = d.pivot_table(index="marca_exib", columns="faixa_btu", values="receita_brl",
                        aggfunc="sum", fill_value=0)
    piv = piv / piv.sum(axis=0).replace(0, np.nan) * 100
    exib = [marca_exibicao(m) for m in marcas]
    linhas = [m for m in exib if m in piv.index]
    cols = [c for c in ("≤7k", "9k", "12k", "18k", "22–24k", "30k+") if c in piv.columns]
    return piv.loc[linhas, cols].round(1)


def _sku_chave(df: pd.DataFrame) -> pd.Series:
    """Chave de SKU: modelo; se vazio/genérico, os 40 primeiros caracteres do título."""
    chave = df["modelo"].fillna("").astype(str).str.strip()
    generico = chave.isin(["", "INVERTER"])
    return chave.where(~generico, df["titulo"].fillna("").astype(str).str.slice(0, 40))


def top_skus_midea(df: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """SKUs Midea por receita (agrupados por modelo, senão título) e onde vendem."""
    m = df[df["is_midea"]].copy()
    if m.empty:
        return pd.DataFrame()
    m["sku_chave"] = _sku_chave(m)
    g = m.groupby("sku_chave").agg(
        titulo=("titulo", "first"), btu=("faixa_btu", "first"),
        receita_brl=("receita_brl", "sum"), unidades=("unidades", "sum"),
        preco_mediano=("preco", "median"), n_sellers=("seller", "nunique"),
        sellers=("seller_exib", lambda s: ", ".join(sorted(set(s))[:6])),
    ).sort_values("receita_brl", ascending=False).head(n).reset_index()
    return g


def penetracao(df: pd.DataFrame) -> pd.DataFrame:
    """Por seller: share da Midea dentro dele × share da Midea no recorte.

    Índice de penetração = share no seller ÷ share no mercado (1,0 = média;
    < 0,8 = Midea sub-representada no seller).
    """
    if df.empty:
        return pd.DataFrame()
    mercado = df["receita_brl"].sum()
    midea_mercado = df.loc[df["is_midea"], "receita_brl"].sum() / mercado * 100 if mercado else np.nan
    g = df.groupby(["seller", "seller_exib", "parceiro"], as_index=False).agg(
        receita_brl=("receita_brl", "sum"), unidades=("unidades", "sum"),
        anuncios=("item_hash", "count"),
        catalogo_pct=("catalogo", "mean"), full_pct=("full_", "mean"), premium_pct=("premium", "mean"),
    )
    mid = df[df["is_midea"]].groupby("seller")["receita_brl"].sum()
    g["receita_midea"] = g["seller"].map(mid).fillna(0.0)
    g["share_midea"] = g["receita_midea"] / g["receita_brl"].replace(0, np.nan) * 100
    g["share_midea_mercado"] = midea_mercado
    g["indice_penetracao"] = g["share_midea"] / midea_mercado if midea_mercado else np.nan
    g["preco_medio"] = g["receita_brl"] / g["unidades"].replace(0, np.nan)
    g["share_seller"] = g["receita_brl"] / mercado * 100 if mercado else np.nan
    for c in ("catalogo_pct", "full_pct", "premium_pct"):
        g[c] = g[c] * 100
    return g.sort_values("receita_brl", ascending=False).reset_index(drop=True)


def mix_marcas_seller(df: pd.DataFrame, sellers: Iterable[str], marcas: Sequence[str] = MARCAS_PADRAO[:6]) -> pd.DataFrame:
    """Mix de marcas (% da receita) dentro de cada seller — barras 100%."""
    d = df[df["seller"].isin(list(sellers))].copy()
    if d.empty:
        return pd.DataFrame()
    d["marca_grupo"] = np.where(d["marca"].isin(marcas), d["marca_exib"], "Outras")
    g = d.groupby(["seller_exib", "marca_grupo"], as_index=False)["receita_brl"].sum()
    g["pct"] = g["receita_brl"] / g.groupby("seller_exib")["receita_brl"].transform("sum") * 100
    return g


# ── oportunidades de sell-out (regras explicáveis) ──────────────────────────

def _opp(seller: str, sku: str, evidencia: str, acao: str, impacto: float, regra: str) -> Dict[str, object]:
    return {"seller": seller, "sku_ou_faixa": sku, "evidencia": evidencia, "acao": acao,
            "impacto_brl": float(max(impacto, 0.0)), "regra": regra}


def oportunidades(df: pd.DataFrame, buscas: Optional[pd.DataFrame] = None,
                  so_parceiros: bool = True) -> pd.DataFrame:
    """Lista de oportunidades ordenada por impacto estimado (R$ na janela do explorador).

    Regras (todas com a evidência em números):
    1. **Penetração** < 0,8 num BTU onde a Midea é forte → ampliar sortimento/visibilidade.
       Impacto = (share Midea no BTU − share no seller) × receita do seller no BTU.
    2. **SKU ausente**: top-10 SKU Midea do mercado que o parceiro não anuncia,
       num BTU que ele vende.
    2'. (impacto da regra 2 = receita do SKU ÷ (sellers que já o têm + 1).)
    3. **Exposição/logística**: anúncio Midea Clássico/sem Full/fora do catálogo
       onde a maioria dos concorrentes no MESMO seller tem. Impacto = +15% da
       receita do anúncio (hipótese explícita, ajuste no código: UPLIFT_EXPOSICAO).
    4. **Preço**: mediana Midea > LG em +10% no mesmo BTU. Impacto = receita da
       LG no BTU × gap acima do limiar (receita que a Midea disputa).
    5. **Busca**: termo de busca subindo num BTU onde o share da Midea está
       abaixo do share total. Impacto = (share total − share no BTU) × receita do BTU.
    """
    if df.empty:
        return pd.DataFrame(columns=["prioridade", "seller", "sku_ou_faixa", "evidencia", "acao", "impacto_brl", "regra"])
    linhas: List[Dict[str, object]] = []
    alvo = df[df["parceiro"]] if so_parceiros and df["parceiro"].any() else df
    total = df["receita_brl"].sum()
    midea_total = df.loc[df["is_midea"], "receita_brl"].sum() / total if total else 0.0

    por_faixa = df.dropna(subset=["faixa_btu"]).groupby("faixa_btu")
    share_faixa = (por_faixa.apply(lambda g: g.loc[g["is_midea"], "receita_brl"].sum() / g["receita_brl"].sum()
                                   if g["receita_brl"].sum() else 0.0, include_groups=False)
                   if len(df.dropna(subset=["faixa_btu"])) else pd.Series(dtype=float))

    # 1. penetração por seller × faixa
    for (seller, faixa), g in alvo.dropna(subset=["faixa_btu"]).groupby(["seller_exib", "faixa_btu"]):
        rec = g["receita_brl"].sum()
        sf = share_faixa.get(faixa, 0.0)
        if rec <= 0 or sf <= 0 or sf < midea_total * 0.9:
            continue
        sm = g.loc[g["is_midea"], "receita_brl"].sum() / rec
        if sm / sf < LIMIAR_PENETRACAO:
            linhas.append(_opp(seller, faixa,
                               f"Midea tem {sm:.0%} da receita do seller em {faixa} contra {sf:.0%} no mercado "
                               f"(índice {sm / sf:.2f})",
                               "Ampliar sortimento/visibilidade Midea nesta faixa", (sf - sm) * rec, "penetração"))

    # 2. SKU Midea top ausente no parceiro
    skus = top_skus_midea(df, n=10)
    if not skus.empty:
        m = df[df["is_midea"]].copy()
        m["sku_chave"] = _sku_chave(m)
        for seller, g in alvo.groupby("seller_exib"):
            faixas_seller = set(g["faixa_btu"].dropna())
            tem = set(m.loc[m["seller_exib"] == seller, "sku_chave"])
            for _, s in skus.iterrows():
                if s["sku_chave"] in tem or s["btu"] not in faixas_seller or not s["n_sellers"]:
                    continue
                linhas.append(_opp(seller, f"{s['sku_chave']} ({s['btu']})",
                                   f"SKU no top 10 Midea ({fmt_brl(s['receita_brl'])} no mercado, "
                                   f"{int(s['n_sellers'])} seller(s)); o seller vende {s['btu']} mas não o anuncia",
                                   f"Listar o SKU {s['sku_chave']}",
                                   # o novo seller divide a venda com quem já tem: receita / (n + 1)
                                   s["receita_brl"] / (s["n_sellers"] + 1), "sku ausente"))

    # 3. exposição/logística no mesmo seller
    for seller, g in alvo.groupby("seller_exib"):
        conc = g[~g["is_midea"]]
        if len(conc) < 3:
            continue
        for _, a in g[g["is_midea"]].iterrows():
            faltas = []
            if not a["premium"] and conc["premium"].mean() >= 0.5:
                faltas.append(f"Clássico (concorrentes {conc['premium'].mean():.0%} Premium)")
            if not a["full_"] and conc["full_"].astype(bool).mean() >= 0.5:
                faltas.append(f"sem Full (concorrentes {conc['full_'].astype(bool).mean():.0%} Full)")
            if not a["catalogo"] and conc["catalogo"].astype(bool).mean() >= 0.5:
                faltas.append(f"fora do catálogo (concorrentes {conc['catalogo'].astype(bool).mean():.0%})")
            if faltas:
                linhas.append(_opp(seller, str(a["titulo"])[:60], "; ".join(faltas),
                                   "Upgrade de exposição/logística", (a["receita_brl"] or 0) * UPLIFT_EXPOSICAO,
                                   "exposição"))

    # 4. preço Midea × LG no mesmo BTU (mercado todo, por faixa)
    for faixa, g in por_faixa:
        pm = g.loc[g["is_midea"], "preco"].median()
        pl = g.loc[g["marca"] == LG, "preco"].median()
        if pd.isna(pm) or pd.isna(pl) or not pl:
            continue
        gap = pm / pl - 1
        if gap > LIMIAR_GAP_PRECO:
            rec_lg = g.loc[g["marca"] == LG, "receita_brl"].sum()
            linhas.append(_opp("Mercado (todos)", faixa,
                               f"Preço mediano Midea {fmt_brl(pm)} vs LG {fmt_brl(pl)} ({gap:+.0%})",
                               "Ação de preço / cupom co-op com os sellers",
                               rec_lg * (gap - LIMIAR_GAP_PRECO), "preço"))

    # 5. buscas em alta num BTU fraco
    if buscas is not None and not buscas.empty:
        for _, t in buscas.iterrows():
            subindo = t.get("tendencia") == "GoingUp" or (
                pd.notna(t.get("posicao_anterior")) and t["posicao_anterior"] > t["posicao"])
            btu = parse_btu(str(t["nome"]).replace("btus", " btus"))
            fx = faixa_btu(btu)
            if not subindo or not fx or fx not in share_faixa.index:
                continue
            sf = share_faixa[fx]
            if sf < midea_total:
                rec = df.loc[df["faixa_btu"] == fx, "receita_brl"].sum()
                linhas.append(_opp("Mercado (todos)", f"busca: {t['nome']}",
                                   f"termo #{int(t['posicao'])} subindo; Midea tem {sf:.0%} em {fx} "
                                   f"contra {midea_total:.0%} no total",
                                   "Ads / SEO de título nos anúncios Midea desta faixa",
                                   (midea_total - sf) * rec, "busca"))

    out = pd.DataFrame(linhas)
    if out.empty:
        return pd.DataFrame(columns=["prioridade", "seller", "sku_ou_faixa", "evidencia", "acao", "impacto_brl", "regra"])
    out = out.sort_values("impacto_brl", ascending=False).reset_index(drop=True)
    out.insert(0, "prioridade", range(1, len(out) + 1))
    return out


# ── cruzamento com as coletas do Tracker ────────────────────────────────────

def _norm_titulo(t: object) -> str:
    t = normalizar_marca(str(t or "")).lower()
    return " ".join(sorted(set(re.findall(r"[a-z0-9]+", t))))


def preparar_coletas(coletas: pd.DataFrame) -> pd.DataFrame:
    """Coletas do ML (Tracker) com marca normalizada, BTU e voltagem."""
    if coletas.empty:
        return coletas
    c = coletas.copy()
    c["marca_n"] = c["marca"].fillna("").map(normalizar_marca)
    c["btu"] = c["produto"].map(parse_btu)
    c["faixa_btu"] = c["btu"].map(faixa_btu)
    c["voltagem"] = c["produto"].map(parse_voltagem)
    c["preco"] = pd.to_numeric(c["preco"], errors="coerce")
    c["posicao"] = pd.to_numeric(c.get("posicao_organica"), errors="coerce")
    return c


def cruzar_por_grupo(anuncios: pd.DataFrame, coletas: pd.DataFrame) -> pd.DataFrame:
    """Posição média e preço coletados × vendas estimadas, por marca × faixa BTU.

    Grão de grupo porque o explorador não traz o MLB (``item_id`` é hash):
    casar anúncio a anúncio só por título é frágil. Ver ``casar_anuncios``.
    """
    if anuncios.empty or coletas.empty or "faixa_btu" not in anuncios:
        return pd.DataFrame()
    v = anuncios.dropna(subset=["faixa_btu"]).groupby(["marca", "faixa_btu"], as_index=False).agg(
        receita_brl=("receita_brl", "sum"), unidades=("unidades", "sum"), preco_nubi=("preco", "median"))
    c = coletas.dropna(subset=["faixa_btu"]).groupby(["marca_n", "faixa_btu"], as_index=False).agg(
        posicao_media=("posicao", "mean"), preco_coletado=("preco", "median"), ofertas=("produto", "count"))
    out = v.merge(c, left_on=["marca", "faixa_btu"], right_on=["marca_n", "faixa_btu"], how="inner")
    out["marca_exib"] = out["marca"].map(marca_exibicao)
    return out.drop(columns=["marca_n"])


def casar_anuncios(anuncios: pd.DataFrame, coletas: pd.DataFrame, limiar: float = 0.90) -> pd.DataFrame:
    """Casamento anúncio a anúncio: marca + BTU + voltagem iguais e título parecido.

    Score = similaridade (0–1) dos tokens do título; só entra ≥ ``limiar``.
    Guarda o score para auditoria (``match_source='fuzzy_titulo'``).
    """
    if anuncios.empty or coletas.empty:
        return pd.DataFrame()
    col = coletas.dropna(subset=["btu"]).copy()
    col["_t"] = col["produto"].map(_norm_titulo)
    linhas = []
    for _, a in anuncios.dropna(subset=["btu"]).iterrows():
        cand = col[(col["marca_n"] == a["marca"]) & (col["btu"] == a["btu"])]
        if a.get("voltagem"):
            cand = cand[cand["voltagem"].isna() | (cand["voltagem"] == a["voltagem"])]
        if cand.empty:
            continue
        ta = _norm_titulo(a["titulo"])
        scores = cand["_t"].map(lambda t: difflib.SequenceMatcher(None, ta, t).ratio())
        i = scores.idxmax()
        if scores[i] >= limiar:
            c = cand.loc[i]
            linhas.append({
                "titulo_nubi": a["titulo"], "titulo_coleta": c["produto"], "marca": a["marca_exib"],
                "faixa_btu": a["faixa_btu"], "seller": a["seller_exib"], "unidades": a["unidades"],
                "preco_nubi": a["preco"], "preco_coletado": c["preco"], "posicao": c["posicao"],
                "score": round(float(scores[i]), 3), "match_source": "fuzzy_titulo",
            })
    return pd.DataFrame(linhas)


def gap_preco_midea_lg(anuncios: pd.DataFrame) -> pd.DataFrame:
    """Preço mediano e unidades Midea × LG por faixa BTU (mesmo segmento)."""
    if anuncios.empty or not {"marca", "faixa_btu", "preco", "unidades"} <= set(anuncios.columns):
        return pd.DataFrame()
    d = anuncios[anuncios["marca"].isin([MIDEA, LG])].dropna(subset=["faixa_btu"])
    if d.empty:
        return pd.DataFrame()
    g = d.pivot_table(index="faixa_btu", columns="marca", values=["preco", "unidades"],
                      aggfunc={"preco": "median", "unidades": "sum"})
    g.columns = [f"{a}_{b}" for a, b in g.columns]
    if {"preco_MIDEA", "preco_LG"} <= set(g.columns):
        g["gap_preco_pct"] = (g["preco_MIDEA"] / g["preco_LG"] - 1) * 100
    if {"unidades_MIDEA", "unidades_LG"} <= set(g.columns):
        g["gap_unid_pct"] = (g["unidades_MIDEA"] / g["unidades_LG"].replace(0, np.nan) - 1) * 100
    return g.reset_index()


# ── forecast e resumo em inglês ─────────────────────────────────────────────

def meta_share(previsao_receita: float, share_atual_pct: float, meta_pct: float) -> Dict[str, float]:
    """Quanto falta para a meta de share no mês projetado."""
    receita_meta = previsao_receita * meta_pct / 100
    receita_atual = previsao_receita * share_atual_pct / 100
    return {"receita_meta": receita_meta, "receita_no_ritmo": receita_atual,
            "falta": max(receita_meta - receita_atual, 0.0)}


def resumo_en(kpis: Dict[str, Optional[float]], mes: pd.Timestamp, opps: pd.DataFrame) -> pd.DataFrame:
    """KPI table + top 5 opportunities in English (report to HQ)."""
    linhas = [
        ("Period", mes if isinstance(mes, str) else pd.Timestamp(mes).strftime("%b %Y")),
        ("AC market revenue (BRL)", f"{(kpis.get('mercado_receita') or 0) / 1e6:,.1f} M"),
        ("AC market units", f"{(kpis.get('mercado_unidades') or 0):,.0f}"),
        ("Midea revenue share", f"{kpis.get('share') or 0:.1f}%"),
        ("Midea share change vs prior month", f"{kpis.get('delta_share_pp') or 0:+.1f} pp"),
        ("Midea rank (brands)", f"#{kpis.get('posicao') or '-'}"),
        ("Gap vs LG", f"{kpis.get('gap_rival_pp') or 0:+.1f} pp"),
    ]
    regra_en = {"penetração": "Low Midea penetration", "sku ausente": "Missing top SKU",
                "exposição": "Listing exposure/logistics", "preço": "Price gap vs LG",
                "busca": "Rising search, weak share"}
    for _, o in opps.head(5).iterrows():
        linhas.append((f"Opportunity #{int(o['prioridade'])}",
                       f"{regra_en.get(o['regra'], o['regra'])} — {o['seller']} · {o['sku_ou_faixa']} "
                       f"(est. BRL {o['impacto_brl'] / 1e3:,.0f}k)"))
    return pd.DataFrame(linhas, columns=["Metric", "Value"])
