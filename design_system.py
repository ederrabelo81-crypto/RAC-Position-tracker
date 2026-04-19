"""
design_system.py — Design System & UI Components for RAC Price Monitor Dashboard

Este módulo fornece componentes visuais e estilos para melhorar o design do dashboard,
focado em dados de disponibilidade e preços de produtos online.

Uso:
    from design_system import render_header, metric_card, kpi_row, apply_custom_theme
"""

import streamlit as st
import plotly.graph_objects as go
from typing import Optional, List, Dict, Any


# =============================================================================
# PALETA DE CORES — E-commerce & Price Monitoring
# =============================================================================

COLOR_PALETTE = {
    # Primary — Confiança, profissionalismo
    "primary": "#2563EB",
    "primary_dark": "#1E40AF",
    "primary_light": "#3B82F6",
    
    # Secondary — Ação, destaque
    "secondary": "#7C3AED",
    "secondary_dark": "#5B21B6",
    
    # Success — Disponível, preço baixo, vantagem
    "success": "#10B981",
    "success_dark": "#059669",
    "success_light": "#34D399",
    
    # Warning — Atenção, estoque limitado
    "warning": "#F59E0B",
    "warning_dark": "#D97706",
    "warning_light": "#FBBF24",
    
    # Danger — Indisponível, preço alto, alerta
    "danger": "#EF4444",
    "danger_dark": "#DC2626",
    "danger_light": "#F87171",
    
    # Info — Neutro informativo
    "info": "#06B6D4",
    "info_dark": "#0891B2",
    
    # Neutrals — Backgrounds, textos, bordas
    "gray_50": "#F9FAFB",
    "gray_100": "#F3F4F6",
    "gray_200": "#E5E7EB",
    "gray_300": "#D1D5DB",
    "gray_400": "#9CA3AF",
    "gray_500": "#6B7280",
    "gray_600": "#4B5563",
    "gray_700": "#374151",
    "gray_800": "#1F2937",
    "gray_900": "#111827",
    
    # Gradients
    "gradient_primary": "linear-gradient(135deg, #2563EB 0%, #7C3AED 100%)",
    "gradient_success": "linear-gradient(135deg, #10B981 0%, #059669 100%)",
    "gradient_warning": "linear-gradient(135deg, #F59E0B 0%, #D97706 100%)",
    "gradient_danger": "linear-gradient(135deg, #EF4444 0%, #DC2626 100%)",
    "gradient_ocean": "linear-gradient(135deg, #2563EB 0%, #06B6D4 100%)",
    "gradient_sunset": "linear-gradient(135deg, #F59E0B 0%, #EF4444 100%)",
}

# Chart colors — Sequência para múltiplas séries
CHART_COLORS = [
    COLOR_PALETTE["primary"],
    COLOR_PALETTE["success"],
    COLOR_PALETTE["warning"],
    COLOR_PALETTE["secondary"],
    COLOR_PALETTE["danger"],
    COLOR_PALETTE["info"],
    "#8B5CF6",
    "#EC4899",
]


# =============================================================================
# CSS AVANÇADO — Glassmorphism, Animações, Componentes
# =============================================================================

