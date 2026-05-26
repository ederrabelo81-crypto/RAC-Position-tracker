"""Availability page — share-of-shelf across all positions for brands/platforms."""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from lib.charts import (
    _CHART_COLORS,
    _apply_chart_style,
    _brand_color_map,
    _emphasize_midea_traces,
    _style_midea_df,
)
from lib.supabase import (
    BTU_OPTIONS,
    PRODUCT_TYPE_OPTIONS,
    get_filter_options,
    get_sku_options,
    query_coletas,
)


def page_availability():
    st.title("📦 Availability")
    st.caption(
        "Presença de marcas e sellers em TODAS as posições coletadas. "
        "Mostra quão amplamente cada marca aparece nas buscas — independente da posição."
    )

    # --- Sidebar filters ---
    with st.sidebar:
        st.subheader("Filters")

        date_range = st.date_input(
            "Date range",
            value=(date.today() - timedelta(days=30), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="av_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=30)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()

        sel_tipo      = st.multiselect("Tipo Plataforma", opts["platform_types"], key="av_tipo")
        sel_platforms = st.multiselect("Platforms", opts["platforms"],      key="av_platforms")
        sel_sellers   = st.multiselect("Sellers",   opts["sellers"],        key="av_sellers")
        sel_brands    = st.multiselect("Brands",    opts["brands"],         key="av_brands")
        sel_keywords  = st.multiselect("Keywords",  opts["keywords"],       key="av_keywords")
        sel_btu       = st.multiselect(
            "Capacity (BTU)",
            BTU_OPTIONS,
            format_func=lambda x: f"{int(x):,} BTUs".replace(",", "."),
            key="av_btu",
        )
        sel_ptype     = st.multiselect(
            "Tipo Produto",
            list(PRODUCT_TYPE_OPTIONS.keys()),
            help="Filtra por tipo de produto detectado no nome (Inverter, On/Off, Janela…)",
            key="av_ptype",
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
            key="av_skus",
        )

        load_btn = st.button("🔄 Load Availability", type="primary", use_container_width=True)

    if not load_btn:
        st.info("Set your filters in the sidebar and click **Load Availability**.")
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

    if df.empty or "posicao_geral" not in df.columns:
        st.warning("No data found for the selected filters.")
        return

    df_all = df[df["posicao_geral"].notna()].copy()

    if df_all.empty:
        st.warning("No records with position data in this range.")
        return

    # --- Summary metrics with enhanced cards ---
    st.markdown("""
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.5rem; margin-bottom: 2rem;">
    """, unsafe_allow_html=True)

    cols = st.columns(4)

    # Metric 1: Total Records
    with cols[0]:
        st.metric(
            label="📊 Total Records",
            value=f"{len(df_all):,}",
            delta=None
        )

    # Metric 2: Platforms
    with cols[1]:
        platform_count = df_all["plataforma"].nunique() if "plataforma" in df_all else 0
        st.metric(
            label="🌐 Platforms",
            value=str(platform_count),
            delta=None
        )

    # Metric 3: Brands Present
    with cols[2]:
        brand_count = df_all["marca"].nunique() if "marca" in df_all else 0
        st.metric(
            label="🏷️ Brands Present",
            value=str(brand_count),
            delta=None
        )

    # Metric 4: Unique Products
    with cols[3]:
        product_count = df_all["produto"].nunique() if "produto" in df_all else 0
        st.metric(
            label="📦 Unique Products",
            value=f"{product_count:,}",
            delta=None
        )

    st.markdown("</div>", unsafe_allow_html=True)
    st.divider()

    tab_share, tab_timeline, tab_detail = st.tabs(
        ["📊 Share", "📅 Timeline", "📋 Detail"]
    )

    # ── Tab 1: Appearance share by Brand / Platform ─────────────────────────
    with tab_share:
        st.subheader("Appearance share by brand")

        if "marca" not in df_all.columns:
            st.info("Brand (marca) column not available.")
        else:
            brand_counts = (
                df_all
                .groupby("marca", as_index=False)
                .size()
                .rename(columns={"size": "Appearances"})
                .sort_values("Appearances", ascending=False)
            )
            total = brand_counts["Appearances"].sum()
            brand_counts["Share (%)"] = (
                brand_counts["Appearances"] / total * 100
            ).round(1)

            col_chart, col_table = st.columns([2, 1])
            with col_chart:
                _av_top15 = brand_counts.head(15)
                _av_order = _av_top15.sort_values("Appearances", ascending=False)["marca"].tolist()
                _av_cmap  = _brand_color_map(_av_top15["marca"])
                fig_bar = px.bar(
                    _av_top15,
                    x="Appearances",
                    y="marca",
                    orientation="h",
                    color="marca",
                    color_discrete_map=_av_cmap,
                    category_orders={"marca": _av_order},
                    text="Share (%)",
                    labels={"marca": "Brand"},
                    title="Top brands by total appearances (all positions)",
                )
                fig_bar.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
                _apply_chart_style(fig_bar, height=420, hovermode="closest")
                fig_bar.update_layout(showlegend=False)
                st.plotly_chart(fig_bar, use_container_width=True)

            with col_table:
                st.dataframe(_style_midea_df(brand_counts), use_container_width=True, hide_index=True)

        if "plataforma" in df_all.columns:
            st.subheader("Appearance share by platform")
            plat_counts = (
                df_all
                .groupby("plataforma", as_index=False)
                .size()
                .rename(columns={"size": "Appearances"})
                .sort_values("Appearances", ascending=False)
            )
            fig_pie = px.pie(
                plat_counts,
                names="plataforma",
                values="Appearances",
                title="Records by platform (all positions)",
                color_discrete_sequence=_CHART_COLORS,
            )
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            _apply_chart_style(fig_pie, height=380, hovermode="closest")
            st.plotly_chart(fig_pie, use_container_width=True)

    # ── Tab 2: Timeline ──────────────────────────────────────────────────────
    with tab_timeline:
        st.subheader("Appearances over time")

        group_opts = ["Brand", "Platform"]
        group_choice = st.radio(
            "Group by", group_opts, horizontal=True, key="av_grp"
        )
        group_col = "marca" if group_choice == "Brand" else "plataforma"

        if group_col not in df_all.columns or "data" not in df_all.columns:
            st.info("Required columns not available.")
        else:
            timeline = (
                df_all
                .groupby(["data", group_col], as_index=False)
                .size()
                .rename(columns={"size": "Appearances", group_col: group_choice})
            )
            timeline["data"] = pd.to_datetime(timeline["data"])

            _cmap = _brand_color_map(timeline[group_choice]) if group_choice == "Brand" else None
            fig_line = px.line(
                timeline,
                x="data",
                y="Appearances",
                color=group_choice,
                color_discrete_map=_cmap,
                markers=True,
                title=f"Daily appearances by {group_choice}",
                labels={"data": "Date"},
            )
            fig_line.update_traces(line=dict(width=2.5), marker=dict(size=6))
            _emphasize_midea_traces(fig_line)
            _apply_chart_style(fig_line, height=450)
            st.plotly_chart(fig_line, use_container_width=True)

    # ── Tab 3: Detail ────────────────────────────────────────────────────────
    with tab_detail:
        st.subheader("All records")

        display_cols = [
            c for c in [
                "data", "turno", "plataforma", "marca", "produto",
                "posicao_geral", "posicao_organica", "posicao_patrocinada",
                "preco", "seller", "keyword", "tag",
            ] if c in df_all.columns
        ]

        st.dataframe(
            _style_midea_df(df_all[display_cols].sort_values(
                ["data", "plataforma", "posicao_geral"],
                ascending=[False, True, True],
            )),
            use_container_width=True,
            height=500,
            column_config={
                "data":                 st.column_config.DateColumn("Date"),
                "preco":                st.column_config.NumberColumn("Price (R$)", format="R$ %.2f"),
                "posicao_geral":        st.column_config.NumberColumn("Position"),
                "posicao_organica":     st.column_config.NumberColumn("Organic"),
                "posicao_patrocinada":  st.column_config.NumberColumn("Sponsored"),
            },
        )

        csv_bytes = df_all[display_cols].to_csv(
            index=False, sep=";", encoding="utf-8-sig"
        ).encode("utf-8-sig")
        st.download_button(
            label="⬇️ Download CSV",
            data=csv_bytes,
            file_name=f"rac_availability_{start_date}_{end_date}.csv",
            mime="text/csv",
        )
