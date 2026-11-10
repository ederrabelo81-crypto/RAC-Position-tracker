"""Patrocinados da Leroy Merlin (busca + PDP) — `utils/leroy_ads.py` e o scraper.

HTML sintético: a nuvem é IP de datacenter e não abre a Leroy. A 1ª validação
contra a tela real é no PC coletor (ver docstring de `utils/leroy_ads.py`).
"""
from __future__ import annotations

import pytest

from scrapers.leroy_merlin import LeroyMerlinScraper
from utils.keyword_taxonomy import (
    CATEGORIA_PDP,
    TIPO_ANUNCIO_PDP,
    TIPO_GENERICA,
    classify_keyword,
    keyword_options,
    pdp_keyword,
)
from utils.leroy_ads import extract_sponsored, leroy_product_id
from utils.leroy_sellers import LeroySellerCache


def _card(pid: int, titulo: str, preco: str, chip: str = "") -> str:
    return (
        f'<li><div>{chip}<a href="/ar-condicionado-{pid}_{pid}">'
        f'<img alt="{titulo}"/></a><h3>{titulo}</h3><span>R$ {preco}</span></div></li>'
    )


SERP = f"""<html><body><main>
<section><h2>Produtos patrocinados</h2><ul>
{_card(91000001, "Ar Condicionado Split Midea 12000 BTUs", "2.199,90")}
{_card(91000002, "Ar Condicionado Split LG 9000 BTUs", "1.899,00")}
</ul></section>
<h2>Resultados para ar condicionado</h2><ul>
{_card(92000001, "Ar Condicionado Gree 12000", "1.000,00")}
{_card(91000002, "Ar Condicionado Split LG 9000 BTUs", "1.899,00")}
</ul></main></body></html>"""


class TestExtractSponsored:
    def test_prateleira_rotulada_na_ordem(self):
        itens = extract_sponsored(SERP)
        assert [i.product_id for i in itens] == ["91000001", "91000002"]
        assert [i.slot for i in itens] == [1, 2]
        assert itens[0].title == "Ar Condicionado Split Midea 12000 BTUs"
        assert itens[0].price == pytest.approx(2199.90)
        assert itens[0].url == "https://www.leroymerlin.com.br/ar-condicionado-91000001_91000001"

    def test_grid_organico_nao_vira_anuncio(self):
        """Cabeçalho irmão do grid: a prateleira para no próximo cabeçalho."""
        html = (
            f"<main><h2>Produtos patrocinados</h2><div>{_card(1111111, 'A', '1,00')}"
            f"{_card(2222222, 'B', '2,00')}</div><h2>Mais produtos</h2>"
            f"<div>{_card(3333333, 'C', '3,00')}</div></main>"
        )
        assert [i.product_id for i in extract_sponsored(html)] == ["1111111", "2222222"]

    def test_titulo_antes_do_link_nao_encerra_a_prateleira(self):
        card = lambda pid, t: f'<li><h3>{t}</h3><a href="/ar-{pid}_{pid}">ver</a></li>'
        html = (
            f"<main><h2>Produtos patrocinados</h2><ul>{card(1111111, 'A')}"
            f"{card(2222222, 'B')}</ul><h2>Resultados</h2><ul>{card(3333333, 'C')}"
            f"{card(4444444, 'D')}</ul></main>"
        )
        assert [i.product_id for i in extract_sponsored(html)] == ["1111111", "2222222"]

    def test_grid_sem_cabecalho_logo_apos_o_carrossel(self):
        """Sem cabeçalho entre o carrossel e o grid: para no 1º bloco."""
        html = (
            f"<main><h2>Produtos patrocinados</h2><ul>{_card(1111111, 'A', '1,00')}"
            f"{_card(2222222, 'B', '2,00')}</ul><ul>{_card(3333333, 'C', '3,00')}"
            f"{_card(4444444, 'D', '4,00')}</ul></main>"
        )
        assert [i.product_id for i in extract_sponsored(html)] == ["1111111", "2222222"]

    def test_cards_soltos_como_irmaos(self):
        html = (
            f"<ul><li><h2>Produtos patrocinados</h2></li>{_card(1111111, 'A', '1,00')}"
            f"{_card(2222222, 'B', '2,00')}</ul>"
        )
        assert [i.product_id for i in extract_sponsored(html)] == ["1111111", "2222222"]

    def test_chip_no_card(self):
        html = (
            f"<ul>{_card(1111111, 'A', '1,00')}"
            f"{_card(2222222, 'B', '2,00', chip='<span>Patrocinado</span>')}"
            f"{_card(3333333, 'C', '3,00')}</ul>"
        )
        assert [i.product_id for i in extract_sponsored(html)] == ["2222222"]

    def test_sem_rotulo_nao_ha_anuncio(self):
        html = f"<h2>Quem viu, viu também</h2><ul>{_card(1111111, 'A', '1,00')}</ul>"
        assert extract_sponsored(html) == []

    def test_texto_longo_nao_e_rotulo(self):
        html = (
            "<p>Saiba como funcionam os anúncios patrocinados no nosso site e "
            f"a política de privacidade</p>{_card(1111111, 'A', '1,00')}"
        )
        assert extract_sponsored(html) == []

    def test_script_nao_conta(self):
        html = f'<script>var t = "Patrocinado";</script><ul>{_card(1111111, "A", "1,00")}</ul>'
        assert extract_sponsored(html) == []

    @pytest.mark.parametrize("html", [None, ""])
    def test_vazio(self, html):
        assert extract_sponsored(html) == []

    def test_id_do_produto(self):
        assert leroy_product_id("https://www.leroymerlin.com.br/ar-tcl_92311464") == "92311464"
        assert leroy_product_id("/x_92311464?origem=ads") == "92311464"
        assert leroy_product_id("https://www.leroymerlin.com.br/ar-condicionado/") is None


