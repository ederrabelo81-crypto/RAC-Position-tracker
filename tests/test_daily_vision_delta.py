"""
tests/test_daily_vision_delta.py — "vs ontem" do 📅 Daily Price Vision só na mesma fonte.

Até 30/09/2026 a seta de cada célula e o KPI "Piso geral" comparavam hoje com
ontem SEM a fonte na chave (para não perder a seta quando o PriceTrack
atrasa). Com o PriceTrack faltando num dia (25, 26 e 29/09/2026), hoje
PriceTrack contra ontem Coletas virava "variação" — media a troca de fonte
(outra medida, outro mix), não o preço. Agora a seta só compara a mesma fonte
e a célula cujo ontem só existe na outra fonte diz isso ("⇄ outra fonte").
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402 — importável sem renderizar

ONTEM, HOJE = date(2026, 9, 29), date(2026, 9, 30)
NAN = float("nan")


def _raw(data, fonte, marca, plataforma, preco, periodo="Manhã"):
    return {"data": data, "source_label": fonte, "periodo": periodo,
            "marca": marca, "plataforma": plataforma, "preco": preco}


@pytest.fixture
def window() -> pd.DataFrame:
    """29/09 sem PriceTrack (só Coletas); 30/09 com PriceTrack no ML."""
    return pd.DataFrame([
        _raw(ONTEM, "Coletas", "Midea", "Mercado Livre", 1900.0),
        _raw(ONTEM, "Coletas", "Midea", "Amazon", 2000.0),
        _raw(ONTEM, "PriceTrack", "LG", "Mercado Livre", 3000.0),
        _raw(HOJE, "PriceTrack", "Midea", "Mercado Livre", 2400.0),
        _raw(HOJE, "Coletas", "Midea", "Amazon", 2100.0),
        _raw(HOJE, "PriceTrack", "LG", "Mercado Livre", 2900.0),
        _raw(HOJE, "PriceTrack", "Gree", "Mercado Livre", 2500.0),
    ])


def _pivot(rows: list[dict]) -> pd.DataFrame:
    p = pd.DataFrame(rows)
    for plat in app._DAILY_VISION_PLATFORMS:
        if plat not in p.columns:
            p[plat] = NAN
    return p


@pytest.fixture
def pivot() -> pd.DataFrame:
    base = {"Data": HOJE, "Turno": "Manhã"}
    return _pivot([
        {**base, "Source": "PriceTrack", "Marca": "Midea", "Mercado Livre": 2400.0},
        {**base, "Source": "Coletas", "Marca": "Midea", "Amazon": 2100.0},
        {**base, "Source": "PriceTrack", "Marca": "LG", "Mercado Livre": 2900.0},
        {**base, "Source": "PriceTrack", "Marca": "Gree", "Mercado Livre": 2500.0},
    ])


class TestDeltaCelula:
    def test_pricetrack_hoje_contra_coletas_ontem_nao_vira_seta(self, pivot, window):
        """O caso do incidente: sem PriceTrack ontem, a velha chave sem fonte
        dava +R$ 500 (2400 PT − 1900 Coletas) — pura troca de fonte."""
        delta, outra = app._dv_delta_vs_ontem(pivot, window)
        assert pd.isna(delta.loc[0, "Mercado Livre"])
        assert bool(outra.loc[0, "Mercado Livre"])

    def test_mesma_fonte_mantem_a_seta(self, pivot, window):
        delta, outra = app._dv_delta_vs_ontem(pivot, window)
        assert delta.loc[1, "Amazon"] == 100.0          # Coletas × Coletas
        assert delta.loc[2, "Mercado Livre"] == -100.0  # PriceTrack × PriceTrack
        assert not outra.loc[1, "Amazon"] and not outra.loc[2, "Mercado Livre"]

    def test_sem_ontem_em_fonte_nenhuma_fica_sem_marca(self, pivot, window):
        delta, outra = app._dv_delta_vs_ontem(pivot, window)
        assert pd.isna(delta.loc[3, "Mercado Livre"])
        assert not outra.loc[3, "Mercado Livre"]

    def test_celula_vazia_hoje_nao_ganha_marca(self, pivot, window):
        _, outra = app._dv_delta_vs_ontem(pivot, window)
        assert not outra.loc[0, "Amazon"]  # PT não tem preço na Amazon hoje

    def test_ontem_e_por_linha(self, window):
        """Range com vários dias: cada linha compara com o próprio dia anterior."""
        extra = pd.concat([window, pd.DataFrame([
            _raw(date(2026, 9, 28), "Coletas", "Midea", "Amazon", 1800.0)])])
        p = _pivot([
            {"Data": HOJE, "Turno": "Manhã", "Source": "Coletas", "Marca": "Midea", "Amazon": 2100.0},
            {"Data": ONTEM, "Turno": "Manhã", "Source": "Coletas", "Marca": "Midea", "Amazon": 2000.0},
        ])
        delta, _ = app._dv_delta_vs_ontem(p, extra)
        assert delta["Amazon"].tolist() == [100.0, 200.0]

    def test_janela_vazia(self, pivot):
        delta, outra = app._dv_delta_vs_ontem(pivot, pd.DataFrame())
        assert delta.isna().all().all() and not outra.any().any()


def _ontem(window: pd.DataFrame) -> pd.DataFrame:
    return window[window["data"] == ONTEM]


class TestPisoGeral:
    def test_piso_pricetrack_sem_pricetrack_ontem(self, window):
        """O caso do incidente: a velha regra dava 2400 (PT) − 1900 (Coletas)."""
        midea = _ontem(window).query("marca == 'Midea'")
        prev, nota = app._dv_floor_prev("PriceTrack", {"Mercado Livre"}, midea)
        assert prev is None
        assert nota == "ontem só em Coletas — outra fonte, sem delta (hoje: PriceTrack)"

    def test_piso_compara_so_a_mesma_fonte(self, window):
        """O menor preço de ontem, entre as duas fontes, é 1900 (Coletas).
        Para um piso de hoje em PriceTrack, a base é o piso PT de ontem (3000)."""
        assert app._dv_floor_prev("PriceTrack", {"Mercado Livre"}, _ontem(window)) == (3000.0, None)

    def test_piso_compara_so_os_mesmos_marketplaces(self, window):
        """Hoje as coletas só têm Amazon (o ML passou para o PriceTrack). Ontem,
        sem PriceTrack, as coletas também cobriam o ML a 1900 — a base é a
        Amazon de ontem (2000), não o ML, senão o KPI mede a troca de mix."""
        assert app._dv_floor_prev("Coletas", {"Amazon"}, _ontem(window)) == (2000.0, None)

    def test_mesma_fonte_so_em_outros_marketplaces(self, window):
        prev, nota = app._dv_floor_prev("Coletas", {"Shopee"}, _ontem(window))
        assert prev is None
        assert nota == "ontem Coletas só em outros marketplaces — sem delta"

    def test_sem_ontem(self, window):
        vazio = window[window["data"] == date(2026, 9, 1)]
        assert app._dv_floor_prev("PriceTrack", {"Mercado Livre"}, vazio) == (None, None)
        assert app._dv_floor_prev(None, {"Mercado Livre"}, _ontem(window)) == (None, None)


class TestRender:
    def test_html_marca_outra_fonte_e_nota_do_piso(self, pivot, window):
        delta, outra = app._dv_delta_vs_ontem(pivot, window)
        p = pivot.assign(**{"Gap 1º→2º": NAN,
                            "Tendência 7d": [[NAN] * 7 for _ in range(len(pivot))]})
        prices = p[app._DAILY_VISION_PLATFORMS].apply(pd.to_numeric, errors="coerce")
        nota = "ontem só em Coletas — outra fonte, sem delta (hoje: PriceTrack)"
        html = app._dv_build_html(app._DVContext(
            pivot=p, base_cols=["Data", "Source", "Turno", "Marca"],
            price_matrix=prices, delta_matrix=delta, champion_idx=1,
            champion_brand="Midea", champion_mp="Amazon", current_min=2100.0,
            delta_v=None, delta_str=None, pct_str=None, sel_grupo="Marca",
            xsrc_matrix=outra, floor_note=nota,
        ))
        assert html.count("⇄ outra fonte</span>") == 1
        assert "▲ R$ 500" not in html                 # a seta falsa do incidente
        assert "▲ R$ 100" in html and "▼ R$ 100" in html
        assert nota in html
        assert "⇄ ontem só na outra fonte" in html   # legenda
