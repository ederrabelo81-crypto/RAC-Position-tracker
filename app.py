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

_CSS_PATH = PROJECT_ROOT / "assets" / "style.css"
try:
    _CSS = f"<style>{_CSS_PATH.read_text(encoding='utf-8')}</style>"
except OSError:
    _CSS = ""


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


def _resolve_screenshot_path(raw) -> Path | None:
    """Resolve uma referência de screenshot armazenada para um Path local.

    No modo local-only os screenshots guardam um caminho de arquivo
    (ex: 'screenshots/20260514/Mercado_Livre/kw_busca.webp'), relativo à raiz
    do projeto. Retorna o Path se o arquivo existir, senão None. URLs http(s)
    retornam None (o chamador trata como imagem remota).
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or raw.startswith("http://") or raw.startswith("https://"):
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p if p.exists() else None


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


# ---------------------------------------------------------------------------
# Catálogo canônico + de-para de família resolvida
#
# Os filtros de Estado/Família/SKU dependem destas duas fontes:
#   - produtos_catalogo: 241 SKUs RAC High Wall (marca, familia, btu, ciclo)
#   - produtos_depara_nome: ligação nome_coletado → família/SKU/estado
# Ambas mudam pouco, então cacheamos por 10 minutos.
# ---------------------------------------------------------------------------

_ESTADOS_RESOLVIDOS = ["MAPEADO", "FORA_ESCOPO", "NAO_AC", "REVISAR"]


@st.cache_data(ttl=600, show_spinner=False)
def get_catalogo() -> pd.DataFrame:
    """Carrega produtos_catalogo (241 SKUs RAC High Wall)."""
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()
    try:
        resp = client.table("produtos_catalogo").select("*").execute()
        return pd.DataFrame(resp.data or [])
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def get_depara() -> pd.DataFrame:
    """Carrega o de-para nome→família (paginado)."""
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()
    try:
        rows: list = []
        offset = 0
        while True:
            resp = (client.table("produtos_depara_nome")
                    .select("nome_coletado,estado,familia,sku,marca_norm")
                    .range(offset, offset + _SUPABASE_PAGE - 1)
                    .execute())
            if not resp.data:
                break
            rows.extend(resp.data)
            if len(resp.data) < _SUPABASE_PAGE:
                break
            offset += _SUPABASE_PAGE
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def _familia_marca(familia: str | None) -> str | None:
    """Extrai a marca normalizada de uma família (real ou genérica)."""
    if not familia:
        return None
    cat = get_catalogo()
    if not cat.empty and familia in cat["familia"].values:
        return cat.loc[cat["familia"] == familia, "marca"].iloc[0]
    # Genérica: <MARCA>-<BTU>-<CICLO>
    parts = familia.split("-")
    if len(parts) == 3 and parts[1].isdigit() and parts[2] in ("F", "QF", "Q"):
        return parts[0]
    return None


def _familia_is_generica(familia: str | None) -> bool:
    if not familia:
        return False
    parts = familia.split("-")
    return len(parts) == 3 and parts[1].isdigit() and parts[2] in ("F", "QF", "Q")


def _familia_display(familia: str | None) -> str:
    """Rótulo para exibição no multiselect — marca genéricas."""
    if not familia:
        return ""
    return f"{familia} (genérica)" if _familia_is_generica(familia) else familia


@st.cache_data(ttl=600, show_spinner=False)
def get_familia_options(brands: tuple = (), estados: tuple = ()) -> list:
    """Famílias disponíveis (catálogo + genéricas em uso), filtradas por marca/estado."""
    depara = get_depara()
    if depara.empty:
        return []
    df = depara[depara["familia"].notna()].copy()
    if estados:
        df = df[df["estado"].isin(estados)]
    if brands:
        brands_upper = {b.upper() for b in brands}
        df = df[df["marca_norm"].isin(brands_upper)]
    fams = sorted(df["familia"].dropna().unique().tolist())
    return fams


@st.cache_data(ttl=600, show_spinner=False)
def get_sku_resolvido_options(familias: tuple = ()) -> list:
    """SKUs do catálogo filtrados pelas famílias selecionadas."""
    cat = get_catalogo()
    if cat.empty:
        return []
    df = cat
    if familias:
        df = df[df["familia"].isin(list(familias))]
    return sorted(df["sku"].dropna().unique().tolist())


@st.cache_data(ttl=600, show_spinner=False)
def get_btu_options_catalogo() -> list:
    cat = get_catalogo()
    if cat.empty:
        return []
    return sorted(cat["capacidade_btu"].dropna().unique().astype(int).tolist())


# ---------------------------------------------------------------------------
# Global filter accessors p/ filtros novos (estado/família/SKU)
# Defaults: estado=['MAPEADO']; demais vazios = sem filtro extra
# ---------------------------------------------------------------------------

def _gf_estados() -> list:
    return list(st.session_state.get("gf_estados", ["MAPEADO"]))


def _gf_familias() -> list:
    return list(st.session_state.get("gf_familias", []))


def _gf_skus_resolvidos() -> list:
    return list(st.session_state.get("gf_skus_resolvidos", []))


def _gf_btu_catalogo() -> list:
    return list(st.session_state.get("gf_btu_catalogo", []))


@st.cache_data(ttl=300, show_spinner=False)
def get_cobertura_resolucao() -> dict:
    """Conta linhas de coletas por estado_match — usado no banner do topo."""
    client = _get_supabase()
    if client is None:
        return {}
    try:
        out: dict = {"total": 0, "MAPEADO": 0, "FORA_ESCOPO": 0, "NAO_AC": 0,
                     "REVISAR": 0, "NULL": 0}
        for est in _ESTADOS_RESOLVIDOS:
            r = (client.table("coletas").select("id", count="exact", head=True)
                 .eq("estado_match", est).execute())
            out[est] = int(r.count or 0)
            out["total"] += out[est]
        r_null = (client.table("coletas").select("id", count="exact", head=True)
                  .is_("estado_match", "null").execute())
        out["NULL"] = int(r_null.count or 0)
        out["total"] += out["NULL"]
        return out
    except Exception:
        return {}


def _render_cobertura_banner() -> None:
    """Banner do topo: % mapeado + alerta se há REVISAR/NULL pendentes."""
    c = get_cobertura_resolucao()
    if not c or c.get("total", 0) == 0:
        return
    pct = 100.0 * c.get("MAPEADO", 0) / c["total"]
    revisar = c.get("REVISAR", 0) + c.get("NULL", 0)
    cols = st.columns(4)
    cols[0].metric("Cobertura (MAPEADO)", f"{pct:.1f}%", help="% de linhas em coletas com família resolvida")
    cols[1].metric("Fora de escopo", f"{c.get('FORA_ESCOPO', 0):,}".replace(",", "."),
                   help="Marca não-catalogada, janela/portátil/cassete/multi-split, BTU fora do range RAC")
    cols[2].metric("Não-AC",         f"{c.get('NAO_AC', 0):,}".replace(",", "."),
                   help="Peças, eletrodomésticos, acessórios, ruído de busca")
    cols[3].metric("Revisar (fila humana)", f"{revisar:,}".replace(",", "."),
                   help="Nomes sem classificação confiável — rode scripts/descobrir_nomes_novos.py")
    if revisar > 0:
        st.caption(f"💡 {revisar:,} linhas pendentes de classificação — "
                   "exporte com `python scripts/descobrir_nomes_novos.py` "
                   "para classificar manualmente.".replace(",", "."))


def _filter_latest_run(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra o DataFrame para mostrar apenas o último run_id de cada
    (data, turno, plataforma), preservando registros históricos (run_id NULL).

    Registros sem run_id (histórico anterior à feature) são sempre exibidos.
    Para dados novos (com run_id), mantém apenas o run mais recente de cada
    combinação (data, turno, plataforma) com base em created_at.
    """
    if "run_id" not in df.columns:
        return df

    mask_historico = df["run_id"].isna()
    df_hist = df[mask_historico]
    df_novos = df[~mask_historico]

    if df_novos.empty:
        return df

    # Determina o run_id mais recente por (data, turno, plataforma)
    if "created_at" in df_novos.columns:
        sort_col = "created_at"
    else:
        sort_col = "run_id"  # fallback — ordena por UUID (não ideal, mas seguro)

    idx_ultimo = (
        df_novos
        .sort_values(sort_col, ascending=False)
        .drop_duplicates(subset=["data", "turno", "plataforma", "run_id"], keep="first")
        .groupby(["data", "turno", "plataforma"])["run_id"]
        .first()
        .reset_index()
    )

    df_filtrado = df_novos.merge(
        idx_ultimo[["data", "turno", "plataforma", "run_id"]],
        on=["data", "turno", "plataforma", "run_id"],
        how="inner",
    )

    return pd.concat([df_hist, df_filtrado], ignore_index=True)


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

        # Camada nova: filtros resolvidos (estado/família/SKU) via session_state.
        # Default = só MAPEADO. Permite ao usuário ver FORA_ESCOPO/NAO_AC/REVISAR.
        gf_estados = _gf_estados()
        if gf_estados:
            q = q.in_("estado_match", gf_estados)
        gf_familias = _gf_familias()
        if gf_familias:
            q = q.in_("familia_resolvida", gf_familias)
        gf_skus = _gf_skus_resolvidos()
        if gf_skus:
            q = q.in_("sku_resolvido", gf_skus)
        gf_btu_cat = _gf_btu_catalogo()
        if gf_btu_cat:
            # BTUs do catálogo: aceita match contra família genérica <MARCA>-<BTU>-<CICLO>
            # ou SKU do catálogo com aquela capacidade.
            cat = get_catalogo()
            skus_btu = (cat[cat["capacidade_btu"].isin(gf_btu_cat)]["sku"].tolist()
                        if not cat.empty else [])
            fams_btu_gen = [f"%-{int(b)}-%" for b in gf_btu_cat]
            parts = [f"familia_resolvida.like.{p}" for p in fams_btu_gen]
            if skus_btu:
                parts.append(f"sku_resolvido.in.({','.join(skus_btu)})")
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

        # --- Filtros resolvidos (Estado / Família / SKU / BTU catálogo) ---
        st.markdown("**Catálogo RAC**")
        st.multiselect(
            "Estado do match",
            _ESTADOS_RESOLVIDOS,
            default=["MAPEADO"],
            help=("MAPEADO = bate com família do catálogo (real ou genérica). "
                  "FORA_ESCOPO = não-RAC HW (janela/portátil/cassete/multi-split ou marca não-catalogada). "
                  "NAO_AC = não é ar-condicionado. REVISAR = pendente de classificação humana."),
            key="gf_estados",
        )
        # Cascata: famílias filtradas por marca selecionada e estados ativos
        _sel_brands_upper = tuple(b.upper() for b in st.session_state.get("gf_brands", []))
        _sel_estados     = tuple(st.session_state.get("gf_estados", ["MAPEADO"]))
        _fam_opts        = get_familia_options(_sel_brands_upper, _sel_estados)
        st.multiselect(
            "Família", _fam_opts,
            format_func=_familia_display,
            placeholder="Todas as famílias do(s) recorte(s) acima",
            key="gf_familias",
        )
        # SKU resolvido (do catálogo) — depende das famílias escolhidas
        _sku_opts = get_sku_resolvido_options(tuple(st.session_state.get("gf_familias", [])))
        st.multiselect(
            "SKU do catálogo", _sku_opts,
            placeholder="Todos os SKUs das famílias acima",
            key="gf_skus_resolvidos",
        )
        st.multiselect(
            "Capacidade BTU (catálogo)", get_btu_options_catalogo(),
            placeholder="Todas as capacidades",
            format_func=lambda b: f"{int(b):,}".replace(",", "."),
            key="gf_btu_catalogo",
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
                    "estados":   st.session_state.get("gf_estados", ["MAPEADO"]),
                    "familias":  st.session_state.get("gf_familias", []),
                    "skus_resolvidos": st.session_state.get("gf_skus_resolvidos", []),
                    "btu_catalogo":    st.session_state.get("gf_btu_catalogo", []),
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
                    st.session_state["gf_estados"]   = p.get("estados", ["MAPEADO"])
                    st.session_state["gf_familias"]  = p.get("familias", [])
                    st.session_state["gf_skus_resolvidos"] = p.get("skus_resolvidos", [])
                    st.session_state["gf_btu_catalogo"]    = p.get("btu_catalogo", [])
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
# Email / SMTP helpers — shared by the Email Digest & Price Anomalies pages
# ---------------------------------------------------------------------------

def _esc(value) -> str:
    """HTML-escape a value for safe inclusion in e-mail markup."""
    import html as _html
    return _html.escape(str(value), quote=False)


def _smtp_config() -> dict:
    """Read SMTP settings from environment variables (Replit Secrets / .env)."""
    return {
        "host":     os.getenv("SMTP_HOST", "").strip(),
        "port":     os.getenv("SMTP_PORT", "587").strip() or "587",
        "user":     os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASS", "").strip(),
        "sender":   os.getenv("SMTP_FROM", "").strip(),
    }


def _smtp_ready(cfg: dict | None = None) -> bool:
    """True when host, user, password and sender are all configured."""
    cfg = cfg or _smtp_config()
    return all(cfg.get(k) for k in ("host", "user", "password", "sender"))


def _parse_recipients(raw: str) -> list[str]:
    """Split a comma/semicolon/newline-separated string into clean addresses."""
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[,;\n]+", raw) if p.strip()]


def _send_email_smtp(
    subject: str,
    html_body: str,
    text_body: str,
    recipients: list[str],
) -> tuple[bool, str]:
    """Send a multipart (text + HTML) e-mail via SMTP. Returns (ok, message)."""
    cfg = _smtp_config()
    if not _smtp_ready(cfg):
        return False, "SMTP não configurado — defina as Replit Secrets."
    if not recipients:
        return False, "Nenhum destinatário válido informado."

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        port = int(cfg["port"])
    except ValueError:
        port = 587

    try:
        if port == 465:
            with smtplib.SMTP_SSL(cfg["host"], port, timeout=30) as server:
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["sender"], recipients, msg.as_string())
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=30) as server:
                server.starttls()
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["sender"], recipients, msg.as_string())
        return True, f"E-mail enviado para {len(recipients)} destinatário(s)."
    except Exception as exc:
        return False, f"Falha no envio SMTP: {exc}"


