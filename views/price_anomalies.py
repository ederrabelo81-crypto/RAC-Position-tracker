"""Price Anomalies page — day-over-day price changes with optional e-mail send."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from lib.email import _email_shell, _email_table, _render_send_section
from lib.formatting import _csv_download_btn, _esc, _fmt_brl
from lib.overview_data import _overview_data


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
