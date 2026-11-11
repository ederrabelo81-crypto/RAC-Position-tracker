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


# --- URL de busca quebrada (10/11/2026) -------------------------------------
from typing import Optional

from utils.leroy_ads import (
    fill_serp_template,
    is_not_found_page,
    is_search_results_url,
    serp_url_template,
)

# Trecho real do dump do PC coletor (busca por /busca?term=ar+condicionado).
ERRO_REAL = """<div class="not-found-notice">
      <h4 class="heading-4">
        Desculpe, não localizamos <br> a página pesquisada.
      </h4></div>"""


class TestUrlDeBusca:
    def test_pagina_de_erro_real(self):
        assert is_not_found_page(ERRO_REAL)
        assert is_not_found_page("<h4>Desculpe, não localizamos <br> a página</h4>")
        assert not is_not_found_page(SERP)
        assert not is_not_found_page(None)

    @pytest.mark.parametrize("url,molde", [
        ("https://www.leroymerlin.com.br/search?q=ar+condicionado&p=1",
         "https://www.leroymerlin.com.br/search?q={kw}&p=1"),
        ("https://www.leroymerlin.com.br/busca/ar-condicionado",
         "https://www.leroymerlin.com.br/busca/{kw}"),
        ("https://www.leroymerlin.com.br/s?term=ar%20condicionado",
         "https://www.leroymerlin.com.br/s?term={kw}"),
    ])
    def test_molde_aprendido(self, url, molde):
        assert serp_url_template(url, "ar condicionado") == molde

    def test_termo_fora_da_url_nao_vira_molde(self):
        assert serp_url_template("https://www.leroymerlin.com.br/busca", "ar condicionado") is None

    def test_preenche_molde(self):
        assert fill_serp_template("https://x/search?q={kw}", "ar split 12000") == \
            "https://x/search?q=ar+split+12000"
        assert fill_serp_template("https://x/busca/{kw}", "Ar Split 12000") == \
            "https://x/busca/ar-split-12000"


# Card real da prateleira de patrocinados (dump do PC coletor, 10/11/2026),
# enxuto: só atributos e textos que o parser usa.
CARD_REAL = """<h2>Produtos Patrocinados</h2><div itemscope itemtype="https://schema.org/ItemList">
<div><div data-testid="ProductThumb">
<a data-gtm-item-list="Produtos Patrocinados" data-gtm-partner-name="retail media"
 data-gtm-item-id="1572309369" data-gtm-item-position="1" data-gtm-item-price="2665.56"
 href="https://www.leroymerlin.com.br/ar-condicionado-split-inverter-r-32-samsung-windfree-ai-12-000-btus-frio-220v-monofasico_1572309369">
 <img alt="Foto de Ar Condicionado Split Inverter R-32 - Samsung - Windfree Ai -"/>
 <div data-testid="ProductThumb-stripe"><span>Oferta</span><span>-20%</span></div></a>
<a data-gtm-item-list="Produtos Patrocinados" data-gtm-item-price="2665.56"
 href="https://www.leroymerlin.com.br/ar-condicionado-split-inverter-r-32-samsung-windfree-ai-12-000-btus-frio-220v-monofasico_1572309369">
 <div><h3>Ar Condicionado Split Inverter R-32 - Samsung - Windfree Ai -</h3>
 <span>Cód. <span>1572309369</span></span><span>R$ 3.332,00</span><span>R$ 2.665,56</span></div></a>
</div></div></div>"""

HOME = '<link rel="canonical" href="https://www.leroymerlin.com.br/modular/home">' + CARD_REAL


class TestCardReal:
    def test_preco_praticado_e_nao_o_riscado(self):
        (item,) = extract_sponsored(CARD_REAL)
        assert item.product_id == "1572309369"
        assert item.price == pytest.approx(2665.56)   # não R$ 3.332,00 ("de")
        assert item.title == "Ar Condicionado Split Inverter R-32 - Samsung - Windfree Ai -"

    def test_metadado_do_card_basta_sem_cabecalho(self):
        sem_h2 = CARD_REAL.replace("<h2>Produtos Patrocinados</h2>", "")
        assert [i.product_id for i in extract_sponsored(sem_h2)] == ["1572309369"]


