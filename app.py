"""
app.py — RAC Price Monitor Dashboard

Usage (local):
    streamlit run app.py

Usage (remote access):
    streamlit run app.py --server.address=0.0.0.0 --server.port=8501
    Then open: http://<your-ip>:8501
"""

import base64
import json
import os
import re
from dataclasses import dataclass
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


def _mode_price(s: pd.Series) -> float:
    # Bucketize to R$1 to make mode meaningful on continuous price data;
    # fall back to median when no bucket repeats (e.g. tiny samples).
    s_clean = pd.to_numeric(s, errors="coerce").dropna()
    if s_clean.empty:
        return float("nan")
    buckets = s_clean.round(0)
    counts = buckets.value_counts()
    if counts.empty or int(counts.iloc[0]) <= 1:
        return float(s_clean.median())
    top_count = int(counts.iloc[0])
    top_buckets = set(counts[counts == top_count].index)
    return float(s_clean[buckets.isin(top_buckets)].median())


# ---------------------------------------------------------------------------
# Price Evolution — métricas de preço por (SKU, dia)
#
# Substitui a antiga lógica "sempre moda, agrupado por NOME de produto".
# A moda mascarava o piso real (uma validação read-only mostrou pricetrack
# do SKU 42EZVQA12M5 travado em R$2.999,90 na moda enquanto o buy box
# estava em R$2.199–2.289). Cada métrica define:
#   - pt_col: coluna bruta do pricetrack_daily usada como base por linha
#     (Buy Box/Moda/Mediana sobre `min_price`; Médio sobre `avg_price`);
#   - agg:    função de agregação por (sku, dia) sobre a base;
#   - title/ylabel: copy dinâmica do gráfico.
# Em `coletas` a base é sempre o `preco` bruto.
# ---------------------------------------------------------------------------

_PRICE_METRICS: dict[str, dict] = {
    "Buy Box (menor preço)": dict(
        pt_col="min_price", agg="min", short="Buy Box",
        title="Buy Box Price Evolution", ylabel="Buy Box (R$)",
    ),
    "Moda (teto 3P / MAP)": dict(
        pt_col="min_price", agg=_mode_price, short="Moda",
        title="Modal Price Evolution (teto 3P/MAP)", ylabel="Modal Price (R$)",
    ),
    "Mediana": dict(
        pt_col="min_price", agg="median", short="Mediana",
        title="Median Price Evolution", ylabel="Median Price (R$)",
    ),
    "Preço médio": dict(
        pt_col="avg_price", agg="mean", short="Médio",
        title="Average Price Evolution", ylabel="Average Price (R$)",
    ),
}


def _metric_basis(df: pd.DataFrame, pt_col: str) -> pd.Series:
    """Preço-base por linha para a métrica escolhida.

    `pricetrack` usa a coluna bruta indicada (`min_price`/`avg_price`);
    `coletas` usa o `preco` cru. Linhas de pricetrack sem a coluna bruta
    (importações antigas) caem de volta no `preco` (= mode_price).
    """
    base = pd.to_numeric(df.get("preco"), errors="coerce")
    if pt_col in df.columns and "source" in df.columns:
        is_pt = df["source"].astype("string") == "pricetrack"
        pt_vals = pd.to_numeric(df[pt_col], errors="coerce")
        base = base.mask(is_pt, pt_vals.where(pt_vals.notna(), base))
    return base


def _is_placeholder_price(p) -> bool:
    """Detecta preços-placeholder (cluster terminando em 999,00 ou 9999).

    Lojas usam valores como R$2.999,00 / R$12.999,00 / R$19.999 como
    "preço de gaveta" quando o item está indisponível. Sem filtrar, eles
    inflam a média e estouram o eixo Y do gráfico.
    """
    try:
        v = float(p)
    except (TypeError, ValueError):
        return False
    if v != v:  # NaN
        return False
    ip = int(round(v))
    cents = round((v - int(v)) * 100)
    if cents == 0 and ip % 1000 == 999:      # ...999,00 (R$2.999,00)
        return True
    if ip % 10000 == 9999:                    # ...9999 (R$19.999)
        return True
    return False


def _norm_platform_key(name) -> str:
    """Chave canônica de plataforma p/ casar fontes (Apêndice A do relatório).

    upper + remoção de acentos + remoção de não-alfanuméricos + aliases.
    Ex.: "Magazine Luiza" e "MAGALU" → "MAGALU"; "Eletrozema" → "ZEMA".
    """
    if name is None:
        return ""
    s = str(name).upper().strip()
    if not s:
        return ""
    acc = "ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ"
    pln = "AAAAAEEEEIIIIOOOOOUUUUC"
    s = s.translate(str.maketrans(acc, pln))
    s = re.sub(r"[^A-Z0-9]", "", s)
    _aliases = {
        "MAGAZINELUIZA": "MAGALU",
        "ELETROZEMA":    "ZEMA",
    }
    return _aliases.get(s, s)


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
# PriceTrack ↔ coletas platform names
#
# O PriceTrack publica o marketplace em CAIXA ALTA com espaços
# ("MERCADO LIVRE", "MAGAZINE LUIZA"); as coletas usam o nome canônico
# ("Mercado Livre", "Magalu"). Sem reconciliar isso, (a) o filtro de
# plataforma no PriceTrack não casa (ex.: "Magalu" nunca bate em
# "MAGAZINE LUIZA", então o preço do PriceTrack é ignorado) e (b) o merge
# duplica o mesmo marketplace em duas categorias nos gráficos agrupados
# por plataforma. Mapa explícito p/ os casos que `.title()` não resolve;
# o restante cai no fallback title-case + `_normalize_platform`.
# ---------------------------------------------------------------------------
_PT_TO_CANONICAL_PLATFORM = {
    # 7 marketplaces no foco
    "MERCADO LIVRE":  "Mercado Livre",
    "AMAZON":         "Amazon",
    "MAGAZINE LUIZA": "Magalu",
    "CASAS BAHIA":    "Casas Bahia",
    "LEROY MERLIN":   "Leroy Merlin",
    "SHOPEE":         "Shopee",
    # dealers (fora do foco, mas presentes nas coletas — evita split)
    "FERREIRA COSTA": "FerreiraCosta",
    "WEB CONTINENTAL": "WebContinental",
    "CENTRAL AR":     "CentralAr",
    "POLO AR":        "PoloAr",
    "FRIOPEÇAS":      "FrioPecas",
    "CLIMA RIO":      "Climario",
    "G BARBOSA":      "GBarbosa",
    "ADIAS":          "ADias",
    "AR CERTO":       "ArCerto",
    "FRIGELAR":       "Frigelar",
    "DUFRIO":         "Dufrio",
    "LEVEROS":        "Leveros",
    "BEMOL":          "Bemol",
    "FUJIOKA":        "Fujioka",
}

# Inverso (canônico → variantes do PriceTrack) para o filtro de plataforma.
_CANONICAL_TO_PT_PLATFORM: dict[str, list[str]] = {}
for _pt_name, _canon in _PT_TO_CANONICAL_PLATFORM.items():
    _CANONICAL_TO_PT_PLATFORM.setdefault(_canon, []).append(_pt_name)


