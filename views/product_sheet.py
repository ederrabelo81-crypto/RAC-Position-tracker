"""Product Sheet page — per-SKU ficha técnica + 2-4 SKU comparator."""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from lib.charts import _apply_chart_style
from lib.formatting import _fmt_brl
from lib.specs import _enrich_specs, _extract_voltagem, _query_products_history
from lib.supabase import BTU_OPTIONS, get_filter_options, get_sku_options


def _render_product_sheet(produto: str, start_date: date, end_date: date) -> None:
    """Renderiza a ficha detalhada de um único SKU."""
    df = _query_products_history((produto,), str(start_date), str(end_date))
    if df.empty:
        st.warning("Sem coletas para este SKU no período selecionado.")
        return

    df = _enrich_specs(df)
    df_price = df.dropna(subset=["preco"])
    df_price = df_price[df_price["preco"] > 0]

    st.subheader(produto)

    # --- Especificações técnicas ---
    btu_series = df["btu"].dropna()
    btu_val    = int(btu_series.mode().iloc[0]) if not btu_series.empty else None
    ciclo_val  = df["ciclo"].mode().iloc[0] if not df["ciclo"].mode().empty else "—"
    marca_mode = df["marca"].dropna().mode() if "marca" in df.columns else pd.Series([])
    marca_val  = marca_mode.iloc[0] if not marca_mode.empty else "—"
    volt_mode  = df["produto"].map(_extract_voltagem).dropna().mode()
    volt_val   = volt_mode.iloc[0] if not volt_mode.empty else "—"

    spec_cols = st.columns(4)
    spec_cols[0].metric("Marca", marca_val)
    spec_cols[1].metric(
        "Capacidade",
        f"{btu_val:,} BTU".replace(",", ".") if btu_val else "—",
    )
    spec_cols[2].metric("Ciclo", ciclo_val)
    spec_cols[3].metric("Voltagem", volt_val)

    # --- Contadores ---
    cnt_cols = st.columns(4)
    cnt_cols[0].metric("Total de coletas", f"{len(df):,}")
    cnt_cols[1].metric("Marketplaces", int(df["plataforma"].nunique()))
    cnt_cols[2].metric(
        "Sellers",
        int(df["seller"].nunique()) if "seller" in df.columns else 0,
    )
    cnt_cols[3].metric(
        "Menor preço",
        _fmt_brl(df_price["preco"].min()) if not df_price.empty else "—",
    )

    if df_price.empty:
        st.info("Sem dados de preço para este SKU no período.")
        return

    st.divider()

    # --- Evolução de preço por marketplace ---
    agg = df_price.groupby(["data", "plataforma"], as_index=False)["preco"].median()
    agg["data"] = pd.to_datetime(agg["data"])
    fig = px.line(
        agg, x="data", y="preco", color="plataforma", markers=True,
        title="Evolução de preço por marketplace",
        labels={"data": "Data", "preco": "Preço (R$)", "plataforma": "Plataforma"},
    )
    fig.update_traces(line=dict(width=2.5), marker=dict(size=6))
    _apply_chart_style(fig, height=420)
    st.plotly_chart(fig, use_container_width=True)

    # --- Sellers por marketplace ---
    if "seller" in df_price.columns:
        st.markdown("**Sellers por marketplace**")
        sellers = (
            df_price.sort_values("data")
            .groupby(["plataforma", "seller"], as_index=False)
            .agg(
                ultimo_preco=("preco", "last"),
                menor_preco=("preco", "min"),
                coletas=("preco", "count"),
            )
            .sort_values("ultimo_preco")
        )
        st.dataframe(
            sellers, use_container_width=True, hide_index=True,
            column_config={
                "plataforma":   st.column_config.TextColumn("Marketplace"),
                "seller":       st.column_config.TextColumn("Seller"),
                "ultimo_preco": st.column_config.NumberColumn("Último preço", format="R$ %.2f"),
                "menor_preco":  st.column_config.NumberColumn("Menor preço", format="R$ %.2f"),
                "coletas":      st.column_config.NumberColumn("Coletas"),
            },
        )


