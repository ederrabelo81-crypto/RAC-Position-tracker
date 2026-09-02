"""Testes das funções puras de UI do app.py (sem runtime Streamlit ativo).

`import pricetrack_dashboard.app` funciona fora de `streamlit run` — nenhuma
chamada de Streamlit roda no nível de módulo, só dentro de funções (chamadas
por `render_page()`). `st.cache_data` também funciona como decorator comum
sem contexto de app ativo.
"""
from __future__ import annotations

import plotly.graph_objects as go

from pricetrack_dashboard import app
from pricetrack_dashboard.analytics import TierSeries, SeriesPoint, daily_series
from pricetrack_dashboard.data_source import demo_offers


class TestMoneyFormatting:
    def test_brl_no_decimals(self):
        assert app._brl(1994.9) == "R$ 1.995"
        assert app._brl(None) == "—"

    def test_brl_cents_ptbr(self):
        assert app._brl_cents(1994.91) == "R$ 1.994,91"
        assert app._brl_cents(-60) == "R$ -60,00"
        assert app._brl_cents(None) == "—"

    def test_pct_ptbr_comma_decimal(self):
        assert app._pct_ptbr(60.2) == "60,2"
        assert app._pct_ptbr(-2.15) == "-2,1"


class TestBrandLabel:
    def test_keeps_acronym_brands_uppercase(self):
        assert app._brand_label("LG") == "LG"
        assert app._brand_label("TCL") == "TCL"

    def test_title_cases_other_brands(self):
        assert app._brand_label("MIDEA") == "Midea"
        assert app._brand_label("PHILCO") == "Philco"
        assert app._brand_label("AGRATTO") == "Agratto"
        assert app._brand_label("GREE") == "Gree"


class TestMideaBadge:
    def test_contains_rs_twice_no_dollar_sign_eaten(self):
        """Regressão do bug: st.metric engolia o '$' por ter 2 cifrões na
        mesma string (LaTeX inline). O badge em HTML puro preserva os dois."""
        html = app._midea_badge_html(1739, -60)
        assert html.count("R$") == 2

    def test_cheaper_is_green_down_arrow(self):
        html = app._midea_badge_html(1000, -50)
        assert "▼" in html and app.GOOD_GREEN in html

    def test_pricier_is_red_up_arrow(self):
        html = app._midea_badge_html(1000, 50)
        assert "▲" in html and app.BAD_RED in html

    def test_none_value_returns_empty(self):
        assert app._midea_badge_html(None, -50) == ""

    def test_none_delta_shows_value_only(self):
        html = app._midea_badge_html(1000, None)
        assert "1.000,00" in html and "▲" not in html and "▼" not in html


class TestTierTableUsesPeersNotMarket:
    """Regressão de produção: `_tier_table`/`_tier_card_html` mostravam o
    modal de `tr.market` (Midea + concorrentes juntos) como "modal do
    mercado" — como a Midea está sempre presente e domina o empate de moda
    (preço MAP repetido entre ofertas), o número parecia não reagir ao
    filtro de Marca (selecionar só um concorrente não movia o modal). As
    colunas "mercado" da tabela têm que vir de `tr.peers` (só concorrentes)."""

    def test_modal_and_piso_mercado_come_from_peers(self):
        import pandas as pd
        from pricetrack_dashboard.analytics import analyze, filter_offers

        offers = demo_offers()
        only_elgin = filter_offers(offers, keep_brands={"ELGIN"})
        analysis = analyze(only_elgin)
        df = app._tier_table(analysis)

        for tr in analysis.tiers:
            row = df[(df["Tier"] == tr.tier)
                     & (df["Capacidade"] == app.CAP_LABEL[tr.capacity])].iloc[0]
            assert pd.isna(row["Modal mercado"]) == (tr.peers.mode is None)
            if tr.peers.mode is not None:
                assert row["Modal mercado"] == tr.peers.mode
                assert row["Piso mercado"] == tr.peers.minimum
            assert row["Ofertas"] == tr.peers.count

    def test_card_html_big_number_is_peers_mode(self):
        """A linha grande do card ("Modal do mercado") tem que ser o modal
        dos concorrentes, não o de `market` (que inclui a Midea)."""
        from pricetrack_dashboard.analytics import analyze, filter_offers

        offers = demo_offers()
        only_elgin = filter_offers(offers, keep_brands={"ELGIN"})
        tr = analyze(only_elgin).tier("Low", "9K")
        html = app._tier_card_html(tr)
        big_number_line = html.split("Modal do mercado</div>")[1].split("</div>")[0]
        assert app._brl_cents(tr.peers.mode) in big_number_line


