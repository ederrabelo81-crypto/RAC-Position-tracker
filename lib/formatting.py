"""Generic formatting & HTML escaping helpers + CSV download button."""

import html as _html

import pandas as pd
import streamlit as st


def _esc(value) -> str:
    """HTML-escape a value for safe inclusion in e-mail markup."""
    return _html.escape(str(value), quote=False)


def _fmt_brl(value: float) -> str:
    """Format float as Brazilian Real string: R$ 1.234,56"""
    try:
        return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


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
