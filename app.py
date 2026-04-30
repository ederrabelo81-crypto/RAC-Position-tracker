"""
app.py — RAC Price Monitor Dashboard

Usage (local):
    streamlit run app.py

Usage (remote access):
    streamlit run app.py --server.address=0.0.0.0 --server.port=8501
    Then open: http://<your-ip>:8501
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv(Path(__file__).parent / ".env")
PROJECT_ROOT = Path(__file__).parent

# Raise Pandas Styler cell limit to cover large datasets (default is 262 144).
# Row-level Midea highlighting is skipped above _STYLE_CELL_THRESHOLD anyway,
# but the format() call still needs the higher limit for float columns.
pd.set_option("styler.render.max_elements", 2_000_000)
_STYLE_CELL_THRESHOLD = 50_000  # cells above which row highlight is skipped

# ---------------------------------------------------------------------------
# Design system — colors, CSS, chart style helper
# ---------------------------------------------------------------------------

_CHART_COLORS = [
    "#1a56db", "#f97316", "#059669", "#8b5cf6",
    "#ef4444", "#0891b2", "#d97706", "#db2777",
]

_CSS = """<style>
/* ===========================
   IMPORT FONT - INTER
   =========================== */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* ===========================
   GLOBAL STYLES
   =========================== */
/* Scope to body so inheritance cascades Inter to text while preserving
   explicit font-family on Material Symbols icon spans (if overridden with
   * + !important those icon names render as raw text, e.g. "arrow_right"). */
body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif !important;
}

/* Explicitly reinforce Inter for common content elements */
p, h1, h2, h3, h4, h5, h6,
.stMarkdown, label, button, input, textarea,
[data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif !important;
}

/* Main container */
.main > div:first-child {
    max-width: 1400px !important;
    padding: 0 1rem;
}

/* ===========================
   HEADER CUSTOMIZATION
   =========================== */
[data-testid="stHeader"] {
    background: linear-gradient(135deg, #1a56db 0%, #1e40af 50%, #1e3a8a 100%) !important;
    border-bottom: 3px solid #fbbf24 !important;
    box-shadow: 0 4px 20px rgba(26, 86, 219, 0.3) !important;
}

/* Hide Streamlit branding */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}

/* ===========================
   ENHANCED METRIC CARDS
   =========================== */
[data-testid="stMetric"] {
    background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%) !important;
    border: 1px solid #e2e8f0 !important;
    border-left: 4px solid #1a56db !important;
    border-radius: 16px !important;
    padding: 1.5rem !important;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08), 0 2px 4px rgba(0, 0, 0, 0.04) !important;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
    position: relative;
    overflow: hidden;
}

[data-testid="stMetric"]:hover {
    transform: translateY(-4px) !important;
    box-shadow: 0 12px 24px rgba(26, 86, 219, 0.15), 0 8px 16px rgba(0, 0, 0, 0.08) !important;
    border-left-color: #fbbf24 !important;
}

[data-testid="stMetric"]::before {
    content: '';
    position: absolute;
    top: -50%;
    right: -50%;
    width: 100%;
    height: 100%;
    background: radial-gradient(circle, rgba(26, 86, 219, 0.05) 0%, transparent 70%);
    pointer-events: none;
}

[data-testid="stMetricLabel"] {
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    color: #64748b !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    margin-bottom: 0.4rem !important;
    white-space: normal !important;
    overflow-wrap: break-word !important;
    word-break: break-word !important;
}

[data-testid="stMetricValue"] {
    font-size: 1.6rem !important;
    font-weight: 800 !important;
    background: linear-gradient(135deg, #1e293b 0%, #1a56db 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    line-height: 1.2 !important;
    white-space: normal !important;
    overflow-wrap: break-word !important;
    word-break: break-word !important;
}

[data-testid="stMetricDelta"] {
    font-size: 0.9rem !important;
    font-weight: 600 !important;
    padding: 0.25rem 0.5rem !important;
    border-radius: 6px !important;
    margin-top: 0.5rem !important;
}

[data-testid="stMetricDelta"][data-testid="stMetricDeltaPositive"] {
    background: rgba(5, 150, 105, 0.1) !important;
    color: #059669 !important;
}

[data-testid="stMetricDelta"][data-testid="stMetricDeltaNegative"] {
    background: rgba(239, 68, 68, 0.1) !important;
    color: #dc2626 !important;
}

/* ===========================
   MODERN TABS
   =========================== */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
    border-radius: 12px;
    padding: 6px;
    margin-bottom: 1.5rem;
    box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.06);
}

.stTabs [data-baseweb="tab"] {
    border-radius: 8px !important;
    padding: 0.75rem 1.5rem !important;
    font-size: 0.9rem !important;
    font-weight: 600 !important;
    color: #64748b !important;
    background: transparent !important;
    border: 2px solid transparent !important;
    transition: all 0.2s ease !important;
    min-height: auto !important;
}

.stTabs [data-baseweb="tab"]:hover {
    color: #1a56db !important;
    background: rgba(255, 255, 255, 0.5) !important;
}

.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%) !important;
    color: #1a56db !important;
    font-weight: 700 !important;
    box-shadow: 0 4px 12px rgba(26, 86, 219, 0.15), 0 2px 4px rgba(0, 0, 0, 0.08) !important;
    border: 2px solid #1a56db !important;
    transform: translateY(-2px);
}

/* ===========================
   PRIMARY BUTTONS
   =========================== */
button[kind="primary"],
[data-testid="stBaseButton-primary"] {
    background: linear-gradient(135deg, #1a56db 0%, #1e40af 100%) !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-size: 0.95rem !important;
    border: none !important;
    letter-spacing: 0.02em !important;
    padding: 0.5rem 1.5rem !important;
    box-shadow: 0 4px 12px rgba(26, 86, 219, 0.3) !important;
    transition: all 0.2s ease !important;
}

button[kind="primary"]:hover,
[data-testid="stBaseButton-primary"]:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 20px rgba(26, 86, 219, 0.4) !important;
    background: linear-gradient(135deg, #1e40af 0%, #1e3a8a 100%) !important;
}

/* Secondary buttons — scoped to Streamlit buttons only to avoid deforming
   Plotly modebar, slider handles, and other embedded <button> elements */
.stButton > button:not([kind="primary"]),
.stDownloadButton > button:not([kind="primary"]),
[data-testid="stBaseButton-secondary"] {
    border: 2px solid #e2e8f0 !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    color: #475569 !important;
    background: #ffffff !important;
    transition: all 0.2s ease !important;
}

.stButton > button:not([kind="primary"]):hover,
.stDownloadButton > button:not([kind="primary"]):hover,
[data-testid="stBaseButton-secondary"]:hover {
    border-color: #1a56db !important;
    color: #1a56db !important;
    background: #eff6ff !important;
}

/* ===========================
   DATAFRAMES / TABLES
   =========================== */
[data-testid="stDataFrame"] {
    border-radius: 12px !important;
    border: 1px solid #e2e8f0 !important;
    overflow: hidden !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04) !important;
    background: #ffffff !important;
}

[data-testid="stDataFrame"] thead tr th {
    background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%) !important;
    color: #475569 !important;
    font-weight: 700 !important;
    font-size: 0.85rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
    border-bottom: 2px solid #cbd5e1 !important;
    padding: 1rem !important;
}

[data-testid="stDataFrame"] tbody td {
    padding: 0.75rem 1rem !important;
    border-bottom: 1px solid #f1f5f9 !important;
    font-size: 0.9rem !important;
    color: #1e293b !important;
}

[data-testid="stDataFrame"] tbody tr:hover {
    background: linear-gradient(90deg, #eff6ff 0%, #f8fafc 100%) !important;
}

/* ===========================
   DIVIDERS
   =========================== */
hr {
    border-color: #e2e8f0 !important;
    border-width: 2px !important;
    margin: 1.5rem 0 !important;
}

/* ===========================
   PROGRESS BAR
   =========================== */
[data-testid="stProgressBar"] > div > div {
    background: linear-gradient(90deg, #1a56db 0%, #0891b2 50%, #059669 100%) !important;
    border-radius: 8px !important;
    box-shadow: 0 2px 8px rgba(26, 86, 219, 0.3) !important;
}

/* ===========================
   SIDEBAR STYLING
   =========================== */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%) !important;
    border-right: 1px solid #334155 !important;
}

section[data-testid="stSidebar"] .stMarkdown h2,
section[data-testid="stSidebar"] .stMarkdown h3 {
    color: #fbbf24 !important;
    font-weight: 700 !important;
}

section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stMarkdown,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span:not([aria-hidden]),
section[data-testid="stSidebar"] div[data-testid] {
    color: #ffffff !important;
}

/* Navigation links in sidebar - MAXIMUM CONTRAST */
section[data-testid="stSidebar"] a,
section[data-testid="stSidebar"] .stNavigation a,
section[data-testid="stSidebar"] nav a,
section[data-testid="stSidebar"] ul li a,
section[data-testid="stSidebar"] .stMultiSelect label,
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stSlider label,
section[data-testid="stSidebar"] .stNumberInput label,
section[data-testid="stSidebar"] .stTextInput label,
section[data-testid="stSidebar"] .stDateInput label {
    color: #ffffff !important;
    font-weight: 600 !important;
    text-decoration: none !important;
    transition: all 0.2s ease !important;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3) !important;
}

section[data-testid="stSidebar"] a:hover,
section[data-testid="stSidebar"] .stNavigation a:hover,
section[data-testid="stSidebar"] nav a:hover,
section[data-testid="stSidebar"] ul li a:hover {
    color: #fbbf24 !important;
    background: rgba(251, 191, 36, 0.15) !important;
    border-radius: 6px !important;
}

/* Selected/active navigation item */
section[data-testid="stSidebar"] a.active,
section[data-testid="stSidebar"] .stNavigation a.active,
section[data-testid="stSidebar"] nav a.active {
    color: #fbbf24 !important;
    font-weight: 700 !important;
    background: rgba(251, 191, 36, 0.2) !important;
    border-left: 4px solid #fbbf24 !important;
    padding-left: 10px !important;
    box-shadow: inset 0 0 10px rgba(251, 191, 36, 0.1) !important;
}

/* Sidebar widget labels - FORCE WHITE TEXT */
section[data-testid="stSidebar"] .stWidgetLabel,
section[data-testid="stSidebar"] .st-emotion-cache label,
section[data-testid="stSidebar"] div[data-testid="stWidgetLabel"] {
    color: #ffffff !important;
    font-weight: 600 !important;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3) !important;
}

/* Sidebar radio/checkbox */
section[data-testid="stSidebar"] .stRadio > div,
section[data-testid="stSidebar"] .stCheckbox > div {
    background: rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 0.5rem;
    margin: 0.25rem 0;
    border: 1px solid rgba(255, 255, 255, 0.1);
}