class TestPeerToPeerDataframe:
    def _analysis(self):
        from pricetrack_dashboard.analytics import analyze
        return analyze(demo_offers(), collection_date="2026-08-31")

    def test_one_row_per_model_both_capacities(self):
        from pricetrack_dashboard.peer import CAP_9K, CAP_12K, PEER, TIER_LOW

        df = app._peer_to_peer_dataframe(self._analysis(), "Low")
        # Uma linha por modelo definido no peer para cada capacidade — deriva
        # do próprio contrato (o peer muda de trimestre), não um número fixo.
        expected = (len(PEER[TIER_LOW][CAP_9K].models)
                    + len(PEER[TIER_LOW][CAP_12K].models))
        assert len(df) == expected
        assert set(df["BTU"]) == {"9k", "12k"}

    def test_midea_first_within_each_capacity_block(self):
        df = self._analysis()
        pp = app._peer_to_peer_dataframe(df, "Low")
        for cap in ("9k", "12k"):
            block = pp[pp["BTU"] == cap].reset_index(drop=True)
            assert block.loc[0, "Marca"] == "Midea"

    def test_peers_sorted_ascending_by_moda_after_midea(self):
        df = app._peer_to_peer_dataframe(self._analysis(), "Low")
        block = df[df["BTU"] == "9k"].reset_index(drop=True)
        peer_modes = block.loc[1:, "Moda"].tolist()
        assert peer_modes == sorted(peer_modes)

    def test_empty_for_unknown_tier_capacity_combo_is_never_hit(self):
        # Todos os 3 tiers têm ambas capacidades definidas — nunca vazio.
        for tier in ("Low", "Mid", "High"):
            assert not app._peer_to_peer_dataframe(self._analysis(), tier).empty


class TestSeriesCaption:
    def _series(self, points):
        return TierSeries(tier="Low", capacity="9K", midea_line="Inverter Lite",
                          points=points)

    def test_rising_trend_and_gap_cheaper(self):
        ts = self._series([
            SeriesPoint("2026-08-01", 1000.0, 1200.0, 5, 5),
            SeriesPoint("2026-08-02", 1100.0, 1150.0, 5, 5),
        ])
        caption = app._series_caption(ts)
        assert "subindo" in caption
        assert "Delta2d" in caption  # 2 pontos, 1 dia de diferença = span de 2
        assert "Midea mais barata" in caption

    def test_falling_trend_and_gap_pricier(self):
        ts = self._series([
            SeriesPoint("2026-08-01", 1200.0, 1000.0, 5, 5),
            SeriesPoint("2026-08-02", 1000.0, 900.0, 5, 5),
        ])
        caption = app._series_caption(ts)
        assert "caindo" in caption
        assert "Midea mais cara" in caption

    def test_insufficient_data_message(self):
        ts = self._series([])
        assert app._series_caption(ts) == "Sem dados suficientes no período."

    def test_label_reflects_real_span_not_a_requested_window(self):
        """Regressão: nunca rotular pelo tamanho de janela pedido pelo
        usuário — só os dados REALMENTE disponíveis (a série pode ser mais
        curta que o pedido por causa da janela quente do Supabase)."""
        ts = self._series([
            SeriesPoint("2026-08-01", 1000.0, 1200.0, 5, 5),
            SeriesPoint("2026-08-10", 1100.0, 1150.0, 5, 5),
        ])
        caption = app._series_caption(ts)
        assert "Delta10d" in caption   # 01 -> 10 de agosto = 10 dias, não 15/30
        assert "Delta15d" not in caption and "Delta30d" not in caption

    def test_span_none_with_single_point(self):
        ts = self._series([SeriesPoint("2026-08-01", 1000.0, 1200.0, 5, 5)])
        assert app._series_span_days(ts) is None


class TestSeriesFigure:
    def test_returns_figure_with_two_traces(self):
        rows_by_date = {
            "2026-08-25": demo_offers(seed=1),
            "2026-08-26": demo_offers(seed=2),
        }
        series = daily_series(rows_by_date)
        ts = next(s for s in series if s.tier == "Low" and s.capacity == "9K")
        fig = app._series_figure(ts)
        assert isinstance(fig, go.Figure)
        assert len(fig.data) == 2
        names = {tr.name for tr in fig.data}
        assert names == {"Midea (moda)", "Peers (mediana)"}


class TestOfferFilterHelpers:
    def _mix(self):
        from pricetrack_api.models import Offer

        def o(brand, mkt, seller):
            return Offer(
                id=f"{brand}-{mkt}-{seller}", sku="", title="", product_name="",
                brand=brand, category="", subcategory="", family="", color=None,
                marketplace=mkt, seller=seller, spot_price=1000.0,
                forward_price=None, pix_price=None, price_from=None,
                installment_number=None, installment_value=None,
                status="AVAILABLE", collection_date=None, collection_hour=None,
                image_url="", screenshot_url=None, url="",
            )
        return [
            o("MIDEA", "AMAZON", "Midea Store"),
            o("LG", "AMAZON", "LG Oficial"),
            o("PHILCO", "MAGALU", "Zé Loja"),
            o("BRASTEMP", "MAGALU", "Fulano"),   # marca fora do peer
        ]

    def test_competitor_brands_excludes_midea_and_non_peer(self):
        brands = app._peer_competitor_brands(self._mix())
        assert "MIDEA" not in brands          # âncora nunca vira opção
        assert "BRASTEMP" not in brands       # fora do peer não entra
        assert "LG" in brands and "PHILCO" in brands

    def test_distinct_marketplaces_sorted_unique(self):
        assert app._distinct(self._mix(), "marketplace") == ["AMAZON", "MAGALU"]

    def test_distinct_sellers(self):
        assert app._distinct(self._mix(), "seller") == \
            ["Fulano", "LG Oficial", "Midea Store", "Zé Loja"]

    def test_selection_or_none_full_or_empty_is_none(self):
        opts = ["A", "B", "C"]
        assert app._selection_or_none(opts, opts) is None      # tudo = Todos
        assert app._selection_or_none([], opts) is None        # nada = Todos
        assert app._selection_or_none(["A"], opts) == {"A"}    # subconjunto filtra
