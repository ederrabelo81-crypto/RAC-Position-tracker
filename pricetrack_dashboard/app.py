"""
Dashboard de Preços RAC 9K/12K — hook ao vivo na API do PriceTrack.

Insights de preço do mercado de ar-condicionado 9.000 e 12.000 BTU (Só Frio),
lidos por tier competitivo (Low/Mid/High = Inverter Lite / AI AirVolution /
AI Ecomaster), no espírito do briefing diário do projeto:

  • Tiers Low/Mid/High: modal (moda) e piso (mínimo) do mercado + Midea vs mercado.
  • Variação Midea: mínimo, máximo, moda e média por capacidade × linha.

Rodar (numa máquina com acesso à API):
    export PRICETRACK_API_KEY=...          # ou em .env / secrets do Streamlit
    streamlit run pricetrack_dashboard/app.py

Sem acesso à API (ex.: sandbox com egress bloqueado), use o modo Demo na
barra lateral — dados sintéticos, marcados como demo, só para ver o layout.
"""
from __future__ import annotations

import os
import sys
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

from pricetrack_dashboard.analytics import Analysis, analyze  # noqa: E402
from pricetrack_dashboard.data_source import (  # noqa: E402
    demo_offers,
    fetch_live,
    peer_brands,
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
CAP_LABEL = {"9K": "9.000 BTU", "12K": "12.000 BTU"}


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
    """Puxa ao vivo e serializa para dict (cacheável). TTL 15 min."""
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


def render_tier_section(analysis: Analysis) -> None:
    st.subheader("Preços por tier — Low / Mid / High")
    st.caption(
        "Cada tier é a linha Midea e o grupo de concorrentes do mesmo ponto de "
        "preço (peer to peer). **Modal** = preço mais frequente do dia; "
        "**Piso** = mínimo. Δ negativo = Midea mais barata que o modal do mercado."
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
                delta = tr.midea_vs_market_delta
                st.metric(
                    label="Modal do mercado",
                    value=_brl(tr.market.mode),
                    delta=(None if delta is None
                           else f"Midea {_brl(tr.midea.mode)} ({'+' if delta > 0 else ''}{_brl(delta)})"),
                    delta_color=("inverse"),
                )
                st.caption(
                    f"Piso mercado {_brl(tr.market.minimum)} · "
                    f"Midea piso {_brl(tr.midea.minimum)} · "
                    f"{tr.market.count} ofertas"
                )
        st.divider()


def _tier_table(analysis: Analysis) -> pd.DataFrame:
    rows = []
    for tr in analysis.tiers:
        rows.append({
            "Tier": tr.tier,
            "Linha Midea": tr.midea_line,
            "Capacidade": CAP_LABEL[tr.capacity],
            "Ofertas": tr.market.count,
            "Modal mercado": tr.market.mode,
            "Piso mercado": tr.market.minimum,
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


# ── App ──────────────────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(
        page_title="Preços RAC 9K/12K · PriceTrack",
        page_icon="❄️", layout="wide",
    )
    st.title("❄️ Preços RAC 9K/12K — PriceTrack ao vivo")
    st.caption(
        "Ar-condicionado Só Frio (CO), tiers competitivos peer to peer. "
        "Dados diretos da API do PriceTrack."
    )

    with st.sidebar:
        st.header("Fonte de dados")
        key_present = bool(os.getenv("PRICETRACK_API_KEY", "").strip())
        default_mode = "🔴 Ao vivo (API)" if key_present else "🟡 Demo (offline)"
        mode = st.radio(
            "Modo",
            ["🔴 Ao vivo (API)", "🟡 Demo (offline)"],
            index=(0 if key_present else 1),
        )
        if key_present:
            st.success("PRICETRACK_API_KEY detectada.")
        else:
            st.warning(
                "PRICETRACK_API_KEY não configurada — modo Demo. "
                "Defina a key no ambiente ou em `.streamlit/secrets.toml` "
                "para o hook ao vivo."
            )
        st.divider()
        date_override = st.text_input(
            "Data (YYYY-MM-DD, vazio = mais recente)", value=""
        ).strip() or None
        use_brand_filter = st.checkbox(
            "Filtrar por marcas do peer (mais rápido)", value=True,
            help="Filtro server-side pelas marcas do peer. Desmarque para puxar "
                 "tudo e casar 100% no cliente (mais lento).",
        )
        if st.button("🔄 Atualizar agora", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    is_live = mode.startswith("🔴")

    # Carrega ofertas
    offers: List[Offer]
    collection_date: Optional[str] = None
    days_back = 0
    demo = False
    if is_live:
        try:
            with st.spinner("Puxando ofertas da API do PriceTrack…"):
                payload = _load_live(date_override, use_brand_filter)
            offers = [_dict_to_offer(d) for d in payload["offers"]]
            collection_date = payload["collection_date"]
            days_back = payload["days_back"]
        except Exception as exc:  # noqa: BLE001 — superfície de erro amigável
            st.error(
                f"Falha no hook ao vivo: {type(exc).__name__}: {exc}\n\n"
                "Verifique a `PRICETRACK_API_KEY` e o acesso de rede a "
                "`api.pricetrack.com.br`. Caindo para o modo Demo."
            )
            offers = demo_offers()
            demo = True
    else:
        offers = demo_offers()
        demo = True

    analysis = analyze(offers, collection_date=collection_date)

    # Faixa de frescor
    if demo:
        st.warning(
            "⚠️ **Modo Demo** — dados sintéticos, apenas para visualizar o "
            "layout. Não use como referência de mercado."
        )
    else:
        freshness = {0: "hoje", 1: "ontem"}.get(days_back, f"há {days_back} dias")
        st.info(
            f"📅 Coleta de **{collection_date}** ({freshness}) · "
            f"{analysis.coverage.matched_offers} ofertas casadas no peer "
            f"de {analysis.coverage.total_offers} puxadas."
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


if __name__ == "__main__":
    main()