def _normalize_pt_platform(raw) -> str | None:
    """Marketplace do PriceTrack (CAIXA ALTA) → nome canônico das coletas."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    mapped = _PT_TO_CANONICAL_PLATFORM.get(s.upper())
    if mapped:
        return mapped
    # Fallback: title-case ("CARREFOUR" → "Carrefour") + correção de typos.
    return _normalize_platform(s.title())


def _pt_platform_match_values(platforms: list[str]) -> list[str]:
    """Valores crus do PriceTrack a casar para as plataformas canônicas dadas.

    Cobre o nome em CAIXA ALTA mapeado (ex.: "Magalu" → "MAGAZINE LUIZA"),
    além do próprio nome em upper como rede de segurança ("AMAZON").
    """
    out: set[str] = set()
    for p in platforms:
        out.update(_CANONICAL_TO_PT_PLATFORM.get(p, []))
        out.add(p.upper())
        out.add(p)
    return sorted(out)

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


# TODO(segurança, fora do escopo do módulo Price Evolution): a validação
# read-only de 2026-06-16 apontou RLS DESABILITADO em todas as tabelas do
# schema public (a anon key lê/escreve tudo: coletas, pricetrack_daily, etc).
# Habilitar Row Level Security + policies por tabela. Ver docs/SECURITY_TODO_RLS.md.
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
    """Build brand normalization maps from config + normalize_product.

    Importers (e.g. PriceTrack since 2026-05-24) sometimes write title-case
    values like "Lg"/"Tcl" alongside the canonical "LG"/"TCL", so we register
    upper / lower / title / capitalize variants of every known alias to
    collapse them into a single canonical entry in the UI dropdown and to
    expand a single user selection back into every DB case variant.
    """
    try:
        from utils.normalize_product import _BRAND_ALIASES
        from config import BRANDS
    except Exception:
        return {}, {}

    canonical_to_raws: dict = {}
    raw_to_canonical:  dict = {}

    def _register(raw_value: str, canonical: str) -> None:
        if raw_value in raw_to_canonical:
            return
        raw_to_canonical[raw_value] = canonical
        canonical_to_raws.setdefault(canonical, []).append(raw_value)

    for raw_brand in BRANDS:
        canonical = _BRAND_ALIASES.get(raw_brand.lower(), raw_brand)
        _register(raw_brand, canonical)

    for alias, canonical in _BRAND_ALIASES.items():
        for variant in {alias, alias.upper(), alias.lower(),
                        alias.title(), alias.capitalize()}:
            _register(variant, canonical)

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


@st.cache_data(ttl=3600, show_spinner=False)
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


@st.cache_data(ttl=3600, show_spinner=False)
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


@st.cache_data(ttl=3600, show_spinner=False)
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


@st.cache_data(ttl=3600, show_spinner=False)
def get_sku_resolvido_options(familias: tuple = ()) -> list:
    """SKUs do catálogo filtrados pelas famílias selecionadas."""
    cat = get_catalogo()
    if cat.empty:
        return []
    df = cat
    if familias:
        df = df[df["familia"].isin(list(familias))]
    return sorted(df["sku"].dropna().unique().tolist())


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


# ---------------------------------------------------------------------------
# Page-level sidebar helper: filtros de Família e SKU do catálogo
# Use após renderizar o multiselect de Brands de uma página, passando o
# `sel_brands` para fazer cascata. Retorna (familias, skus) prontos para
# passar a query_coletas como `familias_resolvidas` e `skus_resolvidos`.
# `page_key` distingue as chaves de widget por página (Results, BuyBox, …).
# ---------------------------------------------------------------------------

def _render_familia_sku_filters(sel_brands: list, page_key: str,
                                 estados: tuple = ("MAPEADO",)) -> tuple[list, list]:
    """Renderiza multiselects de Família e SKU; retorna (familias, skus)."""
    brands_upper = tuple(b.upper() for b in (sel_brands or []))
    fam_opts = get_familia_options(brands_upper, tuple(estados))
    sel_fam = st.multiselect(
        "Família (catálogo)",
        fam_opts,
        format_func=_familia_display,
        placeholder="Todas as famílias",
        key=f"{page_key}__familias",
        help="Famílias normalizadas. Cascata: filtra pelas marcas acima.",
    )
    sku_opts = get_sku_resolvido_options(tuple(sel_fam))
    sel_sku = st.multiselect(
        "SKU do catálogo",
        sku_opts,
        placeholder="Todos os SKUs",
        key=f"{page_key}__skus_resolvidos",
        help="Cascata: lista os SKUs da(s) família(s) selecionada(s). "
             "O filtro expande para incluir todas as linhas da mesma "
             "linha comercial+BTU+ciclo (ex.: pegar Ecomaster 9000 220V "
             "também traz 110V e nomes coletados sem voltagem).",
    )
    return sel_fam, sel_sku


def _apply_sku_filter_with_expansion(q, skus: list):
    """Aplica filtro de SKU expandindo para `familia_linha` + voltagem.

    Regra de negócio: quando o nome coletado não especifica voltagem,
    assume 220V (default para todas as marcas mapeadas). A coluna
    `coletas.voltagem_resolvida` reflete isso (110V/220V/BI).

    No catálogo, SKUs do mesmo conjunto comercial (UNIDADE CONDENSADORA
    38xxx + EVAPORADORA 42xxx) compartilham `familia_linha`. Pegar
    qualquer um deles deve retornar o mesmo cohort de preços.

    Estratégia: para os SKUs pickados, deriva (familia_linha, voltagem)
    e filtra por isso. Ex.: SKU 42EZVCA09M5 (Ecomaster 9000 220V) →
    familia_resolvida='MIDEA-ECOMASTER-9000-F' AND voltagem_resolvida='220V'
    → retorna 3.790 linhas (todas Ecomaster 9000 220V, incluindo
    a condensadora 38EZVCA09M5 do mesmo par). Sem `OR` que disparou
    timeout 57014 no passado.
    """
    cat = get_catalogo()
    if cat.empty or "familia_linha" not in cat.columns:
        return q.in_("sku_resolvido", skus)
    picked = cat[cat["sku"].isin(skus)]
    fam_linhas = picked["familia_linha"].dropna().unique().tolist()
    voltagens  = (picked["voltagem"].dropna().unique().tolist()
                  if "voltagem" in picked.columns else [])
    if fam_linhas:
        q = q.in_("familia_resolvida", fam_linhas)
        if voltagens:
            q = q.in_("voltagem_resolvida", voltagens)
        return q
    return q.in_("sku_resolvido", skus)


@st.cache_data(ttl=1800, show_spinner=False)
def get_cobertura_resolucao() -> dict:
    """Conta linhas de coletas por estado_match — usado no banner do topo."""
    client = _get_supabase()
    if client is None:
        return {}
    try:
        rpc = client.rpc("get_cobertura_resolucao").execute()
        data = rpc.data if isinstance(rpc.data, dict) else (rpc.data[0] if rpc.data else None)
        if data:
            return {k: int(v or 0) for k, v in data.items()}
    except Exception:
        pass
    return {}


@st.cache_data(ttl=1800, show_spinner=False)
def get_mapeado_sem_sku() -> int:
    """Linhas de coletas MAPEADO sem sku_resolvido — gap de reconciliação.

    Essas linhas têm família resolvida mas não participam do cruzamento
    PT × Coletas, do filtro de produto nem da precedência de preço
    (achado da validação de 09/07/2026: 70.441 linhas na janela 01/06–09/07).
    A etapa 🔢 Backfill de SKU da automação trabalha para zerá-las.
    """
    client = _get_supabase()
    if client is None:
        return 0
    try:
        return (client.table("coletas")
                .select("id", count="exact", head=True)
                .eq("estado_match", "MAPEADO")
                .is_("sku_resolvido", "null")
                .execute().count or 0)
    except Exception:
        return 0


@st.cache_data(ttl=1800, show_spinner=False)
def get_sku_proposals() -> pd.DataFrame:
    """Propostas de SKU (confiança alta) p/ nomes MAPEADO sem SKU no de-para."""
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()
    try:
        from utils.admin_automation import get_sku_backfill_proposals
        return pd.DataFrame(get_sku_backfill_proposals(client))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def get_variant_suspects(days: int = 30, delta_pct: float = 25.0,
                         min_dias: int = 5) -> pd.DataFrame | None:
    """SKUs com |Δ piso| Coletas vs PriceTrack persistente — suspeitos de
    de-para de variante errada. None = RPC ausente (migration 010 não aplicada)."""
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()
    try:
        resp = client.rpc("depara_suspeitos_variante", {
            "p_days": days, "p_delta_pct": delta_pct, "p_min_dias": min_dias,
        }).execute()
        return pd.DataFrame(resp.data or [])
    except Exception:
        return None


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
    familias_resolvidas: list[str] | None = None,
    skus_resolvidos: list[str] | None = None,
    estados_match: list[str] | None = None,
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

    # Filtro global de Fonte de Dados: se "Coletas (Python)" estiver desligada,
    # esta fonte não contribui com nenhuma linha (silencioso — é um recorte
    # intencional, não erro). A precedência/merge em query_price_evolution_data
    # herda isto automaticamente.
    if "coletas" not in _gf_sources():
        return pd.DataFrame()

    def _build_q():
        """Fresh filtered query (no cursor yet — added per-page in the loop).

        Composite keyset by (data desc, id desc): ordering only by `id desc`
        forced the planner to backward-scan `coletas_pkey` and filter row by
        row, blowing through statement_timeout on sparse predicates (e.g.
        marca=Midea on a 56-day window). With `data desc` as the leading
        key, the planner picks `idx_coletas_data_turno_plat` and each page
        stays fast regardless of depth.
        """
        q = (
            client.table("coletas")
            .select("*")
            .gte("data", str(start_date))
            .lte("data", str(end_date))
            .order("data", desc=True)
            .order("id", desc=True)
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

        # Camada nova: filtros resolvidos (estado/família/SKU).
        # Argumentos explícitos da página tomam precedência sobre o
        # filtro global (sidebar "Filtros Globais"). Default = só MAPEADO.
        final_estados  = estados_match       if estados_match       is not None else _gf_estados()
        final_familias = familias_resolvidas if familias_resolvidas is not None else _gf_familias()
        final_skus     = skus_resolvidos     if skus_resolvidos     is not None else _gf_skus_resolvidos()
        if final_estados:
            q = q.in_("estado_match", final_estados)
        if final_familias:
            q = q.in_("familia_resolvida", final_familias)
        if final_skus:
            q = _apply_sku_filter_with_expansion(q, final_skus)
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
        last_data: str | None = None
        last_id: int | None = None
        while len(all_data) < limit:
            fetch = min(_SUPABASE_PAGE, limit - len(all_data))
            q = _build_q()
            if last_data is not None and last_id is not None:
                # Composite keyset (data, id) < (last_data, last_id) expressed
                # as a PostgREST or-group: rows from older dates OR rows on
                # the same date with a smaller id.
                q = q.or_(
                    f"data.lt.{last_data},"
                    f"and(data.eq.{last_data},id.lt.{last_id})"
                )
            resp = q.limit(fetch).execute()
            if not resp.data:
                break
            all_data.extend(resp.data)
            if len(resp.data) < fetch:
                break  # server returned fewer rows than requested → last page
            last_row = resp.data[-1]
            last_data = str(last_row.get("data")) if last_row.get("data") else None
            last_id = last_row.get("id")
            if last_data is None or last_id is None:
                break  # safety: cursor unusable without (data, id)

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        df["data"] = pd.to_datetime(df["data"]).dt.date
        for col in ["posicao_organica", "posicao_patrocinada", "posicao_geral",
                    "qtd_avaliacoes", "qtd_sellers"]:
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


_SUPABASE_PAGE_PT = 1000


def _collect_pt_skus(
    products: list[str] | None = None,
    familias_resolvidas: list[str] | None = None,
    skus_resolvidos: list[str] | None = None,
) -> set[str]:
    """Resolve filtros do dashboard para o conjunto de SKUs do catálogo.

    `pricetrack_daily.sku` guarda o código canônico do catálogo
    (ex.: ``42EZVCA09M5``). Os filtros que chegam aqui podem vir como:
    - `products`: nomes free-text vindos do dropdown da coleta (ex.
      ``"Ar Condicionado Midea AI Ecomaster 9.000 BTUs Inverter Frio"``).
      Resolvemos via `produtos_depara_nome.nome_coletado`.
    - `skus_resolvidos`: códigos do catálogo já picados pelo usuário.
      Expande para outras voltagens/condensadora da mesma `familia_linha`
      pra manter paridade com `_apply_sku_filter_with_expansion` (coletas).
    - `familias_resolvidas`: famílias do catálogo (reais ou genéricas).
      Reais → busca em `produtos_catalogo.familia`. Genéricas (ex.
      ``MIDEA-ECOMASTER-9000-F``) não existem no catálogo, então caímos
      no de-para — qualquer linha com aquela família traz o SKU.

    Retorna o conjunto vazio quando nenhum filtro foi passado *e* o
    chamador precisa decidir se segue sem filtro de SKU.
    """
    skus: set[str] = set()
    cat = get_catalogo()
    catalog_skus = (
        set(cat["sku"].astype(str).tolist()) if not cat.empty else set()
    )

    if skus_resolvidos:
        if not cat.empty and "familia_linha" in cat.columns:
            picked = cat[cat["sku"].isin(skus_resolvidos)]
            fam_linhas = picked["familia_linha"].dropna().unique().tolist()
            picked_voltagens = (
                picked["voltagem"].dropna().unique().tolist()
                if "voltagem" in picked.columns else []
            )
            if fam_linhas:
                pool = cat[cat["familia_linha"].isin(fam_linhas)]
                # Match `_apply_sku_filter_with_expansion` (coletas): pegar
                # SKU 220V não deve trazer a versão 110V — voltagem é eixo
                # de comparação. Se a seleção mistura voltagens (ou nenhuma
                # foi resolvida), libera tudo na familia_linha.
                if picked_voltagens and "voltagem" in pool.columns:
                    pool = pool[pool["voltagem"].isin(picked_voltagens)]
                skus.update(pool["sku"].astype(str).tolist())
            else:
                skus.update(str(s) for s in skus_resolvidos)
        else:
            skus.update(str(s) for s in skus_resolvidos)

    if familias_resolvidas:
        if not cat.empty:
            matched = cat[cat["familia"].isin(familias_resolvidas)]["sku"]
            skus.update(matched.astype(str).tolist())
        depara = get_depara()
        if not depara.empty:
            mapped = depara[
                depara["familia"].isin(familias_resolvidas)
                & depara["sku"].notna()
            ]
            skus.update(mapped["sku"].astype(str).tolist())

    if products:
        depara = get_depara()
        if not depara.empty:
            mapped = depara[
                depara["nome_coletado"].isin(products)
                & depara["sku"].notna()
            ]
            skus.update(mapped["sku"].astype(str).tolist())
        # Backward-compat: aceita códigos de SKU passados direto em `products`
        for p in products:
            if isinstance(p, str) and p in catalog_skus:
                skus.add(p)

    return skus


def query_pricetrack_daily(
    start_date: date,
    end_date: date,
    platforms: list[str] | None = None,
    brands: list[str] | None = None,
    sellers: list[str] | None = None,
    products: list[str] | None = None,
    btu_filter: list[str] | None = None,
    product_types: list[str] | None = None,
    familias_resolvidas: list[str] | None = None,
    skus_resolvidos: list[str] | None = None,
    turnos: list[str] | None = None,
    limit: int = 200000,
) -> pd.DataFrame:
    """Query the pricetrack_daily table and remap to the coletas schema.

    Returned columns mirror coletas so the rest of the dashboard pipeline
    works transparently: data, turno, plataforma, marca, produto, preco,
    seller, keyword, posicao_geral, posicao_organica, tag.

    Filters that don't apply (keyword/turno/posições) are silently ignored.
    Marketplace/seller matching is case-insensitive (pricetrack uses
    uppercase; coletas uses title-case).
    """
    # Sinaliza o estado da última consulta PT para a UI distinguir
    # "a query falhou (ex.: statement_timeout)" de "o PriceTrack realmente
    # não cobre o dia". Sem isso, um timeout devolvia DataFrame vazio e a
    # página concluía, erroneamente, que faltava dado no PriceTrack.
    st.session_state["pt_query_error"] = None
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()

    # Filtro global de Fonte de Dados: se "PriceTrack" estiver desligada,
    # esta fonte não contribui com nenhuma linha. O merge em
    # query_price_evolution_data passa a refletir só as coletas.
    if "pricetrack" not in _gf_sources():
        return pd.DataFrame()

    # Resolve qualquer filtro de produto / família / SKU vindo da página
    # para o conjunto canônico de SKUs do catálogo que existe em
    # `pricetrack_daily.sku`. Antes desta tradução, o filtro tentava
    # `title.eq` / `sku.eq` com nomes de coletas — pricetrack publica o
    # mesmo SKU sob dezenas de títulos diferentes por seller, então o
    # match nunca casava e o df voltava vazio.
    sku_set = _collect_pt_skus(
        products=products,
        familias_resolvidas=familias_resolvidas,
        skus_resolvidos=skus_resolvidos,
    )
    # Filtros pedidos mas resolução vazia → devolve cedo para evitar
    # puxar a janela inteira sem critério de produto.
    if (products or familias_resolvidas or skus_resolvidos) and not sku_set:
        return pd.DataFrame()

    def _build_q():
        q = (
            client.table("pricetrack_daily")
            .select(
                "collection_date,turno,brand,sku,title,marketplace,seller,"
                "min_price,avg_price,mode_price,max_price,id"
            )
            .gte("collection_date", str(start_date))
            .lte("collection_date", str(end_date))
            .order("collection_date", desc=True)
            .order("id", desc=True)
        )
        # Turno: por padrão só o agregado "Diário" (1 linha por data/grupo,
        # comportamento histórico). A página Daily Price Vision opta pelos
        # turnos passando turnos=["Diário", "Manhã", "Tarde"].
        if turnos:
            q = q.in_("turno", turnos)
        else:
            q = q.eq("turno", "Diário")
        if brands:
            # _expand_brands already registers upper/lower/title/capitalize
            # variants of every alias, so the pricetrack uppercase values
            # ("MIDEA") are covered when the user picks "Midea".
            q = q.in_("brand", _expand_brands(brands))
        if platforms:
            # marketplace is uppercase in pricetrack_daily ("MERCADO LIVRE",
            # "MAGAZINE LUIZA"), while sel_platforms uses canonical names
            # ("Mercado Livre", "Magalu"). Map each canonical name to its
            # PriceTrack raw value(s) so the filter actually matches.
            parts = [f"marketplace.ilike.{v}"
                     for v in _pt_platform_match_values(platforms)]
            if parts:
                q = q.or_(",".join(parts))
        if sellers:
            parts = [f"seller.ilike.{s}" for s in sellers]
            if parts:
                q = q.or_(",".join(parts))
        if sku_set:
            q = q.in_("sku", sorted(sku_set))
        if btu_filter:
            parts = []
            for btu in btu_filter:
                parts.append(f"title.ilike.%{btu}%")
                try:
                    dotted = f"{int(btu):,}".replace(",", ".")
                    if dotted != btu:
                        parts.append(f"title.ilike.%{dotted}%")
                except ValueError:
                    pass
            if parts:
                q = q.or_(",".join(parts))
        if product_types:
            parts = []
            for label in product_types:
                for pat in PRODUCT_TYPE_OPTIONS.get(label, [label]):
                    parts.append(f"title.ilike.%{pat}%")
            if parts:
                q = q.or_(",".join(parts))
        return q

    try:
        all_data: list = []
        last_date: str | None = None
        last_id: int | None = None
        while len(all_data) < limit:
            fetch = min(_SUPABASE_PAGE_PT, limit - len(all_data))
            q = _build_q()
            if last_date is not None and last_id is not None:
                q = q.or_(
                    f"collection_date.lt.{last_date},"
                    f"and(collection_date.eq.{last_date},id.lt.{last_id})"
                )
            resp = q.limit(fetch).execute()
            if not resp.data:
                break
            all_data.extend(resp.data)
            if len(resp.data) < fetch:
                break
            last_row = resp.data[-1]
            last_date = str(last_row.get("collection_date")) if last_row.get("collection_date") else None
            last_id = last_row.get("id")
            if last_date is None or last_id is None:
                break

        if not all_data:
            return pd.DataFrame()

        raw = pd.DataFrame(all_data)
        preco = pd.to_numeric(raw.get("mode_price"), errors="coerce")
        # Fallback chain for missing mode: avg → min → max
        preco = preco.fillna(pd.to_numeric(raw.get("avg_price"), errors="coerce"))
        preco = preco.fillna(pd.to_numeric(raw.get("min_price"), errors="coerce"))
        preco = preco.fillna(pd.to_numeric(raw.get("max_price"), errors="coerce"))

        # Canonicalise produto pelo SKU do catálogo: PriceTrack publica
        # o mesmo SKU sob dezenas de títulos diferentes por seller (342
        # títulos distintos pra ~25 SKUs em Midea 9000 BTUs), o que
        # inflaria o cartão "Unique SKUs" e desenharia uma linha por
        # anúncio em vez de uma por produto. Quando o catálogo tem o
        # `produto` cadastrado pra esse SKU, usamos o nome amigável
        # (ex.: "AR CONDICIONADO SPLIT 9000 BTU FRIO AI ECOMASTER ...")
        # para o gráfico não ficar com códigos crus na legenda; fallback
        # pro código do SKU e, em último caso, pro título do anúncio.
        sku_series   = raw.get("sku")
        title_series = raw.get("title")
        catalog = get_catalogo()
        sku_to_produto: dict = {}
        if not catalog.empty and "produto" in catalog.columns:
            sku_to_produto = dict(zip(
                catalog["sku"].astype(str),
                catalog["produto"].astype(str),
            ))
        if sku_series is not None:
            sku_str = sku_series.astype(str)
            friendly = sku_str.map(sku_to_produto)
            produto = friendly.where(friendly.notna(), sku_series)
            produto = produto.where(
                sku_series.notna() & (sku_str.str.strip() != ""),
                title_series,
            )
        else:
            produto = title_series

        # Back-compat: linhas "Diário" continuam saindo como "PriceTrack"
        # (sentinela que _TURNO_TO_PERIODO mapeia para o período "Diário");
        # Manhã/Tarde saem com o próprio rótulo para alimentar os turnos.
        if "turno" in raw.columns:
            turno_out = raw["turno"].where(raw["turno"] != "Diário", "PriceTrack")
        else:
            turno_out = pd.Series(["PriceTrack"] * len(raw), index=raw.index)

        df = pd.DataFrame({
            "data":             pd.to_datetime(raw["collection_date"]).dt.date,
            "turno":            turno_out,
            "plataforma":       raw["marketplace"].map(_normalize_pt_platform)
                                if "marketplace" in raw.columns else pd.NA,
            "marca":            raw.get("brand"),
            "produto":          produto,
            "sku":              sku_series,
            "title":            title_series,
            "preco":            preco,
            "seller":           raw.get("seller"),
            "keyword":          pd.NA,
            "posicao_geral":    pd.array([pd.NA] * len(raw), dtype="Int64"),
            "posicao_organica": pd.array([pd.NA] * len(raw), dtype="Int64"),
            "tag":              pd.NA,
            "source":           "pricetrack",
            # Estatísticas brutas da caixa de ofertas — preservadas por linha
            # para que o módulo Price Evolution possa escolher a métrica
            # (Buy Box = min(min_price); Moda/Mediana sobre min_price;
            # Médio sobre avg_price) em vez de ficar preso à moda (`preco`).
            "min_price":        pd.to_numeric(raw.get("min_price"), errors="coerce"),
            "avg_price":        pd.to_numeric(raw.get("avg_price"), errors="coerce"),
            "mode_price":       pd.to_numeric(raw.get("mode_price"), errors="coerce"),
            "max_price":        pd.to_numeric(raw.get("max_price"), errors="coerce"),
        })
        if _MARCA_TO_CANONICAL:
            df["marca"] = df["marca"].map(
                lambda x: _MARCA_TO_CANONICAL.get(x, x) if x else x
            )
        return df
    except Exception as exc:
        # Registra o erro para a UI não confundir falha de consulta com
        # ausência de cobertura. Timeout (57014) é o caso típico em janelas
        # grandes; o dado costuma existir, só não foi lido a tempo.
        st.session_state["pt_query_error"] = str(exc)
        st.warning(
            "Erro consultando pricetrack_daily — a consulta pode ter "
            "expirado (statement_timeout). Os dados provavelmente existem; "
            f"tente recarregar ou reduzir o período. Detalhe: {exc}"
        )
        return pd.DataFrame()


def query_price_evolution_data(
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
    familias_resolvidas: list[str] | None = None,
    skus_resolvidos: list[str] | None = None,
    limit: int = 50000,
) -> tuple[pd.DataFrame, dict]:
    """
    Combined source for the Price Evolution page.

    Precedence rule (revisada 2026-05-28): pricetrack_daily é fonte de
    verdade por **(data, SKU do catálogo)**. Para cada par (data, sku)
    presente em pricetrack, descartamos as linhas de coletas com
    `(data, sku_resolvido)` idêntico — pricetrack representa sozinho o
    preço daquele SKU naquele dia. Coletas continua mostrando:
    - SKUs ainda não cobertos pelo pricetrack (ex.: marca/BTU fora do
      catálogo importado);
    - Produtos sem mapping em `produtos_depara_nome` (estado REVISAR /
      sku_resolvido NULL), que aparecem como linhas independentes.

    A regra anterior era por **data inteira** — quando o pricetrack
    cobria 12-28/05, derrubava todas as 278k linhas de coletas do
    período, escondendo produtos que pricetrack não cobre (ex.: o item
    Ecomaster Quente/Frio que ainda está em REVISAR no de-para).

    Returns (df, meta) where meta has counts for the info banner.
    """
    df_pt = query_pricetrack_daily(
        start_date, end_date,
        platforms=platforms,
        brands=brands,
        sellers=sellers,
        products=products,
        btu_filter=btu_filter,
        product_types=product_types,
        familias_resolvidas=familias_resolvidas,
        skus_resolvidos=skus_resolvidos,
    )

    pt_pairs: set[tuple] = set()
    pt_skus:  set[str]   = set()
    pt_dates: set        = set()
    if not df_pt.empty and "sku" in df_pt.columns:
        sku_norm = df_pt["sku"].astype(str).fillna("")
        pt_pairs = set(zip(df_pt["data"], sku_norm))
        pt_skus  = {s for s in sku_norm.unique() if s}
        pt_dates = set(df_pt["data"].unique().tolist())

    # Coletas continua sendo necessário pra cobrir:
    # (a) datas sem pricetrack;
    # (b) SKUs não cobertos pelo pricetrack (mesmo nas datas que ele cobre);
    # (c) produtos sem sku_resolvido (estado REVISAR), que ficam órfãos
    #     no merge porque (data, NULL) nunca casa com nenhum par do PT.
    # Mantemos a heurística antiga "ILIKE em 278k linhas estoura o
    # statement_timeout de 8s": só pulamos o coletas quando o usuário
    # NÃO trouxe nenhum filtro narrowing E o pricetrack já cobre a
    # janela inteira de datas — aí o resultado seria descartado mesmo.
    has_narrowing_filter = bool(
        products or skus_resolvidos or familias_resolvidas
        or brands or sellers or keywords or platforms
    )
    full_range = {
        start_date + timedelta(days=i)
        for i in range((end_date - start_date).days + 1)
    }
    missing_dates = sorted(full_range - pt_dates)

    if has_narrowing_filter or missing_dates:
        col_start = missing_dates[0] if missing_dates and not has_narrowing_filter else start_date
        col_end   = missing_dates[-1] if missing_dates and not has_narrowing_filter else end_date
        df_col = query_coletas(
            col_start, col_end,
            platforms=platforms,
            platform_types=platform_types,
            brands=brands,
            sellers=sellers,
            keywords=keywords,
            products=products,
            btu_filter=btu_filter,
            product_types=product_types,
            familias_resolvidas=familias_resolvidas,
            skus_resolvidos=skus_resolvidos,
            limit=limit,
        )
    else:
        df_col = pd.DataFrame()

    if not df_col.empty:
        if "source" not in df_col.columns:
            df_col = df_col.assign(source="coletas")
        # Unifica a coluna `sku` entre as duas fontes pra Detail tab e
        # qualquer agrupamento downstream: PT já traz `sku`; coletas tem
        # `sku_resolvido` (NULL quando o de-para ainda não mapeou).
        if "sku_resolvido" in df_col.columns and "sku" not in df_col.columns:
            df_col = df_col.assign(sku=df_col["sku_resolvido"])

    # Precedência por (data, sku_resolvido): pricetrack vence.
    # Linhas de coletas sem sku_resolvido ficam (NULL ≠ qualquer SKU).
    if pt_pairs and not df_col.empty and "sku_resolvido" in df_col.columns:
        skup = df_col["sku_resolvido"].astype("string").fillna("")
        keys = list(zip(df_col["data"], skup))
        mask_dup = pd.Series(
            [k in pt_pairs for k in keys],
            index=df_col.index,
        )
        df_col = df_col[~mask_dup]

    if df_pt.empty and df_col.empty:
        return pd.DataFrame(), {"pricetrack_dates": 0, "coletas_dates": 0,
                                 "pricetrack_rows": 0, "coletas_rows": 0,
                                 "pricetrack_skus": 0}

    df = pd.concat([df_pt, df_col], ignore_index=True, sort=False)
    meta = {
        "pricetrack_dates": len(pt_dates),
        "coletas_dates":    int(df_col["data"].nunique()) if not df_col.empty else 0,
        "pricetrack_rows":  int(len(df_pt)),
        "coletas_rows":     int(len(df_col)),
        "pricetrack_skus":  len(pt_skus),
    }
    return df, meta


@st.cache_data(ttl=1800, show_spinner=False)
def get_filter_options() -> dict:
    """Fetch distinct values for filter dropdowns (last 30 days), paginated."""
    empty = {"platforms": [], "platform_types": [], "brands": [], "keywords": [], "sellers": []}
    client = _get_supabase()
    if client is None:
        return empty
    # Caminho rápido: RPC server-side com DISTINCT (evita timeout em ~261k linhas)
    try:
        rpc = client.rpc("get_filter_options_fast", {"window_days": 30}).execute()
        data = rpc.data if isinstance(rpc.data, dict) else (rpc.data[0] if rpc.data else None)
        if data:
            raw_brands     = data.get("brands") or []
            raw_platforms  = data.get("platforms") or []
            return {
                "platforms":      sorted({_normalize_platform(p) for p in raw_platforms if p}),
                "platform_types": sorted(data.get("platform_types") or []),
                "brands":         sorted({_MARCA_TO_CANONICAL.get(b, b) for b in raw_brands if b}),
                "keywords":       sorted(data.get("keywords") or []),
                "sellers":        sorted(data.get("sellers") or []),
            }
    except Exception:
        pass  # cai no fallback paginado abaixo
    try:
        since = str(date.today() - timedelta(days=30))
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


@st.cache_data(ttl=1800, show_spinner=False)
def get_sku_options(
    brands: tuple = (),
    btu_filter: tuple = (),
    product_types: tuple = (),
) -> list:
    """Fetch distinct product names (last 30 days), paginated past the 1000-row cap."""
    client = _get_supabase()
    if client is None:
        return []
    try:
        since = str(date.today() - timedelta(days=30))

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
        # Inicializa uma vez (ambas) via session_state em vez de `default=`,
        # para nunca colidir com o valor setado por preset/teste — assim o
        # Streamlit não emite o aviso "default + session_state".
        st.session_state.setdefault("gf_sources", list(_DATA_SOURCES))
        st.multiselect(
            "Fonte de Dados", _DATA_SOURCES,
            format_func=lambda s: _SOURCE_LABELS.get(s, s),
            key="gf_sources",
            help=(
                "Quais bases alimentam o dashboard. Padrão: **ambas**.\n\n"
                "• **Coletas (Python)** — scraping próprio (posição, buy box, "
                "sellers, reputação e também preço).\n"
                "• **PriceTrack** — importação externa; fonte de preço por "
                "(data, SKU) com precedência sobre as coletas.\n\n"
                "Selecione apenas uma para isolar a fonte. Páginas sem dados "
                "naquela fonte (ex.: buy box só existe nas coletas) avisam que "
                "não há registros — nada quebra."
            ),
        )

        st.checkbox(
            "Comparar período anterior", key="gf_compare",
            help="Compara com o período imediatamente anterior, de mesma "
                 "duração — calculado automaticamente a partir do Período.",
        )
        if st.session_state.get("gf_compare"):
            _cs, _ce = _gf_cmp_dates()
            st.caption(
                f"↔️ Comparando com **{_cs.strftime('%d/%m/%Y')} → "
                f"{_ce.strftime('%d/%m/%Y')}** (automático)."
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
                    "sources":   _gf_sources(),
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
                    # `sources` é novo; presets antigos caem no padrão (ambas).
                    st.session_state["gf_sources"]   = p.get("sources", list(_DATA_SOURCES))
                    st.rerun()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Global filter accessors
# ---------------------------------------------------------------------------

# Fontes de dados disponíveis no dashboard. "coletas" = scraping Python
# (tabela `coletas`); "pricetrack" = importação do PriceTrack
# (tabela `pricetrack_daily`). Padrão: ambas ligadas (comportamento histórico).
_DATA_SOURCES: list[str] = ["coletas", "pricetrack"]
_SOURCE_LABELS: dict[str, str] = {
    "coletas":    "Coletas (Python)",
    "pricetrack": "PriceTrack",
}


def _gf_dates() -> tuple:
    gf = st.session_state.get("gf_dates", ())
    if len(gf) >= 2:
        return gf[0], gf[1]
    return date.today() - timedelta(days=7), date.today()


def _gf_platforms() -> list:
    return list(st.session_state.get("gf_platforms", []))


def _gf_brands() -> list:
    return list(st.session_state.get("gf_brands", []))


def _gf_sources() -> list:
    """Fontes de dados ativas (subconjunto de `_DATA_SOURCES`).

    Padrão = ambas. Se o usuário esvaziar o multiselect (nenhuma fonte),
    voltamos a ambas — um dashboard 100% vazio nunca é o objetivo; "nenhuma"
    não é um recorte útil. Selecionar uma única fonte é o caminho suportado
    para isolar Coletas Python ou PriceTrack.
    """
    sel = st.session_state.get("gf_sources")
    if not sel:
        return list(_DATA_SOURCES)
    return [s for s in _DATA_SOURCES if s in sel]


def _gf_sources_key() -> tuple:
    """Tupla estável das fontes ativas — usada como chave de cache nas
    funções `@st.cache_data` cujo resultado depende da fonte selecionada."""
    return tuple(_gf_sources())


def _gf_estados_key() -> tuple:
    """Tupla estável dos estados_match globais (chave de cache)."""
    return tuple(_gf_estados())


def _gf_familias_key() -> tuple:
    """Tupla estável das famílias globais (chave de cache)."""
    return tuple(_gf_familias())


def _gf_skus_resolvidos_key() -> tuple:
    """Tupla estável dos SKUs resolvidos globais (chave de cache)."""
    return tuple(_gf_skus_resolvidos())


def _gf_compare() -> bool:
    return bool(st.session_state.get("gf_compare", False))


def _gf_cmp_dates() -> tuple:
    """Janela de comparação — automatizada (não há mais date-picker manual).

    Calcula o período imediatamente anterior ao atual, com a mesma duração:
    para `[início, fim]`, devolve `[início - dur - 1, início - 1]`. Ex.:
    atual = 08/06→15/06 (8 dias) ⇒ comparação = 31/05→07/06.
    """
    start, end = _gf_dates()
    span = (end - start).days
    cmp_end = start - timedelta(days=1)
    cmp_start = cmp_end - timedelta(days=span)
    return cmp_start, cmp_end


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


@st.cache_data(ttl=1800, show_spinner=False)
def _overview_data(
    start_str: str,
    end_str: str,
    platforms_tuple: tuple,
    brands_tuple: tuple,
    limit: int = 15000,
    familias_tuple: tuple = (),
    skus_resolvidos_tuple: tuple = (),
    sources_tuple: tuple = ("coletas", "pricetrack"),
    estados_tuple: tuple = (),
) -> pd.DataFrame:
    """Cached Supabase query for overview / top-movers pages.

    `sources_tuple` espelha o filtro global de Fonte de Dados: entra na chave
    de cache e, como esta função lê só a tabela `coletas`, devolve vazio quando
    "coletas" está desligada.

    `estados_tuple`, `familias_tuple` e `skus_resolvidos_tuple` precisam ser
    passados explicitamente pelos chamadores (use `_gf_*_key()`). Nunca lemos
    `_gf_*()` aqui dentro porque o resultado entra como chave de cache; ler
    session state internamente daria respostas stale quando o filtro global
    muda dentro da janela do TTL.
    """
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()
    if "coletas" not in sources_tuple:
        return pd.DataFrame()

    def _build_q():
        # Composite keyset by (data desc, id desc) — see query_coletas for why
        # ordering by `id desc` alone causes statement_timeout (57014).
        q = (
            client.table("coletas")
            .select("*")
            .gte("data", start_str)
            .lte("data", end_str)
            .order("data", desc=True)
            .order("id", desc=True)
        )
        if platforms_tuple:
            q = q.in_("plataforma", _expand_platforms(list(platforms_tuple)))
        if brands_tuple:
            q = q.in_("marca", _expand_brands(list(brands_tuple)))
        final_estados  = list(estados_tuple)
        final_familias = list(familias_tuple)
        final_skus     = list(skus_resolvidos_tuple)
        if final_estados:
            q = q.in_("estado_match", final_estados)
        if final_familias:
            q = q.in_("familia_resolvida", final_familias)
        if final_skus:
            q = _apply_sku_filter_with_expansion(q, final_skus)
        return q

    try:
        all_data: list = []
        last_data: str | None = None
        last_id: int | None = None
        while len(all_data) < limit:
            fetch = min(_SUPABASE_PAGE, limit - len(all_data))
            q = _build_q()
            if last_data is not None and last_id is not None:
                q = q.or_(
                    f"data.lt.{last_data},"
                    f"and(data.eq.{last_data},id.lt.{last_id})"
                )
            resp = q.limit(fetch).execute()
            if not resp.data:
                break
            all_data.extend(resp.data)
            if len(resp.data) < fetch:
                break
            last_row = resp.data[-1]
            last_data = str(last_row.get("data")) if last_row.get("data") else None
            last_id = last_row.get("id")
            if last_data is None or last_id is None:
                break

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        df["data"] = pd.to_datetime(df["data"]).dt.date
        for col in ["posicao_organica", "posicao_patrocinada", "posicao_geral",
                    "qtd_avaliacoes", "qtd_sellers"]:
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


@st.cache_data(ttl=1800, show_spinner=False)
def _price_data(
    start_str: str,
    end_str: str,
    platforms_tuple: tuple,
    brands_tuple: tuple,
    limit: int = 15000,
    familias_tuple: tuple = (),
    skus_resolvidos_tuple: tuple = (),
    sources_tuple: tuple = ("coletas", "pricetrack"),
) -> pd.DataFrame:
    """Fonte canônica de **preço** — precedência PriceTrack.

    Espelha a assinatura de `_overview_data`, mas a fonte é
    `query_price_evolution_data`: o PriceTrack é a verdade por (data, SKU)
    e as coletas Python só preenchem marcas/produtos/datas que o PriceTrack
    ainda não cobre.

    Use SEMPRE que a métrica exibida for **preço** (médias, variação,
    anomalias, evolução, distribuição). Para contagens de volume / registros
    / presença, continue usando `_overview_data` (coletas cru) — o PriceTrack
    agrega por seller e distorceria contagens de ofertas.

    Returns:
        DataFrame com colunas no schema de coletas (data, plataforma, marca,
        produto, preco, seller, sku, ...). A coluna `source` indica a origem
        de cada linha ("pricetrack" ou "coletas").

    Nota: `sources_tuple` entra apenas como discriminador da chave de cache —
    o recorte de fonte em si acontece nas funções-folha (`query_coletas` /
    `query_pricetrack_daily`), que `query_price_evolution_data` chama.

    `familias_tuple` e `skus_resolvidos_tuple` precisam ser passados
    explicitamente pelos chamadores (use `_gf_familias_key()` /
    `_gf_skus_resolvidos_key()`). Nunca lemos `_gf_*()` aqui dentro porque o
    resultado entra como chave de cache; ler session state internamente daria
    respostas stale quando o filtro global muda dentro da janela do TTL.
    """
    _ = sources_tuple  # cache-key only (ver docstring)
    familias = list(familias_tuple) or None
    skus     = list(skus_resolvidos_tuple) or None
    df, _ = query_price_evolution_data(
        date.fromisoformat(start_str),
        date.fromisoformat(end_str),
        platforms=list(platforms_tuple) or None,
        brands=list(brands_tuple) or None,
        familias_resolvidas=familias,
        skus_resolvidos=skus,
        limit=limit,
    )
    return df


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
        sel_familias, sel_skus_resolvidos = _render_familia_sku_filters(sel_brands, "results")
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
            familias_resolvidas=sel_familias or None,
            skus_resolvidos=sel_skus_resolvidos or None,
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

def _evo_daily_floor(d: pd.DataFrame, src: str, skus: list[str],
                     clean_on: bool) -> pd.DataFrame:
    """Piso diário (buy box = menor preço) por (data, sku) de uma fonte.

    coletas → menor `preco` (Google Shopping excluído — agregador que não
    existe no PriceTrack). pricetrack → menor `min_price`.
    """
    cols = ["data", "sku", "value", "source", "n"]
    if d.empty:
        return pd.DataFrame(columns=cols)
    d = d.copy()
    # coletas (query direta) traz `sku_resolvido`; pricetrack já traz `sku`.
    # Sem normalizar, `d["sku"]` estoura KeyError e quebra o modo comparar.
    if "sku" not in d.columns:
        if "sku_resolvido" in d.columns:
            d["sku"] = d["sku_resolvido"]
        else:
            return pd.DataFrame(columns=cols)
    d = d[d["sku"].astype("string").isin(skus)]
    if src == "coletas":
        d = d[~d["plataforma"].astype("string").str.contains(
            "google", case=False, na=False)]
        price = pd.to_numeric(d.get("preco"), errors="coerce")
    else:
        col = "min_price" if "min_price" in d.columns else "preco"
        price = pd.to_numeric(d.get(col), errors="coerce")
    d = d.assign(_p=price).dropna(subset=["_p", "data"])
    if clean_on and not d.empty:
        # Mesma guarda do gráfico principal: placeholder + outlier > 1,5× a
        # mediana do próprio SKU (mantém o "Dados limpos" coerente entre modos).
        med = d.groupby("sku")["_p"].transform("median")
        ph = d["_p"].map(_is_placeholder_price)
        outlier = (d["_p"] > 1.5 * med) & med.notna()
        d = d[~(ph | outlier)]
    if d.empty:
        return pd.DataFrame(columns=cols)
    g = (d.groupby(["data", "sku"])
         .agg(value=("_p", "min"), n=("_p", "size"))
         .reset_index())
    g["source"] = src
    return g


def _render_evo_compare_sources(df: pd.DataFrame, params: dict,
                                start_date: date, end_date: date,
                                clean_on: bool, group_default: str) -> None:
    """Modo 'Comparar fontes': Coletas × PriceTrack na mesma figura + gap diário.

    As duas fontes NÃO medem o mesmo piso (a validação achou mediana de gap
    do mín ~R$271 no caso base). Esta visão deixa o gap explícito em vez de
    escondê-lo atrás da precedência por (data, SKU).
    """
    if "sku" not in df.columns:
        st.info("Sem coluna de SKU para comparar fontes.")
        return
    sku_clean = df["sku"].astype("string").str.strip()
    sku_counts = (df.assign(_sku=sku_clean)[sku_clean.fillna("") != ""]
                  .groupby("_sku").size().sort_values(ascending=False))
    sku_opts = sku_counts.index.tolist()
    if not sku_opts:
        st.info("Nenhum SKU resolvido disponível para comparação.")
        return

    sel = st.multiselect(
        "SKU(s) para comparar (Coletas × PriceTrack)",
        sku_opts,
        default=sku_opts[:1],
        key="evo_compare_skus",
        help="As fontes são consultadas isoladamente (sem a precedência por "
             "data/SKU) para mostrar o gap real.",
    )
    if not sel:
        st.info("Selecione ao menos um SKU para comparar.")
        return

    # Repassa o MESMO escopo do dashboard (não só plataformas/marcas), senão a
    # comparação sai de um recorte diferente do gráfico principal. `sel`
    # (SKUs escolhidos) substitui skus_resolvidos/familias do params — já é o
    # subconjunto mais estreito e evita AND conflitante com família.
    with st.spinner("Consultando as duas fontes…"):
        df_col = query_coletas(
            start_date, end_date,
            platforms=params.get("platforms"),
            platform_types=params.get("platform_types"),
            brands=params.get("brands"),
            sellers=params.get("sellers"),
            keywords=params.get("keywords"),
            btu_filter=params.get("btu_filter"),
            product_types=params.get("product_types"),
            skus_resolvidos=sel, limit=50000)
        df_pt = query_pricetrack_daily(
            start_date, end_date,
            platforms=params.get("platforms"),
            brands=params.get("brands"),
            sellers=params.get("sellers"),
            btu_filter=params.get("btu_filter"),
            product_types=params.get("product_types"),
            skus_resolvidos=sel)

    floors = []
    if not df_col.empty:
        floors.append(_evo_daily_floor(df_col, "coletas", sel, clean_on))
    if not df_pt.empty:
        floors.append(_evo_daily_floor(df_pt, "pricetrack", sel, clean_on))
    floors = [f for f in floors if not f.empty]
    if not floors:
        st.warning("Sem dados em nenhuma das fontes para os SKUs/filtros "
                   "selecionados (verifique o filtro **Fonte de Dados**).")
        return

    comb = pd.concat(floors, ignore_index=True)
    comb["data"] = pd.to_datetime(comb["data"])
    multi = len(sel) > 1

    fig = px.line(
        comb.sort_values("data"),
        x="data", y="value", color="source",
        line_dash="sku" if multi else None,
        markers=True, custom_data=["n", "sku"],
        color_discrete_map={"coletas": _CHART_COLORS[1],
                            "pricetrack": _CHART_COLORS[0]},
        title="Buy Box (piso diário) — Coletas × PriceTrack",
        labels={"data": "Date", "value": "Buy Box (R$)", "source": "Fonte"},
    )
    fig.update_traces(
        line=dict(width=2.5), marker=dict(size=6),
        hovertemplate=("<b>%{fullData.name}</b> · SKU %{customdata[1]}<br>"
                       "%{x|%d/%m/%Y}<br>Buy Box: R$ %{y:.2f}<br>"
                       "n: %{customdata[0]}<extra></extra>"))
    _apply_chart_style(fig, height=440)
    st.plotly_chart(fig, use_container_width=True)

    # Gap diário: mín_coletas − mín_pricetrack.
    piv = comb.pivot_table(index=["data", "sku"], columns="source",
                           values="value").reset_index()
    if {"coletas", "pricetrack"}.issubset(piv.columns):
        piv["gap"] = piv["coletas"] - piv["pricetrack"]
        gap_valid = piv.dropna(subset=["gap"])
        if not gap_valid.empty:
            med_gap = float(gap_valid["gap"].median())
            c1, c2 = st.columns(2)
            c1.metric("Mediana do gap (coletas − pricetrack)",
                      f"R$ {med_gap:,.2f}".replace(",", "."))
            c2.metric("Dias comparáveis", f"{gap_valid['data'].nunique()}")
            gap_fig = px.bar(
                gap_valid.sort_values("data"),
                x="data", y="gap",
                color="sku" if multi else None,
                title="Gap diário do piso (coletas − pricetrack)",
                labels={"data": "Date", "gap": "Gap (R$)"},
            )
            _apply_chart_style(gap_fig, height=300)
            st.plotly_chart(gap_fig, use_container_width=True)
            st.caption(
                "Gap > 0: o piso das Coletas está acima do PriceTrack naquele "
                "dia. As fontes medem caixas de oferta diferentes — o gap é "
                "esperado, não um bug.")
    else:
        st.info("Só uma das fontes retornou dados — sem gap para calcular. "
                "Confira o filtro **Fonte de Dados** no topo.")


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
        sel_familias, sel_skus_resolvidos = _render_familia_sku_filters(sel_brands, "evo")
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
            key="evo_group_by",
            help="**Product** agrupa por SKU do catálogo (não por nome) — "
                 "variações de título do mesmo produto viram uma única série.",
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

        st.divider()
        st.markdown("**Análise de preço**")
        price_metric = st.radio(
            "Métrica de preço",
            list(_PRICE_METRICS.keys()),
            index=0,
            key="evo_metric",
            help=(
                "**Buy Box** = menor oferta do dia (o que o cliente realmente "
                "paga). **Moda** = preço mais repetido (costuma ser o teto 3P / "
                "MAP). **Mediana** e **Médio** dão a tendência central."
            ),
        )
        clean_on = st.toggle(
            "Dados limpos", value=True, key="evo_clean",
            help=(
                "Remove do gráfico placeholders (preços terminando em 999,00 / "
                "9999) e outliers acima de 1,5× a mediana do próprio SKU. "
                "O contador mostra quantos pontos saíram."
            ),
        )
        excl_google = st.checkbox(
            "Excluir Google Shopping (coletas)", value=False, key="evo_excl_google",
            help="Google Shopping é agregador: devolve título/SKU de outro "
                 "produto e suja a série. Só afeta a fonte Coletas.",
        )
        compare_sources = st.checkbox(
            "Comparar fontes (Coletas × PriceTrack)", value=False,
            key="evo_compare_sources",
            help="Plota as duas fontes lado a lado para o(s) SKU(s) escolhido(s) "
                 "e mostra o gap diário do piso. Google Shopping é excluído "
                 "(não existe no PriceTrack).",
        )

        load_btn = st.button("🔄 Load Chart", type="primary", use_container_width=True)

    # Persistimos o dataframe carregado em session_state para que trocar a
    # métrica ou ligar as guardas re-renderize SEM nova consulta ao Supabase
    # (e sem mexer nos filtros Período/Plataformas/Marcas/Fonte já ativos).
    if load_btn:
        with st.spinner("Loading data..."):
            df_loaded, evo_meta = query_price_evolution_data(
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
                familias_resolvidas=sel_familias or None,
                skus_resolvidos=sel_skus_resolvidos or None,
                limit=50000,
            )
        if modo_evo == "Snapshot oficial (último run)":
            df_loaded = _filter_latest_run(df_loaded)
        st.session_state["evo_df"] = df_loaded
        st.session_state["evo_meta"] = evo_meta
        st.session_state["evo_params"] = {
            "start_date": start_date, "end_date": end_date,
            "platforms": sel_platforms or None,
            "platform_types": sel_tipo or None,
            "brands": sel_brands or None,
            "sellers": sel_sellers or None,
            "keywords": sel_keywords or None,
            "btu_filter": sel_btu or None,
            "product_types": sel_ptype or None,
            "familias_resolvidas": sel_familias or None,
            "skus_resolvidos": sel_skus_resolvidos or None,
        }

    df = st.session_state.get("evo_df")
    evo_meta = st.session_state.get("evo_meta", {})
    params = st.session_state.get("evo_params", {})
    if df is None:
        st.info("Set your filters in the sidebar and click **Load Chart**.")
        return
    start_date = params.get("start_date", start_date)
    end_date = params.get("end_date", end_date)

    if df.empty or "preco" not in df.columns:
        st.warning("No price data found for the selected filters.")
        return

    # Banner de transparência: PriceTrack tem precedência por (data, SKU).
    if evo_meta.get("pricetrack_rows", 0) > 0:
        st.info(
            f"📥 **PriceTrack** cobrindo {evo_meta['pricetrack_dates']} "
            f"data(s) e {evo_meta.get('pricetrack_skus', 0)} SKU(s) do catálogo "
            f"({evo_meta['pricetrack_rows']:,} linhas) · "
            f"**Coletas** preenchendo SKUs/datas não cobertos "
            f"({evo_meta['coletas_dates']} data(s), "
            f"{evo_meta['coletas_rows']:,} linhas)."
            .replace(",", ".")
        )
    elif (sel_skus or sel_skus_resolvidos or sel_familias) and \
         evo_meta.get("pricetrack_rows", 0) == 0:
        # Filtro de produto/SKU/família foi aplicado mas o PT não cobre
        # nenhum dos SKUs resolvidos. Sinaliza pra o usuário não achar
        # que o filtro tá quebrado.
        st.warning(
            "📭 PriceTrack não tem dados para os SKUs filtrados nesta "
            "janela. Exibindo apenas dados de **Coletas**. "
            "Verifique se o(s) produto(s) selecionado(s) têm mapeamento "
            "para SKU do catálogo em `produtos_depara_nome` (estado MAPEADO)."
        )

    # --- Summary metrics with enhanced cards ---
    st.markdown("""
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.5rem; margin-bottom: 2rem;">
    """, unsafe_allow_html=True)

    days = (end_date - start_date).days + 1
    # KPI corrigido: conta SKUs distintos (não variações de nome de produto).
    # A versão antiga (`produto.nunique()`) contava títulos — "…Frio" e
    # "…Frio Preto" viravam dois; e a linha-fantasma de sku nulo entrava.
    if "sku" in df.columns:
        _sku_clean = df["sku"].astype("string").str.strip()
        unique_skus = int(_sku_clean[_sku_clean.fillna("") != ""].nunique())
    else:
        unique_skus = 0
    unique_brands = df['marca'].nunique() if 'marca' in df.columns else 0

    cols = st.columns(4)

    with cols[0]:
        st.metric(label="📊 Total Records", value=f"{len(df):,}", delta=None)
    with cols[1]:
        st.metric(label="📦 Unique SKUs", value=f"{unique_skus:,}", delta=None)
    with cols[2]:
        st.metric(label="🏷️ Brands", value=str(unique_brands), delta=None)
    with cols[3]:
        st.metric(label="📅 Time Range", value=f"{days} days", delta=None)

    st.markdown("</div>", unsafe_allow_html=True)
    st.divider()

    # ── Métrica de preço + chave de agrupamento por SKU ──────────────────────
    metric_cfg = _PRICE_METRICS.get(price_metric, _PRICE_METRICS["Buy Box (menor preço)"])
    series_col = {"Brand": "marca", "Platform": "plataforma", "Product": "sku"}[group_by]
    if series_col not in df.columns:
        st.warning(f"Column '{series_col}' not available in data.")
        return

    work = df.dropna(subset=["data"]).copy()
    work["_basis"] = _metric_basis(work, metric_cfg["pt_col"])
    work = work.dropna(subset=["_basis"])
    # Agrupar por SKU descarta linhas sem sku_resolvido (estado REVISAR / nulo):
    # ~46% das coletas. Mantê-las criava séries-fantasma por nome de título
    # (ex.: "Ecomaster Pro" do Google Shopping, sku nulo).
    if group_by == "Product":
        _sk = work["sku"].astype("string").str.strip()
        work = work[_sk.fillna("") != ""]
    work = work[work[series_col].notna()]
    if work.empty:
        st.warning("No records with price data in this range.")
        return

    # ── Guarda de qualidade ("Dados limpos") ─────────────────────────────────
    removed_google = removed_clean = 0
    if clean_on:
        if excl_google:
            g_mask = (
                (work["source"].astype("string") == "coletas")
                & work["plataforma"].astype("string").str.contains(
                    "google", case=False, na=False)
            )
            removed_google = int(g_mask.sum())
            work = work[~g_mask]
        if not work.empty:
            ph = work["_basis"].map(_is_placeholder_price)
            # Outlier é relativo à mediana do **próprio SKU** (não do grupo do
            # gráfico): em Brand/Platform um grupo mistura SKUs de 9k a 60k BTUs,
            # e uma mediana de grupo cortaria preços legítimos. Linhas sem SKU
            # (views Brand/Platform) caem na coluna de agrupamento.
            if "sku" in work.columns:
                med_key = work["sku"].astype("string").str.strip()
                med_key = med_key.where(
                    med_key.fillna("") != "", work[series_col].astype("string"))
            else:
                med_key = work[series_col].astype("string")
            med = work.groupby(med_key)["_basis"].transform("median")
            outlier = (work["_basis"] > 1.5 * med) & med.notna()
            drop_mask = ph | outlier
            removed_clean = int(drop_mask.sum())
            work = work[~drop_mask]
    removed_total = removed_google + removed_clean
    if work.empty:
        st.warning("All rows were filtered out by the data-quality guard. "
                   "Disable **Dados limpos** to inspect the raw series.")
        return

    # ── Agregação por (data, série) ──────────────────────────────────────────
    agg = (
        work.groupby(["data", series_col])
        .agg(value=("_basis", metric_cfg["agg"]),
             n=("_basis", "size"))
        .reset_index()
    )

    # Rótulo amigável da série (Product = nome representativo + código do SKU).
    if group_by == "Product":
        name_src = work.dropna(subset=["produto"])
        rep = (
            name_src.groupby("sku")["produto"].agg(
                lambda s: s.mode().iat[0] if not s.mode().empty else s.iat[0])
            if not name_src.empty else pd.Series(dtype="object")
        )

        def _series_label(sku_code) -> str:
            nm = rep.get(sku_code)
            if isinstance(nm, str) and nm.strip():
                short = (nm[:46] + "…") if len(nm) > 47 else nm
                return f"{short} · {sku_code}"
            return str(sku_code)

        agg["series"] = agg["sku"].map(_series_label)
    else:
        agg["series"] = agg[series_col].astype(str)

    # ── Flag de série CONGELADA (estática) ────────────────────────────────────
    # 1 único valor diário em ≥10 dias ⇒ pode ser MAP real OU coleta travada.
    freeze = agg.groupby("series").agg(
        distinct=("value", "nunique"), n_days=("value", "size"))
    frozen = set(freeze[(freeze["distinct"] <= 1) & (freeze["n_days"] >= 10)].index)
    agg["frozen"] = agg["series"].isin(frozen)
    agg["series_disp"] = agg.apply(
        lambda r: ("⚠️ " + r["series"]) if r["frozen"] else r["series"], axis=1)
    agg["data"] = pd.to_datetime(agg["data"])

    # ── Banners de cobertura ──────────────────────────────────────────────────
    coverage_msgs: list[str] = []
    _pt = df[df["source"].astype("string") == "pricetrack"] if "source" in df.columns else df.iloc[0:0]
    if not _pt.empty:
        pt_max = pd.to_datetime(_pt["data"]).max().date()
        if pt_max < end_date:
            coverage_msgs.append(
                f"📭 **PriceTrack** sem dados após **{pt_max.strftime('%d/%m')}** "
                f"(período pedido até {end_date.strftime('%d/%m')}) — a série do "
                "PriceTrack não cobre o último dia.")
    _col = df[df["source"].astype("string") == "coletas"] if "source" in df.columns else df.iloc[0:0]
    if not _col.empty:
        cov = _col.groupby("plataforma")["data"].nunique()
        thresh = max(3, days // 4)
        sparse = cov[cov < thresh].sort_values()
        if not sparse.empty:
            itens = ", ".join(f"{p} ({d}d)" for p, d in sparse.items())
            coverage_msgs.append(
                f"🕳️ Cobertura esparsa nas **Coletas** (< {thresh} dias úteis no "
                f"período): {itens}. Séries dessas plataformas têm poucos pontos.")

    tab_chart, tab_summary, tab_detail = st.tabs(
        ["📈 Price Chart", "📊 Summary", "📋 Detail"]
    )

    # ── Tab 1: Price Chart ───────────────────────────────────────────────────
    with tab_chart:
        badge_cols = st.columns([3, 1])
        with badge_cols[0]:
            st.caption(
                f"**Métrica:** {metric_cfg['short']} · agrupado por "
                f"**{'SKU' if group_by == 'Product' else group_by}**"
                + (" · ⚠️ = série estática (MAP real ou coleta travada)"
                   if frozen else ""))
        with badge_cols[1]:
            if clean_on and removed_total:
                st.caption(
                    f"🧹 **{removed_total} ponto(s) removido(s)** "
                    f"(placeholder/outlier: {removed_clean}"
                    + (f"; Google: {removed_google}" if removed_google else "")
                    + ")")
            elif clean_on:
                st.caption("🧹 Dados limpos: nenhum ponto removido.")
            else:
                st.caption("⚠️ Guarda **desligada** — placeholders e outliers visíveis.")

        for _m in coverage_msgs:
            st.warning(_m)

        if compare_sources:
            _render_evo_compare_sources(
                df, params, start_date, end_date,
                clean_on=clean_on, group_default=group_by)
        else:
            _cmap = _brand_color_map(agg["series_disp"]) if group_by == "Brand" else None
            fig = px.line(
                agg,
                x="data",
                y="value",
                color="series_disp",
                color_discrete_map=_cmap,
                markers=True,
                custom_data=["n", "series"],
                title=f"{metric_cfg['title']} by "
                      f"{'SKU' if group_by == 'Product' else group_by}",
                labels={"data": "Date", "value": metric_cfg["ylabel"],
                        "series_disp": group_by},
            )
            fig.update_traces(
                line=dict(width=2.5), marker=dict(size=6),
                hovertemplate=(
                    "<b>%{customdata[1]}</b><br>%{x|%d/%m/%Y}<br>"
                    + metric_cfg["ylabel"] + ": R$ %{y:.2f}<br>"
                    "ofertas no dia (n): %{customdata[0]}<extra></extra>"),
            )
            # Série congelada → linha tracejada.
            for tr in fig.data:
                if isinstance(tr.name, str) and tr.name.startswith("⚠️"):
                    tr.line.dash = "dash"
            _emphasize_midea_traces(fig)
            _apply_chart_style(fig, height=460)
            st.plotly_chart(fig, use_container_width=True)

    # ── Tab 2: Price Summary ─────────────────────────────────────────────────
    with tab_summary:
        st.subheader(f"Price summary — base: {metric_cfg['short']}")
        summary = (
            work
            .groupby(series_col)["_basis"]
            .agg(
                Count="count",
                Min="min",
                Mode=_mode_price,
                Median="median",
                Max="max",
                Avg="mean",
            )
            .round(2)
            .reset_index()
            .rename(columns={series_col: group_by})
            .sort_values("Min", ascending=True)
        )
        _summary_styled = (
            _style_midea_df(summary, brand_col=group_by)
            if group_by == "Brand" else summary
        )
        st.dataframe(_summary_styled, use_container_width=True, hide_index=True)
        st.caption(
            "Buy Box (Min) ≤ Moda em toda linha — por construção, o menor "
            "preço nunca supera o valor mais repetido.")

    # ── Tab 3: Detail ────────────────────────────────────────────────────────
    with tab_detail:
        st.subheader("All records")
        display_cols = [
            c for c in [
                "data", "source", "turno", "plataforma", "marca", "sku",
                "produto", "title",
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
    PRICETRACK_IMPORT_DIR = PROJECT_ROOT / "imports" / "pricetrack"

    tab_folder, tab_upload, tab_pt_folder, tab_pt_upload = st.tabs([
        "From output/ folder",
        "Upload files",
        "PriceTrack — pasta",
        "PriceTrack — upload",
    ])

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

    # --- Tab 3: PriceTrack — pasta imports/pricetrack/ ---
    with tab_pt_folder:
        _render_pricetrack_folder_tab(PRICETRACK_IMPORT_DIR)

    # --- Tab 4: PriceTrack — upload manual de .md/.xlsx ---
    with tab_pt_upload:
        _render_pricetrack_upload_tab(PRICETRACK_IMPORT_DIR)


# ---------------------------------------------------------------------------
# PriceTrack — helpers de UI para a Import History
# ---------------------------------------------------------------------------

_PRICETRACK_EXT = (".md", ".xlsx", ".xlsm")


def _render_pricetrack_folder_tab(pricetrack_dir):
    """Tab que escaneia imports/pricetrack/ e importa .md/.xlsx do PriceTrack."""
    st.markdown(
        "Escaneia a pasta `imports/pricetrack/` e importa todos os arquivos "
        "`.md` ou `.xlsx` exportados do **PriceTrack** para a tabela "
        "`pricetrack_daily`. Reimports são idempotentes (ON CONFLICT)."
    )

    pricetrack_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(
        [f for f in pricetrack_dir.iterdir() if f.suffix.lower() in _PRICETRACK_EXT]
    )

    if not files:
        st.info(
            f"Nenhum arquivo `.md` ou `.xlsx` em `imports/pricetrack/`. "
            f"Faça o export manual do PriceTrack e salve aqui, ou use a tab "
            f"**PriceTrack — upload**."
        )
        return

    preview_rows = []
    for f in files:
        try:
            size_kb = f.stat().st_size / 1024
        except OSError:
            size_kb = 0
        preview_rows.append(
            {
                "Arquivo": f.name,
                "Tipo": f.suffix.lstrip("."),
                "Tamanho (KB)": f"{size_kb:,.1f}",
            }
        )
    st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
    st.caption(f"Total: {len(files)} arquivo(s)")

    col_a, col_b = st.columns([1, 1])
    with col_a:
        force = st.checkbox(
            "Forçar reimport (mesmo se já importado)",
            value=False,
            key="pt_folder_force",
            help="Sem isto, arquivos já presentes em `pricetrack_import_log` "
                 "com status SUCCESS são pulados.",
        )
    with col_b:
        dry_run = st.checkbox(
            "Dry-run (não escreve no Supabase)",
            value=False,
            key="pt_folder_dryrun",
        )

    if st.button("⬆️ Importar todos para Supabase", type="primary", key="pt_folder_btn"):
        results = []
        progress = st.progress(0, text="Iniciando...")
        for i, f in enumerate(files):
            progress.progress(
                (i + 1) / len(files), text=f"Importando {f.name}..."
            )
            results.append(_run_pricetrack_import(f, dry_run=dry_run, force=force))
        progress.empty()
        _render_pricetrack_results(results)


def _render_pricetrack_upload_tab(pricetrack_dir):
    """Tab que aceita upload manual de .md/.xlsx do PriceTrack."""
    st.markdown(
        "Faça upload de um ou mais arquivos `.md` ou `.xlsx` do **PriceTrack**. "
        "Os arquivos são salvos em `imports/pricetrack/` antes da importação."
    )

    uploaded = st.file_uploader(
        "Selecione arquivos do PriceTrack",
        type=["md", "xlsx", "xlsm"],
        accept_multiple_files=True,
        help="Markdown table (export 'tableConvert') ou .xlsx nativo do PriceTrack.",
        key="pt_upload_files",
    )

    if not uploaded:
        return

    dry_run = st.checkbox(
        "Dry-run (não escreve no Supabase)",
        value=False,
        key="pt_upload_dryrun",
    )

    if st.button(
        f"⬆️ Importar {len(uploaded)} arquivo(s) para Supabase",
        type="primary",
        key="pt_upload_btn",
    ):
        pricetrack_dir.mkdir(parents=True, exist_ok=True)
        results = []
        progress = st.progress(0, text="Iniciando...")
        for i, uf in enumerate(uploaded):
            dest = pricetrack_dir / uf.name
            try:
                with open(dest, "wb") as fh:
                    fh.write(uf.getbuffer())
            except OSError as e:
                results.append({
                    "source_file": uf.name,
                    "status": "FAILED",
                    "error": f"Falha ao salvar arquivo: {e}",
                    "rows": None,
                    "log_path": None,
                })
                continue
            progress.progress(
                (i + 1) / len(uploaded), text=f"Importando {uf.name}..."
            )
            # Upload manual: força reimport (usuário escolheu enviar agora)
            results.append(_run_pricetrack_import(dest, dry_run=dry_run, force=True))
        progress.empty()
        _render_pricetrack_results(results)


def _run_pricetrack_import(path, *, dry_run: bool, force: bool) -> dict:
    """Roda o importer para um arquivo e devolve um dict com o resumo."""
    from pricetrack_importer.__main__ import _process_one

    log_dir = PROJECT_ROOT / "logs" / "pricetrack"
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        exec_log = _process_one(
            path,
            dry_run=dry_run,
            force=force,
            batch_size=1000,
            log_dir=log_dir,
        )
    except Exception as e:
        return {
            "source_file": str(path.name),
            "status": "FAILED",
            "error": str(e),
            "rows": None,
            "log_path": None,
            "rejection_samples": [],
        }

    return {
        "source_file": str(path.name),
        "status": exec_log.status,
        "error": exec_log.error,
        "rows": exec_log.rows,
        "rejection_samples": list(exec_log.rejection_samples),
        "unknown_sellers_count": exec_log.unknown_sellers_count,
        "log_path": str(log_dir / f"{exec_log.execution_id}.json"),
        "execution_id": exec_log.execution_id,
    }


def _render_pricetrack_results(results: list[dict]) -> None:
    """Renderiza um resumo + amostras de rejeição para cada arquivo processado."""
    if not results:
        return

    # Agregado no topo
    total_inserted = 0
    total_updated = 0
    total_rejected = 0
    total_parsed = 0
    any_failed = False
    for r in results:
        if r.get("status") == "FAILED":
            any_failed = True
            continue
        rows = r.get("rows")
        if rows is None:
            continue
        total_inserted += rows.inserted
        total_updated += rows.updated
        total_rejected += rows.rejected
        total_parsed += rows.total_parsed

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Parseadas", f"{total_parsed:,}")
    c2.metric("Inseridas", f"{total_inserted:,}")
    c3.metric("Atualizadas", f"{total_updated:,}")
    c4.metric("Rejeitadas", f"{total_rejected:,}")

    if any_failed:
        st.error("Alguns arquivos falharam. Detalhes abaixo.")
    elif total_inserted + total_updated == 0:
        st.warning(
            "Nenhuma linha foi escrita no Supabase. Pode ter sido dry-run "
            "ou todos os arquivos já estavam importados (use **Forçar reimport**)."
        )
    else:
        st.success(
            f"✅ {total_inserted + total_updated:,} linhas escritas em "
            f"`pricetrack_daily` (inseridas + atualizadas)."
        )

    # Detalhe por arquivo
    for r in results:
        status_icon = {"SUCCESS": "✓", "FAILED": "✗"}.get(r.get("status", ""), "?")
        with st.expander(f"{status_icon} {r['source_file']}", expanded=r.get("status") == "FAILED"):
            if r.get("status") == "FAILED":
                st.error(r.get("error") or "Falha sem mensagem.")
                continue

            rows = r.get("rows")
            if rows is None:
                st.write("Sem contadores disponíveis.")
                continue

            cols = st.columns(6)
            cols[0].metric("Parseou", rows.total_parsed)
            cols[1].metric("Válidas", rows.valid)
            cols[2].metric("Dedup", rows.duplicates_collapsed)
            cols[3].metric("Inseridas", rows.inserted)
            cols[4].metric("Atualizadas", rows.updated)
            cols[5].metric("Metadata", rows.metadata_skipped)

            if rows.invalid_seller or rows.invalid_other:
                st.caption(
                    f"Rejeitadas: {rows.invalid_seller} seller inválido + "
                    f"{rows.invalid_other} outros."
                )
            if rows.duplicates_collapsed:
                st.caption(
                    f"🔁 {rows.duplicates_collapsed} linha(s) duplicada(s) "
                    f"colapsada(s) (mesma chave SKU/seller/marketplace/dia — "
                    f"última ocorrência venceu)."
                )

            samples = r.get("rejection_samples") or []
            if samples:
                st.markdown("**Amostras de rejeição (primeiras 10):**")
                df_rej = pd.DataFrame(samples[:10])
                st.dataframe(df_rej, use_container_width=True, hide_index=True)

            unknown_n = r.get("unknown_sellers_count", 0)
            if unknown_n:
                st.info(
                    f"📝 {unknown_n} seller(s) sem match no mapa canônico. "
                    f"Veja `logs/pricetrack/unknown_sellers.log` e considere "
                    f"expandir `SELLER_CANONICAL` em `pricetrack_importer/seller_map.py`."
                )

            if r.get("log_path"):
                st.caption(f"Log JSON: `{r['log_path']}`")
            if r.get("error") == "ALREADY_IMPORTED_SKIPPED":
                st.info(
                    "Arquivo pulado (já importado anteriormente). "
                    "Marque **Forçar reimport** para sobrescrever."
                )


# ---------------------------------------------------------------------------
# Page — Data Health (cobertura/preenchimento dos campos por plataforma)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _query_health(window_days: int) -> pd.DataFrame:
    """Amostra recente com colunas mínimas para análise de cobertura.

    Seleciona só os campos necessários (não `*`) e pagina sem o cap de 50k do
    query_coletas. Cacheado por 10 min — é um painel de monitoramento, não
    precisa ser tempo real.
    """
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()
    since = str(date.today() - timedelta(days=max(window_days, 1) - 1))
    cols = ("data,plataforma,buy_box_seller,tipo_seller,qtd_sellers,"
            "reputacao_seller,avaliacao,fulfillment,posicao_patrocinada,"
            "seller,preco,patrocinado")
    rows: list = []
    offset = 0
    try:
        while True:
            resp = (
                client.table("coletas").select(cols)
                .gte("data", since)
                .order("data", desc=True)
                .range(offset, offset + _SUPABASE_PAGE - 1)
                .execute()
            )
            if not resp.data:
                break
            rows.extend(resp.data)
            if len(resp.data) < _SUPABASE_PAGE:
                break
            offset += _SUPABASE_PAGE
            if offset > 400_000:  # trava de segurança
                break
    except Exception as exc:
        st.error(f"Consulta de health falhou: {exc}")
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["data"] = pd.to_datetime(df["data"]).dt.date
    return df


def page_data_health() -> None:
    st.title("🩺 Data Health")
    st.caption(
        "Cobertura e preenchimento dos campos por plataforma. Detecta coleta "
        "quebrada ou campo de buy box vazio antes que vire dias de buraco."
    )

    with st.sidebar:
        st.subheader("Filtros")
        window = st.slider("Janela (dias)", 1, 7, 2, key="dh_window")

    with st.spinner("Carregando amostra recente…"):
        df = _query_health(window)

    if df.empty:
        st.warning("Sem dados recentes ou Supabase desconectado.")
        return

    # Campos-chave a monitorar (coluna no DB → rótulo). Esta matriz é a fonte
    # de verdade do que cada scraper entrega — nada de listas hardcoded.
    key_cols = {
        "buy_box_seller":      "Buy Box",
        "tipo_seller":         "Tipo Seller",
        "qtd_sellers":         "Qtd Sellers",
        "reputacao_seller":    "Reputação",
        "avaliacao":           "Avaliação",
        "fulfillment":         "Fulfillment",
        "posicao_patrocinada": "Pos. Patroc.",
        "seller":              "Seller",
        "preco":               "Preço",
        "patrocinado":         "Patrocinado",
    }

    last_seen = df.groupby("plataforma")["data"].max()
    today = date.today()

    rows = []
    for plat, g in df.groupby("plataforma"):
        row = {
            "Plataforma":     plat,
            "Registros":      len(g),
            "Última coleta":  last_seen[plat].strftime("%d/%m"),
            "Dias s/ coleta": (today - last_seen[plat]).days,
        }
        for col, label in key_cols.items():
            row[label] = round(g[col].notna().mean() * 100) if col in g.columns else None
        rows.append(row)
    cov = pd.DataFrame(rows).sort_values("Registros", ascending=False)

    # ── Alertas ────────────────────────────────────────────────────────────────
    stale = cov[cov["Dias s/ coleta"] >= 2]["Plataforma"].tolist()
    no_bb = cov[cov["Buy Box"].fillna(0) == 0]["Plataforma"].tolist()

    c1, c2, c3 = st.columns(3)
    c1.metric("Plataformas no período", cov["Plataforma"].nunique())
    c2.metric("Sem coleta ≥ 2 dias", len(stale))
    c3.metric("Buy box vazio (0%)", len(no_bb))

    if stale:
        st.warning(f"⏳ Sem coleta há ≥ 2 dias: {', '.join(stale)}")
    if no_bb:
        st.warning(f"🚫 Buy box 0% no período: {', '.join(no_bb)}")

    st.divider()

    # ── Heatmap de cobertura ─────────────────────────────────────────────────
    pct_cols = list(key_cols.values())
    heat = cov.set_index("Plataforma")[pct_cols]
    fig = px.imshow(
        heat, text_auto=True, aspect="auto",
        color_continuous_scale="RdYlGn", zmin=0, zmax=100,
        labels=dict(x="Campo", y="Plataforma", color="% preenchido"),
        title="Cobertura de campos por plataforma (%)",
    )
    _apply_chart_style(fig, height=max(380, 26 * len(cov)), hovermode="closest")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Campos booleanos (Fulfillment, Patrocinado) contam `false` como "
        "preenchido — 100% significa que o scraper sempre envia o campo. "
        "`Pos. Patroc.` só é preenchida em anúncios pagos: a célula equivale "
        "ao % de anúncios patrocinados da plataforma."
    )

    st.dataframe(cov, use_container_width=True, hide_index=True)
    _csv_download_btn(
        cov, f"rac_data_health_{today}.csv", "⬇️ Exportar cobertura", key="dh_csv",
    )

    # ── Tendência diária de preenchimento de buy box ─────────────────────────
    if "buy_box_seller" in df.columns:
        st.markdown("**Preenchimento diário de buy box (%) — plataformas de foco**")
        trend = (
            df.assign(bb=df["buy_box_seller"].notna())
            .groupby(["data", "plataforma"], as_index=False)["bb"].mean()
        )
        trend["bb"] = (trend["bb"] * 100).round(0)
        trend["data"] = pd.to_datetime(trend["data"])
        focus = ["Mercado Livre", "Amazon", "Leroy Merlin",
                 "Casas Bahia", "Shopee", "Magalu"]
        trend = trend[trend["plataforma"].isin(focus)]
        if not trend.empty:
            fig2 = px.line(
                trend, x="data", y="bb", color="plataforma", markers=True,
                title="% de linhas com buy_box_seller por dia",
                labels={"data": "Data", "bb": "% buy box", "plataforma": "Plataforma"},
                color_discrete_sequence=_CHART_COLORS,
            )
            _apply_chart_style(fig2, height=400)
            st.plotly_chart(fig2, use_container_width=True)


# ---------------------------------------------------------------------------
# Page 5 — Automação Admin (substitui as páginas manuais Data Cleanup e
# Normalize SKUs: as mesmas rotinas agora rodam sozinhas, sem cliques)
# ---------------------------------------------------------------------------

def _admin_auto_clear_caches() -> None:
    """Invalida caches do dashboard após um run da automação."""
    for fn in (get_depara, get_cobertura_resolucao, get_familia_options,
               get_filter_options, get_mapeado_sem_sku, get_sku_proposals):
        try:
            fn.clear()
        except Exception:
            pass


def _admin_auto_rel_time(iso_str) -> str:
    """ISO timestamp → 'há 3h' (aproximado, para métricas)."""
    if not iso_str:
        return "nunca"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        if hours < 1:
            return f"há {max(int(hours * 60), 0)}min"
        if hours < 48:
            return f"há {hours:.0f}h"
        return f"há {hours / 24:.0f}d"
    except Exception:
        return str(iso_str)


def _admin_auto_render_report(report: dict) -> None:
    """Tabela de etapas de um run (último ou recém-executado)."""
    rows = [
        {
            "Etapa":   s.get("label", s.get("name", "?")),
            "Status":  "✅ ok" if s.get("ok") else "❌ erro",
            "Resumo":  s.get("summary", ""),
            "Duração": f"{float(s.get('duration_s') or 0):.1f}s",
        }
        for s in (report.get("steps") or [])
    ]
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    for s in (report.get("steps") or []):
        if s.get("error"):
            st.error(f"{s.get('label', s.get('name'))}: {s['error']}")


def page_admin_automation() -> None:
    st.title("🤖 Automação Admin")
    st.caption(
        "Manutenção do banco **100% automática** — sem cliques: limpeza de "
        "registros não-AC, preços suspeitos (bug ×10), normalização de "
        "nomes/marcas/plataformas/sellers, seed + resolução da fila REVISAR "
        "(Família & SKU), backfill de SKU (sync + propostas) e refresh do "
        "cache de filtros. "
        "Roda após **cada coleta** (`main.py`), via **cron** "
        "(`scripts/admin_auto.py`) e em **auto-run** ao abrir esta página "
        "quando a última execução tem mais de 24h."
    )

    client = _get_supabase()
    if client is None:
        st.error("Supabase não conectado.")
        return

    from utils.admin_automation import (
        get_last_run,
        get_run_history,
        run_admin_automation,
        should_run,
    )

    last = get_last_run(client)

    # ── Auto-run: cobre quem só usa o dashboard (sem cron/coleta local) ────
    # Re-checa a cada 6h dentro da MESMA sessão (sessões longas não ficam sem
    # manutenção); o gate real de 24h é o should_run, que lê o histórico.
    import time as _time
    _last_check = float(st.session_state.get("_admin_auto_checked_at", 0.0))
    if _time.time() - _last_check > 6 * 3600:
        st.session_state["_admin_auto_checked_at"] = _time.time()
        if should_run(client, min_hours=24):
            with st.status(
                "⏳ Última manutenção há mais de 24h — executando agora "
                "(automático, incremental)…", expanded=True,
            ) as box:
                report = run_admin_automation(trigger="dashboard_auto", client=client)
                for s in report.get("steps", []):
                    st.write(("✅ " if s.get("ok") else "❌ ")
                             + f"{s.get('label')}: {s.get('summary', '')}")
                box.update(
                    label=(f"Manutenção automática concluída — "
                           f"{report['status'].upper()} em {report['duration_s']:.0f}s"),
                    state="complete" if report["errors"] == 0 else "error",
                )
            _admin_auto_clear_caches()
            last = report

    # ── Execução manual (atalho opcional — a automação não depende disto) ──
    c1, c2, c3 = st.columns(3)
    run_now  = c1.button("▶️ Executar agora", type="primary", use_container_width=True,
                         help="Roda a pipeline incremental imediatamente.")
    run_dry  = c2.button("🧪 Simular (dry-run)", use_container_width=True,
                         help="Conta o que seria feito sem gravar nada.")
    run_full = c3.button("🔁 Full scan", use_container_width=True,
                         help="Ignora o watermark e varre o histórico inteiro. "
                              "Use após mudar regras/marcas em config.py.")
    if run_now or run_dry or run_full:
        with st.status("Executando pipeline de manutenção…", expanded=True) as box:
            report = run_admin_automation(
                trigger="dashboard_manual",
                dry_run=run_dry,
                full_scan=run_full,
                client=client,
            )
            for s in report.get("steps", []):
                st.write(("✅ " if s.get("ok") else "❌ ")
                         + f"{s.get('label')}: {s.get('summary', '')}")
            box.update(
                label=(f"Pipeline concluída — {report['status'].upper()} "
                       f"em {report['duration_s']:.0f}s"
                       + (" (simulação)" if report["dry_run"] else "")),
                state="complete" if report["errors"] == 0 else "error",
            )
        if not report["dry_run"]:
            _admin_auto_clear_caches()
        last = report

    st.divider()

    # ── Status do último run ────────────────────────────────────────────────
    if not last:
        st.info(
            "Nenhuma execução registrada ainda. A primeira acontece "
            "automaticamente após a próxima coleta — ou agora, pelos botões acima."
        )
        return

    status_icon = {"ok": "🟢", "partial": "🟡", "error": "🔴",
                   "skipped": "⚪"}.get(str(last.get("status")), "⚪")
    cob = get_cobertura_resolucao()
    pendentes = cob.get("REVISAR", 0) + cob.get("NULL", 0)

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Último run", _admin_auto_rel_time(last.get("started_at")),
              help=str(last.get("started_at", "")))
    m2.metric("Status", f"{status_icon} {str(last.get('status', '?')).upper()}"
              + (" (dry)" if last.get("dry_run") else ""))
    m3.metric("Duração", f"{float(last.get('duration_s') or 0):.0f}s")
    m4.metric("Erros", int(last.get("errors") or 0))
    m5.metric("REVISAR pendentes", f"{pendentes:,}".replace(",", "."),
              help="Linhas de coletas com estado REVISAR ou sem estado — "
                   "a automação as resolve a cada ciclo.")
    m6.metric("MAPEADO s/ SKU", f"{get_mapeado_sem_sku():,}".replace(",", "."),
              help="Linhas com família resolvida mas sem sku_resolvido — fora "
                   "do cruzamento PT × Coletas e do filtro de produto. A etapa "
                   "🔢 Backfill de SKU sincroniza e propõe; aprovação das "
                   "propostas na página 🧬 Família & SKU.")

    st.caption(
        f"Trigger: `{last.get('trigger', '?')}` · "
        f"Watermark: `{last.get('watermark_id') or '—'}` "
        f"(varredura incremental a partir deste id de coletas)"
    )

    _admin_auto_render_report(last)

    # ── Histórico ───────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📜 Histórico de execuções")
    history = get_run_history(client, limit=20)
    if history:
        hist_rows = [
            {
                "Início":   str(h.get("started_at", ""))[:19].replace("T", " "),
                "Trigger":  h.get("trigger", "?"),
                "Status":   h.get("status", "?"),
                "Dry-run":  "sim" if h.get("dry_run") else "não",
                "Duração":  f"{float(h.get('duration_s') or 0):.0f}s",
                "Erros":    int(h.get("errors") or 0),
            }
            for h in history
        ]
        st.dataframe(hist_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("Sem histórico (tabela `admin_automation_runs` vazia).")

    # ── Como funciona / configuração ────────────────────────────────────────
    with st.expander("⚙️ Como funciona & configuração (.env)"):
        st.markdown(
            "**Gatilhos automáticos** — nenhuma ação humana necessária:\n"
            "1. **Pós-coleta** — `main.py` dispara a pipeline após cada upload "
            "(cron 10:00/21:00 BRT na VM e GitHub Actions);\n"
            "2. **Cron dedicado** — `python scripts/admin_auto.py` "
            "(sugestão: `--full` semanal, domingo 03:00);\n"
            "3. **Auto-run no dashboard** — ao abrir esta página com a última "
            "execução há mais de 24h.\n\n"
            "**Fila REVISAR (Família & SKU)** — resolvida em 3 camadas: "
            "regras determinísticas (marca/BTU/ciclo + catálogo), classificador "
            "**LLM** (Anthropic, quando `ANTHROPIC_API_KEY` está no .env) e "
            "heurística terminal para o resto. Reclassificações manuais seguem "
            "possíveis na página 🧬 Família & SKU.\n\n"
            "| Variável (.env) | Efeito | Default |\n"
            "|---|---|---|\n"
            "| `ADMIN_AUTOMATION` | `off` desativa o hook pós-coleta | `on` |\n"
            "| `ADMIN_AUTO_LLM` | `off` pula a camada LLM | `on` |\n"
            "| `ADMIN_AUTO_LLM_MODEL` | Modelo Anthropic | `claude-opus-4-8` |\n"
            "| `ADMIN_AUTO_LLM_MAX_NAMES` | Máx. nomes/run na camada LLM | `400` |\n"
            "| `ADMIN_AUTO_RESIDUAL_POLICY` | `terminal` zera a fila sem LLM; `keep` mantém REVISAR | `terminal` |\n"
            "| `ADMIN_AUTO_RESOLVE_MAX` | Máx. resoluções aplicadas/run | `1000` |\n"
            "| `ADMIN_SKU_BACKFILL` | `off` pula a etapa 🔢 Backfill de SKU | `on` |\n"
            "| `ADMIN_SKU_BACKFILL_APPLY` | `on` aplica propostas de SKU automaticamente (senão: aprovação na página 🧬) | `off` |\n"
            "| `ADMIN_SKU_BACKFILL_MAX` | Máx. sync/aplicações por run | `500` |\n\n"
            "Auditoria: tabela `admin_automation_runs` (migration 006) + "
            "`logs/admin_automation.jsonl`. Resumo no Telegram quando há "
            "mudanças ou erros."
        )


# ---------------------------------------------------------------------------
# Admin: Normalização de Família e SKU (fila REVISAR + edição manual)
# ---------------------------------------------------------------------------

def _render_sku_proposals_panel(client) -> None:
    """💡 Fila de aprovação de SKU — nomes MAPEADO sem SKU com resolução unívoca.

    Origem: validação de 09/07/2026 — 70k linhas MAPEADO sem sku_resolvido
    ficavam fora do cruzamento PT × Coletas, do filtro de produto e da
    precedência de preço. A derivação usa utils/sku_matcher (confiança alta);
    a aplicação é humana (1 clique) — cravação automática segue gated.
    """
    props = get_sku_proposals()
    total_gap = get_mapeado_sem_sku()
    header = (f"💡 Propostas de SKU por atributos — {len(props)} nome(s) com "
              f"resolução unívoca · {total_gap:,} linha(s) MAPEADO sem SKU"
              ).replace(",", ".")
    with st.expander(header, expanded=False):
        st.caption(
            "Derivadas por `utils/sku_matcher` (marca + BTU + ciclo + "
            "família-linha, desempate por voltagem) — só entra aqui o que fixa "
            "**1 SKU** do catálogo; ambíguos ficam de fora. Aplicar grava o SKU "
            "no de-para e re-propaga para `coletas`/`rac_monitoramento` (mesma "
            "RPC do editor abaixo). Aplicação automática pós-coleta: "
            "`ADMIN_SKU_BACKFILL_APPLY=on` (default off)."
        )
        if props.empty:
            st.success(
                "Nenhuma proposta pendente — todo nome derivável já tem SKU. "
                "O que restar sem SKU é ambíguo e precisa do editor abaixo."
            )
            return
        st.dataframe(
            props.rename(columns={
                "nome": "Nome coletado", "familia": "Família (mantida)",
                "sku_proposto": "SKU proposto", "motivo": "Motivo",
            })[["Nome coletado", "Família (mantida)", "SKU proposto", "Motivo"]],
            use_container_width=True, hide_index=True,
        )
        if st.button(f"✅ Aplicar todas ({len(props)})", type="primary",
                     key="apply_sku_props"):
            from utils.admin_automation import apply_sku_resolution
            prog = st.progress(0.0, text="Aplicando propostas…")
            ok = err = linhas = 0
            for i, p in enumerate(props.to_dict("records")):
                try:
                    payload = apply_sku_resolution(
                        client, p["nome"], p.get("familia"), p["sku_proposto"], None)
                    linhas += int(payload.get("coletas_atualizadas", 0) or 0)
                    ok += 1
                except Exception as exc:
                    err += 1
                    st.warning(f"Falhou '{p['nome'][:60]}': {exc}")
                prog.progress((i + 1) / len(props))
            _admin_auto_clear_caches()
            st.success(
                (f"{ok} SKU(s) aplicados · {linhas:,} linha(s) de coletas "
                 f"atualizadas" + (f" · {err} erro(s)" if err else "")
                 ).replace(",", ".")
            )
            st.caption("Recarregue a página para atualizar as contagens.")


def _render_variant_suspects_panel() -> None:
    """⚠️ SKUs com Δ de piso persistente vs PriceTrack — de-para suspeito.

    Validação de 09/07/2026: a cauda |Δ|>25% do pareamento PT × Coletas
    concentra-se em SKUs específicos com desvio de sinal constante — anúncio
    de outra variante (capacidade/voltagem/kit) resolvendo para o código errado.
    """
    sus = get_variant_suspects()
    n = "—" if sus is None else len(sus)
    with st.expander(
        f"⚠️ Suspeitos de variante errada (Δ piso vs PriceTrack, 30d) — {n}",
        expanded=False,
    ):
        st.caption(
            "Compara o **piso diário** por SKU entre Coletas e "
            "`pricetrack_daily` (turno Diário) nos últimos 30 dias. Δ "
            "persistente de ±25%+ no mesmo sentido não é reprecificação — é "
            "anúncio de outra variante resolvendo para o código errado em uma "
            "das fontes. Use *Buscar no nome* abaixo com termos do produto "
            "para achar os títulos e corrigir o de-para."
        )
        if sus is None:
            st.info(
                "RPC `depara_suspeitos_variante` não encontrada — aplique "
                "`docs/migrations/010_depara_suspeitos_variante.sql` no Supabase."
            )
            return
        if sus.empty:
            st.success("Nenhum SKU com divergência persistente nos últimos 30 dias.")
            return
        st.dataframe(
            sus.rename(columns={
                "sku": "SKU", "produto_catalogo": "Produto (catálogo)",
                "dias_pareados": "Dias pareados", "dias_extremos": "Dias |Δ|>25%",
                "delta_mediano_pct": "Δ mediano (%)",
                "piso_pt_mediano": "Piso PT (R$)",
                "piso_coletas_mediano": "Piso Coletas (R$)",
            }),
            use_container_width=True, hide_index=True,
        )


def page_familia_sku_admin() -> None:
    st.title("🧬 Normalização de Família & SKU")
    st.caption("Audita e corrige o de-para `produtos_depara_nome`. "
               "Mudanças propagam para `coletas` e `rac_monitoramento` imediatamente.")
    st.info(
        "A fila REVISAR agora é resolvida automaticamente pela página "
        "**🤖 Automação** (regras → LLM → heurística), após cada coleta e via "
        "cron — nenhuma classificação manual é necessária. Use esta página "
        "apenas para auditar ou corrigir exceções pontuais.",
        icon="🤖",
    )

    client = _get_supabase()
    if client is None:
        st.error("Supabase não conectado.")
        return

    cat = get_catalogo()
    depara = get_depara()

    # ── Métricas no topo ─────────────────────────────────────────────────────
    if not depara.empty:
        m = depara["estado"].value_counts().to_dict()
        cols = st.columns(5)
        cols[0].metric("MAPEADO",     m.get("MAPEADO", 0))
        cols[1].metric("FORA_ESCOPO", m.get("FORA_ESCOPO", 0))
        cols[2].metric("NAO_AC",      m.get("NAO_AC", 0))
        cols[3].metric("REVISAR",     m.get("REVISAR", 0))
        with cols[4]:
            st.write("")  # vertical spacer
            if st.button("🔄 Atualizar cache de filtros",
                         help="Refresh da materialized view usada pelo banner e dropdowns globais."):
                try:
                    client.rpc("refresh_filter_options").execute()
                    get_filter_options.clear()
                    get_cobertura_resolucao.clear()
                    get_depara.clear()
                    st.success("Cache atualizado.")
                except Exception as exc:
                    st.error(f"Falhou: {exc}")

    # ── Reconciliação de SKU (achados da validação 09/07/2026) ─────────────
    _render_sku_proposals_panel(client)
    _render_variant_suspects_panel()

    # ── Filtros para listar nomes ───────────────────────────────────────────
    with st.expander("🔎 Filtros", expanded=True):
        c1, c2, c3 = st.columns([1, 1, 2])
        f_estado = c1.multiselect("Estado", _ESTADOS_RESOLVIDOS, default=["REVISAR"])
        marcas_dispo = sorted(depara["marca_norm"].dropna().unique().tolist()) if not depara.empty else []
        f_marca = c2.multiselect("Marca normalizada", marcas_dispo)
        f_busca = c3.text_input("Buscar no nome", placeholder="ex: ecomaster 12000")
        f_limit = st.slider("Quantos nomes mostrar", 5, 100, 25, step=5)

    if depara.empty:
        st.info("De-para vazio.")
        return

    df = depara.copy()
    if f_estado:
        df = df[df["estado"].isin(f_estado)]
    if f_marca:
        df = df[df["marca_norm"].isin(f_marca)]
    if f_busca:
        df = df[df["nome_coletado"].str.contains(f_busca, case=False, na=False, regex=False)]

    st.caption(f"{len(df)} nome(s) no filtro · mostrando {min(len(df), f_limit)}")

    df_view = df.head(f_limit)
    if df_view.empty:
        st.warning("Nenhum nome bate com o filtro.")
        return

    # Opções de família = catálogo + genéricas em uso
    fams_catalogo = sorted(cat["familia"].dropna().unique().tolist()) if not cat.empty else []
    fams_genericas = sorted([f for f in depara["familia"].dropna().unique()
                              if _familia_is_generica(f)])
    todas_familias = ["(nenhuma)"] + fams_catalogo + fams_genericas
    skus_catalogo = ["(nenhum)"] + (sorted(cat["sku"].dropna().unique().tolist()) if not cat.empty else [])

    st.divider()

    # ── Editor linha-a-linha ────────────────────────────────────────────────
    for _, row in df_view.iterrows():
        nome = row["nome_coletado"]
        with st.expander(f"**{nome}** · {row['estado']}"
                          f" · {row.get('marca_norm') or 'marca?'}"
                          f" · fam={row.get('familia') or '—'}", expanded=False):
            c1, c2, c3 = st.columns(3)
            est_atual = row["estado"] if row["estado"] in _ESTADOS_RESOLVIDOS else "REVISAR"
            novo_estado = c1.selectbox(
                "Estado", _ESTADOS_RESOLVIDOS,
                index=_ESTADOS_RESOLVIDOS.index(est_atual),
                key=f"est_{nome}",
            )
            fam_atual = row.get("familia") or "(nenhuma)"
            fam_idx = todas_familias.index(fam_atual) if fam_atual in todas_familias else 0
            nova_familia = c2.selectbox(
                "Família",
                todas_familias,
                index=fam_idx,
                format_func=lambda f: _familia_display(f) if f != "(nenhuma)" else f,
                key=f"fam_{nome}",
                disabled=(novo_estado != "MAPEADO"),
                help="Só usado quando estado=MAPEADO. Genéricas: <MARCA>-<BTU>-<CICLO>",
            )
            sku_atual = row.get("sku") or "(nenhum)"
            sku_idx = skus_catalogo.index(sku_atual) if sku_atual in skus_catalogo else 0
            novo_sku = c3.selectbox(
                "SKU do catálogo",
                skus_catalogo,
                index=sku_idx,
                key=f"sku_{nome}",
                disabled=(novo_estado != "MAPEADO"),
            )

            # Quantas linhas serão afetadas
            try:
                ct_c = client.table("coletas").select("id", count="exact", head=True) \
                             .eq("produto", nome).execute().count or 0
                ct_r = client.table("rac_monitoramento").select("id", count="exact", head=True) \
                             .eq("produto_sku", nome).execute().count or 0
                st.caption(f"💾 Afetará {ct_c:,} linha(s) em coletas e "
                           f"{ct_r:,} em rac_monitoramento.".replace(",", "."))
            except Exception:
                pass

            if st.button("💾 Salvar", key=f"save_{nome}", type="primary"):
                fam_val = None if nova_familia == "(nenhuma)" else nova_familia
                sku_val = None if novo_sku == "(nenhum)" else novo_sku
                # row.get('marca_norm') vem do pandas e pode ser NaN — JSON
                # serialização do RPC falha com "Out of range float values".
                marca_raw = row.get("marca_norm")
                marca_val = None if (marca_raw is None or (isinstance(marca_raw, float) and pd.isna(marca_raw))) else marca_raw
                try:
                    res = client.rpc("admin_normalizar_nome", {
                        "p_nome":    nome,
                        "p_estado":  novo_estado,
                        "p_familia": fam_val,
                        "p_sku":     sku_val,
                        "p_marca":   marca_val,
                    }).execute()
                    payload = res.data if isinstance(res.data, dict) else (res.data[0] if res.data else {})
                    st.success(
                        f"✅ Salvo · coletas atualizadas: {payload.get('coletas_atualizadas', 0)} · "
                        f"rac_monitoramento: {payload.get('rac_monitoramento_atualizadas', 0)}"
                    )
                    # Invalida caches dependentes
                    get_depara.clear()
                    get_cobertura_resolucao.clear()
                    get_familia_options.clear()
                except Exception as exc:
                    st.error(f"Erro ao salvar: {exc}")

    st.divider()

    # ── Edição livre por nome (busca + classificação rápida) ───────────────
    with st.expander("✏️ Classificar nome avulso (não precisa estar na fila)"):
        nome_livre = st.text_input("Nome coletado exato", key="adm_nome_livre")
        c1, c2, c3 = st.columns(3)
        est_livre = c1.selectbox("Estado", _ESTADOS_RESOLVIDOS, key="adm_est_livre")
        fam_livre = c2.selectbox("Família", todas_familias, key="adm_fam_livre",
                                 disabled=(est_livre != "MAPEADO"),
                                 format_func=lambda f: _familia_display(f) if f != "(nenhuma)" else f)
        sku_livre = c3.selectbox("SKU", skus_catalogo, key="adm_sku_livre",
                                 disabled=(est_livre != "MAPEADO"))
        if st.button("Aplicar", key="adm_apply_livre", type="primary",
                     disabled=not nome_livre.strip()):
            try:
                res = client.rpc("admin_normalizar_nome", {
                    "p_nome":    nome_livre.strip(),
                    "p_estado":  est_livre,
                    "p_familia": None if fam_livre == "(nenhuma)" else fam_livre,
                    "p_sku":     None if sku_livre == "(nenhum)" else sku_livre,
                    "p_marca":   None,
                }).execute()
                payload = res.data if isinstance(res.data, dict) else (res.data[0] if res.data else {})
                st.success(
                    f"✅ Aplicado · coletas: {payload.get('coletas_atualizadas', 0)} · "
                    f"rac_monitoramento: {payload.get('rac_monitoramento_atualizadas', 0)}"
                )
                get_depara.clear(); get_cobertura_resolucao.clear(); get_familia_options.clear()
            except Exception as exc:
                st.error(f"Erro: {exc}")


# ---------------------------------------------------------------------------
# Page 7 — BuyBox Position
# ---------------------------------------------------------------------------

def page_buybox_position():
    st.title("🏆 BuyBox Position")
    st.caption(
        "Quem está em posição #1 para cada produto/plataforma? "
        "Analise quais marcas e sellers dominam o topo das buscas."
    )
    st.info(
        "ℹ️ Esta página usa a **posição orgânica/geral ≤ N** como *proxy* de buy box "
        "(funciona para todas as plataformas). Para o **vencedor real da buy box** "
        "(campo `buy_box_seller`, mix 1P/3P e nº de sellers competindo), veja a página "
        "**👑 Share of Buy Box**.",
        icon="ℹ️",
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
        sel_familias, sel_skus_resolvidos = _render_familia_sku_filters(sel_brands, "bb")
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
            familias_resolvidas=sel_familias or None,
            skus_resolvidos=sel_skus_resolvidos or None,
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

        group_opts = ["Brand", "Platform", "Seller (Buy Box)"]
        group_choice = st.radio(
            "Group by", group_opts, horizontal=True, key="bb_grp"
        )
        group_col = {
            "Brand": "marca",
            "Platform": "plataforma",
            "Seller (Buy Box)": "buy_box_seller",
        }[group_choice]

        df_grp = df_top
        if group_col == "buy_box_seller" and "buy_box_seller" in df_top.columns:
            df_grp = df_top[
                df_top["buy_box_seller"].notna()
                & (df_top["buy_box_seller"].astype(str).str.strip() != "")
            ]
            if df_grp.empty:
                st.info(
                    "Nenhuma plataforma no filtro atual expõe `buy_box_seller`. "
                    "Tente ML, Amazon ou Leroy num período recente."
                )

        if group_col not in df_top.columns or "data" not in df_top.columns:
            st.info("Required columns not available.")
        elif not df_grp.empty:
            timeline = (
                df_grp
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
                "preco", "seller", "buy_box_seller", "tipo_seller", "qtd_sellers",
                "keyword", "tag",
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
# Page — Share of Buy Box (vencedor REAL da buy box, via campo buy_box_seller)
# ---------------------------------------------------------------------------

def page_share_of_buybox() -> None:
    st.title("👑 Share of Buy Box")
    st.caption(
        "Quem vence a oferta principal (buy box) de cada produto — por seller, "
        "tipo (1P/3P/Loja Oficial) e nível de competição. Baseado no campo real "
        "`buy_box_seller`, não em posição de busca."
    )

    with st.sidebar:
        st.subheader("Filtros")
        date_range = st.date_input(
            "Período",
            value=(date.today() - timedelta(days=14), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="sbb_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=14)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()
        sel_platforms = st.multiselect("Plataformas", opts["platforms"], key="sbb_platforms")
        sel_brands    = st.multiselect("Marcas", opts["brands"], key="sbb_brands")
        sel_familias, sel_skus_resolvidos = _render_familia_sku_filters(sel_brands, "sbb")
        modo = st.radio(
            "Modo de visualização",
            ["Snapshot oficial (último run)", "Todos os runs (auditoria)"],
            index=0, key="sbb_modo",
        )
        load_btn = st.button("🔄 Carregar Buy Box", type="primary", use_container_width=True)

    if not load_btn:
        st.info("Defina os filtros na barra lateral e clique em **Carregar Buy Box**.")
        st.caption(
            "💡 A cobertura de `buy_box_seller` varia por plataforma e scraper — "
            "consulte a matriz campo × plataforma na página 🩺 Data Health. "
            "A coleta de buy box começou no fim de Maio/2026."
        )
        return

    with st.spinner("Carregando dados…"):
        df = query_coletas(
            start_date, end_date,
            platforms=sel_platforms or None,
            brands=sel_brands or None,
            familias_resolvidas=sel_familias or None,
            skus_resolvidos=sel_skus_resolvidos or None,
            limit=50000,
        )

    if modo.startswith("Snapshot"):
        df = _filter_latest_run(df)

    if df.empty or "buy_box_seller" not in df.columns:
        st.warning("Nenhum dado com informação de buy box para os filtros selecionados.")
        return

    # Mantém só linhas com vencedor real da buy box
    bb = df[
        df["buy_box_seller"].notna()
        & (df["buy_box_seller"].astype(str).str.strip() != "")
    ].copy()

    if bb.empty:
        st.warning(
            "As plataformas/períodos selecionados não têm `buy_box_seller` preenchido. "
            "Veja quais plataformas entregam o campo na página 🩺 Data Health e "
            "tente um período recente."
        )
        cov = (
            df.assign(tem_bb=df["buy_box_seller"].notna())
            .groupby("plataforma")["tem_bb"].mean().mul(100).round(0)
            .reset_index().rename(columns={"tem_bb": "% com buy box", "plataforma": "Plataforma"})
            .sort_values("% com buy box", ascending=False)
        )
        st.markdown("**Cobertura de buy box por plataforma no período:**")
        st.dataframe(cov, use_container_width=True, hide_index=True)
        return

    # ── KPIs ──────────────────────────────────────────────────────────────────
    n_records   = len(bb)
    n_sellers   = bb["buy_box_seller"].nunique()
    n_products  = bb["produto"].nunique()   if "produto"   in bb.columns else 0
    n_platforms = bb["plataforma"].nunique() if "plataforma" in bb.columns else 0

    share_1p = None
    if "tipo_seller" in bb.columns:
        tipo = bb["tipo_seller"].fillna("").astype(str).str.strip()
        non_empty = tipo.ne("")
        if non_empty.any():
            first_party = tipo.str.contains("1P|Loja Oficial|Mall", case=False, regex=True)
            share_1p = first_party.sum() / non_empty.sum() * 100

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Registros c/ buy box", f"{n_records:,}")
    c2.metric("Sellers distintos", f"{n_sellers:,}")
    c3.metric("Produtos", f"{n_products:,}")
    c4.metric("Plataformas", str(n_platforms))
    c5.metric(
        "Share 1P / Oficial",
        f"{share_1p:.0f}%" if share_1p is not None else "—",
        help="% de buy box vencida por 1P / Loja Oficial / Mall (campo Tipo Seller)",
    )
    st.divider()

    tab_seller, tab_tipo, tab_comp, tab_timeline, tab_detail = st.tabs(
        ["👑 Share por Seller", "🏷️ Mix 1P/3P", "⚔️ Competição",
         "📅 Timeline", "📋 Detalhe"]
    )

    # ── Tab 1: Share por seller ─────────────────────────────────────────────
    with tab_seller:
        wins = (
            bb.groupby("buy_box_seller", as_index=False).size()
            .rename(columns={"size": "Buy box wins", "buy_box_seller": "Seller"})
            .sort_values("Buy box wins", ascending=False)
        )
        wins["Share (%)"] = (wins["Buy box wins"] / wins["Buy box wins"].sum() * 100).round(1)

        col_chart, col_table = st.columns([2, 1])
        with col_chart:
            top = wins.head(15)
            fig = px.bar(
                top, x="Buy box wins", y="Seller", orientation="h",
                color="Seller", color_discrete_sequence=_CHART_COLORS,
                text="Share (%)", title="Top sellers por buy box vencida",
            )
            fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
            fig.update_layout(showlegend=False,
                              yaxis={"categoryorder": "total ascending"})
            _apply_chart_style(fig, height=480, hovermode="closest")
            st.plotly_chart(fig, use_container_width=True)
        with col_table:
            st.dataframe(wins, use_container_width=True, hide_index=True, height=480)
        _csv_download_btn(
            wins, f"rac_share_buybox_seller_{start_date}_{end_date}.csv",
            "⬇️ Exportar share por seller", key="sbb_seller_csv",
        )

    # ── Tab 2: Mix 1P / 3P (tipo_seller) ────────────────────────────────────
    with tab_tipo:
        has_tipo = (
            "tipo_seller" in bb.columns
            and not bb["tipo_seller"].fillna("").astype(str).str.strip().eq("").all()
        )
        if not has_tipo:
            st.info(
                "Nenhum registro com `tipo_seller` preenchido nos filtros/período "
                "selecionados. A cobertura varia por plataforma e scraper — veja a "
                "matriz campo × plataforma na página 🩺 Data Health."
            )
        else:
            tdf = bb[bb["tipo_seller"].notna()
                     & (bb["tipo_seller"].astype(str).str.strip() != "")]
            mix = (
                tdf.groupby(["plataforma", "tipo_seller"], as_index=False).size()
                .rename(columns={"size": "Buy box wins"})
            )
            fig = px.bar(
                mix, x="plataforma", y="Buy box wins", color="tipo_seller",
                barmode="stack", title="Buy box por tipo de seller e plataforma",
                color_discrete_sequence=_CHART_COLORS,
                labels={"plataforma": "Plataforma", "tipo_seller": "Tipo Seller"},
            )
            _apply_chart_style(fig, height=440)
            st.plotly_chart(fig, use_container_width=True)

            overall = (
                tdf.groupby("tipo_seller", as_index=False).size()
                .rename(columns={"size": "Buy box wins", "tipo_seller": "Tipo Seller"})
                .sort_values("Buy box wins", ascending=False)
            )
            overall["Share (%)"] = (
                overall["Buy box wins"] / overall["Buy box wins"].sum() * 100
            ).round(1)
            st.dataframe(overall, use_container_width=True, hide_index=True)

    # ── Tab 3: Competição (qtd_sellers) ─────────────────────────────────────
    with tab_comp:
        has_qtd = "qtd_sellers" in bb.columns and not bb["qtd_sellers"].dropna().empty
        if not has_qtd:
            st.info(
                "Nenhum registro com `qtd_sellers` preenchido nos filtros/período "
                "selecionados. A cobertura varia por plataforma e scraper — veja a "
                "matriz campo × plataforma na página 🩺 Data Health."
            )
        else:
            comp = bb.dropna(subset=["qtd_sellers"])
            by_plat = (
                comp.groupby("plataforma", as_index=False)["qtd_sellers"].mean()
                .rename(columns={"qtd_sellers": "Média de sellers/listagem"})
                .sort_values("Média de sellers/listagem", ascending=False)
            )
            by_plat["Média de sellers/listagem"] = by_plat["Média de sellers/listagem"].round(1)
            fig = px.bar(
                by_plat, x="plataforma", y="Média de sellers/listagem",
                color="plataforma", color_discrete_sequence=_CHART_COLORS,
                title="Competição média por listagem (nº de sellers)",
            )
            fig.update_layout(showlegend=False)
            _apply_chart_style(fig, height=420)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "Mais sellers por listagem = buy box mais disputada — útil para "
                "priorizar onde defender/atacar preço."
            )

    # ── Tab 4: Timeline (troca de dono da buy box) ──────────────────────────
    with tab_timeline:
        if "data" not in bb.columns:
            st.info("Coluna 'data' indisponível.")
        else:
            top_sellers = bb["buy_box_seller"].value_counts().head(8).index.tolist()
            tl = (
                bb[bb["buy_box_seller"].isin(top_sellers)]
                .groupby(["data", "buy_box_seller"], as_index=False).size()
                .rename(columns={"size": "Buy box wins", "buy_box_seller": "Seller"})
            )
            tl["data"] = pd.to_datetime(tl["data"])
            fig = px.line(
                tl, x="data", y="Buy box wins", color="Seller", markers=True,
                title="Evolução do share de buy box (top 8 sellers)",
                labels={"data": "Data"}, color_discrete_sequence=_CHART_COLORS,
            )
            fig.update_traces(line=dict(width=2.5), marker=dict(size=6))
            _apply_chart_style(fig, height=460)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Cruzamentos entre linhas indicam troca de dono da buy box no agregado.")

    # ── Tab 5: Detalhe ──────────────────────────────────────────────────────
    with tab_detail:
        cols = [c for c in [
            "data", "turno", "plataforma", "marca", "produto",
            "buy_box_seller", "tipo_seller", "qtd_sellers", "preco",
            "posicao_geral", "keyword",
        ] if c in bb.columns]
        st.dataframe(
            bb[cols].sort_values(["data", "plataforma"], ascending=[False, True]),
            use_container_width=True, height=500,
            column_config={
                "data":          st.column_config.DateColumn("Data"),
                "preco":         st.column_config.NumberColumn("Preço (R$)", format="R$ %.2f"),
                "qtd_sellers":   st.column_config.NumberColumn("Qtd Sellers"),
                "posicao_geral": st.column_config.NumberColumn("Posição"),
            },
        )
        _csv_download_btn(
            bb[cols], f"rac_buybox_detalhe_{start_date}_{end_date}.csv",
            "⬇️ Exportar detalhe", key="sbb_detail_csv",
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
        sel_familias, sel_skus_resolvidos = _render_familia_sku_filters(sel_brands, "av")
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
            familias_resolvidas=sel_familias or None,
            skus_resolvidos=sel_skus_resolvidos or None,
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

    tab_share, tab_vis, tab_timeline, tab_detail = st.tabs(
        ["📊 Share", "🎯 Visibility Score", "📅 Timeline", "📋 Detail"]
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

    # ── Tab 2: Visibility Score ponderado por posição ────────────────────────
    with tab_vis:
        st.subheader("Visibility Score — share of shelf ponderado por posição")
        st.caption(
            "Cada aparição vale `1/posição_geral`: o slot 1 vale 1.0, o slot 10 "
            "vale 0.1. O share ponderado premia quem domina o topo da busca; o "
            "share simples conta presença em qualquer posição."
        )
        if "marca" not in df_all.columns:
            st.info("Brand (marca) column not available.")
        else:
            vis = df_all[df_all["posicao_geral"] > 0].copy()
            if vis.empty:
                st.info("No records with valid position for the visibility score.")
            else:
                vis["_peso"] = 1.0 / vis["posicao_geral"].astype(float)

                por_marca = (
                    vis.groupby("marca")
                    .agg(**{"Aparições": ("_peso", "size"),
                            "_peso_total": ("_peso", "sum")})
                    .reset_index()
                )
                por_marca["Share simples (%)"] = (
                    por_marca["Aparições"] / por_marca["Aparições"].sum() * 100
                ).round(2)
                por_marca["Share ponderado (%)"] = (
                    por_marca["_peso_total"] / por_marca["_peso_total"].sum() * 100
                ).round(2)
                por_marca["Δ (p.p.)"] = (
                    por_marca["Share ponderado (%)"] - por_marca["Share simples (%)"]
                ).round(2)
                por_marca = por_marca.sort_values("Share ponderado (%)", ascending=False)

                top_vis = por_marca.head(12)
                comp = top_vis.melt(
                    id_vars="marca",
                    value_vars=["Share simples (%)", "Share ponderado (%)"],
                    var_name="Métrica", value_name="Share (%)",
                )
                fig_cmp = px.bar(
                    comp, x="marca", y="Share (%)", color="Métrica",
                    barmode="group", color_discrete_sequence=_CHART_COLORS,
                    title="Share ponderado (1/posição) vs share simples, por marca",
                    labels={"marca": "Marca"},
                )
                _apply_chart_style(fig_cmp, height=440)
                st.plotly_chart(fig_cmp, use_container_width=True)
                st.caption(
                    "Δ positivo = a marca rankeia melhor do que o volume sugere "
                    "(slots no topo); Δ negativo = presença grande mas enterrada."
                )
                st.dataframe(
                    _style_midea_df(
                        por_marca.drop(columns="_peso_total")
                        .rename(columns={"marca": "Marca"}),
                        brand_col="Marca",
                    ),
                    use_container_width=True, hide_index=True,
                )
                _csv_download_btn(
                    por_marca.drop(columns="_peso_total"),
                    f"rac_visibility_score_{start_date}_{end_date}.csv",
                    "⬇️ Exportar visibility score", key="av_vis_csv",
                )

                # Heatmap marca × plataforma — share ponderado dentro da plataforma
                if "plataforma" in vis.columns:
                    top10_marcas = por_marca.head(10)["marca"].tolist()
                    sub = vis[vis["marca"].isin(top10_marcas)]
                    heat_plat = sub.pivot_table(
                        index="marca", columns="plataforma",
                        values="_peso", aggfunc="sum",
                    )
                    plat_tot = vis.pivot_table(
                        columns="plataforma", values="_peso", aggfunc="sum",
                    )
                    heat_plat = (
                        heat_plat.div(plat_tot.iloc[0], axis=1) * 100
                    ).round(1)
                    fig_hp = px.imshow(
                        heat_plat, text_auto=".1f", aspect="auto",
                        color_continuous_scale="Blues",
                        labels=dict(x="Plataforma", y="Marca",
                                    color="Share ponderado (%)"),
                        title="Share ponderado por marca × plataforma (% dentro da plataforma)",
                    )
                    _apply_chart_style(
                        fig_hp, height=max(380, 32 * len(heat_plat)),
                        hovermode="closest",
                    )
                    st.plotly_chart(fig_hp, use_container_width=True)

                # Segmentação por categoria de keyword (campo `categoria`)
                if "categoria" in vis.columns and vis["categoria"].notna().any():
                    seg = vis[vis["categoria"].notna()
                              & (vis["categoria"].astype(str).str.strip() != "")]
                    top10_marcas = por_marca.head(10)["marca"].tolist()
                    sub_cat = seg[seg["marca"].isin(top10_marcas)]
                    if not sub_cat.empty:
                        heat_cat = sub_cat.pivot_table(
                            index="marca", columns="categoria",
                            values="_peso", aggfunc="sum",
                        )
                        cat_tot = seg.pivot_table(
                            columns="categoria", values="_peso", aggfunc="sum",
                        )
                        heat_cat = (
                            heat_cat.div(cat_tot.iloc[0], axis=1) * 100
                        ).round(1)
                        fig_hc = px.imshow(
                            heat_cat, text_auto=".1f", aspect="auto",
                            color_continuous_scale="Greens",
                            labels=dict(x="Categoria Keyword", y="Marca",
                                        color="Share ponderado (%)"),
                            title="Share ponderado por marca × categoria de keyword (% dentro da categoria)",
                        )
                        _apply_chart_style(
                            fig_hc, height=max(380, 32 * len(heat_cat)),
                            hovermode="closest",
                        )
                        st.plotly_chart(fig_hc, use_container_width=True)
                        st.caption(
                            "Categoria Keyword segmenta a intenção da busca "
                            "(Genérica, Capacidade BTU, Marca, Intenção Compra…): "
                            "mostra onde cada marca domina o topo do funil."
                        )
                else:
                    st.info(
                        "Campo `categoria` (Categoria Keyword) sem dados no período — "
                        "segmentação por categoria indisponível."
                    )

    # ── Tab 3: Timeline ──────────────────────────────────────────────────────
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

    # ── Tab 4: Detail ────────────────────────────────────────────────────────
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
# Page — Reputação & Avaliações
# ---------------------------------------------------------------------------

_MCJV_BRANDS = {"Midea", "Springer Midea", "Springer"}


def page_reputacao() -> None:
    """Página ⭐ Reputação & Avaliações.

    Usa as colunas `avaliacao`, `qtd_avaliacoes`, `reputacao_seller` e
    `fulfillment` já carregadas por query_coletas — campos que até então não
    apareciam em nenhuma análise do dashboard.
    """
    st.title("⭐ Reputação & Avaliações")
    st.caption(
        "Avaliação dos produtos, volume de reviews, reputação do seller e "
        "fulfillment — e como isso se relaciona com posição orgânica e buy box."
    )

    with st.sidebar:
        st.subheader("Filtros")
        date_range = st.date_input(
            "Período",
            value=(date.today() - timedelta(days=14), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="rep_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=14)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()
        sel_platforms = st.multiselect("Plataformas", opts["platforms"], key="rep_platforms")
        sel_brands    = st.multiselect("Marcas", opts["brands"], key="rep_brands")
        sel_familias, sel_skus_resolvidos = _render_familia_sku_filters(sel_brands, "rep")
        min_cell = st.number_input(
            "Mín. de registros avaliados por célula (heatmap)",
            min_value=1, max_value=100, value=5, key="rep_min_cell",
        )
        load_btn = st.button("🔄 Carregar Avaliações", type="primary",
                             use_container_width=True)

    if not load_btn:
        st.info("Defina os filtros na barra lateral e clique em **Carregar Avaliações**.")
        st.caption(
            "💡 A cobertura de `avaliacao` e `reputacao_seller` varia por plataforma "
            "e scraper — veja a matriz campo × plataforma na página 🩺 Data Health."
        )
        return

    with st.spinner("Carregando dados…"):
        df = query_coletas(
            start_date, end_date,
            platforms=sel_platforms or None,
            brands=sel_brands or None,
            familias_resolvidas=sel_familias or None,
            skus_resolvidos=sel_skus_resolvidos or None,
            limit=50000,
        )

    if df.empty or "avaliacao" not in df.columns:
        st.warning("Nenhum dado com avaliação para os filtros selecionados.")
        return

    rated = df[df["avaliacao"].notna()].copy()
    if rated.empty:
        st.warning("Nenhum registro com `avaliacao` preenchida no período/filtros.")
        cov = (
            df.assign(tem_aval=df["avaliacao"].notna())
            .groupby("plataforma")["tem_aval"].mean().mul(100).round(0)
            .reset_index()
            .rename(columns={"tem_aval": "% com avaliação", "plataforma": "Plataforma"})
            .sort_values("% com avaliação", ascending=False)
        )
        st.markdown("**Cobertura de avaliação por plataforma no período:**")
        st.dataframe(cov, use_container_width=True, hide_index=True)
        return

    has_marca   = "marca" in rated.columns
    has_reviews = "qtd_avaliacoes" in rated.columns

    # ── KPIs: MCJV vs concorrência ────────────────────────────────────────────
    is_mcjv = (
        rated["marca"].isin(_MCJV_BRANDS) if has_marca
        else pd.Series(False, index=rated.index)
    )
    aval_mcjv = rated.loc[is_mcjv, "avaliacao"].mean()
    conc_mask = ~is_mcjv
    if has_marca:
        conc_mask &= rated["marca"].ne("Desconhecida")
    aval_conc = rated.loc[conc_mask, "avaliacao"].mean()
    gap = (
        aval_mcjv - aval_conc
        if pd.notna(aval_mcjv) and pd.notna(aval_conc) else None
    )

    reviews_marca = pd.DataFrame()
    total_reviews_mcjv = None
    if has_reviews and has_marca and "produto" in rated.columns:
        rev = rated.dropna(subset=["qtd_avaliacoes"])
        if not rev.empty:
            # máx. por produto: o mesmo anúncio aparece em várias keywords/runs
            por_produto = rev.groupby(
                ["marca", "produto"], as_index=False
            )["qtd_avaliacoes"].max()
            reviews_marca = (
                por_produto.groupby("marca", as_index=False)["qtd_avaliacoes"].sum()
                .rename(columns={"qtd_avaliacoes": "Total de reviews"})
                .sort_values("Total de reviews", ascending=False)
            )
            total_reviews_mcjv = int(
                reviews_marca.loc[
                    reviews_marca["marca"].isin(_MCJV_BRANDS), "Total de reviews"
                ].sum()
            )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Registros c/ avaliação", f"{len(rated):,}")
    c2.metric(
        "Avaliação média MCJV",
        f"{aval_mcjv:.2f} ⭐" if pd.notna(aval_mcjv) else "—",
        help="Midea + Springer Midea + Springer",
    )
    c3.metric(
        "Média concorrentes",
        f"{aval_conc:.2f} ⭐" if pd.notna(aval_conc) else "—",
        help="Todas as marcas identificadas fora do grupo MCJV",
    )
    c4.metric(
        "Gap MCJV − concorrência",
        f"{gap:+.2f}" if gap is not None else "—",
        help="Positivo = produtos MCJV melhor avaliados que a concorrência",
    )
    c5.metric(
        "Reviews MCJV",
        f"{total_reviews_mcjv:,}" if total_reviews_mcjv is not None else "—",
        help="Soma do nº de avaliações dos produtos MCJV (máx. por produto, sem dupla contagem)",
    )
    st.divider()

    tab_heat, tab_scatter, tab_rep, tab_ful = st.tabs(
        ["🗺️ Marca × Plataforma", "🔵 Posição × Avaliação",
         "🏅 Reputação do Seller", "📦 Fulfillment"]
    )

    # ── Tab 1: heatmap de avaliação média + volume de reviews ───────────────
    with tab_heat:
        if not has_marca or "plataforma" not in rated.columns:
            st.info("Colunas marca/plataforma indisponíveis.")
        else:
            top_marcas = rated["marca"].value_counts().head(12).index
            sub = rated[rated["marca"].isin(top_marcas)]
            medias = sub.pivot_table(
                index="marca", columns="plataforma",
                values="avaliacao", aggfunc="mean",
            )
            counts = sub.pivot_table(
                index="marca", columns="plataforma",
                values="avaliacao", aggfunc="count",
            )
            medias = medias.where(counts >= min_cell).round(2)
            if medias.dropna(how="all").dropna(how="all", axis=1).empty:
                st.info(
                    f"Nenhuma célula marca × plataforma com ≥ {min_cell} "
                    "registros avaliados. Reduza o mínimo na barra lateral."
                )
            else:
                fig = px.imshow(
                    medias, text_auto=".2f", aspect="auto",
                    color_continuous_scale="RdYlGn", zmin=3, zmax=5,
                    labels=dict(x="Plataforma", y="Marca", color="Avaliação média"),
                    title=f"Avaliação média por marca × plataforma (células com ≥ {min_cell} registros)",
                )
                _apply_chart_style(
                    fig, height=max(380, 32 * len(medias)), hovermode="closest"
                )
                st.plotly_chart(fig, use_container_width=True)
                _csv_download_btn(
                    medias.reset_index(),
                    f"rac_avaliacao_heatmap_{start_date}_{end_date}.csv",
                    "⬇️ Exportar heatmap", key="rep_heat_csv",
                )

            if not reviews_marca.empty:
                top_rev = reviews_marca.head(12)
                fig_rev = px.bar(
                    top_rev, x="marca", y="Total de reviews",
                    color="marca", color_discrete_map=_brand_color_map(top_rev["marca"]),
                    title="Total de reviews por marca (máx. por produto — proxy de base instalada)",
                    labels={"marca": "Marca"},
                )
                fig_rev.update_layout(showlegend=False)
                _apply_chart_style(fig_rev, height=400, hovermode="closest")
                st.plotly_chart(fig_rev, use_container_width=True)

    # ── Tab 2: scatter posição orgânica × avaliação ──────────────────────────
    with tab_scatter:
        st.markdown("**Produtos bem avaliados rankeiam melhor?**")
        needed = {"posicao_organica", "produto"}
        if not (needed <= set(rated.columns) and has_marca):
            st.info("Colunas posicao_organica/produto/marca indisponíveis.")
        else:
            base = rated.dropna(subset=["posicao_organica"])
            if base.empty:
                st.info("Nenhum registro com posição orgânica e avaliação simultâneas.")
            else:
                agg_kwargs = {
                    "aval":  ("avaliacao", "mean"),
                    "pos":   ("posicao_organica", "mean"),
                    "n":     ("avaliacao", "size"),
                }
                if has_reviews:
                    agg_kwargs["reviews"] = ("qtd_avaliacoes", "max")
                prod = base.groupby(["produto", "marca"], as_index=False).agg(**agg_kwargs)
                if has_reviews:
                    prod["reviews"] = (
                        pd.to_numeric(prod["reviews"], errors="coerce")
                        .fillna(1).clip(lower=1).astype(float)
                    )
                else:
                    prod["reviews"] = 1.0
                prod = prod.sort_values("reviews", ascending=False).head(400)
                fig = px.scatter(
                    prod, x="aval", y="pos", size="reviews", color="marca",
                    color_discrete_map=_brand_color_map(prod["marca"]),
                    hover_name="produto", size_max=42,
                    labels={"aval": "Avaliação média", "pos": "Posição orgânica média",
                            "reviews": "Qtd avaliações", "marca": "Marca"},
                    title="Posição orgânica média × avaliação (bolha = nº de avaliações)",
                )
                fig.update_yaxes(autorange="reversed")
                _apply_chart_style(fig, height=520, hovermode="closest")
                st.plotly_chart(fig, use_container_width=True)
                corr = prod["aval"].corr(prod["pos"])
                st.caption(
                    "Eixo Y invertido: quanto mais alto o ponto, melhor a posição. "
                    + (
                        f"Correlação avaliação × posição: {corr:+.2f} "
                        "(negativa = melhor avaliação anda junto com melhor posição)."
                        if pd.notna(corr) else ""
                    )
                )
                _csv_download_btn(
                    prod.rename(columns={
                        "aval": "Avaliação média", "pos": "Posição orgânica média",
                        "n": "Registros", "reviews": "Qtd avaliações",
                    }),
                    f"rac_posicao_avaliacao_{start_date}_{end_date}.csv",
                    "⬇️ Exportar produtos", key="rep_scatter_csv",
                )

    # ── Tab 3: buy box win-rate por reputação do seller ─────────────────────
    with tab_rep:
        st.info(
            "Cobertura limitada: `reputacao_seller` só é preenchida pela coleta "
            "complementar via API oficial do ML (`python main.py --platforms "
            "ml_api` — requer ML_APP_ID/ML_APP_SECRET no .env e o setup único "
            "`python scripts/ml_oauth_setup.py`) e pela Shopee — confira "
            "a página 🩺 Data Health."
        )
        if not {"reputacao_seller", "buy_box_seller"} <= set(df.columns):
            st.warning("Colunas de reputação/buy box indisponíveis no schema.")
        else:
            rep = df[
                df["reputacao_seller"].notna()
                & (df["reputacao_seller"].astype(str).str.strip() != "")
                & df["buy_box_seller"].notna()
                & (df["buy_box_seller"].astype(str).str.strip() != "")
            ].copy()
            if rep.empty:
                st.warning("Nenhum registro com reputação + buy box no período/filtros.")
            else:
                wins = (
                    rep.groupby("reputacao_seller", as_index=False)
                    .agg(**{
                        "Buy box wins": ("buy_box_seller", "count"),
                        "Sellers distintos": ("buy_box_seller", "nunique"),
                    })
                    .rename(columns={"reputacao_seller": "Reputação"})
                    .sort_values("Buy box wins", ascending=False)
                )
                wins["Share (%)"] = (
                    wins["Buy box wins"] / wins["Buy box wins"].sum() * 100
                ).round(1)
                col_chart, col_table = st.columns([2, 1])
                with col_chart:
                    fig = px.bar(
                        wins.head(12), x="Buy box wins", y="Reputação",
                        orientation="h", color="Reputação",
                        color_discrete_sequence=_CHART_COLORS, text="Share (%)",
                        title="Buy box vencida por nível de reputação do seller",
                    )
                    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
                    fig.update_layout(showlegend=False,
                                      yaxis={"categoryorder": "total ascending"})
                    _apply_chart_style(fig, height=440, hovermode="closest")
                    st.plotly_chart(fig, use_container_width=True)
                with col_table:
                    st.dataframe(wins, use_container_width=True,
                                 hide_index=True, height=440)
                _csv_download_btn(
                    wins, f"rac_buybox_reputacao_{start_date}_{end_date}.csv",
                    "⬇️ Exportar reputação", key="rep_rep_csv",
                )

    # ── Tab 4: fulfillment × buy box e preço ────────────────────────────────
    with tab_ful:
        if "fulfillment" not in df.columns:
            st.info("Coluna `fulfillment` indisponível no schema.")
        else:
            ful = df[df["fulfillment"].notna()].copy()
            if ful.empty:
                st.info("Nenhum registro com `fulfillment` no período/filtros.")
            else:
                ful["fulfillment"] = ful["fulfillment"].astype(bool)
                if "buy_box_seller" in ful.columns:
                    bb = ful[
                        ful["buy_box_seller"].notna()
                        & (ful["buy_box_seller"].astype(str).str.strip() != "")
                    ]
                else:
                    bb = pd.DataFrame()
                if bb.empty:
                    st.info("Nenhum registro com buy box + fulfillment no período.")
                else:
                    pct = (
                        bb.groupby("plataforma", as_index=False)["fulfillment"]
                        .mean()
                        .rename(columns={"fulfillment": "frac"})
                    )
                    pct["% buy box c/ fulfillment"] = (pct["frac"] * 100).round(1)
                    fig = px.bar(
                        pct.sort_values("% buy box c/ fulfillment", ascending=False),
                        x="plataforma", y="% buy box c/ fulfillment",
                        color="plataforma", color_discrete_sequence=_CHART_COLORS,
                        title="% de buy box vencida com fulfillment, por plataforma",
                        labels={"plataforma": "Plataforma"},
                    )
                    fig.update_layout(showlegend=False)
                    _apply_chart_style(fig, height=400, hovermode="closest")
                    st.plotly_chart(fig, use_container_width=True)

                if "preco" in ful.columns:
                    # ≥ R$100: descarta parcela/erro de scraping (regra do projeto)
                    pr = ful[ful["preco"].notna() & (ful["preco"] >= 100)]
                    if not pr.empty and pr["fulfillment"].nunique() > 1:
                        cmp_df = (
                            pr.groupby(["plataforma", "fulfillment"], as_index=False)["preco"]
                            .mean()
                        )
                        cmp_df["Oferta"] = cmp_df["fulfillment"].map(
                            {True: "Com fulfillment", False: "Sem fulfillment"}
                        )
                        cmp_df["preco"] = cmp_df["preco"].round(2)
                        fig2 = px.bar(
                            cmp_df, x="plataforma", y="preco", color="Oferta",
                            barmode="group", color_discrete_sequence=_CHART_COLORS,
                            title="Preço médio com vs sem fulfillment (preços ≥ R$100)",
                            labels={"plataforma": "Plataforma", "preco": "Preço médio (R$)"},
                        )
                        _apply_chart_style(fig2, height=400)
                        st.plotly_chart(fig2, use_container_width=True)


# ---------------------------------------------------------------------------
# Page — Share of Voice Patrocinado
# ---------------------------------------------------------------------------

def page_sov_patrocinado() -> None:
    """Página 📣 Share of Voice Patrocinado.

    Usa `posicao_patrocinada`, `patrocinado` e `posicao_organica` para mostrar
    quem compra mídia, em quais keywords e com que dominância de SERP.
    """
    st.title("📣 Share of Voice Patrocinado")
    st.caption(
        "Quem compra mídia: densidade de anúncios patrocinados no top-10 por "
        "marca/plataforma, keywords mais disputadas em leilão, dupla presença "
        "orgânico + patrocinado e evolução diária."
    )

    with st.sidebar:
        st.subheader("Filtros")
        date_range = st.date_input(
            "Período",
            value=(date.today() - timedelta(days=14), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="sov_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today() - timedelta(days=14)
        end_date   = date_range[1] if len(date_range) > 1 else date.today()

        opts = get_filter_options()
        sel_platforms = st.multiselect("Plataformas", opts["platforms"], key="sov_platforms")
        sel_brands    = st.multiselect("Marcas", opts["brands"], key="sov_brands")
        sel_keywords  = st.multiselect("Keywords", opts["keywords"], key="sov_keywords")
        load_btn = st.button("🔄 Carregar SoV", type="primary", use_container_width=True)

    if not load_btn:
        st.info("Defina os filtros na barra lateral e clique em **Carregar SoV**.")
        st.caption(
            "💡 Plataformas com ads: ML (Product Ads — extração corrigida em "
            "Jun/2026), Amazon (AMS) e Magalu (HEROs). A cobertura real por "
            "plataforma está na página 🩺 Data Health."
        )
        return

    with st.spinner("Carregando dados…"):
        df = query_coletas(
            start_date, end_date,
            platforms=sel_platforms or None,
            brands=sel_brands or None,
            keywords=sel_keywords or None,
            limit=50000,
        )

    if df.empty:
        st.warning("Nenhum dado para os filtros selecionados.")
        return
    if "patrocinado" not in df.columns and "posicao_patrocinada" not in df.columns:
        st.warning("Colunas de patrocinado indisponíveis no schema.")
        return

    spon = pd.Series(False, index=df.index)
    if "patrocinado" in df.columns:
        spon |= df["patrocinado"].fillna(False).astype(bool)
    if "posicao_patrocinada" in df.columns:
        spon |= df["posicao_patrocinada"].notna()
    df = df.assign(_spon=spon)

    if not bool(spon.any()):
        st.warning(
            "Nenhum anúncio patrocinado no período/filtros. Confira a cobertura "
            "de `patrocinado` por plataforma na página 🩺 Data Health — coleta "
            "quebrada aparece lá como 0% (caso do ML até Jun/2026)."
        )
        return

    # ── KPIs ──────────────────────────────────────────────────────────────────
    n_spon = int(spon.sum())
    pct_spon = n_spon / len(df) * 100
    plats_com_ads = (
        df.loc[df["_spon"], "plataforma"].nunique() if "plataforma" in df.columns else 0
    )
    kws_com_ads = (
        df.loc[df["_spon"], "keyword"].nunique() if "keyword" in df.columns else 0
    )
    lider = None
    if "marca" in df.columns:
        spon_brands = df.loc[df["_spon"], "marca"].value_counts()
        if not spon_brands.empty:
            lider = f"{spon_brands.index[0]} ({spon_brands.iloc[0]:,})"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Anúncios patrocinados", f"{n_spon:,}")
    c2.metric("% patrocinado geral", f"{pct_spon:.1f}%")
    c3.metric("Plataformas c/ ads", str(plats_com_ads))
    c4.metric("Keywords c/ ads", f"{kws_com_ads:,}")
    c5.metric("Marca líder em ads", lider or "—",
              help="Marca com mais anúncios patrocinados no período")
    st.divider()

    tab_midia, tab_kw, tab_dupla, tab_evo = st.tabs(
        ["📊 Quem compra mídia", "🔑 Keywords disputadas",
         "👯 Dupla presença", "📈 Evolução diária"]
    )

    # ── Tab 1: % patrocinado no top-10 por marca e plataforma ───────────────
    with tab_midia:
        if "posicao_geral" not in df.columns:
            st.info("Coluna posicao_geral indisponível.")
        else:
            t10 = df[df["posicao_geral"].notna() & (df["posicao_geral"] <= 10)]
            if t10.empty:
                st.info("Nenhum registro no top-10 para o período/filtros.")
            else:
                col_m, col_p = st.columns(2)
                if "marca" in t10.columns:
                    por_marca = (
                        t10.groupby("marca")
                        .agg(n=("_spon", "size"), spon=("_spon", "sum"))
                        .reset_index()
                    )
                    por_marca = por_marca[por_marca["n"] >= 5]
                    por_marca["% patrocinado no top-10"] = (
                        por_marca["spon"] / por_marca["n"] * 100
                    ).round(1)
                    total_spon_t10 = por_marca["spon"].sum()
                    por_marca["Share dos ads (%)"] = (
                        (por_marca["spon"] / total_spon_t10 * 100).round(1)
                        if total_spon_t10 else 0.0
                    )
                    por_marca = por_marca.sort_values(
                        "% patrocinado no top-10", ascending=False
                    )
                    with col_m:
                        top_m = por_marca.head(12)
                        fig = px.bar(
                            top_m, x="% patrocinado no top-10", y="marca",
                            orientation="h", color="marca",
                            color_discrete_map=_brand_color_map(top_m["marca"]),
                            title="% de slots top-10 que são patrocinados, por marca",
                            labels={"marca": "Marca"},
                        )
                        fig.update_layout(showlegend=False,
                                          yaxis={"categoryorder": "total ascending"})
                        _apply_chart_style(fig, height=440, hovermode="closest")
                        st.plotly_chart(fig, use_container_width=True)
                if "plataforma" in t10.columns:
                    por_plat = (
                        t10.groupby("plataforma")
                        .agg(n=("_spon", "size"), spon=("_spon", "sum"))
                        .reset_index()
                    )
                    por_plat["% patrocinado no top-10"] = (
                        por_plat["spon"] / por_plat["n"] * 100
                    ).round(1)
                    with col_p:
                        fig = px.bar(
                            por_plat.sort_values("% patrocinado no top-10",
                                                 ascending=False),
                            x="plataforma", y="% patrocinado no top-10",
                            color="plataforma",
                            color_discrete_sequence=_CHART_COLORS,
                            title="% de slots top-10 patrocinados, por plataforma",
                            labels={"plataforma": "Plataforma"},
                        )
                        fig.update_layout(showlegend=False)
                        _apply_chart_style(fig, height=440, hovermode="closest")
                        st.plotly_chart(fig, use_container_width=True)
                if "marca" in t10.columns and not por_marca.empty:
                    st.dataframe(
                        _style_midea_df(por_marca.rename(columns={
                            "marca": "Marca", "n": "Slots top-10",
                            "spon": "Patrocinados",
                        }), brand_col="Marca"),
                        use_container_width=True, hide_index=True,
                    )
                    _csv_download_btn(
                        por_marca,
                        f"rac_sov_top10_{start_date}_{end_date}.csv",
                        "⬇️ Exportar top-10", key="sov_t10_csv",
                    )

    # ── Tab 2: keywords com maior densidade de patrocinados ─────────────────
    with tab_kw:
        if "keyword" not in df.columns:
            st.info("Coluna keyword indisponível.")
        else:
            kw = (
                df.groupby("keyword")
                .agg(n=("_spon", "size"), spon=("_spon", "sum"))
                .reset_index()
            )
            kw = kw[kw["n"] >= 10]
            kw["Densidade patrocinada (%)"] = (kw["spon"] / kw["n"] * 100).round(1)
            kw = kw.sort_values("Densidade patrocinada (%)", ascending=False)
            kw_com_ads = kw[kw["spon"] > 0]
            if kw_com_ads.empty:
                st.info("Nenhuma keyword com anúncio patrocinado no período.")
            else:
                top_kw = kw_com_ads.head(15)
                fig = px.bar(
                    top_kw, x="Densidade patrocinada (%)", y="keyword",
                    orientation="h", color="Densidade patrocinada (%)",
                    color_continuous_scale="OrRd",
                    title="Keywords com leilão mais disputado (% de anúncios pagos)",
                    labels={"keyword": "Keyword"},
                )
                fig.update_layout(yaxis={"categoryorder": "total ascending"},
                                  coloraxis_showscale=False)
                _apply_chart_style(fig, height=480, hovermode="closest")
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(
                    kw_com_ads.rename(columns={
                        "keyword": "Keyword", "n": "Registros",
                        "spon": "Patrocinados",
                    }),
                    use_container_width=True, hide_index=True,
                )
                _csv_download_btn(
                    kw_com_ads,
                    f"rac_sov_keywords_{start_date}_{end_date}.csv",
                    "⬇️ Exportar keywords", key="sov_kw_csv",
                )

    # ── Tab 3: dupla presença orgânico + patrocinado ────────────────────────
    with tab_dupla:
        st.markdown(
            "**Dominância de SERP:** a mesma marca ocupando slot orgânico **e** "
            "patrocinado na mesma keyword/coleta."
        )
        run_cols = [c for c in ["data", "turno", "plataforma", "keyword", "marca"]
                    if c in df.columns]
        if not {"keyword", "marca"} <= set(run_cols) or "posicao_organica" not in df.columns:
            st.info("Colunas necessárias (keyword/marca/posicao_organica) indisponíveis.")
        else:
            org_flag = df["posicao_organica"].notna() & ~df["_spon"]
            runs = (
                df.assign(_org=org_flag)
                .groupby(run_cols)
                .agg(org=("_org", "any"), spon=("_spon", "any"))
                .reset_index()
            )
            runs["dupla"] = runs["org"] & runs["spon"]
            por_marca = (
                runs.groupby("marca")
                .agg(**{"Buscas da marca": ("dupla", "size"),
                        "Com dupla presença": ("dupla", "sum")})
                .reset_index()
            )
            por_marca["% de dupla presença"] = (
                por_marca["Com dupla presença"] / por_marca["Buscas da marca"] * 100
            ).round(1)
            por_marca = por_marca[por_marca["Com dupla presença"] > 0].sort_values(
                "Com dupla presença", ascending=False
            )
            if por_marca.empty:
                st.info("Nenhuma marca com dupla presença no período/filtros.")
            else:
                top_d = por_marca.head(12)
                fig = px.bar(
                    top_d, x="Com dupla presença", y="marca", orientation="h",
                    color="marca", color_discrete_map=_brand_color_map(top_d["marca"]),
                    text="% de dupla presença",
                    title="Keyword-coletas com slot orgânico E patrocinado da mesma marca",
                    labels={"marca": "Marca"},
                )
                fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
                fig.update_layout(showlegend=False,
                                  yaxis={"categoryorder": "total ascending"})
                _apply_chart_style(fig, height=440, hovermode="closest")
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(
                    _style_midea_df(por_marca.rename(columns={"marca": "Marca"}),
                                    brand_col="Marca"),
                    use_container_width=True, hide_index=True,
                )
                _csv_download_btn(
                    por_marca,
                    f"rac_sov_dupla_{start_date}_{end_date}.csv",
                    "⬇️ Exportar dupla presença", key="sov_dupla_csv",
                )

    # ── Tab 4: evolução diária do % patrocinado por marca ───────────────────
    with tab_evo:
        if "marca" not in df.columns or "data" not in df.columns:
            st.info("Colunas marca/data indisponíveis.")
        else:
            top8 = (
                df.loc[df["_spon"], "marca"].value_counts().head(8).index
            )
            ev = (
                df[df["marca"].isin(top8)]
                .groupby(["data", "marca"])
                .agg(n=("_spon", "size"), spon=("_spon", "sum"))
                .reset_index()
            )
            if ev.empty:
                st.info("Sem dados para a evolução diária.")
            else:
                ev["% patrocinado"] = (ev["spon"] / ev["n"] * 100).round(1)
                ev["data"] = pd.to_datetime(ev["data"])
                fig = px.line(
                    ev, x="data", y="% patrocinado", color="marca", markers=True,
                    color_discrete_map=_brand_color_map(ev["marca"]),
                    title="% de anúncios patrocinados por dia (top-8 marcas em ads)",
                    labels={"data": "Data", "marca": "Marca"},
                )
                _emphasize_midea_traces(fig)
                _apply_chart_style(fig, height=450)
                st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Page — 🛡️ Price Compliance (monitor de preço-piso PriceTrack)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _query_pt_compliance(
    window_days: int,
    sources_tuple: tuple = ("coletas", "pricetrack"),
) -> pd.DataFrame:
    """Linhas MCJV do PriceTrack na janela — base do monitor de preço-piso.

    Uma linha por (dia, sku, marketplace, seller) com o preço mínimo
    observado (docs/PRICETRACK_INSIGHTS.md §2.1). Pagina além do cap de
    1000 do PostgREST e seleciona só as colunas necessárias.

    `sources_tuple` espelha o filtro global de Fonte de Dados — entra na chave
    de cache e zera o resultado quando "pricetrack" está desligada.
    """
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()
    if "pricetrack" not in sources_tuple:
        return pd.DataFrame()
    since = str(date.today() - timedelta(days=max(window_days, 1)))
    cols = "collection_date,sku,brand,marketplace,seller_canonical,min_price"
    rows: list = []
    offset = 0
    try:
        while True:
            resp = (
                client.table("pricetrack_daily").select(cols)
                .eq("is_midea_group", True)
                .gte("collection_date", since)
                .neq("sku", "")
                .order("id", desc=True)
                .range(offset, offset + _SUPABASE_PAGE - 1)
                .execute()
            )
            if not resp.data:
                break
            rows.extend(resp.data)
            if len(resp.data) < _SUPABASE_PAGE:
                break
            offset += _SUPABASE_PAGE
            if offset > 200_000:  # trava de segurança (30d ≈ 110k linhas MCJV)
                break
    except Exception as exc:
        st.error(f"Consulta pricetrack_daily falhou: {exc}")
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["collection_date"] = pd.to_datetime(df["collection_date"]).dt.date
    df["min_price"] = pd.to_numeric(df["min_price"], errors="coerce")
    df = df.dropna(subset=["min_price"])
    df = df[df["min_price"] > 0]
    return df


def page_price_compliance() -> None:
    """Página 🛡️ Price Compliance — quem rompe o preço-piso MCJV, e onde.

    Implementa o insight §2.1 do docs/PRICETRACK_INSIGHTS.md com o
    `min_price` diário por (sku, seller) do PriceTrack. Sem `map_price`
    no catálogo (futuro), a referência de piso é a mediana dos preços
    mínimos dos sellers do mesmo SKU no dia: rompimento = ofertar mais de
    X% abaixo dela (limiar configurável).
    """
    st.title("🛡️ Price Compliance")
    st.caption(
        "Monitor de preço-piso dos SKUs MCJV (`pricetrack_daily.is_midea_group`). "
        "**Rompimento** = preço mínimo do seller mais de X% abaixo da mediana "
        "dos sellers do mesmo SKU no dia. Quando o catálogo ganhar a coluna "
        "`map_price`, ela vira a referência oficial."
    )

    with st.sidebar:
        st.subheader("Filtros")
        window = st.select_slider(
            "Janela (dias)", options=[7, 14, 30], value=7, key="pc_window",
        )
        limiar = st.slider(
            "Limiar de rompimento (% abaixo da mediana)",
            min_value=1, max_value=30, value=10, key="pc_limiar",
        )
        min_sellers = st.slider(
            "Mín. de sellers no SKU/dia", 2, 10, 3, key="pc_min_sellers",
            help="A mediana só é referência confiável com concorrência mínima "
                 "no SKU naquele dia.",
        )

    with st.spinner("Carregando PriceTrack…"):
        df = _query_pt_compliance(window, sources_tuple=_gf_sources_key())

    if df.empty:
        st.warning("Sem dados MCJV do PriceTrack na janela (ou Supabase desconectado).")
        return

    # Referências por SKU × dia — espelha o SQL do §2.1 (piso_dia via window
    # function), acrescentando mediana e nº de sellers para o limiar.
    grp = df.groupby(["sku", "collection_date"])
    df["piso_dia"] = grp["min_price"].transform("min")
    df["mediana_dia"] = grp["min_price"].transform("median")
    df["n_sellers"] = grp["seller_canonical"].transform("nunique")
    df["pct_abaixo"] = (
        (df["mediana_dia"] - df["min_price"]) / df["mediana_dia"] * 100
    ).round(1)
    df["plataforma"] = df["marketplace"].map(_normalize_pt_platform)

    elegiveis = df[df["n_sellers"] >= min_sellers]
    breaches = elegiveis[elegiveis["pct_abaixo"] >= limiar].copy()
    # Para rankings, 1 rompimento = (seller, sku, dia) — o mesmo seller no
    # mesmo SKU em N marketplaces no dia conta uma vez ("quem"); o recorte
    # por marketplace ("onde") usa as linhas granulares.
    breach_events = breaches.drop_duplicates(
        ["seller_canonical", "sku", "collection_date"]
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rompimentos (seller×SKU×dia)", len(breach_events))
    c2.metric("Sellers rompendo", breaches["seller_canonical"].nunique())
    c3.metric("SKUs afetados", breaches["sku"].nunique())
    c4.metric(
        "Maior desvio",
        f"-{breaches['pct_abaixo'].max():.0f}%" if not breaches.empty else "—",
    )

    if breaches.empty:
        st.success(
            f"Nenhum seller rompeu o piso em mais de {limiar}% na janela de "
            f"{window} dias (SKUs com ≥ {min_sellers} sellers/dia)."
        )
        return

    st.divider()

    # ── QUEM rompe — ranking de sellers ──────────────────────────────────────
    rank = (
        breach_events.groupby("seller_canonical")
        .agg(
            rompimentos=("sku", "size"),
            skus=("sku", "nunique"),
            desvio_max=("pct_abaixo", "max"),
        )
        .reset_index()
        .sort_values("rompimentos", ascending=False)
    )
    col_quem, col_onde = st.columns(2)
    with col_quem:
        top = rank.head(15)
        fig = px.bar(
            top, x="rompimentos", y="seller_canonical", orientation="h",
            title=f"QUEM rompe — top {len(top)} sellers (≥{limiar}% abaixo da mediana)",
            labels={"rompimentos": "Rompimentos (SKU×dia)", "seller_canonical": "Seller"},
            color_discrete_sequence=_CHART_COLORS,
        )
        fig.update_layout(yaxis=dict(autorange="reversed"))
        _apply_chart_style(fig, height=max(380, 24 * len(top)), hovermode="closest")
        st.plotly_chart(fig, use_container_width=True)

    # ── ONDE — marketplaces dos rompimentos ──────────────────────────────────
    with col_onde:
        onde = (
            breaches.groupby("plataforma", dropna=False)
            .size().reset_index(name="linhas")
            .sort_values("linhas", ascending=False)
        )
        fig2 = px.bar(
            onde, x="plataforma", y="linhas",
            title="ONDE — linhas de rompimento por marketplace",
            labels={"plataforma": "Marketplace", "linhas": "Linhas"},
            color_discrete_sequence=_CHART_COLORS,
        )
        _apply_chart_style(fig2, height=max(380, 24 * len(rank.head(15))),
                           hovermode="closest")
        st.plotly_chart(fig2, use_container_width=True)

    # ── Tendência diária ──────────────────────────────────────────────────────
    trend = (
        breach_events.groupby("collection_date")
        .size().reset_index(name="rompimentos")
    )
    trend["collection_date"] = pd.to_datetime(trend["collection_date"])
    fig3 = px.line(
        trend, x="collection_date", y="rompimentos", markers=True,
        title="Rompimentos por dia (seller×SKU)",
        labels={"collection_date": "Data", "rompimentos": "Rompimentos"},
        color_discrete_sequence=_CHART_COLORS,
    )
    _apply_chart_style(fig3, height=320)
    st.plotly_chart(fig3, use_container_width=True)

    # ── Detalhe (espelho do SQL §2.1, com referências) ────────────────────────
    cat = get_catalogo()
    sku_nome: dict = {}
    if not cat.empty and "produto" in cat.columns:
        sku_nome = dict(zip(cat["sku"].astype(str), cat["produto"].astype(str)))

    detail = breaches.copy()
    detail["produto"] = detail["sku"].astype(str).map(sku_nome).fillna(detail["sku"])
    detail = detail.sort_values(
        ["collection_date", "pct_abaixo"], ascending=[False, False]
    )[[
        "collection_date", "sku", "produto", "brand", "seller_canonical",
        "plataforma", "min_price", "piso_dia", "mediana_dia", "pct_abaixo",
        "n_sellers",
    ]].rename(columns={
        "collection_date": "Data",
        "sku": "SKU",
        "produto": "Produto",
        "brand": "Marca",
        "seller_canonical": "Seller",
        "plataforma": "Marketplace",
        "min_price": "Preço Min (R$)",
        "piso_dia": "Piso do dia (R$)",
        "mediana_dia": "Mediana do dia (R$)",
        "pct_abaixo": "% abaixo da mediana",
        "n_sellers": "Sellers no dia",
    })
    st.markdown(f"**Detalhe dos rompimentos** — {len(detail)} linha(s)")
    st.dataframe(detail, use_container_width=True, hide_index=True, height=420)
    _csv_download_btn(
        detail, f"rac_price_compliance_{date.today()}.csv",
        "⬇️ Exportar rompimentos", key="pc_csv",
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
        # `df` (coletas) é a base para contagens de volume/registros/presença.
        # `dfp` (precedência PriceTrack) é a fonte de tudo que for **preço**.
        df  = _overview_data(
            str(start_date), str(end_date),
            tuple(sorted(sel_platforms)), tuple(sorted(sel_brands)),
            familias_tuple=_gf_familias_key(),
            skus_resolvidos_tuple=_gf_skus_resolvidos_key(),
            sources_tuple=_gf_sources_key(),
            estados_tuple=_gf_estados_key(),
        )
        dfp = _price_data(
            str(start_date), str(end_date),
            tuple(sorted(sel_platforms)), tuple(sorted(sel_brands)),
            familias_tuple=_gf_familias_key(),
            skus_resolvidos_tuple=_gf_skus_resolvidos_key(),
            sources_tuple=_gf_sources_key(),
        )

    if df.empty:
        st.info(
            "Nenhum dado encontrado. Configure os **Filtros Globais** na barra lateral "
            "e aguarde o carregamento."
        )
        return

    # Comparison window
    compare_on = _gf_compare()
    df_cmp  = pd.DataFrame()
    dfp_cmp = pd.DataFrame()
    if compare_on:
        cmp_start, cmp_end = _gf_cmp_dates()
        with st.spinner("Carregando período de comparação…"):
            df_cmp  = _overview_data(
                str(cmp_start), str(cmp_end),
                tuple(sorted(sel_platforms)), tuple(sorted(sel_brands)),
                familias_tuple=_gf_familias_key(),
                skus_resolvidos_tuple=_gf_skus_resolvidos_key(),
                sources_tuple=_gf_sources_key(),
                estados_tuple=_gf_estados_key(),
            )
            dfp_cmp = _price_data(
                str(cmp_start), str(cmp_end),
                tuple(sorted(sel_platforms)), tuple(sorted(sel_brands)),
                familias_tuple=_gf_familias_key(),
                skus_resolvidos_tuple=_gf_skus_resolvidos_key(),
                sources_tuple=_gf_sources_key(),
            )

    # ── KPI Strip ────────────────────────────────────────────────────────────
    last_date  = df["data"].max() if "data"      in df.columns else None
    n_records  = len(df)
    n_platforms = df["plataforma"].nunique() if "plataforma" in df.columns else 0
    n_brands   = df["marca"].nunique()        if "marca"      in df.columns else 0
    n_skus     = df["produto"].nunique()      if "produto"    in df.columns else 0

    # Preço sempre do PriceTrack (dfp); contagens do coletas (df).
    midea_mask = dfp["marca"].str.contains("Midea", case=False, na=False) if ("marca" in dfp.columns and not dfp.empty) else pd.Series(False, index=dfp.index)
    avg_midea  = dfp.loc[midea_mask, "preco"].mean() if ("preco" in dfp.columns and not dfp.empty) else None

    delta_records = None
    delta_price   = None
    if compare_on and not df_cmp.empty:
        delta_records = f"{n_records - len(df_cmp):+,}"
    if compare_on and not dfp_cmp.empty:
        midea_cmp_mask = dfp_cmp["marca"].str.contains("Midea", case=False, na=False) if "marca" in dfp_cmp.columns else pd.Series(False, index=dfp_cmp.index)
        avg_cmp = dfp_cmp.loc[midea_cmp_mask, "preco"].mean() if "preco" in dfp_cmp.columns else None
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
        df_price = dfp.dropna(subset=["preco", "data", "marca"]) if (not dfp.empty and all(c in dfp.columns for c in ["preco", "data", "marca"])) else pd.DataFrame()
        if not df_price.empty:
            try:
                top_brands = df_price["marca"].value_counts().head(6).index.tolist()
                trend = (
                    df_price[df_price["marca"].isin(top_brands)]
                    .groupby(["data", "marca"])["preco"]
                    .agg(_mode_price)
                    .reset_index()
                    .rename(columns={"preco": "Preço Modal (R$)", "marca": "Marca"})
                )
                trend["data"] = pd.to_datetime(trend["data"])
                if trend.empty or trend["Preço Modal (R$)"].isna().all():
                    raise ValueError("sem dados válidos após agrupamento")
                fig1 = px.line(
                    trend, x="data", y="Preço Modal (R$)", color="Marca",
                    color_discrete_map=_brand_color_map(trend["Marca"]),
                    markers=True,
                    title="Preço Modal por Marca",
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
        if not dfp.empty and req_cols.issubset(dfp.columns):
            sorted_dates = sorted(dfp["data"].unique(), reverse=True)
            if len(sorted_dates) >= 2:
                d_new, d_old = sorted_dates[0], sorted_dates[1]
                new_med = dfp[dfp["data"] == d_new].dropna(subset=["preco"]).groupby("produto")["preco"].agg(_mode_price)
                old_med = dfp[dfp["data"] == d_old].dropna(subset=["preco"]).groupby("produto")["preco"].agg(_mode_price)
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

# Confidence threshold (in raw records) below which a SKU's Δ% is flagged as
# low-sample. ~2 days of presence at the median offer count for popular SKUs.
_TM_LOW_SAMPLE = 14


def _streamlit_supports_linechart() -> bool:
    """LineChartColumn is available since Streamlit 1.23."""
    cc = getattr(st, "column_config", None)
    return cc is not None and hasattr(cc, "LineChartColumn")


@st.cache_data(ttl=1800, show_spinner=False)
def _pt_top_movers_data(
    start_str: str,
    end_str_exclusive: str,
    platforms_tuple: tuple,
    brands_tuple: tuple,
    familias_tuple: tuple,
    skus_resolvidos_tuple: tuple,
    limit: int = 300000,
    sources_tuple: tuple = ("coletas", "pricetrack"),
) -> pd.DataFrame:
    """Paginated read of `pricetrack_daily` over the half-open `[start, end)`.

    Top Movers reads pricetrack_daily exclusively to preserve the
    `(sku, marketplace, seller, collection_date)` granularity guarantee.
    Mixing in coletas would double-count rows the PT already covers.

    Returns raw columns (sku, brand, title, marketplace, seller,
    seller_canonical, min_price, avg_price, mode_price, collection_date).

    `sources_tuple` espelha o filtro global de Fonte de Dados — entra na chave
    de cache e zera o resultado quando "pricetrack" está desligada.
    """
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()
    if "pricetrack" not in sources_tuple:
        return pd.DataFrame()

    sku_set = _collect_pt_skus(
        familias_resolvidas=list(familias_tuple) or None,
        skus_resolvidos=list(skus_resolvidos_tuple) or None,
    )
    if (familias_tuple or skus_resolvidos_tuple) and not sku_set:
        return pd.DataFrame()

    try:
        end_inclusive = (
            date.fromisoformat(end_str_exclusive) - timedelta(days=1)
        ).isoformat()
    except ValueError:
        return pd.DataFrame()
    if end_inclusive < start_str:
        return pd.DataFrame()

    def _build_q():
        q = (
            client.table("pricetrack_daily")
            .select(
                "collection_date,sku,brand,title,marketplace,"
                "seller,seller_canonical,min_price,avg_price,mode_price,id"
            )
            .gte("collection_date", start_str)
            .lte("collection_date", end_inclusive)
            .order("collection_date", desc=True)
            .order("id", desc=True)
        )
        if brands_tuple:
            q = q.in_("brand", _expand_brands(list(brands_tuple)))
        if platforms_tuple:
            parts = [
                f"marketplace.ilike.{v}"
                for v in _pt_platform_match_values(list(platforms_tuple))
            ]
            if parts:
                q = q.or_(",".join(parts))
        if sku_set:
            q = q.in_("sku", sorted(sku_set))
        return q

    try:
        all_rows: list = []
        last_date: str | None = None
        last_id: int | None = None
        while len(all_rows) < limit:
            fetch = min(_SUPABASE_PAGE_PT, limit - len(all_rows))
            q = _build_q()
            if last_date is not None and last_id is not None:
                q = q.or_(
                    f"collection_date.lt.{last_date},"
                    f"and(collection_date.eq.{last_date},id.lt.{last_id})"
                )
            resp = q.limit(fetch).execute()
            if not resp.data:
                break
            all_rows.extend(resp.data)
            if len(resp.data) < fetch:
                break
            last_row = resp.data[-1]
            last_date = (
                str(last_row.get("collection_date"))
                if last_row.get("collection_date") else None
            )
            last_id = last_row.get("id")
            if last_date is None or last_id is None:
                break

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows)
        df["collection_date"] = pd.to_datetime(df["collection_date"]).dt.date
        for col in ("min_price", "avg_price", "mode_price"):
            df[col] = pd.to_numeric(df.get(col), errors="coerce")
        return df
    except Exception as exc:
        st.warning(f"Erro consultando pricetrack_daily: {exc}")
        return pd.DataFrame()


def page_top_movers() -> None:
    st.title("🚨 Top Movers")
    st.caption(
        "SKUs com maior variação de preço entre duas janelas temporais. "
        "Fonte: `pricetrack_daily` (granularidade garantida: 1 linha por "
        "SKU × marketplace × seller × dia). Janelas meio-abertas `[ini, fim)`."
    )

    start_date, end_date = _gf_dates()
    cmp_start, cmp_end   = _gf_cmp_dates()
    sel_platforms = _gf_platforms()
    sel_brands    = _gf_brands()

    with st.sidebar:
        st.subheader("Configuração")
        dr = st.date_input(
            "Janela atual", value=(start_date, end_date),
            max_value=date.today(), format="DD/MM/YYYY", key="tm_dates",
            help="Intervalo meio-aberto `[ini, fim)`: a data final NÃO é incluída.",
        )
        start_date = dr[0] if len(dr) > 0 else start_date
        end_date   = dr[1] if len(dr) > 1 else end_date

        mirror = st.checkbox(
            "Espelhar duração da janela atual",
            value=False, key="tm_mirror",
            help=(
                "Recalcula a janela de comparação para ter exatamente o mesmo "
                "nº de dias da atual, terminando no início da atual: "
                "`cmp = [atual_ini - duração, atual_ini)`."
            ),
        )
        if mirror:
            duration = max((end_date - start_date).days, 1)
            cmp_end   = start_date
            cmp_start = start_date - timedelta(days=duration)

        cr = st.date_input(
            "Janela de comparação", value=(cmp_start, cmp_end),
            max_value=date.today(), format="DD/MM/YYYY",
            key="tm_cmp_dates", disabled=mirror,
            help="Intervalo meio-aberto `[ini, fim)`: a data final NÃO é incluída.",
        )
        if not mirror:
            cmp_start = cr[0] if len(cr) > 0 else cmp_start
            cmp_end   = cr[1] if len(cr) > 1 else cmp_end

        opts = get_filter_options()
        sel_platforms = st.multiselect("Plataformas", opts["platforms"],
                                       default=sel_platforms, key="tm_platforms")
        sel_brands    = st.multiselect("Marcas", opts["brands"],
                                       default=sel_brands, key="tm_brands")
        sel_familias, sel_skus_resolvidos = _render_familia_sku_filters(sel_brands, "tm")

        with st.expander("Refinar — Movers", expanded=True):
            price_metric = st.radio(
                "Preço de referência",
                ["Moda", "Mínimo", "Médio"],
                index=0, key="tm_price_metric", horizontal=True,
                help=(
                    "Coluna usada como preço por registro antes da mediana por janela.\n\n"
                    "• **Moda** → `mode_price` (fallback `avg_price` → `min_price`)\n"
                    "• **Mínimo** → `min_price` (proxy Buy Box)\n"
                    "• **Médio** → `avg_price`"
                ),
            )
            min_delta_pct = st.slider("Mín. |Δ preço|%", 0, 50, 5, key="tm_min_delta")
            direction = st.radio(
                "Direção",
                ["Ambos ▲▼", "Apenas altas ▲", "Apenas quedas ▼"],
                key="tm_direction",
            )
            min_registros = st.number_input(
                "Mín. registros por janela", min_value=1, max_value=5000,
                value=30, step=5, key="tm_min_registros",
                help=(
                    "Remove SKUs cujo `MIN(registros_ant, registros_atual)` < "
                    "limiar. **Registros** é COUNT(*) bruto na janela "
                    "(1 linha por SKU×marketplace×seller×dia)."
                ),
            )
            both_windows = st.checkbox(
                "Apenas SKUs presentes nas duas janelas",
                value=True, key="tm_both_windows",
                help=(
                    "**Ligado** (padrão): inner join — só SKUs em ambas as "
                    "janelas, Δ% sempre definido.\n\n"
                    "**Desligado**: outer join — inclui SKUs novos ou "
                    "descontinuados (Δ% em branco). Use com Direção "
                    "*Ambos ▲▼* para vê-los."
                ),
            )

        load_btn = st.button("🔄 Calcular Movers", type="primary",
                             use_container_width=True)

    # Half-open semantic: user picks (ini, fim) → window = [ini, fim).
    # The picked fim date is EXCLUDED; the boundary belongs to a single window.
    cur_lo, cur_hi = start_date, end_date
    cmp_lo, cmp_hi = cmp_start, cmp_end
    cur_dur = (cur_hi - cur_lo).days
    cmp_dur = (cmp_hi - cmp_lo).days

    badge = (
        f"📐 Janela atual: **{cur_lo} → {cur_hi}** ({cur_dur}d) · "
        f"Comparação: **{cmp_lo} → {cmp_hi}** ({cmp_dur}d)"
    )
    if cur_dur != cmp_dur and cur_dur > 0 and cmp_dur > 0:
        badge += " · ⚠️ tamanhos diferentes — comparação não simétrica"
    st.caption(badge)

    if not load_btn:
        st.info(
            "Configure as **janelas temporais** na barra lateral e clique em "
            "**Calcular Movers**. Intervalos são meio-abertos `[ini, fim)` — "
            "a data final NÃO é incluída, então a fronteira pertence a uma "
            "única janela."
        )
        return

    if cur_dur <= 0 or cmp_dur <= 0:
        st.warning("Cada janela precisa cobrir ao menos 1 dia (fim > ini).")
        return

    _fam_t  = tuple(sorted(sel_familias))
    _sku_t  = tuple(sorted(sel_skus_resolvidos))
    _plat_t = tuple(sorted(sel_platforms))
    _brand_t = tuple(sorted(sel_brands))

    # Single PT query over the UNION of both windows + (no gap) → also covers
    # the sparkline series. The intermediate days when the two windows are
    # non-adjacent are cheap to ignore in pandas.
    union_lo = min(cur_lo, cmp_lo)
    union_hi = max(cur_hi, cmp_hi)
    with st.spinner("Carregando pricetrack_daily…"):
        df = _pt_top_movers_data(
            str(union_lo), str(union_hi),
            _plat_t, _brand_t, _fam_t, _sku_t,
            sources_tuple=_gf_sources_key(),
        )

    if df.empty:
        st.warning(
            "Nenhum registro em `pricetrack_daily` para os filtros/período. "
            "Confira marcas, plataformas e a janela."
        )
        return

    df = df.dropna(subset=["sku"]).copy()
    df["sku"] = df["sku"].astype(str)
    if df.empty:
        st.warning("Sem SKUs válidos no recorte.")
        return

    # Pick the price column per the user's metric. "Moda" cascades to
    # avg→min so partially-published rows still contribute (matches the
    # existing fallback chain in query_pricetrack_daily).
    if price_metric == "Mínimo":
        df["_preco"] = df["min_price"]
    elif price_metric == "Médio":
        df["_preco"] = df["avg_price"]
    else:
        df["_preco"] = (
            df["mode_price"].fillna(df["avg_price"]).fillna(df["min_price"])
        )

    # Offer identity = marketplace + canonical seller (fallback raw seller).
    seller_id = df["seller_canonical"].where(
        df["seller_canonical"].notna() & (df["seller_canonical"].astype(str) != ""),
        df["seller"],
    )
    df["_offer"] = df["marketplace"].astype(str) + "|" + seller_id.astype(str)

    cur_mask = (df["collection_date"] >= cur_lo) & (df["collection_date"] < cur_hi)
    cmp_mask = (df["collection_date"] >= cmp_lo) & (df["collection_date"] < cmp_hi)

    def _agg(window_df: pd.DataFrame, suffix: str) -> pd.DataFrame:
        # Median over the chosen price column is robust to outliers — spread_pct
        # confirms heavy dispersion across sellers on the same SKU.
        if window_df.empty:
            return pd.DataFrame(columns=[
                "sku", f"preco_{suffix}", f"registros_{suffix}",
                f"ofertas_{suffix}", f"dias_{suffix}",
            ])
        priced = window_df.dropna(subset=["_preco"])
        g = window_df.groupby("sku", sort=False)
        g_priced = priced.groupby("sku", sort=False) if not priced.empty else None
        out = pd.DataFrame({
            f"registros_{suffix}": g.size(),
            f"ofertas_{suffix}":   g["_offer"].nunique(),
            f"dias_{suffix}":      g["collection_date"].nunique(),
        })
        out[f"preco_{suffix}"] = (
            g_priced["_preco"].median() if g_priced is not None else pd.NA
        )
        return out.reset_index()

    cur_agg = _agg(df[cur_mask], "atual")
    cmp_agg = _agg(df[cmp_mask], "anterior")

    if cur_agg.empty and cmp_agg.empty:
        st.warning("Nenhuma janela retornou registros após o recorte.")
        return

    join_how = "inner" if both_windows else "outer"
    movers = cur_agg.merge(cmp_agg, on="sku", how=join_how)

    # Confidence filter, applied BEFORE fillna so NaN (absent window in the
    # outer-join case) can be distinguished from "0 records in a present
    # window" (which can't actually happen — SKUs with no rows are absent
    # from the per-window agg entirely). Without this distinction the
    # `both_windows=False` toggle is neutralized: one-window SKUs would
    # always fail `MIN(registros) >= threshold` because the missing side
    # collapses to 0.
    if both_windows:
        keep = movers[["registros_atual", "registros_anterior"]] \
                  .min(axis=1) >= int(min_registros)
    else:
        cur_ok = movers["registros_atual"].isna() | (
            movers["registros_atual"] >= int(min_registros)
        )
        cmp_ok = movers["registros_anterior"].isna() | (
            movers["registros_anterior"] >= int(min_registros)
        )
        keep = cur_ok & cmp_ok
    movers = movers[keep]

    for col in ("registros_atual", "registros_anterior",
                "ofertas_atual", "ofertas_anterior",
                "dias_atual", "dias_anterior"):
        if col in movers.columns:
            movers[col] = movers[col].fillna(0).astype("Int64")

    if movers.empty:
        scope = "ambas as janelas" if both_windows else "ao menos uma janela presente"
        st.warning(
            f"Nenhum SKU com ≥ {int(min_registros)} registros em {scope}. "
            "Reduza o limiar ou amplie as janelas."
        )
        return

    movers["delta_abs"] = movers["preco_atual"] - movers["preco_anterior"]
    movers["delta_pct"] = (
        movers["delta_abs"] / movers["preco_anterior"].replace(0, pd.NA) * 100
    )

    # When the toggle is on, every retained row has both prices defined; drop
    # rows where Δ% can't be computed defensively. When it's off, keep
    # one-window SKUs (NaN Δ%) so the toggle has a visible effect — they show
    # up with blank Δ% and the present window's price filled in.
    if both_windows:
        movers = movers.dropna(subset=["delta_pct"])

    if direction == "Apenas altas ▲":
        movers = movers[movers["delta_pct"] > 0]
    elif direction == "Apenas quedas ▼":
        movers = movers[movers["delta_pct"] < 0]
    # Preserve NaN Δ% (one-window SKUs) through the magnitude filter so they
    # remain visible when `both_windows=False` and direction is "Ambos".
    movers = movers[
        (movers["delta_pct"].abs() >= min_delta_pct) | movers["delta_pct"].isna()
    ]

    if movers.empty:
        st.warning(f"Nenhum SKU com variação ≥ {min_delta_pct}% após filtros.")
        return

    movers = movers.sort_values(
        "delta_pct", key=lambda s: s.abs(), ascending=False
    ).reset_index(drop=True)

    # Friendly product name from catalog (sku → produto); fallback to title.
    catalog = get_catalogo()
    if not catalog.empty and {"sku", "produto"}.issubset(catalog.columns):
        sku_to_produto = dict(zip(
            catalog["sku"].astype(str), catalog["produto"].astype(str)
        ))
    else:
        sku_to_produto = {}
    first_title = (df.dropna(subset=["title"])
                     .groupby("sku")["title"].first().to_dict())
    movers["produto"] = movers["sku"].map(
        lambda s: sku_to_produto.get(s) or first_title.get(s) or s
    )

    # Low-sample marker (independent of the user filter): visual cue when
    # MIN(registros) < confidence threshold (~2 days of presence).
    movers["confianca"] = (
        movers[["registros_atual", "registros_anterior"]]
        .min(axis=1).fillna(0).astype(int)
        .map(lambda n: "⚠️" if n < _TM_LOW_SAMPLE else "")
    )

    # ── KPI cards ────────────────────────────────────────────────────────────
    n_up   = int((movers["delta_pct"] > 0).sum())
    n_down = int((movers["delta_pct"] < 0).sum())
    biggest = movers.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("SKUs em movimento", str(len(movers)))
    c2.metric("▲ Altas",  str(n_up))
    c3.metric("▼ Quedas", str(n_down))
    c4.metric("Maior salto", f"{biggest['delta_pct']:+.1f}%",
              delta=str(biggest["produto"])[:30])

    st.divider()

    # ── Bar chart (top 20) ───────────────────────────────────────────────────
    top20 = movers.head(20).copy().sort_values("delta_pct")
    top20["SKU_label"] = top20["produto"].astype(str).str[:45]
    fig = px.bar(
        top20, x="delta_pct", y="SKU_label", orientation="h",
        color="delta_pct",
        color_continuous_scale=["#ef4444", "#fbbf24", "#059669"],
        color_continuous_midpoint=0,
        title=(
            f"Top 20 Movers — {cur_lo.strftime('%d/%m')}→{cur_hi.strftime('%d/%m')}"
            f" vs {cmp_lo.strftime('%d/%m')}→{cmp_hi.strftime('%d/%m')}"
        ),
        labels={"delta_pct": "Variação %", "SKU_label": "SKU"},
    )
    fig.update_coloraxes(showscale=False)
    _apply_chart_style(fig, height=max(350, len(top20) * 28 + 100))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ── Sparkline series (intra-window daily median) ────────────────────────
    spark_lo = min(cur_lo, cmp_lo)
    spark_hi = max(cur_hi, cmp_hi)
    spark_days = [spark_lo + timedelta(days=i)
                  for i in range((spark_hi - spark_lo).days)]
    spark_map: dict[str, list] = {}
    if spark_days:
        spark_src_mask = (
            (df["collection_date"] >= spark_lo)
            & (df["collection_date"] < spark_hi)
            & df["sku"].isin(movers["sku"])
        )
        spark_df = (
            df[spark_src_mask].dropna(subset=["_preco"])
              .groupby(["sku", "collection_date"])["_preco"]
              .median().reset_index()
        )
        if not spark_df.empty:
            pivot = (spark_df.pivot(index="sku", columns="collection_date",
                                    values="_preco")
                              .reindex(columns=spark_days))
            # Forward-fill so missing days draw a flat segment instead of a
            # vertical gap that would collapse the chart to a single dot.
            pivot = pivot.ffill(axis=1).bfill(axis=1)
            for sku, row in pivot.iterrows():
                spark_map[str(sku)] = [
                    float(v) if pd.notna(v) else None for v in row.tolist()
                ]
    movers["tendencia"] = movers["sku"].map(spark_map)
    movers["tendencia"] = movers["tendencia"].apply(
        lambda v: v if isinstance(v, list) else []
    )

    # ── Detail table ─────────────────────────────────────────────────────────
    st.subheader("Tabela Detalhada")
    st.caption(
        "Por janela: **Registros** = COUNT(*) bruto · "
        "**Ofertas** = marketplace × seller únicos · "
        "**Dias** = nº de datas com presença. "
        f"⚠️ marca amostras pequenas (MIN(registros) < {_TM_LOW_SAMPLE})."
    )

    display_cols = [
        "confianca", "produto", "sku",
        "preco_anterior", "preco_atual", "delta_abs", "delta_pct",
        "tendencia",
        "ofertas_anterior", "ofertas_atual",
        "registros_anterior", "registros_atual",
        "dias_anterior", "dias_atual",
    ]
    display = movers.reindex(columns=display_cols).copy()
    display.columns = [
        "⚠️", "Produto", "SKU",
        "Preço Anterior (R$)", "Preço Atual (R$)", "Δ R$", "Δ %",
        "Tendência",
        "Ofertas (ant)", "Ofertas (atual)",
        "Registros (ant)", "Registros (atual)",
        "Dias (ant)", "Dias (atual)",
    ]

    column_config = {
        "Preço Anterior (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
        "Preço Atual (R$)":    st.column_config.NumberColumn(format="R$ %.2f"),
        "Δ R$":                st.column_config.NumberColumn(format="R$ %.2f"),
        "Δ %":                 st.column_config.NumberColumn(format="%.1f%%"),
        "⚠️": st.column_config.TextColumn(
            help=(
                f"⚠️ = MIN(registros_ant, registros_atual) < {_TM_LOW_SAMPLE} "
                "(amostra baixa — Δ% pouco confiável)."
            ),
            width="small",
        ),
        "Produto": st.column_config.TextColumn(width="large"),
        "SKU":     st.column_config.TextColumn(width="small"),
        "Ofertas (ant)":   st.column_config.NumberColumn(format="%d"),
        "Ofertas (atual)": st.column_config.NumberColumn(format="%d"),
        "Registros (ant)":   st.column_config.NumberColumn(format="%d"),
        "Registros (atual)": st.column_config.NumberColumn(format="%d"),
        "Dias (ant)":   st.column_config.NumberColumn(format="%d"),
        "Dias (atual)": st.column_config.NumberColumn(format="%d"),
    }
    if _streamlit_supports_linechart():
        column_config["Tendência"] = st.column_config.LineChartColumn(
            help=(
                f"Mediana diária do preço ({price_metric.lower()}) ao longo "
                "da união das duas janelas. Forward-fill nos dias sem dado."
            ),
        )
    else:
        # Graceful fallback when LineChartColumn isn't available — render an
        # arrow marker so the column still conveys direction without breaking
        # the table layout.
        def _arrow(vals):
            nums = [v for v in (vals or []) if v is not None]
            if len(nums) < 2:
                return "▬"
            if nums[-1] > nums[0]:
                return "▲"
            if nums[-1] < nums[0]:
                return "▼"
            return "▬"
        display["Tendência"] = display["Tendência"].apply(_arrow)
        column_config["Tendência"] = st.column_config.TextColumn(
            width="small",
            help="LineChartColumn indisponível — exibindo tendência geral.",
        )

    st.dataframe(
        display,
        use_container_width=True, height=460,
        column_config=column_config, hide_index=True,
    )

    # CSV export: collapse sparkline list into a compact "x;y;z" string so
    # Excel doesn't choke on Python list repr. Semicolon-delimited rows +
    # UTF-8 BOM (project convention) handled by _csv_download_btn.
    export = display.copy()
    if "Tendência" in export.columns:
        export["Tendência"] = export["Tendência"].apply(
            lambda v: " ".join(f"{x:.2f}" for x in v if x is not None)
            if isinstance(v, list) else (str(v) if v is not None else "")
        )
    _csv_download_btn(
        export,
        f"rac_top_movers_{cur_lo}_{cur_hi}_vs_{cmp_lo}_{cmp_hi}.csv",
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
        # Contagens / BuyBox vêm das coletas (posicao_geral só existe lá);
        # o cálculo de movers de **preço** usa a precedência PriceTrack.
        _fam_k, _sku_k, _src_k, _est_k = (
            _gf_familias_key(), _gf_skus_resolvidos_key(),
            _gf_sources_key(), _gf_estados_key(),
        )
        df_cur   = _overview_data(str(window_start), str(window_end), (), (),
                                  familias_tuple=_fam_k, skus_resolvidos_tuple=_sku_k,
                                  sources_tuple=_src_k, estados_tuple=_est_k)
        df_prev  = _overview_data(str(prev_start), str(prev_end), (), (),
                                  familias_tuple=_fam_k, skus_resolvidos_tuple=_sku_k,
                                  sources_tuple=_src_k, estados_tuple=_est_k)
        dfp_cur  = _price_data(str(window_start), str(window_end), (), (),
                               familias_tuple=_fam_k, skus_resolvidos_tuple=_sku_k,
                               sources_tuple=_src_k)
        dfp_prev = _price_data(str(prev_start), str(prev_end), (), (),
                               familias_tuple=_fam_k, skus_resolvidos_tuple=_sku_k,
                               sources_tuple=_src_k)

    if df_cur.empty:
        st.warning("No records found in the active window.")
        return

    # ── Top movers — modal price per SKU, current vs previous window ──────
    ups = downs = pd.DataFrame()
    if not dfp_prev.empty and {"preco", "produto"}.issubset(dfp_cur.columns):
        cur_agg = (dfp_cur.dropna(subset=["preco", "produto"])
                   .groupby("produto")["preco"]
                   .agg(preco_atual=_mode_price, obs_atual="count").reset_index())
        prev_agg = (dfp_prev.dropna(subset=["preco", "produto"])
                    .groupby("produto")["preco"]
                    .agg(preco_anterior=_mode_price, obs_anterior="count")
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

    # brand_map alimenta as tabelas de movers → usa a mesma fonte (dfp_cur)
    # para que as chaves `produto` casem com os movers do PriceTrack.
    brand_map: dict = {}
    if not dfp_cur.empty and "marca" in dfp_cur.columns:
        brand_map = (dfp_cur.dropna(subset=["produto"])
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
        "`send_anomalies.py` (Replit Scheduled Deployments). "
        "Prices come from PriceTrack (Python collections only fill brands/"
        "products PriceTrack does not cover)."
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
        _fam_k, _sku_k, _src_k = (
            _gf_familias_key(), _gf_skus_resolvidos_key(), _gf_sources_key(),
        )
        df_today = _price_data(str(target_day), str(target_day), (), (),
                               familias_tuple=_fam_k, skus_resolvidos_tuple=_sku_k,
                               sources_tuple=_src_k)
        df_prev  = _price_data(str(prev_day), str(prev_day), (), (),
                               familias_tuple=_fam_k, skus_resolvidos_tuple=_sku_k,
                               sources_tuple=_src_k)

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


@st.cache_data(ttl=1800, show_spinner=False)
def _query_products_history(
    products: tuple, start_str: str, end_str: str,
    sources_tuple: tuple = ("coletas", "pricetrack"),
) -> pd.DataFrame:
    """Histórico de preço por SKU (cacheado), com precedência PriceTrack.

    Fonte é `query_price_evolution_data`: o PriceTrack é a verdade de preço
    por (data, SKU) e as coletas Python só preenchem produtos/datas que o
    PriceTrack ainda não cobre. A ficha do produto e o comparador são
    centrados em preço, então toda a página segue a regra de preço.

    `sources_tuple` entra apenas como discriminador da chave de cache; o
    recorte de fonte acontece nas funções-folha chamadas por
    `query_price_evolution_data`.
    """
    if not products:
        return pd.DataFrame()
    _ = sources_tuple  # cache-key only (ver docstring)
    df, _ = query_price_evolution_data(
        date.fromisoformat(start_str),
        date.fromisoformat(end_str),
        products=list(products),
        limit=50000,
    )
    return df


# ---------------------------------------------------------------------------
# Fase 5 — Market Analytics (distribuição de preços + presença por marketplace)
# ---------------------------------------------------------------------------

def page_market_analytics() -> None:
    st.title("📊 Market Analytics")
    st.caption("Distribuição de preços (fonte: PriceTrack, fallback coletas) e "
               "presença por marketplace (volume de ofertas das coletas) ao longo do tempo.")

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
        sel_familias, sel_skus_resolvidos = _render_familia_sku_filters(sel_brands, "ma")
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
        # `df` (coletas) → presença/volume de ofertas por marketplace.
        df = query_coletas(
            start_date, end_date,
            platforms=sel_platforms or None,
            brands=sel_brands or None,
            btu_filter=sel_btu or None,
            familias_resolvidas=sel_familias or None,
            skus_resolvidos=sel_skus_resolvidos or None,
            limit=50000,
        )
        # `dfp` (precedência PriceTrack) → distribuição de **preços**.
        dfp, _ = query_price_evolution_data(
            start_date, end_date,
            platforms=sel_platforms or None,
            brands=sel_brands or None,
            btu_filter=sel_btu or None,
            familias_resolvidas=sel_familias or None,
            skus_resolvidos=sel_skus_resolvidos or None,
            limit=50000,
        )

    if modo.startswith("Snapshot"):
        # Em dfp, as linhas do PriceTrack não têm run_id e são preservadas;
        # apenas as linhas de coletas (fallback) são reduzidas ao último run.
        df  = _filter_latest_run(df)
        dfp = _filter_latest_run(dfp)

    if df.empty and dfp.empty:
        st.warning("Nenhum dado encontrado para os filtros selecionados.")
        return

    df  = _enrich_specs(df)
    dfp = _enrich_specs(dfp)
    if sel_ciclo != "Todos":
        if not df.empty:
            df = df[df["ciclo"] == sel_ciclo]
        if not dfp.empty:
            dfp = dfp[dfp["ciclo"] == sel_ciclo]
        if df.empty and dfp.empty:
            st.warning(f"Nenhum produto com ciclo '{sel_ciclo}' no período.")
            return

    tab_dist, tab_presenca = st.tabs(
        ["💰 Distribuição de Preços", "🏪 Presença por Marketplace"]
    )

    # ── 5.2 Distribuição de preços por faixa (preço do PriceTrack) ───────────
    with tab_dist:
        df_price = dfp.dropna(subset=["preco", "data"]) if not dfp.empty else pd.DataFrame(columns=["preco", "data"])
        df_price = df_price[df_price["preco"] > 0]
        if df_price.empty:
            st.warning("Sem dados de preço no período.")
        else:
            fine_edges  = list(range(1500, 2501, 50))
            fine_labels = [f"{a}-{b}" for a, b in zip(fine_edges[:-1], fine_edges[1:])]
            bins   = [0] + fine_edges + [3000, 3500, 4000, 5000, 1e12]
            labels = (
                ["< 1500"]
                + fine_labels
                + ["2500-3000", "3000-3500", "3500-4000", "4000-5000", "> 5000"]
            )
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
            _apply_chart_style(fig, height=820, hovermode="closest")
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
    df = _query_products_history((produto,), str(start_date), str(end_date), sources_tuple=_gf_sources_key())
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
    cnt_cols[0].metric("Registros de preço", f"{len(df):,}")
    cnt_cols[1].metric("Marketplaces", int(df["plataforma"].nunique()))
    cnt_cols[2].metric(
        "Sellers",
        int(df["seller"].nunique()) if "seller" in df.columns else 0,
    )
    cnt_cols[3].metric(
        "Preço Moda",
        _fmt_brl(_mode_price(df_price["preco"])) if not df_price.empty else "—",
    )

    if df_price.empty:
        st.info("Sem dados de preço para este SKU no período.")
        return

    st.divider()

    # --- Evolução de preço por marketplace ---
    agg = (df_price.groupby(["data", "plataforma"])["preco"]
           .agg(_mode_price).reset_index())
    agg["data"] = pd.to_datetime(agg["data"])
    fig = px.line(
        agg, x="data", y="preco", color="plataforma", markers=True,
        title="Evolução de preço por marketplace (moda diária)",
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
    df = _query_products_history(produtos, str(start_date), str(end_date), sources_tuple=_gf_sources_key())
    if df.empty:
        st.warning("Sem coletas para os produtos selecionados.")
        return

    df_price = df.dropna(subset=["preco"])
    df_price = df_price[df_price["preco"] > 0]
    if df_price.empty:
        st.warning("Sem dados de preço para os produtos selecionados.")
        return

    # --- Evolução sobreposta ---
    agg = (df_price.groupby(["data", "produto"])["preco"]
           .agg(_mode_price).reset_index())
    agg["data"] = pd.to_datetime(agg["data"])
    fig = px.line(
        agg, x="data", y="preco", color="produto", markers=True,
        title="Evolução de preço comparada (moda diária)",
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
        .agg(menor="min", moda=_mode_price, maior="max")
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
            "moda":           st.column_config.NumberColumn("Moda", format="R$ %.2f"),
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
# Daily Price Vision — menor preço por marketplace, por turno
# ---------------------------------------------------------------------------

_DAILY_VISION_PLATFORMS: list[str] = [
    "Magalu", "Casas Bahia", "Shopee",
    "Leroy Merlin", "Mercado Livre", "Amazon",
]

_TURNO_TO_PERIODO: dict[str, str] = {
    # Coletas Python
    "Abertura":   "Manhã",
    "Fechamento": "Tarde",
    # PriceTrack: "PriceTrack" é o sentinela legado do agregado diário;
    # Manhã/Tarde vêm do recorte por collection_hour (migration 003).
    "PriceTrack": "Diário",
    "Diário":     "Diário",
    "Manhã":      "Manhã",
    "Tarde":      "Tarde",
}

# Cor primária por marca para o chip de Marca (borda lateral colorida).
# Cores aproximam o branding sem precisar de PNG real — substitua por
# `assets/logos/marcas/{slug}.png` numa 2ª iteração se quiser logo de verdade.
_BRAND_COLORS: dict[str, str] = {
    "Agratto":         "#dc2626",
    "Electrolux":      "#1e40af",
    "Elgin":           "#d97706",
    "Gree":            "#15803d",
    "LG":              "#a21caf",
    "Midea":           "#4338ca",
    "Philco":          "#ea580c",
    "Samsung":         "#0f172a",
    "TCL":             "#0369a1",
    "Springer Midea":  "#1d4ed8",
    "Consul":          "#0e7490",
    "Komeco":          "#6d28d9",
    "Carrier":         "#0891b2",
    "Daikin":          "#0c4a6e",
    "Fujitsu":         "#7f1d1d",
    "Britânia":        "#be123c",
    "Britania":        "#be123c",
}

def _brand_color(brand: str | None) -> str:
    """Cor primária da marca (fallback cinza neutro)."""
    if not brand:
        return "#64748b"
    # Normaliza marca para tolerar variações de caixa/acento mantendo
    # o dicionário enxuto (uma entrada por canônico).
    norm = str(brand).strip()
    return _BRAND_COLORS.get(norm, "#64748b")


def _hex_tint(hex_color: str, ratio: float = 0.88) -> str:
    """Mistura cor hex com branco. `ratio=0` → cor pura, `1` → branco puro.

    Usado pra gerar a versão "tonalizada" do chip da marca: fundo claro
    derivado da cor primária mantém a identidade visual sem comprometer a
    leitura do texto. ratio=0.88 dá ~12% da cor com 88% branco —
    equivalente a um Tailwind `-100` (ex.: red-600 → red-100).
    """
    if not hex_color or not hex_color.startswith("#") or len(hex_color) != 7:
        return "#f1f5f9"  # cinza-200 neutro
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except ValueError:
        return "#f1f5f9"
    r2 = int(round(r + (255 - r) * ratio))
    g2 = int(round(g + (255 - g) * ratio))
    b2 = int(round(b + (255 - b) * ratio))
    return f"#{r2:02x}{g2:02x}{b2:02x}"


def _fmt_brl(v) -> str:
    """Formata número como moeda BR (R$ 1.738,17). NaN/None → travessão."""
    if v is None or pd.isna(v):
        return "—"
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@st.cache_data(ttl=3600, show_spinner=False)
def _query_sparkline_7d(
    spark_start: date,
    end_date: date,
    brands_key: tuple[str, ...],
    sku_set_key: tuple[str, ...],
    btu_keys: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Piso diário por marca p/ o sparkline 7d do Daily Vision.

    Chama a RPC `pricetrack_brand_daily_floor` (migration 004), que faz
    `MIN(min_price) GROUP BY (collection_date, brand)` no Postgres e
    devolve ~marcas×7 ≈ 100 linhas.

    Por que RPC e não `client.table(...).select(...)`: a versão anterior
    puxava linhas cruas com um único `.limit(20000)`, mas o PostgREST capa
    a resposta em 1000 linhas (max_rows). Uma só marca popular (Midea:
    2,6k–8,6k linhas/dia) já estourava esse teto, então o recorte de 1000
    linhas cobria só um punhado de (marca, dia) — o sparkline saía esparso
    ou vazio (lista [1648, NaN×6]). Agregar server-side elimina o problema.

    Retorna colunas (`brand`, `collection_date`, `min_price`) p/ manter o
    pipeline a jusante inalterado. Cache de 15 min. Falha silenciosa:
    erro/timeout → DataFrame vazio, sparkline some sem warning vermelho.
    """
    client = _get_supabase()
    if client is None:
        return pd.DataFrame()
    try:
        params: dict = {
            "p_start": str(spark_start),
            "p_end":   str(end_date),
            # RPC filtra no `brand` RAW; expandimos os aliases aqui
            # (mesma regra da query principal: "Midea" → MIDEA/SPRINGER…).
            "p_brands": _expand_brands(list(brands_key)) if brands_key else None,
            "p_btus":   list(btu_keys) if btu_keys else None,
            "p_skus":   list(sku_set_key) if sku_set_key else None,
        }
        resp = client.rpc("pricetrack_brand_daily_floor", params).execute()
        if not resp.data:
            return pd.DataFrame()
        df = pd.DataFrame(resp.data)
        # Normaliza o nome da coluna de preço pro que o builder espera.
        if "floor_price" in df.columns:
            df = df.rename(columns={"floor_price": "min_price"})
        return df
    except Exception:
        # Falha silenciosa: sparkline é feature secundária. Sem ele a
        # coluna "Tendência 7d" fica vazia mas o resto da página segue
        # funcionando normalmente.
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Daily Vision — render HTML (mockup "Daily Price Vision")
# ---------------------------------------------------------------------------
# O mockup pede recursos que `st.dataframe`/Styler não renderiza: logos em
# chip, headers de marketplace com badge colorida, sparkline SVG, badge-pílula
# de gap e cards de KPI com gradiente. Por isso KPIs + tabela são montados
# como HTML e injetados via `st.html`. Todas as classes recebem prefixo `dv-`
# para não colidir com o CSS global do Streamlit.

