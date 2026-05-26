"""Market Analytics page — price distribution heatmap + per-marketplace presence."""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from lib.charts import _apply_chart_style
from lib.formatting import _csv_download_btn
from lib.specs import _enrich_specs
from lib.supabase import (
    BTU_OPTIONS,
    _filter_latest_run,
    get_filter_options,
    query_coletas,
)


def page_market_analytics() -> None:
    st.title("📊 Market Analytics")
    st.caption("Distribuição de preços e presença por marketplace ao longo do tempo.")

    with st.sidebar:
        st.subheader("Filtros")
        date_range = st.date_input(
            "Período",
            value=(date.today() - timedelta(days=30), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="ma_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=30)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()
        sel_brands    = st.multiselect("Marcas", opts["brands"], key="ma_brands")
        sel_platforms = st.multiselect("Plataformas", opts["platforms"], key="ma_platforms")
        sel_btu = st.multiselect(
            "Capacidade (BTU)", BTU_OPTIONS,
            format_func=lambda x: f"{int(x):,} BTUs".replace(",", "."),
            key="ma_btu",
        )
        sel_ciclo = st.selectbox(
            "Ciclo", ["Todos", "Só Frio", "Quente/Frio"], key="ma_ciclo",
        )
        modo = st.radio(
            "Modo de visualização",
            ["Snapshot oficial (último run)", "Todos os runs (auditoria)"],
            index=0, key="ma_modo",
        )
        load_btn = st.button("🔄 Carregar", type="primary", use_container_width=True)

    if not load_btn:
        st.info("Defina os filtros na barra lateral e clique em **Carregar**.")
        return

    with st.spinner("Carregando dados..."):
        df = query_coletas(
            start_date, end_date,
            platforms=sel_platforms or None,
            brands=sel_brands or None,
            btu_filter=sel_btu or None,
            limit=50000,
        )

    if modo.startswith("Snapshot"):
        df = _filter_latest_run(df)

    if df.empty:
        st.warning("Nenhum dado encontrado para os filtros selecionados.")
        return

    df = _enrich_specs(df)
    if sel_ciclo != "Todos":
        df = df[df["ciclo"] == sel_ciclo]
        if df.empty:
            st.warning(f"Nenhum produto com ciclo '{sel_ciclo}' no período.")
            return

    tab_dist, tab_presenca = st.tabs(
        ["💰 Distribuição de Preços", "🏪 Presença por Marketplace"]
    )

    # ── 5.2 Distribuição de preços por faixa ─────────────────────────────────
    with tab_dist:
        df_price = df.dropna(subset=["preco", "data"])
        df_price = df_price[df_price["preco"] > 0]
        if df_price.empty:
            st.warning("Sem dados de preço no período.")
        else:
            bins   = [0, 1500, 2000, 2500, 3000, 3500, 4000, 5000, 1e12]
            labels = ["< 1.5k", "1.5–2k", "2–2.5k", "2.5–3k",
                      "3–3.5k", "3.5–4k", "4–5k", "> 5k"]
            df_price = df_price.copy()
            df_price["faixa"] = pd.cut(df_price["preco"], bins=bins, labels=labels)
            pivot = (
                df_price.groupby(["data", "faixa"], observed=False)
                .size().unstack(fill_value=0)
            )
            pivot = pivot.reindex(columns=labels, fill_value=0).sort_index()
            totals = pivot.sum(axis=1).replace(0, pd.NA)
            pivot_pct = pivot.div(totals, axis=0).fillna(0) * 100
            dia_labels = [pd.to_datetime(d).strftime("%d/%m") for d in pivot.index]

            heat = pivot_pct.T
            heat.columns = dia_labels
            fig = px.imshow(
                heat,
                labels=dict(x="Data", y="Faixa de preço (R$)", color="% ofertas"),
                color_continuous_scale="Blues",
                aspect="auto",
                text_auto=".0f",
                title="Distribuição de ofertas por faixa de preço (% por dia)",
            )
            _apply_chart_style(fig, height=420, hovermode="closest")
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("**Contagem absoluta de ofertas por faixa**")
            disp = pivot.reset_index()
            disp["data"] = [pd.to_datetime(d).strftime("%d/%m/%Y") for d in disp["data"]]
            st.dataframe(disp, use_container_width=True, hide_index=True)
            _csv_download_btn(
                disp, f"rac_distribuicao_precos_{start_date}_{end_date}.csv",
                "⬇️ Exportar distribuição", key="ma_dist_csv",
            )

    # ── 5.3 Presença por marketplace ─────────────────────────────────────────
    with tab_presenca:
        if "plataforma" not in df.columns:
            st.warning("Coluna 'plataforma' indisponível.")
        else:
            presence = (
                df.groupby(["data", "plataforma"], observed=False)
                .size().reset_index(name="ofertas")
            )
            total_dia = presence.groupby("data")["ofertas"].transform("sum")
            presence["pct"] = (presence["ofertas"] / total_dia * 100).round(1)
            presence["data"] = pd.to_datetime(presence["data"])

            fig = px.bar(
                presence, x="data", y="pct", color="plataforma",
                title="Presença por marketplace (% de ofertas por dia)",
                labels={"data": "Data", "pct": "% de ofertas",
                        "plataforma": "Plataforma"},
            )
            fig.update_layout(barmode="stack")
            _apply_chart_style(fig, height=440)
            st.plotly_chart(fig, use_container_width=True)

            share = df.groupby("plataforma").size().reset_index(name="ofertas")
            share["% do período"] = (
                share["ofertas"] / share["ofertas"].sum() * 100
            ).round(1)
            share = share.sort_values("ofertas", ascending=False)
            st.markdown("**Share de ofertas no período**")
            st.dataframe(share, use_container_width=True, hide_index=True)
            _csv_download_btn(
                share, f"rac_presenca_marketplace_{start_date}_{end_date}.csv",
                "⬇️ Exportar presença", key="ma_pres_csv",
            )
