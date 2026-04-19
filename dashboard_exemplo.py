"""
dashboard_exemplo.py — Exemplo de Dashboard com Design System Avançado

Este arquivo demonstra como aplicar o design_system.py em um dashboard real
de monitoramento de preços e disponibilidade de produtos online.

Para testar:
    streamlit run dashboard_exemplo.py
"""

import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime, timedelta
import streamlit as st

# Importar o design system
from design_system import (
    apply_custom_theme,
    render_header,
    metric_card,
    kpi_card,
    status_badge,
    price_card,
    info_box,
    style_plotly_chart,
    create_availability_gauge,
    format_currency,
    format_percentage,
    COLOR_PALETTE,
)


# =============================================================================
# GERAÇÃO DE DADOS DEMO
# =============================================================================

@st.cache_data
def generate_demo_data():
    """Gera dados fictícios para demonstração do dashboard."""
    np.random.seed(42)
    
    # Período de 30 dias
    dates = [datetime.now() - timedelta(days=i) for i in range(30)]
    dates.reverse()
    
    # Plataformas
    platforms = ["Mercado Livre", "Amazon", "Magalu", "Google Shopping", "Leroy Merlin"]
    
    # Marcas
    brands = ["Midea", "LG", "Samsung", "Springer", "Carrier", "Britânia"]
    
    # BTUs
    btus = ["9000", "12000", "18000", "24000"]
    
    data = []
    for date in dates:
        for platform in platforms:
            for brand in brands:
                for btu in btus:
                    # Simular preço com variação
                    base_price = {"9000": 1800, "12000": 2200, "18000": 3500, "24000": 4500}[btu]
                    brand_multiplier = {"Midea": 1.0, "LG": 1.15, "Samsung": 1.2, "Springer": 0.95, "Carrier": 1.1, "Britânia": 0.9}[brand]
                    platform_multiplier = {"Mercado Livre": 1.0, "Amazon": 1.05, "Magalu": 0.98, "Google Shopping": 1.02, "Leroy Merlin": 1.08}[platform]
                    
                    price = base_price * brand_multiplier * platform_multiplier
                    price *= np.random.uniform(0.95, 1.15)  # Variação diária
                    
                    # Simular disponibilidade (85-95%)
                    availability = np.random.choice(
                        ["available", "unavailable", "limited"],
                        p=[0.85, 0.08, 0.07]
                    )
                    
                    data.append({
                        "data": date.date(),
                        "plataforma": platform,
                        "marca": brand,
                        "btu": btu,
                        "preco": round(price, 2),
                        "disponibilidade": availability,
                        "seller": f"Seller {np.random.randint(1, 20)}",
                        "avaliacao": round(np.random.uniform(3.5, 5.0), 1),
                        "qtd_avaliacoes": np.random.randint(50, 2000),
                    })
    
    return pd.DataFrame(data)


# =============================================================================
# MAIN — Dashboard
# =============================================================================