def _render_smtp_help(default_to_key: str) -> None:
    """Render the SMTP setup expander (or a 'configured' note)."""
    cfg = _smtp_config()
    if _smtp_ready(cfg):
        with st.expander("⚙️ SMTP configured", expanded=False):
            st.success(
                f"SMTP pronto — enviando como `{cfg['sender']}` via "
                f"`{cfg['host']}:{cfg['port']}`."
            )
        return
    with st.expander("⚙️ SMTP not configured — click to see what to add",
                     expanded=False):
        st.markdown(
            "Add the following Replit Secrets to enable **Send via SMTP**:"
        )
        st.markdown(
            "| Key | Example |\n"
            "|---|---|\n"
            "| `SMTP_HOST` | `smtp.gmail.com` |\n"
            "| `SMTP_PORT` | `587` (default) |\n"
            "| `SMTP_USER` | `you@gmail.com` |\n"
            "| `SMTP_PASS` | App-password (Gmail → Account → Security "
            "→ App passwords) |\n"
            "| `SMTP_FROM` | `RAC Monitor <you@gmail.com>` |\n"
            f"| `{default_to_key}` | *(optional)* default recipients, "
            "comma-separated |\n"
        )
        st.caption(
            "You can still **preview** and **download** the HTML/text "
            "below without SMTP."
        )


