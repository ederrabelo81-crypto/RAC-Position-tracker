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
from dataclasses import replace
from datetime import date, timedelta
from typing import List, Optional

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
    analyze_with_fallback,
    daily_series,
    filter_offers,
)
from pricetrack_dashboard.data_source import (  # noqa: E402
    _supabase_client,
    demo_offers,
    fetch_live,
    fetch_supabase_range_detailed,
    PRICE_BASIS_BEST_CASH,
    PRICE_BASIS_SPOT_LEGACY,
    PRICE_BASIS_UNKNOWN,
    peer_brands,
    supabase_configured,
    supabase_latest_date,
)
from pricetrack_dashboard.peer import (  # noqa: E402
    CAP_ORDER,
    TIER_MIDEA_LINE,
    TIER_ORDER,
)

# ── Turnos de `pricetrack_daily` ─────────────────────────────────────────────
# A tabela guarda três recortes por dia (migração 003): "Diário" (dia inteiro),
# "Manhã" (08–12h BRT) e "Tarde" (18–22h BRT). Até Set/2026 esta página lia
# "Diário" fixo, sem seletor — então não havia como reproduzir o painel do
# PriceTrack, que é sempre olhado num turno específico, e a divergência ficava
# sem explicação na tela.
TURNO_DIARIO = "Diário"
TURNO_MANHA = "Manhã"
TURNO_TARDE = "Tarde"
TURNO_OPTIONS = [TURNO_DIARIO, TURNO_MANHA, TURNO_TARDE]

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