class TestUrlNova:
    def test_build_url_formato_das_sugestoes_do_site(self):
        assert LeroyMerlinScraper._build_url("ar condicionado") == (
            "https://www.leroymerlin.com.br/search?term=ar+condicionado"
            "&searchTerm=ar+condicionado&searchType=default"
        )

    @pytest.mark.parametrize("url,ok", [
        ("https://www.leroymerlin.com.br/search?term=ar+condicionado&searchType=default", True),
        ("https://www.leroymerlin.com.br/busca?term=ar", True),
        ("https://www.leroymerlin.com.br/modular/home", False),
        ("https://www.leroymerlin.com.br/", False),
        ("https://www.leroymerlin.com.br/search", False),
        ("https://www.leroymerlin.com.br/ar-condicionado_1572309369", False),
    ])
    def test_so_pagina_de_resultados(self, url, ok):
        assert is_search_results_url(url) is ok

    def test_molde_troca_todas_as_ocorrencias(self):
        url = ("https://www.leroymerlin.com.br/search?term=ar%20condicionado"
               "&searchTerm=ar%20condicionado&searchType=default")
        assert serp_url_template(url, "ar condicionado").count("{kw}") == 2


class _CampoFalso:
    def __init__(self, aba: "_AbaFalsa") -> None:
        self.aba, self.texto = aba, ""

    @property
    def first(self):
        return self

    def count(self) -> int:
        return 1

    def click(self, **_kw) -> None:
        pass

    def fill(self, texto: str, **_kw) -> None:
        self.texto = texto

    def press(self, _tecla: str) -> None:
        from urllib.parse import quote_plus
        self.aba.url = self.aba.destino_campo.replace("{kw}", quote_plus(self.texto))


class _AbaFalsa:
    """Aba de mentira: `quebrada` dá a página de erro; o campo leva a `destino_campo`."""

    def __init__(
        self,
        quebrada: Optional[str] = "/search?term=",
        destino_campo: str = "https://www.leroymerlin.com.br/search?q={kw}",
    ) -> None:
        self.url, self.visitas = "about:blank", []
        self.quebrada, self.destino_campo = quebrada, destino_campo

    def goto(self, url: str, **_kw) -> None:
        self.url = url
        self.visitas.append(url)

    def content(self) -> str:
        if self.quebrada and self.quebrada in self.url:
            return ERRO_REAL
        return HOME if "/modular/home" in self.url else SERP

    def locator(self, _sel: str) -> _CampoFalso:
        return _CampoFalso(self)

    def wait_for_url(self, *_a, **_kw) -> None:
        pass

    def wait_for_load_state(self, *_a, **_kw) -> None:
        pass

    def wait_for_timeout(self, *_a) -> None:
        pass


class TestAbrirBusca:
    def test_formato_novo_abre_direto(self, scraper):
        aba = _AbaFalsa(quebrada=None)
        assert scraper._abrir_busca(aba, "ar condicionado")
        assert aba.visitas == [LeroyMerlinScraper._build_url("ar condicionado")]
        assert scraper._serp_template is None

    def test_cai_no_campo_e_aprende_a_url(self, scraper):
        aba = _AbaFalsa()
        assert scraper._abrir_busca(aba, "ar condicionado")
        assert scraper._serp_template == "https://www.leroymerlin.com.br/search?q={kw}"
        # Próxima keyword vai direto pelo molde, sem passar pela URL quebrada.
        assert scraper._abrir_busca(aba, "ar split")
        assert aba.visitas[-1] == "https://www.leroymerlin.com.br/search?q=ar+split"

    def test_campo_que_leva_a_home_nao_vale(self, scraper):
        """Dump de 10/11/2026: a busca pelo campo terminou na home, e a
        prateleira de patrocinados DELA seria gravada como da keyword."""
        scraper._page = _AbaFalsa(destino_campo="https://www.leroymerlin.com.br/modular/home")
        assert scraper._patrocinados_serp("ar condicionado") == []
        assert scraper._serp_busca_quebrada

    def test_erro_tambem_no_campo_desliga_no_run(self, scraper):
        aba = _AbaFalsa()
        aba.content = lambda: ERRO_REAL  # type: ignore[method-assign]
        assert not scraper._abrir_busca(aba, "ar condicionado")
        assert scraper._serp_busca_quebrada
        assert not scraper._abrir_busca(aba, "outra")

    def test_patrocinados_da_busca_pela_url_aprendida(self, scraper):
        """Ponta a ponta: URL montada dá erro, o campo acha os resultados e a
        prateleira de patrocinados é lida (antes voltava vazio em silêncio)."""
        scraper._page = _AbaFalsa()
        itens = scraper._patrocinados_serp("ar condicionado")
        assert [i.product_id for i in itens] == ["91000001", "91000002"]