@pytest.fixture
def scraper(tmp_path):
    s = LeroyMerlinScraper()
    s._seller_cache = LeroySellerCache(path=tmp_path / "cache.json")
    s._buybox_pdp_enabled = False
    s._preco_pdp_enabled = False
    return s


def _organico(s: LeroyMerlinScraper, pid: str, pos: int) -> dict:
    return s._build_record(
        keyword="ar condicionado",
        keyword_category_map={"Genérica": ["ar condicionado"]},
        title=f"Ar Condicionado Produto {pid}",
        position_general=pos,
        position_organic=pos,
        position_sponsored=None,
        url_produto=f"https://www.leroymerlin.com.br/ar-{pid}_{pid}",
        marketplace_product_id=pid,
    )


class TestPatrocinadosBusca:
    def test_anuncio_tambem_organico_marca_a_linha(self, scraper):
        """Mesmo produto no orgânico: marca a linha, não duplica (a chave única
        (keyword, produto) do banco descartaria a segunda)."""
        recs = [_organico(scraper, "92000001", 1), _organico(scraper, "91000002", 2)]
        novos = scraper._aplicar_patrocinados_serp(
            extract_sponsored(SERP), recs, "ar condicionado",
            {"Genérica": ["ar condicionado"]},
        )
        assert len(novos) == 1
        assert novos[0]["ID Produto Marketplace"] == "91000001"
        assert novos[0]["Patrocinado?"] == "Sim"
        assert novos[0]["Posição Patrocinada"] == 1
        assert novos[0]["Posição Geral"] == 1
        assert novos[0]["Posição Orgânica"] is None
        assert novos[0]["Marca Monitorada"] == "Midea"

        lg = recs[1]
        assert lg["Patrocinado?"] == "Sim"
        assert lg["Posição Patrocinada"] == 2
        assert lg["Posição Orgânica"] == 2       # o orgânico não muda
        assert recs[0]["Patrocinado?"] == "Não"

    def test_sem_browser_nao_quebra(self, scraper):
        assert scraper._page is None
        assert scraper._patrocinados_serp("ar condicionado") == []

    def test_anuncio_com_organico_vazio_vira_linha(self, scraper):
        novos = scraper._aplicar_patrocinados_serp(
            extract_sponsored(SERP), [], "ar condicionado",
            {"Genérica": ["ar condicionado"]},
        )
        assert [r["ID Produto Marketplace"] for r in novos] == ["91000001", "91000002"]

    def test_html_do_fallback_e_reaproveitado(self, scraper):
        itens = scraper._patrocinados_serp("ar condicionado", SERP)
        assert [i.product_id for i in itens] == ["91000001", "91000002"]


PDP = f"""<main><h1>Ar Condicionado Gree 12000</h1>
<section><h2>Produtos patrocinados</h2><ul>
{_card(91000001, "Ar Condicionado Split Midea 12000 BTUs", "2.199,90")}
{_card(92000001, "Ar Condicionado Gree 12000", "1.000,00")}
</ul></section></main>"""


