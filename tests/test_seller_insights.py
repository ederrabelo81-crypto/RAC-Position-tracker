"""Contas puras do Track Position Seller (seller_app/seller_insights.py)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seller_app import seller_insights as ins  # noqa: E402


def _share(linhas):
    return pd.DataFrame(linhas, columns=["data", "plataforma", "produtos_detidos",
                                         "produtos_universo", "share_buybox_pct"])


def test_cores_espelham_os_tokens_do_painel_interno() -> None:
    from utils.ui_tokens import PLATFORM_COLORS

    assert ins.PLATFORM_COLORS == PLATFORM_COLORS, (
        "seller_app é publicado sozinho e copia as cores — mantenha as duas iguais")


def test_cores_na_ordem_das_colunas_e_dealer_na_rotacao() -> None:
    cores = ins.cores_plataformas(["Magalu", "Dufrio", "Amazon"])
    assert cores[0] == ins.PLATFORM_COLORS["Magalu"]
    assert cores[2] == ins.PLATFORM_COLORS["Amazon"]
    assert cores[1] not in ins.PLATFORM_COLORS.values()


def test_share_soma_sobre_soma_cada_plataforma_no_seu_ultimo_dia() -> None:
    share = _share([
        ("2026-11-01", "Magalu", 10, 100, 10.0),
        ("2026-11-08", "Magalu", 20, 100, 20.0),
        ("2026-10-31", "Amazon", 5, 50, 10.0),
        ("2026-11-07", "Amazon", 10, 50, 20.0),   # Amazon atrasada 1 dia
    ])
    f = ins.share_atual_e_anterior(share, dias=7)
    assert f["atual"].detidos == 30 and f["atual"].universo == 150
    assert round(f["atual"].pct, 1) == 20.0
    assert round(f["anterior"].pct, 1) == 10.0          # 15/150
    assert f["atual_comp"].plataformas == 2


def test_delta_ignora_plataforma_sem_referencia() -> None:
    share = _share([
        ("2026-11-01", "Magalu", 10, 100, 10.0),
        ("2026-11-08", "Magalu", 20, 100, 20.0),
        ("2026-11-08", "Shopee", 0, 900, 0.0),   # entrou agora: não pode puxar o delta
    ])
    f = ins.share_atual_e_anterior(share, dias=7)
    assert round(f["atual_comp"].pct, 1) == 20.0
    assert round(f["anterior"].pct, 1) == 10.0
    assert f["atual"].universo == 1000


def test_share_sem_historico_suficiente_nao_tem_delta() -> None:
    f = ins.share_atual_e_anterior(_share([("2026-11-08", "Magalu", 2, 10, 20.0)]))
    assert f["anterior"] is None and f["atual"].pct == 20.0
    assert ins.share_atual_e_anterior(_share([]))["atual"].pct is None


def test_ranking_relativo_posicao_total_e_gap() -> None:
    mercado = pd.DataFrame([
        dict(data="2026-11-08", plataforma="Magalu", seller_canonical="A", share_buybox_pct=30.0),
        dict(data="2026-11-08", plataforma="Magalu", seller_canonical="Eu", share_buybox_pct=12.0),
        dict(data="2026-11-08", plataforma="Magalu", seller_canonical="C", share_buybox_pct=5.0),
        dict(data="2026-11-07", plataforma="Magalu", seller_canonical="Eu", share_buybox_pct=90.0),
        dict(data="2026-11-08", plataforma="Amazon", seller_canonical="Eu", share_buybox_pct=40.0),
        dict(data="2026-11-08", plataforma="Amazon", seller_canonical="B", share_buybox_pct=10.0),
    ])
    r = ins.ranking_relativo(mercado, "Eu")
    assert list(r["plataforma"]) == ["Amazon", "Magalu"]        # melhor posição primeiro
    amazon, magalu = r.iloc[0], r.iloc[1]
    assert (amazon["posicao"], amazon["total"], amazon["gap_lider_pp"]) == (1, 2, 0.0)
    assert (magalu["posicao"], magalu["total"], magalu["lider"]) == (2, 3, "A")
    assert magalu["gap_lider_pp"] == 18.0
    assert ins.ranking_relativo(mercado, "Ninguém").empty


def _fato(linhas):
    cols = ["data", "turno", "plataforma", "marketplace_product_id", "produto",
            "preco", "detentor_buybox"]
    return pd.DataFrame(linhas, columns=cols)


def _perd(linhas):
    cols = ["data", "turno", "plataforma", "seller_canonical", "marketplace_product_id",
            "produto", "marca", "preco"]
    return pd.DataFrame(linhas, columns=cols)


def test_onde_agir_preco_dele_seu_ultimo_e_recuperacao() -> None:
    fato = _fato([
        ("2026-11-06", "Tarde", "Magalu", "111", "Split 12K", "2500.00", True),
        ("2026-11-07", "Abertura", "Magalu", "111", "Split 12K", "2459.00", True),
        ("2026-11-07", "Abertura", "Magalu", "222", "Split 9K", "1999.00", True),
        ("2026-11-08", "Tarde", "Magalu", "222", "Split 9K", "1950.00", True),   # recuperou
    ])
    perdidos = _perd([
        ("2026-11-07", "Tarde", "Magalu", "Frigelar", "111", "Split 12K", "Midea", "2389.00"),
        ("2026-11-07", "Fechamento", "Magalu", "Dufrio", "222", "Split 9K", "Midea", "1700.00"),
    ])
    a = ins.onde_agir(perdidos, fato)
    assert list(a["levou"]) == ["Frigelar", "Dufrio"]           # aberta antes de recuperada
    aberta = a.iloc[0]
    assert aberta["seu_preco"] == 2459.0 and aberta["preco_dele"] == 2389.0
    assert round(aberta["diferenca_pct"], 1) == -2.8
    assert not aberta["recuperado"] and bool(a.iloc[1]["recuperado"])


def test_onde_agir_turno_ordena_pelo_horario_nao_pelo_alfabeto() -> None:
    # 'Fechamento' < 'Tarde' no alfabeto; o preço anterior à perda da Tarde
    # é o da Abertura, nunca o do Fechamento do mesmo dia (6h no futuro).
    fato = _fato([
        ("2026-11-08", "Abertura", "ML", "9", "X", "100", True),
        ("2026-11-08", "Fechamento", "ML", "9", "X", "999", True),
    ])
    perdidos = _perd([("2026-11-08", "Tarde", "ML", "B", "9", "X", "M", "90")])
    a = ins.onde_agir(perdidos, fato)
    assert a.iloc[0]["seu_preco"] == 100.0
    assert bool(a.iloc[0]["recuperado"])


def test_onde_agir_sem_seu_preco_nao_inventa_diferenca() -> None:
    perdidos = _perd([("2026-11-08", "Tarde", "ML", "B", None, "Produto Y", "M", "90")])
    a = ins.onde_agir(perdidos, _fato([]))
    assert len(a) == 1 and pd.isna(a.iloc[0]["diferenca_pct"]) and a.iloc[0]["seu_preco"] is None
    assert ins.onde_agir(_perd([]), _fato([])).empty


def test_resumo_cobertura_aponta_turno_faltando() -> None:
    cob = pd.DataFrame([
        dict(data="2026-11-08", turno=t, plataforma="Magalu", observado=True)
        for t in ("Abertura", "Tarde", "Fechamento")
    ] + [
        dict(data="2026-11-08", turno="Abertura", plataforma="Amazon", observado=True),
        dict(data="2026-11-08", turno="Tarde", plataforma="Amazon", observado=False),
        dict(data="2026-11-07", turno="Tarde", plataforma="Shopee", observado=False),
    ])
    r = ins.resumo_cobertura(cob)
    assert r.nivel == "parcial" and r.dia == date(2026, 11, 8)
    assert (r.completas, r.plataformas) == (1, 2)
    assert r.faltando == ["Amazon (Tarde, Fechamento)"]
    assert ins.resumo_cobertura(pd.DataFrame()).nivel == "sem_registro"