# Marketplace → (iniciais do logo, fundo do chip, cor do texto) — chip 18×18.
_MP_LOGO: dict[str, tuple[str, str, str]] = {
    "Magalu":        ("M",  "#0086ff", "#ffffff"),
    "Casas Bahia":   ("CB", "#1e3a8a", "#ffffff"),
    "Shopee":        ("S",  "#ee4d2d", "#ffffff"),
    "Leroy Merlin":  ("LM", "#78bf00", "#003a1f"),
    "Mercado Livre": ("ML", "#ffe600", "#2d3277"),
    "Amazon":        ("A",  "#131921", "#ff9900"),
}

# Marketplace → (fundo da badge do header, cor do texto da badge).
_MP_BADGE: dict[str, tuple[str, str]] = {
    "Magalu":        ("#fff7ed", "#c2410c"),
    "Casas Bahia":   ("#fef2f2", "#b91c1c"),
    "Shopee":        ("#fff1ed", "#ea580c"),
    "Leroy Merlin":  ("#f0fdf4", "#15803d"),
    "Mercado Livre": ("#fefce8", "#a16207"),
    "Amazon":        ("#fff7ed", "#9a3412"),
}

# Rótulo curto exibido no header (Leroy Merlin → "Leroy", como no mockup).
_MP_HEAD_LABEL: dict[str, str] = {
    "Leroy Merlin": "Leroy",
}

