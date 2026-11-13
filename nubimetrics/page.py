"""Página 🛒 Sell-out ML — vendas estimadas Nubimetrics × coletas do Tracker.

Lê SÓ do banco (tabelas da migração 023 + `coletas` do ML), com
``st.cache_data(ttl=3600)``; nunca chama o MCP da UI. Contas em
``nubimetrics/analytics.py``.

Registrada em ``app.py`` (seção DEMANDA). ``render(ctx)`` recebe do app o
cliente do banco e os componentes visuais do projeto (``_como_ler``,
``_empty_state``), para a página seguir o mesmo sistema visual sem importar o
``app.py`` (import circular).
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from nubimetrics import analytics as an
from nubimetrics.filters import FAIXAS_BTU, SEG_HW_INVERTER, SEGMENTOS, marca_exibicao

RODAPE = ("Vendas estimadas Nubimetrics por categoria — podem diferir das vendas reais. "
          "Última atualização: {data}")
_TTL = 3600
_PAGINA = 1000


@dataclass
class Contexto:
    """O que a página precisa do ``app.py``."""

    get_client: Callable[[], Any]
    como_ler: Callable[..., None]
    empty_state: Callable[..., None]


# ── leitura (cache de 1 h) ──────────────────────────────────────────────────

def _ler(client: Any, tabela: str, colunas: str = "*", filtros: Optional[List[Tuple[str, str, Any]]] = None,
         ordem: Optional[List[str]] = None, teto: int = 20000) -> pd.DataFrame:
    """Lê uma tabela paginando (PostgREST corta em 1.000 linhas)."""
    linhas: List[Dict[str, Any]] = []
    offset = 0
    while offset < teto:
        q = client.table(tabela).select(colunas)
        for op, col, val in filtros or []:
            q = getattr(q, op)(col, val)
        for o in ordem or []:
            q = q.order(o)
        lote = q.range(offset, offset + _PAGINA - 1).execute().data or []
        linhas.extend(lote)
        if len(lote) < _PAGINA:
            break
        offset += _PAGINA
    return pd.DataFrame(linhas)


def _numerico(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    # O PostgREST serializa `numeric` como string (precisão de dinheiro).
    for c in cols:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(ttl=_TTL, show_spinner="Carregando vendas Nubimetrics…")
def carregar(_get_client: Callable[[], Any]) -> Dict[str, Any]:
    """Todas as tabelas Nubimetrics (pequenas) + coletas do ML (2 dias)."""
    client = _get_client()
    if client is None:
        return {"erro": "banco não configurado"}
    out: Dict[str, Any] = {}
    try:
        out["mercado"] = _numerico(_ler(client, "nubi_mercado_mensal", ordem=["mes", "tipo"]),
                                   ["receita_brl", "unidades", "ticket_medio", "receita_brl_aa", "unidades_aa"])
        out["ranking"] = _numerico(_ler(client, "nubi_ranking_mensal", ordem=["mes", "dimensao", "posicao"]),
                                   ["receita_brl", "unidades", "share_brl", "cresc_receita_pct",
                                    "cresc_unid_pct", "catalogo_pct"])
        # Sub-linhas Midea (Springer Midea, Midea Branca…) somadas numa linha só.
        out["ranking"] = an.canonizar_marcas(out["ranking"])
        out["anuncios"] = _ler(client, "nubi_anuncios", ordem=["item_hash"])
        out["concorrente"] = _numerico(_ler(client, "nubi_concorrente_diario", ordem=["data"]),
                                       ["visitas", "unidades", "receita_brl", "conversao_pct"])
        out["parceiros"] = _ler(client, "dim_partner_sellers")
    except Exception as exc:  # noqa: BLE001 — tabela ausente = migração não aplicada
        return {"erro": f"{exc}"}
    try:
        desde = (date.today() - timedelta(days=2)).isoformat()
        out["coletas"] = _ler(
            client, "coletas",
            "data,turno,keyword,marca,produto,posicao_organica,preco,buy_box_seller,seller",
            filtros=[("eq", "plataforma", "Mercado Livre"), ("gte", "data", desde)],
            ordem=["data", "id"], teto=15000)
    except Exception:  # noqa: BLE001 — cruzamento degrada, não quebra
        out["coletas"] = pd.DataFrame()
    return out


# ── componentes ─────────────────────────────────────────────────────────────

def _cores(valores) -> Dict[str, str]:
    from utils.ui_tokens import focus_color_map

    return focus_color_map(valores, focus=["Midea", "LG"])


def _xlsx(df: pd.DataFrame, aba: str) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=aba[:31])
    return buf.getvalue()


def _tabela_brl(df: pd.DataFrame, brl: List[str] = (), pct: List[str] = (), inteiro: List[str] = ()) -> pd.DataFrame:
    """Cópia com números formatados em pt-BR (só exibição)."""
    out = df.copy()
    for c in brl:
        if c in out:
            out[c] = out[c].map(an.fmt_brl)
    for c in pct:
        if c in out:
            out[c] = out[c].map(an.fmt_pct)
    for c in inteiro:
        if c in out:
            out[c] = out[c].map(an.fmt_int)
    return out


def _sidebar(anuncios: pd.DataFrame, ranking: pd.DataFrame, parceiros: pd.DataFrame) -> Dict[str, Any]:
    st.sidebar.subheader("Filtros — Sell-out ML")
    meses = an.meses_disponiveis(ranking)
    mes = st.sidebar.selectbox(
        "Mês fechado (rankings)", meses, format_func=lambda m: pd.Timestamp(m).strftime("%m/%Y"),
        key="nubi_mes", help="O Nubimetrics só publica ranking de mês fechado.") if meses else None
    segs = st.sidebar.multiselect("Segmento", list(SEGMENTOS), default=[SEG_HW_INVERTER], key="nubi_seg",
                                  help="Padrão: High Wall Inverter. Limpe para ver todos os tipos.")
    faixas = st.sidebar.multiselect("BTU", list(FAIXAS_BTU), key="nubi_btu")
    volts = st.sidebar.multiselect("Voltagem", ["220V", "127V", "Bivolt"], key="nubi_volt")
    opcoes_marca = sorted(set(anuncios.get("marca", pd.Series(dtype=str)).dropna()) | set(an.MARCAS_PADRAO))
    marcas = st.sidebar.multiselect("Marcas", opcoes_marca, default=[], key="nubi_marcas",
                                    format_func=marca_exibicao,
                                    help="Vazio = todas (o share precisa do mercado inteiro no denominador).")
    so_parc = st.sidebar.toggle("Só sellers parceiros Midea", value=False, key="nubi_parc")
    so_cat = st.sidebar.toggle("Só catálogo", value=False, key="nubi_cat")
    so_full = st.sidebar.toggle("Só Full", value=False, key="nubi_full")
    return {"mes": mes, "segmentos": segs, "faixas": faixas, "voltagens": volts, "marcas": marcas,
            "so_parceiros": so_parc, "so_catalogo": so_cat, "so_full": so_full}


# ── abas ────────────────────────────────────────────────────────────────────

def _aba_executiva(ctx: Contexto, d: Dict[str, Any], f: Dict[str, Any], rec: pd.DataFrame) -> Dict[str, Any]:
    import plotly.express as px

    ranking, mercado = d["ranking"], d["mercado"]
    if f["mes"] is None:
        ctx.empty_state("sem_coleta", "Ainda não há ranking mensal gravado.",
                        "rode o workflow **Nubimetrics Sell-out** com backfill=true.")
        return {}
    k = an.kpis_marca(ranking, f["mes"])
    mes_txt = pd.Timestamp(f["mes"]).strftime("%m/%Y")
    st.caption(f"Ar condicionado no Mercado Livre — mês fechado **{mes_txt}** (todos os tipos de AC). "
               "Midea = MIDEA + Springer Midea + demais grafias com Midea no nome.")
    c = st.columns(5)
    c[0].metric("Mercado AC", an.fmt_brl(k["mercado_receita"]), help="Soma das marcas do ranking no mês.")
    c[1].metric("Unidades", an.fmt_int(k["mercado_unidades"], " un"))
    c[2].metric("Ticket médio", an.fmt_brl(k["ticket_medio"]))
    c[3].metric("Share Midea (R$)", an.fmt_pct(k["share"]), delta=an.fmt_pp(k["delta_share_pp"]) if k["delta_share_pp"] is not None else None,
                help="Δ vs mês fechado anterior.")
    c[4].metric(f"Midea vs LG ({an.fmt_pct(k['rival_share'])})", an.fmt_pp(k["gap_rival_pp"]),
                help="Diferença de share em R$ entre Midea e LG.")

    if not rec.empty:
        sm = an.share_marcas(rec)
        mid = sm[sm["marca"] == an.MIDEA]
        tot = sm["receita_brl"].sum()
        c2 = st.columns(3)
        c2[0].metric("Recorte do painel (foto)", an.fmt_brl(tot),
                     help="Receita da janela do explorador no recorte da barra lateral.")
        c2[1].metric("Share Midea no recorte", an.fmt_pct(mid["share"].iloc[0]) if len(mid) else "—")
        c2[2].metric("Unid. Midea no recorte", an.fmt_int(mid["unidades"].iloc[0], " un") if len(mid) else "—")

    col1, col2 = st.columns([3, 2])
    with col1:
        bs = an.brandshare(ranking)
        if bs["mes"].nunique() > 1:
            fig = px.line(bs, x="mes", y="share_brl", color="marca", markers=True,
                          color_discrete_map=_cores(bs["marca"]),
                          labels={"share_brl": "Share R$ (%)", "mes": "", "marca": ""},
                          title="Brandshare mensal — Midea × top 4")
            fig.update_layout(height=360, margin=dict(t=50, b=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Série de brandshare com 1 mês só — rode o backfill (13 meses) uma vez.", icon="📈")
    with col2:
        rk = an.ranking_mes(ranking, "brands", f["mes"]).head(10).copy()
        rk["marca"] = rk["nome"].map(marca_exibicao)
        fig = px.bar(rk.iloc[::-1], x="share_brl", y="marca", orientation="h",
                     color="marca", color_discrete_map=_cores(rk["marca"]),
                     labels={"share_brl": "Share R$ (%)", "marca": ""}, title=f"Top 10 marcas — {mes_txt}")
        fig.update_layout(height=360, showlegend=False, margin=dict(t=50, b=10))
        st.plotly_chart(fig, use_container_width=True)

    with st.expander("Ranking de marcas (tabela)"):
        t = an.ranking_mes(ranking, "brands", f["mes"])[
            ["posicao", "nome", "receita_brl", "cresc_receita_pct", "unidades", "cresc_unid_pct",
             "share_brl", "qtd_sellers", "tendencia"]].copy()
        t["nome"] = t["nome"].map(marca_exibicao)
        st.dataframe(_tabela_brl(t, brl=["receita_brl"], pct=["cresc_receita_pct", "cresc_unid_pct", "share_brl"],
                                 inteiro=["unidades"]).rename(columns={
            "posicao": "#", "nome": "Marca", "receita_brl": "Receita", "cresc_receita_pct": "Δ receita",
            "unidades": "Unid.", "cresc_unid_pct": "Δ unid.", "share_brl": "Share R$",
            "qtd_sellers": "Sellers", "tendencia": "Tendência"}), hide_index=True, use_container_width=True)
        st.caption("Saturação e ranking de demanda por marca não existem no MCP da Nubimetrics "
                   "(saturação só por categoria — aba Forecast & mercado).")

    real = mercado[mercado["tipo"] == "real"].copy()
    if not real.empty:
        real["mes"] = pd.to_datetime(real["mes"])
        real["rotulo"] = real["parcial"].map({True: "mês corrente (parcial)", False: "fechado"})
        fig = px.bar(real, x="mes", y="receita_brl", color="rotulo",
                     color_discrete_map={"fechado": "#94a3b8", "mês corrente (parcial)": "#cbd5e1"},
                     labels={"receita_brl": "Receita (R$)", "mes": "", "rotulo": ""},
                     title="Mercado de AC no ML — receita mensal")
        fig.update_layout(height=300, margin=dict(t=50, b=10))
        st.plotly_chart(fig, use_container_width=True)
    return k


def _aba_oportunidades(d: Dict[str, Any], f: Dict[str, Any], rec: pd.DataFrame, k: Dict[str, Any]) -> pd.DataFrame:
    if rec.empty:
        st.info("Sem anúncios no recorte.")
        return pd.DataFrame()
    buscas = an.ranking_mes(d["ranking"], "search_terms", f["mes"]) if f["mes"] is not None else None
    # Segue o toggle da barra lateral ("Só sellers parceiros Midea").
    opps = an.oportunidades(rec, buscas, so_parceiros=bool(f["so_parceiros"]))
    if opps.empty:
        st.success("Nenhuma regra disparou neste recorte.", icon="✅")
        return opps
    regras = st.multiselect("Tipos de oportunidade", sorted(opps["regra"].unique()),
                            default=sorted(opps["regra"].unique()), key="nubi_opp_regras")
    vis = opps[opps["regra"].isin(regras)]
    c = st.columns(3)
    c[0].metric("Oportunidades", an.fmt_int(len(vis)))
    c[1].metric("Impacto estimado (top 10)", an.fmt_brl(vis["impacto_brl"].head(10).sum()))
    c[2].metric("Sellers envolvidos", an.fmt_int(vis["seller"].nunique()))
    st.dataframe(_tabela_brl(vis, brl=["impacto_brl"]).rename(columns={
        "prioridade": "#", "seller": "Seller", "sku_ou_faixa": "SKU / faixa", "evidencia": "Evidência",
        "acao": "Ação sugerida", "impacto_brl": "Impacto est.", "regra": "Regra"}),
        hide_index=True, use_container_width=True, height=460)
    b = st.columns(3)
    b[0].download_button("⬇️ CSV para os sellers", vis.to_csv(index=False, sep=";").encode("utf-8-sig"),
                         "oportunidades_sellout_ml.csv", "text/csv", use_container_width=True)
    b[1].download_button("⬇️ XLSX para os sellers", _xlsx(vis, "Oportunidades"), "oportunidades_sellout_ml.xlsx",
                         use_container_width=True)
    if k and f["mes"] is not None:
        en = an.resumo_en(k, f["mes"], vis)
        b[2].download_button("🌐 Export EN summary", en.to_csv(index=False).encode("utf-8"),
                             "midea_ml_sellout_summary_en.csv", "text/csv", use_container_width=True)
    return opps


def _aba_produtos(rec: pd.DataFrame) -> None:
    import plotly.express as px

    if rec.empty:
        st.info("Sem anúncios no recorte.")
        return
    top = rec.sort_values("receita_brl", ascending=False).head(30)[
        ["titulo", "marca_exib", "faixa_btu", "seller_exib", "preco", "receita_brl", "unidades",
         "catalogo", "full_", "premium"]]
    st.subheader("Anúncios mais vendidos")
    st.dataframe(_tabela_brl(top, brl=["preco", "receita_brl"], inteiro=["unidades"]).rename(columns={
        "titulo": "Anúncio", "marca_exib": "Marca", "faixa_btu": "BTU", "seller_exib": "Seller",
        "preco": "Preço", "receita_brl": "Receita", "unidades": "Unid.", "catalogo": "Catálogo",
        "full_": "Full", "premium": "Premium"}), hide_index=True, use_container_width=True, height=380)
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Top SKUs Midea e onde vendem")
        sk = an.top_skus_midea(rec)
        if sk.empty:
            st.info("Nenhum anúncio Midea no recorte.")
        else:
            st.dataframe(_tabela_brl(sk, brl=["receita_brl", "preco_mediano"], inteiro=["unidades"]).rename(columns={
                "sku_chave": "Modelo", "titulo": "Anúncio", "btu": "BTU", "receita_brl": "Receita",
                "unidades": "Unid.", "preco_mediano": "Preço med.", "n_sellers": "Nº sellers",
                "sellers": "Sellers"}), hide_index=True, use_container_width=True)
    with c2:
        st.subheader("Share por BTU × marca")
        hm = an.heatmap_btu_marca(rec)
        if hm.empty:
            st.info("Sem BTU identificado no recorte.")
        else:
            fig = px.imshow(hm, text_auto=".0f", aspect="auto", color_continuous_scale="Blues",
                            labels={"color": "Share R$ (%)", "x": "BTU", "y": ""})
            fig.update_layout(height=340, margin=dict(t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)


def _aba_sellers(d: Dict[str, Any], f: Dict[str, Any], rec: pd.DataFrame) -> None:
    import plotly.express as px

    if f["mes"] is not None:
        rk = an.ranking_mes(d["ranking"], "seller", f["mes"]).head(20).copy()
        if not rk.empty:
            nomes = dict(zip(d["parceiros"].get("nickname", []), d["parceiros"].get("display_name", [])))
            rk["seller"] = rk["nome"].map(lambda s: nomes.get(s, s))
            st.subheader(f"Ranking de sellers em AC — {pd.Timestamp(f['mes']):%m/%Y}")
            st.dataframe(_tabela_brl(rk[["posicao", "seller", "receita_brl", "cresc_receita_pct", "unidades",
                                        "share_brl", "catalogo_pct", "tendencia"]],
                                     brl=["receita_brl"], pct=["cresc_receita_pct", "share_brl", "catalogo_pct"],
                                     inteiro=["unidades"]).rename(columns={
                "posicao": "#", "seller": "Seller", "receita_brl": "Receita", "cresc_receita_pct": "Δ receita",
                "unidades": "Unid.", "share_brl": "Share R$", "catalogo_pct": "% catálogo",
                "tendencia": "Tendência"}), hide_index=True, use_container_width=True, height=300)
    if rec.empty:
        return
    pen = an.penetracao(rec)
    st.subheader("Penetração da Midea por seller (recorte)")
    vis = pen.head(25)
    st.dataframe(_tabela_brl(vis[["seller_exib", "parceiro", "receita_brl", "share_seller", "share_midea",
                                  "indice_penetracao", "preco_medio", "catalogo_pct", "full_pct", "premium_pct"]],
                             brl=["receita_brl", "preco_medio"],
                             pct=["share_seller", "share_midea", "catalogo_pct", "full_pct", "premium_pct"])
                 .rename(columns={"seller_exib": "Seller", "parceiro": "Parceiro", "receita_brl": "Receita",
                                  "share_seller": "Share do seller", "share_midea": "Share Midea no seller",
                                  "indice_penetracao": "Índice penetração", "preco_medio": "Preço médio",
                                  "catalogo_pct": "% catálogo", "full_pct": "% Full", "premium_pct": "% Premium"})
                 .style.format({"Índice penetração": "{:.2f}"}),
                 hide_index=True, use_container_width=True, height=380)
    mix = an.mix_marcas_seller(rec, pen["seller"].head(12))
    if not mix.empty:
        fig = px.bar(mix, x="pct", y="seller_exib", color="marca_grupo", orientation="h",
                     color_discrete_map={**_cores(mix["marca_grupo"]), "Outras": "#e2e8f0"},
                     labels={"pct": "% da receita do seller", "seller_exib": "", "marca_grupo": ""},
                     title="Mix de marcas por seller (top 12)")
        fig.update_layout(height=420, barmode="stack", margin=dict(t=50, b=10))
        st.plotly_chart(fig, use_container_width=True)
    conc = d["concorrente"]
    if not conc.empty:
        with st.expander("Concorrente acompanhado no Nubimetrics (visitas e conversão diárias)"):
            st.caption("A conta tem 1 slot de concorrência: é o seller do grupo 'Nicknames Favoritos' no "
                       "Nubimetrics (o MCP não informa o nickname). Trocar o seller = trocar no app do Nubimetrics.")
            conc = conc.assign(data=pd.to_datetime(conc["data"]))
            fig = px.line(conc, x="data", y=["visitas", "unidades"], labels={"value": "", "data": "", "variable": ""})
            fig.update_layout(height=280, margin=dict(t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
            st.metric("Conversão média (30d)", an.fmt_pct(conc["conversao_pct"].mean(), 2))


def _aba_busca(d: Dict[str, Any], f: Dict[str, Any], rec: pd.DataFrame) -> None:
    from nubimetrics.filters import faixa_btu, parse_btu

    if f["mes"] is None:
        return
    b = an.ranking_mes(d["ranking"], "search_terms", f["mes"]).copy()
    if b.empty:
        st.info("Sem ranking de buscas no mês.")
        return
    b["Δ posições"] = (b["posicao_anterior"] - b["posicao"]).where(b["posicao_anterior"].notna())
    b["faixa_btu"] = b["nome"].map(lambda t: faixa_btu(parse_btu(str(t))))
    share_fx = {}
    if not rec.empty:
        for fx, g in rec.dropna(subset=["faixa_btu"]).groupby("faixa_btu"):
            tot = g["receita_brl"].sum()
            share_fx[fx] = g.loc[g["is_midea"], "receita_brl"].sum() / tot * 100 if tot else None
    b["Share Midea no BTU"] = b["faixa_btu"].map(share_fx).map(an.fmt_pct)
    st.caption("Ranking de termos digitados na categoria AC (sem volume de busca — o MCP não informa). "
               "A ponte termo → BTU → oferta Midea usa o share da Midea na faixa do termo.")
    st.dataframe(b[["posicao", "nome", "Δ posições", "tendencia", "faixa_btu", "Share Midea no BTU"]].rename(columns={
        "posicao": "#", "nome": "Termo", "tendencia": "Tendência", "faixa_btu": "BTU"}),
        hide_index=True, use_container_width=True, height=520)


def _aba_cruzamento(ctx: Contexto, d: Dict[str, Any], rec: pd.DataFrame) -> None:
    import plotly.express as px

    col = an.preparar_coletas(d.get("coletas", pd.DataFrame()))
    if col.empty:
        ctx.empty_state("sem_coleta", "Sem coletas do Mercado Livre nos últimos 2 dias no banco.",
                        "o cruzamento volta sozinho quando a coleta do ML gravar de novo.")
        return
    grp = an.cruzar_por_grupo(rec, col)
    if grp.empty:
        ctx.empty_state("sem_dado", "Nenhuma marca × BTU em comum entre as coletas e o recorte.")
    else:
        fig = px.scatter(grp, x="posicao_media", y="unidades", color="marca_exib", size="receita_brl",
                         hover_data={"faixa_btu": True, "preco_coletado": ":.0f"},
                         color_discrete_map=_cores(grp["marca_exib"]),
                         labels={"posicao_media": "Posição orgânica média (coletas)", "unidades": "Unidades (Nubimetrics)",
                                 "marca_exib": ""}, title="Posição na busca × venda, por marca e BTU")
        fig.update_xaxes(autorange="reversed")
        fig.update_layout(height=420, margin=dict(t=50, b=10))
        st.plotly_chart(fig, use_container_width=True)
    gap = an.gap_preco_midea_lg(rec)
    if not gap.empty:
        st.subheader("Midea × LG no mesmo BTU")
        st.dataframe(gap.rename(columns={"faixa_btu": "BTU", "preco_MIDEA": "Preço Midea", "preco_LG": "Preço LG",
                                         "unidades_MIDEA": "Unid. Midea", "unidades_LG": "Unid. LG",
                                         "gap_preco_pct": "Gap preço %", "gap_unid_pct": "Gap unid. %"})
                     .style.format(precision=0), hide_index=True, use_container_width=True)
    with st.expander("Casamento anúncio a anúncio (auditoria)"):
        m = an.casar_anuncios(rec.head(400), col)
        st.caption(f"{len(m)} anúncio(s) casados por marca + BTU + voltagem + título (score ≥ 0,90). "
                   "O explorador não traz o código MLB, então o casamento é por texto.")
        if not m.empty:
            st.dataframe(m, hide_index=True, use_container_width=True)


def _aba_forecast(d: Dict[str, Any], k: Dict[str, Any]) -> None:
    import plotly.express as px

    m = d["mercado"].copy()
    if m.empty:
        st.info("Sem série de mercado.")
        return
    m["mes"] = pd.to_datetime(m["mes"])
    m["Série"] = m["tipo"].map({"real": "Real", "previsao": "Previsão Nubimetrics"})
    fig = px.line(m, x="mes", y="receita_brl", color="Série", markers=True,
                  color_discrete_map={"Real": "#334155", "Previsão Nubimetrics": "#0f766e"},
                  labels={"receita_brl": "Receita (R$)", "mes": ""}, title="AC no Mercado Livre — real × previsão")
    fig.update_layout(height=360, margin=dict(t=50, b=10))
    st.plotly_chart(fig, use_container_width=True)
    det = m[(m["tipo"] == "real") & m.get("saturacao", pd.Series(dtype=object)).notna()] \
        if "saturacao" in m else pd.DataFrame()
    if not det.empty:
        u = det.sort_values("mes").iloc[-1]
        c = st.columns(4)
        c[0].metric(f"Saturação da categoria ({u['mes']:%m/%Y})", u["saturacao"] or "—")
        c[1].metric("Oportunidade", u["oportunidade"] or "—")
        c[2].metric("% vendas via catálogo", an.fmt_pct(pd.to_numeric(u["pct_catalogo"], errors="coerce")))
        c[3].metric("% anúncios com desconto", an.fmt_pct(pd.to_numeric(u["pct_desconto"], errors="coerce")))
    prev = m[(m["tipo"] == "previsao") & (m["mes"] >= pd.Timestamp(date.today().replace(day=1)))]
    if prev.empty or not k or k.get("share") is None:
        return
    alvo = prev.iloc[0]
    meta = st.slider("Meta de share Midea (R$, %)", 5.0, 40.0, value=float(round((k["share"] or 0) + 2)), step=0.5,
                     key="nubi_meta")
    r = an.meta_share(float(alvo["receita_brl"]), float(k["share"]), meta)
    c = st.columns(3)
    c[0].metric(f"Mercado previsto {alvo['mes']:%m/%Y}", an.fmt_brl(alvo["receita_brl"]))
    c[1].metric(f"Midea no ritmo atual ({an.fmt_pct(k['share'])})", an.fmt_brl(r["receita_no_ritmo"]))
    c[2].metric(f"Falta para {an.fmt_pct(meta)}", an.fmt_brl(r["falta"]))
    st.caption("Previsão da Nubimetrics para a categoria AC inteira (não existe previsão só de High Wall "
               "Inverter). O ritmo atual usa o share do último mês fechado.")


# ── página ──────────────────────────────────────────────────────────────────

def render(ctx: Contexto) -> None:
    st.title("🛒 Sell-out ML")
    ctx.como_ler(
        "Vendas **estimadas** do Mercado Livre (Nubimetrics) para ar condicionado, cruzadas com as coletas "
        "do Tracker. **Rankings** são de mês fechado; **anúncios** são uma foto diária do explorador da "
        "Nubimetrics (receita/unidades na janela dela). O recorte padrão é **High Wall Inverter**, "
        "classificado pelo título — o MCP não entrega atributo (veja o % por origem abaixo). "
        "Oportunidades: regras explicáveis, impacto em R$ estimado. O período global do app (dias) "
        "não se aplica: a Nubimetrics publica mês fechado."
    )
    with st.sidebar:
        if st.button("🔄 Recarregar Sell-out ML", use_container_width=True, key="nubi_reload"):
            carregar.clear()
            st.rerun()
    d = carregar(ctx.get_client)
    if d.get("erro"):
        ctx.empty_state("sem_coleta", f"Tabelas Nubimetrics indisponíveis ({d['erro'][:160]}).",
                        "aplique `docs/migrations/023_nubimetrics.sql` e rode o workflow "
                        "**Nubimetrics Sell-out** (Actions) com backfill=true.")
        return
    anuncios = an.preparar_anuncios(d["anuncios"], d["parceiros"])
    f = _sidebar(anuncios, d["ranking"], d["parceiros"])
    rec = an.filtrar(anuncios, f["segmentos"], f["faixas"], f["voltagens"], f["marcas"], (),
                     f["so_catalogo"], f["so_full"]) if not anuncios.empty else anuncios
    if f["so_parceiros"] and not rec.empty:
        rec = rec[rec["parceiro"]]
    if not rec.empty:
        orig = an.origem_classificacao(rec)
        st.caption("Classificação do recorte (% da receita): " + " · ".join(
            f"{ {'title_regex': 'título explícito', 'model_regex': 'BTU pelo modelo', 'title_default': 'inferido', 'attribute': 'atributo'}.get(k, k)} {an.fmt_pct(v)}"
            for k, v in orig.items()))

    abas = st.tabs(["📊 Visão executiva", "🎯 Oportunidades", "🏷️ Produtos", "🏪 Sellers",
                    "🔎 Busca & demanda", "🔗 Coletas × vendas", "🔮 Forecast & mercado"])
    with abas[0]:
        k = _aba_executiva(ctx, d, f, rec)
    with abas[1]:
        _aba_oportunidades(d, f, rec, k)
    with abas[2]:
        _aba_produtos(rec)
    with abas[3]:
        _aba_sellers(d, f, rec)
    with abas[4]:
        _aba_busca(d, f, rec)
    with abas[5]:
        _aba_cruzamento(ctx, d, rec)
    with abas[6]:
        _aba_forecast(d, k)

    ultima = "—"
    for nome in ("anuncios", "ranking", "mercado"):
        df = d.get(nome)
        if df is not None and not df.empty and "atualizado_em" in df:
            ultima = pd.to_datetime(df["atualizado_em"]).max().strftime("%d/%m/%Y %H:%M")
            break
    st.divider()
    st.caption(RODAPE.format(data=ultima))
