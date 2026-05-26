"""Top Movers page — biggest price changes between two date windows."""

from datetime import date

import plotly.express as px
import streamlit as st

from lib.charts import _apply_chart_style
from lib.filters import _gf_brands, _gf_cmp_dates, _gf_dates, _gf_platforms
from lib.formatting import _csv_download_btn
from lib.overview_data import _overview_data
from lib.supabase import get_filter_options


def page_top_movers() -> None:
    st.title("🚨 Top Movers")
    st.caption("SKUs com maior variação de preço entre duas janelas temporais.")

    start_date, end_date = _gf_dates()
    cmp_start, cmp_end   = _gf_cmp_dates()
    sel_platforms = _gf_platforms()
    sel_brands    = _gf_brands()

    with st.sidebar:
        st.subheader("Configuração")
        dr = st.date_input("Janela atual", value=(start_date, end_date),
                           max_value=date.today(), format="DD/MM/YYYY", key="tm_dates")
        start_date = dr[0] if len(dr) > 0 else start_date
        end_date   = dr[1] if len(dr) > 1 else end_date

        cr = st.date_input("Janela de comparação", value=(cmp_start, cmp_end),
                           max_value=date.today(), format="DD/MM/YYYY", key="tm_cmp_dates")
        cmp_start = cr[0] if len(cr) > 0 else cmp_start
        cmp_end   = cr[1] if len(cr) > 1 else cmp_end

        opts = get_filter_options()
        sel_platforms = st.multiselect("Plataformas", opts["platforms"],
                                       default=sel_platforms, key="tm_platforms")
        sel_brands    = st.multiselect("Marcas", opts["brands"],
                                       default=sel_brands, key="tm_brands")

        with st.expander("Refinar — Movers", expanded=True):
            min_delta_pct = st.slider("Mín. |Δ preço|%", 0, 50, 5, key="tm_min_delta")
            direction = st.radio(
                "Direção",
                ["Ambos ▲▼", "Apenas altas ▲", "Apenas quedas ▼"],
                key="tm_direction",
            )
            min_obs = st.number_input("Mín. obs. por janela", 1, 20, 2, key="tm_min_obs")

        load_btn = st.button("🔄 Calcular Movers", type="primary", use_container_width=True)

    if not load_btn:
        st.info(
            "Configure as **janelas temporais** na barra lateral e clique em "
            "**Calcular Movers**. Apenas SKUs com ≥ N observações em **ambas** as "
            "janelas são incluídos para evitar falsos positivos."
        )
        return

    with st.spinner("Carregando janela atual…"):
        df_cur = _overview_data(str(start_date), str(end_date),
                                tuple(sorted(sel_platforms)), tuple(sorted(sel_brands)))
    with st.spinner("Carregando janela de comparação…"):
        df_cmp = _overview_data(str(cmp_start), str(cmp_end),
                                tuple(sorted(sel_platforms)), tuple(sorted(sel_brands)))

    if df_cur.empty or df_cmp.empty:
        st.warning("Uma das janelas não retornou dados. Ajuste as datas ou filtros.")
        return

    if not {"preco", "produto"}.issubset(df_cur.columns):
        st.warning("Colunas 'preco' ou 'produto' ausentes nos dados.")
        return

    cur_agg = (df_cur.dropna(subset=["preco", "produto"])
               .groupby("produto")["preco"]
               .agg(preco_atual="median", obs_atual="count").reset_index())
    cmp_agg = (df_cmp.dropna(subset=["preco", "produto"])
               .groupby("produto")["preco"]
               .agg(preco_anterior="median", obs_anterior="count").reset_index())

    movers = cur_agg.merge(cmp_agg, on="produto", how="inner")
    movers = movers[(movers["obs_atual"] >= min_obs) & (movers["obs_anterior"] >= min_obs)]

    if movers.empty:
        st.warning(f"Nenhum SKU com ≥ {min_obs} observações em ambas as janelas.")
        return

    movers["delta_abs"] = movers["preco_atual"] - movers["preco_anterior"]
    movers["delta_pct"] = movers["delta_abs"] / movers["preco_anterior"] * 100

    if direction == "Apenas altas ▲":
        movers = movers[movers["delta_pct"] > 0]
    elif direction == "Apenas quedas ▼":
        movers = movers[movers["delta_pct"] < 0]
    movers = movers[movers["delta_pct"].abs() >= min_delta_pct]

    if movers.empty:
        st.warning(f"Nenhum SKU com variação ≥ {min_delta_pct}% após aplicar os filtros.")
        return

    movers = movers.sort_values("delta_pct", key=abs, ascending=False).reset_index(drop=True)

    # ── KPI cards ─────────────────────────────────────────────────────────────
    n_up   = int((movers["delta_pct"] > 0).sum())
    n_down = int((movers["delta_pct"] < 0).sum())
    biggest = movers.iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("SKUs em movimento", str(len(movers)))
    c2.metric("▲ Altas",  str(n_up))
    c3.metric("▼ Quedas", str(n_down))
    c4.metric("Maior salto", f"{biggest['delta_pct']:+.1f}%",
              delta=biggest["produto"][:30])

    st.divider()

    # ── Bar chart (top 20) ─────────────────────────────────────────────────────
    top20 = movers.head(20).copy().sort_values("delta_pct")
    top20["SKU"] = top20["produto"].str[:45]
    fig = px.bar(
        top20, x="delta_pct", y="SKU", orientation="h",
        color="delta_pct",
        color_continuous_scale=["#ef4444", "#fbbf24", "#059669"],
        color_continuous_midpoint=0,
        title=(
            f"Top 20 Movers — {start_date.strftime('%d/%m')}→{end_date.strftime('%d/%m')}"
            f" vs {cmp_start.strftime('%d/%m')}→{cmp_end.strftime('%d/%m')}"
        ),
        labels={"delta_pct": "Variação %"},
    )
    fig.update_coloraxes(showscale=False)
    _apply_chart_style(fig, height=max(350, len(top20) * 28 + 100))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ── Detail table ───────────────────────────────────────────────────────────
    st.subheader("Tabela Detalhada")
    display = movers[["produto", "preco_anterior", "preco_atual", "delta_abs",
                       "delta_pct", "obs_anterior", "obs_atual"]].copy()
    display.columns = ["Produto / SKU", "Preço Anterior (R$)", "Preço Atual (R$)",
                       "Δ R$", "Δ %", "Obs. (anterior)", "Obs. (atual)"]
    st.dataframe(
        display,
        use_container_width=True,
        height=420,
        column_config={
            "Preço Anterior (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
            "Preço Atual (R$)":    st.column_config.NumberColumn(format="R$ %.2f"),
            "Δ R$":                st.column_config.NumberColumn(format="R$ %.2f"),
            "Δ %":                 st.column_config.NumberColumn(format="%.1f%%"),
        },
    )
    _csv_download_btn(
        display,
        f"rac_top_movers_{start_date}_{end_date}_vs_{cmp_start}_{cmp_end}.csv",
        "⬇️ Exportar Top Movers CSV",
        key="tm_export",
    )