# Marca → (iniciais, fundo do chip, cor do texto) — paleta exata do mockup.
_BRAND_LOGO: dict[str, tuple[str, str, str]] = {
    "Agratto":    ("AG", "#fee2e2", "#991b1b"),
    "Electrolux": ("EL", "#dbeafe", "#1e40af"),
    "Elgin":      ("EG", "#fef3c7", "#92400e"),
    "Gree":       ("GR", "#d1fae5", "#065f46"),
    "LG":         ("LG", "#fce7f3", "#9d174d"),
    "Midea":      ("MD", "#e0e7ff", "#3730a3"),
    "Philco":     ("PH", "#fed7aa", "#9a3412"),
    "Samsung":    ("SS", "#1e3a8a", "#ffffff"),
    "TCL":        ("TC", "#0c4a6e", "#ffffff"),
}

# Folha de estilo do componente (injetada uma vez por render; prefixo dv-).
_DV_CSS = """<style>
.dv-root { font-family:'Inter',-apple-system,'Segoe UI',sans-serif; color:#0f172a; }
.dv-kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; }
.dv-kpi { background:linear-gradient(135deg,#fff 0%,#f8fafc 100%); border:1px solid #e2e8f0;
  border-left:4px solid #1a56db; border-radius:16px; padding:18px 20px;
  box-shadow:0 4px 12px rgba(0,0,0,.04); }
.dv-kpi.good { border-left-color:#10b981; }
.dv-kpi.bad  { border-left-color:#dc2626; }
.dv-kpi-label { font-size:11px; font-weight:700; color:#475569; text-transform:uppercase; letter-spacing:.08em; }
.dv-kpi-value { font-size:26px; font-weight:800; margin-top:6px; letter-spacing:-.02em; color:#0f172a; line-height:1.15; }
.dv-kpi-value.sm { font-size:21px; }
.dv-kpi-delta { font-size:12px; font-weight:600; margin-top:6px; color:#475569; }
.dv-kpi-delta.down { color:#16a34a; }
.dv-kpi-delta.up   { color:#dc2626; }
.dv-twrap { background:#fff; border:1px solid #e2e8f0; border-radius:16px; overflow-x:auto;
  box-shadow:0 4px 12px rgba(0,0,0,.04); margin-top:18px; }
.dv-table { width:100%; border-collapse:collapse; font-size:13px; }
.dv-table thead th { background:#0f172a; color:#f8fafc; font-weight:600; text-transform:uppercase;
  font-size:11px; letter-spacing:.06em; padding:12px 10px; border-bottom:2px solid #1e293b;
  text-align:left; vertical-align:middle; white-space:nowrap; }
.dv-table thead th.num { text-align:right; }
.dv-mphead { display:inline-flex; align-items:center; gap:6px; padding:4px 9px; border-radius:999px;
  font-weight:700; font-size:11px; letter-spacing:.02em; }
.dv-logo { width:18px; height:18px; border-radius:4px; display:inline-flex; align-items:center;
  justify-content:center; font-weight:800; font-size:9px; flex-shrink:0; }
.dv-table tbody td { padding:9px 10px; border-bottom:1px solid #f1f5f9; vertical-align:middle; color:#0f172a; }
.dv-table tbody tr:hover { background:#f8fafc; }
.dv-table tbody tr.dv-champ td:first-child { border-left:4px solid #fbbf24; }
.dv-bpill { display:inline-flex; align-items:center; gap:10px; font-weight:600; }
.dv-blogo { width:26px; height:26px; border-radius:6px; display:inline-flex; align-items:center;
  justify-content:center; font-weight:800; font-size:11px; flex-shrink:0; }
.dv-champ-brand { background:linear-gradient(90deg,#fef3c7 0%,#fde68a 100%); color:#78350f; font-weight:700; }
.dv-num { text-align:right; font-variant-numeric:tabular-nums; }
.dv-price { font-weight:600; }
.dv-delta { display:block; font-size:10px; font-weight:500; color:#475569; margin-top:2px; }
.dv-delta.down { color:#16a34a; }
.dv-delta.up { color:#dc2626; }
.dv-win { background:#d1fae5; border-left:3px solid #10b981; }
.dv-win .dv-price { color:#065f46; font-weight:700; }
.dv-match { background:#ecfdf5; }
.dv-match .dv-price { color:#047857; }
.dv-empty .dv-price { color:#cbd5e1; font-style:italic; font-weight:400; }
.dv-gap { display:inline-block; padding:3px 8px; border-radius:999px; background:#f1f5f9;
  color:#475569; font-size:11px; font-weight:600; }
.dv-gap.big { background:#fef3c7; color:#92400e; }
.dv-spark { display:block; }
.dv-table tfoot td { background:#f8fafc; font-weight:700; color:#475569; padding:11px 10px;
  border-top:2px solid #e2e8f0; font-size:12px; }
.dv-table tfoot td.num { text-align:right; }
.dv-legend { margin-top:12px; font-size:12px; color:#475569; display:flex; gap:16px; flex-wrap:wrap; }
.dv-legi { display:inline-flex; align-items:center; gap:6px; }
.dv-sw { width:14px; height:14px; border-radius:3px; display:inline-block; }
</style>"""


