"""Recorte do painel na URL + links dos alertas (utils/url_state.py)."""
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

from utils import url_state as us

HOJE = date(2026, 11, 8)
VALIDOS = {
    "canais": ["Marketplaces", "Lojas próprias (dealers)"],
    "plataformas": ["Magalu", "Amazon", "Mercado Livre"],
    "marcas": ["Midea", "Philco"],
    "fontes": ["coletas", "pricetrack"],
}


def test_periodo_padrao_nao_vai_para_a_url() -> None:
    ini, fim = us.periodo_padrao(HOJE)
    qp = us.encode_filters({"inicio": ini, "fim": fim}, HOJE)
    assert qp == {}, "link sem datas é rolante — abre sempre nos últimos 7 dias"


def test_ida_e_volta_preserva_o_recorte() -> None:
    f = {"inicio": date(2026, 11, 1), "fim": date(2026, 11, 5),
         "canais": ["Marketplaces"], "plataformas": ["Magalu", "Amazon"],
         "marcas": ["Midea"], "fontes": ["coletas"], "comparar": True}
    qp = us.encode_filters(f, HOJE, fontes_padrao=["coletas", "pricetrack"])
    assert qp["plat"] == "Magalu,Amazon" and qp["comparar"] == "1"
    assert us.decode_filters(qp, VALIDOS, HOJE) == f


def test_fontes_padrao_ficam_fora() -> None:
    qp = us.encode_filters({"fontes": ["pricetrack", "coletas"]}, HOJE,
                           fontes_padrao=["coletas", "pricetrack"])
    assert "fonte" not in qp


def test_valor_invalido_some_sem_derrubar() -> None:
    qp = {"plat": "Magalu,Plataforma Renomeada", "marca": "Xpto", "canal": "Lixo",
          "de": "2026-13-40", "ate": "2026-11-05"}
    out = us.decode_filters(qp, VALIDOS, HOJE)
    assert out == {"plataformas": ["Magalu"]}


def test_datas_invertidas_e_futuras() -> None:
    out = us.decode_filters({"de": "2026-11-20", "ate": "2026-11-01"}, VALIDOS, HOJE)
    assert out["inicio"] == date(2026, 11, 1) and out["fim"] == HOJE
    assert us.decode_filters({"de": "2026-12-01", "ate": "2026-12-05"}, VALIDOS, HOJE) == {}


def test_lista_aceita_lista_ou_texto() -> None:
    out = us.decode_filters({"plat": ["Magalu,Amazon", "Amazon"]}, VALIDOS, HOJE)
    assert out["plataformas"] == ["Magalu", "Amazon"]


def test_link_so_existe_com_a_variavel(monkeypatch) -> None:
    monkeypatch.delenv("RAC_DASHBOARD_URL", raising=False)
    assert us.dashboard_link("buy-box") is None
    assert us.linha_telegram("buy-box") == ""
    monkeypatch.setenv("RAC_DASHBOARD_URL", "https://rac.streamlit.app/")
    url = us.dashboard_link("saude-do-dado", {"inicio": HOJE, "fim": HOJE,
                                              "plataformas": ["Mercado Livre"]}, hoje=HOJE)
    partes = urlparse(url)
    assert partes.path == "/saude-do-dado"
    assert parse_qs(partes.query) == {"de": ["2026-11-08"], "ate": ["2026-11-08"],
                                      "plat": ["Mercado Livre"]}
    assert us.dashboard_link("", hoje=HOJE) == "https://rac.streamlit.app/"


def test_linha_telegram_escapa_html(monkeypatch) -> None:
    monkeypatch.setenv("RAC_DASHBOARD_URL", "https://x.app")
    linha = us.linha_telegram("buy-box", {"marcas": ["Midea"], "plataformas": ["A&B"]},
                              rotulo="<abrir>", hoje=HOJE)
    assert linha.startswith('🔗 <a href="https://x.app/buy-box?')
    assert "&amp;" in linha and "&lt;abrir&gt;" in linha


def test_paginas_dos_alertas_existem_no_painel() -> None:
    import app

    slugs = {app._slug(p) for p in app.PAGES}
    for usada in ("saude-do-dado", "mais-vendidos"):
        assert usada in slugs, f"alerta aponta para /{usada}, que não existe no menu"


def test_recortes_padrao_usam_canais_validos() -> None:
    import app

    for nome, f in us.recortes_padrao(HOJE).items():
        assert set(f["canais"]) <= set(app._CANAIS), nome
        assert f["inicio"] <= f["fim"] == HOJE
