"""Overview page — executive landing with KPIs, mini-charts and deep-link buttons."""

import pandas as pd
import plotly.express as px
import streamlit as st

from lib.charts import (
    _CHART_COLORS,
    _apply_chart_style,
    _brand_color_map,
    _emphasize_midea_traces,
)
from lib.filters import _gf_brands, _gf_cmp_dates, _gf_compare, _gf_dates, _gf_platforms
from lib.formatting import _csv_download_btn, _fmt_brl
from lib.overview_data import _overview_data


def page_overview() -> None:
    st.title("🏠 Overview")
    st.caption("Visão executiva consolidada do monitoramento de preços e posicionamento.")

    start_date, end_date = _gf_dates()
    sel_platforms = _gf_platforms()
    sel_brands    = _gf_brands()

    # Context chips
    plat_label  = ", ".join(sel_platforms[:3]) + ("…" if len(sel_platforms) > 3 else "") if sel_platforms else "Todas"
    brand_label = ", ".join(sel_brands[:3])    + ("…" if len(sel_brands) > 3 else "")    if sel_brands    else "Todas"
    st.markdown(
        f"📅 **{start_date.strftime('%d/%m/%Y')} → {end_date.strftime('%d/%m/%Y')}** &nbsp;·&nbsp; "
        f"🛒 {plat_label} &nbsp;·&nbsp; 🏷️ {brand_label}",
        unsafe_allow_html=True,
    )
    st.divider()

    with st.spinner("Carregando dados…"):
        df = _overview_data(
            str(start_date), str(end_date),
            tuple(sorted(sel_platforms)), tuple(sorted(sel_brands)),
        )

    if df.empty:
        st.info(
            "Nenhum dado encontrado. Configure os **Filtros Globais** na barra lateral "
            "e aguarde o carregamento."
        )
        return

    # Comparison window
    compare_on = _gf_compare()
    df_cmp = pd.DataFrame()
    if compare_on:
        cmp_start, cmp_end = _gf_cmp_dates()
        with st.spinner("Carregando período de comparação…"):
            df_cmp = _overview_data(
                str(cmp_start), str(cmp_end),
                tuple(sorted(sel_platforms)), tuple(sorted(sel_brands)),
            )

    # ── KPI Strip ────────────────────────────────────────────────────────────
    last_date  = df["data"].max() if "data"      in df.columns else None
    n_records  = len(df)
    n_platforms = df["plataforma"].nunique() if "plataforma" in df.columns else 0
    n_brands   = df["marca"].nunique()        if "marca"      in df.columns else 0
    n_skus     = df["produto"].nunique()      if "produto"    in df.columns else 0

    midea_mask = df["marca"].str.contains("Midea", case=False, na=False) if "marca" in df.columns else pd.Series(False, index=df.index)
    avg_midea  = df.loc[midea_mask, "preco"].mean() if "preco" in df.columns else None

    delta_records = None
    delta_price   = None
    if compare_on and not df_cmp.empty:
        delta_records = f"{n_records - len(df_cmp):+,}"
        midea_cmp_mask = df_cmp["marca"].str.contains("Midea", case=False, na=False) if "marca" in df_cmp.columns else pd.Series(False, index=df_cmp.index)
        avg_cmp = df_cmp.loc[midea_cmp_mask, "preco"].mean() if "preco" in df_cmp.columns else None
        if avg_midea and avg_cmp and avg_cmp > 0:
            delta_price = f"{(avg_midea - avg_cmp) / avg_cmp * 100:+.1f}%"

    n_days = (end_date - start_date).days + 1
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Última Coleta",    last_date.strftime("%d/%m/%Y") if last_date else "—")
    c2.metric(f"Registros ({n_days}d)", f"{n_records:,}", delta=delta_records)
    c3.metric("Plataformas",      str(n_platforms))
    c4.metric("Marcas",           str(n_brands), help=f"{n_skus:,} SKUs únicos")
    c5.metric("Preço Médio Midea", _fmt_brl(avg_midea) if avg_midea else "—",
              delta=delta_price, delta_color="inverse")

    # ── Comparison strip ─────────────────────────────────────────────────────
    if compare_on and not df_cmp.empty:
        cmp_start, cmp_end = _gf_cmp_dates()
        st.info(
            f"📊 Comparando **{start_date.strftime('%d/%m')}–{end_date.strftime('%d/%m')}** "
            f"vs **{cmp_start.strftime('%d/%m')}–{cmp_end.strftime('%d/%m')}** "
            f"— {n_records:,} vs {len(df_cmp):,} registros"
        )

    st.divider()

    # ── Mini Charts 2 × 2 ────────────────────────────────────────────────────
    col_l, col_r = st.columns(2)

    # Chart 1 — Price trend by brand
    with col_l:
        st.subheader("Tendência de Preço por Marca")
        df_price = df.dropna(subset=["preco", "data", "marca"]) if all(c in df.columns for c in ["preco", "data", "marca"]) else pd.DataFrame()
        if not df_price.empty:
            try:
                top_brands = df_price["marca"].value_counts().head(6).index.tolist()
                trend = (
                    df_price[df_price["marca"].isin(top_brands)]
                    .groupby(["data", "marca"], as_index=False)["preco"]
                    .median()
                    .rename(columns={"preco": "Preço Mediano (R$)", "marca": "Marca"})
                )
                trend["data"] = pd.to_datetime(trend["data"])
                if trend.empty or trend["Preço Mediano (R$)"].isna().all():
                    raise ValueError("sem dados válidos após agrupamento")
                fig1 = px.line(
                    trend, x="data", y="Preço Mediano (R$)", color="Marca",
                    color_discrete_map=_brand_color_map(trend["Marca"]),
                    markers=True,
                    title="Preço Mediano por Marca",
                    labels={"data": "Data"},
                )
                fig1.update_traces(line=dict(width=2), marker=dict(size=5))
                _emphasize_midea_traces(fig1)
                _apply_chart_style(fig1, height=320)
                st.plotly_chart(fig1, use_container_width=True, config={"displayModeBar": False})
            except Exception:
                st.info("Sem dados suficientes para exibir o gráfico de tendência.")
        else:
            st.info("Sem dados de preço no período.")
        if st.button("→ Evolução de Preços", key="ov_goto_price", use_container_width=True):
            st.session_state["_nav_page"] = "📈 Price Evolution"
            st.rerun()

    # Chart 2 — Volume by platform
    with col_r:
        st.subheader("Volume por Plataforma")
        if "plataforma" in df.columns:
            try:
                vol = (
                    df.groupby("plataforma", as_index=False).size()
                    .rename(columns={"size": "Registros", "plataforma": "Plataforma"})
                    .sort_values("Registros", ascending=False).head(10)
                )
                fig2 = px.bar(
                    vol, x="Plataforma", y="Registros",
                    color="Plataforma", color_discrete_sequence=_CHART_COLORS,
                    title="Registros por Plataforma",
                )
                _apply_chart_style(fig2, height=320)
                st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})
            except Exception:
                st.info("Sem dados suficientes para exibir o gráfico de volume.")
        else:
            st.info("Coluna 'plataforma' não disponível.")
        if st.button("→ Resultados", key="ov_goto_results", use_container_width=True):
            st.session_state["_nav_page"] = "📊 Results"
            st.rerun()

    col_l2, col_r2 = st.columns(2)

    # Chart 3 — Brand share (donut)
    with col_l2:
        st.subheader("Share de Marcas")
        if "marca" in df.columns:
            try:
                bshare = df.groupby("marca", as_index=False).size().rename(columns={"size": "Registros", "marca": "Marca"})
                threshold = bshare["Registros"].sum() * 0.02
                main  = bshare[bshare["Registros"] >= threshold].copy()
                outros = bshare[bshare["Registros"] < threshold]["Registros"].sum()
                if outros > 0:
                    main = pd.concat([main, pd.DataFrame([{"Marca": "Outras", "Registros": outros}])], ignore_index=True)
                fig3 = px.pie(
                    main, names="Marca", values="Registros",
                    color="Marca", color_discrete_map=_brand_color_map(main["Marca"]),
                    hole=0.45,
                    title="Distribuição por Marca",
                )
                fig3.update_traces(textposition="inside", textinfo="percent+label")
                _apply_chart_style(fig3, height=320, hovermode="closest")
                st.plotly_chart(fig3, use_container_width=True, config={"displayModeBar": False})
            except Exception:
                st.info("Sem dados suficientes para exibir o gráfico de share.")
        else:
            st.info("Coluna 'marca' não disponível.")
        if st.button("→ BuyBox Position", key="ov_goto_buybox", use_container_width=True):
            st.session_state["_nav_page"] = "🏆 BuyBox Position"
            st.rerun()

    # Chart 4 — Top movers (latest 2 days)
    with col_r2:
        st.subheader("Top Movers (últimas 48h)")
        req_cols = {"preco", "data", "produto"}
        if req_cols.issubset(df.columns):
            sorted_dates = sorted(df["data"].unique(), reverse=True)
            if len(sorted_dates) >= 2:
                d_new, d_old = sorted_dates[0], sorted_dates[1]
                new_med = df[df["data"] == d_new].dropna(subset=["preco"]).groupby("produto")["preco"].median()
                old_med = df[df["data"] == d_old].dropna(subset=["preco"]).groupby("produto")["preco"].median()
                mv = pd.concat([new_med.rename("novo"), old_med.rename("antigo")], axis=1).dropna()
                mv["delta_pct"] = (mv["novo"] - mv["antigo"]) / mv["antigo"] * 100
                mv = mv[mv["delta_pct"].abs() >= 1].sort_values("delta_pct").head(10).reset_index()
                mv["SKU"] = mv["produto"].str[:40]
                if not mv.empty:
                    try:
                        fig4 = px.bar(
                            mv, x="delta_pct", y="SKU", orientation="h",
                            color="delta_pct",
                            color_continuous_scale=["#ef4444", "#fbbf24", "#059669"],
                            color_continuous_midpoint=0,
                            title="Variação de Preço (48h)",
                            labels={"delta_pct": "Variação %"},
                        )
                        fig4.update_coloraxes(showscale=False)
                        _apply_chart_style(fig4, height=320)
                        st.plotly_chart(fig4, use_container_width=True, config={"displayModeBar": False})
                    except Exception:
                        st.info("Sem dados suficientes para exibir o gráfico de movers.")
                else:
                    st.info("Sem variações significativas nas últimas 48h.")
            else:
                st.info("Necessário pelo menos 2 datas para comparar.")
        else:
            st.info("Dados de preço/produto não disponíveis.")
        if st.button("→ Top Movers completo", key="ov_goto_movers", use_container_width=True):
            st.session_state["_nav_page"] = "🚨 Top Movers"
            st.rerun()

    st.divider()
    _csv_download_btn(df, f"rac_overview_{start_date}_{end_date}.csv", key="ov_export")