def _dv_brand_logo(brand: str) -> tuple[str, str, str]:
    """(iniciais, fundo, cor) do chip de marca. Fora do mockup → deriva.

    Marcas não mapeadas usam a cor primária de `_BRAND_COLORS` (com fundo
    tonalizado via `_hex_tint`) e as duas primeiras letras como sigla.
    """
    norm = str(brand or "").strip()
    if norm in _BRAND_LOGO:
        return _BRAND_LOGO[norm]
    color = _brand_color(norm)
    letters = "".join(ch for ch in norm if ch.isalnum())[:2].upper() or "?"
    return letters, _hex_tint(color, 0.85), color


def _dv_sparkline_svg(values) -> str:
    """Sparkline da série de pisos 7d como ``<img>`` (data URI) — look do mockup.

    Verde se o piso CAIU no período (boa notícia p/ consumidor), vermelho se
    subiu, cinza se estável. NaN viram lacuna (a linha liga só pontos
    válidos). Menos de 2 pontos válidos → string vazia (coluna em branco).

    Por que ``<img>`` e não ``<svg>`` inline: o Streamlit sanitiza/descarta
    SVG solto dentro de ``st.html`` (e rebaixa atributos camelCase como
    ``viewBox`` → ``viewbox``), então o traço sumia e a coluna ficava vazia
    apesar de haver série. ``<img src="data:image/svg+xml;base64,…">`` é o
    caminho que o Streamlit renderiza sem sanitizar — o sparkline volta a
    aparecer mantendo a tabela HTML do mockup intacta.

    Args:
        values: lista de floats (pisos diários), podendo conter NaN.

    Returns:
        Marcação ``<img>`` pronta p/ embutir na célula, ou ``""``.
    """
    if not isinstance(values, (list, tuple)):
        return ""
    pairs = [
        (i, float(v)) for i, v in enumerate(values)
        if v is not None and not pd.isna(v)
    ]
    if len(pairs) < 2:
        return ""
    n = len(values)
    vals = [p[1] for p in pairs]
    lo, hi = min(vals), max(vals)
    span = hi - lo
    w, h, pad = 80.0, 24.0, 4.0
    step = w / (n - 1) if n > 1 else w
    pts = " ".join(
        f"{i * step:.0f},"
        f"{(h / 2) if span == 0 else (pad + (hi - v) / span * (h - 2 * pad)):.0f}"
        for i, v in pairs
    )
    first, last = pairs[0][1], pairs[-1][1]
    stroke = "#64748b"
    if last < first - 0.01:
        stroke = "#10b981"
    elif last > first + 0.01:
        stroke = "#dc2626"

    # SVG STANDALONE: dentro de um data URI o markup é parseado como documento
    # isolado, então `xmlns` é obrigatório (inline ele é opcional). viewBox é
    # preservado aqui — o rebaixamento camelCase do sanitizer do st.html não
    # alcança o conteúdo do data URI.
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'width="80" height="24" viewBox="0 0 80 24">'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round" points="{pts}"/></svg>'
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")

    # Tooltip nativo com a variação do piso no período (queda/alta/estável).
    pct = (last - first) / first * 100 if first else 0.0
    if last < first - 0.01:
        tip = f"Piso 7d em queda ({pct:+.1f}%)"
    elif last > first + 0.01:
        tip = f"Piso 7d em alta ({pct:+.1f}%)"
    else:
        tip = "Piso 7d estável"
    return (
        f'<img class="dv-spark" width="80" height="24" alt="{tip}" '
        f'title="{tip}" src="data:image/svg+xml;base64,{b64}"/>'
    )