section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stCheckbox label {
    color: #ffffff !important;
    font-weight: 500 !important;
    transition: color 0.2s ease;
}

section[data-testid="stSidebar"] .stRadio label:hover,
section[data-testid="stSidebar"] .stCheckbox label:hover {
    color: #fbbf24 !important;
}

/* Selected sidebar item */
section[data-testid="stSidebar"] .stRadio input:checked + span {
    color: #fbbf24 !important;
    font-weight: 700 !important;
}

/* Navigation buttons — full text, left-aligned */
section[data-testid="stSidebar"] .stButton > button {
    text-align: left !important;
    white-space: normal !important;
    overflow: visible !important;
    height: auto !important;
    min-height: 2.2rem !important;
    padding: 0.35rem 0.75rem !important;
    line-height: 1.4 !important;
    font-size: 0.88rem !important;
}

/* Page link specific styling */
section[data-testid="stSidebar"] nav a[href],
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {
    color: #ffffff !important;
    font-weight: 600 !important;
    padding: 0.5rem 1rem !important;
    margin: 0.25rem 0 !important;
    border-radius: 6px !important;
    display: block !important;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3) !important;
}

section[data-testid="stSidebar"] nav a[href]:hover,
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover {
    background: rgba(251, 191, 36, 0.15) !important;
    color: #fbbf24 !important;
}

/* ===========================
   CARDS & CONTAINERS
   =========================== */
div.stAlert {
    border-radius: 12px !important;
    border: none !important;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08) !important;
}

div.stSuccess {
    background: linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%) !important;
    border-left: 4px solid #059669 !important;
}

div.stWarning {
    background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%) !important;
    border-left: 4px solid #d97706 !important;
}

div.stError {
    background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%) !important;
    border-left: 4px solid #dc2626 !important;
}

div.stInfo {
    background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%) !important;
    border-left: 4px solid #1a56db !important;
}

/* ===========================
   EXPANDER / COLLAPSIBLE
   =========================== */
.streamlit-expanderHeader {
    background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%) !important;
    border-radius: 10px !important;
    border: 1px solid #e2e8f0 !important;
    font-weight: 600 !important;
    color: #1e293b !important;
    padding: 1rem !important;
    transition: all 0.2s ease !important;
}

.streamlit-expanderHeader:hover {
    background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%) !important;
    border-color: #1a56db !important;
}

/* ===========================
   NUMBER INPUT / TEXT INPUT
   =========================== */
input[type="number"],
input[type="text"],
.stTextInput > div > div > input {
    border-radius: 8px !important;
    border: 2px solid #e2e8f0 !important;
    padding: 0.6rem 1rem !important;
    font-size: 0.95rem !important;
    transition: all 0.2s ease !important;
}

input[type="number"]:focus,
input[type="text"]:focus,
.stTextInput > div > div > input:focus {
    border-color: #1a56db !important;
    box-shadow: 0 0 0 3px rgba(26, 86, 219, 0.1) !important;
}

/* ===========================
   SELECTBOX
   =========================== */
.stSelectbox > div > div {
    border-radius: 8px !important;
    border: 2px solid #e2e8f0 !important;
    transition: all 0.2s ease !important;
}

.stSelectbox > div > div:hover {
    border-color: #cbd5e1 !important;
}

/* ===========================
   SLIDER
   =========================== */
/* Target only the track bar (BaseWeb slider track), not label/tick rows */
.stSlider [data-baseweb="slider"] > div:nth-child(2) {
    background: #e2e8f0 !important;
}

/* Slider thumb (native + BaseWeb) */
.stSlider [role="slider"] {
    background: #ffffff !important;
    border: 3px solid #1a56db !important;
    box-shadow: 0 2px 8px rgba(26, 86, 219, 0.4) !important;
}

/* ===========================
   CHART CONTAINERS
   =========================== */
.chart-container {
    background: #ffffff;
    border-radius: 16px;
    padding: 1.5rem;
    border: 1px solid #e2e8f0;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
    margin-bottom: 1.5rem;
}

/* ===========================
   CUSTOM BADGES (for availability status)
   =========================== */
.badge-available {
    display: inline-block;
    padding: 0.25rem 0.75rem;
    background: linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%);
    color: #059669;
    border-radius: 20px;
    font-weight: 600;
    font-size: 0.85rem;
    border: 1px solid #059669;
}

.badge-unavailable {
    display: inline-block;
    padding: 0.25rem 0.75rem;
    background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%);
    color: #dc2626;
    border-radius: 20px;
    font-weight: 600;
    font-size: 0.85rem;
    border: 1px solid #dc2626;
}

.badge-warning {
    display: inline-block;
    padding: 0.25rem 0.75rem;
    background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
    color: #d97706;
    border-radius: 20px;
    font-weight: 600;
    font-size: 0.85rem;
    border: 1px solid #d97706;
}

/* ===========================
   ANIMATIONS
   =========================== */
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
}

@keyframes pulse {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.05); }
}

.metric-card {
    animation: fadeIn 0.5s ease-out;
}

/* ===========================
   RESPONSIVE ADJUSTMENTS
   =========================== */
@media (max-width: 768px) {
    [data-testid="stMetricValue"] {
        font-size: 1.8rem !important;
    }
    
    .stTabs [data-baseweb="tab"] {
        padding: 0.5rem 1rem !important;
        font-size: 0.8rem !important;
    }
}
</style>"""


def _apply_chart_style(fig, height: int = 440, hovermode: str = "x unified") -> None:
    """Apply consistent visual style to a Plotly figure in-place."""
    fig.update_layout(
        height=height,
        hovermode=hovermode,
        font=dict(family="Inter, -apple-system, sans-serif", size=13, color="#1e293b"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title=dict(
            font=dict(size=15, color="#1e293b", family="Inter, sans-serif"),
            x=0,
            xanchor="left",
            pad=dict(t=4, b=4),
        ),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.18,
            xanchor="center",
            x=0.5,
            font=dict(size=11, color="#475569"),
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#e2e8f0",
            borderwidth=1,
            title_text="",        # remove o label "Platform" / "Brand" acima da legenda
            itemsizing="constant",
            tracegroupgap=4,
        ),
        margin=dict(l=50, r=20, t=48, b=140),
        colorway=_CHART_COLORS,
    )
    fig.update_xaxes(
        showgrid=True, gridcolor="#e2e8f0", gridwidth=1,
        zeroline=False, showline=True, linecolor="#cbd5e1",
    )
    fig.update_yaxes(
        showgrid=True, gridcolor="#e2e8f0", gridwidth=1,
        zeroline=False, showline=False,
    )


_MIDEA_BRAND = "Midea"


def _brand_color_map(values) -> dict:
    """Discrete color map: Midea → primary brand blue; others rotate through palette."""
    unique = sorted(set(str(v) for v in values if pd.notna(v)))
    secondary = [c for c in _CHART_COLORS if c != _CHART_COLORS[0]]
    cmap, idx = {}, 0
    for v in unique:
        if v == _MIDEA_BRAND:
            cmap[v] = _CHART_COLORS[0]
        else:
            cmap[v] = secondary[idx % len(secondary)]
            idx += 1
    return cmap


def _emphasize_midea_traces(fig) -> None:
    """Make Midea's trace thicker and markers bigger so it stands out."""
    for trace in fig.data:
        if getattr(trace, "name", None) == _MIDEA_BRAND:
            if hasattr(trace, "line") and trace.line is not None:
                trace.line.width = 4.5
            if hasattr(trace, "marker") and trace.marker is not None:
                trace.marker.size = 10


def _style_midea_df(df: pd.DataFrame, brand_col: str = "marca"):
    """Return a Pandas Styler that highlights Midea rows and limits float decimals.

    Row highlighting is skipped when the frame exceeds _STYLE_CELL_THRESHOLD
    cells — at that scale every row would be highlighted or it's too large to
    render efficiently.  Float formatting is always applied.
    """
    styler = df.style
    if df.size <= _STYLE_CELL_THRESHOLD and brand_col in df.columns:
        def _row_style(row):
            if row[brand_col] == _MIDEA_BRAND:
                return ["background-color: #eff6ff; font-weight: 700; color: #1d4ed8"] * len(row)
            return [""] * len(row)
        styler = styler.apply(_row_style, axis=1)
    float_cols = df.select_dtypes(include="float").columns.tolist()
    if float_cols:
        styler = styler.format({col: "{:.2f}" for col in float_cols})
    return styler