def main():
    # Aplicar tema customizado
    apply_custom_theme()
    
    # Carregar dados
    df = generate_demo_data()
    
    # Header principal
    render_header(
        title="Dashboard de Preços & Disponibilidade",
        subtitle="Monitoramento em tempo real de produtos de ar condicionado nas principais plataformas de e-commerce",
        icon="❄️"
    )
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # ==========================================================================
    # KPIs PRINCIPAIS
    # ==========================================================================
    
    # Calcular métricas
    total_products = df.groupby(["plataforma", "marca", "btu"]).ngroups
    latest_date = df["data"].max()
    latest_df = df[df["data"] == latest_date]
    
    availability_rate = (latest_df["disponibilidade"] == "available").mean() * 100
    avg_price = latest_df["preco"].mean()
    unavailable_count = (latest_df["disponibilidade"] == "unavailable").sum()
    limited_stock = (latest_df["disponibilidade"] == "limited").sum()
    
    # Variação de preço (últimos 7 dias)
    week_ago = latest_date - timedelta(days=7)
    week_ago_df = df[df["data"] == week_ago]
    if not week_ago_df.empty:
        price_change = ((latest_df["preco"].mean() - week_ago_df["preco"].mean()) / week_ago_df["preco"].mean()) * 100
    else:
        price_change = 0
    
    # KPI Row 1 — Cards com ícones
    st.markdown("### 📈 Visão Geral")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        kpi_card(
            title="Produtos Monitorados",
            value=f"{total_products:,}",
            icon="📦",
            gradient="primary"
        )
    
    with col2:
        kpi_card(
            title="Taxa de Disponibilidade",
            value=format_percentage(availability_rate),
            icon="✅",
            gradient="success",
            subtitle=f"{int(unavailable_count)} indisponíveis"
        )
    
    with col3:
        kpi_card(
            title="Preço Médio",
            value=format_currency(avg_price),
            icon="💰",
            gradient="ocean",
            subtitle=f"{price_change:+.1f}% vs 7 dias atrás"
        )
    
    with col4:
        kpi_card(
            title="Estoque Limitado",
            value=f"{int(limited_stock)}",
            icon="⚠️",
            gradient="warning"
        )
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # ==========================================================================
    # MÉTRICAS AVANÇADAS
    # ==========================================================================
    
    st.markdown("### 📊 Métricas Detalhadas")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        metric_card(
            label="Disponibilidade Geral",
            value=format_percentage(availability_rate),
            delta="+2.3%" if np.random.random() > 0.5 else "-1.2%",
            delta_positive=np.random.random() > 0.5,
            gradient="success"
        )
    
    with col2:
        metric_card(
            label="Variação de Preços (7d)",
            value=f"{price_change:+.1f}%",
            delta=f"{abs(price_change):.1f}%",
            delta_positive=price_change < 0,  # queda é positiva
            gradient="ocean"
        )
    
    with col3:
        metric_card(
            label="Produtos Indisponíveis",
            value=f"{int(unavailable_count)}",
            delta="+5" if unavailable_count > 10 else "-3",
            delta_positive=unavailable_count < 10,
            gradient="danger"
        )
    
    st.markdown("<br>")
    
    # ==========================================================================
    # GRÁFICOS
    # ==========================================================================
    
    tab1, tab2, tab3, tab4 = st.tabs([
        "📈 Evolução de Preços",
        "📊 Disponibilidade por Plataforma",
        "🏷️ Comparativo por Marca",
        "🎯 Gauge de Disponibilidade"
    ])
    
    with tab1:
        st.markdown("#### Tendência de Preços ao Longo do Tempo")
        
        # Agrupar por data e marca
        price_trend = df.groupby(["data", "marca"])["preco"].mean().reset_index()
        
        fig = px.line(
            price_trend,
            x="data",
            y="preco",
            color="marca",
            markers=True,
            color_discrete_sequence=COLOR_PALETTE.values() if len(df["marca"].unique()) <= len(COLOR_PALETTE) else None,
        )
        
        fig = style_plotly_chart(
            fig,
            height=450,
            title="Evolução do Preço Médio por Marca",
            legend_position="top-center"
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    with tab2:
        st.markdown("#### Disponibilidade por Plataforma")
        
        # Disponibilidade por plataforma
        availability_by_platform = latest_df.groupby("plataforma")["disponibilidade"].apply(
            lambda x: (x == "available").mean() * 100
        ).reset_index(name="taxa_disponibilidade")
        
        fig = px.bar(
            availability_by_platform,
            x="plataforma",
            y="taxa_disponibilidade",
            color="taxa_disponibilidade",
            color_continuous_scale=["#EF4444", "#F59E0B", "#10B981"],
            text_auto=".1f",
        )
        
        fig = style_plotly_chart(
            fig,
            height=400,
            title="Taxa de Disponibilidade por Plataforma (%)",
        )
        
        fig.update_layout(coloraxis_showscale=False)
        
        st.plotly_chart(fig, use_container_width=True)
    
    with tab3:
        st.markdown("#### Comparativo de Preços por Marca e BTU")
        
        # Preço médio por marca e BTU
        price_by_brand_btu = latest_df.groupby(["marca", "btu"])["preco"].mean().reset_index()
        
        fig = px.bar(
            price_by_brand_btu,
            x="marca",
            y="preco",
            color="btu",
            barmode="group",
            text_auto=",.0f",
        )
        
        fig = style_plotly_chart(
            fig,
            height=450,
            title="Preço Médio por Marca e Capacidade (BTU)",
            legend_position="top-right"
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    with tab4:
        st.markdown("#### Taxa Geral de Disponibilidade")
        
        # Gauge chart
        gauge_fig = create_availability_gauge(availability_rate)
        st.plotly_chart(gauge_fig, use_container_width=True)
        
        # Info box com insights
        if availability_rate >= 90:
            info_box("✅ Excelente! A disponibilidade está acima de 90%, indicando bom estoque nas plataformas.", "success")
        elif availability_rate >= 70:
            info_box("⚠️ Atenção: A disponibilidade está em nível moderado. Monitore de perto.", "warning")
        else:
            info_box("🚨 Crítico: Disponibilidade abaixo de 70%. Ação necessária!", "danger")
    
    st.markdown("<br>")
    
    # ==========================================================================
    # TABELA DE PRODUTOS
    # ==========================================================================
    
    st.markdown("### 📋 Produtos Recentes")
    
    # Selecionar coluna de status badge
    def make_badge(status):
        return status_badge(status)
    
    # Filtrar dados mais recentes
    latest_products = latest_df.sample(min(10, len(latest_df))).copy()
    
    # Adicionar badges HTML
    latest_products["status_html"] = latest_products["disponibilidade"].apply(make_badge)
    
    # Mostrar tabela formatada
    display_df = latest_products[[
        "plataforma", "marca", "btu", "preco", "disponibilidade", 
        "avaliacao", "seller"
    ]].copy()
    
    # Formatar preço
    display_df["preco_formatado"] = display_df["preco"].apply(lambda x: format_currency(x))
    
    # Mostrar com formatação condicional
    st.dataframe(
        display_df.style.format({"preco": lambda x: format_currency(x)})
        .applymap(lambda x: f"background-color: rgba(16, 185, 129, 0.1)" if x == "available" else 
                          f"background-color: rgba(239, 68, 68, 0.1)" if x == "unavailable" else
                          f"background-color: rgba(245, 158, 11, 0.1)",
                  subset=["disponibilidade"]),
        use_container_width=True,
        hide_index=True,
    )
    
    st.markdown("<br>")
    
    # ==========================================================================
    # ALERTAS E INSIGHTS
    # ==========================================================================
    
    st.markdown("### 🔔 Alertas e Insights")
    
    # Detectar produtos com grande variação de preço
    latest_prices = latest_df.groupby(["marca", "btu"])["preco"].mean()
    old_prices = week_ago_df.groupby(["marca", "btu"])["preco"].mean() if not week_ago_df.empty else None
    
    if old_prices is not None:
        price_changes = ((latest_prices - old_prices) / old_prices * 100).dropna()
        
        big_increases = price_changes[price_changes > 10].sort_values(ascending=False)
        big_decreases = price_changes[price_changes < -10].sort_values()
        
        if len(big_increases) > 0:
            info_box(
                f"📈 **Aumento significativo**: {len(big_increases)} produtos tiveram aumento > 10% nos últimos 7 dias.",
                "warning"
            )
        
        if len(big_decreases) > 0:
            info_box(
                f"📉 **Queda significativa**: {len(big_decreases)} produtos tiveram queda > 10% nos últimos 7 dias.",
                "success"
            )
    
    # Alerta de indisponibilidade
    unavailable_brands = latest_df[latest_df["disponibilidade"] == "unavailable"]["marca"].value_counts()
    if len(unavailable_brands) > 0:
        top_unavailable = unavailable_brands.head(3)
        info_box(
            f"⚠️ **Marcas com mais indisponíveis**: {', '.join([f'{brand} ({count})' for brand, count in top_unavailable.items()])}",
            "warning"
        )
    
    # Footer
    st.markdown("<br><br>")
    st.markdown("---")
    st.markdown(
        f"""
        <div style="text-align: center; color: {COLOR_PALETTE['gray_500']}; font-size: 0.875rem;">
            <p>Dashboard atualizado em {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
            <p>RAC Price Monitor © 2024 — Monitoramento Inteligente de Preços</p>
        </div>
        """,
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
