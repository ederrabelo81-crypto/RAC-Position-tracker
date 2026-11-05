"""
diagnostico_page.py — página 🩻 Diagnóstico do app.py (Streamlit).

Reproduz a aba Diagnóstico do PriceTrack só para ar-condicionado, padrão Split
Hi-Wall Inverter até 30.000 BTU. As contas vivem em ``utils/diagnostico.py``
(fórmulas em ``docs/diagnostico.md``); aqui só há filtro, gráfico e tabela.

Os blocos recebem o frame pronto e não sabem de onde ele veio — é o gancho
para, numa próxima fase, sobrepor posição média e dono da Buy Box (coletas
próprias) nos mesmos gráficos.
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Callable, Optional

import pandas as pd
import plotly.express as px
import streamlit as st

from utils import ac_attributes as aa
from utils import diagnostico as dg

FOCO_PADRAO = "CASAS BAHIA"
MARCA_PADRAO = "MIDEA"
_LARGURA_FOCO = 4.5
#: Azul Midea do app.py — o foco (marketplace ou marca) sempre na mesma cor.
_COR_FOCO = "#1a56db"


def _brl(valor: Optional[float]) -> str:
    """R$ no formato brasileiro (``R$ 1.234,56``)."""
    if valor is None or pd.isna(valor):
        return "—"
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _destacar(fig, nome: str) -> None:
    """Linha mais grossa para o foco (marketplace ou marca)."""
    for trace in fig.data:
        if getattr(trace, "name", None) == nome:
            trace.line.width = _LARGURA_FOCO
            trace.line.color = _COR_FOCO
            trace.marker.color = _COR_FOCO
            trace.marker.size = 9
        else:
            trace.line.width = 1.6
            trace.opacity = 0.75


def _estilo(fig, altura: int = 420) -> None:
    fig.update_layout(
        height=altura, hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.2, x=0.5, xanchor="center", title_text=""),
        margin=dict(l=40, r=10, t=40, b=110),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#e2e8f0", title_text="",
                     tickformat="%d/%m", hoverformat="%d/%m/%Y")
    fig.update_yaxes(showgrid=True, gridcolor="#e2e8f0")


def _pontos(evento) -> list:
    """Pontos selecionados num ``st.plotly_chart(on_select=...)``."""
    try:
        return list(evento.selection.points)  # type: ignore[union-attr]
    except AttributeError:
        try:
            return list(evento["selection"]["points"])
        except (KeyError, TypeError):
            return []


def _serie_do_ponto(fig, ponto: dict) -> Optional[str]:
    """Nome da série (traço) do ponto clicado, via ``curve_number``."""
    n = ponto.get("curve_number")
    if n is None or not (0 <= int(n) < len(fig.data)):
        return ponto.get("legendgroup")
    return fig.data[int(n)].name


def _drill(df: pd.DataFrame, pontos: list, fig, coluna_serie: str, rotulo: str) -> None:
    """Lista de anúncios do(s) ponto(s) clicado(s): (dia × série)."""
    if not pontos:
        return
    alvos = set()
    for p in pontos:
        dia = pd.to_datetime(p.get("x")).date() if p.get("x") is not None else None
        serie = _serie_do_ponto(fig, p)
        if dia is not None and serie:
            alvos.add((dia, serie))
    if not alvos:
        return
    sel = df[[(d, s) in alvos for d, s in zip(df["dia"], df[coluna_serie])]]
    with st.expander(f"🔎 {len(sel):,} anúncio(s) no ponto selecionado ({rotulo})", expanded=True):
        st.dataframe(
            sel.sort_values("preco")[["dia", "marketplace", "brand", "brand_original", "sku", "title", "seller", "preco"]]
            .rename(columns={"brand": "marca", "brand_original": "marca no PriceTrack",
                             "title": "produto", "preco": "menor preço à vista"}),
            hide_index=True, use_container_width=True,
            column_config={"menor preço à vista": st.column_config.NumberColumn(format="R$ %.2f")},
        )


def _opcoes(serie: pd.Series) -> list:
    return sorted(v for v in serie.dropna().unique() if str(v).strip())


def render(carregar: Callable[[date, date], pd.DataFrame], inicio: date, fim: date) -> None:
    """Desenha a página. ``carregar(inicio, fim)`` devolve o frame preparado."""
    st.title("🩻 Diagnóstico")
    st.caption(
        "Ar-condicionado · padrão **Split Hi-Wall Inverter até 30.000 BTU** · "
        "fonte: PriceTrack (histórico no Drive) · fórmulas em `docs/diagnostico.md`"
    )

    base = carregar(inicio, fim)
    if base.empty:
        st.warning(
            "Sem dado do PriceTrack no período no histórico (Drive). "
            "Confira o import diário e o status do histórico na barra lateral."
        )
        return

    ultimo = max(base["dia"])
    ontem = date.today() - timedelta(days=1)
    if ultimo < ontem and fim >= ontem:
        st.warning(
            f"⚠️ Último dia disponível: **{ultimo.strftime('%d/%m/%Y')}** — anterior a ontem. "
            "O import do PriceTrack de D-1 pode ter atrasado."
        )

    # ── Filtros ──────────────────────────────────────────────────────────────
    mkts = _opcoes(base["marketplace"])
    c1, c2, c3 = st.columns(3)
    foco = c1.selectbox(
        "Marketplace foco", mkts,
        index=mkts.index(FOCO_PADRAO) if FOCO_PADRAO in mkts else 0, key="dg_foco",
    )
    marcas = _opcoes(base["brand"])
    marca_foco = c2.selectbox(
        "Marca foco", marcas,
        index=marcas.index(MARCA_PADRAO) if MARCA_PADRAO in marcas else 0, key="dg_marca",
    )
    subs = _opcoes(base["subcategory"])
    subcats = c3.multiselect(
        "Subcategoria", subs,
        default=[aa.SUBCATEGORIA_HIWALL] if aa.SUBCATEGORIA_HIWALL in subs else [],
        key="dg_sub", help="Só ar-condicionado. Padrão: Hi-Wall.",
    )
    c4, c5, c6 = st.columns(3)
    tecs = _opcoes(base["tecnologia"])
    tecnologias = c4.multiselect(
        "Tecnologia", tecs,
        default=[aa.TECNOLOGIA_INVERTER] if aa.TECNOLOGIA_INVERTER in tecs else [], key="dg_tec",
    )
    btu_opcoes = sorted(int(b) for b in base["btu"].dropna().unique() if b <= aa.BTU_MAXIMO_ESCOPO)
    btus = c5.multiselect("Capacidade (BTU)", btu_opcoes, key="dg_btu",
                          format_func=lambda b: f"{b:,}".replace(",", "."))
    ciclos = c6.multiselect("Ciclo", _opcoes(base["ciclo"]), key="dg_ciclo")

    recorte = dg.apply_filters(
        base, subcategorias=subcats or None, tecnologias=tecnologias or None,
        btus=btus or None, ciclos=ciclos or None,
    )
    produtos = (
        recorte.drop_duplicates("sku").assign(
            rotulo=lambda d: d["brand"] + " · " + d["sku"] + " — " + d["title"].fillna("").str.slice(0, 70)
        ).sort_values(["brand", "sku"])
    )
    rotulo_por_sku = dict(zip(produtos["sku"], produtos["rotulo"]))
    skus = st.multiselect(
        "Produtos × Marca (vazio = todos do recorte)", list(rotulo_por_sku),
        format_func=rotulo_por_sku.get, key="dg_skus",
    )
    if skus:
        recorte = recorte[recorte["sku"].isin(skus)]

    with st.expander("⚙️ Mediana de mercado"):
        incluir_foco = st.toggle("Incluir o marketplace foco na mediana", False, key="dg_med_foco")
        incluir_lojas = st.toggle(
            "Incluir lojas próprias de fabricante (MIDEA, LG, SAMSUNG…)", False, key="dg_med_lojas",
            help="Fora por padrão: ali o fabricante define o preço sozinho.",
        )
    kw = {"incluir_foco": incluir_foco, "excluir_lojas_marca": not incluir_lojas}

    if recorte.empty:
        st.info("Nenhum anúncio no recorte escolhido.")
        return
    st.caption(
        f"{recorte['sku'].nunique():,} produtos · {recorte['marketplace'].nunique():,} marketplaces · "
        f"{len(recorte):,} anúncios-dia · {recorte['dia'].nunique()} dias"
    )

    _bloco_ofertas(recorte, foco)
    if len(subcats) > 1:
        _bloco_dimensao(recorte, foco, "subcategoria", "Anúncios por subcategoria", marca_foco)
    _bloco_dimensao(recorte, foco, "marca", "Anúncios por marca", marca_foco)
    _bloco_preco_minimo(recorte, foco, marca_foco)
    _bloco_profundidade(recorte, foco, marca_foco, kw)
    _bloco_agressivos(recorte, foco, kw)
    skus_recorte = set(recorte["sku"])
    _bloco_comparador(recorte, foco, {k: v for k, v in rotulo_por_sku.items()
                                      if k in skus_recorte})
    _bloco_exportar(recorte, foco, inicio, fim)


def _bloco_ofertas(df: pd.DataFrame, foco: str) -> None:
    st.subheader("1 · Quantidade de ofertas — foco vs. mercado")
    st.caption("Anúncios ativos = pares produto × seller distintos por dia. Top 10 marketplaces + o foco.")
    r = dg.ofertas_por_marketplace(df, foco)
    fig = px.line(r, x="dia", y="ofertas", color="marketplace", markers=True)
    _estilo(fig)
    _destacar(fig, foco)
    ev = st.plotly_chart(fig, use_container_width=True, on_select="rerun",
                         selection_mode="points", key="dg_g1")
    _drill(df, _pontos(ev), fig, "marketplace", "dia × marketplace")


def _bloco_dimensao(df: pd.DataFrame, foco: str, dimensao: str, titulo: str, marca_foco: str) -> None:
    st.subheader(f"{'2' if dimensao == 'subcategoria' else '3'} · {titulo} — {foco}")
    r = dg.anuncios_por_dimensao(df, foco, dimensao)
    if r.empty:
        st.info(f"Sem anúncios em {foco} no recorte.")
        return
    modo = "Qtd."
    if dimensao == "marca":
        modo = st.radio("Mostrar", ["Qtd.", "%"], horizontal=True, key="dg_marca_modo")
        if modo == "%":
            r = r[r["grupo"] != dg.TODAS]
    y = "qtd" if modo == "Qtd." else "pct"
    fig = px.line(r, x="dia", y=y, color="grupo", markers=True)
    _estilo(fig)
    if dimensao == "marca":
        _destacar(fig, marca_foco)
    st.plotly_chart(fig, use_container_width=True, key=f"dg_g_{dimensao}")


def _bloco_preco_minimo(df: pd.DataFrame, foco: str, marca_foco: str) -> None:
    st.subheader(f"4 · Preço mínimo praticado — {foco}")
    st.caption("Produto mais barato de cada marca no foco, por dia (menor preço à vista).")
    r = dg.preco_minimo_por_marca(df, foco)
    if r.empty:
        st.info(f"Sem preço em {foco} no recorte.")
        return
    fig = px.line(r, x="dia", y="preco_min", color="marca", markers=True,
                  hover_data={"sku": True, "seller": True, "title": True})
    _estilo(fig)
    _destacar(fig, marca_foco)
    fig.update_yaxes(tickprefix="R$ ", separatethousands=True)
    ev = st.plotly_chart(fig, use_container_width=True, on_select="rerun",
                         selection_mode="points", key="dg_g5")
    _drill(df[df["marketplace"] == foco], _pontos(ev), fig, "brand", "dia × marca no foco")


def _bloco_profundidade(df: pd.DataFrame, foco: str, marca_foco: str, kw: dict) -> None:
    st.subheader(f"5 · Profundidade vs. mercado — {foco}")
    st.caption(
        "Quanto a marca está mais barata (+) ou mais cara (−) que a mediana de mercado, em %: "
        "mediana, entre os produtos da marca, de (mediana − preço no foco) / mediana."
    )
    r = dg.profundidade_por_marca(df, foco, **kw)
    if r.empty:
        st.info("Sem produtos com preço no foco e no mercado ao mesmo tempo.")
        return
    fig = px.line(r, x="dia", y="profundidade_pct", color="marca", markers=True,
                  hover_data={"n_skus": True})
    _estilo(fig)
    _destacar(fig, marca_foco)
    fig.add_hline(y=0, line_dash="dash", line_color="#64748b",
                  annotation_text="Mediana do mercado", annotation_position="bottom right")
    fig.update_yaxes(ticksuffix="%")
    st.plotly_chart(fig, use_container_width=True, key="dg_g6")


def _bloco_agressivos(df: pd.DataFrame, foco: str, kw: dict) -> None:
    st.subheader(f"6 · Produtos mais agressivos no período — {foco}")
    st.caption("Mais abaixo da mediana de mercado na média do período; presença em pelo menos metade dos dias.")
    r = dg.produtos_agressivos(df, foco, limite=10, **kw)
    if r.empty:
        st.info("Nenhum produto com presença suficiente no período.")
        return
    tabela = pd.DataFrame({
        "#": range(1, len(r) + 1),
        "Produto": r["produto"],
        "Marca": r["marca"],
        "Menor preço": r["menor_preco"].map(_brl),
        "Seller": [
            s if n <= 1 else f"{s} (menor de {n} sellers)"
            for s, n in zip(r["seller"], r["n_sellers_foco"])
        ],
        "Mediana mercado": r["mediana_mercado"].map(_brl),
        "% abaixo": r["pct_abaixo_medio"].round(1),
        "Preço no período": r["serie_precos"],
    })
    st.dataframe(
        tabela, hide_index=True, use_container_width=True,
        column_config={
            "% abaixo": st.column_config.NumberColumn(format="%.1f%%"),
            "Preço no período": st.column_config.LineChartColumn(),
        },
    )


def _bloco_comparador(df: pd.DataFrame, foco: str, rotulos: dict) -> None:
    st.subheader("7 · Comparador de produtos")
    skus = st.multiselect("Produtos", list(rotulos), format_func=rotulos.get,
                          max_selections=8, key="dg_cmp")
    if not skus:
        st.caption("Escolha até 8 produtos para sobrepor o histórico de preço.")
        return
    so_foco = st.toggle(f"Só {foco}", True, key="dg_cmp_foco")
    r = dg.historico_produtos(df, skus, foco=foco if so_foco else None)
    if r.empty:
        st.info("Sem preço para esses produtos no recorte.")
        return
    if so_foco:
        fig = px.line(r, x="dia", y="preco_min", color="sku", markers=True)
    else:
        r = r.assign(serie=r["sku"] + " · " + r["marketplace"])
        fig = px.line(r, x="dia", y="preco_min", color="serie", markers=True)
    _estilo(fig)
    fig.update_yaxes(tickprefix="R$ ", separatethousands=True)
    st.plotly_chart(fig, use_container_width=True, key="dg_g8")


def _bloco_exportar(df: pd.DataFrame, foco: str, inicio: date, fim: date) -> None:
    st.subheader("8 · Exportar dados")
    out = dg.anuncios_dia(df)
    st.caption(f"{len(out):,} anúncios-dia no recorte (todos os marketplaces).")
    nome = f"diagnostico_{inicio.isoformat()}_{fim.isoformat()}"
    c1, c2 = st.columns(2)
    csv = out.to_csv(sep=";", index=False).encode("utf-8-sig")
    c1.download_button("⬇️ CSV (;)", csv, f"{nome}.csv", "text/csv", key="dg_csv")
    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            out.to_excel(xw, index=False, sheet_name="anuncios_dia")
        c2.download_button(
            "⬇️ XLSX", buf.getvalue(), f"{nome}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dg_xlsx",
        )
    except ImportError:
        c2.caption("XLSX indisponível neste servidor (falta `openpyxl`); use o CSV.")