def _badge(label: str, *, bg: str = "#eff6ff", fg: str = "#1a56db",
           border: str = "#bfdbfe") -> None:
    """Render a small rounded pill badge in the main area."""
    st.markdown(
        f"<span style='display:inline-block;background:{bg};color:{fg};"
        f"border:1px solid {border};border-radius:8px;padding:3px 12px;"
        f"font-size:0.85rem;font-weight:600;'>{_esc(label)}</span>",
        unsafe_allow_html=True,
    )


def _email_shell(eyebrow: str, title: str, subtitle: str,
                 accent1: str, accent2: str, body_html: str) -> str:
    """Wrap e-mail body content in a styled, mail-client-safe HTML shell."""
    generated = date.today().strftime("%d/%m/%Y")
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;'
        'background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;">'
        '<div style="max-width:680px;margin:0 auto;padding:24px;">'
        f'<div style="background:linear-gradient(135deg,{accent1},{accent2});'
        'border-radius:12px;padding:24px 28px;color:#ffffff;">'
        f'<div style="font-size:12px;letter-spacing:0.12em;font-weight:700;'
        f'opacity:0.85;">{_esc(eyebrow)}</div>'
        f'<div style="font-size:24px;font-weight:800;margin-top:6px;">'
        f'{_esc(title)}</div>'
        f'<div style="font-size:13px;opacity:0.9;margin-top:4px;">'
        f'{_esc(subtitle)}</div></div>'
        '<div style="background:#ffffff;border-radius:12px;padding:20px 24px;'
        f'margin-top:16px;">{body_html}</div>'
        '<div style="text-align:center;color:#94a3b8;font-size:11px;'
        f'margin-top:16px;">Gerado pelo RAC Price Monitor · {generated}</div>'
        '</div></body></html>'
    )


def _email_table(headers: list[str], rows: list[list[str]],
                 align: list[str]) -> str:
    """Build an HTML <table> for e-mail. Cell strings are inserted verbatim."""
    th = "".join(
        f'<th style="text-align:{align[i]};padding:8px 6px;'
        f'border-bottom:2px solid #e2e8f0;font-size:11px;color:#475569;'
        f'text-transform:uppercase;letter-spacing:0.04em;">{h}</th>'
        for i, h in enumerate(headers)
    )
    body = ""
    for row in rows:
        tds = "".join(
            f'<td style="text-align:{align[i]};padding:8px 6px;'
            f'border-bottom:1px solid #f1f5f9;font-size:12px;'
            f'color:#1e293b;">{cell}</td>'
            for i, cell in enumerate(row)
        )
        body += f"<tr>{tds}</tr>"
    return (
        '<table style="width:100%;border-collapse:collapse;margin-top:6px;">'
        f'<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'
    )