def _fmt_br_date(iso: Optional[str]) -> str:
    """ISO -> dd/mm/aaaa; devolve a string original se não for uma data."""
    d = _iso_to_date(iso)
    return d.strftime("%d/%m/%Y") if d else (iso or "—")


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
def _supabase_latest(turno: str = TURNO_DIARIO) -> Optional[str]:
    """Data mais recente COM AQUELE TURNO (default do calendário).

    O turno entra na consulta, não só na leitura: cada turno tem sua própria
    última data em `pricetrack_daily`, e resolver a data pelo "Diário" para
    depois consultar "Tarde" devolve uma janela vazia sempre que o turno
    escolhido está atrasado ou ausente naquele dia.
    """
    client = _supabase_client()
    if client is None:
        return None
    try:
        return supabase_latest_date(client, turno)
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
    # Badge de confiança da amostra (🟢/🟡/🔴)
    badge = tr.peers.confidence_badge
    tooltip = f"Amostra {tr.peers.confidence_label}: {tr.peers.count} ofertas"
    
    return (
        '<div style="border-left:4px solid ' + MIDEA_ACCENT + '; '
        'padding:10px 14px; background:rgba(30,136,229,0.06); '
        'border-radius:8px; margin-bottom:8px;">'
        f'<div style="font-size:0.72rem;letter-spacing:0.06em;'
        f'text-transform:uppercase;color:{MARKET_GREY};font-weight:700;">'
        f'Modal do mercado <span title="{tooltip}">{badge}</span></div>'
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
                if tr is None or tr.peers.is_empty:
                    st.info("Sem oferta casada")
                    continue
                st.html(_tier_card_html(tr))
                if tr.fallback_date:
                    st.caption(
                        "⚠️ Sem oferta casada no período selecionado — "
                        f"mostrando o último dado disponível "
                        f"({_fmt_br_date(tr.fallback_date)})."
                    )
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
            "Dado de": _fmt_br_date(tr.fallback_date) if tr.fallback_date else "",
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
            "Confiança": f"{s.confidence_badge} {s.confidence_label}",
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
                "Confiança": f"{mr.stats.confidence_badge}",
                "Mín": mr.stats.minimum,
                "Média": mr.stats.mean,
                "Moda": mr.stats.mode,
                "Máx": mr.stats.maximum,
                "n": mr.stats.count,
                "Dado de": _fmt_br_date(mr.fallback_date) if mr.fallback_date else "",
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
        n_fallback = int((df["Dado de"] != "").sum())
        n_weak_sample = int((df["Confiança"] == "🔴").sum())
        if n_with_data < n_total:
            st.caption(
                f"⚠️ {n_total - n_with_data} de {n_total} modelos do peer sem "
                "oferta casada, mesmo com fallback do histórico recente."
            )
        elif n_fallback:
            st.caption(
                f"ℹ️ {n_fallback} de {n_total} modelos sem oferta casada no "
                "período selecionado — mostrando o último dado disponível de "
                "cada um."
            )
        if n_weak_sample and n_with_data > 0:
            st.caption(
                f"🔴 {n_weak_sample} de {n_with_data} modelos com oferta têm "
                "**amostra fraca** (<10 ofertas) — interpretar com cautela."
            )
        elif n_fallback:
            st.caption(
                f"Cada um (coluna 'Dado de'); os demais são do período pedido."
            )
        else:
            st.caption(f"Todos os {n_total} modelos do peer tinham preço no período selecionado.")


# ── Janela de datas (período + evolução + fallback) ──────────────────────────
def _fetch_range_dict(
    start_iso: str, end_iso: str, use_brand_filter: bool, turno: str = TURNO_DIARIO,
) -> dict:
    """Lê ``pricetrack_daily`` de ``start_iso`` a ``end_iso`` e serializa para
    dict (corpo compartilhado dos loaders cacheados abaixo).

    Devolve ``{"by_date": {...}, "price_basis": {...}}``: o carimbo de base
    viaja junto com as ofertas porque é ele que diz se a janela mistura dado
    corrigido com dado da base antiga — sem isso a tela não teria como avisar.
    """
    brands = peer_brands() if use_brand_filter else []
    result = fetch_supabase_range_detailed(
        start_iso, end_iso, brands=brands if brands else None, turno=turno,
    )
    return {
        "by_date": {d: [_offer_to_dict(o) for o in offs]
                    for d, offs in result.by_date.items()},
        "price_basis": result.price_basis,
    }


@st.cache_data(show_spinner=False, ttl=900)
def _load_series_supabase(
    end_date_iso: str, days: int, use_brand_filter: bool, turno: str = TURNO_DIARIO,
) -> dict:
    """Lê N dias de ``pricetrack_daily`` e serializa para dict (cacheável)."""
    end = date.fromisoformat(end_date_iso)
    start = end - timedelta(days=days - 1)
    return _fetch_range_dict(start.isoformat(), end_date_iso, use_brand_filter, turno)


@st.cache_data(show_spinner=False, ttl=900)
def _load_window_supabase(
    start_iso: str, end_iso: str, use_brand_filter: bool, turno: str = TURNO_DIARIO,
) -> dict:
    """Lê um intervalo arbitrário de ``pricetrack_daily`` — usado para o
    período pedido nos tiers (que pode ser um range) somado à janela de
    lookback do fallback (dias antes do período)."""
    return _fetch_range_dict(start_iso, end_iso, use_brand_filter, turno)


def _demo_window(start_iso: str, end_iso: str) -> dict:
    """Amostra sintética cobrindo um intervalo (Demo — tiers/fallback/evolução)."""
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    out: dict = {}
    d, i = start, 0
    while d <= end:
        out[d.isoformat()] = demo_offers(seed=1000 + i)
        d += timedelta(days=1)
        i += 1
    return out


def _demo_series(end_date_iso: str, days: int) -> dict:
    """Amostra sintética multi-dia (para a evolução renderizar em modo Demo)."""
    end = date.fromisoformat(end_date_iso)
    start = end - timedelta(days=days - 1)
    return _demo_window(start.isoformat(), end_date_iso)


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
    keep_brands=None, marketplaces=None, sellers=None, turno: str = TURNO_DIARIO,
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
                payload = _load_series_supabase(
                    end_iso, days, use_brand_filter, turno)
            rows_by_date = {d: [_dict_to_offer(x) for x in offs]
                            for d, offs in payload.get("by_date", {}).items()}
            # A janela da evolução é maior que a dos cards, então pode alcançar
            # dias da base antiga que os cards nem leem — a checagem é repetida
            # aqui de propósito, junto do gráfico que a mistura distorce. Uma
            # série que emenda duas bases desenha o degrau da virada como se
            # fosse tendência de mercado, que é o pior formato possível do erro.
            if not render_price_basis_notice(payload.get("price_basis", {})):
                return
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


# Span default do seletor de intervalo (só define o range inicial mostrado
# no calendário — o usuário pode alargar/encurtar livremente).
_RANGE_DEFAULT_SPAN_DAYS = 6


def _normalize_date_range(value, fallback: date) -> tuple:
    """Normaliza o retorno do ``date_input`` em modo intervalo p/ (start, end).

    O widget só devolve uma tupla de 2 quando as duas pontas já foram
    clicadas; enquanto isso, devolve uma tupla de 1 (ou um ``date`` solto) —
    tratado aqui como um intervalo de 1 dia só, para nunca quebrar a página
    no meio da seleção do usuário.
    """
    if isinstance(value, (tuple, list)):
        if len(value) >= 2:
            start, end = value[0], value[1]
        elif len(value) == 1:
            start = end = value[0]
        else:
            start = end = fallback
    else:
        start = end = value or fallback
    if start > end:
        start, end = end, start
    return start, end


def _controls(embedded: bool) -> tuple:
    """Renderiza os controles (fonte, data/período, filtro).

    Retorna ``(source, date_range, use_brand_filter, use_latest, turno)`` onde
    ``source`` é um dos rótulos SRC_* e ``date_range`` é ``(start_iso,
    end_iso)`` — os dois iguais para um único dia. Com "Data mais recente"
    marcado e fonte ao vivo, o ``date_range`` é só um placeholder: o
    carregamento resolve a data mais recente sondando a própria API.
    """
    supa_ok = _supabase_present()
    api_ok = _api_key_present()
    options = [SRC_SUPABASE, SRC_LIVE, SRC_DEMO]
    default = SRC_SUPABASE if supa_ok else (SRC_LIVE if api_ok else SRC_DEMO)

    container = st.container() if embedded else st.sidebar
    with container:
        if not embedded:
            st.header("Fonte de dados")
        cols = st.columns([1.4, 1.8, 1.2, 0.8]) if embedded else [st] * 4
        c1, c2, c3, c4 = cols

        source = c1.radio(
            "Fonte", options, index=options.index(default),
            key="pt_source",
            help="Supabase lê o import diário (rápido). A API ao vivo bate no "
                 "PriceTrack, porém responde em ~2min por consulta.",
        )

        # O turno é lido ANTES da data porque a data default depende dele: a
        # "mais recente" tem de ser a última data COM AQUELE TURNO. Lendo o
        # último dia de "Diário" e consultando "Tarde" nele, um turno atrasado
        # ou ausente devolvia janela vazia em vez da última data disponível.
        turno = c3.selectbox(
            "Turno", TURNO_OPTIONS, index=0, key="pt_turno",
            help="Recorte do dia em `pricetrack_daily`: Diário = dia inteiro; "
                 "Manhã = coletas 08–12h; Tarde = coletas 18–22h (BRT). "
                 "Use o mesmo turno do painel do PriceTrack para comparar "
                 "número com número.",
        )
        if source == SRC_LIVE and turno != TURNO_DIARIO:
            c3.caption("ℹ️ Turno só vale para a fonte 🟢 Supabase.")

        use_latest = c2.checkbox(
            "Data mais recente", value=True, key="pt_latest",
            help="Usa a última data disponível. Desmarque para escolher no calendário.")
        range_supported = source != SRC_LIVE
        use_range = c2.checkbox(
            "Intervalo de datas", value=False, key="pt_range",
            help="Soma as ofertas de TODOS os dias do período no cálculo "
                 "(mín/máx/moda/média) — em vez de olhar só um dia isolado. "
                 "Indisponível na API ao vivo (lenta demais para várias datas).",
            disabled=use_latest or not range_supported,
        )
        default_day = _supabase_latest(turno) if source == SRC_SUPABASE else None
        default_day_d = _iso_to_date(default_day) or date.today()

        if use_latest:
            c2.date_input(
                "Data", value=default_day_d, format="YYYY-MM-DD",
                key="pt_datepick_latest", disabled=True)
            start_d = end_d = default_day_d
        elif use_range and range_supported:
            picked = c2.date_input(
                "Período", format="YYYY-MM-DD", key="pt_datepick_range",
                value=(default_day_d - timedelta(days=_RANGE_DEFAULT_SPAN_DAYS),
                       default_day_d),
            )
            start_d, end_d = _normalize_date_range(picked, default_day_d)
        else:
            picked = c2.date_input(
                "Data", value=default_day_d, format="YYYY-MM-DD", key="pt_datepick")
            start_d = end_d = picked

        date_range = (_date_to_iso(start_d), _date_to_iso(end_d))

        use_brand_filter = c3.checkbox(
            "Filtrar marcas do peer", value=True, key="pt_brandfilter",
            help="Filtra pelas marcas do peer (mais rápido).")

        if c4.button("🔄 Atualizar", use_container_width=True, key="pt_refresh"):
            _load_live.clear()
            _supabase_latest.clear()
            _load_series_supabase.clear()
            _load_window_supabase.clear()
            st.rerun()

        if source == SRC_SUPABASE and not supa_ok:
            st.caption("🟢 Supabase não configurado — defina `SUPABASE_URL`/"
                       "`SUPABASE_KEY` (env ou `.streamlit/secrets.toml`).")
        if source == SRC_LIVE and not api_ok:
            st.caption("🔴 `PRICETRACK_API_KEY` não configurada (env ou secrets).")
        if source == SRC_LIVE and use_range:
            st.caption("ℹ️ Intervalo de datas não é suportado na API ao vivo — "
                       "usando só a data selecionada.")
    return source, date_range, use_brand_filter, use_latest, turno


def _iso_to_date(iso: Optional[str]):
    try:
        return date.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None


def _date_to_iso(d) -> Optional[str]:
    return d.isoformat() if hasattr(d, "isoformat") else None


def _period_dates(start_iso: str, end_iso: str) -> List[str]:
    """Todas as datas ISO entre ``start_iso`` e ``end_iso``, inclusive."""
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    out = []
    d = start
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _load_window(
    source: str, date_range: tuple, use_brand_filter: bool, use_latest: bool,
    fallback_days: int, turno: str = TURNO_DIARIO,
):
    """Carrega o período pedido + uma janela de lookback antes dele (fallback).

    Retorna ``(rows_by_date, period_dates, source_label, days_back, basis)``,
    onde ``basis`` é ``{price_basis: nº de linhas}`` do que foi lido — é o que
    permite avisar na tela quando a janela mistura a base de preço corrigida
    com o histórico gravado na base errada.
    ``rows_by_date`` cobre tanto o período quanto o lookback (``Offer``s já
    desserializados, indexados por data ISO); ``period_dates`` são as datas
    do período pedido pelo usuário (as demais chaves de ``rows_by_date`` são
    só candidatas a fallback); ``days_back`` só é significativo quando o
    período é de 1 dia só (freshness "hoje"/"ontem"/"há N dias").

    Cai para Demo (com aviso) quando a fonte escolhida falha. Filtros de
    Marca/Marketplace/Vendedor são aplicados pelo chamador, depois deste
    carregamento — assim valem tanto para o período quanto para o fallback.
    """
    start_iso, end_iso = date_range
    period_dates = _period_dates(start_iso, end_iso)
    demo = False
    days_back = 0
    basis: dict = {}

    if source == SRC_DEMO:
        window_start = (date.fromisoformat(start_iso) - timedelta(days=fallback_days)).isoformat()
        rows_by_date = _demo_window(window_start, end_iso)
        demo = True
    elif source == SRC_SUPABASE:
        window_start = (date.fromisoformat(start_iso) - timedelta(days=fallback_days)).isoformat()
        try:
            with st.spinner("Lendo pricetrack_daily do Supabase…"):
                payload = _load_window_supabase(
                    window_start, end_iso, use_brand_filter, turno)
            basis = payload.get("price_basis", {})
            rows_by_date = {d: [_dict_to_offer(x) for x in offs]
                            for d, offs in payload.get("by_date", {}).items()}
            if not rows_by_date:
                st.warning(
                    "⚠️ Sem dados em `pricetrack_daily` para o período "
                    "selecionado."
                )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Falha lendo o Supabase: {type(exc).__name__}: {exc}\n\n"
                     "Confira `SUPABASE_URL`/`SUPABASE_KEY`. Caindo para Demo.")
            rows_by_date = _demo_window(window_start, end_iso)
            demo = True
    else:  # SRC_LIVE — sem intervalo nem fallback (a API ao vivo é lenta demais)
        collection_date = None if use_latest else start_iso
        try:
            with st.spinner("Puxando da API do PriceTrack (pode levar ~2min)…"):
                payload = _load_live(collection_date, use_brand_filter)
            offers = [_dict_to_offer(d) for d in payload["offers"]]
            resolved_date = payload["collection_date"]
            days_back = payload["days_back"]
            rows_by_date = {resolved_date: offers}
            period_dates = [resolved_date]
            basis = {PRICE_BASIS_BEST_CASH: len(offers)}
        except Exception as exc:  # noqa: BLE001
            st.error(f"Falha no hook ao vivo: {type(exc).__name__}: {exc}\n\n"
                     "A API de coleta é lenta (~2min). Prefira a fonte Supabase. "
                     "Caindo para Demo.")
            rows_by_date = {end_iso: demo_offers()}
            period_dates = [end_iso]
            demo = True

    if source != SRC_LIVE and len(period_dates) == 1:
        try:
            days_back = max(0, (date.today() - date.fromisoformat(period_dates[0])).days)
        except (TypeError, ValueError):
            days_back = 0

    label = SRC_DEMO if demo else source
    return rows_by_date, period_dates, label, days_back, ({} if demo else basis)


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
def render_price_basis_notice(basis: dict) -> bool:
    """Avisa sobre a base de preço lida. Retorna False se NÃO se pode agregar.

    ``spot_legacy`` são linhas gravadas antes da correção de Set/2026: o preço
    é o `spotPrice` (à vista cheio), não o menor entre spot e PIX que o painel
    do PriceTrack mostra — em marketplace com desconto PIX (Magazine Luiza,
    10%) fica ~10% acima do preço real.

    Uma janela com **uma** base só é agregável: `spot_legacy` puro dá um número
    consistente (errado em ~10%, e o aviso diz isso), `best_cash` puro dá o
    número certo. Uma janela **misturada** não é agregável de jeito nenhum —
    piso, moda e média sairiam de duas réguas diferentes e o degrau da virada
    apareceria como movimento de mercado. Nesse caso a função devolve False e o
    chamador não renderiza análise: um aviso vermelho acima de um número
    inválido ainda é um número inválido, e é o número que as pessoas copiam.

    Silencioso quando tudo é `best_cash` — aviso que aparece sempre vira
    decoração e para de ser lido.
    """
    if not basis:
        return True
    legacy = int(basis.get(PRICE_BASIS_SPOT_LEGACY, 0))
    good = int(basis.get(PRICE_BASIS_BEST_CASH, 0))
    # Carimbo que esta versão não conhece conta como não-comparável.
    unknown = {k: v for k, v in basis.items()
               if k.startswith(PRICE_BASIS_UNKNOWN)}
    n_unknown = sum(int(v) for v in unknown.values())
    total = legacy + good + n_unknown
    reimport = (
        "Corrija reimportando os dias afetados do NDJSON bruto:\n\n"
        "```\npython scripts/pricetrack_api_import.py --force "
        "--start AAAA-MM-DD --end AAAA-MM-DD\n```"
    )

    if n_unknown:
        st.error(
            f"⛔ **Base de preço desconhecida** — {n_unknown:,} de {total:,} "
            f"linhas trazem um `price_basis` que esta versão do dashboard não "
            f"sabe interpretar ({', '.join(sorted(unknown))}). Sem saber a "
            f"régua, nenhum piso ou modal é confiável — a análise não foi "
            f"calculada. Atualize o dashboard ou confira o import."
        )
        return False

    if legacy and good:
        st.error(
            f"⛔ **Bases de preço misturadas — análise não calculada.** "
            f"{legacy:,} de {total:,} linhas desta janela estão na base antiga "
            f"(`spot_legacy`: à vista cheio, sem o desconto PIX, com oferta "
            f"indisponível somada ao piso) e {good:,} em `best_cash`. Piso, "
            f"modal e média sairiam de duas réguas diferentes, e o degrau da "
            f"virada pareceria movimento de mercado.\n\n"
            f"Escolha um período inteiro numa base só, ou termine o reimport.\n\n"
            + reimport
        )
        return False

    if legacy:
        st.warning(
            f"⚠️ **Base de preço antiga** — as {legacy:,} linhas desta janela "
            "são `spot_legacy`: o preço é o à vista cheio (`spotPrice`), não o "
            "menor entre à vista e PIX que o painel do PriceTrack exibe. Em "
            "marketplace com desconto PIX o número fica ~10% acima do real. "
            "Os números abaixo são consistentes entre si, mas altos nessa "
            "medida.\n\n"
            + reimport
        )
    return True


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
    source, date_range, use_brand_filter, use_latest, turno = _controls(embedded)
    fallback_days = _hot_window_days_safe() if source != SRC_LIVE else 0
    rows_by_date, period_dates, source_label, days_back, price_basis = _load_window(
        source, date_range, use_brand_filter, use_latest, fallback_days, turno,
    )

    # Amostra p/ montar os multiselects de Marca/Marketplace/Vendedor: a
    # união do PERÍODO pedido (nunca o lookback do fallback, que fica
    # invisível para o usuário) — assim as opções cobrem qualquer dia do
    # intervalo escolhido, não só um dia âncora.
    period_sample = [o for d in period_dates for o in rows_by_date.get(d, [])]
    keep_brands, mkts, sellers = _render_offer_filters(period_sample, embedded)

    # Filtros Marca/Marketplace/Vendedor — aplicados antes do analyze, em TODO
    # o intervalo carregado (período + lookback do fallback), valem para
    # todas as seções (cards, lista peer-to-peer e evolução).
    filtered_by_date = {
        d: filter_offers(offs, keep_brands=keep_brands, marketplaces=mkts, sellers=sellers)
        for d, offs in rows_by_date.items()
    }
    analysis = analyze_with_fallback(filtered_by_date, period_dates)

    is_range = len(period_dates) > 1
    period_label = (
        period_dates[0] if len(period_dates) == 1
        else f"{period_dates[0]} a {period_dates[-1]}" if period_dates
        else "—"
    )
    analysis = replace(analysis, collection_date=period_label)

    if source_label == SRC_DEMO:
        st.warning(
            "⚠️ **Modo Demo** — dados sintéticos, apenas para visualizar o "
            "layout. Não use como referência de mercado."
        )
    elif is_range:
        origem = "Supabase" if source_label == SRC_SUPABASE else "API ao vivo"
        turno_txt = f" · turno: {turno}" if source_label == SRC_SUPABASE else ""
        st.info(
            f"📅 **{period_label}** ({len(period_dates)} dias) · fonte: {origem}"
            f"{turno_txt} · {analysis.coverage.matched_offers} ofertas casadas "
            f"no peer de {analysis.coverage.total_offers} lidas no período "
            f"(soma dos dias)."
        )
    else:
        freshness = {0: "hoje", 1: "ontem"}.get(days_back, f"há {days_back} dias")
        origem = "Supabase" if source_label == SRC_SUPABASE else "API ao vivo"
        turno_txt = f" · turno: {turno}" if source_label == SRC_SUPABASE else ""
        st.info(
            f"📅 **{period_label}** ({freshness}) · fonte: {origem}{turno_txt} · "
            f"{analysis.coverage.matched_offers} ofertas casadas no peer "
            f"de {analysis.coverage.total_offers} lidas."
        )

    if not render_price_basis_notice(price_basis):
        # Base não agregável: o aviso já explicou. Renderizar os cards mesmo
        # assim entregaria um número inválido logo abaixo do alerta — e é o
        # número que as pessoas copiam para o relatório, não o alerta.
        return

    if source_label != SRC_DEMO:
        st.caption(
            "💵 Preço = **melhor à vista da última coleta** da janela (menor "
            "entre à vista e PIX), a mesma base que o painel do PriceTrack "
            "exibe com À Vista + PIX + Menor. Linhas antigas sem esse dado caem "
            "para o piso do dia. Uma linha de `pricetrack_daily` é uma "
            "listagem-dia agregada, não uma oferta."
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
    render_peer_evolution(source_label, period_dates[-1] if period_dates else None,
                          use_brand_filter, keep_brands=keep_brands,
                          marketplaces=mkts, sellers=sellers, turno=turno)

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
