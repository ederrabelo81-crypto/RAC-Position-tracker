"""Price Evolution page — median price chart over time, grouped by brand/platform/product."""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from lib.charts import (
    _apply_chart_style,
    _brand_color_map,
    _emphasize_midea_traces,
    _style_midea_df,
)
from lib.supabase import (
    BTU_OPTIONS,
    PRODUCT_TYPE_OPTIONS,
    _filter_latest_run,
    get_filter_options,
    get_sku_options,
    query_coletas,
)


def page_price_evolution():
    st.title("📈 Price Evolution")
    st.caption("Track price changes over time by product or brand.")

    with st.sidebar:
        st.subheader("Filters")

        date_range = st.date_input(
            "Date range",
            value=(date.today() - timedelta(days=30), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="evo_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=30)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()

        sel_tipo      = st.multiselect("Tipo Plataforma", opts["platform_types"], key="evo_tipo")
        sel_brands    = st.multiselect("Brands",    opts["brands"],         key="evo_brands")
        sel_platforms = st.multiselect("Platforms", opts["platforms"],      key="evo_platforms")
        sel_sellers   = st.multiselect("Sellers",   opts["sellers"],        key="evo_sellers")
        sel_keywords  = st.multiselect("Keywords",  opts["keywords"],       key="evo_keywords")
        sel_btu       = st.multiselect(
            "Capacity (BTU)",
            BTU_OPTIONS,
            format_func=lambda x: f"{int(x):,} BTUs".replace(",", "."),
            key="evo_btu",
        )
        sel_ptype     = st.multiselect(
            "Tipo Produto",
            list(PRODUCT_TYPE_OPTIONS.keys()),
            help="Filtra por tipo de produto detectado no nome (Inverter, On/Off, Janela…)",
            key="evo_ptype",
        )

        # SKU drill-down — list narrows when brand, BTU or tipo filters change
        _sku_opts = get_sku_options(
            tuple(sorted(sel_brands)),
            tuple(sorted(sel_btu)),
            tuple(sorted(sel_ptype)),
        )
        _sku_label = (
            f"Product / SKU  ({len(_sku_opts)} available)"
            if _sku_opts else "Product / SKU"
        )
        sel_skus = st.multiselect(
            _sku_label,
            _sku_opts,
            placeholder="All SKUs" if not sel_brands else "Select SKU(s)…",
            help="Type to search. Select a Brand first to narrow options.",
            key="evo_skus",
        )

        group_by = st.radio(
            "Group chart by",
            ["Product", "Brand", "Platform"],
            horizontal=True,
        )

        st.divider()
        modo_evo = st.radio(
            "Modo de visualização",
            ["Snapshot oficial (último run)", "Todos os runs (auditoria)"],
            index=0,
            key="evo_modo",
            help=(
                "**Snapshot oficial**: usa apenas o último run de cada "
                "(data, turno, plataforma) para evitar duplicatas nos gráficos.\n\n"
                "**Auditoria**: inclui todos os runs — útil para inspecionar "
                "variações intra-turno."
            ),
        )
        load_btn = st.button("🔄 Load Chart", type="primary", use_container_width=True)

    if not load_btn:
        st.info("Set your filters in the sidebar and click **Load Chart**.")
        return

    with st.spinner("Loading data..."):
        df = query_coletas(
            start_date,
            end_date,
            platforms=sel_platforms or None,
            platform_types=sel_tipo or None,
            brands=sel_brands or None,
            sellers=sel_sellers or None,
            keywords=sel_keywords or None,
            products=sel_skus or None,
            btu_filter=sel_btu or None,
            product_types=sel_ptype or None,
            limit=50000,
        )

    if modo_evo == "Snapshot oficial (último run)":
        df = _filter_latest_run(df)

    if df.empty or "preco" not in df.columns:
        st.warning("No price data found for the selected filters.")
        return

    # --- Summary metrics with enhanced cards ---
    st.markdown("""
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.5rem; margin-bottom: 2rem;">
    """, unsafe_allow_html=True)

    days = (end_date - start_date).days + 1
    unique_skus = df['produto'].nunique() if 'produto' in df.columns else 0
    unique_brands = df['marca'].nunique() if 'marca' in df.columns else 0

    cols = st.columns(4)

    with cols[0]:
        st.metric(
            label="📊 Total Records",
            value=f"{len(df):,}",
            delta=None
        )

    with cols[1]:
        st.metric(
            label="📦 Unique SKUs",
            value=f"{unique_skus:,}",
            delta=None
        )

    with cols[2]:
        st.metric(
            label="🏷️ Brands",
            value=str(unique_brands),
            delta=None
        )

    with cols[3]:
        st.metric(
            label="📅 Time Range",
            value=f"{days} days",
            delta=None
        )

    st.markdown("</div>", unsafe_allow_html=True)
    st.divider()

    df_price = df.dropna(subset=["preco", "data"])
    if df_price.empty:
        st.warning("No records with price data in this range.")
        return

    # Aggregate: median price per (date, group)
    group_col_map = {
        "Brand":    "marca",
        "Platform": "plataforma",
        "Product":  "produto",
    }
    group_col = group_col_map[group_by]

    if group_col not in df_price.columns:
        st.warning(f"Column '{group_col}' not available in data.")
        return

    agg = (
        df_price
        .groupby(["data", group_col], as_index=False)["preco"]
        .median()
        .rename(columns={"preco": "Median Price (R$)", group_col: group_by})
    )
    agg["data"] = pd.to_datetime(agg["data"])

    tab_chart, tab_summary, tab_detail = st.tabs(
        ["📈 Price Chart", "📊 Summary", "📋 Detail"]
    )

    # ── Tab 1: Price Chart ───────────────────────────────────────────────────
    with tab_chart:
        _cmap = _brand_color_map(agg[group_by]) if group_by == "Brand" else None
        fig = px.line(
            agg,
            x="data",
            y="Median Price (R$)",
            color=group_by,
            color_discrete_map=_cmap,
            markers=True,
            title=f"Median Price Evolution by {group_by}",
            labels={"data": "Date"},
        )
        fig.update_traces(line=dict(width=2.5), marker=dict(size=6))
        _emphasize_midea_traces(fig)
        _apply_chart_style(fig, height=460)
        st.plotly_chart(fig, use_container_width=True)

    # ── Tab 2: Price Summary ─────────────────────────────────────────────────
    with tab_summary:
        st.subheader("Price summary")
        summary = (
            df_price
            .groupby(group_col)["preco"]
            .agg(
                Count="count",
                Min="min",
                Median="median",
                Max="max",
                Avg="mean",
            )
            .round(2)
            .reset_index()
            .rename(columns={group_col: group_by})
            .sort_values("Median", ascending=True)
        )
        _summary_styled = (
            _style_midea_df(summary, brand_col=group_by)
            if group_by == "Brand" else summary
        )
        st.dataframe(_summary_styled, use_container_width=True, hide_index=True)

    # ── Tab 3: Detail ────────────────────────────────────────────────────────
    with tab_detail:
        st.subheader("All records")
        display_cols = [
            c for c in [
                "data", "turno", "plataforma", "marca", "produto",
                "posicao_geral", "posicao_organica", "preco",
                "seller", "keyword", "tag",
            ] if c in df.columns
        ]
        st.dataframe(
            _style_midea_df(df[display_cols].sort_values(
                ["data", "plataforma"], ascending=[False, True]
            )),
            use_container_width=True,
            height=500,
            column_config={
                "data":             st.column_config.DateColumn("Date"),
                "preco":            st.column_config.NumberColumn("Price (R$)", format="R$ %.2f"),
                "posicao_geral":    st.column_config.NumberColumn("Position"),
                "posicao_organica": st.column_config.NumberColumn("Organic Pos."),
            },
        )
        csv_bytes = df[display_cols].to_csv(
            index=False, sep=";", encoding="utf-8-sig"
        ).encode("utf-8-sig")
        st.download_button(
            label="⬇️ Download CSV",
            data=csv_bytes,
            file_name=f"rac_price_evolution_{start_date}_{end_date}.csv",
            mime="text/csv",
        )