def _render_send_section(
    html_body: str,
    text_body: str,
    *,
    subject: str,
    filename_stub: str,
    default_to_env: str,
    send_button_label: str,
    state_prefix: str,
    recipients_raw: str | None = None,
    recipients_in_section: bool = False,
    show_smtp_help: bool = True,
) -> None:
    """Render the shared 'Send as email' block: preview, downloads, send."""
    st.divider()
    st.subheader("📨 Send as email")

    with st.expander("Preview email", expanded=False):
        tab_html, tab_text = st.tabs(["HTML", "Plain text"])
        with tab_html:
            import streamlit.components.v1 as components
            components.html(html_body, height=520, scrolling=True)
        with tab_text:
            st.code(text_body, language=None)

    dl1, dl2 = st.columns(2)
    dl1.download_button(
        "⬇️ Download HTML", data=html_body.encode("utf-8"),
        file_name=f"{filename_stub}.html", mime="text/html",
        use_container_width=True, key=f"{state_prefix}_dl_html",
    )
    dl2.download_button(
        "⬇️ Download text", data=text_body.encode("utf-8"),
        file_name=f"{filename_stub}.txt", mime="text/plain",
        use_container_width=True, key=f"{state_prefix}_dl_txt",
    )

    if recipients_in_section:
        recipients_raw = st.text_input(
            "Recipients (comma-separated)",
            value=os.getenv(default_to_env, ""),
            placeholder="alice@example.com, bob@example.com",
            key=f"{state_prefix}_recipients",
        )

    if show_smtp_help:
        _render_smtp_help(default_to_env)

    recipients = _parse_recipients(recipients_raw or "")
    if not recipients:
        recipients = _parse_recipients(os.getenv(default_to_env, ""))

    smtp_ok = _smtp_ready()
    if st.button(f"📧 {send_button_label}", type="primary",
                 disabled=not smtp_ok, use_container_width=False,
                 key=f"{state_prefix}_send"):
        if not recipients:
            st.warning("Informe ao menos um destinatário antes de enviar.")
        else:
            ok, msg = _send_email_smtp(subject, html_body, text_body,
                                       recipients)
            (st.success if ok else st.error)(msg)
    if not smtp_ok:
        st.caption(
            "Configure SMTP (see expander) to enable sending — "
            "preview and downloads work without it."
        )


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

        st.divider()
        modo_evo = st.radio(
            "Modo de visualização",
            ["Snapshot oficial (último run)", "Todos os runs (auditoria)"],
            index=0,
            key="evo_modo",
            help=(
                "**Snapshot oficial**: usa apenas o último run de cada "
                "(data, turno, plataforma) para evitar duplicatas nos gráficos.\n\n"
                "**Auditoria**: inclui todos os runs — útil para inspecionar "
                "variações intra-turno."
            ),
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

    if modo_evo == "Snapshot oficial (último run)":
        df = _filter_latest_run(df)

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

    # ── Re-extrair marcas Desconhecidas ───────────────────────────────────────
    st.subheader("🔄 Recalcular Marcas Desconhecidas")
    st.caption(
        "Varre registros com `marca = 'Desconhecida'` e re-aplica `extract_brand()` "
        "usando a lista atual de marcas em `config.BRANDS`. "
        "Use após adicionar novas marcas para recuperar registros históricos."
    )
    new_brands_list = [
        "AIWA", "American Range", "Geminis", "Fontaine", "Luxor",
        "Turbro", "Velleman", "Whynter", "DeLonghi", "Kian", "Equation",
    ]
    st.info(
        "Marcas recém-adicionadas (Abril 2026): "
        + ", ".join(f"**{b}**" for b in new_brands_list)
    )

    col_rb1, col_rb2 = st.columns(2)
    with col_rb1:
        rebrand_scan_btn = st.button(
            "🔍 Scan 'Desconhecida' records",
            use_container_width=True,
            key="rebrand_scan_btn",
        )
    with col_rb2:
        rebrand_apply_btn = st.button(
            "✏️ Apply brand recalculation",
            type="primary",
            use_container_width=True,
            key="rebrand_apply_btn",
            help="Atualiza o campo marca para todos os registros identificados.",
        )

    if rebrand_scan_btn:
        with st.spinner("Scanning 'Desconhecida' records…"):
            from utils.supabase_client import recalculate_unknown_brands_in_supabase
            rebrand_result = recalculate_unknown_brands_in_supabase(dry_run=True)
        st.session_state["rebrand_scan"] = rebrand_result

    if "rebrand_scan" in st.session_state:
        rb = st.session_state["rebrand_scan"]
        st.metric("Registros escaneados", f"{rb['scanned']:,}")
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Identificados", f"{rb['scanned'] - rb['unchanged']:,}")
        col_m2.metric("Permanecem desconhecidos", f"{rb['unchanged']:,}")
        col_m3.metric("Erros", f"{rb.get('errors', 0):,}")

        if rb["preview"]:
            st.dataframe(
                rb["preview"],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "id": st.column_config.NumberColumn("ID", width="small"),
                    "produto": st.column_config.TextColumn("Produto", width="large"),
                    "nova_marca": st.column_config.TextColumn("Nova Marca", width="medium"),
                },
            )

        if rb["scanned"] - rb["unchanged"] == 0:
            st.success("✅ Nenhum registro 'Desconhecida' identificado com as marcas atuais.")
        else:
            st.warning(
                f"**{rb['scanned'] - rb['unchanged']:,}** registros podem ser atualizados. "
                "Clique em **Apply brand recalculation** para gravar."
            )

    if rebrand_apply_btn:
        scan = st.session_state.get("rebrand_scan")
        to_fix = (scan["scanned"] - scan["unchanged"]) if scan else 0
        if not scan or to_fix == 0:
            st.warning("Execute o scan primeiro para confirmar os registros a atualizar.")
        else:
            with st.spinner(f"Atualizando {to_fix:,} registros…"):
                from utils.supabase_client import recalculate_unknown_brands_in_supabase
                rebrand_result = recalculate_unknown_brands_in_supabase(dry_run=False)
            if rebrand_result["errors"] == 0:
                st.success(
                    f"✅ Concluído. **{rebrand_result['updated']:,}** registros atualizados."
                )
            else:
                st.warning(
                    f"Parcial: {rebrand_result['updated']:,} atualizados, "
                    f"{rebrand_result['errors']:,} com erros."
                )
            if "rebrand_scan" in st.session_state:
                del st.session_state["rebrand_scan"]
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


def _build_digest_email(window_start, window_end, buybox_pos, brand_map,
                        ups, downs, bb_by_brand, n_records):
    """Build (html, text) for the weekly digest e-mail."""
    headers = ["Product", "Brand", "Prev", "Now", "Δ %"]
    align = ["left", "left", "right", "right", "right"]

    def _mover_rows(df, up):
        color = "#059669" if up else "#dc2626"
        arrow = "▲" if up else "▼"
        rows = []
        for _, r in df.iterrows():
            rows.append([
                _esc(str(r["produto"])[:60]),
                _esc(brand_map.get(r["produto"], "—")),
                _esc(_fmt_brl(r["preco_anterior"])),
                _esc(_fmt_brl(r["preco_atual"])),
                f'<span style="color:{color};font-weight:700;">'
                f'{arrow} {r["delta_pct"]:+.1f}%</span>',
            ])
        return rows

    summary = (f"{len(ups)} movers up · {len(downs)} movers down · "
               f"{n_records:,} records · BuyBox ≤ {buybox_pos}")
    parts = [
        '<div style="background:#fef9c3;border:1px solid #fde68a;'
        'border-radius:8px;padding:8px 14px;display:inline-block;'
        f'font-size:13px;color:#854d0e;font-weight:600;">{_esc(summary)}</div>'
    ]
    if not ups.empty:
        parts.append('<h3 style="color:#059669;font-size:15px;'
                     'margin:18px 0 4px;">▲ TOP MOVERS UP</h3>')
        parts.append(_email_table(headers, _mover_rows(ups, True), align))
    if not downs.empty:
        parts.append('<h3 style="color:#dc2626;font-size:15px;'
                     'margin:18px 0 4px;">▼ TOP MOVERS DOWN</h3>')
        parts.append(_email_table(headers, _mover_rows(downs, False), align))
    if bb_by_brand is not None and not bb_by_brand.empty:
        bb_rows = [[_esc(b), str(int(n))] for b, n in bb_by_brand.items()]
        parts.append('<h3 style="color:#1a56db;font-size:15px;'
                     f'margin:18px 0 4px;">🏆 BUYBOX SNAPSHOT '
                     f'(positions ≤ {buybox_pos})</h3>')
        parts.append(_email_table(["Brand", "Slots"], bb_rows,
                                  ["left", "right"]))
    if len(parts) == 1:
        parts.append('<p style="color:#64748b;">No movers or BuyBox records '
                     'for this window.</p>')

    html = _email_shell(
        "📧 RAC PRICE MONITOR",
        f"Weekly digest — {window_end}",
        f"Active window {window_start} → {window_end}",
        "#1a56db", "#1e3a8a", "".join(parts),
    )

    lines = ["RAC PRICE MONITOR — Weekly digest",
             f"Window: {window_start} -> {window_end}", summary, ""]
    for label, df in (("TOP MOVERS UP", ups), ("TOP MOVERS DOWN", downs)):
        if df.empty:
            continue
        lines.append(label)
        for _, r in df.iterrows():
            lines.append(f"  {r['delta_pct']:+.1f}%  {str(r['produto'])[:60]}"
                         f"  {_fmt_brl(r['preco_anterior'])} -> "
                         f"{_fmt_brl(r['preco_atual'])}")
        lines.append("")
    if bb_by_brand is not None and not bb_by_brand.empty:
        lines.append(f"BUYBOX SNAPSHOT (positions <= {buybox_pos})")
        for b, n in bb_by_brand.items():
            lines.append(f"  {b}: {int(n)}")
    return html, "\n".join(lines)


