"""Email Digest page — weekly e-mail with top movers + BuyBox snapshot."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from lib.email import (
    _badge,
    _email_shell,
    _email_table,
    _render_send_section,
    _render_smtp_help,
)
from lib.formatting import _esc, _fmt_brl
from lib.overview_data import _overview_data


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
