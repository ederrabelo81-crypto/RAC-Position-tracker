"""Results page — filtered Supabase browser with screenshots & CSV export."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from lib.charts import _style_midea_df
from lib.formatting import _fmt_brl
from lib.screenshots import _resolve_screenshot_path
from lib.supabase import (
    BTU_OPTIONS,
    PRODUCT_TYPE_OPTIONS,
    _filter_latest_run,
    get_filter_options,
    get_sku_options,
    query_coletas,
)


def page_results():
    st.title("📊 Results")
    st.caption("Browse collected data. Filters are applied before loading.")

    # --- Sidebar filters ---
    with st.sidebar:
        st.subheader("Filters")

        date_range = st.date_input(
            "Date range",
            value=(date.today() - timedelta(days=7), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=7)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()

        sel_tipo      = st.multiselect("Tipo Plataforma", opts["platform_types"])
        sel_platforms = st.multiselect("Platforms",       opts["platforms"])
        sel_sellers   = st.multiselect("Sellers",         opts["sellers"])
        sel_brands    = st.multiselect("Brands",          opts["brands"])
        sel_keywords  = st.multiselect("Keywords",        opts["keywords"])
        sel_btu       = st.multiselect(
            "Capacity (BTU)",
            BTU_OPTIONS,
            format_func=lambda x: f"{int(x):,} BTUs".replace(",", "."),
        )
        sel_ptype     = st.multiselect(
            "Tipo Produto",
            list(PRODUCT_TYPE_OPTIONS.keys()),
            help="Filtra por tipo de produto detectado no nome (Inverter, On/Off, Janela…)",
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
            help=(
                "Type to search within the list. "
                "Select a Brand first to narrow options."
            ),
        )

        st.divider()
        modo_results = st.radio(
            "Modo de visualização",
            ["Snapshot oficial (último run)", "Todos os runs (auditoria)"],
            index=0,
            help=(
                "**Snapshot oficial**: mostra apenas o último run de cada "
                "(data, turno, plataforma) — ideal para análise de mercado.\n\n"
                "**Auditoria**: mostra todos os runs do período — útil para "
                "comparar execuções múltiplas do scraper no mesmo turno."
            ),
        )
        load_btn = st.button("🔄 Load Data", type="primary", use_container_width=True)

    if not load_btn:
        st.info("Set your filters in the sidebar and click **Load Data**.")
        return

    with st.spinner("Loading data from Supabase..."):
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

    if df.empty:
        st.warning("No data found for the selected filters.")
        return

    if modo_results == "Snapshot oficial (último run)":
        df = _filter_latest_run(df)

    # --- Summary metrics with enhanced cards ---
    st.markdown("""
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.5rem; margin-bottom: 2rem;">
    """, unsafe_allow_html=True)

    cols = st.columns(4)

    with cols[0]:
        st.metric(
            label="📊 Total Records",
            value=f"{len(df):,}",
            delta=None
        )

    with cols[1]:
        platform_count = df["plataforma"].nunique() if "plataforma" in df else 0
        st.metric(
            label="🌐 Platforms",
            value=str(platform_count),
            delta=None
        )

    with cols[2]:
        brand_count = df["marca"].nunique() if "marca" in df else 0
        st.metric(
            label="🏷️ Brands",
            value=str(brand_count),
            delta=None
        )

    with cols[3]:
        price_count = df['preco'].notna().sum() if "preco" in df else 0
        st.metric(
            label="💰 With Price",
            value=f"{price_count:,}",
            delta=None
        )

    st.markdown("</div>", unsafe_allow_html=True)
    st.divider()

    # --- Display columns ---
    display_cols = [
        c for c in [
            "data", "turno", "plataforma", "marca", "produto",
            "posicao_geral", "posicao_organica", "preco",
            "seller", "keyword", "tag", "url_produto",
        ] if c in df.columns
    ]

    st.caption(
        "💡 Clique em **Abrir** na coluna Link para ir ao produto, ou selecione "
        "uma linha para ver os links clicáveis e o screenshot da coleta."
    )
    event = st.dataframe(
        _style_midea_df(df[display_cols]),
        use_container_width=True,
        height=520,
        on_select="rerun",
        selection_mode="single-row",
        key="results_table",
        column_config={
            "data":            st.column_config.DateColumn("Date"),
            "preco":           st.column_config.NumberColumn("Price (R$)", format="R$ %.2f"),
            "posicao_geral":   st.column_config.NumberColumn("Position"),
            "posicao_organica":st.column_config.NumberColumn("Organic Pos."),
            "produto":         st.column_config.TextColumn("Produto / SKU", width="large"),
            "url_produto":     st.column_config.LinkColumn(
                "Link", display_text="Abrir ↗", width="small",
                help="Abre a página do produto numa nova aba",
            ),
        },
    )

    # --- Detalhe da linha selecionada: links clicáveis + screenshot local ---
    sel_rows = event.selection.rows if (event and event.selection) else []
    if sel_rows:
        row = df.iloc[sel_rows[0]]
        st.divider()
        st.subheader("🔎 Detalhe do produto selecionado")

        nome  = row.get("produto") or "(sem nome)"
        url   = row.get("url_produto")
        preco = row.get("preco")
        preco_fmt = _fmt_brl(preco) if pd.notna(preco) else "—"
        has_url = isinstance(url, str) and url.startswith("http")

        c1, c2 = st.columns([2, 1])
        with c1:
            if has_url:
                st.markdown(
                    f"**Produto:** [{nome}]({url})  \n"
                    f"**Preço:** [{preco_fmt}]({url})  \n"
                    f"**Plataforma:** {row.get('plataforma', '—')}  ·  "
                    f"**Seller:** {row.get('seller', '—')}"
                )
                st.caption(url)
            else:
                st.markdown(
                    f"**Produto:** {nome}  \n"
                    f"**Preço:** {preco_fmt}  \n"
                    f"**Plataforma:** {row.get('plataforma', '—')}  ·  "
                    f"**Seller:** {row.get('seller', '—')}"
                )
                st.caption("URL do produto não disponível para este registro.")
        with c2:
            if has_url:
                st.link_button("🛒 Abrir produto", url, use_container_width=True)

        # Screenshot da página de busca (modo local-only)
        shot = row.get("screenshot_busca")
        shot_path = _resolve_screenshot_path(shot)
        if shot_path:
            st.image(
                str(shot_path),
                caption=f"📸 Screenshot da coleta — {shot_path}",
                use_column_width=True,
            )
        elif isinstance(shot, str) and shot.startswith("http"):
            st.image(shot, caption="📸 Screenshot da coleta (Supabase)", use_column_width=True)
        elif isinstance(shot, str) and shot.strip():
            # Modo local-only: o screenshot existe apenas no PC onde a coleta
            # rodou. No Streamlit Cloud o arquivo não está presente — mostramos
            # só a referência, sem alarmar (os links de URL continuam funcionando).
            st.caption(
                f"📸 Screenshot salvo localmente: `{shot}` — visível apenas no "
                "dashboard rodado no PC onde a coleta foi feita (modo local-only)."
            )
        else:
            st.caption("Sem screenshot para este registro.")

    # --- Download ---
    csv_bytes = df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label="⬇️ Download CSV",
        data=csv_bytes,
        file_name=f"rac_{start_date}_{end_date}.csv",
        mime="text/csv",
    )
