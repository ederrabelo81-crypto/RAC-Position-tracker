"""
Radar de marcas em ascensão (bestsellers/radar.py).

Os cenários são inspirados no backtest de 12/09/2026 contra o banco (Philco
subindo em Magalu e ML, Agratto saltando +13,5 p.p. no Magalu, Grupo Midea
subindo em três praças e saindo do top 10 do Magalu) — com números redondos,
não os valores exatos do banco.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from bestsellers.radar import (
    ATENCAO,
    CAINDO,
    SEM_DADO,
    ESTAVEL,
    GRUPO_MIDEA,
    MISTO,
    SUBINDO,
    JanelaRadar,
    radar_marcas,
    resumo_texto,
    share_por_plataforma,
)

ATE = date(2026, 9, 12)
JANELA = JanelaRadar(ate=ATE)


def _dias(inicio: date, fim: date):
    d = inicio
    while d <= fim:
        yield d
        d += timedelta(days=1)


def _lista(dia, plataforma, marcas, **extra):
    """Top 10 de um dia: `marcas` em ordem de rank (completa com 'Outra')."""
    marcas = list(marcas) + ["Outra"] * (10 - len(marcas))
    linhas = []
    for rank, marca in enumerate(marcas, 1):
        linha = {
            "data": dia.isoformat(), "plataforma": plataforma, "rank": rank,
            "marca": "Midea" if marca == GRUPO_MIDEA else marca,
            "grupo_midea": marca == GRUPO_MIDEA, "tipo": "SPLIT_HW",
            "referencia": "mais_vendidos", "sku_plataforma": f"{plataforma}-{marca}-{rank}",
        }
        linha.update(extra.get(marca, {}))
        linhas.append(linha)
    return linhas


def _serie(plataforma, base, recente, **extra):
    """Mesmo top 10 todo dia na base e outro todo dia na janela recente."""
    linhas = []
    for dia in _dias(JANELA.inicio_base, ATE):
        marcas = recente if dia >= JANELA.inicio_recente else base
        linhas += _lista(dia, plataforma, marcas, **extra)
    return linhas


def _df(*series):
    return pd.DataFrame([linha for serie in series for linha in serie])


def _status(radar, marca):
    return radar.set_index("marca").loc[marca, "status"]


class TestShare:
    def test_share_por_plataforma_e_delta(self):
        df = _df(_serie("magalu", ["Agratto"], ["Agratto", "Agratto", "Agratto"]))
        sh = share_por_plataforma(df, JANELA).set_index("marca_radar")
        assert sh.loc["Agratto", "share_base"] == 10.0
        assert sh.loc["Agratto", "share_recente"] == 30.0
        assert sh.loc["Agratto", "delta_pp"] == 20.0
        assert bool(sh.loc["Agratto", "comparavel"])

    def test_ausencia_do_topo_conta_zero(self):
        df = _df(_serie("magalu", [GRUPO_MIDEA, GRUPO_MIDEA], ["Agratto"]))
        sh = share_por_plataforma(df, JANELA).set_index("marca_radar")
        assert sh.loc[GRUPO_MIDEA, "share_recente"] == 0.0
        assert sh.loc[GRUPO_MIDEA, "delta_pp"] == -20.0

    def test_plataforma_com_poucas_leituras_nao_compara(self):
        linhas = _serie("magalu", ["Agratto"], ["Agratto", "Agratto"])
        # só 2 dias na janela recente
        linhas = [l for l in linhas if l["data"] < JANELA.inicio_recente.isoformat()
                  or l["data"] >= (ATE - timedelta(days=1)).isoformat()]
        sh = share_por_plataforma(_df(linhas), JANELA).set_index("marca_radar")
        assert not bool(sh.loc["Agratto", "comparavel"])
        assert pd.isna(sh.loc["Agratto", "delta_pp"])

    def test_relevancia_e_fora_do_hiwall_ficam_fora(self):
        linhas = _serie("dufrio", ["Agratto"], ["Agratto"] * 5)
        for l in linhas:
            l["referencia"] = "relevancia"
        portatil = _serie("magalu", ["Philco"], ["Philco"] * 5)
        for l in portatil:
            l["tipo"] = "PORTATIL"
        assert share_por_plataforma(_df(linhas, portatil), JANELA).empty


class TestRadar:
    def test_backtest_12_09(self):
        df = _df(
            _serie("magazineluiza",
                   [GRUPO_MIDEA, GRUPO_MIDEA, GRUPO_MIDEA, "Philco", "Philco"],
                   ["Agratto", "Philco", "Philco", "Philco", "Agratto"]),
            _serie("mercadolivre", ["Philco"], ["Philco", "Philco"]),
            _serie("webcontinental", ["LG"], [GRUPO_MIDEA, GRUPO_MIDEA, "LG"]),
            _serie("fastshop", [GRUPO_MIDEA, "LG"], [GRUPO_MIDEA, GRUPO_MIDEA, GRUPO_MIDEA, "LG"]),
            _serie("belmicro", [GRUPO_MIDEA, GRUPO_MIDEA], []),
        )
        radar = radar_marcas(df, JANELA)

        assert _status(radar, "Philco") == SUBINDO
        assert _status(radar, "Agratto") == ATENCAO        # 1 praça, salto ≥ 10 p.p.
        assert _status(radar, GRUPO_MIDEA) == MISTO        # sobe em 2, cai em 2
        philco = radar.set_index("marca").loc["Philco"]
        assert philco["n_subindo"] == 2
        assert "Magalu +" in philco["sinais"]  # nome de exibição, não a chave
        assert radar.iloc[0]["marca"] == "Philco"          # sinal mais forte primeiro

    def test_caindo(self):
        df = _df(
            _serie("a", ["LG", "LG", "LG"], []),
            _serie("b", ["LG", "LG"], []),
        )
        assert _status(radar_marcas(df, JANELA), "LG") == CAINDO

    def test_variacao_pequena_e_estavel(self):
        df = _df(_serie("a", ["LG", "LG", "LG"], ["LG", "LG", "LG"]))
        assert _status(radar_marcas(df, JANELA), "LG") == ESTAVEL

    def test_uma_praca_com_tracao_de_avaliacoes_vira_atencao(self):
        linhas = []
        reviews, j = 1000, 0
        for dia in _dias(JANELA.inicio_base, ATE):
            recente = dia >= JANELA.inicio_recente
            # Philco ganha +5,7 p.p. (abaixo do salto de 10) e as avaliações
            # do anúncio líder aceleram 10× — só a tração justifica o alerta
            marcas = ["Philco", "Philco"] if recente and j < 4 else ["Philco"]
            j += recente
            reviews += 50 if recente else 5
            linhas += _lista(dia, "shopee", marcas, Philco={"reviews": reviews})
        df = pd.DataFrame(linhas)
        # O contador é só do anúncio líder; o 2º anúncio Philco não tem avaliações.
        df.loc[(df["marca"] == "Philco") & (df["rank"] == 1), "sku_plataforma"] = "P1"
        df.loc[(df["marca"] == "Philco") & (df["rank"] == 2), "reviews"] = None
        radar = radar_marcas(df, JANELA).set_index("marca")
        assert radar.loc["Philco", "tracao_avaliacoes"] == pytest.approx(10.0)
        assert radar.loc["Philco", "status"] == ATENCAO

    def test_preco_compara_os_mesmos_anuncios(self):
        linhas = []
        for dia in _dias(JANELA.inicio_base, ATE):
            preco = 1800.0 if dia >= JANELA.inicio_recente else 2000.0
            linhas += _lista(dia, "magalu", ["Agratto"], Agratto={"preco": preco})
        radar = radar_marcas(pd.DataFrame(linhas), JANELA).set_index("marca")
        assert radar.loc["Agratto", "preco_var_pct"] == pytest.approx(-10.0)

    def test_vendidos_so_com_base_mensal(self):
        linhas = []
        for dia in _dias(JANELA.inicio_base, ATE):
            recente = dia >= JANELA.inicio_recente
            linhas += _lista(dia, "shopee", ["Philco"], Philco={
                "vendidos": 300 if recente else 100, "base_vendidos": "mes"})
            linhas += _lista(dia, "mercadolivre", ["Philco"], Philco={
                "vendidos": 5000, "base_vendidos": "acumulado"})
        radar = radar_marcas(pd.DataFrame(linhas), JANELA).set_index("marca")
        assert radar.loc["Philco", "vendidos_mes_base"] == 100
        assert radar.loc["Philco", "tracao_vendidos"] == 3.0

    def test_vazio(self):
        assert radar_marcas(pd.DataFrame(), JANELA).empty


class TestResumo:
    def test_midea_entra_mesmo_estavel(self):
        df = _df(
            _serie("a", ["Philco"], ["Philco", "Philco"]),
            _serie("b", ["Philco"], ["Philco", "Philco"]),
            _serie("c", [GRUPO_MIDEA], [GRUPO_MIDEA]),
        )
        radar = radar_marcas(df, JANELA)
        assert _status(radar, GRUPO_MIDEA) == ESTAVEL   # 10% → 10% na plataforma c
        texto = resumo_texto(radar, JANELA)
        assert "Philco — SUBINDO" in texto
        assert GRUPO_MIDEA in texto
        assert "06/09–12/09" in texto


class TestRevisaoCubic:
    def test_misto_vem_antes_de_subindo(self):
        """3 altas e 2 quedas não é SUBINDO."""
        df = _df(*[_serie(p, ["LG"], ["LG", "LG"]) for p in ("a", "b", "c")],
                 *[_serie(p, ["LG", "LG"], ["LG"]) for p in ("d", "e")])
        assert _status(radar_marcas(df, JANELA), "LG") == MISTO

    def test_grupo_midea_ausente_nao_vira_grupo(self):
        df = _df(_serie("a", ["Philco"], ["Philco", "Philco"]))
        df["grupo_midea"] = None
        assert GRUPO_MIDEA not in set(share_por_plataforma(df, JANELA)["marca_radar"])

    def test_sem_plataforma_comparavel_e_sem_dado(self):
        linhas = [l for l in _serie("a", ["LG"], ["LG"]) if l["data"] >= JANELA.inicio_recente.isoformat()]
        assert _status(radar_marcas(_df(linhas), JANELA), "LG") == SEM_DADO


def test_ordem_sem_somar_entre_plataformas():
    """Quem cai em mais plataformas vem primeiro; a ordem não soma deltas."""
    df = _df(
        _serie("a", ["LG", "LG"], []), _serie("b", ["LG", "LG"], []),
        _serie("c", ["LG", "LG"], []),
        _serie("d", ["TCL"] * 9, ["TCL"]), _serie("e", ["TCL"] * 9, ["TCL"]),
    )
    radar = radar_marcas(df, JANELA)
    caindo = radar[radar["status"] == CAINDO]["marca"].tolist()
    assert caindo == ["LG", "TCL"]                  # 3 praças antes de 2 (mesmo com queda menor)
    assert "soma_delta_pp" not in radar.columns
    assert radar.set_index("marca").loc["TCL", "maior_queda_pp"] == -80.0