def _build_anomaly_email(target_day, prev_day, threshold, shown):
    """Build (html, text) for the price-anomalies e-mail."""
    inc = shown[shown["delta_pct"] > 0].sort_values("delta_pct",
                                                    ascending=False)
    dec = shown[shown["delta_pct"] < 0].sort_values("delta_pct")
    headers = ["Product", "Brand", "Platform", str(prev_day),
               str(target_day), "Δ"]
    align = ["left", "left", "left", "right", "right", "right"]

    def _rows(df, up):
        color = "#059669" if up else "#dc2626"
        arrow = "▲" if up else "▼"
        out = []
        for _, r in df.head(40).iterrows():
            out.append([
                _esc(str(r["produto"])[:55]),
                _esc(r.get("marca", "—")),
                _esc(r.get("plataforma", "—")),
                _esc(_fmt_brl(r["price_prev"])),
                _esc(_fmt_brl(r["price_today"])),
                f'<span style="color:{color};font-weight:700;">'
                f'{arrow} {abs(r["delta_pct"]):.1f}%</span>',
            ])
        return out

    badge = (f"{len(shown)} anomalies ≥ {threshold:.1f}% "
             f"(▲ {len(inc)} · ▼ {len(dec)})")
    parts = [
        '<div style="background:#fef9c3;border:1px solid #fde68a;'
        'border-radius:8px;padding:8px 14px;display:inline-block;'
        f'font-size:13px;color:#854d0e;font-weight:600;">{_esc(badge)}</div>'
    ]
    if not inc.empty:
        parts.append('<h3 style="color:#059669;font-size:15px;'
                     'margin:18px 0 4px;">▲ INCREASES</h3>')
        parts.append(_email_table(headers, _rows(inc, True), align))
    if not dec.empty:
        parts.append('<h3 style="color:#dc2626;font-size:15px;'
                     'margin:18px 0 4px;">▼ DECREASES</h3>')
        parts.append(_email_table(headers, _rows(dec, False), align))

    html = _email_shell(
        "🚨 RAC PRICE MONITOR",
        f"Price anomalies — {target_day}",
        f"Day-over-day comparison vs {prev_day} · threshold ≥ "
        f"{threshold:.1f}%",
        "#b91c1c", "#7f1d1d", "".join(parts),
    )

    lines = ["RAC PRICE MONITOR — Price anomalies",
             f"Target day: {target_day}  (vs {prev_day})",
             f"Threshold: >= {threshold:.1f}%", badge.replace("≥", ">="), ""]
    for label, df in (("INCREASES", inc), ("DECREASES", dec)):
        if df.empty:
            continue
        lines.append(label)
        for _, r in df.iterrows():
            lines.append(
                f"  {r['delta_pct']:+.1f}%  {str(r['produto'])[:55]}  "
                f"[{r.get('marca', '—')} / {r.get('plataforma', '—')}]  "
                f"{_fmt_brl(r['price_prev'])} -> {_fmt_brl(r['price_today'])}"
            )
        lines.append("")
    return html, "\n".join(lines)