def _dv_mp_header_html(plat: str) -> str:
    """<th> do marketplace: badge colorida + logo em chip (look do mockup)."""
    badge_bg, badge_ink = _MP_BADGE.get(plat, ("#f1f5f9", "#475569"))
    logo_txt, logo_bg, logo_ink = _MP_LOGO.get(plat, ("?", "#64748b", "#ffffff"))
    label = _MP_HEAD_LABEL.get(plat, plat)
    return (
        f'<th class="num"><span class="dv-mphead" '
        f'style="background:{badge_bg};color:{badge_ink};">'
        f'<span class="dv-logo" style="background:{logo_bg};color:{logo_ink};">'
        f'{_esc(logo_txt)}</span>{_esc(label)}</span></th>'
    )


@dataclass
class _DVContext:
    """Tudo que o `_dv_build_html` precisa para montar a página Daily Vision.

    Agregado num dataclass (em vez de ~12 parâmetros soltos) para manter a
    assinatura enxuta e a função de render testável isoladamente.
    """
    pivot: pd.DataFrame
    base_cols: list[str]          # dimensões à esquerda (Data, Source, …)
    price_matrix: pd.DataFrame    # preços numéricos (linhas × 6 MPs)
    delta_matrix: pd.DataFrame    # delta vs ontem por (linha × MP)
    champion_idx: int | None      # índice da linha campeã (menor piso global)
    champion_brand: str | None
    champion_mp: object           # rótulo da coluna MP vencedora da campeã
    current_min: float | None     # piso geral do recorte
    delta_v: float | None         # variação do piso vs período anterior
    delta_str: str | None         # "R$ 73,64" (módulo, já formatado)
    pct_str: str | None           # "-4,6%"
    sel_grupo: str                # modo de agrupamento (rótulo do radio)