def _render_comparator(produtos: tuple, start_date: date, end_date: date) -> None:
    """Renderiza a comparação lado a lado de 2–4 SKUs."""
    df = _query_products_history(produtos, str(start_date), str(end_date))
    if df.empty:
        st.warning("Sem coletas para os produtos selecionados.")
        return

    df_price = df.dropna(subset=["preco"])
    df_price = df_price[df_price["preco"] > 0]
    if df_price.empty:
        st.warning("Sem dados de preço para os produtos selecionados.")
        return

    # --- Evolução sobreposta ---
    agg = df_price.groupby(["data", "produto"], as_index=False)["preco"].median()
    agg["data"] = pd.to_datetime(agg["data"])
    fig = px.line(
        agg, x="data", y="preco", color="produto", markers=True,
        title="Evolução de preço comparada (mediana diária)",
        labels={"data": "Data", "preco": "Preço (R$)", "produto": "Produto"},
    )
    fig.update_traces(line=dict(width=2.5), marker=dict(size=6))
    _apply_chart_style(fig, height=460)
    st.plotly_chart(fig, use_container_width=True)

    # --- Menor preço por marketplace ---
    pivot = df_price.groupby(["produto", "plataforma"])["preco"].min().unstack()
    st.markdown("**Menor preço por marketplace**")
    st.dataframe(
        pivot.style.format("R$ {:.2f}", na_rep="—"),
        use_container_width=True,
    )

    # --- Resumo comparativo + diferença percentual ---
    summary = (
        df_price.groupby("produto")["preco"]
        .agg(menor="min", mediana="median", maior="max")
        .reset_index()
    )
    cheapest = summary["menor"].min()
    summary["dif_%_vs_menor"] = (
        (summary["menor"] - cheapest) / cheapest * 100
    ).round(1)
    summary = summary.sort_values("menor")
    st.markdown("**Resumo comparativo**")
    st.dataframe(
        summary, use_container_width=True, hide_index=True,
        column_config={
            "produto":        st.column_config.TextColumn("Produto"),
            "menor":          st.column_config.NumberColumn("Menor", format="R$ %.2f"),
            "mediana":        st.column_config.NumberColumn("Mediana", format="R$ %.2f"),
            "maior":          st.column_config.NumberColumn("Maior", format="R$ %.2f"),
            "dif_%_vs_menor": st.column_config.NumberColumn("Δ% vs mais barato", format="%.1f%%"),
        },
    )


def page_product_sheet() -> None:
    st.title("🗂️ Ficha do Produto")
    st.caption("Detalhamento técnico e de preços por SKU, com comparador.")

    with st.sidebar:
        st.subheader("Filtros")
        date_range = st.date_input(
            "Período",
            value=(date.today() - timedelta(days=30), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="ps_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=30)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()
        sel_brands = st.multiselect(
            "Marcas (filtra a lista de SKUs)", opts["brands"], key="ps_brands",
        )
        sel_btu = st.multiselect(
            "Capacidade (BTU)", BTU_OPTIONS,
            format_func=lambda x: f"{int(x):,} BTUs".replace(",", "."),
            key="ps_btu",
        )

    sku_opts = get_sku_options(
        tuple(sorted(sel_brands)), tuple(sorted(sel_btu)), (),
    )

    tab_ficha, tab_cmp = st.tabs(["📋 Ficha individual", "⚖️ Comparador"])

    with tab_ficha:
        if not sku_opts:
            st.info("Nenhum SKU disponível. Ajuste os filtros na barra lateral.")
        else:
            sku = st.selectbox(
                f"Produto / SKU  ({len(sku_opts)} disponíveis)",
                sku_opts, key="ps_sku",
            )
            if sku:
                _render_product_sheet(sku, start_date, end_date)

    with tab_cmp:
        if not sku_opts:
            st.info("Nenhum SKU disponível. Ajuste os filtros na barra lateral.")
        else:
            sel = st.multiselect(
                "Selecione 2 a 4 produtos para comparar",
                sku_opts, max_selections=4, key="ps_cmp",
            )
            if len(sel) < 2:
                st.info("Selecione ao menos 2 produtos para comparar.")
            else:
                _render_comparator(tuple(sel), start_date, end_date)