CUSTOM_CSS = """
<style>
/* ============================================
   IMPORT FONT — Inter ( Moderna, legível )
   ============================================ */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

/* ============================================
   ROOT VARIABLES
   ============================================ */
:root {
    --primary: """ + COLOR_PALETTE["primary"] + """;
    --primary-dark: """ + COLOR_PALETTE["primary_dark"] + """;
    --success: """ + COLOR_PALETTE["success"] + """;
    --warning: """ + COLOR_PALETTE["warning"] + """;
    --danger: """ + COLOR_PALETTE["danger"] + """;
    --gray-100: """ + COLOR_PALETTE["gray_100"] + """;
    --gray-200: """ + COLOR_PALETTE["gray_200"] + """;
    --gray-700: """ + COLOR_PALETTE["gray_700"] + """;
}

/* ============================================
   GLOBAL STYLES
   ============================================ */
* {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

.stApp {
    background: linear-gradient(180deg, #F9FAFB 0%, #FFFFFF 100%);
}

/* ============================================
   HEADER CUSTOMIZADO — Glassmorphism
   ============================================ */
.dashboard-header {
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border-bottom: 1px solid """ + COLOR_PALETTE["gray_200"] + """;
    padding: 1.5rem 2rem;
    margin: -1.5rem -1.5rem 1.5rem -1.5rem;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
    border-radius: 0 0 16px 16px;
}

.dashboard-header h1 {
    font-size: 1.875rem !important;
    font-weight: 800 !important;
    color: """ + COLOR_PALETTE["gray_900"] + """ !important;
    margin: 0 0 0.5rem 0 !important;
    letter-spacing: -0.025em;
}

.dashboard-header p {
    font-size: 0.95rem !important;
    color: """ + COLOR_PALETTE["gray_500"] + """ !important;
    margin: 0 !important;
    font-weight: 400;
}

/* ============================================
   METRIC CARDS — Advanced Design
   ============================================ */
.metric-card {
    background: white;
    border-radius: 16px;
    padding: 1.5rem;
    border: 1px solid """ + COLOR_PALETTE["gray_200"] + """;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    transition: all 0.3s ease;
    position: relative;
    overflow: hidden;
}

.metric-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 4px;
    background: var(--gradient, """ + COLOR_PALETTE["gradient_primary"] + """);
}

.metric-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 16px rgba(0, 0, 0, 0.08);
    border-color: """ + COLOR_PALETTE["primary"] + """;
}

.metric-label {
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: """ + COLOR_PALETTE["gray_500"] + """ !important;
    margin-bottom: 0.5rem;
}

.metric-value {
    font-size: 2rem !important;
    font-weight: 800 !important;
    color: """ + COLOR_PALETTE["gray_900"] + """ !important;
    line-height: 1.2;
}

.metric-delta {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.875rem !important;
    font-weight: 600 !important;
    padding: 0.25rem 0.75rem;
    border-radius: 9999px;
    margin-top: 0.5rem;
}

.metric-delta.positive {
    background: rgba(16, 185, 129, 0.1);
    color: """ + COLOR_PALETTE["success_dark"] + """;
}

.metric-delta.negative {
    background: rgba(239, 68, 68, 0.1);
    color: """ + COLOR_PALETTE["danger_dark"] + """;
}

/* ============================================
   KPI CARDS — Com ícones e gradientes
   ============================================ */
.kpi-card {
    background: white;
    border-radius: 16px;
    padding: 1.25rem;
    border: 1px solid """ + COLOR_PALETTE["gray_200"] + """;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
    display: flex;
    align-items: center;
    gap: 1rem;
    transition: all 0.3s ease;
}

.kpi-card:hover {
    transform: translateX(4px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}

.kpi-icon {
    width: 48px;
    height: 48px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.5rem;
    background: var(--icon-gradient, """ + COLOR_PALETTE["gradient_primary"] + """);
    color: white;
    flex-shrink: 0;
}

.kpi-content {
    flex: 1;
}

.kpi-title {
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: """ + COLOR_PALETTE["gray_500"] + """ !important;
    margin-bottom: 0.25rem;
}

.kpi-value {
    font-size: 1.5rem !important;
    font-weight: 700 !important;
    color: """ + COLOR_PALETTE["gray_900"] + """ !important;
}

/* ============================================
   STATUS BADGES — Disponibilidade
   ============================================ */
.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.375rem 0.875rem;
    border-radius: 9999px;
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.025em;
}

.status-badge.available {
    background: rgba(16, 185, 129, 0.1);
    color: """ + COLOR_PALETTE["success_dark"] + """;
}

.status-badge.unavailable {
    background: rgba(239, 68, 68, 0.1);
    color: """ + COLOR_PALETTE["danger_dark"] + """;
}

.status-badge.limited {
    background: rgba(245, 158, 11, 0.1);
    color: """ + COLOR_PALETTE["warning_dark"] + """;
}

.status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
    animation: pulse 2s infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

/* ============================================
   PRICE CARDS — Destaque para preços
   ============================================ */
.price-card {
    background: white;
    border-radius: 12px;
    padding: 1rem;
    border: 1px solid """ + COLOR_PALETTE["gray_200"] + """;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    transition: all 0.2s ease;
}

.price-card:hover {
    border-color: """ + COLOR_PALETTE["primary"] + """;
    box-shadow: 0 4px 8px rgba(37, 99, 235, 0.1);
}

.price-label {
    font-size: 0.75rem !important;
    color: """ + COLOR_PALETTE["gray_500"] + """ !important;
    font-weight: 500;
    margin-bottom: 0.25rem;
}

.price-value {
    font-size: 1.5rem !important;
    font-weight: 800 !important;
    color: """ + COLOR_PALETTE["primary"] + """ !important;
}

.price-old {
    font-size: 0.875rem !important;
    color: """ + COLOR_PALETTE["gray_400"] + """ !important;
    text-decoration: line-through;
    margin-left: 0.5rem;
}

/* ============================================
   TABS — Moderno com indicador
   ============================================ */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background: """ + COLOR_PALETTE["gray_100"] + """;
    border-radius: 12px;
    padding: 6px;
    margin-bottom: 1.5rem;
}

.stTabs [data-baseweb="tab"] {
    border-radius: 8px !important;
    padding: 0.5rem 1.25rem !important;
    font-size: 0.875rem !important;
    font-weight: 600 !important;
    color: """ + COLOR_PALETTE["gray_600"] + """ !important;
    background: transparent !important;
    border: none !important;
    transition: all 0.2s ease;
}

.stTabs [aria-selected="true"] {
    background: white !important;
    color: """ + COLOR_PALETTE["primary"] + """ !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
    font-weight: 700 !important;
}

/* ============================================
   BUTTONS — Gradientes e hover effects
   ============================================ */
button[kind="primary"],
[data-testid="stBaseButton-primary"] {
    background: """ + COLOR_PALETTE["gradient_primary"] + """ !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    border: none !important;
    padding: 0.5rem 1.25rem !important;
    letter-spacing: 0.025em;
    box-shadow: 0 2px 8px rgba(37, 99, 235, 0.2) !important;
    transition: all 0.2s ease !important;
}

button[kind="primary"]:hover,
[data-testid="stBaseButton-primary"]:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3) !important;
}

/* ============================================
   DATAFRAMES — Clean e moderno
   ============================================ */
[data-testid="stDataFrame"] {
    border-radius: 12px;
    border: 1px solid """ + COLOR_PALETTE["gray_200"] + """;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

[data-testid="stDataFrame"] thead tr th {
    background: """ + COLOR_PALETTE["gray_50"] + """ !important;
    color: """ + COLOR_PALETTE["gray_700"] + """ !important;
    font-weight: 600 !important;
    font-size: 0.75rem !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 2px solid """ + COLOR_PALETTE["gray_200"] + """ !important;
}

/* ============================================
   SIDEBAR — Clean e organizado
   ============================================ */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #FFFFFF 0%, #F9FAFB 100%);
    border-right: 1px solid """ + COLOR_PALETTE["gray_200"] + """;
}

section[data-testid="stSidebar"] .stMarkdown {
    color: """ + COLOR_PALETTE["gray_700"] + """;
}

/* ============================================
   PROGRESS BARS — Gradientes
   ============================================ */
[data-testid="stProgressBar"] > div > div {
    background: """ + COLOR_PALETTE["gradient_ocean"] + """ !important;
    border-radius: 8px !important;
    transition: width 0.5s ease !important;
}

/* ============================================
   DIVIDERS — Sutis
   ============================================ */
hr {
    border-color: """ + COLOR_PALETTE["gray_200"] + """ !important;
    margin: 1.5rem 0 !important;
}

/* ============================================
   ALERTS / INFO BOXES
   ============================================ */
.info-box {
    background: linear-gradient(135deg, rgba(37, 99, 235, 0.05) 0%, rgba(124, 58, 237, 0.05) 100%);
    border-left: 4px solid """ + COLOR_PALETTE["primary"] + """;
    border-radius: 8px;
    padding: 1rem 1.25rem;
    margin: 1rem 0;
}

.info-box.success {
    background: rgba(16, 185, 129, 0.05);
    border-left-color: """ + COLOR_PALETTE["success"] + """;
}

.info-box.warning {
    background: rgba(245, 158, 11, 0.05);
    border-left-color: """ + COLOR_PALETTE["warning"] + """;
}

.info-box.danger {
    background: rgba(239, 68, 68, 0.05);
    border-left-color: """ + COLOR_PALETTE["danger"] + """;
}

/* ============================================
   CHART CONTAINERS
   ============================================ */
.chart-container {
    background: white;
    border-radius: 16px;
    padding: 1.5rem;
    border: 1px solid """ + COLOR_PALETTE["gray_200"] + """;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
    margin-bottom: 1.5rem;
}

/* ============================================
   RESPONSIVE
   ============================================ */
@media (max-width: 768px) {
    .dashboard-header {
        padding: 1rem;
        margin: -1rem -1rem 1rem -1rem;
    }
    
    .metric-value {
        font-size: 1.5rem !important;
    }
}
</style>
"""


