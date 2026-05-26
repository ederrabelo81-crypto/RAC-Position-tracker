"""Supabase client + cached queries for filter options, SKUs, raw coletas."""

import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from lib.brands import (
    _MARCA_TO_CANONICAL,
    _expand_brands,
    _expand_platforms,
    _normalize_platform,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUPABASE_PAGE = 1000  # PostgREST default server-side max_rows cap

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
