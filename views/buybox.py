"""BuyBox Position page — who wins position #1 across product/platform/brand."""

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


def page_buybox_position():
    st.title("🏆 BuyBox Position")
    st.caption(
        "Quem está em posição #1 para cada produto/plataforma? "
        "Analise quais marcas e sellers dominam o topo das buscas."
    )

    # --- Sidebar filters ---
    with st.sidebar:
        st.subheader("Filters")

        date_range = st.date_input(
            "Date range",
            value=(date.today() - timedelta(days=30), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="bb_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=30)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()

        sel_tipo      = st.multiselect("Tipo Plataforma", opts["platform_types"], key="bb_tipo")
        sel_platforms = st.multiselect("Platforms", opts["platforms"],      key="bb_platforms")
        sel_sellers   = st.multiselect("Sellers",   opts["sellers"],        key="bb_sellers")
        sel_brands    = st.multiselect("Brands",    opts["brands"],         key="bb_brands")
        sel_keywords  = st.multiselect("Keywords",  opts["keywords"],       key="bb_keywords")
        sel_btu       = st.multiselect(
            "Capacity (BTU)",
            BTU_OPTIONS,
            format_func=lambda x: f"{int(x):,} BTUs".replace(",", "."),
            key="bb_btu",
        )
        sel_ptype     = st.multiselect(
            "Tipo Produto",
            list(PRODUCT_TYPE_OPTIONS.keys()),
            help="Filtra por tipo de produto detectado no nome (Inverter, On/Off, Janela…)",
            key="bb_ptype",
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
            key="bb_skus",
        )

        top_n = st.slider(
            "Top-N positions to consider as BuyBox",
            min_value=1, max_value=5, value=1,
            help="Position 1 = strict BuyBox winner. Increase to include near-top.",
            key="bb_topn",
        )

        load_btn = st.button("🔄 Load BuyBox", type="primary", use_container_width=True)

    if not load_btn:
        st.info("Set your filters in the sidebar and click **Load BuyBox**.")
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
            # Server-side position cap: prevents a single date from consuming
            # the entire limit when all platforms are selected.
            max_position=top_n,
            limit=50000,
        )

    if df.empty or "posicao_geral" not in df.columns:
        st.warning("No data found for the selected filters.")
        return

    # Server already filtered; this is a safety net for cached/stale data
    df_top = df[df["posicao_geral"].notna() & (df["posicao_geral"] <= top_n)].copy()

    if df_top.empty:
        st.warning(f"No records with position ≤ {top_n} in this range.")
        return

    # --- Summary metrics with enhanced cards ---
    st.markdown("""
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.5rem; margin-bottom: 2rem;">
    """, unsafe_allow_html=True)

    cols = st.columns(4)

    with cols[0]:
        st.metric(
            label="🏅 BuyBox Records",
            value=f"{len(df_top):,}",
            delta=None
        )

    with cols[1]:
        platform_count = df_top["plataforma"].nunique() if "plataforma" in df_top else 0
        st.metric(
            label="🌐 Platforms",
            value=str(platform_count),
            delta=None
        )

    with cols[2]:
        brand_count = df_top["marca"].nunique() if "marca" in df_top else 0
        st.metric(
            label="🏷️ Brands in Top",
            value=str(brand_count),
            delta=None
        )

    with cols[3]:
        product_count = df_top["produto"].nunique() if "produto" in df_top else 0
        st.metric(
            label="📦 Unique Products",
            value=f"{product_count:,}",
            delta=None
        )

    st.markdown("</div>", unsafe_allow_html=True)
    st.divider()

    tab_wins, tab_timeline, tab_detail = st.tabs(
        ["🏅 Win Rate", "📅 Timeline", "📋 Detail"]
    )

    # ── Tab 1: BuyBox Win Rate by Brand ────────────────────────────────────
    with tab_wins:
        st.subheader("BuyBox share by brand")

        if "marca" not in df_top.columns:
            st.info("Brand (marca) column not available.")
        else:
            # Win count per brand
            win_counts = (
                df_top
                .groupby("marca", as_index=False)
                .size()
                .rename(columns={"size": "BuyBox wins"})
                .sort_values("BuyBox wins", ascending=False)
            )
            total_wins = win_counts["BuyBox wins"].sum()
            win_counts["Win rate (%)"] = (
                win_counts["BuyBox wins"] / total_wins * 100
            ).round(1)

            col_chart, col_table = st.columns([2, 1])
            with col_chart:
                _bb_top15 = win_counts.head(15)
                _bb_order = _bb_top15.sort_values("BuyBox wins", ascending=False)["marca"].tolist()
                _bb_cmap  = _brand_color_map(_bb_top15["marca"])
                fig_bar = px.bar(
                    _bb_top15,
                    x="BuyBox wins",
                    y="marca",
                    orientation="h",
                    color="marca",
                    color_discrete_map=_bb_cmap,
                    category_orders={"marca": _bb_order},
                    text="Win rate (%)",
                    labels={"marca": "Brand"},
                    title=f"Top brands in position ≤ {top_n}",
                )
                fig_bar.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
                _apply_chart_style(fig_bar, height=420, hovermode="closest")
                fig_bar.update_layout(showlegend=False)
                st.plotly_chart(fig_bar, use_container_width=True)

            with col_table:
                st.dataframe(_style_midea_df(win_counts), use_container_width=True, hide_index=True)

        # BuyBox win rate by platform
        if "plataforma" in df_top.columns:
            st.subheader("BuyBox share by platform")
            plat_counts = (
                df_top
                .groupby("plataforma", as_index=False)
                .size()
                .rename(columns={"size": "BuyBox wins"})
                .sort_values("BuyBox wins", ascending=False)
            )
            fig_pie = px.pie(
                plat_counts,
                names="plataforma",
                values="BuyBox wins",
                title="Records in top position by platform",
                color_discrete_sequence=_CHART_COLORS,
            )
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            _apply_chart_style(fig_pie, height=380, hovermode="closest")
            st.plotly_chart(fig_pie, use_container_width=True)

    # ── Tab 2: Timeline ─────────────────────────────────────────────────────
    with tab_timeline:
        st.subheader("BuyBox wins over time")

        group_opts = ["Brand", "Platform"]
        group_choice = st.radio(
            "Group by", group_opts, horizontal=True, key="bb_grp"
        )
        group_col = "marca" if group_choice == "Brand" else "plataforma"

        if group_col not in df_top.columns or "data" not in df_top.columns:
            st.info("Required columns not available.")
        else:
            timeline = (
                df_top
                .groupby(["data", group_col], as_index=False)
                .size()
                .rename(columns={"size": "BuyBox wins", group_col: group_choice})
            )
            timeline["data"] = pd.to_datetime(timeline["data"])

            _cmap = _brand_color_map(timeline[group_choice]) if group_choice == "Brand" else None
            fig_line = px.line(
                timeline,
                x="data",
                y="BuyBox wins",
                color=group_choice,
                color_discrete_map=_cmap,
                markers=True,
                title=f"Daily BuyBox wins by {group_choice}",
                labels={"data": "Date"},
            )
            fig_line.update_traces(line=dict(width=2.5), marker=dict(size=6))
            _emphasize_midea_traces(fig_line)
            _apply_chart_style(fig_line, height=450)
            st.plotly_chart(fig_line, use_container_width=True)

    # ── Tab 3: Detail ────────────────────────────────────────────────────────
    with tab_detail:
        st.subheader(f"All records with position ≤ {top_n}")

        display_cols = [
            c for c in [
                "data", "turno", "plataforma", "marca", "produto",
                "posicao_geral", "posicao_organica", "posicao_patrocinada",
                "preco", "seller", "keyword", "tag",
            ] if c in df_top.columns
        ]

        st.dataframe(
            _style_midea_df(df_top[display_cols].sort_values(
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

        csv_bytes = df_top[display_cols].to_csv(
            index=False, sep=";", encoding="utf-8-sig"
        ).encode("utf-8-sig")
        st.download_button(
            label="⬇️ Download BuyBox CSV",
            data=csv_bytes,
            file_name=f"rac_buybox_{start_date}_{end_date}.csv",
            mime="text/csv",
        )