# =============================================================================
# COMPONENTES DE UI
# =============================================================================

def apply_custom_theme():
    """Aplica o tema customizado ao dashboard."""
    st.set_page_config(
        page_title="RAC Price Monitor",
        page_icon="❄️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def render_header(title: str, subtitle: str = "", icon: str = "📊"):
    """
    Renderiza um cabeçalho customizado com glassmorphism.
    
    Args:
        title: Título principal
        subtitle: Subtítulo descritivo (opcional)
        icon: Emoji ou ícone
    """
    header_html = f"""
    <div class="dashboard-header">
        <h1>{icon} {title}</h1>
        {f'<p>{subtitle}</p>' if subtitle else ''}
    </div>
    """
    st.markdown(header_html, unsafe_allow_html=True)


def metric_card(
    label: str,
    value: str,
    delta: Optional[str] = None,
    delta_positive: bool = True,
    gradient: str = "primary",
    help_text: Optional[str] = None,
):
    """
    Renderiza um card de métrica avançado.
    
    Args:
        label: Rótulo da métrica
        value: Valor principal
        delta: Variação (ex: "+12%", "-5%")
        delta_positive: Se True, delta é positivo (verde), senão negativo (vermelho)
        gradient: Tipo de gradiente ("primary", "success", "warning", "danger")
        help_text: Texto de ajuda (tooltip)
    """
    gradient_map = {
        "primary": COLOR_PALETTE["gradient_primary"],
        "success": COLOR_PALETTE["gradient_success"],
        "warning": COLOR_PALETTE["gradient_warning"],
        "danger": COLOR_PALETTE["gradient_danger"],
        "ocean": COLOR_PALETTE["gradient_ocean"],
        "sunset": COLOR_PALETTE["gradient_sunset"],
    }
    
    gradient_css = gradient_map.get(gradient, COLOR_PALETTE["gradient_primary"])
    delta_class = "positive" if delta_positive else "negative"
    delta_icon = "↑" if delta_positive else "↓"
    
    card_html = f"""
    <div class="metric-card" style="--gradient: {gradient_css};">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        {f'<div class="metric-delta {delta_class}">{delta_icon} {delta}</div>' if delta else ''}
    </div>
    """
    
    if help_text:
        st.markdown(card_html, unsafe_allow_html=True)
    else:
        st.markdown(card_html, unsafe_allow_html=True)


def kpi_card(
    title: str,
    value: str,
    icon: str,
    gradient: str = "primary",
    subtitle: Optional[str] = None,
):
    """
    Renderiza um card KPI com ícone e gradiente.
    
    Args:
        title: Título do KPI
        value: Valor principal
        icon: Emoji ou caractere como ícone
        gradient: Tipo de gradiente
        subtitle: Subtítulo opcional
    """
    gradient_map = {
        "primary": COLOR_PALETTE["gradient_primary"],
        "success": COLOR_PALETTE["gradient_success"],
        "warning": COLOR_PALETTE["gradient_warning"],
        "danger": COLOR_PALETTE["gradient_danger"],
        "ocean": COLOR_PALETTE["gradient_ocean"],
        "sunset": COLOR_PALETTE["gradient_sunset"],
    }
    
    gradient_css = gradient_map.get(gradient, COLOR_PALETTE["gradient_primary"])
    
    card_html = f"""
    <div class="kpi-card" style="--icon-gradient: {gradient_css};">
        <div class="kpi-icon">{icon}</div>
        <div class="kpi-content">
            <div class="kpi-title">{title}</div>
            <div class="kpi-value">{value}</div>
            {f'<div style="font-size: 0.75rem; color: {COLOR_PALETTE["gray_500"]}; margin-top: 0.25rem;">{subtitle}</div>' if subtitle else ''}
        </div>
    </div>
    """
    
    st.markdown(card_html, unsafe_allow_html=True)


def status_badge(status: str, show_dot: bool = True):
    """
    Renderiza um badge de status de disponibilidade.
    
    Args:
        status: "available", "unavailable", ou "limited"
        show_dot: Mostrar ponto animado
    
    Returns:
        HTML do badge
    """
    status_map = {
        "available": ("Disponível", "available"),
        "unavailable": ("Indisponível", "unavailable"),
        "limited": ("Estoque Limitado", "limited"),
    }
    
    label, css_class = status_map.get(status.lower(), (status, "limited"))
    dot_html = f'<span class="status-dot"></span>' if show_dot else ''
    
    return f"""
    <span class="status-badge {css_class}">
        {dot_html}{label}
    </span>
    """


def price_card(
    current_price: str,
    old_price: Optional[str] = None,
    label: str = "Preço Atual",
):
    """
    Renderiza um card de preço com destaque.
    
    Args:
        current_price: Preço atual formatado
        old_price: Preço antigo (opcional, mostra riscado)
        label: Rótulo do preço
    """
    old_price_html = f'<span class="price-old">{old_price}</span>' if old_price else ''
    
    card_html = f"""
    <div class="price-card">
        <div class="price-label">{label}</div>
        <div>
            <span class="price-value">{current_price}</span>
            {old_price_html}
        </div>
    </div>
    """
    
    st.markdown(card_html, unsafe_allow_html=True)


def info_box(message: str, type: str = "info"):
    """
    Renderiza uma caixa de informação/alerta.
    
    Args:
        message: Mensagem a exibir
        type: "info", "success", "warning", ou "danger"
    """
    box_html = f"""
    <div class="info-box {type}">
        {message}
    </div>
    """
    st.markdown(box_html, unsafe_allow_html=True)


# =============================================================================
# PLOTLY — Estilos de Gráficos
# =============================================================================

def style_plotly_chart(
    fig,
    height: int = 450,
    title: Optional[str] = None,
    hovermode: str = "x unified",
    show_legend: bool = True,
    legend_position: str = "top-right",
):
    """
    Aplica estilo consistente a gráficos Plotly.
    
    Args:
        fig: Figura Plotly
        height: Altura do gráfico
        title: Título opcional
        hovermode: Modo de hover
        show_legend: Mostrar legenda
        legend_position: Posição da legenda
    
    Returns:
        Figura estilizada
    """
    # Mapa de posição da legenda
    legend_pos_map = {
        "top-right": dict(x=1, y=1.02, xanchor="right", yanchor="bottom"),
        "top-left": dict(x=0, y=1.02, xanchor="left", yanchor="bottom"),
        "bottom-right": dict(x=1, y=-0.15, xanchor="right", yanchor="top"),
        "bottom-left": dict(x=0, y=-0.15, xanchor="left", yanchor="top"),
        "top-center": dict(x=0.5, y=1.02, xanchor="center", yanchor="bottom"),
    }
    
    fig.update_layout(
        height=height,
        hovermode=hovermode,
        font=dict(family="Inter, sans-serif", size=12, color=COLOR_PALETTE["gray_700"]),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=50, r=30, t=60 if title else 40, b=50),
        colorway=CHART_COLORS,
        showlegend=show_legend,
        legend=legend_pos_map.get(legend_position, legend_pos_map["top-right"]) if show_legend else None,
        title=dict(
            text=title,
            font=dict(size=16, weight=700, color=COLOR_PALETTE["gray_800"]),
            x=0,
            y=0.98,
            xanchor="left",
            yanchor="top",
        ) if title else None,
    )
    
    # Estilo dos eixos
    fig.update_xaxes(
        showgrid=True,
        gridcolor=COLOR_PALETTE["gray_100"],
        gridwidth=1,
        zeroline=False,
        showline=True,
        linecolor=COLOR_PALETTE["gray_200"],
        tickfont=dict(size=11, color=COLOR_PALETTE["gray_600"]),
    )
    
    fig.update_yaxes(
        showgrid=True,
        gridcolor=COLOR_PALETTE["gray_100"],
        gridwidth=1,
        zeroline=False,
        showline=False,
        tickfont=dict(size=11, color=COLOR_PALETTE["gray_600"]),
    )
    
    return fig