def _dv_build_html(ctx: _DVContext) -> str:
    """Monta o HTML completo (CSS + KPIs + tabela + legenda) do Daily Vision.

    Reproduz o mockup `daily_vision_mockup.html`: cards de KPI com gradiente
    e cor de status, tabela com logos em chip, headers de marketplace
    coloridos, sparkline SVG por marca, destaque do MP vencedor, badge de
    gap competitivo, linha campeã em âmbar e rodapé com média por MP.
    """
    pivot = ctx.pivot
    base_cols = ctx.base_cols
    price_matrix = ctx.price_matrix
    delta_matrix = ctx.delta_matrix
    champion_idx = ctx.champion_idx

    # ── KPIs como cards (look do mockup) ─────────────────────────────────
    n_brands = int(pivot["Marca"].nunique())
    n_mps = int(price_matrix.notna().any(axis=0).sum())
    kpi_cards: list[str] = []
    kpi_cards.append(
        '<div class="dv-kpi"><div class="dv-kpi-label">Linhas</div>'
        f'<div class="dv-kpi-value">{len(pivot):,}</div>'
        f'<div class="dv-kpi-delta">{n_brands} marcas · '
        f'{n_mps} marketplaces</div></div>'
    )
    if "SKU" in pivot.columns:
        sku_val = f"{pivot['SKU'].nunique():,}"
        sku_sub = "SKUs no recorte"
    else:
        sku_val = "—"
        sku_sub = f"modo {ctx.sel_grupo}"
    kpi_cards.append(
        '<div class="dv-kpi"><div class="dv-kpi-label">SKUs únicos</div>'
        f'<div class="dv-kpi-value">{sku_val}</div>'
        f'<div class="dv-kpi-delta">{_esc(sku_sub)}</div></div>'
    )
    piso_val = _fmt_brl(ctx.current_min) if ctx.current_min is not None else "—"
    if ctx.delta_v is not None and ctx.delta_str:
        down = ctx.delta_v < 0
        arrow = "▼" if down else "▲"
        status_cls = "good" if down else "bad"
        delta_cls = "down" if down else "up"
        pct_part = f" ({ctx.pct_str})" if ctx.pct_str else ""
        delta_line = (
            f'<div class="dv-kpi-delta {delta_cls}">{arrow} {ctx.delta_str} '
            f'vs ontem{pct_part}</div>'
        )
    else:
        status_cls = ""
        delta_line = '<div class="dv-kpi-delta">sem base de ontem</div>'
    kpi_cards.append(
        f'<div class="dv-kpi {status_cls}"><div class="dv-kpi-label">'
        f'Piso geral</div><div class="dv-kpi-value">{piso_val}</div>'
        f'{delta_line}</div>'
    )
    if ctx.champion_brand and ctx.champion_mp:
        kpi_cards.append(
            '<div class="dv-kpi good"><div class="dv-kpi-label">'
            'Marca campeã</div>'
            f'<div class="dv-kpi-value sm">🏆 {_esc(ctx.champion_brand)}</div>'
            f'<div class="dv-kpi-delta">{_esc(str(ctx.champion_mp))} · '
            'menor preço do recorte</div></div>'
        )
    else:
        kpi_cards.append(
            '<div class="dv-kpi"><div class="dv-kpi-label">Marcas</div>'
            f'<div class="dv-kpi-value">{n_brands:,}</div>'
            '<div class="dv-kpi-delta">no recorte</div></div>'
        )
    kpis_html = f'<div class="dv-kpis">{"".join(kpi_cards)}</div>'

    # ── Tabela ───────────────────────────────────────────────────────────
    gap_series = pd.to_numeric(pivot["Gap 1º→2º"], errors="coerce")

    head_cells = [f"<th>{_esc(c)}</th>" for c in base_cols]
    head_cells.append("<th>Tendência 7d</th>")
    head_cells += [_dv_mp_header_html(p) for p in _DAILY_VISION_PLATFORMS]
    head_cells.append('<th class="num">Gap 1º→2º</th>')
    thead = f"<thead><tr>{''.join(head_cells)}</tr></thead>"

    body_rows: list[str] = []
    for idx in pivot.index:
        is_champ = champion_idx is not None and idx == champion_idx
        cells: list[str] = []
        for c in base_cols:
            val = pivot.loc[idx, c]
            if c == "Data":
                try:
                    txt = pd.to_datetime(val).strftime("%d/%m/%Y")
                except (ValueError, TypeError):
                    txt = _esc(val)
                cells.append(f"<td>{txt}</td>")
            elif c == "Marca":
                if is_champ:
                    cells.append(
                        f'<td class="dv-champ-brand">🏆 {_esc(val)}</td>'
                    )
                else:
                    letters, bg, ink = _dv_brand_logo(str(val))
                    cells.append(
                        '<td><span class="dv-bpill">'
                        f'<span class="dv-blogo" '
                        f'style="background:{bg};color:{ink};">{_esc(letters)}'
                        f'</span>{_esc(val)}</span></td>'
                    )
            else:
                cells.append(f"<td>{_esc(val)}</td>")

        cells.append(
            f"<td>{_dv_sparkline_svg(pivot.loc[idx, 'Tendência 7d'])}</td>"
        )

        row_prices = price_matrix.loc[idx]
        valid = row_prices.dropna()
        rmin = float(valid.min()) if not valid.empty else None
        for plat in _DAILY_VISION_PLATFORMS:
            v = row_prices.get(plat)
            if v is None or pd.isna(v):
                cells.append(
                    '<td class="num dv-empty"><span class="dv-price">—'
                    '</span></td>'
                )
                continue
            v = float(v)
            cell_cls = ""
            if rmin is not None and v == rmin:
                cell_cls = " dv-win"
            elif rmin is not None and rmin > 0 and (v - rmin) / rmin <= 0.02:
                cell_cls = " dv-match"
            d = (delta_matrix.loc[idx, plat]
                 if plat in delta_matrix.columns else None)
            delta_html = ""
            if d is not None and not pd.isna(d) and abs(d) >= 1:
                d_down = d < 0
                d_arrow = "▼" if d_down else "▲"
                d_cls = "down" if d_down else "up"
                d_amt = f"R$ {int(round(abs(float(d)))):,}".replace(",", ".")
                delta_html = (
                    f'<span class="dv-delta {d_cls}">{d_arrow} {d_amt}</span>'
                )
            cells.append(
                f'<td class="num{cell_cls}"><span class="dv-price">'
                f'{_fmt_brl(v)}</span>{delta_html}</td>'
            )

        g = gap_series.loc[idx]
        if g is None or pd.isna(g):
            cells.append('<td class="num"><span class="dv-gap">—</span></td>')
        else:
            # Gap >= R$ 50 destaca o espaço competitivo amplo do vencedor.
            big_cls = " big" if float(g) >= 50 else ""
            cells.append(
                f'<td class="num"><span class="dv-gap{big_cls}">'
                f'{_fmt_brl(g)}</span></td>'
            )

        row_cls = ' class="dv-champ"' if is_champ else ""
        body_rows.append(f"<tr{row_cls}>{''.join(cells)}</tr>")
    tbody = f"<tbody>{''.join(body_rows)}</tbody>"

    # Rodapé: média de preço por marketplace (Gap fica em branco).
    foot_cells = [
        f'<td colspan="{len(base_cols) + 1}">Média por marketplace</td>'
    ]
    foot_cells += [
        f'<td class="num">{_fmt_brl(price_matrix[p].mean())}</td>'
        for p in _DAILY_VISION_PLATFORMS
    ]
    foot_cells.append("<td></td>")
    tfoot = f"<tfoot><tr>{''.join(foot_cells)}</tr></tfoot>"

    table_html = (
        f'<div class="dv-twrap"><table class="dv-table">'
        f'{thead}{tbody}{tfoot}</table></div>'
    )

    legend_html = (
        '<div class="dv-legend">'
        '<span class="dv-legi"><span class="dv-sw" '
        'style="background:#fde68a;border:1px solid #fbbf24;"></span> '
        'marca campeã do recorte</span>'
        '<span class="dv-legi"><span class="dv-sw" '
        'style="background:#d1fae5;border-left:3px solid #10b981;"></span> '
        'marketplace vencedor da linha</span>'
        '<span class="dv-legi"><span class="dv-sw" '
        'style="background:#ecfdf5;"></span> dentro de 2% do piso (match)</span>'
        '<span class="dv-legi">▼ ▲ delta vs ontem (mesmo MP/marca)</span>'
        '<span class="dv-legi">Gap = piso vs 2º colocado</span>'
        '</div>'
    )

    return (
        f'{_DV_CSS}<div class="dv-root">{kpis_html}{table_html}'
        f'{legend_html}</div>'
    )


