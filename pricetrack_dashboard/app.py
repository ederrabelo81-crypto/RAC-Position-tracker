"""
Dashboard de Preços RAC 9K/12K — Supabase (padrão) / API PriceTrack / Demo.

Insights de preço do mercado de ar-condicionado 9.000 e 12.000 BTU (Só Frio),
lidos por tier competitivo (Low/Mid/High = Inverter Lite / AI AirVolution /
AI Ecomaster), no espírito do briefing diário do projeto:

  • Tiers Low/Mid/High: modal (moda) e piso (mínimo) do mercado + Midea vs mercado.
  • Variação Midea: mínimo, máximo, moda e média por capacidade × linha.
  • Lista peer-to-peer: uma linha por modelo EXATO do peer (não agregado por
    marca) — Midea + cada concorrente, com mín/média/moda/máx/n.
  • Evolução: série diária de modal Midea × mediana dos peers, com Delta%
    do período e gap Midea vs peers no último dia.

Rodar (recomendado: dentro do painel do projeto — `streamlit run app.py`,
menu INSIGHTS → 💰 Preços 9K/12K). Standalone:
    streamlit run pricetrack_dashboard/app.py

Credenciais em `.streamlit/secrets.toml` (ou env): `SUPABASE_URL`/`SUPABASE_KEY`
(fonte padrão, rápida) e opcionalmente `PRICETRACK_API_KEY` (fonte "API ao
vivo" — responde em ~2min por consulta, use com paciência). Sem nenhuma das
duas, a página cai em modo Demo — dados sintéticos, marcados como tal.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# `streamlit run pricetrack_dashboard/app.py` executa este arquivo como script
# (sem pacote pai), então imports relativos quebram. Coloca a raiz do repo no
# path e usa imports absolutos — funciona tanto via `streamlit run` quanto
# via `python -m pricetrack_dashboard.app`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pricetrack_api.models import Offer  # noqa: E402

from pricetrack_dashboard.analytics import (  # noqa: E402
    Analysis,
    TierSeries,
    analyze,
    daily_series,
    filter_offers,
)
from pricetrack_dashboard.data_source import (  # noqa: E402
    _supabase_client,
    demo_offers,
    fetch_live,
    fetch_supabase,
    fetch_supabase_range,
    peer_brands,
    supabase_configured,
    supabase_latest_date,
)
from pricetrack_dashboard.peer import (  # noqa: E402
    CAP_ORDER,
    TIER_MIDEA_LINE,
    TIER_ORDER,
)

# ── Paleta ───────────────────────────────────────────────────────────────────
MIDEA_BLUE = "#0B3D91"
MIDEA_ACCENT = "#1E88E5"
MARKET_GREY = "#8895A7"
GOOD_GREEN = "#2E7D32"
BAD_RED = "#C62828"
PEERS_ORANGE = "#E08A2E"
CAP_LABEL = {"9K": "9.000 BTU", "12K": "12.000 BTU"}
CAP_SHORT = {"9K": "9k", "12K": "12k"}

# Brands cuja grafia de marca é convencionalmente TODA MAIÚSCULA (siglas). As
# demais viram Title Case para a lista peer-to-peer (espelha o padrão visto no
# painel de referência: "Gree"/"Agratto"/"Philco" mas "LG"/"TCL").
_BRAND_KEEP_UPPER = {"LG", "TCL"}


def _brand_label(brand: str) -> str:
    b = (brand or "").upper()
    return b if b in _BRAND_KEEP_UPPER else b.title()


def _pct_ptbr(value: float) -> str:
    """Formata percentual com vírgula decimal (pt-BR): 60.2 -> '60,2'."""
    return f"{value:.1f}".replace(".", ",")


def _brl(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return "R$ " + f"{value:,.0f}".replace(",", ".")


def _brl_cents(value: Optional[float]) -> str:
    if value is None:
        return "—"
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "R$ " + s


# ── Carga de dados (cacheada) ────────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=900)
def _load_live(collection_date: Optional[str], use_brand_filter: bool) -> dict:
    """Puxa ao vivo da API e serializa para dict (cacheável). TTL 15 min."""
    brands = peer_brands() if use_brand_filter else []
    result = fetch_live(
        collection_date=collection_date,
        brands=brands if brands else None,
    )
    return {
        "offers": [_offer_to_dict(o) for o in result.offers],
        "collection_date": result.collection_date,
        "days_back": result.days_back,
    }


@st.cache_data(show_spinner=False, ttl=900)
def _load_supabase(collection_date: Optional[str], use_brand_filter: bool) -> dict:
    """Lê pricetrack_daily do Supabase e serializa. TTL 15 min."""
    brands = peer_brands() if use_brand_filter else []
    result = fetch_supabase(
        collection_date=collection_date,
        brands=brands if brands else None,
    )
    return {
        "offers": [_offer_to_dict(o) for o in result.offers],
        "collection_date": result.collection_date,
        "days_back": result.days_back,
    }


@st.cache_data(show_spinner=False, ttl=900)
def _supabase_latest() -> Optional[str]:
    """Data mais recente disponível no Supabase (para default do calendário)."""
    client = _supabase_client()
    if client is None:
        return None
    try:
        return supabase_latest_date(client)
    except Exception:  # noqa: BLE001
        return None


def _offer_to_dict(o: Offer) -> dict:
    return {
        "id": o.id, "sku": o.sku, "title": o.title,
        "product_name": o.product_name, "brand": o.brand,
        "marketplace": o.marketplace, "seller": o.seller,
        "spot_price": o.spot_price, "forward_price": o.forward_price,
        "pix_price": o.pix_price, "price_from": o.price_from,
        "status": o.status,
        "category": o.category, "subcategory": o.subcategory, "family": o.family,
    }


def _dict_to_offer(d: dict) -> Offer:
    return Offer(
        id=d["id"], sku=d["sku"], title=d["title"],
        product_name=d["product_name"], brand=d["brand"],
        category=d.get("category", ""), subcategory=d.get("subcategory", ""),
        family=d.get("family", ""), color=None,
        marketplace=d["marketplace"], seller=d["seller"],
        spot_price=d["spot_price"], forward_price=d["forward_price"],
        pix_price=d["pix_price"], price_from=d["price_from"],
        installment_number=None, installment_value=None,
        status=d["status"], collection_date=None, collection_hour=None,
        image_url="", screenshot_url=None, url="",
    )


# ── Componentes de UI ────────────────────────────────────────────────────────
def _tier_header(tier: str) -> str:
    return f"{tier} · {TIER_MIDEA_LINE[tier]}"


def _midea_badge_html(midea_value: Optional[float], delta: Optional[float]) -> str:
    """Badge 'Midea R$ X (Δ)' em HTML puro.

    Não usa ``st.metric``/``st.markdown`` para esse texto: ambos tratam ``$``
    como abertura de LaTeX inline, e como ``_brl_cents`` produz DOIS cifrões na
    mesma string (valor + delta), o primeiro "engolia" tudo até o segundo —
    era o defeito visto em produção ("Midea R 1.739 (R -60)", sem o "$").
    ``st.html`` renderiza a marcação literalmente, sem esse parsing.

    Seta (▼/▲) e cor: verde+seta-baixo quando Midea é mais barata que o modal
    do mercado (bom); vermelho+seta-cima quando mais cara.
    """
    if midea_value is None:
        return ""
    if delta is None:
        return (f'<span style="font-size:0.85rem;font-weight:600;">'
                f'Midea {_brl_cents(midea_value)}</span>')
    cheaper = delta < 0
    arrow = "▼" if cheaper else ("▲" if delta > 0 else "—")
    color = GOOD_GREEN if cheaper else (BAD_RED if delta > 0 else MARKET_GREY)
    sign = "+" if delta > 0 else ""
    return (
        f'<span style="font-size:0.85rem;font-weight:600;color:{color};">'
        f'{arrow} Midea {_brl_cents(midea_value)} '
        f'<span style="opacity:0.8;font-weight:500;">'
        f'({sign}{_brl_cents(delta)})</span></span>'
    )


def _tier_card_html(tr) -> str:
    return (
        '<div style="border-left:4px solid ' + MIDEA_ACCENT + '; '
        'padding:10px 14px; background:rgba(30,136,229,0.06); '
        'border-radius:8px; margin-bottom:8px;">'
        f'<div style="font-size:0.72rem;letter-spacing:0.06em;'
        f'text-transform:uppercase;color:{MARKET_GREY};font-weight:700;">'
        'Modal do mercado</div>'
        f'<div style="font-size:1.5rem;font-weight:800;margin:2px 0 6px;">'
        f'{_brl_cents(tr.peers.mode)}</div>'
        f'<div style="margin-bottom:6px;">'
        f'{_midea_badge_html(tr.midea.mode, tr.midea_vs_market_delta)}</div>'
        f'<div style="font-size:0.78rem;color:{MARKET_GREY};">'
        f'Piso mercado {_brl_cents(tr.peers.minimum)} · '
        f'Midea piso {_brl_cents(tr.midea.minimum)} · '
        f'{tr.peers.count} ofertas</div>'
        '</div>'
    )


def render_tier_section(analysis: Analysis) -> None:
    st.subheader("Preços por tier — Low / Mid / High")
    st.caption(
        "Cada tier é a linha Midea e o grupo de concorrentes do mesmo ponto de "
        "preço (peer to peer). **Mercado** = ofertas dos concorrentes que "
        "passaram no filtro de Marca/Marketplace/Vendedor acima — Midea nunca "
        "entra nesse cálculo, é sempre a âncora comparada a ele (por isso, ao "
        "filtrar só uma marca, o mercado passa a ser aquela marca). **Modal** = "
        "preço mais frequente do dia; **Piso** = mínimo. ▼ verde = Midea mais "
        "barata que o modal do mercado; ▲ vermelho = mais cara."
    )
    for cap in CAP_ORDER:
        st.markdown(f"#### {CAP_LABEL[cap]}")
        cols = st.columns(len(TIER_ORDER))
        for col, tier in zip(cols, TIER_ORDER):
            tr = analysis.tier(tier, cap)
            with col:
                st.markdown(f"**{_tier_header(tier)}**")
                if tr is None or tr.market.is_empty:
                    st.info("Sem oferta casada")
                    continue
                st.html(_tier_card_html(tr))
        st.divider()


def _tier_table(analysis: Analysis) -> pd.DataFrame:
    rows = []
    for tr in analysis.tiers:
        rows.append({
            "Tier": tr.tier,
            "Linha Midea": tr.midea_line,
            "Capacidade": CAP_LABEL[tr.capacity],
            "Ofertas": tr.peers.count,
            "Modal mercado": tr.peers.mode,
            "Piso mercado": tr.peers.minimum,
            "Modal Midea": tr.midea.mode,
            "Piso Midea": tr.midea.minimum,
            "Δ Midea vs mercado": tr.midea_vs_market_delta,
        })
    return pd.DataFrame(rows)


def render_midea_variation(analysis: Analysis) -> None:
    st.subheader("Variação de preço Midea — mín / máx / moda / média")
    st.caption(
        "Por capacidade e linha (Inverter Lite, AI AirVolution, AI Ecomaster). "
        "A barra vai do **mínimo** ao **máximo**; os marcadores são **moda** "
        "(◆) e **média** (●)."
    )
    for cap in CAP_ORDER:
        fig = _variation_figure(analysis, cap)
        if fig is None:
            st.info(f"{CAP_LABEL[cap]}: sem dados Midea para plotar.")
            continue
        st.markdown(f"#### {CAP_LABEL[cap]}")
        st.plotly_chart(fig, use_container_width=True)


def _variation_figure(analysis: Analysis, cap: str) -> Optional[go.Figure]:
    lines, lows, highs, modes, means, texts = [], [], [], [], [], []
    for tier in TIER_ORDER:
        tr = analysis.tier(tier, cap)
        if tr is None or tr.midea.is_empty:
            continue
        s = tr.midea
        lines.append(f"{TIER_MIDEA_LINE[tier]}<br>({tier})")
        lows.append(s.minimum)
        highs.append(s.maximum)
        modes.append(s.mode)
        means.append(s.mean)
        texts.append(
            f"mín {_brl(s.minimum)} · máx {_brl(s.maximum)}<br>"
            f"moda {_brl(s.mode)} · média {_brl(s.mean)} · n={s.count}"
        )
    if not lines:
        return None

    fig = go.Figure()
    # Faixa mín→máx como barra flutuante
    fig.add_trace(go.Bar(
        x=lines,
        base=lows,
        y=[h - l for h, l in zip(highs, lows)],
        marker_color=MIDEA_ACCENT, opacity=0.35,
        name="Faixa mín–máx", hovertext=texts, hoverinfo="text",
        width=0.5,
    ))
    fig.add_trace(go.Scatter(
        x=lines, y=modes, mode="markers", name="Moda (modal)",
        marker=dict(symbol="diamond", size=16, color=MIDEA_BLUE,
                    line=dict(width=1, color="white")),
    ))
    fig.add_trace(go.Scatter(
        x=lines, y=means, mode="markers", name="Média",
        marker=dict(symbol="circle", size=13, color=BAD_RED,
                    line=dict(width=1, color="white")),
    ))
    # Rótulos de mín e máx
    for x, lo, hi in zip(lines, lows, highs):
        fig.add_annotation(x=x, y=hi, text=_brl(hi), showarrow=False,
                           yshift=12, font=dict(size=11, color=MARKET_GREY))
        fig.add_annotation(x=x, y=lo, text=_brl(lo), showarrow=False,
                           yshift=-12, font=dict(size=11, color=MARKET_GREY))
    fig.update_layout(
        barmode="overlay",
        yaxis_title="Preço à vista (R$)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=40, b=10),
        height=420,
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_yaxes(gridcolor="rgba(136,149,167,0.2)", tickprefix="R$ ")
    return fig


def render_midea_table(analysis: Analysis) -> None:
    rows = []
    for tr in analysis.tiers:
        s = tr.midea
        rows.append({
            "Linha": tr.midea_line,
            "Capacidade": CAP_LABEL[tr.capacity],
            "Ofertas": s.count,
            "Mínimo": s.minimum,
            "Máximo": s.maximum,
            "Moda": s.mode,
            "Média": s.mean,
            "Mediana": s.median,
        })
    df = pd.DataFrame(rows)
    money = ["Mínimo", "Máximo", "Moda", "Média", "Mediana"]
    st.dataframe(
        df.style.format({c: (lambda v: _brl_cents(v) if pd.notna(v) else "—")
                         for c in money}),
        use_container_width=True, hide_index=True,
    )


# ── Lista peer-to-peer — por modelo exato ────────────────────────────────────
def _peer_to_peer_dataframe(analysis: Analysis, tier: str) -> pd.DataFrame:
    """Uma linha por modelo exato do peer (Midea + concorrentes), 9K e 12K."""
    rows = []
    for cap in CAP_ORDER:
        tr = analysis.tier(tier, cap)
        if tr is None:
            continue
        for mr in tr.models:
            rows.append({
                "Marca": _brand_label(mr.brand),
                "Modelo": mr.model_label,
                "BTU": CAP_SHORT[cap],
                "Mín": mr.stats.minimum,
                "Média": mr.stats.mean,
                "Moda": mr.stats.mode,
                "Máx": mr.stats.maximum,
                "n": mr.stats.count,
                "_is_midea": mr.is_midea,
            })
    return pd.DataFrame(rows)


def render_peer_to_peer_list(analysis: Analysis) -> None:
    st.subheader("Lista peer-to-peer — por modelo exato")
    st.caption(
        f"Base `pricetrack_daily`, {analysis.collection_date or '—'}. O tier "
        "acima soma marca+modelo; aqui a leitura é peer-to-peer por **modelo "
        "exato** (um SKU específico do peer por linha — uma marca pode ter "
        "mais de um modelo no mesmo tier). **Midea** vem primeiro em cada "
        "capacidade; os demais, em moda crescente."
    )
    money_cols = ["Mín", "Média", "Moda", "Máx"]
    for tier in TIER_ORDER:
        df = _peer_to_peer_dataframe(analysis, tier)
        if df.empty:
            continue
        st.markdown(f"#### {tier} — {TIER_MIDEA_LINE[tier]}")
        is_midea_col = df["_is_midea"]
        display_df = df.drop(columns=["_is_midea"])

        def _bold_midea_rows(row, _mask=is_midea_col):
            style = "font-weight:700" if _mask.loc[row.name] else ""
            return [style] * len(row)

        styler = display_df.style.format(
            {c: (lambda v: _brl_cents(v) if pd.notna(v) else "—") for c in money_cols}
        ).apply(_bold_midea_rows, axis=1)
        st.dataframe(styler, use_container_width=True, hide_index=True)

        n_total = len(df)
        n_with_data = int((df["n"] > 0).sum())
        if n_with_data < n_total:
            st.caption(
                f"⚠️ {n_total - n_with_data} de {n_total} modelos do peer sem "
                "oferta casada nesta data."
            )
        else:
            st.caption(f"Todos os {n_total} modelos do peer tinham preço nesta data.")


# ── Evolução — Midea (moda) × Peers (mediana) ────────────────────────────────
@st.cache_data(show_spinner=False, ttl=900)
def _load_series_supabase(end_date_iso: str, days: int, use_brand_filter: bool) -> dict:
    """Lê N dias de ``pricetrack_daily`` e serializa para dict (cacheável)."""
    brands = peer_brands() if use_brand_filter else []
    end = date.fromisoformat(end_date_iso)
    start = end - timedelta(days=days - 1)
    rows_by_date = fetch_supabase_range(
        start.isoformat(), end.isoformat(), brands=brands if brands else None,
    )
    return {d: [_offer_to_dict(o) for o in offs] for d, offs in rows_by_date.items()}


def _demo_series(end_date_iso: str, days: int) -> dict:
    """Amostra sintética multi-dia (para a evolução renderizar em modo Demo)."""
    end = date.fromisoformat(end_date_iso)
    out = {}
    for i in range(days):
        d = (end - timedelta(days=days - 1 - i)).isoformat()
        out[d] = demo_offers(seed=1000 + i)
    return out


def _series_figure(ts: TierSeries) -> go.Figure:
    xs = [p.date for p in ts.points]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=[p.midea_mode for p in ts.points],
        mode="lines+markers", name="Midea (moda)",
        line=dict(color=MIDEA_BLUE, width=3), marker=dict(size=5),
    ))
    fig.add_trace(go.Scatter(
        x=xs, y=[p.peers_median for p in ts.points],
        mode="lines", name="Peers (mediana)",
        line=dict(color=PEERS_ORANGE, width=2, dash="dash"),
    ))
    fig.update_layout(
        height=280, margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_yaxes(gridcolor="rgba(136,149,167,0.2)", tickprefix="R$ ")
    fig.update_xaxes(gridcolor="rgba(136,149,167,0.15)")
    return fig


def _series_span_days(ts: TierSeries) -> Optional[int]:
    """Dias de calendário do 1º ao último ponto REAL da série (inclusive).

    Nunca confiar na janela pedida pelo usuário para rotular o Delta: o
    Supabase só guarda a janela quente (`RAC_HOT_WINDOW_DAYS`, ~15 dias por
    padrão) — pedir 30 dias pode devolver bem menos, e rotular "Delta30d"
    nesse caso mentiria sobre o período realmente medido.
    """
    dated = [p.date for p in ts.points if p.midea_mode is not None]
    if len(dated) < 2:
        return None
    first, last = date.fromisoformat(dated[0]), date.fromisoformat(dated[-1])
    return (last - first).days + 1


def _series_caption(ts: TierSeries) -> str:
    delta = ts.delta_pct()
    gap = ts.gap_last()
    span = _series_span_days(ts)
    parts = []
    if delta is not None:
        trend = "subindo" if delta > 0 else ("caindo" if delta < 0 else "estável")
        sign = "+" if delta > 0 else ""
        label = f"Delta{span}d" if span else "Delta"
        parts.append(
            f"Midea {trend} no período ({label}: {sign}{_pct_ptbr(delta)}%)"
        )
    if gap is not None:
        leitura = ("Midea mais barata" if gap < 0
                   else ("Midea mais cara" if gap > 0 else "empatada"))
        parts.append(f"gap vs mediana dos peers em {_brl_cents(gap)} ({leitura})")
    return "; ".join(parts) if parts else "Sem dados suficientes no período."


def _hot_window_days_safe() -> int:
    """Dias que o Supabase mantém antes de migrar pro histórico frio (Drive).

    Import isolado (não `utils.history` inteiro) para não arrastar as
    dependências do Drive por conta de um número; qualquer falha cai no
    default do projeto em vez de quebrar a página.
    """
    try:
        from utils.history.store import hot_window_days
        return hot_window_days()
    except Exception:  # noqa: BLE001
        return 15


def render_peer_evolution(
    source_label: str, collection_date: Optional[str], use_brand_filter: bool,
    keep_brands=None, marketplaces=None, sellers=None,
) -> None:
    st.subheader("Evolução — Midea (moda) × Peers (mediana)")
    st.caption(
        "Série diária por tier/capacidade: linha azul cheia é a moda Midea; "
        "linha laranja tracejada é a mediana dos peers (concorrentes, sem Midea). "
        "Respeita os filtros de Marca/Marketplace/Vendedor acima."
    )
    if source_label == SRC_LIVE:
        st.info(
            "📈 Disponível só com a fonte 🟢 Supabase (ou 🟡 Demo) — a API ao "
            "vivo levaria ~2min por dia, inviável para uma janela de várias datas."
        )
        return

    days = st.select_slider(
        "Janela (dias)", options=[7, 15, 30], value=15, key="pt_series_days")
    end_iso = collection_date or date.today().isoformat()

    if source_label == SRC_DEMO:
        rows_by_date = _demo_series(end_iso, days)
    else:  # SRC_SUPABASE
        hot_days = _hot_window_days_safe()
        if days > hot_days:
            st.caption(
                f"ℹ️ Pedido {days}d, mas o Supabase mantém só a janela quente "
                f"(~{hot_days}d) de `pricetrack_daily` — dias mais antigos "
                "vivem no histórico frio (Drive), fora do alcance desta "
                "página. O gráfico usa os dias realmente disponíveis; a "
                "legenda sempre informa o período real, não o pedido."
            )
        try:
            with st.spinner(f"Lendo {days} dias de pricetrack_daily…"):
                payload = _load_series_supabase(end_iso, days, use_brand_filter)
            rows_by_date = {d: [_dict_to_offer(x) for x in offs]
                            for d, offs in payload.items()}
        except Exception as exc:  # noqa: BLE001
            st.error(f"Falha lendo a série do Supabase: {type(exc).__name__}: {exc}")
            return

    # Mesmos filtros das outras seções, aplicados a cada dia da janela.
    rows_by_date = {
        d: filter_offers(offs, keep_brands=keep_brands,
                         marketplaces=marketplaces, sellers=sellers)
        for d, offs in rows_by_date.items()
    }
    rows_by_date = {d: offs for d, offs in rows_by_date.items() if offs}

    if not rows_by_date:
        st.info("Sem dados no intervalo (após os filtros).")
        return

    series = daily_series(rows_by_date)
    for tier in TIER_ORDER:
        st.markdown(f"#### {tier} ({TIER_MIDEA_LINE[tier]})")
        cols = st.columns(len(CAP_ORDER))
        for col, cap in zip(cols, CAP_ORDER):
            ts = next((s for s in series if s.tier == tier and s.capacity == cap), None)
            with col:
                st.markdown(f"**{CAP_LABEL[cap]}**")
                if ts is None or not ts.has_data:
                    st.info("Sem dado no período.")
                    continue
                st.plotly_chart(_series_figure(ts), use_container_width=True)
                st.caption(_series_caption(ts))


# ── Segredos (env ou st.secrets do Streamlit Cloud) ──────────────────────────
_SECRET_KEYS = ("PRICETRACK_API_KEY", "SUPABASE_URL", "SUPABASE_KEY")


def _bridge_secrets_to_env() -> None:
    """Espelha segredos de `st.secrets` para `os.environ`.

    `pricetrack_api` e o client Supabase leem de `os.environ`. No Streamlit
    Cloud os segredos costumam vir só em `st.secrets`; esta ponte deixa as duas
    fontes (API e Supabase) funcionarem via env ou via secrets.toml.
    """
    for name in _SECRET_KEYS:
        if os.getenv(name, "").strip():
            continue
        try:
            secret = st.secrets.get(name, "")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — secrets pode nem existir
            secret = ""
        if secret:
            os.environ[name] = str(secret).strip()


def _api_key_present() -> bool:
    _bridge_secrets_to_env()
    return bool(os.getenv("PRICETRACK_API_KEY", "").strip())


def _supabase_present() -> bool:
    _bridge_secrets_to_env()
    return supabase_configured()


# Rótulos das fontes
SRC_SUPABASE = "🟢 Supabase (rápido)"
SRC_LIVE = "🔴 API ao vivo (lento)"
SRC_DEMO = "🟡 Demo (offline)"


def _controls(embedded: bool) -> tuple:
    """Renderiza os controles (fonte, data, filtro).

    Retorna ``(source, collection_date, use_brand_filter)`` onde ``source`` é um
    dos rótulos SRC_* e ``collection_date`` é ISO ou None (mais recente).
    """
    supa_ok = _supabase_present()
    api_ok = _api_key_present()
    options = [SRC_SUPABASE, SRC_LIVE, SRC_DEMO]
    default = SRC_SUPABASE if supa_ok else (SRC_LIVE if api_ok else SRC_DEMO)

    container = st.container() if embedded else st.sidebar
    with container:
        if not embedded:
            st.header("Fonte de dados")
        cols = st.columns([1.6, 1.4, 1.4, 0.8]) if embedded else [st] * 4
        c1, c2, c3, c4 = cols

        source = c1.radio(
            "Fonte", options, index=options.index(default),
            key="pt_source",
            help="Supabase lê o import diário (rápido). A API ao vivo bate no "
                 "PriceTrack, porém responde em ~2min por consulta.",
        )

        use_latest = c2.checkbox(
            "Data mais recente", value=True, key="pt_latest",
            help="Usa a última data disponível. Desmarque para escolher no calendário.")
        default_day = _supabase_latest() if source == SRC_SUPABASE else None
        default_day_d = _iso_to_date(default_day) or date.today()
        picked = c2.date_input(
            "Data", value=default_day_d, format="YYYY-MM-DD",
            key="pt_datepick", disabled=use_latest)
        collection_date = None if use_latest else _date_to_iso(picked)

        use_brand_filter = c3.checkbox(
            "Filtrar marcas do peer", value=True, key="pt_brandfilter",
            help="Filtra pelas marcas do peer (mais rápido).")

        if c4.button("🔄 Atualizar", use_container_width=True, key="pt_refresh"):
            _load_live.clear()
            _load_supabase.clear()
            _supabase_latest.clear()
            _load_series_supabase.clear()
            st.rerun()

        if source == SRC_SUPABASE and not supa_ok:
            st.caption("🟢 Supabase não configurado — defina `SUPABASE_URL`/"
                       "`SUPABASE_KEY` (env ou `.streamlit/secrets.toml`).")
        if source == SRC_LIVE and not api_ok:
            st.caption("🔴 `PRICETRACK_API_KEY` não configurada (env ou secrets).")
    return source, collection_date, use_brand_filter


def _iso_to_date(iso: Optional[str]):
    try:
        return date.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None


def _date_to_iso(d) -> Optional[str]:
    return d.isoformat() if hasattr(d, "isoformat") else None


def _load_offers(source: str, collection_date, use_brand_filter):
    """Carrega ofertas CRUAS da fonte → (offers, source_label, date, days_back).

    Cai para Demo (com aviso) quando a fonte escolhida falha. O ``analyze`` é
    feito pelo chamador, depois de aplicar os filtros de Marca/Marketplace/
    Vendedor — assim os filtros valem para todas as seções.
    """
    demo = False
    date_out: Optional[str] = None
    days_back = 0

    if source == SRC_DEMO:
        offers, demo = demo_offers(), True
    elif source == SRC_SUPABASE:
        try:
            with st.spinner("Lendo pricetrack_daily do Supabase…"):
                payload = _load_supabase(collection_date, use_brand_filter)
            offers = [_dict_to_offer(d) for d in payload["offers"]]
            date_out, days_back = payload["collection_date"], payload["days_back"]
        except Exception as exc:  # noqa: BLE001
            st.error(f"Falha lendo o Supabase: {type(exc).__name__}: {exc}\n\n"
                     "Confira `SUPABASE_URL`/`SUPABASE_KEY`. Caindo para Demo.")
            offers, demo = demo_offers(), True
    else:  # SRC_LIVE
        try:
            with st.spinner("Puxando da API do PriceTrack (pode levar ~2min)…"):
                payload = _load_live(collection_date, use_brand_filter)
            offers = [_dict_to_offer(d) for d in payload["offers"]]
            date_out, days_back = payload["collection_date"], payload["days_back"]
        except Exception as exc:  # noqa: BLE001
            st.error(f"Falha no hook ao vivo: {type(exc).__name__}: {exc}\n\n"
                     "A API de coleta é lenta (~2min). Prefira a fonte Supabase. "
                     "Caindo para Demo.")
            offers, demo = demo_offers(), True

    label = SRC_DEMO if demo else source
    return offers, label, date_out, days_back


# ── Filtros de Marca / Marketplace / Vendedor (client-side) ──────────────────
def _distinct(offers, attr: str) -> list:
    """Valores distintos não-vazios de um atributo das ofertas, ordenados."""
    seen = {getattr(o, attr) for o in offers if getattr(o, attr)}
    return sorted(seen, key=str.casefold)


def _peer_competitor_brands(offers) -> list:
    """Concorrentes do peer presentes nas ofertas (Midea nunca entra na lista)."""
    present = {(o.brand or "").upper() for o in offers}
    comp = [b for b in peer_brands() if b != "MIDEA" and b in present]
    return sorted(comp, key=str.casefold)


def _selection_or_none(selected: list, options: list):
    """Recorte só quando é subconjunto próprio e não-vazio; senão None (=Todos).

    Espelha o "Todos" do PriceTrack: nada selecionado, ou tudo selecionado,
    não filtra.
    """
    if not selected or len(selected) >= len(options):
        return None
    return set(selected)


def _render_offer_filters(offers, embedded: bool):
    """Multiselects Marcas / Marketplace / Vendedor. Retorna as 3 seleções
    (cada uma como set p/ filtrar, ou None p/ "Todos")."""
    brand_opts = _peer_competitor_brands(offers)
    mkt_opts = _distinct(offers, "marketplace")
    seller_opts = _distinct(offers, "seller")

    container = st.container() if embedded else st.sidebar
    with container:
        c1, c2, c3 = st.columns(3) if embedded else (st, st, st)
        brands_sel = c1.multiselect(
            "Marcas (vs Midea)", brand_opts, default=brand_opts, key="pt_f_brands",
            help="Midea está sempre presente; escolha os concorrentes a comparar. "
                 "Ex.: só Elgin → Midea vs Elgin em todas as seções.")
        mkt_sel = c2.multiselect(
            "Marketplace", mkt_opts, default=mkt_opts, key="pt_f_mkt")
        seller_sel = c3.multiselect(
            "Vendedor", seller_opts, default=seller_opts, key="pt_f_seller")
        st.caption(
            "ℹ️ Filtro *Grupo* (1P / Lojas Oficiais / 3P) não disponível: o "
            "export do PriceTrack em `pricetrack_daily` não traz essa "
            "classificação (só marketplace e vendedor)."
        )
    return (
        _selection_or_none(brands_sel, brand_opts),
        _selection_or_none(mkt_sel, mkt_opts),
        _selection_or_none(seller_sel, seller_opts),
    )


# ── Corpo da página (reutilizável no painel do projeto) ──────────────────────
def render_page(embedded: bool = False) -> None:
    """Renderiza a página inteira (controles + tiers + variação Midea).

    ``embedded=True`` para embutir no painel do projeto (app.py): sem
    ``set_page_config``, controles no corpo. ``False`` para o app standalone.
    """
    st.title("❄️ Preços RAC 9K/12K — PriceTrack")
    st.caption(
        "Ar-condicionado Só Frio (CO), tiers competitivos peer to peer. "
        "Fonte padrão: Supabase (import diário do PriceTrack); API ao vivo opcional."
    )
    source, collection_date_in, use_brand_filter = _controls(embedded)
    offers, source_label, collection_date, days_back = _load_offers(
        source, collection_date_in, use_brand_filter
    )

    # Filtros Marca/Marketplace/Vendedor — aplicados antes do analyze, valem
    # para todas as seções (cards, lista peer-to-peer e evolução).
    keep_brands, mkts, sellers = _render_offer_filters(offers, embedded)
    offers = filter_offers(offers, keep_brands=keep_brands,
                           marketplaces=mkts, sellers=sellers)
    analysis = analyze(offers, collection_date=collection_date)

    if source_label == SRC_DEMO:
        st.warning(
            "⚠️ **Modo Demo** — dados sintéticos, apenas para visualizar o "
            "layout. Não use como referência de mercado."
        )
    else:
        freshness = {0: "hoje", 1: "ontem"}.get(days_back, f"há {days_back} dias")
        origem = "Supabase" if source_label == SRC_SUPABASE else "API ao vivo"
        st.info(
            f"📅 **{collection_date}** ({freshness}) · fonte: {origem} · "
            f"{analysis.coverage.matched_offers} ofertas casadas no peer "
            f"de {analysis.coverage.total_offers} lidas."
        )

    render_tier_section(analysis)

    with st.expander("📋 Tabela de tiers (modal/piso, mercado × Midea)"):
        df = _tier_table(analysis)
        money = ["Modal mercado", "Piso mercado", "Modal Midea",
                 "Piso Midea", "Δ Midea vs mercado"]
        st.dataframe(
            df.style.format({c: (lambda v: _brl_cents(v) if pd.notna(v) else "—")
                             for c in money}),
            use_container_width=True, hide_index=True,
        )

    st.divider()
    render_midea_variation(analysis)
    render_midea_table(analysis)

    st.divider()
    render_peer_to_peer_list(analysis)

    st.divider()
    render_peer_evolution(source_label, collection_date, use_brand_filter,
                          keep_brands=keep_brands, marketplaces=mkts, sellers=sellers)

    with st.expander("🔎 Cobertura do peer (diagnóstico de casamento)"):
        cov = analysis.coverage
        st.write(
            f"**{cov.peer_models_with_data}/{cov.peer_models}** modelos do peer "
            f"com ao menos uma oferta. **{cov.matched_offers}** ofertas casadas."
        )
        if cov.missing:
            st.caption("Modelos do peer sem oferta casada nesta coleta:")
            st.write(pd.DataFrame({"Modelo sem dado": cov.missing}))
        st.caption(
            "Casamento por código de modelo do fabricante (substring no texto "
            "normalizado da oferta). Cobertura baixa em live pode indicar que o "
            "código não aparece no título/sku — ajuste os códigos em "
            "`pricetrack_dashboard/peer.py`."
        )


def main() -> None:
    st.set_page_config(
        page_title="Preços RAC 9K/12K · PriceTrack",
        page_icon="❄️", layout="wide",
    )
    render_page(embedded=False)


if __name__ == "__main__":
    main()
