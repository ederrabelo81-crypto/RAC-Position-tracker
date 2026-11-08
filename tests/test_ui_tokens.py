"""Tokens visuais únicos (utils/ui_tokens.py) — uma marca, uma cor."""
import math

from utils import ui_tokens as ui


def test_midea_tem_a_cor_reservada_e_o_acento_e_outro() -> None:
    assert ui.brand_color("Midea") == ui.MIDEA_BLUE
    assert ui.brand_color("Midea Carrier") == ui.MIDEA_BLUE
    # A interface não pode competir com a série da Midea.
    assert ui.ACCENT.lower() != ui.MIDEA_BLUE.lower()


def test_marcas_principais_sao_distintas() -> None:
    principais = ["Midea", "Elgin", "LG", "Gree", "Samsung", "TCL", "Philco", "Agratto"]
    cores = [ui.brand_color(m) for m in principais]
    assert len(set(cores)) == len(cores), dict(zip(principais, cores))


def test_marca_desconhecida_ou_vazia_cai_no_neutro() -> None:
    assert ui.brand_color(None) == ui.TEXT_MUTED
    assert ui.brand_color(float("nan")) == ui.TEXT_MUTED
    assert ui.brand_color("MarcaQueNaoExiste") == ui.TEXT_MUTED


def test_mapa_nao_repete_cor_entre_marcas_fixas_e_rotacao() -> None:
    marcas = ["Midea", "Philco", "Xpto", "Ypsilon", "Zeta", None, math.nan]
    cmap = ui.brand_color_map(marcas)
    assert set(cmap) == {"Midea", "Philco", "Xpto", "Ypsilon", "Zeta"}
    assert cmap["Midea"] == ui.MIDEA_BLUE
    assert len(set(cmap.values())) == len(cmap)


def test_plataforma_tem_cor_estavel_independente_da_ordem() -> None:
    a = ui.platform_color_map(["Amazon", "Mercado Livre", "Magalu"])
    b = ui.platform_color_map(["Magalu", "Mercado Livre", "Amazon"])
    assert a == b
    assert a["Mercado Livre"] == ui.PLATFORM_COLORS["Mercado Livre"]


def test_foco_pinta_o_resto_de_cinza() -> None:
    cmap = ui.focus_color_map(["Midea", "Philco", "LG", "Gree"], focus=["Midea", "Philco"])
    assert cmap["Midea"] == ui.MIDEA_BLUE
    assert cmap["Philco"] == ui.BRAND_COLORS["Philco"]
    assert cmap["LG"] == ui.NEUTRAL and cmap["Gree"] == ui.NEUTRAL


def test_app_usa_a_mesma_paleta_nos_graficos_e_nos_chips() -> None:
    import app

    for marca in ("Midea", "Philco", "LG", "Elgin", "Samsung"):
        assert app._brand_color(marca) == app._brand_color_map([marca])[marca]