st.set_page_config(
    page_title="RAC Price Monitor",
    page_icon="❄️",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(_CSS, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Platform registry (active platforms only)
# ---------------------------------------------------------------------------

PLATFORMS = {
    "ml":              "Mercado Livre",
    "magalu":          "Magalu",
    "amazon":          "Amazon",
    "google_shopping": "Google Shopping",
    "leroy":           "Leroy Merlin",
    "dealers":         "Dealers (33 sites)",
}

# ---------------------------------------------------------------------------
# Platform name normalization
#
# The DB may contain typos or variations of platform names (e.g.,
# "FerreiraCosta" and "FerreiraCoasta"). We normalize these to a single
# canonical name for display in filters, then expand back when querying.
# ---------------------------------------------------------------------------

_PLATFORM_ALIASES = {
    # Canonical display name -> all raw DB values that map to it
    "Ferreira Costa": ["FerreiraCosta", "FerreiraCoasta"],
    "WebContinental":  ["WebContinental", "Webcontinental"],
}

# Build reverse map: variation -> canonical
_VARIATION_TO_CANONICAL = {}
for canonical, variations in _PLATFORM_ALIASES.items():
    for var in variations:
        _VARIATION_TO_CANONICAL[var] = canonical


def _normalize_platform(platform: str) -> str:
    """Normalize a raw platform name from DB to its canonical form."""
    return _VARIATION_TO_CANONICAL.get(platform, platform)


def _expand_platforms(platforms: list[str]) -> list[str]:
    """
    Given a list of canonical platform names (what the user selected),
    return the full set of raw DB platform values to include in the query.
    E.g. ["Ferreira Costa"] -> ["FerreiraCosta", "FerreiraCoasta"]
    """
    expanded = set()
    for p in platforms:
        variants = _PLATFORM_ALIASES.get(p, [p])
        expanded.update(variants)
    return sorted(expanded)

# ---------------------------------------------------------------------------
# Supabase client (cached — one connection per session)
# ---------------------------------------------------------------------------

def _resolve_secret(name: str) -> str:
    """st.secrets (Streamlit Cloud) → os.getenv (.env local) → ''."""
    try:
        v = st.secrets.get(name, "")
        if v:
            return str(v).strip()
    except Exception:
        pass
    return os.getenv(name, "").strip()


@st.cache_resource(show_spinner=False)
def _get_supabase():
    url = _resolve_secret("SUPABASE_URL")
    key = _resolve_secret("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


BTU_OPTIONS = ["9000", "12000", "18000", "24000", "36000", "48000", "60000"]

# ---------------------------------------------------------------------------
# Product Type filter — patterns to match inside the normalized produto string
# (Tipo + Forma combined, since both live in the produto column after
# normalize_product_name())
# ---------------------------------------------------------------------------
PRODUCT_TYPE_OPTIONS: dict = {
    # Tipo (compressor)
    "Inverter":   ["inverter"],
    "On/Off":     ["on/off", "on-off", "convencional"],
    # Forma (form factor)
    "Hi-Wall":    ["hi-wall", "hi wall", "hiwall"],
    "Janela":     ["janela", "janeleiro", "window"],
    "Cassete":    ["cassete", "cassette"],
    "Piso-Teto":  ["piso-teto", "piso teto"],
    "Portátil":   ["portátil", "portatil"],
}

# ---------------------------------------------------------------------------
# Brand alias expansion
#
# extract_brand() uses config.BRANDS verbatim, so the DB can contain:
#   "Springer Midea", "Midea Carrier", "Springer", "Midea" (all = Midea canonical)
#   "Britania" and "Britânia" (same brand, two spellings in BRANDS)
#
# We build two dicts from config.BRANDS + normalize_product._BRAND_ALIASES:
#   _MARCA_TO_CANONICAL: raw DB value  → canonical display name
#   _CANONICAL_TO_MARCAS: canonical   → [all raw DB values to query]
# ---------------------------------------------------------------------------

def _build_brand_maps() -> tuple:
    """Build brand normalization maps from config + normalize_product."""
    try:
        from utils.normalize_product import _BRAND_ALIASES
        from config import BRANDS
    except Exception:
        return {}, {}

    canonical_to_raws: dict = {}
    raw_to_canonical:  dict = {}

    for raw_brand in BRANDS:
        # _BRAND_ALIASES uses lowercase keys
        canonical = _BRAND_ALIASES.get(raw_brand.lower(), raw_brand)
        canonical_to_raws.setdefault(canonical, []).append(raw_brand)
        raw_to_canonical[raw_brand] = canonical

    return canonical_to_raws, raw_to_canonical


_CANONICAL_TO_MARCAS, _MARCA_TO_CANONICAL = _build_brand_maps()


def _expand_brands(brands: list) -> list:
    """
    Given a list of canonical brand names (what the user selected),
    return the full set of raw DB marca values to include in the query.
    E.g. ["Midea"] → ["Midea", "Springer Midea", "Midea Carrier", "Springer"]
    """
    expanded = set()
    for b in brands:
        variants = _CANONICAL_TO_MARCAS.get(b)
        if variants:
            expanded.update(variants)
        else:
            expanded.add(b)  # unknown brand — use as-is
    return sorted(expanded)


_SUPABASE_PAGE = 1000  # PostgREST default server-side max_rows cap


def query_coletas(
    start_date: date,
    end_date: date,
    platforms: list[str] | None = None,
    platform_types: list[str] | None = None,
    brands: list[str] | None = None,
    sellers: list[str] | None = None,
    keywords: list[str] | None = None,
    products: list[str] | None = None,
    btu_filter: list[str] | None = None,
    product_types: list[str] | None = None,
    max_position: int | None = None,
    limit: int = 50000,
) -> pd.DataFrame:
    """Query the coletas table with filters, paginating past the 1000-row cap.

    PostgREST enforces a server-side max_rows of 1000 regardless of what
    .limit() requests.  We use .range() in a loop to collect up to `limit`
    rows transparently.
    """
    client = _get_supabase()
    if client is None:
        st.error("Supabase não conectado. Verifique o arquivo .env.")
        return pd.DataFrame()

    def _build_q():
        """Fresh filtered query (no range yet — added per-page in the loop)."""
        q = (
            client.table("coletas")
            .select("*")
            .gte("data", str(start_date))
            .lte("data", str(end_date))
            .order("data", desc=True)
        )
        if platforms:
            q = q.in_("plataforma", _expand_platforms(platforms))
        if platform_types:
            q = q.in_("tipo", platform_types)
        if brands:
            q = q.in_("marca", _expand_brands(brands))
        if sellers:
            q = q.in_("seller", sellers)
        if keywords:
            q = q.in_("keyword", keywords)
        if products:
            q = q.in_("produto", products)
        if max_position is not None:
            q = q.lte("posicao_geral", max_position)
        if btu_filter:
            parts = []
            for btu in btu_filter:
                parts.append(f"produto.ilike.%{btu}%")
                try:
                    dotted = f"{int(btu):,}".replace(",", ".")
                    if dotted != btu:
                        parts.append(f"produto.ilike.%{dotted}%")
                except ValueError:
                    pass
            q = q.or_(",".join(parts))
        if product_types:
            parts = []
            for label in product_types:
                for pat in PRODUCT_TYPE_OPTIONS.get(label, [label]):
                    parts.append(f"produto.ilike.%{pat}%")
            if parts:
                q = q.or_(",".join(parts))
        return q

    try:
        all_data: list = []
        offset = 0
        while len(all_data) < limit:
            fetch = min(_SUPABASE_PAGE, limit - len(all_data))
            resp = _build_q().range(offset, offset + fetch - 1).execute()
            if not resp.data:
                break
            all_data.extend(resp.data)
            if len(resp.data) < fetch:
                break  # server returned fewer rows than requested → last page
            offset += fetch

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        df["data"] = pd.to_datetime(df["data"]).dt.date
        for col in ["posicao_organica", "posicao_patrocinada", "posicao_geral", "qtd_avaliacoes"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in ["preco", "avaliacao"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "marca" in df.columns and _MARCA_TO_CANONICAL:
            df["marca"] = df["marca"].map(
                lambda x: _MARCA_TO_CANONICAL.get(x, x) if x else x
            )
        return df
    except Exception as exc:
        st.error(f"Erro na consulta: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def get_filter_options() -> dict:
    """Fetch distinct values for filter dropdowns (last 90 days), paginated."""
    empty = {"platforms": [], "platform_types": [], "brands": [], "keywords": [], "sellers": []}
    client = _get_supabase()
    if client is None:
        return empty
    try:
        since = str(date.today() - timedelta(days=90))
        all_rows: list = []
        offset = 0
        while True:
            resp = (
                client.table("coletas")
                .select("plataforma, tipo, marca, keyword, seller")
                .gte("data", since)
                .range(offset, offset + _SUPABASE_PAGE - 1)
                .execute()
            )
            if not resp.data:
                break
            all_rows.extend(resp.data)
            if len(resp.data) < _SUPABASE_PAGE:
                break
            offset += _SUPABASE_PAGE
        df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame()
        if df.empty:
            return empty

        # Normalize raw marca values to canonical names so "Springer Midea",
        # "Midea Carrier" and "Springer" all appear as "Midea" in the dropdown.
        brands_canonical = sorted(set(
            _MARCA_TO_CANONICAL.get(b, b)
            for b in df["marca"].dropna().unique()
        ))

        # Normalize platform names to handle variations (e.g., "FerreiraCosta"
        # and "FerreiraCoasta" both appear as "Ferreira Costa")
        platforms_normalized = sorted(set(
            _normalize_platform(p)
            for p in df["plataforma"].dropna().unique()
        ))

        return {
            "platforms":      platforms_normalized,
            "platform_types": sorted(df["tipo"].dropna().unique().tolist()) if "tipo" in df.columns else [],
            "brands":         brands_canonical,
            "keywords":       sorted(df["keyword"].dropna().unique().tolist()),
            "sellers":        sorted(df["seller"].dropna().unique().tolist()) if "seller" in df.columns else [],
        }
    except Exception as exc:
        st.warning(f"Filter options query failed: {exc}")
        return empty


@st.cache_data(ttl=300, show_spinner=False)
def get_sku_options(
    brands: tuple = (),
    btu_filter: tuple = (),
    product_types: tuple = (),
) -> list:
    """Fetch distinct product names (last 90 days), paginated past the 1000-row cap."""
    client = _get_supabase()
    if client is None:
        return []
    try:
        since = str(date.today() - timedelta(days=90))

        def _base_q():
            q = (
                client.table("coletas")
                .select("produto")
                .gte("data", since)
                .not_.is_("produto", "null")
            )
            if brands:
                q = q.in_("marca", _expand_brands(list(brands)))
            if btu_filter:
                parts = []
                for btu in btu_filter:
                    parts.append(f"produto.ilike.%{btu}%")
                    try:
                        dotted = f"{int(btu):,}".replace(",", ".")
                        if dotted != btu:
                            parts.append(f"produto.ilike.%{dotted}%")
                    except ValueError:
                        pass
                if parts:
                    q = q.or_(",".join(parts))
            if product_types:
                parts = []
                for label in product_types:
                    for pat in PRODUCT_TYPE_OPTIONS.get(label, [label]):
                        parts.append(f"produto.ilike.%{pat}%")
                if parts:
                    q = q.or_(",".join(parts))
            return q

        all_rows: list = []
        offset = 0
        while True:
            resp = _base_q().range(offset, offset + _SUPABASE_PAGE - 1).execute()
            if not resp.data:
                break
            all_rows.extend(resp.data)
            if len(resp.data) < _SUPABASE_PAGE:
                break
            offset += _SUPABASE_PAGE
        return sorted({r["produto"] for r in all_rows if r.get("produto")})
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def _init_state():
    defaults = {
        "process":          None,
        "running":          False,
        "log":              "",
        "run_done":         False,
        "tasks_done":       0,
        "total_tasks":      1,
        "start_time":       None,
        "current_platform": "",
        "current_keyword":  "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

# ---------------------------------------------------------------------------
# Global filter presets (persisted to filter_presets.json)
# ---------------------------------------------------------------------------

_FILTER_PRESETS_FILE = PROJECT_ROOT / "filter_presets.json"


def _load_presets() -> dict:
    try:
        if _FILTER_PRESETS_FILE.exists():
            return json.loads(_FILTER_PRESETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_presets(presets: dict) -> None:
    try:
        _FILTER_PRESETS_FILE.write_text(
            json.dumps(presets, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Global filter renderer — call inside a `with st.sidebar:` block
# ---------------------------------------------------------------------------

def _render_global_filters() -> None:
    """Render persistent global filters in the sidebar."""
    opts = get_filter_options()
    with st.expander("🌐 Filtros Globais", expanded=False):
        st.date_input(
            "Período",
            value=(date.today() - timedelta(days=7), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="gf_dates",
        )
        st.multiselect(
            "Plataformas", opts["platforms"],
            placeholder="Selecione plataformas…",
            key="gf_platforms",
        )
        st.multiselect(
            "Marcas", opts["brands"],
            placeholder="Selecione marcas…",
            key="gf_brands",
        )
        st.checkbox("Comparar período anterior", key="gf_compare")

        if st.session_state.get("gf_compare"):
            st.date_input(
                "Período de comparação",
                value=(date.today() - timedelta(days=14), date.today() - timedelta(days=8)),
                max_value=date.today(),
                format="DD/MM/YYYY",
                key="gf_cmp_dates",
            )

        # Preset save / load
        st.caption("Presets")
        presets = _load_presets()
        preset_name = st.text_input(
            "Nome do preset",
            placeholder="Ex: Midea - 7 dias",
            key="gf_preset_name",
            label_visibility="collapsed",
        )
        if st.button("💾 Salvar preset", key="gf_save_preset",
                     use_container_width=True, help="Salvar filtros atuais como preset"):
            if preset_name:
                gf = st.session_state.get("gf_dates", ())
                presets[preset_name] = {
                    "start":     str(gf[0]) if gf else str(date.today() - timedelta(days=7)),
                    "end":       str(gf[1]) if len(gf) > 1 else str(date.today()),
                    "platforms": st.session_state.get("gf_platforms", []),
                    "brands":    st.session_state.get("gf_brands", []),
                }
                _save_presets(presets)
                st.success(f"Salvo: '{preset_name}'")
            else:
                st.warning("Digite um nome para o preset.")

        if presets:
            sel = st.selectbox(
                "Carregar preset",
                ["— selecione —"] + list(presets.keys()),
                key="gf_load_preset",
                label_visibility="collapsed",
            )
            if sel and sel != "— selecione —" and sel != st.session_state.get("_last_loaded_preset"):
                st.session_state["_last_loaded_preset"] = sel
                p = presets[sel]
                try:
                    st.session_state["gf_dates"]     = (date.fromisoformat(p["start"]), date.fromisoformat(p["end"]))
                    st.session_state["gf_platforms"] = p.get("platforms", [])
                    st.session_state["gf_brands"]    = p.get("brands", [])
                    st.rerun()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Global filter accessors
# ---------------------------------------------------------------------------

def _gf_dates() -> tuple:
    gf = st.session_state.get("gf_dates", ())
    if len(gf) >= 2:
        return gf[0], gf[1]
    return date.today() - timedelta(days=7), date.today()


def _gf_platforms() -> list:
    return list(st.session_state.get("gf_platforms", []))


def _gf_brands() -> list:
    return list(st.session_state.get("gf_brands", []))


def _gf_compare() -> bool:
    return bool(st.session_state.get("gf_compare", False))


def _gf_cmp_dates() -> tuple:
    gf = st.session_state.get("gf_cmp_dates", ())
    if len(gf) >= 2:
        return gf[0], gf[1]
    return date.today() - timedelta(days=14), date.today() - timedelta(days=8)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _csv_download_btn(
    df: pd.DataFrame,
    filename: str,
    label: str = "⬇️ Exportar CSV",
    key: str | None = None,
) -> None:
    """Render a UTF-8-BOM CSV download button for `df`."""
    csv_bytes = df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    kwargs = {"label": label, "data": csv_bytes, "file_name": filename, "mime": "text/csv"}
    if key:
        kwargs["key"] = key
    st.download_button(**kwargs)


def _fmt_brl(value: float) -> str:
    """Format float as Brazilian Real string: R$ 1.234,56"""
    try:
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


@st.cache_data(ttl=300, show_spinner=False)
def _overview_data(
    start_str: str,
    end_str: str,
    platforms_tuple: tuple,
    brands_tuple: tuple,
    limit: int = 15000,
) -> pd.DataFrame:
    """Cached Supabase query for overview / top-movers pages."""
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()

    def _build_q():
        q = (
            client.table("coletas")
            .select("*")
            .gte("data", start_str)
            .lte("data", end_str)
            .order("data", desc=True)
        )
        if platforms_tuple:
            q = q.in_("plataforma", _expand_platforms(list(platforms_tuple)))
        if brands_tuple:
            q = q.in_("marca", _expand_brands(list(brands_tuple)))
        return q

    try:
        all_data: list = []
        offset = 0
        while len(all_data) < limit:
            fetch = min(_SUPABASE_PAGE, limit - len(all_data))
            resp = _build_q().range(offset, offset + fetch - 1).execute()
            if not resp.data:
                break
            all_data.extend(resp.data)
            if len(resp.data) < fetch:
                break
            offset += fetch

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        df["data"] = pd.to_datetime(df["data"]).dt.date
        for col in ["posicao_organica", "posicao_patrocinada", "posicao_geral", "qtd_avaliacoes"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in ["preco", "avaliacao"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "marca" in df.columns and _MARCA_TO_CANONICAL:
            df["marca"] = df["marca"].map(lambda x: _MARCA_TO_CANONICAL.get(x, x) if x else x)
        return df
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Page 1 — Run Collection
# ---------------------------------------------------------------------------

def page_run_collection():
    st.title("🚀 Run Collection")
    st.caption("Select platforms and keywords, then start the scraping bot.")

    _init_state()

    col_left, col_right = st.columns([1, 1], gap="large")

    with col_left:
        st.subheader("Platforms")
        selected_platforms = []
        for key, label in PLATFORMS.items():
            if st.checkbox(label, value=True, key=f"plat_{key}"):
                selected_platforms.append(key)

        st.subheader("Pages per keyword")
        pages = st.slider("Pages", min_value=1, max_value=5, value=2)

        st.subheader("Options")
        headless = st.checkbox("Headless browser (recommended)", value=True)

    with col_right:
        st.subheader("Keywords")

        # Load keywords from config
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from config import KEYWORDS_LIST
            kw_by_cat: dict = {}
            for kw in KEYWORDS_LIST:
                kw_by_cat.setdefault(kw.category, []).append(kw.term)
        except Exception:
            kw_by_cat = {"All": []}

        selected_keywords: list[str] = []
        for cat, terms in kw_by_cat.items():
            with st.expander(f"{cat} ({len(terms)})", expanded=False):
                for term in terms:
                    if st.checkbox(term, value=True, key=f"kw_{term}"):
                        selected_keywords.append(term)

    st.divider()

    # --- Start / Stop ---
    col_btn1, col_btn2, col_status = st.columns([1, 1, 4])

    with col_btn1:
        start = st.button(
            "▶ Start Collection",
            type="primary",
            disabled=st.session_state.running or not selected_platforms,
        )

    with col_btn2:
        stop = st.button(
            "⏹ Stop",
            disabled=not st.session_state.running,
        )

    with col_status:
        if st.session_state.running:
            _done  = st.session_state.get("tasks_done", 0)
            _total = st.session_state.get("total_tasks", 1)
            _pct   = min(_done / _total, 1.0) if _total > 0 else 0.0
            _elapsed = time.time() - (st.session_state.get("start_time") or time.time())
            if _pct > 0.01:
                _eta = (_elapsed / _pct) * (1 - _pct)
                _h, _rem = divmod(int(_eta), 3600)
                _m, _s   = divmod(_rem, 60)
                _eta_str = (f"~{_h}h {_m}m" if _h else f"~{_m}m {_s}s") + " remaining"
            else:
                _eta_str = "estimating…"
            _plat = st.session_state.get("current_platform", "")
            _kw   = st.session_state.get("current_keyword", "")
            _label = f"⏳ {int(_pct * 100)}%  ·  {_done}/{_total} tasks  ·  {_eta_str}"
            if _plat:
                _label += f"  ·  {_plat}"
            if _kw:
                _label += f"  →  {_kw[:50]}"
            st.progress(_pct, text=_label)
        elif st.session_state.run_done:
            st.success("✅ Collection completed.")

    # --- Handle start ---
    if start and not st.session_state.running and selected_platforms:
        cmd = [sys.executable, str(PROJECT_ROOT / "main.py")]
        cmd += ["--platforms"] + selected_platforms
        cmd += ["--pages", str(pages)]
        if selected_keywords:
            cmd += ["--keywords"] + selected_keywords
        if not headless:
            cmd += ["--no-headless"]

        # Compute total tasks for progress tracking:
        # dealers use their own site list; other platforms use keyword list.
        try:
            from scrapers.dealers import DEALER_CONFIGS as _DC
            _n_dealers = len(_DC)
        except Exception:
            _n_dealers = 13
        _n_kw = len(selected_keywords) if selected_keywords else sum(len(v) for v in kw_by_cat.values())
        _total = sum(_n_dealers if p == "dealers" else _n_kw for p in selected_platforms)

        st.session_state.process          = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            bufsize=1,
        )
        st.session_state.running          = True
        st.session_state.run_done         = False
        st.session_state.log              = ""
        st.session_state.tasks_done       = 0
        st.session_state.total_tasks      = max(_total, 1)
        st.session_state.start_time       = time.time()
        st.session_state.current_platform = ""
        st.session_state.current_keyword  = ""
        st.rerun()

    # --- Handle stop ---
    if stop and st.session_state.process:
        st.session_state.process.terminate()
        st.session_state.running  = False
        st.session_state.run_done = False
        st.session_state.log     += "\n[Stopped by user]"
        st.rerun()

    # --- Live log ---
    st.subheader("Log")
    log_box = st.empty()

    if st.session_state.running and st.session_state.process:
        proc = st.session_state.process
        # Read up to 50 lines per rerun cycle and parse progress markers
        new_lines = []
        for _ in range(50):
            line = proc.stdout.readline()
            if not line:
                break
            new_lines.append(line)
            st.session_state.log += line

        for line in new_lines:
            if "Iniciando scraper:" in line:
                m = re.search(r"Iniciando scraper:\s*(.+)$", line.strip())
                if m:
                    st.session_state.current_platform = m.group(1).strip()
            elif "Iniciando keyword:" in line:
                st.session_state.tasks_done += 1
                m = re.search(r"Iniciando keyword:\s*'([^']+)'", line)
                if m:
                    st.session_state.current_keyword = m.group(1)
            elif "Coleta finalizada!" in line:
                st.session_state.tasks_done = st.session_state.total_tasks

        if proc.poll() is not None:
            remaining = proc.stdout.read()
            if remaining:
                st.session_state.log += remaining
            st.session_state.running          = False
            st.session_state.run_done         = True
            st.session_state.process          = None
            st.session_state.tasks_done       = st.session_state.total_tasks
            st.session_state.current_platform = ""
            st.session_state.current_keyword  = ""
        else:
            time.sleep(0.3)
            st.rerun()

    log_box.code(
        st.session_state.log[-4000:] if st.session_state.log else "No output yet.",
        language="bash",
    )


# ---------------------------------------------------------------------------
# Page 2 — Results
# ---------------------------------------------------------------------------

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
            "seller", "keyword", "tag",
        ] if c in df.columns
    ]

    st.dataframe(
        _style_midea_df(df[display_cols]),
        use_container_width=True,
        height=520,
        column_config={
            "data":            st.column_config.DateColumn("Date"),
            "preco":           st.column_config.NumberColumn("Price (R$)", format="R$ %.2f"),
            "posicao_geral":   st.column_config.NumberColumn("Position"),
            "posicao_organica":st.column_config.NumberColumn("Organic Pos."),
        },
    )

    # --- Download ---
    csv_bytes = df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label="⬇️ Download CSV",
        data=csv_bytes,
        file_name=f"rac_{start_date}_{end_date}.csv",
        mime="text/csv",
    )


# ---------------------------------------------------------------------------
# Page 3 — Price Evolution
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Page 4 — Import History
# ---------------------------------------------------------------------------

def page_import_history():
    st.title("📂 Import History")
    st.caption("Upload historical CSV files to Supabase. Duplicates are ignored automatically.")

    OUTPUT_DIR = PROJECT_ROOT / "output"

    tab_folder, tab_upload = st.tabs(["From output/ folder", "Upload files"])

    # --- Tab 1: scan output/ folder ---
    with tab_folder:
        st.markdown("Scans the `output/` folder and imports all `rac_monitoramento_*.csv` files.")

        if not OUTPUT_DIR.exists():
            st.warning(f"Folder `output/` not found at `{OUTPUT_DIR}`.")
        else:
            csv_files = sorted(OUTPUT_DIR.glob("rac_monitoramento_*.csv"), reverse=True)

            if not csv_files:
                st.info("No CSV files found in `output/`.")
            else:
                # Preview table
                preview = []
                for f in csv_files:
                    try:
                        df_preview = pd.read_csv(f, sep=";", encoding="utf-8-sig", nrows=1)
                        rows = sum(1 for _ in open(f, encoding="utf-8-sig")) - 1
                    except Exception:
                        rows = "?"
                    preview.append({"File": f.name, "Rows": rows})

                st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)
                st.caption(f"Total: {len(csv_files)} files")

                if st.button("⬆️ Import all to Supabase", type="primary"):
                    from utils.supabase_client import upload_to_supabase
                    progress = st.progress(0, text="Starting...")
                    log_area = st.empty()
                    log_lines = []
                    total_ok = 0

                    for i, f in enumerate(csv_files):
                        progress.progress((i + 1) / len(csv_files), text=f"Importing {f.name}...")
                        try:
                            df_csv = pd.read_csv(f, sep=";", encoding="utf-8-sig", dtype=str)
                            df_csv = df_csv.dropna(how="all")
                            records = df_csv.where(pd.notna(df_csv), None).to_dict("records")
                            ok = upload_to_supabase(records)
                            status = "✓" if ok else "✗"
                            total_ok += len(records) if ok else 0
                        except Exception as e:
                            status = f"✗ {e}"
                        log_lines.append(f"{status}  {f.name}  ({len(records)} rows)")
                        log_area.code("\n".join(log_lines), language="bash")

                    progress.empty()
                    st.success(f"Done. {total_ok:,} records sent. Duplicates were ignored.")

    # --- Tab 2: upload files ---
    with tab_upload:
        st.markdown("Upload one or more CSV files directly from your computer.")
        uploaded = st.file_uploader(
            "Select CSV files",
            type=["csv"],
            accept_multiple_files=True,
            help="Must be rac_monitoramento_*.csv format (semicolon-separated, UTF-8 BOM)",
        )

        if uploaded:
            total_rows = 0
            all_records = []
            for f in uploaded:
                try:
                    df_up = pd.read_csv(f, sep=";", encoding="utf-8-sig", dtype=str)
                    df_up = df_up.dropna(how="all")
                    records = df_up.where(pd.notna(df_up), None).to_dict("records")
                    all_records.extend(records)
                    total_rows += len(records)
                    st.write(f"✓ `{f.name}` — {len(records)} rows")
                except Exception as e:
                    st.error(f"✗ `{f.name}`: {e}")

            if all_records:
                st.info(f"Ready to import **{total_rows:,}** records total.")
                if st.button("⬆️ Send to Supabase", type="primary"):
                    from utils.supabase_client import upload_to_supabase
                    with st.spinner(f"Uploading {total_rows:,} records..."):
                        ok = upload_to_supabase(all_records)
                    if ok:
                        st.success(f"✅ {total_rows:,} records imported. Duplicates ignored.")
                    else:
                        st.error("Upload failed. Check Supabase connection.")


# ---------------------------------------------------------------------------
# Page 5 — Data Cleanup
# ---------------------------------------------------------------------------

def page_data_cleanup():
    st.title("🧹 Data Cleanup")
    st.caption(
        "Scans Supabase for records that are not air-conditioner products "
        "(e.g. iPhones, diapers, notebooks) and removes them."
    )

    st.info(
        "**How it works:** Each record's product name is checked against a list of "
        "strong AC terms (BTU, ar condicionado, evaporadora…), weak terms (split, inverter), "
        "and a blocklist of known non-AC products. Records that fail the check are flagged for deletion."
    )

    col1, col2 = st.columns(2)

    # --- Scan ---
    with col1:
        scan_btn = st.button("🔍 Scan for invalid records", use_container_width=True)

    with col2:
        delete_btn = st.button(
            "🗑️ Delete invalid records",
            type="primary",
            use_container_width=True,
            help="Permanently removes all records that don't pass the AC product filter.",
        )

    if scan_btn:
        with st.spinner("Scanning Supabase… this may take a moment for large datasets."):
            from utils.supabase_client import delete_invalid_from_supabase
            result = delete_invalid_from_supabase(dry_run=True)

        st.session_state["cleanup_scan"] = result

    if "cleanup_scan" in st.session_state:
        r = st.session_state["cleanup_scan"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Records scanned", f"{r['scanned']:,}")
        c2.metric("Invalid (non-AC)", f"{r['invalid']:,}", delta=f"-{r['invalid']:,}" if r["invalid"] else None, delta_color="inverse")
        c3.metric("Valid", f"{r['scanned'] - r['invalid']:,}")

        if r["invalid"] == 0:
            st.success("✅ No invalid records found. Your dataset is clean!")
        else:
            pct = r["invalid"] / r["scanned"] * 100 if r["scanned"] else 0
            st.warning(
                f"Found **{r['invalid']:,}** records ({pct:.1f}%) that appear unrelated "
                f"to air-conditioners. Click **Delete invalid records** to remove them."
            )

    if delete_btn:
        if "cleanup_scan" not in st.session_state or st.session_state["cleanup_scan"]["invalid"] == 0:
            st.warning("Run a scan first to confirm there are invalid records.")
        else:
            with st.spinner("Deleting invalid records…"):
                from utils.supabase_client import delete_invalid_from_supabase
                result = delete_invalid_from_supabase(dry_run=False)

            if result["errors"] == 0:
                st.success(
                    f"✅ Done. **{result['deleted']:,}** invalid records deleted. "
                    f"Your dataset now contains only AC-related products."
                )
            else:
                st.warning(
                    f"Partial cleanup: {result['deleted']:,} deleted, "
                    f"{result['errors']:,} with errors. Check Supabase logs."
                )
            # Clear cached scan result
            del st.session_state["cleanup_scan"]

    st.divider()
    # ── Price Validation ─────────────────────────────────────────────────────
    st.subheader("💰 Price Validation")
    st.caption(
        "Identifies records where the price significantly exceeds the reasonable ceiling "
        "for the detected BTU capacity — likely caused by historical parsing errors (×10 bug). "
        "E.g., a 9.000 BTU AC priced at R$ 18.990 instead of ~R$ 1.899."
    )

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        price_scan_btn = st.button(
            "🔍 Scan for bad prices",
            use_container_width=True,
            key="price_scan_btn",
        )
    with col_p2:
        price_delete_btn = st.button(
            "🗑️ Delete records with bad prices",
            type="primary",
            use_container_width=True,
            key="price_delete_btn",
            help="Permanently removes records where price exceeds the BTU-based ceiling.",
        )

    if price_scan_btn:
        with st.spinner("Scanning for suspicious prices… this may take a moment."):
            from utils.supabase_client import scan_fix_bad_prices_in_supabase
            price_result = scan_fix_bad_prices_in_supabase(dry_run=True)
        st.session_state["price_scan"] = price_result

    if "price_scan" in st.session_state:
        pr = st.session_state["price_scan"]
        pc1, pc2 = st.columns(2)
        pc1.metric("Records scanned", f"{pr['scanned']:,}")
        pc2.metric(
            "Suspicious prices",
            f"{pr['suspicious']:,}",
            delta=f"-{pr['suspicious']:,}" if pr["suspicious"] else None,
            delta_color="inverse",
        )

        if pr["suspicious"] == 0:
            st.success("✅ No price anomalies found!")
        else:
            pct = pr["suspicious"] / pr["scanned"] * 100 if pr["scanned"] else 0
            st.warning(
                f"Found **{pr['suspicious']:,}** records ({pct:.1f}%) with suspiciously high "
                "prices. These are likely ×10 parsing errors. "
                "Click **Delete records with bad prices** to remove them."
            )
            if pr.get("examples"):
                with st.expander(f"Examples ({len(pr['examples'])} shown)", expanded=True):
                    st.dataframe(pr["examples"], use_container_width=True, hide_index=True)

    if price_delete_btn:
        scan = st.session_state.get("price_scan")
        if not scan or scan["suspicious"] == 0:
            st.warning("Run a price scan first to confirm there are records to remove.")
        else:
            with st.spinner(f"Deleting {scan['suspicious']:,} records with bad prices…"):
                from utils.supabase_client import scan_fix_bad_prices_in_supabase
                price_result = scan_fix_bad_prices_in_supabase(dry_run=False)
            if price_result["errors"] == 0:
                st.success(
                    f"✅ Done. **{price_result['deleted']:,}** records with bad prices deleted."
                )
            else:
                st.warning(
                    f"Partial cleanup: {price_result['deleted']:,} deleted, "
                    f"{price_result['errors']:,} with errors. Check Supabase logs."
                )
            if "price_scan" in st.session_state:
                del st.session_state["price_scan"]

    st.divider()
    st.markdown(
        "**Price ceilings by BTU capacity** *(prices above these are flagged)*\n\n"
        "| Capacity | Max reasonable |\n|---|---|\n"
        "| 7.000 BTUs | R$ 4.500 |\n"
        "| 9.000 BTUs | R$ 5.500 |\n"
        "| 12.000 BTUs | R$ 7.000 |\n"
        "| 18.000 BTUs | R$ 12.000 |\n"
        "| 24.000 BTUs | R$ 16.000 |\n"
        "| 36.000 BTUs | R$ 28.000 |\n"
        "| 48.000 BTUs | R$ 40.000 |\n"
        "| 60.000 BTUs | R$ 55.000 |\n"
    )

    st.divider()
    st.subheader("Filter rules reference")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown("**✅ Strong AC terms** *(any one = keep)*")
        st.code(
            "ar condicionado\nBTU / BTUs\nevaporadora\ncondensadora\nhi-wall\nmini-split\ncassete",
            language=None,
        )
    with col_b:
        st.markdown("**🟡 Weak AC terms** *(need 2+ = keep)*")
        st.code("split\ninverter", language=None)
    with col_c:
        st.markdown("**🚫 Blocklist** *(any one = remove)*")
        st.code(
            "iphone / ipad\nnotebook / laptop\ncelular / smartphone\nfralda\ngeladeira / refrigerador\nfogão / microondas\ntablet / airpods / macbook\ncolchão / sofá",
            language=None,
        )


# ---------------------------------------------------------------------------
# Page 6 — Normalize SKUs
# ---------------------------------------------------------------------------

def page_normalize_skus():
    st.title("🔤 Normalize SKUs")
    st.caption(
        "Re-applies the RAC normalization rules to every `produto` field stored in Supabase. "
        "Only rows whose name actually changes are written back. "
        "Records without a recognized brand or BTU value are left untouched."
    )

    st.info(
        "**Format:** `Ar Condicionado {Marca} {Linha} {BTUs} {Tipo} {Ciclo} [{Forma}] [{Cor}]`\n\n"
        "Run a **Scan** first to preview which records would change, then **Apply** to write the updates."
    )

    col1, col2 = st.columns(2)

    with col1:
        scan_btn = st.button("🔍 Scan for outdated names", use_container_width=True)
    with col2:
        apply_btn = st.button(
            "✏️ Apply normalization",
            type="primary",
            use_container_width=True,
            help="Updates only rows whose normalized name differs from the stored value.",
        )

    # ── Scan (dry-run) ──
    if scan_btn:
        with st.spinner("Scanning Supabase… this may take a moment for large datasets."):
            from utils.supabase_client import normalize_all_products_in_supabase
            result = normalize_all_products_in_supabase(dry_run=True, preview_limit=30)
        st.session_state["norm_scan"] = result

    if "norm_scan" in st.session_state:
        r = st.session_state["norm_scan"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Records scanned",     f"{r['scanned']:,}")
        c2.metric("Need update",         f"{r['changed']:,}",
                  delta=f"-{r['changed']:,}" if r["changed"] else None,
                  delta_color="inverse")
        c3.metric("Already normalized",  f"{r['unchanged']:,}")

        if r["changed"] == 0:
            st.success("✅ All product names are already normalized. Nothing to do.")
        else:
            pct = r["changed"] / r["scanned"] * 100 if r["scanned"] else 0
            st.warning(
                f"**{r['changed']:,}** records ({pct:.1f}%) have outdated names. "
                "Click **Apply normalization** to update them."
            )
            if r.get("preview"):
                with st.expander(f"Preview of changes ({len(r['preview'])} examples)", expanded=True):
                    rows = [
                        {"ID": ex["id"], "Before": ex["before"], "After": ex["after"]}
                        for ex in r["preview"]
                    ]
                    st.dataframe(rows, use_container_width=True)

    # ── Apply ──
    if apply_btn:
        scan = st.session_state.get("norm_scan")
        if not scan or scan["changed"] == 0:
            st.warning("Run a scan first and confirm there are records to update.")
        else:
            with st.spinner(f"Updating {scan['changed']:,} records…"):
                from utils.supabase_client import normalize_all_products_in_supabase
                result = normalize_all_products_in_supabase(dry_run=False)

            upd  = result["updated"]
            ded  = result.get("deduped", 0)
            errs = result["errors"]
            if errs == 0:
                st.success(
                    f"✅ Done. **{upd:,}** records renamed, "
                    f"**{ded:,}** duplicate old-name records removed."
                )
            else:
                st.warning(
                    f"Partial run: {upd:,} renamed, {ded:,} duplicates removed, "
                    f"{errs:,} with errors. Check Supabase logs."
                )
            if "norm_scan" in st.session_state:
                del st.session_state["norm_scan"]

    st.divider()

    # ── Brand Normalization ───────────────────────────────────────────────────
    st.subheader("🏷️ Brand Normalization")
    st.caption(
        "Unifies brand variants stored in the `marca` column. "
        '"Springer Midea", "Midea Carrier", and "Springer" are all Midea products — '
        "this consolidates them under a single `Midea` entry for cleaner analysis."
    )
    st.info(
        "| Variant in DB | → Canonical |\n|---|---|\n"
        "| Springer Midea | **Midea** |\n"
        "| Midea Carrier | **Midea** |\n"
        "| Springer | **Midea** |\n"
        "| Britania | **Britânia** |"
    )

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        brand_scan_btn = st.button(
            "🔍 Scan brand variants",
            use_container_width=True,
            key="brand_scan_btn",
        )
    with col_b2:
        brand_apply_btn = st.button(
            "✏️ Apply brand normalization",
            type="primary",
            use_container_width=True,
            key="brand_apply_btn",
            help="Updates marca for all variant rows to the canonical name.",
        )

    if brand_scan_btn:
        with st.spinner("Scanning brand variants…"):
            from utils.supabase_client import normalize_brands_in_supabase
            brand_result = normalize_brands_in_supabase(dry_run=True)
        st.session_state["brand_scan"] = brand_result

    if "brand_scan" in st.session_state:
        br = st.session_state["brand_scan"]
        total_variants = sum(
            v["count"] for v in br["by_brand"].values() if v["count"] > 0
        )
        rows = [
            {
                "Variant (DB)": src,
                "→ Canonical": info["target"],
                "Records": info["count"] if info["count"] >= 0 else "error",
            }
            for src, info in br["by_brand"].items()
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        if total_variants == 0:
            st.success("✅ All brand names are already normalized!")
        else:
            st.warning(
                f"Found **{total_variants:,}** records with non-canonical brand names. "
                "Click **Apply brand normalization** to consolidate them."
            )

    if brand_apply_btn:
        scan = st.session_state.get("brand_scan")
        total_to_fix = (
            sum(v["count"] for v in scan["by_brand"].values() if v["count"] > 0)
            if scan else 0
        )
        if not scan or total_to_fix == 0:
            st.warning("Run a scan first to confirm there are records to update.")
        else:
            with st.spinner(f"Normalizing {total_to_fix:,} brand records…"):
                from utils.supabase_client import normalize_brands_in_supabase
                brand_result = normalize_brands_in_supabase(dry_run=False)
            if brand_result["errors"] == 0:
                st.success(
                    f"✅ Done. **{brand_result['total_updated']:,}** records updated."
                )
            else:
                st.warning(
                    f"Partial run: {brand_result['total_updated']:,} updated, "
                    f"{brand_result['errors']:,} with errors."
                )
            if "brand_scan" in st.session_state:
                del st.session_state["brand_scan"]
            # Clear cached filter options so the brand dropdown refreshes
            get_filter_options.clear()

    st.divider()

    # ── Platform / Seller Normalization ──────────────────────────────────────
    st.subheader("🏪 Platform / Seller Normalization")
    st.caption(
        "Corrige typos e capitalização nos campos `plataforma` e `seller`. "
        "Aplica em ambas as colunas simultaneamente."
    )
    st.info(
        "| Variant in DB | → Canonical |\n|---|---|\n"
        "| FerreiraCoasta | **FerreiraCosta** |\n"
        "| Webcontinental | **WebContinental** |"
    )

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        plat_scan_btn = st.button(
            "🔍 Scan platform/seller variants",
            use_container_width=True,
            key="plat_scan_btn",
        )
    with col_p2:
        plat_apply_btn = st.button(
            "✏️ Apply platform/seller normalization",
            type="primary",
            use_container_width=True,
            key="plat_apply_btn",
            help="Updates plataforma and seller columns for all variant rows.",
        )

    if plat_scan_btn:
        with st.spinner("Scanning platform/seller variants…"):
            from utils.supabase_client import normalize_platforms_sellers_in_supabase
            plat_result = normalize_platforms_sellers_in_supabase(dry_run=True)
        st.session_state["plat_scan"] = plat_result

    if "plat_scan" in st.session_state:
        pr = st.session_state["plat_scan"]
        total_variants = sum(
            (v.get("plataforma") or 0) + (v.get("seller") or 0)
            for v in pr["by_mapping"].values()
            if isinstance(v.get("plataforma"), int) and isinstance(v.get("seller"), int)
        )
        rows = [
            {
                "Variant (DB)": src,
                "→ Canonical":   info["target"],
                "plataforma":    info.get("plataforma", "?"),
                "seller":        info.get("seller", "?"),
            }
            for src, info in pr["by_mapping"].items()
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        if total_variants == 0:
            st.success("✅ All platform/seller names are already correct!")
        else:
            st.warning(
                f"Found **{total_variants:,}** records with non-canonical "
                "platform/seller names. Click **Apply** to fix them."
            )

    if plat_apply_btn:
        scan = st.session_state.get("plat_scan")
        total_to_fix = 0
        if scan:
            for v in scan["by_mapping"].values():
                p = v.get("plataforma") or 0
                s = v.get("seller") or 0
                if isinstance(p, int):
                    total_to_fix += p
                if isinstance(s, int):
                    total_to_fix += s
        if not scan or total_to_fix == 0:
            st.warning("Run a scan first to confirm there are records to update.")
        else:
            with st.spinner(f"Normalizing {total_to_fix:,} records…"):
                from utils.supabase_client import normalize_platforms_sellers_in_supabase
                plat_result = normalize_platforms_sellers_in_supabase(dry_run=False)
            if plat_result["errors"] == 0:
                st.success(
                    f"✅ Done. **{plat_result['total_updated']:,}** records updated."
                )
            else:
                st.warning(
                    f"Partial run: {plat_result['total_updated']:,} updated, "
                    f"{plat_result['errors']:,} with errors."
                )
            if "plat_scan" in st.session_state:
                del st.session_state["plat_scan"]
            get_filter_options.clear()

    st.divider()
    st.subheader("Normalization rules")
    st.markdown(
        "| Component | Rule |\n"
        "|-----------|------|\n"
        "| **Marca** | Aliases unified (Springer Midea → Midea, TCL Semp → TCL, …) |\n"
        "| **Linha** | Preserved exactly per brand — each model line stays distinct for phase-out tracking |\n"
        "| **BTUs** | Brazilian format: 12.000 BTUs, 9.000 BTUs, … |\n"
        "| **Tipo** | `Inverter` (default) or `On/Off` |\n"
        "| **Ciclo** | `Frio` (default) or `Quente/Frio` |\n"
        "| **Forma** | Omitted when Hi-Wall (default); shown for Janela, Cassete, Piso-Teto… |\n"
        "| **Cor** | Omitted when white (default); shown for Preto, etc. |\n"
        "| **Fallback** | Name unchanged when brand or BTU cannot be identified |\n"
    )


# ---------------------------------------------------------------------------
# Page 7 — BuyBox Position
# ---------------------------------------------------------------------------

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






# ---------------------------------------------------------------------------
# Page 8 — Availability
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Competitive Intelligence — system prompt + page
# ---------------------------------------------------------------------------

_CI_SYSTEM_PROMPT = """Você é um analista de inteligência de mercado sênior especializado em e-commerce brasileiro e análise competitiva no setor de climatização (ar condicionado residencial - RAC).

Você está analisando dados de monitoramento de posicionamento coletados automaticamente em marketplaces brasileiros para a Midea Carrier Brasil (MCJV).

CONTEXTO DA EMPRESA:
- Midea Carrier (MCJV) tem ~14% de market share online (target: 15-17%)
- Marcas do grupo: "Midea", "Springer Midea", "Springer" (todas = MCJV)
- Concorrentes diretos: LG, Elgin, Samsung, TCL, Philco, Gree, Electrolux, Agratto
- Concorrentes emergentes: Aufit, Hisense, HQ, Britânia
- Linha de produtos 2026: AI Ecomaster (hero, EF7.6), AI Airvolution (EF7.0), Airvolution Lite (EF4.6, entry), AI Ecomaster Black Edition (lançamento abril/2026), Save Connect (legacy)
- Benchmark de preço: Lite 100%, Airvolution 105%, Ecomaster 113%
- Segmentação BTU: 9K (~34-38%), 12K (~46-50% = dominante), 18K (~9-10%), 24K (~6%)
- Sellers-chave no ML: WebContinental, ClimaRio, Dufrio, Centralar.com, Leveros, Midea Store, Engage Eletro, Bwinx, Bagatoli, Friopeças, Comprebel
- Plataformas com ads (patrocinado): Amazon (AMS) e Magalu (HEROs). ML é 100% orgânico.
- ROAS benchmarks: DCA (ML) ≥35x, AMS (Amazon) ≥8x

ESTRUTURA DO RELATÓRIO (seguir exatamente esta estrutura):

# Relatório de Inteligência Competitiva — Monitoramento de Posicionamento RAC HW
## [Data(s)] | Coletas: [horários]

## Sumário Executivo
- 5 pontos numerados com os achados mais críticos, cada um com dados quantitativos específicos
- Linguagem direta, sem hedge words

## 1. Panorama de Marcas
### 1.1 Share of Shelf (Volume de Aparições)
- Tabela: Marca | Manhã | % | Noite | % | Delta | Plataformas
- Incluir MCJV consolidado (Midea + Springer Midea)

### 1.2 Investimento em Patrocinado (Ads)
- Tabela: Marca | Spon AM | % Total | Spon PM | % Total | Principal Canal
- Identificar quem está investindo mais em cada plataforma

### 1.3 Tags de Destaque (ML)
- Tabela comparativa: MAIS VENDIDO, OFERTA IMPERDÍVEL, RECOMENDADO, Escolha
- Posição de Midea em cada tag

## 2. Análise por Canal/Plataforma
### 2.1 Volume e Competição
- Tabela por plataforma: Total, Marcas, Sponsored, Avg/Min/Max preço

### 2.2 Midea por Plataforma — Posição e Top-10
- Tabela: Plataforma | Total AM | T10 AM | T10% AM | Total PM | T10 PM | T10% PM | Trend

### 2.3 Taxa Top-10 Midea vs Concorrentes
- Tabela separada para ML, Amazon, Magalu
- Calcular T10% = (posições top-10 / total aparições) x 100

## 3. Sellers do Mercado Livre
### 3.1 Ranking de Sellers por Volume
- Top 15-20 sellers: Volume, Top10, Avg preço, Perfil resumido

### 3.2 Composição de Marca por Seller
- Para os top 5 sellers: breakdown das marcas que vendem e volumes

### 3.3 Estratégia de Preço por Seller (foco Midea)
- Tabela: Seller | Midea Min 9K | Midea Min 12K | Postura

### 3.4 Midea Store Performance
- Listings, posições, keywords, portfólio disponível

## 4. Insights de Preço
### 4.1 Mapa de Preço por Marca x BTU
- Tabelas para 9K, 12K, 18K, 24K com Min, Med, Avg por marca

### 4.2 Gap de Preço Midea vs Mercado (ML)
- Tabela: BTU | Midea Min | Concorrente mais barato | Gap %
- Separar outliers de preço (parcela, erro) dos preços reais

### 4.3 Movimentações Intra-dia
- Identificar as maiores variações de preço entre AM e PM
- Destacar variações Midea especificamente
- Flagrar anomalias (variações >30%)

## 5. Análise de Produto Midea
### 5.1 Performance por Linha de Produto
- Tabela: Linha | Listings | Top10 | T10% | Avg R$ | Min R$
- Classificar: AI Ecomaster, AI Airvolution, Airvolution Lite, AI Ecomaster Black, Save Connect, Xtreme Save, Other

### 5.2 Keywords Genéricas — Midea Performance ML
- Tabela: Keyword | Best Pos AM | Best Pos PM | T10 AM | T10 PM | Trend
- Focar nas keywords genéricas de alto tráfego

## 6. Recomendações Estratégicas
### 6.1 Ações Imediatas (próximas 48h)
- 2-3 ações com justificativa quantitativa

### 6.2 Ações de Semana
- 2-3 ações táticas

### 6.3 Ações Estruturais
- 2-3 recomendações de médio prazo

REGRAS DE ANÁLISE:
1. SEMPRE usar números específicos. Nunca dizer "significativo" ou "considerável" sem quantificar.
2. Calcular T10% como: (contagem de registros com Posição Geral ≤ 10) / total de registros da marca naquela plataforma × 100
3. Para classificar como "sponsored/patrocinado": o campo "Posição Patrocinada" está preenchido (não vazio, não "nan", não "None")
4. Marcas MCJV = "Midea" + "Springer Midea" + "Springer" (sempre consolidar quando relevante)
5. Ignorar preços < R$100 (provavelmente parcela ou erro de scraping) para análises de preço real
6. Para extrair BTU do nome do produto, procurar: "9000", "9k", "9.000", "12000", "12k", "12.000", "18000", "18k", "18.000", "24000", "24k", "24.000", "30000", "30k"
7. Para classificar linhas de produto Midea: verificar no nome se contém "ecomaster" (+ "pro"/"8.2" para Pro, + "black"/"pret" para Black), "airvolution" (+ "lite" para Lite), "save connect"/"saveconnect", "xtreme save"
8. Tags de destaque relevantes: "MAIS VENDIDO", "OFERTA IMPERDÍVEL", "RECOMENDADO", "Escolha", "Oferta", "Menor preço em 365 dias"
9. Se houver dados de mais de um dia, incluir análise D/D (dia a dia) com tendências
10. Manter linguagem em PORTUGUÊS BRASILEIRO, usar termos do mercado em inglês quando padrão (sell-in, sell-out, Buy Box, ROAS, etc.)

OUTPUT: Relatório completo em Markdown. Sem blocos de código. Sem introduções genéricas. Começar direto com o título do relatório."""

# Columns to send to Claude (Supabase name → display name)
_CI_COL_MAP = {
    "data":               "Data",
    "turno":              "Turno",
    "plataforma":         "Plataforma",
    "keyword":            "Keyword Buscada",
    "marca":              "Marca Monitorada",
    "produto":            "Produto / SKU",
    "posicao_organica":   "Posição Orgânica",
    "posicao_patrocinada":"Posição Patrocinada",
    "posicao_geral":      "Posição Geral",
    "preco":              "Preço (R$)",
    "seller":             "Seller / Vendedor",
    "tag":                "Tag Destaque",
}
_CI_MAX_RAW = 20_000  # Above this, send pre-aggregated tables instead of raw CSV


def _ci_build_payload(df: pd.DataFrame) -> str:
    """Return the data portion of the Claude user message (raw CSV or aggregated tables)."""
    # Rename to display names for readability
    col_map = {k: v for k, v in _CI_COL_MAP.items() if k in df.columns}
    export = df[list(col_map.keys())].rename(columns=col_map)

    if len(df) <= _CI_MAX_RAW:
        return export.to_csv(sep=";", index=False)

    # Pre-aggregate when dataset is too large to send raw
    sections: list[str] = ["DADOS PRÉ-PROCESSADOS (volume elevado — tabelas agregadas):\n"]

    # 1. Brand × platform × shift summary
    brand_agg = df.groupby(
        ["data", "turno", "plataforma", "marca"], dropna=False
    ).agg(
        total=("posicao_geral", "count"),
        top10=("posicao_geral", lambda x: (x <= 10).sum()),
        top5=("posicao_geral", lambda x: (x <= 5).sum()),
        sponsored=("posicao_patrocinada", lambda x: x.notna().sum()),
        avg_price=("preco", "mean"),
        min_price=("preco", "min"),
    ).reset_index()
    sections.append("=== BRAND SUMMARY ===\n" + brand_agg.to_csv(sep=";", index=False))

    # 2. Seller analysis (ML only)
    ml_df = df[df["plataforma"].str.contains("Mercado Livre|ML", case=False, na=False)]
    if not ml_df.empty:
        seller_agg = ml_df.groupby(
            ["data", "turno", "seller", "marca"], dropna=False
        ).agg(
            count=("posicao_geral", "count"),
            top10=("posicao_geral", lambda x: (x <= 10).sum()),
            avg_price=("preco", "mean"),
            min_price=("preco", "min"),
        ).reset_index()
        sections.append("=== SELLER SUMMARY (ML) ===\n" + seller_agg.to_csv(sep=";", index=False))

        # 3. Keyword × brand performance (ML)
        kw_agg = ml_df.groupby(
            ["data", "turno", "keyword", "marca"], dropna=False
        ).agg(
            count=("posicao_geral", "count"),
            best_pos=("posicao_geral", "min"),
            top10=("posicao_geral", lambda x: (x <= 10).sum()),
            min_price=("preco", "min"),
        ).reset_index()
        sections.append("=== KEYWORD PERFORMANCE (ML) ===\n" + kw_agg.to_csv(sep=";", index=False))

    # 4. Tags distribution
    tag_df = df[df["tag"].notna() & (df["tag"] != "")]
    if not tag_df.empty:
        tag_agg = tag_df.groupby(
            ["data", "turno", "tag", "marca"], dropna=False
        ).size().reset_index(name="count")
        sections.append("=== TAGS DISTRIBUTION ===\n" + tag_agg.to_csv(sep=";", index=False))

    # 5. Price by BTU (sample of raw rows with price data, ML only)
    price_df = df[df["preco"].notna() & (df["preco"] >= 100)]
    if not price_df.empty:
        price_sample = (
            price_df[["data", "turno", "plataforma", "marca", "produto", "preco", "seller"]]
            .sort_values("preco")
            .head(5000)
        )
        sections.append("=== PRICE SAMPLE (up to 5000 rows) ===\n" + price_sample.to_csv(sep=";", index=False))

    return "\n\n".join(sections)


def page_ci_analysis() -> None:
    st.title("🧠 Competitive Intelligence")
    st.caption("Análise competitiva gerada por IA com base nos dados de monitoramento de posicionamento.")

    # ── Sidebar ────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.subheader("Filtros")

        date_range = st.date_input(
            "Período de Análise",
            value=(date.today() - timedelta(days=1), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="ci_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=1)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()

        turno_filter = st.multiselect(
            "Turno",
            ["Abertura", "Fechamento"],
            default=["Abertura", "Fechamento"],
            key="ci_turno",
        )

        sel_platforms = st.multiselect(
            "Plataformas",
            opts.get("platforms", []),
            key="ci_platforms",
        )

        sel_brands = st.multiselect(
            "Marcas de Foco",
            opts.get("brands", []),
            help="Vazio = todas as marcas",
            key="ci_brands",
        )

        sel_keywords = st.multiselect(
            "Keywords",
            opts.get("keywords", []),
            help="Vazio = todas as keywords",
            key="ci_keywords",
        )

        compare_mode = st.radio(
            "Modo de Análise",
            [
                "Dia Completo (AM vs PM)",
                "Comparativo Diário (D/D)",
                "Período Consolidado",
            ],
            key="ci_mode",
        )

        load_btn = st.button("🔍 Carregar Dados", type="primary", use_container_width=True)

    # ── Session-state keys ─────────────────────────────────────────────────────
    if "ci_df" not in st.session_state:
        st.session_state["ci_df"] = None
    if "ci_report" not in st.session_state:
        st.session_state["ci_report"] = None

    # ── Load data ──────────────────────────────────────────────────────────────
    if load_btn:
        st.session_state["ci_report"] = None  # reset previous report
        with st.spinner("Carregando dados do Supabase…"):
            df = query_coletas(
                start_date,
                end_date,
                platforms=sel_platforms or None,
                brands=sel_brands or None,
                keywords=sel_keywords or None,
                limit=100_000,
            )
        # Filter by turno (not a direct Supabase filter — apply in-memory)
        if turno_filter and not df.empty and "turno" in df.columns:
            df = df[df["turno"].isin(turno_filter)]

        st.session_state["ci_df"] = df if not df.empty else None

    df: pd.DataFrame | None = st.session_state.get("ci_df")

    if df is None:
        st.info("Configure os filtros na barra lateral e clique em **Carregar Dados**.")
        return

    if df.empty:
        st.warning("Nenhum registro encontrado para o período e filtros selecionados.")
        return

    # ── Summary metrics ────────────────────────────────────────────────────────
    n_plat    = df["plataforma"].nunique() if "plataforma" in df.columns else 0
    n_kw      = df["keyword"].nunique()    if "keyword"    in df.columns else 0
    n_brands  = df["marca"].nunique()      if "marca"      in df.columns else 0
    n_days    = df["data"].nunique()       if "data"       in df.columns else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Registros", f"{len(df):,}")
    c2.metric("Plataformas", n_plat)
    c3.metric("Keywords", n_kw)
    c4.metric("Marcas", n_brands)
    c5.metric("Dias", n_days)

    with st.expander("Pré-visualização dos dados (primeiras 100 linhas)"):
        preview_cols = [c for c in _CI_COL_MAP if c in df.columns]
        st.dataframe(
            df[preview_cols].head(100).rename(columns=_CI_COL_MAP),
            use_container_width=True,
            height=300,
        )

    st.divider()

    # ── Generate report ────────────────────────────────────────────────────────
    api_key = _resolve_secret("ANTHROPIC_API_KEY")

    if not api_key:
        st.error(
            "**ANTHROPIC_API_KEY** não encontrada. "
            "Adicione-a em `.env` (local) ou em **Settings → Secrets** (Streamlit Cloud)."
        )
        return

    gen_btn = st.button(
        "🤖 Gerar Análise Competitiva",
        type="primary",
        use_container_width=True,
        key="ci_gen",
    )

    if gen_btn:
        st.session_state["ci_report"] = None

        # Build metadata header for user message
        platforms_str = ", ".join(sorted(df["plataforma"].unique())) if "plataforma" in df.columns else "N/D"
        turnos_str    = ", ".join(sorted(df["turno"].unique()))       if "turno"      in df.columns else "N/D"
        dates_str     = f"{start_date} a {end_date}" if start_date != end_date else str(start_date)

        data_payload = _ci_build_payload(df)
        payload_mode = "dados brutos" if len(df) <= _CI_MAX_RAW else "dados pré-processados"

        user_msg = (
            f"Analise os seguintes dados de monitoramento de posicionamento RAC coletados em {dates_str}.\n\n"
            f"Total de registros: {len(df):,}\n"
            f"Plataformas: {platforms_str}\n"
            f"Keywords: {n_kw} únicas\n"
            f"Turnos: {turnos_str}\n"
            f"Modo de análise solicitado: {compare_mode}\n"
            f"Formato dos dados: {payload_mode}\n\n"
            f"DADOS (separador ponto-e-vírgula):\n{data_payload}"
        )

        with st.spinner(f"Analisando {len(df):,} registros… (pode levar 30–90 s)"):
            try:
                import anthropic as _anthropic
                client_ai = _anthropic.Anthropic(api_key=api_key)
                response = client_ai.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=16_000,
                    system=_CI_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                )
                st.session_state["ci_report"] = response.content[0].text
            except Exception as exc:
                st.error(f"Erro ao chamar a Claude API: {exc}")
                return

    # ── Render report ──────────────────────────────────────────────────────────
    report_md: str | None = st.session_state.get("ci_report")

    if report_md:
        st.markdown(report_md)

        st.divider()

        dates_label = f"{start_date}_{end_date}" if start_date != end_date else str(start_date)
        st.download_button(
            label="📥 Download Relatório (.md)",
            data=report_md,
            file_name=f"Relatorio_CI_{dates_label}.md",
            mime="text/markdown",
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Page: Overview — Executive landing
# ---------------------------------------------------------------------------

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
                    title={"text": "Preço Mediano por Marca"},
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
            vol = (
                df.groupby("plataforma", as_index=False).size()
                .rename(columns={"size": "Registros", "plataforma": "Plataforma"})
                .sort_values("Registros", ascending=False).head(10)
            )
            fig2 = px.bar(
                vol, x="Plataforma", y="Registros",
                color="Plataforma", color_discrete_sequence=_CHART_COLORS,
                title={"text": "Registros por Plataforma"},
            )
            _apply_chart_style(fig2, height=320)
            st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})
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
                title={"text": "Distribuição por Marca"},
            )
            fig3.update_traces(textposition="inside", textinfo="percent+label")
            _apply_chart_style(fig3, height=320, hovermode="closest")
            st.plotly_chart(fig3, use_container_width=True, config={"displayModeBar": False})
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
                    fig4 = px.bar(
                        mv, x="delta_pct", y="SKU", orientation="h",
                        color="delta_pct",
                        color_continuous_scale=["#ef4444", "#fbbf24", "#059669"],
                        color_continuous_midpoint=0,
                        title={"text": "Variação de Preço (48h)"},
                        labels={"delta_pct": "Variação %"},
                    )
                    fig4.update_coloraxes(showscale=False)
                    _apply_chart_style(fig4, height=320)
                    st.plotly_chart(fig4, use_container_width=True, config={"displayModeBar": False})
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


# ---------------------------------------------------------------------------
# Page: Top Movers — Price movement between two windows
# ---------------------------------------------------------------------------

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
        title={"text": (
            f"Top 20 Movers — {start_date.strftime('%d/%m')}→{end_date.strftime('%d/%m')}"
            f" vs {cmp_start.strftime('%d/%m')}→{cmp_end.strftime('%d/%m')}"
        )},
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


# ---------------------------------------------------------------------------
# Page registry & grouped navigation
# ---------------------------------------------------------------------------

PAGES = {
    "🏠 Overview":                 page_overview,
    "🚨 Top Movers":               page_top_movers,
    "📊 Results":                  page_results,
    "📈 Price Evolution":           page_price_evolution,
    "🏆 BuyBox Position":          page_buybox_position,
    "📦 Availability":             page_availability,
    "🧠 Competitive Intelligence": page_ci_analysis,
    "🚀 Run Collection":           page_run_collection,
    "📂 Import History":           page_import_history,
    "🧹 Data Cleanup":             page_data_cleanup,
    "🔤 Normalize SKUs":           page_normalize_skus,
}

_NAV_GROUPS: dict[str, list[str]] = {
    "INSIGHTS": [
        "🏠 Overview",
        "🚨 Top Movers",
        "📊 Results",
        "📈 Price Evolution",
        "🏆 BuyBox Position",
        "📦 Availability",
        "🧠 Competitive Intelligence",
    ],
    "OPERAÇÕES": [
        "🚀 Run Collection",
        "📂 Import History",
    ],
    "ADMIN": [
        "🧹 Data Cleanup",
        "🔤 Normalize SKUs",
    ],
}

# Resolve deep-link navigation from overview/movers shortcut buttons
if "_nav_page" in st.session_state:
    target = st.session_state.pop("_nav_page")
    if target in PAGES:
        st.session_state["_current_page"] = target
    st.rerun()

if "_current_page" not in st.session_state:
    st.session_state["_current_page"] = "🏠 Overview"

# Guard against stale keys after a code update
if st.session_state["_current_page"] not in PAGES:
    st.session_state["_current_page"] = "🏠 Overview"

_SECTION_LABEL_CSS = (
    "color:#94a3b8; font-size:0.65rem; font-weight:700; "
    "letter-spacing:0.12em; text-transform:uppercase; "
    "margin:0.75rem 0 0.2rem; padding:0;"
)

with st.sidebar:
    st.markdown("## ❄️ RAC Monitor")
    st.divider()

    # ── Global filters ────────────────────────────────────────────────────────
    _render_global_filters()
    st.divider()

    # ── Grouped navigation ────────────────────────────────────────────────────
    current = st.session_state["_current_page"]

    for group_label, page_list in _NAV_GROUPS.items():
        st.markdown(f"<p style='{_SECTION_LABEL_CSS}'>{group_label}</p>",
                    unsafe_allow_html=True)
        for page_name in page_list:
            is_active = current == page_name
            # Prefix active page with a bullet so users see which page is open
            btn_label = f"▶ {page_name}" if is_active else f"  {page_name}"
            if st.button(btn_label, key=f"nav__{page_name}",
                         use_container_width=True, type="secondary"):
                st.session_state["_current_page"] = page_name
                st.rerun()

    st.divider()

    # ── Status footer ─────────────────────────────────────────────────────────
    client_ok = _get_supabase() is not None
    st.caption(f"Supabase: {'🟢 conectado' if client_ok else '🔴 desconectado'}")
    st.caption(f"🕐 {date.today().strftime('%d/%m/%Y')}")

PAGES[st.session_state["_current_page"]]()