# --- Posição da prateleira na página (3º probe, 10/11/2026) ------------------
from utils.leroy_ads import organic_before_shelf


def _card_gtm(pid: int, lista: str, parceiro: str, preco: str = "1000") -> str:
    return (
        f'<div data-testid="ProductThumb"><a data-gtm-item-list="{lista}" '
        f'data-gtm-partner-name="{parceiro}" data-gtm-item-price="{preco}" '
        f'href="https://www.leroymerlin.com.br/ar-{pid}_{pid}"><h3>Ar Condicionado {pid}</h3></a></div>'
    )


# Como a busca real: grid orgânico (algolia) primeiro, prateleira no fim.
BUSCA_REAL = (
    "<title>Resultados da Busca por: ar condicionado</title><main><div>"
    + "".join(_card_gtm(9100000 + i, "entry-key-ar condicionado", "algolia") for i in range(36))
    + "</div><h2>Produtos Patrocinados</h2><div>"
    + _card_gtm(3165336429, "Produtos Patrocinados", "retail media", "2169")
    + _card_gtm(3884636121, "Produtos Patrocinados", "retail media", "3444")
    + "</div></main>"
)


class TestPosicaoDaPrateleira:
    def test_organicos_antes_da_prateleira(self):
        assert organic_before_shelf(BUSCA_REAL) == 36
        assert [i.product_id for i in extract_sponsored(BUSCA_REAL)] == ["3165336429", "3884636121"]

    def test_prateleira_no_topo_nao_desloca(self):
        topo = (
            "<h2>Produtos Patrocinados</h2>"
            + _card_gtm(1111111, "Produtos Patrocinados", "retail media")
            + _card_gtm(2222222, "entry-key-x", "algolia")
        )
        assert organic_before_shelf(topo) == 0

    def test_sem_anuncio_marcado_nao_mede(self):
        assert organic_before_shelf(_card_gtm(2222222, "entry-key-x", "algolia")) == 0
        assert organic_before_shelf(None) == 0

    def test_anuncio_no_fim_da_pagina_nao_disputa_o_top10(self, scraper):
        itens = scraper._patrocinados_serp("ar condicionado", BUSCA_REAL)
        novos = scraper._aplicar_patrocinados_serp(
            itens, [], "ar condicionado", {"Genérica": ["ar condicionado"]},
        )
        assert [r["Posição Patrocinada"] for r in novos] == [1, 2]
        assert [r["Posição Geral"] for r in novos] == [37, 38]
        assert novos[0]["Preço (R$)"] == pytest.approx(2169.0)


class TestProbePdp:
    """`leroy_seller_probe.py --patrocinados URL` (4º probe, 10/11/2026: o
    "dump do PDP" enviado era a página de BUSCA — o probe não regravava o
    arquivo quando o PDP falhava, e não conferia para onde a aba foi)."""

    PDP = "https://www.leroymerlin.com.br/ar-condicionado-lg-12000_1572750548"

    def _rodar(self, monkeypatch, tmp_path, url_final, html):
        import scripts.leroy_seller_probe as probe
        import scrapers.leroy_merlin as lm

        class _Aba:
            url = url_final

        class _Falso:
            def __init__(self, **_kw): ...
            def __enter__(self): return self
            def __exit__(self, *_a): return False
            def _pagina_pdp(self): return _Aba()
            def _ler_patrocinados(self, *_a, **_kw): return html
            def _html_da_aba(self, _p): return html
            def fechar_pdp_local(self): ...

        monkeypatch.setattr(lm, "LeroyMerlinScraper", _Falso)
        monkeypatch.chdir(tmp_path)
        velho = tmp_path / "logs" / "leroy_ads_probe_pdp.html"
        velho.parent.mkdir()
        velho.write_text("dump de outra execução", encoding="utf-8")
        return probe.cmd_patrocinados(self.PDP), velho

    def test_pdp_redirecionado_nao_lista_anuncio(self, monkeypatch, tmp_path, capsys):
        codigo, dump = self._rodar(
            monkeypatch, tmp_path, "https://www.leroymerlin.com.br/search?term=ar", BUSCA_REAL
        )
        assert codigo == 1
        assert "3165336429" not in capsys.readouterr().out
        assert dump.read_text(encoding="utf-8") == BUSCA_REAL  # evidência nova

    def test_pdp_certo_lista_anuncio(self, monkeypatch, tmp_path, capsys):
        codigo, _ = self._rodar(monkeypatch, tmp_path, self.PDP, CARD_REAL)
        assert codigo == 0
        assert "1572309369" in capsys.readouterr().out

    def test_falha_nao_deixa_dump_velho(self, monkeypatch, tmp_path):
        codigo, dump = self._rodar(monkeypatch, tmp_path, self.PDP, None)
        assert codigo == 1
        assert not dump.exists()


