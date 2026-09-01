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
from datetime import date
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

from pricetrack_dashboard.analytics import Analysis, analyze  # noqa: E402
from pricetrack_dashboard.data_source import (  # noqa: E402
    _supabase_client,
    demo_offers,
    fetch_live,
    fetch_supabase,
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
            _load_live.clear(); _load_supabase.clear(); _supabase_latest.clear()
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


def _load_analysis(source: str, collection_date, use_brand_filter):
    """Carrega ofertas da fonte escolhida → (analysis, source_label, date, days_back).

    Cai para Demo (com aviso) quando a fonte escolhida falha.
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
    return analyze(offers, collection_date=date_out), label, date_out, days_back


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
    analysis, source_label, collection_date, days_back = _load_analysis(
        source, collection_date_in, use_brand_filter
    )

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