class TestPatrocinadosPdp:
    URL = "https://www.leroymerlin.com.br/ar-condicionado-gree_92000001"

    def test_fora_da_busca_nao_registra(self, scraper):
        assert scraper._ads_pdp_lidos == {}

    def test_registra_e_agrega_por_keyword(self, scraper):
        scraper._ads_kw = "ar condicionado"
        scraper._registrar_ads_pdp(self.URL, PDP)
        scraper._registrar_ads_pdp(self.URL, PDP)  # mesmo PDP conta uma vez
        scraper._registrar_ads_pdp("https://www.leroymerlin.com.br/outro_93000001", PDP)

        # O próprio produto do PDP não é anúncio dele mesmo.
        agg = scraper._ads_pdp_agregados()
        assert agg["91000001"]["vezes"] == 2
        assert agg["92000001"]["vezes"] == 1

        recs = scraper._registros_ads_pdp("ar condicionado")
        assert [r["ID Produto Marketplace"] for r in recs] == ["91000001", "92000001"]
        midea = recs[0]
        assert midea["Keyword Buscada"] == "PDP · ar condicionado"
        assert midea["Categoria Keyword"] == CATEGORIA_PDP
        assert midea["Patrocinado?"] == "Sim"
        assert midea["Posição Orgânica"] is None
        assert midea["Tag Destaque"] == "Patrocinado (PDP) · em 2 de 2 PDPs"

    def test_leitura_vazia_pode_ser_refeita_com_rolagem(self, scraper):
        scraper._ads_kw = "ar condicionado"
        scraper._registrar_ads_pdp(self.URL, "<main>sem prateleira</main>")
        assert scraper._ads_pdp_agregados() == {}
        scraper._registrar_ads_pdp(self.URL, PDP)
        assert "91000001" in scraper._ads_pdp_agregados()
        assert scraper._seller_metrics["ads_pdp_paginas"] == 1

    def test_leitura_rolada_substitui_a_parcial(self, scraper):
        """Leitura rápida com só parte da prateleira não bloqueia a rolada."""
        scraper._ads_kw = "ar condicionado"
        parcial = (
            f"<section><h2>Produtos patrocinados</h2><ul>"
            f"{_card(92000001, 'Ar Gree', '1,00')}</ul></section>"
        )
        scraper._registrar_ads_pdp("https://www.leroymerlin.com.br/x_93000001", parcial)
        scraper._registrar_ads_pdp(
            "https://www.leroymerlin.com.br/x_93000001", PDP, rolado=True
        )
        agg = scraper._ads_pdp_agregados()
        assert set(agg) == {"91000001", "92000001"}
        assert agg["92000001"]["vezes"] == 1      # o PDP conta uma vez só

    def test_desligado_por_env(self, scraper, monkeypatch):
        monkeypatch.setattr("scrapers.leroy_merlin._ADS_PDP_ENABLED", False)
        scraper._ads_kw = "ar condicionado"
        scraper._registrar_ads_pdp(self.URL, PDP)
        assert scraper._ads_pdp_lidos == {}


class TestTaxonomiaPdp:
    def test_keyword_de_pdp_tem_tipo_proprio(self):
        assert classify_keyword(pdp_keyword("ar condicionado")) == TIPO_ANUNCIO_PDP
        assert classify_keyword("qualquer", CATEGORIA_PDP) == TIPO_ANUNCIO_PDP
        assert classify_keyword("ar condicionado") == TIPO_GENERICA

    def test_fora_do_filtro_de_busca(self):
        opcoes = keyword_options(["ar condicionado", pdp_keyword("ar condicionado")])
        assert pdp_keyword("ar condicionado") not in opcoes


class TestSovSeparaPdp:
    def test_anuncio_de_pdp_fica_fora_das_metricas_de_busca(self):
        """Linha de PDP não pode virar slot de busca no 📣 Share of Voice."""
        import pandas as pd

        import app

        df = pd.DataFrame({
            "keyword": ["ar condicionado", pdp_keyword("ar condicionado"), "outra"],
            "categoria": ["Genérica", CATEGORIA_PDP, CATEGORIA_PDP],
        })
        pdp, busca = app._split_anuncios_pdp(df)
        assert len(pdp) == 2
        assert busca["keyword"].tolist() == ["ar condicionado"]
