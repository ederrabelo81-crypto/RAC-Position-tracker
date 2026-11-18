"""Aba 📅 Diário da página 🛒 Sell-out ML — vendas estimadas por dia (exports).

Lê ``nubi_vendas_diarias_agg``/``_anuncio`` (migração 024) e, para períodos
fora da janela do banco, o Parquet ``nubi_vendas_diarias``. Contas em
``nubimetrics/diario.py``. Os filtros moram DENTRO da aba: os da barra lateral
valem para a foto do explorador (outra fonte, outro grão).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import streamlit as st

from nubimetrics import analytics as an
from nubimetrics import diario as D
from nubimetrics.filters import FAIXAS_BTU, SEG_HW_INVERTER

_TTL = 3600
_PAGINA = 1000
_fragment = getattr(st, "fragment", lambda f: f)

_SEG_TOTAL = "Total AC"


def _ler(client: Any, tabela: str, desde: date, ate: date, teto: int = 120000) -> pd.DataFrame:
    linhas: List[Dict[str, Any]] = []
    offset = 0
    while offset < teto:
        lote = (client.table(tabela).select("*").gte("data", desde.isoformat()).lte("data", ate.isoformat())
                .order("data").order("marca").order("seller").range(offset, offset + _PAGINA - 1)
                .execute().data or [])
        linhas.extend(lote)
        if len(lote) < _PAGINA:
            break
        offset += _PAGINA
    df = pd.DataFrame(linhas)
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"]).dt.date
        for c in ("unidades", "gmv_brl"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


@st.cache_data(ttl=_TTL, show_spinner=False)
def limites(_get_client: Callable[[], Any]) -> Optional[Dict[str, date]]:
    """Primeiro e último dia com dado no banco (``None`` = migração 024 ausente)."""
    client = _get_client()
    if client is None:
        return None
    try:
        ult = client.table("nubi_vendas_diarias_agg").select("data").order("data", desc=True).limit(1).execute().data
        pri = client.table("nubi_vendas_diarias_agg").select("data").order("data").limit(1).execute().data
    except Exception:  # noqa: BLE001
        return None
    if not ult:
        return {}
    return {"min": date.fromisoformat(pri[0]["data"]), "max": date.fromisoformat(ult[0]["data"])}


@st.cache_data(ttl=_TTL, show_spinner="Carregando o sell-out diário…")
def carregar_periodo(_get_client: Callable[[], Any], _get_store: Optional[Callable[[], Any]],
                     desde: date, ate: date) -> pd.DataFrame:
    """Agregado do período: banco; o que o banco não tem (fora da janela), Parquet."""
    client = _get_client()
    df = _ler(client, "nubi_vendas_diarias_agg", desde, ate) if client is not None else pd.DataFrame()
    no_banco = set(df["data"]) if not df.empty else set()
    faltam = [d for d in D.iter_dias(desde, ate) if d not in no_banco]
    if faltam and _get_store is not None:
        try:
            from nubimetrics.import_export import DATASET_PARQUET

            fr = _get_store().read(DATASET_PARQUET, start=min(faltam), end=max(faltam))
            if not fr.empty:
                fr["data"] = pd.to_datetime(fr["data"]).dt.date
                fr = fr[fr["data"].isin(faltam)]
                df = pd.concat([df, D.agregar(fr)], ignore_index=True)
        except Exception:  # noqa: BLE001 — histórico indisponível: fica o que o banco tem
            pass
    return df


@st.cache_data(ttl=_TTL, show_spinner=False)
def carregar_anuncios_dia(_get_client: Callable[[], Any], dia: date) -> pd.DataFrame:
    client = _get_client()
    if client is None:
        return pd.DataFrame()
    try:
        return _ler(client, "nubi_vendas_diarias_anuncio", dia, dia, teto=5000)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=_TTL, show_spinner="Cruzando com as coletas do Tracker…")
def carregar_tracker(_get_client: Callable[[], Any], desde: date, ate: date) -> Dict[str, pd.DataFrame]:
    """Coletas do ML (histórico + janela quente) e Mais Vendidos do ML no período."""
    out = {"coletas": pd.DataFrame(), "bestsellers": pd.DataFrame()}
    client = _get_client()
    try:
        from utils.history import read_coletas

        col = read_coletas(desde, ate, supabase_client=client,
                           columns=["data", "plataforma", "marca", "posicao_organica", "preco",
                                    "seller", "buy_box_seller"])
        if not col.empty:
            out["coletas"] = col[col["plataforma"] == "Mercado Livre"]
    except Exception:  # noqa: BLE001 — cruzamento degrada, não quebra
        pass
    if client is not None:
        try:
            bs = (client.table("bestsellers").select("data,marca,titulo,btu,rank")
                  .eq("plataforma", "mercadolivre").eq("referencia", "mais_vendidos")
                  .gte("data", desde.isoformat()).lte("data", ate.isoformat()).limit(20000).execute().data)
            out["bestsellers"] = pd.DataFrame(bs or [])
        except Exception:  # noqa: BLE001
            pass
    return out


def _filtros(lim: Dict[str, date], marcas: List[str], sellers: List[str],
             parceiros: Dict[str, str]) -> Dict[str, Any]:
    c1, c2, c3 = st.columns([2, 1.3, 1.5])
    fim = lim["max"]
    ini_padrao = max(fim.replace(day=1), lim["min"])
    periodo = c1.date_input("Período", (ini_padrao, fim), min_value=lim["min"] - timedelta(days=400),
                            max_value=fim, key="nubi_d_periodo", format="DD/MM/YYYY")
    seg = c2.radio("Segmento", [SEG_HW_INVERTER, _SEG_TOTAL], horizontal=True, key="nubi_d_seg")
    medida = c3.radio("Medida", ["Unidades", "GMV (R$)"], horizontal=True, key="nubi_d_medida")
    c4, c5, c6 = st.columns(3)
    faixas = c4.multiselect("BTU", list(FAIXAS_BTU) + [D.FAIXA_ND], key="nubi_d_btu")
    sel_marcas = c5.multiselect("Marca", marcas, key="nubi_d_marca",
                                help="Vazio = todas. KPIs de share sempre usam o mercado inteiro.")
    sel_sellers = c6.multiselect("Seller", sellers, key="nubi_d_seller",
                                 format_func=lambda s: D.seller_exibicao(s, parceiros))
    st.caption("Tipo de seller (1P/2P/3P), Full e oferta não estão nos templates exportados — "
               "inclua as colunas no template para liberar esses filtros.")
    if isinstance(periodo, (tuple, list)) and len(periodo) == 2:
        ini, fim_sel = periodo
    else:
        ini = fim_sel = periodo[0] if isinstance(periodo, (tuple, list)) else periodo
    return {"ini": ini, "fim": fim_sel, "seg": seg, "medida": "unidades" if medida == "Unidades" else "gmv_brl",
            "faixas": faixas, "marcas": sel_marcas, "sellers": sel_sellers}


def _recorte(df: pd.DataFrame, f: Dict[str, Any], com_marca: bool = True) -> pd.DataFrame:
    if df.empty:
        return df
    x = df
    if f["seg"] != _SEG_TOTAL:
        x = x[x["segmento"] == f["seg"]]
    if f["faixas"]:
        x = x[x["faixa_btu"].isin(f["faixas"])]
    if f["sellers"]:
        x = x[x["seller"].isin(f["sellers"])]
    if com_marca and f["marcas"]:
        x = x[x["marca"].isin(f["marcas"])]
    return x


def _kpis(atual: pd.DataFrame, anterior: pd.DataFrame) -> None:
    k = D.kpis(atual, anterior if not anterior.empty else None)
    c = st.columns(6)

    def _d(v: Optional[float], pp: bool = False) -> Optional[str]:
        if v is None:
            return None
        return f"{v * 100:+.1f} p.p." if pp else f"{v:+.1%}"

    c[0].metric("Unidades", an.fmt_int(k.get("unidades")), _d(k.get("delta_unidades")))
    c[1].metric("GMV", an.fmt_brl(k.get("gmv_brl")), _d(k.get("delta_gmv_brl")))
    c[2].metric("Preço médio", an.fmt_brl(k.get("preco_medio")), _d(k.get("delta_preco_medio")))
    def _pct(v: Optional[float]) -> str:
        return an.fmt_pct(v * 100 if v is not None else None)

    c[3].metric("Share Midea (unid.)", _pct(k.get("share_midea_unid")),
                _d(k.get("delta_share_midea_unid"), pp=True))
    c[4].metric("Share Midea (GMV)", _pct(k.get("share_midea_gmv")),
                _d(k.get("delta_share_midea_gmv"), pp=True))
    pos = k.get("posicao_midea")
    dp = k.get("delta_posicao_midea")
    c[5].metric("Posição Midea (marcas)", f"#{pos}" if pos else "—",
                f"{dp:+d} pos." if dp else None)
    if anterior.empty:
        st.caption("Δ vazio: o período anterior de mesmo tamanho não está carregado (importe o export "
                   "do mês anterior para comparar).")


def _curva(df: pd.DataFrame, f: Dict[str, Any]) -> None:
    import plotly.express as px

    s = D.serie_por_marca(df, f["medida"])
    tot = s.groupby("data")[f["medida"]].sum().reset_index().assign(marca_g="Total")
    fig = px.line(pd.concat([tot, s]), x="data", y=f["medida"], color="marca_g", markers=True,
                  color_discrete_map={**_cores(s["marca_g"]), "Total": "#1e293b"},
                  labels={"data": "", f["medida"]: "", "marca_g": ""},
                  title="Vendas por dia — total e por marca")
    for d, nome in D.datas_promocionais(f["ini"], f["fim"]).items():
        fig.add_vline(x=pd.Timestamp(d).timestamp() * 1000, line_dash="dot", line_color="#b45309",
                      annotation_text=nome, annotation_position="top")
    fig.update_layout(height=420, margin=dict(t=50, b=10))
    st.plotly_chart(fig, use_container_width=True)


def _share(df: pd.DataFrame, f: Dict[str, Any]) -> None:
    import plotly.express as px

    s = D.share_diario(df, f["medida"])
    ordem = ["Midea", "LG"] + [m for m in D.MARCAS_FOCO if m not in ("Midea", "LG")] + [D.OUTRAS]
    fig = px.bar(s, x="data", y="share", color="marca_g", category_orders={"marca_g": ordem},
                 color_discrete_map=_cores(s["marca_g"]), labels={"data": "", "share": "", "marca_g": ""},
                 title="Share diário de marca (100%) — Midea × LG em destaque")
    fig.update_layout(barmode="stack", height=400, yaxis_tickformat=".0%", margin=dict(t=50, b=10))
    st.plotly_chart(fig, use_container_width=True)


def _cores(valores) -> Dict[str, str]:
    from utils.ui_tokens import focus_color_map

    return focus_color_map(valores, focus=["Midea", "LG"])


def _heatmap(df: pd.DataFrame, f: Dict[str, Any], parceiros: Dict[str, str]) -> None:
    import plotly.express as px

    m = D.matriz_seller(df, f["medida"], top=25, parceiros=parceiros)
    if m.empty:
        return
    m.columns = [pd.Timestamp(c).strftime("%d/%m") for c in m.columns]
    fig = px.imshow(m, aspect="auto", color_continuous_scale="Teal",
                    labels={"color": "Unid." if f["medida"] == "unidades" else "GMV R$", "x": "", "y": ""},
                    title="Dia × seller (top 25 do período)")
    fig.update_layout(height=640, margin=dict(t=50, b=10))
    st.plotly_chart(fig, use_container_width=True)


def _wallet(df: pd.DataFrame, f: Dict[str, Any], parceiros: Dict[str, str]) -> None:
    import plotly.express as px

    inv = {v: k for k, v in parceiros.items()}
    padrao = [inv[n] for n in D.PARCEIROS_PRIORITARIOS if n in inv and inv[n] in set(df["seller"])]
    opcoes = sorted(set(df["seller"]), key=lambda s: -df.loc[df["seller"] == s, "unidades"].sum())
    sel = st.multiselect("Parceiros", opcoes, default=padrao, key="nubi_d_wallet",
                         format_func=lambda s: D.seller_exibicao(s, parceiros))
    w = D.share_of_wallet(df, sel, f["medida"])
    if w.empty:
        st.info("Escolha ao menos um seller.")
        return
    w["seller"] = w["seller"].map(lambda s: D.seller_exibicao(s, parceiros))
    fig = px.line(w, x="data", y="share_midea", color="seller", markers=True,
                  labels={"data": "", "share_midea": "Midea na venda do parceiro", "seller": ""},
                  title="Share of wallet: quanto da venda de cada parceiro é Midea")
    fig.update_layout(height=400, yaxis_tickformat=".0%", margin=dict(t=50, b=10))
    st.plotly_chart(fig, use_container_width=True)
    resumo = w.groupby("seller")[["midea", "total"]].sum().reset_index()
    resumo["share_midea"] = resumo["midea"] / resumo["total"]
    st.dataframe(resumo.sort_values("total", ascending=False).rename(columns={
        "midea": "Midea", "total": "Total do parceiro", "share_midea": "% Midea"}).style.format(
        {"Midea": "{:,.0f}", "Total do parceiro": "{:,.0f}", "% Midea": "{:.1%}"}),
        hide_index=True, use_container_width=True)


def _top(ctx: Any, f: Dict[str, Any], dias: List[date], parceiros: Dict[str, str]) -> None:
    if not dias:
        return
    dia = st.selectbox("Dia", sorted(dias, reverse=True), key="nubi_d_topdia",
                       format_func=lambda d: d.strftime("%d/%m/%Y"))
    an_dia = carregar_anuncios_dia(ctx.get_client, dia)
    if an_dia.empty:
        ctx.empty_state("fora_cobertura", f"Ranking de anúncios de {dia:%d/%m/%Y} fora da janela do banco "
                        "(35 dias).", "o histórico completo está no Parquet do Drive.")
        return
    t = D.top_anuncios(_recorte(an_dia, f), dia, n=50, parceiros=parceiros)
    st.dataframe(t.rename(columns={"titulo": "Título", "marca": "Marca", "seller": "Seller",
                                   "segmento": "Segmento", "faixa_btu": "BTU", "unidades": "Unid.",
                                   "gmv_brl": "GMV R$", "preco_medio": "Preço médio"}).style.format(
        {"Unid.": "{:,.0f}", "GMV R$": "{:,.0f}", "Preço médio": "{:,.0f}"}),
        hide_index=True, use_container_width=True)


def _cruzamento(ctx: Any, df: pd.DataFrame, f: Dict[str, Any], parceiros: Dict[str, str]) -> None:
    import plotly.express as px

    tr = carregar_tracker(ctx.get_client, f["ini"], f["fim"])
    painel = D.painel_tracker(tr["coletas"])
    cruz = D.cruzar_seller(df, painel, parceiros)
    st.markdown("**Seller × dia: posição e preço da Midea (coletas) contra unidades Midea (Nubimetrics)**")
    if cruz.empty:
        ctx.empty_state("sem_dado", "Nenhum seller × dia em comum entre as coletas do ML e as vendas.",
                        "o cruzamento precisa do histórico de coletas no Parquet (Drive) cobrindo o período.")
    else:
        res = D.resumo_cruzamento(cruz)
        st.dataframe(res.rename(columns={
            "seller": "Seller", "dias": "Dias", "r_posicao_unidades": "r (posição × unid.)",
            "forca_posicao": "Força", "r_preco_unidades": "r (preço × unid.)",
            "elasticidade_preco": "Elasticidade-preço", "forca_preco": "Força "}).style.format(
            {"r (posição × unid.)": "{:+.2f}", "r (preço × unid.)": "{:+.2f}", "Elasticidade-preço": "{:+.2f}"},
            na_rep="—"), hide_index=True, use_container_width=True)
        fig = px.scatter(cruz, x="pos_midea", y="unidades", color="seller_canonico",
                         labels={"pos_midea": "Melhor posição orgânica da Midea no seller",
                                 "unidades": "Unid. Midea no seller (dia)", "seller_canonico": ""})
        fig.update_xaxes(autorange="reversed")
        fig.update_layout(height=380, margin=dict(t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)
    bs = D.cruzar_bestseller(df, tr["bestsellers"])
    st.markdown("**Mais Vendidos do ML: melhor posição da marca × BTU contra unidades do dia**")
    if bs.empty:
        st.caption("Sem Mais Vendidos do ML no período.")
    else:
        r = D.spearman(bs["rank"], bs["unidades"])
        st.caption(f"Spearman(rank, unidades) = {r:+.2f} em {len(bs)} pontos — {D.forca_evidencia(r, len(bs))} "
                   "(rank menor = melhor; r negativo = posição melhor acompanha mais venda)."
                   if r is not None else "Pontos insuficientes para correlação.")
        fig = px.scatter(bs, x="rank", y="unidades", color="marca_g", hover_data=["faixa_btu", "data"],
                         color_discrete_map=_cores(bs["marca_g"]),
                         labels={"rank": "Melhor posição no Mais Vendidos", "unidades": "Unidades", "marca_g": ""})
        fig.update_xaxes(autorange="reversed")
        fig.update_layout(height=380, margin=dict(t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)


def render(ctx: Any, parceiros_df: pd.DataFrame) -> None:
    """Aba 📅 Diário (chamada por ``page.render``)."""
    ctx.como_ler(
        "Vendas **estimadas** da Nubimetrics **por dia**, do export dos templates 'RAC | …' "
        "(relatório por publicações). Defasagem de ~2 dias; o dia mais recente ainda pode ser revisado. "
        "**HW Inverter** = regra título + atributos (+ override manual). Sem o export de atributos, só "
        "o título decide — e ~37% das unidades têm título cortado em 40 caracteres, o que subestima o "
        "segmento. Share e posição da Midea somam todas as grafias com Midea."
    )
    lim = limites(ctx.get_client)
    if lim is None:
        ctx.empty_state("sem_coleta", "Tabelas do sell-out diário ausentes.",
                        "aplique `docs/migrations/024_nubi_sellout_diario.sql` (e reaplique a 022).")
        return
    if not lim:
        ctx.empty_state("sem_coleta", "Nenhum export diário importado ainda.",
                        "exporte os templates 'RAC | …' e rode `python -m nubimetrics.import_export`.")
        return
    parceiros = (dict(zip(parceiros_df["nickname"], parceiros_df["display_name"]))
                 if not parceiros_df.empty and "nickname" in parceiros_df else {})
    atraso = (date.today() - lim["max"]).days
    if atraso > 3:
        st.warning(f"Último dia importado: {lim['max']:%d/%m/%Y} ({atraso} dias atrás). "
                   "Exporte os templates de novo para atualizar.")
    base_marcas = carregar_periodo(ctx.get_client, ctx.get_store, lim["max"].replace(day=1), lim["max"])
    marcas = sorted(base_marcas["marca"].unique()) if not base_marcas.empty else []
    sellers = (base_marcas.groupby("seller")["unidades"].sum().sort_values(ascending=False).index.tolist()
               if not base_marcas.empty else [])
    _corpo(ctx, lim, marcas, sellers, parceiros)


@_fragment
def _corpo(ctx: Any, lim: Dict[str, date], marcas: List[str], sellers: List[str],
           parceiros: Dict[str, str]) -> None:
    f = _filtros(lim, marcas, sellers, parceiros)
    if f["ini"] > f["fim"]:
        st.info("Escolha o fim do período.")
        return
    df = carregar_periodo(ctx.get_client, ctx.get_store, f["ini"], f["fim"])
    a_ini, a_fim = D.periodo_anterior(f["ini"], f["fim"])
    ant = carregar_periodo(ctx.get_client, ctx.get_store, a_ini, a_fim)
    # KPIs: mercado inteiro do segmento (marca não filtra o denominador).
    atual_k, ant_k = _recorte(df, f, com_marca=False), _recorte(ant, f, com_marca=False)
    if atual_k.empty:
        ctx.empty_state("sem_dado", "Sem venda no recorte escolhido.", "afrouxe os filtros ou o período.")
        return
    dias_ant = set(ant_k["data"]) if not ant_k.empty else set()
    completo = len(dias_ant) == (a_fim - a_ini).days + 1
    _kpis(atual_k, ant_k if completo else pd.DataFrame())
    rec = _recorte(df, f)
    abas = st.tabs(["📈 Curva diária", "🧮 Dia × seller", "🥧 Share de marca", "🤝 Parceiros Midea",
                    "🏅 Top anúncios", "🔗 × Tracker"])
    with abas[0]:
        _curva(rec, f)
    with abas[1]:
        _heatmap(rec, f, parceiros)
    with abas[2]:
        _share(atual_k, f)
    with abas[3]:
        _wallet(atual_k, f, parceiros)
    with abas[4]:
        _top(ctx, f, sorted(set(df["data"])), parceiros)
    with abas[5]:
        _cruzamento(ctx, atual_k, f, parceiros)