def page_email_digest() -> None:
    st.title("📧 Email Digest")
    st.markdown(
        "Send a consolidated email with the **Top Movers** and **BuyBox** "
        "snapshot for the active window. The same digest can be sent "
        "automatically on a cron schedule via `send_digest.py` (see Replit "
        "Scheduled Deployments)."
    )

    # Active window — the 7 days ending yesterday
    window_end   = date.today() - timedelta(days=1)
    window_start = window_end - timedelta(days=7)
    prev_end     = window_start - timedelta(days=1)
    prev_start   = prev_end - timedelta(days=7)

    _badge(f"📅 {window_start} → {window_end}")
    st.write("")

    # ── Sidebar refinement ────────────────────────────────────────────────
    with st.sidebar:
        with st.expander("Refine — Email Digest", expanded=True):
            buybox_max_pos = st.slider("BuyBox: positions ≤", 1, 5, 1,
                                       key="dg_buybox_pos")
            top_n_movers   = st.slider("Top movers per direction", 3, 25, 10,
                                       key="dg_top_n")
            min_delta_pct  = st.slider("Min |Δ %| for movers", 0.0, 30.0, 3.0,
                                       step=0.5, format="%.1f%%",
                                       key="dg_min_delta")
            min_records    = st.slider("Min records per SKU per window",
                                       1, 20, 2, key="dg_min_records")
            recipients_raw = st.text_area("Recipients (comma-separated)",
                                          value="", height=80,
                                          key="dg_recipients")
            do_generate    = st.button("📝 Generate digest", type="primary",
                                       use_container_width=True)

    _render_smtp_help("DIGEST_TO")

    if do_generate:
        st.session_state["dg_generated"] = True
    if not st.session_state.get("dg_generated"):
        st.info("Adjust the parameters in the sidebar and click "
                "**📝 Generate digest** to build the email.")
        return

    with st.spinner("Building digest…"):
        df_cur  = _overview_data(str(window_start), str(window_end), (), ())
        df_prev = _overview_data(str(prev_start), str(prev_end), (), ())

    if df_cur.empty:
        st.warning("No records found in the active window.")
        return

    # ── Top movers — median price per SKU, current vs previous window ─────
    ups = downs = pd.DataFrame()
    if not df_prev.empty and {"preco", "produto"}.issubset(df_cur.columns):
        cur_agg = (df_cur.dropna(subset=["preco", "produto"])
                   .groupby("produto")["preco"]
                   .agg(preco_atual="median", obs_atual="count").reset_index())
        prev_agg = (df_prev.dropna(subset=["preco", "produto"])
                    .groupby("produto")["preco"]
                    .agg(preco_anterior="median", obs_anterior="count")
                    .reset_index())
        movers = cur_agg.merge(prev_agg, on="produto", how="inner")
        movers = movers[(movers["obs_atual"] >= min_records)
                        & (movers["obs_anterior"] >= min_records)
                        & (movers["preco_anterior"] > 0)]
        if not movers.empty:
            movers["delta_abs"] = (movers["preco_atual"]
                                   - movers["preco_anterior"])
            movers["delta_pct"] = (movers["delta_abs"]
                                   / movers["preco_anterior"] * 100)
            movers = movers[movers["delta_pct"].abs() >= min_delta_pct]
            ups = (movers[movers["delta_pct"] > 0]
                   .sort_values("delta_pct", ascending=False)
                   .head(top_n_movers))
            downs = (movers[movers["delta_pct"] < 0]
                     .sort_values("delta_pct").head(top_n_movers))

    # ── BuyBox snapshot ───────────────────────────────────────────────────
    buybox = pd.DataFrame()
    if "posicao_geral" in df_cur.columns:
        buybox = df_cur[df_cur["posicao_geral"].notna()
                        & (df_cur["posicao_geral"] <= buybox_max_pos)].copy()

    brand_map: dict = {}
    if "marca" in df_cur.columns:
        brand_map = (df_cur.dropna(subset=["produto"])
                     .groupby("produto")["marca"]
                     .agg(lambda s: (s.dropna().mode().iat[0]
                                     if not s.dropna().mode().empty else "—"))
                     .to_dict())

    bb_by_brand = pd.Series(dtype=int)
    if not buybox.empty and "marca" in buybox.columns:
        bb_by_brand = (buybox.groupby("marca").size()
                       .sort_values(ascending=False))

    # ── KPI strip ─────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Records — window", f"{len(df_cur):,}")
    c2.metric("▲ Movers up", str(len(ups)))
    c3.metric("▼ Movers down", str(len(downs)))
    c4.metric(f"BuyBox ≤ {buybox_max_pos}", f"{len(buybox):,}")
    st.divider()

    # ── Preview tables ────────────────────────────────────────────────────
    def _fmt_movers(df):
        out = df.copy()
        out.insert(0, "Brand", out["produto"].map(brand_map).fillna("—"))
        out = out[["produto", "Brand", "preco_anterior", "preco_atual",
                   "delta_abs", "delta_pct"]]
        out.columns = ["Product / SKU", "Brand", "Prev (R$)", "Now (R$)",
                       "Δ R$", "Δ %"]
        return out

    _money_cfg = {
        "Prev (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
        "Now (R$)":  st.column_config.NumberColumn(format="R$ %.2f"),
        "Δ R$":      st.column_config.NumberColumn(format="R$ %.2f"),
        "Δ %":       st.column_config.NumberColumn(format="%.1f%%"),
    }
    cu, cd = st.columns(2)
    with cu:
        st.subheader("▲ Top Movers Up")
        if ups.empty:
            st.caption("No qualifying upward movers.")
        else:
            st.dataframe(_fmt_movers(ups), use_container_width=True,
                         hide_index=True, column_config=_money_cfg)
    with cd:
        st.subheader("▼ Top Movers Down")
        if downs.empty:
            st.caption("No qualifying downward movers.")
        else:
            st.dataframe(_fmt_movers(downs), use_container_width=True,
                         hide_index=True, column_config=_money_cfg)

    st.subheader(f"🏆 BuyBox Snapshot — positions ≤ {buybox_max_pos}")
    if bb_by_brand.empty:
        st.caption("No BuyBox records in the window.")
    else:
        bb_df = bb_by_brand.reset_index()
        bb_df.columns = ["Brand", "BuyBox slots"]
        st.dataframe(bb_df, use_container_width=True, hide_index=True)

    # ── Build & send the e-mail ───────────────────────────────────────────
    html_body, text_body = _build_digest_email(
        window_start, window_end, buybox_max_pos, brand_map,
        ups, downs, bb_by_brand, len(df_cur),
    )
    _render_send_section(
        html_body, text_body,
        subject=f"RAC Digest — {window_start} → {window_end}",
        filename_stub=f"rac_digest_{window_start}_{window_end}",
        default_to_env="DIGEST_TO",
        send_button_label="Send digest email now",
        state_prefix="dg",
        recipients_raw=recipients_raw,
        recipients_in_section=False,
        show_smtp_help=False,
    )