def page_daily_vision() -> None:
    st.title("📅 Daily Price Vision")
    st.caption(
        "Menor preço por marketplace, consolidado por marca, capacidade e SKU. "
        "**PriceTrack** alimenta **Manhã** (08–12h) e **Tarde** (18–22h) — "
        "recorte por hora de coleta — além da linha **Diário** (dia inteiro). "
        "Nos modos **Marca** e **Marca + Capacidade**, o PriceTrack é "
        "autoridade: existindo qualquer linha PT no recorte (data, marca, "
        "capacidade, período), as coletas do mesmo recorte são suprimidas. "
        "No modo **SKU (detalhado)** as coletas continuam preenchendo "
        "(data, SKU, período, plataforma) onde o PriceTrack não cobre."
    )

    with st.sidebar:
        st.subheader("Filtros")
        date_range = st.date_input(
            "Período",
            value=(date.today(), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="dv_dates",
        )
        start_date = date_range[0] if len(date_range) > 0 else date.today()
        end_date   = date_range[1] if len(date_range) > 1 else start_date

        opts = get_filter_options()

        sel_brands = st.multiselect(
            "Marcas", opts["brands"], key="dv_brands",
        )
        sel_btu = st.multiselect(
            "Capacidade (BTU)", BTU_OPTIONS,
            format_func=lambda x: f"{int(x):,} BTUs".replace(",", "."),
            key="dv_btu",
        )
        sel_familias, sel_skus_resolvidos = _render_familia_sku_filters(
            sel_brands, "dv",
        )
        sku_opts = get_sku_options(
            tuple(sorted(sel_brands)), tuple(sorted(sel_btu)), (),
        )
        sel_skus = st.multiselect(
            f"SKU  ({len(sku_opts)} disponíveis)" if sku_opts else "SKU",
            sku_opts, key="dv_sku",
            placeholder="Todos os SKUs",
        )
        sel_periodos = st.multiselect(
            "Período (turno)",
            ["Manhã", "Tarde", "Diário"],
            default=["Manhã", "Tarde", "Diário"],
            key="dv_periodos",
            help="PriceTrack: Manhã=08–12h, Tarde=18–22h, Diário=dia inteiro. "
                 "Coletas Python (Abertura/Fechamento) entram como fallback.",
        )
        sel_grupo = st.radio(
            "Agrupar por",
            ["SKU (detalhado)", "Marca + Capacidade", "Marca"],
            # Default "Marca" → vista consolidada por marca (uma linha por
            # marca), que é exatamente o recorte do mockup Daily Price Vision.
            index=2,
            key="dv_grupo",
            help=(
                "**SKU (detalhado)**: uma linha por SKU.\n\n"
                "**Marca + Capacidade**: consolida o menor preço entre todos "
                "os SKUs daquela marca + BTU.\n\n"
                "**Marca**: consolida o menor preço entre todos os SKUs da "
                "marca, independente da capacidade."
            ),
        )

    # Buscamos coletas e pricetrack SEPARADAMENTE. O PriceTrack agora traz
    # Manhã/Tarde (recorte por collection_hour) além do Diário, e é a FONTE
    # dos turnos; as coletas entram só como fallback onde o PT não cobre
    # aquele (data, sku, período) — precedência aplicada abaixo, antes do
    # concat (espelha a regra por (data, sku) de query_price_evolution_data,
    # acrescida da dimensão de período).
    #
    # Janela de query principal = D-1 do start_date (precisamos do dia
    # anterior pro delta KPI) até end_date. O sparkline 7d roda numa
    # query SEPARADA enxuta (`_query_sparkline_7d`) p/ evitar o
    # `statement_timeout` que estoura quando se estende a janela cheia
    # cobrindo 7 dias × ilike(title, %BTU%).
    prev_day = start_date - timedelta(days=1)
    spark_start = end_date - timedelta(days=6)   # 7 dias inclusivos
    with st.spinner("Carregando dados..."):
        df_co = query_coletas(
            prev_day,
            end_date,
            platforms=_DAILY_VISION_PLATFORMS,
            brands=sel_brands or None,
            products=sel_skus or None,
            btu_filter=sel_btu or None,
            familias_resolvidas=sel_familias or None,
            skus_resolvidos=sel_skus_resolvidos or None,
            limit=80000,
        )
        df_pt = query_pricetrack_daily(
            prev_day,
            end_date,
            platforms=_DAILY_VISION_PLATFORMS,
            brands=sel_brands or None,
            products=sel_skus or None,
            btu_filter=sel_btu or None,
            familias_resolvidas=sel_familias or None,
            skus_resolvidos=sel_skus_resolvidos or None,
            turnos=["Diário", "Manhã", "Tarde"],
            limit=80000,
        )

    # Transparência D-1: o PriceTrack é importado no dia seguinte (06h BRT) e,
    # provisoriamente, intra-dia (~13h/23h BRT). Enquanto não chega, os turnos
    # do dia exibidos vêm das Coletas (fallback parcial — menos marketplaces),
    # o que distorce a comparação vs. dias 100% PriceTrack (ex.: o "vs ontem"
    # do KPI Piso geral). Avisamos quais dias do recorte ainda não têm PT.
    # Só faz sentido quando a fonte PriceTrack está ativa no filtro global.
    if "pricetrack" in _gf_sources():
        pt_error = st.session_state.get("pt_query_error")
        pt_dates = set(df_pt["data"]) if not df_pt.empty else set()
        requested_dates = {
            start_date + timedelta(days=i)
            for i in range((end_date - start_date).days + 1)
        }
        missing_pt = sorted(d for d in requested_dates if d not in pt_dates)
        if pt_error:
            # A consulta ao PriceTrack FALHOU (ex.: timeout) — o df veio vazio
            # por erro, não por falta de cobertura. NÃO afirmar "ainda não
            # cobre": isso induz o analista a achar que falta dado quando, na
            # verdade, ele existe e só não foi lido. Mensagem específica e o
            # detalhe técnico já saíram no st.warning de query_pricetrack_daily.
            st.warning(
                "⚠️ A consulta ao **PriceTrack** não completou neste recorte "
                "(provável tempo de consulta esgotado). Os turnos exibidos "
                "podem estar vindo apenas das **Coletas**. Recarregue a página "
                "ou estreite o período/marca — o dado do PriceTrack "
                "normalmente existe, só não foi lido a tempo."
            )
        elif missing_pt:
            dias = ", ".join(d.strftime("%d/%m") for d in missing_pt)
            if "coletas" in _gf_sources():
                # Coletas ativa → ela preenche o(s) dia(s) sem PriceTrack.
                corpo = (
                    "Os turnos exibidos nesse(s) dia(s) vêm das **Coletas** "
                    "(fallback, geralmente menos marketplaces) — compare com "
                    "cautela contra dias 100% PriceTrack."
                )
            else:
                # Coletas desligada no filtro global → não há fallback, o(s)
                # dia(s) ficam sem dados (não afirmar que vêm das Coletas).
                corpo = (
                    "Com a fonte **Coletas** desligada no filtro global, "
                    "esse(s) dia(s) ficam **sem dados** até o PriceTrack ser "
                    "importado."
                )
            msg = f"📭 **PriceTrack** ainda não cobre: **{dias}**. {corpo}"
            if date.today() in missing_pt:
                msg += (
                    " O PriceTrack de **hoje** entra no import intra-dia "
                    "(~13h/23h BRT) ou no D-1 (06h BRT de amanhã)."
                )
            st.warning(msg)

    # Para PriceTrack o "menor preço" do dia é `min_price` (o piso/buy-box),
    # não o `mode_price` que vira `preco` por padrão no remap. Sobrescrevemos
    # antes de concatenar com coletas pra que o pivot reflita o piso real.
    if not df_pt.empty and "min_price" in df_pt.columns:
        df_pt = df_pt.copy()
        df_pt["preco"] = pd.to_numeric(df_pt["min_price"], errors="coerce") \
                           .fillna(df_pt["preco"])

    # Normaliza schema antes do concat: coletas usa `sku_resolvido` (e nem
    # sempre existe), enquanto pricetrack já traz `sku`. O resto do pipeline
    # lê `df["sku"]` e `df["source"]`, então garantimos ambos nas duas pontas.
    if not df_co.empty:
        df_co = df_co.copy()
        if "sku" not in df_co.columns:
            df_co["sku"] = df_co["sku_resolvido"] \
                if "sku_resolvido" in df_co.columns else pd.NA
        if "source" not in df_co.columns:
            df_co["source"] = "coletas"
        # A tabela `coletas` não tem coluna `title` — nela o título anunciado
        # É o próprio `produto`. Sem este espelho, quando o PriceTrack não
        # contribui com nenhuma linha (fonte desligada no filtro global ou
        # dia sem cobertura PT), o concat sai sem `title` e o drill-down
        # "Ver detalhes de uma linha" quebra com KeyError no
        # `agg(title=("title", "first"))`.
        if "title" not in df_co.columns:
            df_co["title"] = df_co["produto"] \
                if "produto" in df_co.columns else pd.NA

    # Precedência (Q2 Jun/2026): PriceTrack é a fonte dos turnos; as coletas
    # só preenchem lacunas. Calculamos o período nas duas fontes e descartamos
    # as linhas de coletas cujo (data, sku, período) o PriceTrack já cobre.
    # SKUs de coleta sem resolução (sku NULL) nunca casam um SKU canônico do
    # PT, então permanecem como fallback legítimo (produto ainda em REVISAR).
    if not df_co.empty:
        df_co["periodo"] = df_co["turno"].map(_TURNO_TO_PERIODO).fillna("Outro")
    if not df_pt.empty:
        df_pt = df_pt.copy()
        df_pt["periodo"] = df_pt["turno"].map(_TURNO_TO_PERIODO).fillna("Outro")
    if not df_co.empty and not df_pt.empty and "sku" in df_co.columns:
        # Precedência por (data, sku, período, PLATAFORMA): o PriceTrack só
        # "vence" a coleta na MESMA marketplace que ele cobre. Sem a plataforma
        # na chave, um SKU coberto pelo PT no Mercado Livre descartaria a coleta
        # do mesmo SKU no Magalu (que o PT pode não cobrir), abrindo buraco
        # naquele marketplace — e a página pivota justamente por plataforma.
        pt_keys = set(zip(
            df_pt["data"], df_pt["sku"].astype(str),
            df_pt["periodo"], df_pt["plataforma"].astype(str),
        ))
        co_keys = zip(
            df_co["data"], df_co["sku"].astype(str),
            df_co["periodo"], df_co["plataforma"].astype(str),
        )
        mask_dup = pd.Series(
            [k in pt_keys for k in co_keys], index=df_co.index,
        )
        df_co = df_co[~mask_dup]

    df = pd.concat([df_co, df_pt], ignore_index=True) \
        if not (df_co.empty and df_pt.empty) else pd.DataFrame()

    if df.empty:
        st.warning("Sem dados para os filtros selecionados.")
        return

    # Linhas sem preço ou plataforma não contribuem para o piso.
    df = df[df["preco"].notna() & df["plataforma"].notna()].copy()
    df = df[df["plataforma"].isin(_DAILY_VISION_PLATFORMS)]
    if df.empty:
        st.warning("Sem preços válidos nas plataformas monitoradas.")
        return

    # Mapeia turno → período amigável. Turnos desconhecidos viram "Outro".
    df["periodo"] = df["turno"].map(_TURNO_TO_PERIODO).fillna("Outro")
    if sel_periodos:
        df = df[df["periodo"].isin(sel_periodos)]
        if df.empty:
            st.warning("Nenhuma linha no(s) período(s) selecionado(s).")
            return

    # Capacidade (BTU) via catálogo — pricetrack já traz SKU canônico;
    # coletas traz `sku_resolvido` em algumas linhas (não no schema atual
    # devolvido pela query_coletas, então fallback para extração no título).
    cat = get_catalogo()
    sku_to_btu: dict = {}
    if not cat.empty and {"sku", "capacidade_btu"}.issubset(cat.columns):
        sku_to_btu = dict(zip(
            cat["sku"].astype(str),
            cat["capacidade_btu"].astype("Int64").astype(str)
                .where(cat["capacidade_btu"].notna(), None),
        ))

    def _resolve_btu(row) -> str:
        sku_val = row.get("sku")
        if sku_val and str(sku_val) in sku_to_btu:
            v = sku_to_btu[str(sku_val)]
            if v and v != "<NA>":
                return f"{int(float(v)):,}".replace(",", ".")
        # Fallback: extrai do título.
        title = str(row.get("title") or row.get("produto") or "")
        for btu in BTU_OPTIONS:
            dotted = f"{int(btu):,}".replace(",", ".")
            if btu in title or dotted in title:
                return dotted
        return "—"

    df["capacidade"] = df.apply(_resolve_btu, axis=1)

    # Precedência ampliada para vistas agrupadas: quando o pivot é por Marca
    # ou Marca + Capacidade (sem SKU), o PriceTrack manda no recorte inteiro.
    # Para cada (data, marca, [capacidade], período) coberto por qualquer
    # linha PT, descartamos todas as coletas do mesmo recorte — inclusive
    # nas plataformas que o PT não cobriu (trade-off escolhido: clareza
    # vence completude, evita pintar duas linhas "Agratto Manhã" no pivot).
    # Modo SKU (detalhado) mantém a regra fina por (data, sku, período,
    # plataforma) aplicada antes do concat — coletas continuam cobrindo
    # SKUs/plataformas que o PriceTrack não alcança.
    if sel_grupo in ("Marca", "Marca + Capacidade") and not df.empty:
        key_cols = ["data", "marca", "periodo"]
        if sel_grupo == "Marca + Capacidade":
            key_cols.append("capacidade")
        pt_rows = df[df["source"] == "pricetrack"]
        if not pt_rows.empty:
            pt_keys = {
                tuple(r) for r in
                pt_rows[key_cols].astype(str)
                .itertuples(index=False, name=None)
            }
            df_keys = (
                tuple(r) for r in
                df[key_cols].astype(str)
                .itertuples(index=False, name=None)
            )
            keep_mask = [
                not (src == "coletas" and k in pt_keys)
                for src, k in zip(df["source"], df_keys)
            ]
            df = df[keep_mask]
            if df.empty:
                st.warning("Sem dados após aplicar precedência PriceTrack.")
                return

    # Rótulo de origem da linha (PriceTrack vs Coletas Python).
    df["source_label"] = df["source"].map({
        "pricetrack": "PriceTrack",
        "coletas":    "Coletas",
    }).fillna(df["source"].astype(str))

    # SKU de exibição: prefere o canônico; senão usa o produto.
    sku_display = df["sku"].astype(str)
    df["sku_disp"] = sku_display.where(
        df["sku"].notna() & (sku_display.str.strip() != ""),
        df["produto"].astype(str),
    )

    # Janela 7d é preservada em `df_window` antes de cortar `df` ao recorte
    # pedido pelo usuário. Usamos `df_window` adiante para o sparkline 7d
    # por marca e para o delta vs período anterior do KPI "Piso geral";
    # o pivot principal continua plotando só [start_date, end_date].
    data_dates = pd.to_datetime(df["data"], errors="coerce").dt.date
    df_window = df.copy()
    df = df[data_dates >= start_date].copy()
    if df.empty:
        st.warning("Sem dados no recorte selecionado.")
        return

    # Granularidade do pivot — controlado pelo radio "Agrupar por".
    # Data/Source/Turno/Marca são sempre dimensões; Capacidade/SKU entram
    # conforme a escolha. Quando uma dimensão é tirada, o `min` por plataforma
    # passa a consolidar todos os SKUs da marca naquele recorte.
    group_cols = ["data", "source_label", "periodo", "marca"]
    if sel_grupo in ("SKU (detalhado)", "Marca + Capacidade"):
        group_cols.append("capacidade")
    if sel_grupo == "SKU (detalhado)":
        group_cols.append("sku_disp")

    # Menor preço por (linha de visão × plataforma).
    pivot = (
        df.groupby(group_cols + ["plataforma"], dropna=False)["preco"]
        .min()
        .unstack("plataforma")
        .reset_index()
    )
    for plat in _DAILY_VISION_PLATFORMS:
        if plat not in pivot.columns:
            pivot[plat] = pd.NA

    pivot = pivot.rename(columns={
        "data":         "Data",
        "source_label": "Source",
        "periodo":      "Turno",
        "marca":        "Marca",
        "capacidade":   "Capacidade",
        "sku_disp":     "SKU",
    })

    base_cols = ["Data", "Source", "Turno", "Marca"]
    if "Capacidade" in pivot.columns:
        base_cols.append("Capacidade")
    if "SKU" in pivot.columns:
        base_cols.append("SKU")
    sort_cols = [c for c in ["Data", "Marca", "Capacidade", "SKU", "Turno"]
                 if c in pivot.columns]
    pivot = pivot[base_cols + _DAILY_VISION_PLATFORMS].sort_values(
        sort_cols,
        ascending=[False] + [True] * (len(sort_cols) - 1),
    ).reset_index(drop=True)

    # ── Métricas derivadas ────────────────────────────────────────────────
    # `price_matrix` é a matriz numérica (linhas × 6 MPs) que alimenta:
    # (1) destaque do vencedor por linha, (2) gap 1º→2º, (3) campeã global,
    # (4) médias por MP no rodapé. Calculada uma vez antes do Styler.
    price_matrix = pivot[_DAILY_VISION_PLATFORMS].apply(
        pd.to_numeric, errors="coerce",
    )
    row_min = price_matrix.min(axis=1)
    champion_idx = int(row_min.idxmin()) if row_min.notna().any() else None
    champion_brand = (
        str(pivot.loc[champion_idx, "Marca"]) if champion_idx is not None else None
    )
    champion_mp = (
        price_matrix.loc[champion_idx].idxmin()
        if champion_idx is not None and price_matrix.loc[champion_idx].notna().any()
        else None
    )

    # Gap 1º→2º por linha: diferença entre o 2º menor preço e o vencedor.
    # Linhas com menos de 2 ofertas viram NaN (não há gap a comparar).
    def _row_gap(row: pd.Series) -> float | None:
        s = pd.to_numeric(row, errors="coerce").dropna().sort_values()
        if len(s) < 2:
            return None
        return float(s.iloc[1] - s.iloc[0])
    pivot["Gap 1º→2º"] = price_matrix.apply(_row_gap, axis=1)

    # Sparkline 7d por Marca: piso diário (min entre todos os MPs e SKUs)
    # nos 7 dias terminados em `end_date`. Mesma série vale para todas as
    # linhas da mesma Marca, independente do modo. Roda numa query
    # SEPARADA enxuta (3 colunas, cache 15min) p/ não estourar o
    # `statement_timeout` que afetava a query principal de 7 dias.
    #
    # Filtro de BTU vai DIRETO pra query do sparkline como `title.ilike`
    # (mesmo critério do pivot principal). A versão anterior traduzia BTU
    # → SKU set via catálogo, mas marcas cujos SKUs não estão catalogados
    # (Hisense, Agratto em algumas capacidades) saíam com sparkline vazia
    # enquanto o pivot — que usa título — mostrava o piso normalmente.
    window_dates = [spark_start + timedelta(days=i) for i in range(7)]
    sku_set_for_spark = _collect_pt_skus(
        products=sel_skus or None,
        familias_resolvidas=sel_familias or None,
        skus_resolvidos=sel_skus_resolvidos or None,
    )
    df_spark = _query_sparkline_7d(
        spark_start,
        end_date,
        tuple(sorted(sel_brands or [])),
        tuple(sorted(sku_set_for_spark)) if sku_set_for_spark else (),
        tuple(sorted(sel_btu or [])),
    )
    spark_by_brand: dict[str, list[float]] = {}
    nan = float("nan")
    if not df_spark.empty:
        df_spark = df_spark.copy()
        df_spark["_d"] = pd.to_datetime(
            df_spark["collection_date"], errors="coerce",
        ).dt.date
        df_spark["_p"] = pd.to_numeric(df_spark["min_price"], errors="coerce")
        brand_day_min = (
            df_spark.groupby(["brand", "_d"])["_p"].min().reset_index()
        )
        for brand_v, g in brand_day_min.groupby("brand"):
            by_date = dict(zip(g["_d"], g["_p"]))
            # Normaliza chave para o título-case usado em `pivot["Marca"]`
            # — pricetrack_daily publica em CAIXA ALTA ("MIDEA") e o
            # pivot lê "Midea". Sem isso o map devolve sempre vazio.
            #
            # Dias sem dado viram `NaN` (não `None`): `_dv_sparkline_svg`
            # trata NaN como lacuna no traço (a linha liga só pontos
            # válidos), mantendo as 7 posições alinhadas às datas da janela.
            spark_by_brand[str(brand_v).strip().title()] = [
                float(by_date[d]) if d in by_date and pd.notna(by_date[d]) else nan
                for d in window_dates
            ]
    pivot["Tendência 7d"] = pivot["Marca"].astype(str).map(
        lambda b: spark_by_brand.get(b.strip().title(), [nan] * len(window_dates))
    )

    # ── KPIs ──────────────────────────────────────────────────────────────
    # Delta vs período anterior: comparamos o piso do recorte atual com o
    # piso do mesmo recorte de FILTROS no dia anterior a `start_date`. Se
    # `start_date == end_date`, o "anterior" é o dia D-1. `df_window`
    # cobre [D-1, end_date] depois do split feito na carga.
    window_data_dates = pd.to_datetime(
        df_window["data"], errors="coerce",
    ).dt.date
    prev_mask = window_data_dates == prev_day
    prev_min = pd.to_numeric(
        df_window.loc[prev_mask, "preco"], errors="coerce",
    ).dropna().min()
    current_min = float(row_min.min()) if row_min.notna().any() else None
    # `delta_v` (sinalizado), `delta_str` ("R$ x" formatado) e `pct_str`
    # ("-4,6%") alimentam o card "Piso geral" no HTML mais abaixo. Queda do
    # piso (delta_v < 0) é boa notícia p/ o consumidor → card/seta verdes.
    delta_v: float | None = None
    delta_str: str | None = None
    pct_str: str | None = None
    if pd.notna(prev_min) and current_min is not None:
        delta_v = current_min - float(prev_min)
        delta_str = f"R$ {abs(delta_v):,.2f}".replace(
            ",", "X").replace(".", ",").replace("X", ".")
        if float(prev_min) > 0:
            pct_str = f"{delta_v / float(prev_min) * 100:+.1f}%".replace(".", ",")

    # ── Delta vs ontem por (linha do pivot × marketplace) ────────────────
    # Reaproveita `df_window` (que cobre [start_date - 1, end_date]) para
    # calcular o preço-mínimo do dia ANTERIOR nas mesmas dimensões do
    # pivot atual. O delta vira parte do label da célula (ex.:
    # ``"R$ 1.738,17  ▼ R$ 41"``). Quando não há dado de ontem para
    # comparar, a célula sai só com o preço atual (sem seta).
    #
    # IMPORTANTE: o "ontem" é POR LINHA — `row.Data - 1 dia`, NÃO um
    # `prev_day` fixo. Em ranges com múltiplos dias o pivot tem linhas
    # de várias datas e cada uma compara com seu próprio dia anterior.
    #
    # `Source` é DELIBERADAMENTE OMITIDA da chave (e do groupby): se
    # hoje é PriceTrack e ontem só tem Coletas (ou vice-versa, comum
    # quando a ingestão do PT atrasa um dia), exigir match de Source
    # quebra o merge silenciosamente e a célula sai sem delta apesar
    # de termos o preço de ontem. Sem Source, basta casar
    # (Data, Turno, Marca, [Capacidade, SKU]).
    join_cols = [
        c for c in ["Turno", "Marca", "Capacidade", "SKU"]
        if c in pivot.columns
    ]
    _display_to_raw = {
        "Turno":       "periodo",
        "Marca":       "marca",
        "Capacidade":  "capacidade",
        "SKU":         "sku_disp",
    }
    delta_matrix = pd.DataFrame(
        index=pivot.index, columns=_DAILY_VISION_PLATFORMS, dtype=float,
    )
    if not df_window.empty and join_cols:
        raw_key_cols = [_display_to_raw[c] for c in join_cols]
        # Agrega a janela inteira por (data, dims SEM source, plataforma)
        # → min(preco). Colapsar Source no `min` é seguro: se hoje e
        # ontem tiverem fontes diferentes para o mesmo recorte, o piso
        # ainda representa o melhor preço daquele dia/MP.
        window_agg = (
            df_window.groupby(
                ["data"] + raw_key_cols + ["plataforma"], dropna=False,
            )["preco"]
            .min()
            .unstack("plataforma")
            .reset_index()
        )
        for plat in _DAILY_VISION_PLATFORMS:
            if plat not in window_agg.columns:
                window_agg[plat] = pd.NA
        window_agg["data"] = pd.to_datetime(
            window_agg["data"], errors="coerce",
        ).dt.date
        window_agg = window_agg.rename(
            columns={"data": "Data",
                     **dict(zip(raw_key_cols, join_cols))}
        )
        # `pivot_prev` carrega `Data = row.Data - 1 dia` para fazer o
        # merge bater com o dia anterior de CADA linha do pivot.
        pivot_prev = pivot[["Data"] + join_cols].copy()
        pivot_prev["_pos"] = pivot_prev.index
        pivot_prev["Data"] = (
            pd.to_datetime(pivot_prev["Data"], errors="coerce").dt.date
            - timedelta(days=1)
        )
        merged = pivot_prev.merge(
            window_agg[["Data"] + join_cols + _DAILY_VISION_PLATFORMS],
            on=["Data"] + join_cols, how="left",
        ).set_index("_pos")
        for plat in _DAILY_VISION_PLATFORMS:
            curr = pd.to_numeric(pivot[plat], errors="coerce")
            prev = pd.to_numeric(merged[plat], errors="coerce").reindex(
                pivot.index
            )
            delta_matrix[plat] = curr - prev

    # ── Render HTML (mockup "Daily Price Vision") ────────────────────────
    # KPIs + tabela + legenda são montados como HTML por `_dv_build_html`
    # (logos em chip, headers coloridos, sparkline SVG, badge de gap, linha
    # campeã em âmbar, rodapé com média por MP) e injetados via `st.html`.
    st.html(_dv_build_html(_DVContext(
        pivot=pivot,
        base_cols=base_cols,
        price_matrix=price_matrix,
        delta_matrix=delta_matrix,
        champion_idx=champion_idx,
        champion_brand=champion_brand,
        champion_mp=champion_mp,
        current_min=current_min,
        delta_v=delta_v,
        delta_str=delta_str,
        pct_str=pct_str,
        sel_grupo=sel_grupo,
    )))

    # ── Drill-down — quem (SKU/seller) ofertou cada preço da linha ───────
    # A tabela agora é HTML (sem seleção nativa de linha), então o
    # drill-down usa um selectbox: cada opção corresponde a uma linha do
    # recorte (posição + dimensões), mapeada de volta à posição no `pivot`.
    row_labels: list[str] = []
    label_to_pos: dict[str, int] = {}
    for pos, idx in enumerate(pivot.index):
        parts = [str(pivot.loc[idx, "Marca"])]
        if "Capacidade" in pivot.columns:
            parts.append(f"{pivot.loc[idx, 'Capacidade']} BTU")
        if "SKU" in pivot.columns:
            parts.append(str(pivot.loc[idx, "SKU"]))
        parts.append(str(pivot.loc[idx, "Turno"]))
        lbl = f"{pos + 1}. " + " · ".join(
            p for p in parts if p and p not in ("—", "— BTU")
        )
        row_labels.append(lbl)
        label_to_pos[lbl] = pos
    sel_label = st.selectbox(
        "🔎 Ver detalhes de uma linha (SKU · seller · título por marketplace)",
        ["— selecione —"] + row_labels,
        index=0,
        key="dv_detail_pick",
    )
    if sel_label != "— selecione —" and sel_label in label_to_pos:
        row_pos = label_to_pos[sel_label]
        if 0 <= row_pos < len(pivot):
            sel = pivot.iloc[row_pos]
            mask = (
                (df["data"] == sel["Data"])
                & (df["source_label"] == sel["Source"])
                & (df["periodo"] == sel["Turno"])
                & (df["marca"] == sel["Marca"])
            )
            if "Capacidade" in pivot.columns:
                mask &= (df["capacidade"] == sel["Capacidade"])
            if "SKU" in pivot.columns:
                mask &= (df["sku_disp"] == sel["SKU"])
            detail = df[mask].copy()

            badge_parts = [
                f"**Marca:** {sel['Marca']}",
                f"**Turno:** {sel['Turno']}",
                f"**Source:** {sel['Source']}",
            ]
            if "Capacidade" in pivot.columns:
                badge_parts.insert(1, f"**Capacidade:** {sel['Capacidade']}")
            if "SKU" in pivot.columns:
                badge_parts.append(f"**SKU:** {sel['SKU']}")
            st.markdown("### 🔎 Detalhes da linha selecionada")
            st.markdown(" · ".join(badge_parts))

            if detail.empty:
                st.info("Sem ofertas detalhadas para esse recorte.")
            else:
                # Agregamos por (plataforma, sku_disp, seller) — esta é a grão
                # natural da oferta: `produto`/`title` variam por anúncio para
                # o mesmo SKU+seller e, incluídos no groupby, inflavam linhas
                # duplicadas e bagunçavam a marcação 🥇 por marketplace.
                # `sku_disp` é o fallback canônico do pivot — usa `sku` quando
                # presente e o nome amigável do produto para coletas sem SKU
                # mapeado (caso contrário a coluna ficaria em branco).
                base_cols = ["plataforma", "sku_disp", "seller"]
                detail_agg = (
                    detail.groupby(base_cols, dropna=False, as_index=False)
                    .agg(
                        preco=("preco", "min"),
                        produto=("produto", "first"),
                        title=("title", "first"),
                    )
                    .sort_values(["plataforma", "preco"])
                )

                # Marca a oferta vencedora de cada plataforma.
                detail_agg["Vencedora"] = (
                    detail_agg["preco"]
                    == detail_agg.groupby("plataforma")["preco"]
                    .transform("min")
                )
                detail_agg["Vencedora"] = detail_agg["Vencedora"].map(
                    {True: "🥇", False: ""}
                )

                rename_map = {
                    "plataforma": "Marketplace",
                    "sku_disp":   "SKU",
                    "produto":    "Produto",
                    "seller":     "Seller",
                    "title":      "Título anunciado",
                    "preco":      "Preço",
                }
                detail_agg = detail_agg.rename(columns=rename_map)

                cols_order = [c for c in [
                    "Vencedora", "Marketplace", "SKU", "Produto",
                    "Seller", "Título anunciado", "Preço",
                ] if c in detail_agg.columns]
                detail_agg = detail_agg[cols_order]

                st.dataframe(
                    detail_agg,
                    use_container_width=True,
                    hide_index=True,
                    height=min(420, 60 + 35 * len(detail_agg)),
                    column_config={
                        "Preço": st.column_config.NumberColumn(
                            "Preço", format="R$ %.2f",
                        ),
                        "Vencedora": st.column_config.TextColumn(
                            "🏅", width="small",
                            help="Oferta vencedora dentro da plataforma",
                        ),
                        "Título anunciado": st.column_config.TextColumn(
                            "Título anunciado", width="large",
                        ),
                    },
                )
                st.caption(
                    f"{len(detail_agg)} oferta(s) agregada(s) por "
                    f"(marketplace, SKU, seller) · 🥇 = piso por marketplace."
                )
    else:
        st.caption(
            "💡 Selecione uma linha acima para ver SKU, seller e título "
            "do anúncio por marketplace."
        )

    # CSV omite "Tendência 7d" (lista de floats que serializaria feio) e
    # mantém o "Gap 1º→2º" como número limpo p/ análise downstream.
    csv_cols = [c for c in pivot.columns if c != "Tendência 7d"]
    csv_bytes = pivot[csv_cols].to_csv(
        index=False, sep=";", decimal=",",
    ).encode("utf-8-sig")
    st.download_button(
        "⬇️ Baixar CSV",
        data=csv_bytes,
        file_name=f"daily_vision_{start_date}_{end_date}.csv",
        mime="text/csv",
    )


# ---------------------------------------------------------------------------
# Page registry & grouped navigation
# ---------------------------------------------------------------------------

PAGES = {
    "🏠 Overview":                 page_overview,
    "📅 Daily Price Vision":       page_daily_vision,
    "🚨 Top Movers":               page_top_movers,
    "📊 Results":                  page_results,
    "📈 Price Evolution":           page_price_evolution,
    "📊 Market Analytics":         page_market_analytics,
    "🗂️ Ficha do Produto":         page_product_sheet,
    "🏆 BuyBox Position":          page_buybox_position,
    "👑 Share of Buy Box":         page_share_of_buybox,
    "⭐ Reputação & Avaliações":   page_reputacao,
    "📣 SoV Patrocinado":          page_sov_patrocinado,
    "🛡️ Price Compliance":         page_price_compliance,
    "📦 Availability":             page_availability,
    "📧 Email Digest":             page_email_digest,
    "🔔 Price Anomalies":          page_price_anomalies,
    "📂 Import History":           page_import_history,
    "🩺 Data Health":              page_data_health,
    "🤖 Automação":                page_admin_automation,
    "🧬 Família & SKU":            page_familia_sku_admin,
}

_NAV_GROUPS: dict[str, list[str]] = {
    "INSIGHTS": [
        "🏠 Overview",
        "📅 Daily Price Vision",
        "🚨 Top Movers",
        "📊 Results",
        "📈 Price Evolution",
        "📊 Market Analytics",
        "🗂️ Ficha do Produto",
        "🏆 BuyBox Position",
        "👑 Share of Buy Box",
        "⭐ Reputação & Avaliações",
        "📣 SoV Patrocinado",
        "🛡️ Price Compliance",
        "📦 Availability",
    ],
    "OPERAÇÕES": [
        "📧 Email Digest",
        "🔔 Price Anomalies",
        "📂 Import History",
        "🩺 Data Health",
    ],
    "ADMIN": [
        "🤖 Automação",
        "🧬 Família & SKU",
    ],
}

_SECTION_LABEL_CSS = (
    "color:#94a3b8; font-size:0.65rem; font-weight:700; "
    "letter-spacing:0.12em; text-transform:uppercase; "
    "margin:0.75rem 0 0.2rem; padding:0;"
)


def _main() -> None:
    """Renderiza o dashboard (sidebar + página ativa).

    Só roda sob `streamlit run app.py` (ou via AppTest), onde
    ``__name__ == "__main__"``. Importar `app` como módulo (testes unitários)
    NÃO dispara a renderização — as funções puras ficam testáveis sem efeitos
    colaterais de Streamlit/Supabase.
    """
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

    with st.sidebar:
        st.markdown("## ❄️ RAC Monitor")
        st.divider()

        # ── Global filters ─────────────────────────────────────────────────────
        _render_global_filters()
        st.divider()

        # ── Grouped navigation ─────────────────────────────────────────────────
        current = st.session_state["_current_page"]

        for group_label, page_list in _NAV_GROUPS.items():
            st.markdown(f"<p style='{_SECTION_LABEL_CSS}'>{group_label}</p>",
                        unsafe_allow_html=True)
            for page_name in page_list:
                is_active = current == page_name
                # Prefix active page with a bullet so users see which is open
                btn_label = f"▶ {page_name}" if is_active else f"  {page_name}"
                if st.button(btn_label, key=f"nav__{page_name}",
                             use_container_width=True, type="secondary"):
                    st.session_state["_current_page"] = page_name
                    st.rerun()

        st.divider()

        # ── Status footer ──────────────────────────────────────────────────────
        client_ok = _get_supabase() is not None
        st.caption(f"Supabase: {'🟢 conectado' if client_ok else '🔴 desconectado'}")
        st.caption(f"🕐 {date.today().strftime('%d/%m/%Y')}")

    _render_cobertura_banner()
    PAGES[st.session_state["_current_page"]]()


if __name__ == "__main__":
    _main()
