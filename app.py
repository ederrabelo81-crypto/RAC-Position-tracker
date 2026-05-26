"""
app.py — RAC Price Monitor Dashboard

Usage (local):
    streamlit run app.py

Usage (remote access):
    streamlit run app.py --server.address=0.0.0.0 --server.port=8501
    Then open: http://<your-ip>:8501
"""

from datetime import date
from pathlib import Path

import pandas as pd
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

st.set_page_config(
    page_title="RAC Price Monitor",
    page_icon="❄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Load CSS from assets/style.css and inject inline.
_CSS_PATH = PROJECT_ROOT / "assets" / "style.css"
if _CSS_PATH.exists():
    st.markdown(
        f"<style>{_CSS_PATH.read_text(encoding='utf-8')}</style>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Shared helpers + global filters (sidebar)
# ---------------------------------------------------------------------------

from lib.filters import _render_global_filters
from lib.supabase import _get_supabase

# ---------------------------------------------------------------------------
# Page modules — one function per page, all imported from views/
# ---------------------------------------------------------------------------

from views.availability import page_availability
from views.buybox import page_buybox_position
from views.ci_analysis import page_ci_analysis
from views.data_cleanup import page_data_cleanup
from views.email_digest import page_email_digest
from views.import_history import page_import_history
from views.market_analytics import page_market_analytics
from views.normalize_skus import page_normalize_skus
from views.overview import page_overview
from views.price_anomalies import page_price_anomalies
from views.price_evolution import page_price_evolution
from views.product_sheet import page_product_sheet
from views.results import page_results
from views.run_collection import page_run_collection
from views.top_movers import page_top_movers

# ---------------------------------------------------------------------------
# Page registry & grouped navigation
# ---------------------------------------------------------------------------

PAGES = {
    "🏠 Overview":                 page_overview,
    "🚨 Top Movers":               page_top_movers,
    "📊 Results":                  page_results,
    "📈 Price Evolution":           page_price_evolution,
    "📊 Market Analytics":         page_market_analytics,
    "🗂️ Ficha do Produto":         page_product_sheet,
    "🏆 BuyBox Position":          page_buybox_position,
    "📦 Availability":             page_availability,
    "🧠 Competitive Intelligence": page_ci_analysis,
    "🚀 Run Collection":           page_run_collection,
    "📧 Email Digest":             page_email_digest,
    "🔔 Price Anomalies":          page_price_anomalies,
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
        "📊 Market Analytics",
        "🗂️ Ficha do Produto",
        "🏆 BuyBox Position",
        "📦 Availability",
        "🧠 Competitive Intelligence",
    ],
    "OPERAÇÕES": [
        "🚀 Run Collection",
        "📧 Email Digest",
        "🔔 Price Anomalies",
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