def page_price_anomalies() -> None:
    st.title("🔔 Price Anomalies")
    st.markdown(
        "Detects per-SKU price changes between two consecutive days. Any "
        "product whose mean price moved by at least the threshold is "
        "reported. The same logic runs daily on a cron via "
        "`send_anomalies.py` (Replit Scheduled Deployments)."
    )

    with st.sidebar:
        with st.expander("Refine — Anomalies", expanded=True):
            target_day = st.date_input(
                "Target day",
                value=date.today() - timedelta(days=1),
                max_value=date.today(),
                format="YYYY/MM/DD",
                key="an_target",
            )
            min_delta_pct = st.number_input(
                "Min |Δ %|", min_value=0.0, max_value=100.0, value=5.0,
                step=1.0, format="%.2f", key="an_min_delta",
                help="Minimum absolute day-over-day price change to flag "
                     "a SKU.",
            )
            direction = st.selectbox(
                "Direction", ["Both", "Increases only", "Decreases only"],
                key="an_direction",
            )

    prev_day = target_day - timedelta(days=1)

    with st.spinner("Loading price records…"):
        df_today = _overview_data(str(target_day), str(target_day), (), ())
        df_prev  = _overview_data(str(prev_day), str(prev_day), (), ())

    def _agg(df):
        if df.empty or not {"preco", "produto"}.issubset(df.columns):
            return pd.DataFrame()
        d = df.dropna(subset=["preco", "produto"]).copy()
        for col in ("marca", "plataforma"):
            if col not in d.columns:
                d[col] = "—"
            d[col] = d[col].fillna("—")
        return (d.groupby(["produto", "marca", "plataforma"])["preco"]
                .agg(price="mean", n="count").reset_index())

    cur, prv = _agg(df_today), _agg(df_prev)

    anomalies = pd.DataFrame()
    if not cur.empty and not prv.empty:
        merged = cur.merge(prv, on=["produto", "marca", "plataforma"],
                           suffixes=("_today", "_prev"))
        merged = merged[merged["price_prev"] > 0].copy()
        merged["delta_abs"] = merged["price_today"] - merged["price_prev"]
        merged["delta_pct"] = (merged["delta_abs"]
                               / merged["price_prev"] * 100)
        anomalies = merged[merged["delta_pct"].abs() >= min_delta_pct].copy()

    n_inc = int((anomalies["delta_pct"] > 0).sum()) if not anomalies.empty else 0
    n_dec = int((anomalies["delta_pct"] < 0).sum()) if not anomalies.empty else 0

    # ── KPI strip ─────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Records — {target_day}", f"{len(df_today):,}")
    c2.metric(f"Records — {prev_day}", f"{len(df_prev):,}")
    c3.metric("▲ Increases", str(n_inc))
    c4.metric("▼ Decreases", str(n_dec))
    st.divider()

    if df_today.empty or df_prev.empty:
        st.warning("Need price records on **both** the target day and the "
                   "previous day to compute anomalies. Pick another day.")
        return

    shown = anomalies.copy()
    if direction == "Increases only":
        shown = shown[shown["delta_pct"] > 0]
    elif direction == "Decreases only":
        shown = shown[shown["delta_pct"] < 0]

    if shown.empty:
        st.success("✅ No price anomalies above the threshold for this day.")
        return

    shown = shown.sort_values("delta_pct", key=lambda s: s.abs(),
                              ascending=False).reset_index(drop=True)

    disp = shown[["produto", "marca", "plataforma", "price_today", "n_today",
                  "price_prev", "n_prev", "delta_abs", "delta_pct"]].copy()
    disp.columns = ["Product", "Brand", "Platform", f"Price {target_day}",
                    "n today", f"Price {prev_day}", "n prev", "Δ R$", "Δ %"]
    st.dataframe(
        disp, use_container_width=True, height=440, hide_index=True,
        column_config={
            f"Price {target_day}":
                st.column_config.NumberColumn(format="R$ %.2f"),
            f"Price {prev_day}":
                st.column_config.NumberColumn(format="R$ %.2f"),
            "Δ R$": st.column_config.NumberColumn(format="R$ %.2f"),
            "Δ %":  st.column_config.NumberColumn(format="%.1f%%"),
        },
    )
    _csv_download_btn(disp, f"rac_anomalies_{target_day}.csv",
                      "⬇️ Export anomalies CSV", key="an_export")

    html_body, text_body = _build_anomaly_email(
        target_day, prev_day, float(min_delta_pct), shown,
    )
    _render_send_section(
        html_body, text_body,
        subject=f"RAC Price Anomalies — {target_day}",
        filename_stub=f"rac_anomalies_{target_day}",
        default_to_env="ANOMALY_TO",
        send_button_label="Send anomalies email now",
        state_prefix="an",
        recipients_in_section=True,
        show_smtp_help=True,
    )


# ---------------------------------------------------------------------------
# Fase 5 — extração de specs técnicas (BTU, ciclo, voltagem) do nome do produto
# ---------------------------------------------------------------------------

_BTU_DOTTED_RE = re.compile(r"(\d{1,2})[.\s](\d{3})\s*btus?\b", re.IGNORECASE)
_BTU_PLAIN_RE  = re.compile(r"\b(\d{4,5})\s*btus?\b", re.IGNORECASE)
_CICLO_QF_RE   = re.compile(r"\bq[/\s.-]?f\b", re.IGNORECASE)
_VOLT_110_RE   = re.compile(r"\b1(?:10|27)\s*v\b", re.IGNORECASE)
_VOLT_220_RE   = re.compile(r"\b220\s*v\b", re.IGNORECASE)


def _extract_btu(produto) -> int | None:
    """Extrai a capacidade em BTU do nome do produto. None se não encontrado."""
    if not produto or not isinstance(produto, str):
        return None
    m = _BTU_DOTTED_RE.search(produto)
    if m:
        return int(m.group(1) + m.group(2))
    m = _BTU_PLAIN_RE.search(produto)
    if m:
        return int(m.group(1))
    return None


def _classify_ciclo(produto) -> str:
    """Classifica o ciclo (Quente/Frio, Só Frio) a partir do nome do produto."""
    if not produto or not isinstance(produto, str):
        return "Não identificado"
    t = produto.lower()
    if ("quente" in t and "frio" in t) or _CICLO_QF_RE.search(t):
        return "Quente/Frio"
    if "frio" in t:
        return "Só Frio"
    return "Não identificado"


def _extract_voltagem(produto) -> str | None:
    """Extrai a voltagem (110V/220V/Bivolt) do nome do produto."""
    if not produto or not isinstance(produto, str):
        return None
    t = produto.lower()
    if "bivolt" in t or "bi-volt" in t:
        return "Bivolt"
    has_110 = bool(_VOLT_110_RE.search(t))
    has_220 = bool(_VOLT_220_RE.search(t))
    if has_110 and has_220:
        return "Bivolt"
    if has_220:
        return "220V"
    if has_110:
        return "110V"
    return None


