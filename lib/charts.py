"""Chart styling and brand-aware coloring helpers."""

import pandas as pd

_CHART_COLORS = [
    "#1a56db", "#f97316", "#059669", "#8b5cf6",
    "#ef4444", "#0891b2", "#d97706", "#db2777",
]

_STYLE_CELL_THRESHOLD = 50_000  # cells above which row highlight is skipped

_MIDEA_BRAND = "Midea"


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
