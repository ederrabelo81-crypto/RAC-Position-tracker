"""Radar meteorológico (utils/clima.py) — leitura, normal, índices, correlação."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from utils.clima import (
    BRASIL,
    CIDADES,
    MIN_ANOS_NORMAL,
    OBSERVADO,
    PESO_REGIAO,
    PREVISAO,
    Cidade,
    Correlacao,
    com_anomalia,
    consolidar,
    correlacao_defasada,
    correlacao_mensal,
    indice_regional,
    ler_veredito,
    parse_open_meteo,
    proxy_vendas_diario,
)

SP = Cidade("São Paulo", "SP", "Sudeste", -23.55, -46.63)
POA = Cidade("Porto Alegre", "RS", "Sul", -30.03, -51.23)

# Formato real da resposta diária do Open-Meteo (archive e forecast).
_RESPOSTA = {
    "latitude": -23.5, "longitude": -46.625, "timezone": "America/Sao_Paulo",
    "daily_units": {"time": "iso8601", "temperature_2m_max": "°C"},
    "daily": {
        "time": ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"],
        "temperature_2m_max": [24.1, 26.3, None, 31.0],
        "temperature_2m_min": [15.9, 16.1, 17.0, 19.0],
        "apparent_temperature_max": [24.8, 27.5, None, 33.2],
        "precipitation_sum": [12.4, 0.0, 3.1, 0.0],
    },
}


def _obs(cidade, inicio, dias, tmax):
    """Série observada sintética; ``tmax`` é constante ou função do dia."""
    linhas = []
    for i in range(dias):
        d = inicio + timedelta(days=i)
        t = tmax(d) if callable(tmax) else tmax
        linhas.append({"data": d, "cidade": cidade.nome, "uf": cidade.uf,
                       "regiao": cidade.regiao, "tipo": OBSERVADO, "tmax": t,
                       "tmin": t - 10, "tmed": t - 5, "sensacao_max": t,
                       "precip_mm": 0.0, "fonte": "archive"})
    return pd.DataFrame(linhas)


class TestParse:
    def test_converte_e_separa_observado_de_previsao(self):
        df = parse_open_meteo(_RESPOSTA, SP, hoje=date(2026, 10, 29), fonte="forecast")
        assert list(df["data"]) == [date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 30)]
        assert list(df["tipo"]) == [OBSERVADO, OBSERVADO, PREVISAO]
        assert df.iloc[0]["tmed"] == 20.0
        assert set(df["regiao"]) == {"Sudeste"}

    def test_resposta_vazia(self):
        assert parse_open_meteo({}, SP, date(2026, 10, 29), "archive").empty

    def test_archive_vence_forecast_no_mesmo_dia(self):
        f = parse_open_meteo(_RESPOSTA, SP, date(2026, 10, 29), "forecast")
        a = f.copy()
        a["fonte"], a["tmax"] = "archive", a["tmax"] + 1
        juntos = consolidar(a, f)
        assert len(juntos) == len(f)
        assert set(juntos["fonte"]) == {"archive"}

    def test_cidades_cobrem_as_cinco_regioes(self):
        assert {c.regiao for c in CIDADES} == set(PESO_REGIAO)
        assert sum(PESO_REGIAO.values()) == pytest.approx(1.0, abs=0.01)


class TestAnomalia:
    def test_normal_usa_so_anos_anteriores(self):
        # 4 anos a 25 °C, 2026 a 30 °C: anomalia de +5
        df = pd.concat([
            _obs(SP, date(ano, 10, 1), 31, 25.0) for ano in range(2022, 2026)
        ] + [_obs(SP, date(2026, 10, 1), 31, 30.0)])
        out = com_anomalia(df)
        linha = out[out["data"] == date(2026, 10, 15)].iloc[0]
        assert linha["normal_tmax"] == 25.0
        assert linha["anomalia_tmax"] == 5.0

    def test_sem_anos_suficientes_fica_vazio(self):
        df = pd.concat([_obs(SP, date(ano, 10, 1), 31, 25.0)
                        for ano in range(2026 - MIN_ANOS_NORMAL + 1, 2027)])
        out = com_anomalia(df)
        assert out[out["data"] == date(2026, 10, 15)]["normal_tmax"].isna().all()

    def test_previsao_usa_a_normal_mas_nao_ensina(self):
        hist = pd.concat([_obs(SP, date(ano, 11, 1), 30, 28.0) for ano in range(2022, 2026)])
        prev = _obs(SP, date(2026, 11, 1), 5, 35.0).assign(tipo=PREVISAO)
        out = com_anomalia(pd.concat([hist, prev]))
        p = out[out["tipo"] == PREVISAO].iloc[0]
        assert p["normal_tmax"] == 28.0 and p["anomalia_tmax"] == 7.0

    def test_janela_atravessa_a_virada_do_ano(self):
        df = pd.concat([_obs(SP, date(ano, 12, 20), 25, 30.0) for ano in range(2021, 2026)])
        out = com_anomalia(df)
        assert out[out["data"] == date(2026, 1, 3)]["normal_tmax"].iloc[0] == 30.0


class TestIndice:
    def test_brasil_pondera_por_regiao(self):
        d = date(2026, 10, 28)
        df = pd.concat([_obs(SP, d, 1, 30.0), _obs(POA, d, 1, 20.0)])
        idx = indice_regional(df).set_index("regiao")
        esperado = (30 * PESO_REGIAO["Sudeste"] + 20 * PESO_REGIAO["Sul"]) / (
            PESO_REGIAO["Sudeste"] + PESO_REGIAO["Sul"])
        assert idx.loc[BRASIL, "tmax"] == pytest.approx(esperado, abs=0.05)
        assert idx.loc["Sudeste", "dias_quentes"] == 1.0
        assert idx.loc["Sul", "dias_quentes"] == 0.0

    def test_observado_e_previsao_nao_se_misturam(self):
        d = date(2026, 10, 28)
        df = pd.concat([_obs(SP, d, 1, 30.0), _obs(SP, d, 1, 20.0).assign(tipo=PREVISAO)])
        idx = indice_regional(df)
        assert set(idx[idx["regiao"] == "Sudeste"]["tipo"]) == {OBSERVADO, PREVISAO}
        assert len(idx[(idx["regiao"] == "Sudeste") & (idx["tipo"] == OBSERVADO)]) == 1


class TestVendas:
    def test_proxy_de_avaliacoes(self):
        bs = pd.DataFrame([
            {"data": "2026-10-01", "plataforma": "amazon", "sku_plataforma": "A", "reviews": 100},
            {"data": "2026-10-03", "plataforma": "amazon", "sku_plataforma": "A", "reviews": 120},
            {"data": "2026-10-03", "plataforma": "shopee", "sku_plataforma": "B", "reviews": 50},
            {"data": "2026-10-04", "plataforma": "shopee", "sku_plataforma": "B", "reviews": 45},
        ])
        p = proxy_vendas_diario(bs).set_index("data")
        assert p.loc[date(2026, 10, 3), "avaliacoes_dia"] == 10.0   # 20 em 2 dias
        assert p.loc[date(2026, 10, 4), "avaliacoes_dia"] == 0.0    # contador encolheu
        assert date(2026, 10, 1) not in p.index                     # 1ª leitura não tem delta

    def test_correlacao_defasada_acha_o_atraso(self):
        rng = np.random.default_rng(7)
        dias = pd.date_range("2026-01-01", periods=200, freq="D")
        clima = pd.Series(np.sin(np.arange(200) / 9) + rng.normal(0, 0.1, 200), index=dias)
        vendas = clima.shift(5) * 10 + 100  # venda reage 5 dias depois
        res = correlacao_defasada(clima, vendas, range(0, 11))
        melhor = max(res, key=lambda c: c.spearman)
        assert melhor.defasagem == 5
        assert melhor.forte

    def test_veredito_nao_afirma_com_pouco_dado(self):
        texto = ler_veredito([Correlacao(3, 20, 0.8)])
        assert "FRACA" in texto and "mais calor, mais venda" in texto
        assert "FRACA" not in ler_veredito([Correlacao(3, 120, 0.5)])

    def test_correlacao_mensal_com_serie_externa(self):
        meses = pd.date_range("2024-01-01", periods=24, freq="MS")
        linhas = []
        for i, m in enumerate(meses):
            linhas.append({"data": m.date(), "tipo": OBSERVADO, "regiao": BRASIL,
                           "tmax": 25 + 5 * np.sin(i / 2), "anomalia_tmax": np.nan})
        clima = pd.DataFrame(linhas)
        vendas = pd.DataFrame({"mes": meses.strftime("%Y-%m"),
                               "vendas": [1000 + 300 * np.sin(i / 2) for i in range(24)]})
        res = correlacao_mensal(clima, vendas)
        assert res is not None and res.pontos == 24 and res.spearman > 0.95


# --------------------------------------------------------------------------
# Página do dashboard
# --------------------------------------------------------------------------

def _pagina_clima_harness() -> None:
    from datetime import date, timedelta

    import numpy as np
    import pandas as pd

    import app
    from utils.clima import CIDADES, OBSERVADO, PREVISAO
    from utils.text import now_brt

    hoje = now_brt().date()
    linhas = []
    for c in CIDADES:
        for i in range(5 * 365):
            d = hoje - timedelta(days=5 * 365 - i)
            t = 27 + 5 * np.sin(i * 2 * np.pi / 365)
            linhas.append({"data": d, "cidade": c.nome, "uf": c.uf, "regiao": c.regiao,
                           "tipo": OBSERVADO, "tmax": t, "tmin": t - 9, "tmed": t - 4.5,
                           "sensacao_max": t, "precip_mm": 0.0, "fonte": "archive"})
        for i in range(16):
            linhas.append({"data": hoje + timedelta(days=i), "cidade": c.nome, "uf": c.uf,
                           "regiao": c.regiao, "tipo": PREVISAO, "tmax": 33.0, "tmin": 22.0,
                           "tmed": 27.5, "sensacao_max": 35.0, "precip_mm": 0.0,
                           "fonte": "forecast"})
    clima_df = pd.DataFrame(linhas)

    bs = pd.DataFrame([
        {"data": (hoje - timedelta(days=60 - i)).isoformat(), "plataforma": "shopee",
         "sku_plataforma": "S1", "reviews": 100 + i * 5, "referencia": "mais_vendidos"}
        for i in range(60)
    ])

    orig_clima, orig_bs = app.query_clima, app.query_bestsellers
    app.query_clima = lambda anos=11: (clima_df, "teste")  # type: ignore[assignment]
    app.query_bestsellers = lambda limit=0: (bs, "teste", "")  # type: ignore[assignment]
    try:
        app.page_clima()
    finally:
        app.query_clima, app.query_bestsellers = orig_clima, orig_bs


def test_pagina_clima_renderiza():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_pagina_clima_harness)
    at.run(timeout=120)
    assert not at.exception, [str(e.value) for e in at.exception]
    rotulos = [m.label for m in at.metric]
    assert "Sudeste · próx. 7d" in rotulos
    textos = " ".join(str(m.value) for m in at.markdown)
    assert "Spearman" in textos or "Sem pontos" in textos