def _enrich_specs(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona colunas calculadas btu/ciclo a partir do nome do produto."""
    if df.empty or "produto" not in df.columns:
        return df
    df = df.copy()
    df["btu"]   = df["produto"].map(_extract_btu)
    df["ciclo"] = df["produto"].map(_classify_ciclo)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _query_products_history(
    products: tuple, start_str: str, end_str: str
) -> pd.DataFrame:
    """Histórico de coletas para SKUs específicos (cacheado)."""
    if not products:
        return pd.DataFrame()
    return query_coletas(
        date.fromisoformat(start_str),
        date.fromisoformat(end_str),
        products=list(products),
        limit=50000,
    )


# ---------------------------------------------------------------------------
# Fase 5 — Market Analytics (distribuição de preços + presença por marketplace)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Fase 5 — Ficha do Produto + Comparador
# ---------------------------------------------------------------------------

def _render_product_sheet(produto: str, start_date: date, end_date: date) -> None:
    """Renderiza a ficha detalhada de um único SKU."""
    df = _query_products_history((produto,), str(start_date), str(end_date))
    if df.empty:
        st.warning("Sem coletas para este SKU no período selecionado.")
        return

    df = _enrich_specs(df)
    df_price = df.dropna(subset=["preco"])
    df_price = df_price[df_price["preco"] > 0]

    st.subheader(produto)

    # --- Especificações técnicas ---
    btu_series = df["btu"].dropna()
    btu_val    = int(btu_series.mode().iloc[0]) if not btu_series.empty else None
    ciclo_val  = df["ciclo"].mode().iloc[0] if not df["ciclo"].mode().empty else "—"
    marca_mode = df["marca"].dropna().mode() if "marca" in df.columns else pd.Series([])
    marca_val  = marca_mode.iloc[0] if not marca_mode.empty else "—"
    volt_mode  = df["produto"].map(_extract_voltagem).dropna().mode()
    volt_val   = volt_mode.iloc[0] if not volt_mode.empty else "—"

    spec_cols = st.columns(4)
    spec_cols[0].metric("Marca", marca_val)
    spec_cols[1].metric(
        "Capacidade",
        f"{btu_val:,} BTU".replace(",", ".") if btu_val else "—",
    )
    spec_cols[2].metric("Ciclo", ciclo_val)
    spec_cols[3].metric("Voltagem", volt_val)

    # --- Contadores ---
    cnt_cols = st.columns(4)
    cnt_cols[0].metric("Total de coletas", f"{len(df):,}")
    cnt_cols[1].metric("Marketplaces", int(df["plataforma"].nunique()))
    cnt_cols[2].metric(
        "Sellers",
        int(df["seller"].nunique()) if "seller" in df.columns else 0,
    )
    cnt_cols[3].metric(
        "Menor preço",
        _fmt_brl(df_price["preco"].min()) if not df_price.empty else "—",
    )

    if df_price.empty:
        st.info("Sem dados de preço para este SKU no período.")
        return

    st.divider()

    # --- Evolução de preço por marketplace ---
    agg = df_price.groupby(["data", "plataforma"], as_index=False)["preco"].median()
    agg["data"] = pd.to_datetime(agg["data"])
    fig = px.line(
        agg, x="data", y="preco", color="plataforma", markers=True,
        title="Evolução de preço por marketplace",
        labels={"data": "Data", "preco": "Preço (R$)", "plataforma": "Plataforma"},
    )
    fig.update_traces(line=dict(width=2.5), marker=dict(size=6))
    _apply_chart_style(fig, height=420)
    st.plotly_chart(fig, use_container_width=True)

    # --- Sellers por marketplace ---
    if "seller" in df_price.columns:
        st.markdown("**Sellers por marketplace**")
        sellers = (
            df_price.sort_values("data")
            .groupby(["plataforma", "seller"], as_index=False)
            .agg(
                ultimo_preco=("preco", "last"),
                menor_preco=("preco", "min"),
                coletas=("preco", "count"),
            )
            .sort_values("ultimo_preco")
        )
        st.dataframe(
            sellers, use_container_width=True, hide_index=True,
            column_config={
                "plataforma":   st.column_config.TextColumn("Marketplace"),
                "seller":       st.column_config.TextColumn("Seller"),
                "ultimo_preco": st.column_config.NumberColumn("Último preço", format="R$ %.2f"),
                "menor_preco":  st.column_config.NumberColumn("Menor preço", format="R$ %.2f"),
                "coletas":      st.column_config.NumberColumn("Coletas"),
            },
        )


def _render_comparator(produtos: tuple, start_date: date, end_date: date) -> None:
    """Renderiza a comparação lado a lado de 2–4 SKUs."""
    df = _query_products_history(produtos, str(start_date), str(end_date))
    if df.empty:
        st.warning("Sem coletas para os produtos selecionados.")
        return

    df_price = df.dropna(subset=["preco"])
    df_price = df_price[df_price["preco"] > 0]
    if df_price.empty:
        st.warning("Sem dados de preço para os produtos selecionados.")
        return

    # --- Evolução sobreposta ---
    agg = df_price.groupby(["data", "produto"], as_index=False)["preco"].median()
    agg["data"] = pd.to_datetime(agg["data"])
    fig = px.line(
        agg, x="data", y="preco", color="produto", markers=True,
        title="Evolução de preço comparada (mediana diária)",
        labels={"data": "Data", "preco": "Preço (R$)", "produto": "Produto"},
    )
    fig.update_traces(line=dict(width=2.5), marker=dict(size=6))
    _apply_chart_style(fig, height=460)
    st.plotly_chart(fig, use_container_width=True)

    # --- Menor preço por marketplace ---
    pivot = df_price.groupby(["produto", "plataforma"])["preco"].min().unstack()
    st.markdown("**Menor preço por marketplace**")
    st.dataframe(
        pivot.style.format("R$ {:.2f}", na_rep="—"),
        use_container_width=True,
    )

    # --- Resumo comparativo + diferença percentual ---
    summary = (
        df_price.groupby("produto")["preco"]
        .agg(menor="min", mediana="median", maior="max")
        .reset_index()
    )
    cheapest = summary["menor"].min()
    summary["dif_%_vs_menor"] = (
        (summary["menor"] - cheapest) / cheapest * 100
    ).round(1)
    summary = summary.sort_values("menor")
    st.markdown("**Resumo comparativo**")
    st.dataframe(
        summary, use_container_width=True, hide_index=True,
        column_config={
            "produto":        st.column_config.TextColumn("Produto"),
            "menor":          st.column_config.NumberColumn("Menor", format="R$ %.2f"),
            "mediana":        st.column_config.NumberColumn("Mediana", format="R$ %.2f"),
            "maior":          st.column_config.NumberColumn("Maior", format="R$ %.2f"),
            "dif_%_vs_menor": st.column_config.NumberColumn("Δ% vs mais barato", format="%.1f%%"),
        },
    )


def page_product_sheet() -> None:
    st.title("🗂️ Ficha do Produto")
    st.caption("Detalhamento técnico e de preços por SKU, com comparador.")

    with st.sidebar:
        st.subheader("Filtros")
        date_range = st.date_input(
            "Período",
            value=(date.today() - timedelta(days=30), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="ps_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=30)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()
        sel_brands = st.multiselect(
            "Marcas (filtra a lista de SKUs)", opts["brands"], key="ps_brands",
        )
        sel_btu = st.multiselect(
            "Capacidade (BTU)", BTU_OPTIONS,
            format_func=lambda x: f"{int(x):,} BTUs".replace(",", "."),
            key="ps_btu",
        )

    sku_opts = get_sku_options(
        tuple(sorted(sel_brands)), tuple(sorted(sel_btu)), (),
    )

    tab_ficha, tab_cmp = st.tabs(["📋 Ficha individual", "⚖️ Comparador"])

    with tab_ficha:
        if not sku_opts:
            st.info("Nenhum SKU disponível. Ajuste os filtros na barra lateral.")
        else:
            sku = st.selectbox(
                f"Produto / SKU  ({len(sku_opts)} disponíveis)",
                sku_opts, key="ps_sku",
            )
            if sku:
                _render_product_sheet(sku, start_date, end_date)

    with tab_cmp:
        if not sku_opts:
            st.info("Nenhum SKU disponível. Ajuste os filtros na barra lateral.")
        else:
            sel = st.multiselect(
                "Selecione 2 a 4 produtos para comparar",
                sku_opts, max_selections=4, key="ps_cmp",
            )
            if len(sel) < 2:
                st.info("Selecione ao menos 2 produtos para comparar.")
            else:
                _render_comparator(tuple(sel), start_date, end_date)


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

_render_cobertura_banner()
PAGES[st.session_state["_current_page"]]()
