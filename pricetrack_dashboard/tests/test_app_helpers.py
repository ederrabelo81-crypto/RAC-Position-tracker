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
