"""Extraction & classification of product technical specs (BTU, ciclo, voltagem)."""

import re
from datetime import date

import pandas as pd
import streamlit as st

from lib.supabase import query_coletas

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