def create_availability_gauge(availability_pct: float):
    """
    Cria um gauge chart para taxa de disponibilidade.
    
    Args:
        availability_pct: Porcentagem de disponibilidade (0-100)
    
    Returns:
        Figura Plotly
    """
    # Cor baseada no valor
    if availability_pct >= 90:
        color = COLOR_PALETTE["success"]
    elif availability_pct >= 70:
        color = COLOR_PALETTE["warning"]
    else:
        color = COLOR_PALETTE["danger"]
    
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=availability_pct,
        domain={'x': [0, 1], 'y': [0, 1]},
        title={'text': "Taxa de Disponibilidade", 'font': {'size': 14, 'weight': 600}},
        number={'font': {'size': 40, 'weight': 700, 'color': COLOR_PALETTE["gray_800"]}},
        gauge={
            'axis': {
                'range': [None, 100],
                'tickwidth': 1,
                'tickcolor': COLOR_PALETTE["gray_300"],
                'tickfont': {'size': 10, 'color': COLOR_PALETTE["gray_500"]},
            },
            'bar': {'color': color, 'thickness': 0.5},
            'bgcolor': COLOR_PALETTE["gray_100"],
            'borderwidth': 0,
            'steps': [
                {'range': [0, 70], 'color': 'rgba(239, 68, 68, 0.1)'},
                {'range': [70, 90], 'color': 'rgba(245, 158, 11, 0.1)'},
                {'range': [90, 100], 'color': 'rgba(16, 185, 129, 0.1)'},
            ],
        }
    ))
    
    fig.update_layout(
        height=250,
        margin=dict(l=20, r=20, t=40, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    
    return fig


def create_price_trend_chart(df, date_col: str, price_col: str, group_col: Optional[str] = None):
    """
    Cria um gráfico de tendência de preços.
    
    Args:
        df: DataFrame com dados
        date_col: Coluna de datas
        price_col: Coluna de preços
        group_col: Coluna para agrupar (opcional)
    
    Returns:
        Figura Plotly
    """
    if group_col:
        fig = px.line(
            df,
            x=date_col,
            y=price_col,
            color=group_col,
            markers=True,
        )
    else:
        fig = px.line(
            df,
            x=date_col,
            y=price_col,
            markers=True,
        )
    
    return style_plotly_chart(fig, title="Evolução de Preços")


# =============================================================================
# UTILITÁRIOS
# =============================================================================

def format_currency(value: float, currency: str = "R$") -> str:
    """Formata valor como moeda brasileira."""
    return f"{currency} {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_percentage(value: float, decimals: int = 1) -> str:
    """Formata valor como porcentagem."""
    return f"{value:.{decimals}f}%"


def get_status_color(status: str) -> str:
    """Retorna cor hex baseada no status."""
    status_colors = {
        "available": COLOR_PALETTE["success"],
        "unavailable": COLOR_PALETTE["danger"],
        "limited": COLOR_PALETTE["warning"],
    }
    return status_colors.get(status.lower(), COLOR_PALETTE["gray_400"])


# =============================================================================
# EXEMPLO DE USO
# =============================================================================

if __name__ == "__main__":
    # Exemplo de uso do design system
    apply_custom_theme()
    
    render_header(
        title="Dashboard de Preços & Disponibilidade",
        subtitle="Monitoramento em tempo real de produtos e-commerce",
        icon="🛒"
    )
    
    st.markdown("---")
    
    # KPIs
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        kpi_card(
            title="Produtos Monitorados",
            value="1,234",
            icon="📦",
            gradient="primary"
        )
    
    with col2:
        kpi_card(
            title="Taxa de Disponibilidade",
            value="87.5%",
            icon="✅",
            gradient="success",
            subtitle="+2.3% vs ontem"
        )
    
    with col3:
        kpi_card(
            title="Preço Médio",
            value="R$ 2.459",
            icon="💰",
            gradient="ocean",
            subtitle="-5.2% vs semana passada"
        )
    
    with col4:
        kpi_card(
            title="Alertas Ativos",
            value="23",
            icon="⚠️",
            gradient="warning"
        )
    
    st.markdown("---")
    
    # Métricas avançadas
    st.subheader("📊 Métricas Principais")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        metric_card(
            label="Disponibilidade Geral",
            value="87.5%",
            delta="+2.3%",
            delta_positive=True,
            gradient="success"
        )
    
    with col2:
        metric_card(
            label="Variação de Preços",
            value="-5.2%",
            delta="-5.2%",
            delta_positive=True,  # queda de preço é positiva para consumidor
            gradient="ocean"
        )
    
    with col3:
        metric_card(
            label="Produtos Indisponíveis",
            value="156",
            delta="+12",
            delta_positive=False,
            gradient="danger"
        )
    
    st.markdown("---")
    
    # Status badges
    st.subheader("🏷️ Status de Disponibilidade")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(status_badge("available"), unsafe_allow_html=True)
    with col2:
        st.markdown(status_badge("limited"), unsafe_allow_html=True)
    with col3:
        st.markdown(status_badge("unavailable"), unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Info boxes
    st.subheader("ℹ️ Alertas e Informações")
    
    info_box("Dashboard atualizado há 5 minutos. Dados em tempo real.", "info")
    info_box("Disponibilidade aumentou 2.3% nas últimas 24 horas.", "success")
    info_box("Atenção: 3 produtos com variação de preço superior a 10%.", "warning")
    info_box("Erro na coleta de 5 produtos. Verifique logs.", "danger")
