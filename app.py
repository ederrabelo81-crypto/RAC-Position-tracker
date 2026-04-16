"""
app.py — RAC Price Monitor Dashboard

Usage (local):
    streamlit run app.py

Usage (remote access):
    streamlit run app.py --server.address=0.0.0.0 --server.port=8501
    Then open: http://<your-ip>:8501
"""

import os
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

st.set_page_config(
    page_title="RAC Price Monitor",
    page_icon="❄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

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
    # Canonical name -> list of known variations in DB
    "Ferreira Costa": ["FerreiraCosta", "FerreiraCoasta"],
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

@st.cache_resource(show_spinner=False)
def _get_supabase():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
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


def query_coletas(
    start_date: date,
    end_date: date,
    platforms: list[str] | None = None,
    platform_types: list[str] | None = None,
    brands: list[str] | None = None,
    keywords: list[str] | None = None,
    products: list[str] | None = None,
    btu_filter: list[str] | None = None,
    product_types: list[str] | None = None,
    limit: int = 5000,
) -> pd.DataFrame:
    """Query the coletas table with filters. Returns empty DataFrame on error."""
    client = _get_supabase()
    if client is None:
        st.error("Supabase not connected. Check your .env file.")
        return pd.DataFrame()
    try:
        q = (
            client.table("coletas")
            .select("*")
            .gte("data", str(start_date))
            .lte("data", str(end_date))
            .order("data", desc=True)
            .limit(limit)
        )
        if platforms:
            q = q.in_("plataforma", platforms)
        if platform_types:
            # DB column is "tipo" (mapped from CSV "Tipo Plataforma" — see _COLUMN_MAP)
            q = q.in_("tipo", platform_types)
        if brands:
            # Expand canonical names to all raw DB variants
            # (e.g. "Midea" → ["Midea", "Springer Midea", "Midea Carrier", "Springer"])
            q = q.in_("marca", _expand_brands(brands))
        if keywords:
            q = q.in_("keyword", keywords)
        if products:
            q = q.in_("produto", products)
        # Combine btu_filter and product_types into a single OR expression
        # to avoid multiple .or_() calls which create separate query params
        or_patterns = []
        
        if btu_filter:
            # Match both raw ("12000") and normalized ("12.000") formats —
            # after normalization, product names contain "12.000 BTUs" with dot.
            for btu in btu_filter:
                or_patterns.append(f"produto.ilike.%{btu}%")
                try:
                    dotted = f"{int(btu):,}".replace(",", ".")  # "12000" → "12.000"
                    if dotted != btu:
                        or_patterns.append(f"produto.ilike.%{dotted}%")
                except ValueError:
                    pass
        
        if product_types:
            # Each label may map to several spelling variants — OR them together.
            for label in product_types:
                for pat in PRODUCT_TYPE_OPTIONS.get(label, [label]):
                    or_patterns.append(f"produto.ilike.%{pat}%")
        
        # Apply all ILIKE patterns in a single OR expression
        if or_patterns:
            q = q.or_(",".join(or_patterns))

        resp = q.execute()
        if not resp.data:
            return pd.DataFrame()
        df = pd.DataFrame(resp.data)
        df["data"] = pd.to_datetime(df["data"]).dt.date
        for col in ["posicao_organica", "posicao_patrocinada", "posicao_geral", "qtd_avaliacoes"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in ["preco", "avaliacao"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception as exc:
        st.error(f"Query error: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def get_filter_options() -> dict:
    """Fetch distinct values for filter dropdowns (last 90 days)."""
    empty = {"platforms": [], "platform_types": [], "brands": [], "keywords": []}
    client = _get_supabase()
    if client is None:
        return empty
    try:
        since = str(date.today() - timedelta(days=90))
        # DB column is "tipo" (CSV "Tipo Plataforma" → DB "tipo" per _COLUMN_MAP).
        resp = (
            client.table("coletas")
            .select("plataforma, tipo, marca, keyword")
            .gte("data", since)
            .limit(50000)
            .execute()
        )
        df = pd.DataFrame(resp.data) if resp.data else pd.DataFrame()
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
        }
    except Exception as exc:
        st.warning(f"Filter options query failed: {exc}")
        return empty


@st.cache_data(ttl=300, show_spinner=False)
def get_sku_options(brands: tuple = ()) -> list:
    """
    Fetch distinct normalized product names from the last 90 days.
    When brands is non-empty, filters by all raw DB marca variants of those
    canonical brands (e.g. "Midea" also queries "Springer Midea" etc.).
    Returns a sorted list ready for st.multiselect.
    """
    client = _get_supabase()
    if client is None:
        return []
    try:
        since = str(date.today() - timedelta(days=90))
        q = (
            client.table("coletas")
            .select("produto")
            .gte("data", since)
            .not_.is_("produto", "null")
            .limit(10000)
        )
        if brands:
            # Expand canonical names to all raw DB variants before filtering
            q = q.in_("marca", _expand_brands(list(brands)))
        resp = q.execute()
        if not resp.data:
            return []
        return sorted({r["produto"] for r in resp.data if r.get("produto")})
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def _init_state():
    defaults = {
        "process":  None,
        "running":  False,
        "log":      "",
        "run_done": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

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
            st.info("⏳ Collection in progress...")
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

        st.session_state.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            bufsize=1,
        )
        st.session_state.running  = True
        st.session_state.run_done = False
        st.session_state.log      = ""
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
        # Read up to 50 lines per rerun cycle
        for _ in range(50):
            line = proc.stdout.readline()
            if not line:
                break
            st.session_state.log += line

        if proc.poll() is not None:
            remaining = proc.stdout.read()
            if remaining:
                st.session_state.log += remaining
            st.session_state.running  = False
            st.session_state.run_done = True
            st.session_state.process  = None
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
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=7)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()

        sel_tipo      = st.multiselect("Tipo Plataforma", opts["platform_types"])
        sel_platforms = st.multiselect("Platforms",       opts["platforms"])
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

        # SKU drill-down — list refreshes when brand selection changes
        _sku_opts = get_sku_options(tuple(sorted(sel_brands)))
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
            keywords=sel_keywords or None,
            products=sel_skus or None,
            btu_filter=sel_btu or None,
            product_types=sel_ptype or None,
        )

    if df.empty:
        st.warning("No data found for the selected filters.")
        return

    # --- Summary metrics ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total records",  f"{len(df):,}")
    c2.metric("Platforms",      df["plataforma"].nunique() if "plataforma" in df else 0)
    c3.metric("Brands",         df["marca"].nunique() if "marca" in df else 0)
    c4.metric("With price",     f"{df['preco'].notna().sum():,}" if "preco" in df else 0)

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
        df[display_cols],
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
            key="evo_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=30)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()

        sel_tipo      = st.multiselect("Tipo Plataforma", opts["platform_types"], key="evo_tipo")
        sel_brands    = st.multiselect("Brands",    opts["brands"],         key="evo_brands")
        sel_platforms = st.multiselect("Platforms", opts["platforms"],      key="evo_platforms")
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

        # SKU drill-down
        _sku_opts = get_sku_options(tuple(sorted(sel_brands)))
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
            keywords=sel_keywords or None,
            products=sel_skus or None,
            btu_filter=sel_btu or None,
            product_types=sel_ptype or None,
            limit=10000,
        )

    if df.empty or "preco" not in df.columns:
        st.warning("No price data found for the selected filters.")
        return

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

    # --- Line chart ---
    fig = px.line(
        agg,
        x="data",
        y="Median Price (R$)",
        color=group_by,
        markers=True,
        title=f"Median Price Evolution by {group_by}",
        labels={"data": "Date"},
        template="plotly_white",
    )
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        height=500,
    )
    fig.update_traces(line=dict(width=2), marker=dict(size=5))
    st.plotly_chart(fig, use_container_width=True)

    # --- Summary table ---
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
    st.dataframe(summary, use_container_width=True, hide_index=True)


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
            key="bb_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=30)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()

        sel_tipo      = st.multiselect("Tipo Plataforma", opts["platform_types"], key="bb_tipo")
        sel_platforms = st.multiselect("Platforms", opts["platforms"],      key="bb_platforms")
        sel_brands    = st.multiselect("Brands",    opts["brands"],         key="bb_brands")
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

        # SKU drill-down
        _sku_opts = get_sku_options(tuple(sorted(sel_brands)))
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
            products=sel_skus or None,
            btu_filter=sel_btu or None,
            product_types=sel_ptype or None,
            limit=20000,
        )

    if df.empty or "posicao_geral" not in df.columns:
        st.warning("No data found for the selected filters.")
        return

    # Filter to top-N positions only
    df_top = df[df["posicao_geral"].notna() & (df["posicao_geral"] <= top_n)].copy()

    if df_top.empty:
        st.warning(f"No records with position ≤ {top_n} in this range.")
        return

    # --- Summary metrics ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BuyBox records",   f"{len(df_top):,}")
    c2.metric("Platforms",        df_top["plataforma"].nunique() if "plataforma" in df_top else 0)
    c3.metric("Brands in top",    df_top["marca"].nunique() if "marca" in df_top else 0)
    c4.metric("Unique products",  df_top["produto"].nunique() if "produto" in df_top else 0)

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
                fig_bar = px.bar(
                    win_counts.head(15),
                    x="BuyBox wins",
                    y="marca",
                    orientation="h",
                    color="Win rate (%)",
                    color_continuous_scale="Blues",
                    text="Win rate (%)",
                    labels={"marca": "Brand"},
                    title=f"Top brands in position ≤ {top_n}",
                    template="plotly_white",
                )
                fig_bar.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
                fig_bar.update_layout(
                    yaxis=dict(autorange="reversed"),
                    coloraxis_showscale=False,
                    height=420,
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            with col_table:
                st.dataframe(win_counts, use_container_width=True, hide_index=True)

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
                template="plotly_white",
            )
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
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

            fig_line = px.line(
                timeline,
                x="data",
                y="BuyBox wins",
                color=group_choice,
                markers=True,
                title=f"Daily BuyBox wins by {group_choice}",
                labels={"data": "Date"},
                template="plotly_white",
            )
            fig_line.update_layout(
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                hovermode="x unified",
                height=450,
            )
            fig_line.update_traces(line=dict(width=2), marker=dict(size=5))
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
            df_top[display_cols].sort_values(
                ["data", "plataforma", "posicao_geral"],
                ascending=[False, True, True],
            ),
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




PAGES = {
    "🚀 Run Collection":  page_run_collection,
    "📊 Results":         page_results,
    "📈 Price Evolution":  page_price_evolution,
    "🏆 BuyBox Position": page_buybox_position,
    "📂 Import History":  page_import_history,
    "🧹 Data Cleanup":    page_data_cleanup,
    "🔤 Normalize SKUs":  page_normalize_skus,
}

with st.sidebar:
    st.markdown("## ❄️ RAC Monitor")
    st.divider()
    page = st.radio("", list(PAGES.keys()), label_visibility="collapsed")
    st.divider()
    client_ok = _get_supabase() is not None
    st.caption(f"Supabase: {'🟢 connected' if client_ok else '🔴 not connected'}")

PAGES[page]()