# --- Painel: a Leroy sumia do 📣 Share of Voice (11/11/2026) ----------------
class TestSovSharePorPlataforma:
    """Turno de 10/11/2026: 18 anúncios da busca gravados, 17 com Posição Geral
    37+ (prateleira no fim da página) — o top-10 da página não mostrava a
    Leroy. O quadro por plataforma conta todas as posições."""

    def test_leroy_aparece_com_anuncio_fora_do_top10(self):
        import pandas as pd

        import app

        df = pd.DataFrame({
            "plataforma": ["Leroy Merlin"] * 4 + ["Mercado Livre"] * 2,
            "marca": ["Midea", "Midea", "LG", "Gree", "Midea", "LG"],
            "posicao_geral": [37, 38, 39, 5, 2, 3],
            "_spon": [True, True, True, False, True, True],
        })
        share = app._sov_share_plataforma_marca(df)
        leroy = share[share["plataforma"] == "Leroy Merlin"].set_index("marca")
        assert leroy.loc["Midea", "Anúncios"] == 2
        assert leroy.loc["Midea", "Share na plataforma (%)"] == pytest.approx(66.7)
        assert "Gree" not in leroy.index        # orgânico não é anúncio
        ml = share[share["plataforma"] == "Mercado Livre"]
        assert ml["Share na plataforma (%)"].sum() == pytest.approx(100.0)

    def test_sem_anuncio_devolve_vazio(self):
        import pandas as pd

        import app

        df = pd.DataFrame({"plataforma": ["Leroy Merlin"], "marca": ["Midea"], "_spon": [False]})
        assert app._sov_share_plataforma_marca(df).empty
        assert app._sov_share_plataforma_marca(pd.DataFrame()).empty


class TestEsperaCards:
    def test_leva_a_prateleira_a_tela_e_espera_os_cards(self, scraper):
        chamadas = []

        class _Aba:
            def evaluate(self, js, arg=None):
                chamadas.append(("evaluate", arg))

            def wait_for_function(self, js, **kw):
                chamadas.append(("wait", kw.get("timeout")))

            def wait_for_timeout(self, ms):
                chamadas.append(("sleep", ms))

        scraper._esperar_cards_patrocinados(_Aba())
        assert chamadas[0][0] == "evaluate"
        import scrapers.leroy_merlin as lm
        assert chamadas[1] == ("wait", lm._ADS_CARDS_TIMEOUT)

    def test_sem_cards_no_teto_nao_quebra(self, scraper):
        class _Aba:
            def evaluate(self, *_a):
                raise RuntimeError("aba fechada")

            def wait_for_function(self, *_a, **_kw):
                raise TimeoutError("sem cards")

            def wait_for_timeout(self, _ms):
                raise RuntimeError("aba fechada")

        scraper._esperar_cards_patrocinados(_Aba())  # não levanta


class TestSovShareDedup:
    def test_turno_gravado_duas_vezes_conta_uma(self):
        """Até 30/09/2026 cada turno do PC entrava 2× em `coletas` (run_id
        UUID4 + UUID5): o quadro de share não pode contar o anúncio dobrado."""
        import pandas as pd

        import app

        base = {"data": "2026-09-20", "turno": "Tarde", "plataforma": "Mercado Livre",
                "keyword": "ar", "posicao_organica": None, "_spon": True}
        df = pd.DataFrame([
            {**base, "marca": "Midea", "run_id": "a", "id": 1},
            {**base, "marca": "LG", "run_id": "a", "id": 2},
            {**base, "marca": "Midea", "run_id": "b", "id": 3},
            {**base, "marca": "LG", "run_id": "b", "id": 4},
        ])
        share = app._sov_share_plataforma_marca(df)
        assert share["Anúncios"].sum() == 2
