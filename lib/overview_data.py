"""Cached Supabase query specifically for overview / top-movers pages."""

import pandas as pd
import streamlit as st

from lib.brands import _MARCA_TO_CANONICAL, _expand_brands, _expand_platforms
from lib.supabase import _SUPABASE_PAGE, _get_supabase


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
