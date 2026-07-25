"""
tests/test_ml_parse.py — Extração de campos de insight da SERP do Mercado Livre.

Cobre a correção de Jun/2026: avaliação, qtd_avaliações, patrocinado e
Loja Oficial estavam 0% no banco desde Mar/2026 porque os seletores Poly
originais (.poly-component__reviews-*) não existiam no DOM real.

A fixture replica o sistema "Poly" do ML (cards de 2025+) com as variantes
que cada camada de detecção precisa cobrir. Validação contra o DOM vivo:
`python scripts/diagnose_ml.py` (requer IP residencial — ML bloqueia datacenter).

Rode: pytest tests/test_ml_parse.py
"""
import pytest
from bs4 import BeautifulSoup
from loguru import logger

from scrapers.mercado_livre import MLScraper, _SerpCursor, _BLOCK_SIGNALS_RE


def _item(html: str):
    """Parseia um <li> de card e retorna o Tag raiz."""
    return BeautifulSoup(html, "html.parser").select_one("li")


# ---------------------------------------------------------------------------
# Fixtures de cards
# ---------------------------------------------------------------------------

# Card orgânico Poly completo: rating/total dedicados, seller "Por X",
# highlight clássico.
CARD_ORGANIC_POLY = """
<li class="ui-search-layout__item">
  <div class="poly-card poly-card--grid-card">
    <div class="poly-card__content">
      <a class="poly-component__title" href="https://www.mercadolivre.com.br/ar-midea/p/MLB123">
        Ar Condicionado Split Midea 12000 Btus Frio
      </a>
      <span class="poly-component__seller">Por WebContinental</span>
      <div class="poly-component__reviews">
        <span class="andes-visually-hidden">Avaliação 4,8 de 5 (1.234 avaliações)</span>
        <span aria-hidden="true" class="poly-reviews__rating">4.8</span>
        <span aria-hidden="true" class="poly-reviews__total">(1.234)</span>
      </div>
      <div class="poly-component__highlight">MAIS VENDIDO</div>
      <div class="andes-money-amount">
        <span class="andes-money-amount__fraction">2.799</span>
        <span class="andes-money-amount__cents">90</span>
      </div>
    </div>
  </div>
</li>
"""

# Card patrocinado via chip Poly; reviews SÓ no texto acessível
# (sem .poly-reviews__rating) — exercita o fallback "de 5".
CARD_SPONSORED_CHIP = """
<li class="ui-search-layout__item">
  <div class="poly-card">
    <div class="poly-card__content">
      <a class="poly-component__title" href="https://www.mercadolivre.com.br/x/p/MLB9">Ar LG</a>
      <span class="poly-component__ads-promotions">Patrocinado</span>
      <div class="poly-component__reviews">
        <span class="andes-visually-hidden">Avaliação 4,7 de 5 (89 avaliações)</span>
      </div>
    </div>
  </div>
</li>
"""

# Card patrocinado SEM rótulo textual — só a âncora de click-tracking.
# Também é Loja Oficial via texto do seller.
CARD_SPONSORED_ADHREF_OFICIAL = """
<li class="ui-search-layout__item">
  <div class="poly-card">
    <div class="poly-card__content">
      <a class="poly-component__title"
         href="https://click1.mercadolivre.com.br/mclics/clicks/external/MLB/count?a=abc">
        Ar Condicionado Samsung WindFree
      </a>
      <span class="poly-component__seller">Loja oficial Samsung</span>
    </div>
  </div>
</li>
"""

# Card legado (pré-Poly): promoted-label antigo + reviews legadas.
CARD_LEGACY = """
<li class="ui-search-layout__item">
  <div class="ui-search-result__wrapper">
    <h2 class="ui-search-item__title">Ar Condicionado Elgin Eco 9000</h2>
    <span class="ui-search-item__promoted-label">Patrocinado</span>
    <span class="ui-search-reviews__rating-number">4,5</span>
    <span class="ui-search-reviews__amount">(321)</span>
    <span class="ui-search-official-store-label">Loja oficial Elgin</span>
  </div>
</li>
"""

# Card mínimo: nada além do título — todos os campos devem voltar None/3P.
CARD_BARE = """
<li class="ui-search-layout__item">
  <div class="poly-card">
    <a class="poly-component__title" href="https://www.mercadolivre.com.br/y/p/MLB7">Ar TCL</a>
  </div>
</li>
"""

# Card com selo de verificação (cockade) no seller — Loja Oficial via camada 4.
CARD_COCKADE = """
<li class="ui-search-layout__item">
  <div class="poly-card">
    <span class="poly-component__seller">Por Midea
      <svg class="poly-component__cockade" aria-label="Verificado"></svg>
    </span>
  </div>
</li>
"""


# ---------------------------------------------------------------------------
# Patrocinado
# ---------------------------------------------------------------------------

class TestIsSponsored:
    def test_organico_nao_marca(self):
        assert MLScraper._is_sponsored(_item(CARD_ORGANIC_POLY)) is False

    def test_chip_poly(self):
        assert MLScraper._is_sponsored(_item(CARD_SPONSORED_CHIP)) is True

    def test_ad_href_sem_rotulo_textual(self):
        assert MLScraper._is_sponsored(_item(CARD_SPONSORED_ADHREF_OFICIAL)) is True

    def test_label_legado(self):
        assert MLScraper._is_sponsored(_item(CARD_LEGACY)) is True

    def test_aria_label(self):
        html = """
        <li class="ui-search-layout__item">
          <div class="poly-card"><span aria-label="Patrocinado"></span></div>
        </li>"""
        assert MLScraper._is_sponsored(_item(html)) is True

    def test_is_advertising_query_param(self):
        html = """
        <li class="ui-search-layout__item">
          <a href="https://www.mercadolivre.com.br/p/MLB1?is_advertising=true&ad_domain=VQCATCORE">x</a>
        </li>"""
        assert MLScraper._is_sponsored(_item(html)) is True

    def test_card_vazio_nao_marca(self):
        assert MLScraper._is_sponsored(_item(CARD_BARE)) is False


# ---------------------------------------------------------------------------
# Avaliação + qtd avaliações
# ---------------------------------------------------------------------------

class TestExtractReviews:
    def test_seletores_poly_dedicados(self):
        rating, count = MLScraper._extract_reviews(_item(CARD_ORGANIC_POLY))
        assert rating == 4.8
        assert count == 1234

    def test_fallback_texto_acessivel(self):
        rating, count = MLScraper._extract_reviews(_item(CARD_SPONSORED_CHIP))
        assert rating == 4.7
        assert count == 89

    def test_seletores_legados(self):
        rating, count = MLScraper._extract_reviews(_item(CARD_LEGACY))
        assert rating == 4.5
        assert count == 321

    def test_sem_reviews(self):
        rating, count = MLScraper._extract_reviews(_item(CARD_BARE))
        assert rating is None
        assert count is None

    def test_texto_acessivel_nao_confunde_com_preco(self):
        # "de 5" ancora o parsing — texto de parcela não deve virar rating
        html = """
        <li class="ui-search-layout__item">
          <span class="andes-visually-hidden">12x de 233 reais</span>
        </li>"""
        rating, count = MLScraper._extract_reviews(_item(html))
        assert rating is None
        assert count is None

    def test_contagem_em_texto_separado_sem_de5(self):
        # contagem com a palavra "avaliações" vale mesmo sem o trecho "de 5"
        # no mesmo nó (rating e contagem em spans separados)
        html = """
        <li class="ui-search-layout__item">
          <span class="andes-visually-hidden">Avaliação 4,6 de 5</span>
          <span class="andes-visually-hidden">2.345 avaliações</span>
        </li>"""
        rating, count = MLScraper._extract_reviews(_item(html))
        assert rating == 4.6
        assert count == 2345

    def test_parenteses_sem_ancora_nao_vira_contagem(self):
        # "(2026)" num texto qualquer (ex: ano) não pode virar qtd_avaliacoes
        html = """
        <li class="ui-search-layout__item">
          <span class="andes-visually-hidden">Lançamento (2026) novo</span>
        </li>"""
        rating, count = MLScraper._extract_reviews(_item(html))
        assert rating is None
        assert count is None


# ---------------------------------------------------------------------------
# Loja Oficial vs 3P
# ---------------------------------------------------------------------------

class TestDetectTipoSeller:
    def test_3p_padrao(self):
        item = _item(CARD_ORGANIC_POLY)
        assert MLScraper._detect_tipo_seller(item, "WebContinental") == "3P"

    def test_texto_loja_oficial_no_seller(self):
        item = _item(CARD_SPONSORED_ADHREF_OFICIAL)
        assert MLScraper._detect_tipo_seller(item, "Loja oficial Samsung") == "Loja Oficial"

    def test_label_legado(self):
        assert MLScraper._detect_tipo_seller(_item(CARD_LEGACY), "Elgin") == "Loja Oficial"

    def test_cockade_poly(self):
        assert MLScraper._detect_tipo_seller(_item(CARD_COCKADE), "Midea") == "Loja Oficial"

    def test_card_vazio(self):
        assert MLScraper._detect_tipo_seller(_item(CARD_BARE), None) == "3P"


# ---------------------------------------------------------------------------
# Seleção de container — resiliência a mudança do wrapper da SERP
# ---------------------------------------------------------------------------

class TestSelectItems:
    def test_wrapper_classico(self):
        html = CARD_ORGANIC_POLY + CARD_BARE  # dois <li.ui-search-layout__item>
        soup = BeautifulSoup(f"<ol>{html}</ol>", "html.parser")
        items, sel = MLScraper._select_items(soup)
        assert len(items) == 2
        assert sel == "li.ui-search-layout__item"

    def test_fallback_poly_card_sem_li(self):
        # ML removeu o <li> wrapper — só restam os .poly-card. Deve casar no
        # fallback e ainda achar todos os cards (sem contagem duplicada).
        html = """
        <ol class="ui-search-layout">
          <div class="poly-card"><a class="poly-component__title" href="/p/MLB1">A</a></div>
          <div class="poly-card"><a class="poly-component__title" href="/p/MLB2">B</a></div>
        </ol>"""
        soup = BeautifulSoup(html, "html.parser")
        items, sel = MLScraper._select_items(soup)
        assert len(items) == 2
        assert sel == "div.poly-card"

    def test_prioridade_sem_duplicar(self):
        # Com o <li> presente E .poly-card aninhado, deve parar no <li> (1º
        # candidato) e contar 1 por produto — nunca somar os dois seletores.
        soup = BeautifulSoup(f"<ol>{CARD_ORGANIC_POLY}</ol>", "html.parser")
        items, sel = MLScraper._select_items(soup)
        assert len(items) == 1
        assert sel == "li.ui-search-layout__item"

    def test_nada_casa(self):
        soup = BeautifulSoup("<div class='xpto'>vazio</div>", "html.parser")
        items, sel = MLScraper._select_items(soup)
        assert items == []
        assert sel is None

    def test_parse_results_usa_fallback(self):
        # ponta-a-ponta: _parse_results deve extrair via .poly-card quando o
        # wrapper <li> não existe mais no HTML.
        html = """
        <ol class="ui-search-layout">
          <div class="poly-card">
            <a class="poly-component__title" href="https://www.mercadolivre.com.br/x/p/MLB1">
              Ar Condicionado Midea 9000
            </a>
            <div class="andes-money-amount">
              <span class="andes-money-amount__fraction">1.999</span>
              <span class="andes-money-amount__cents">00</span>
            </div>
          </div>
        </ol>"""
        scraper = MLScraper.__new__(MLScraper)  # sem __init__ (evita browser)
        scraper._last_screenshot_busca = None    # atributo normalmente setado no __init__
        records = scraper._parse_results(html, "ar condicionado", {}, page_offset=0)
        assert len(records) == 1

    def test_parse_results_html_vazio_sem_page(self, tmp_path, monkeypatch):
        # uso offline: parser chamado sem página Playwright associada. O ramo de
        # diagnóstico (0 itens) não pode estourar ao consultar _is_login_gate —
        # deve preservar o retorno normal de lista vazia.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        scraper = MLScraper.__new__(MLScraper)  # sem _page
        records = scraper._parse_results("<html><body>vazio</body></html>",
                                         "ar condicionado", {}, page_offset=0)
        assert records == []

    def test_is_login_gate_sem_page(self):
        # _is_login_gate deve degradar para False (não AttributeError) quando
        # não há página associada.
        assert MLScraper._is_login_gate(MLScraper.__new__(MLScraper)) is False


# ---------------------------------------------------------------------------
# Heurística de bloqueio — distingue bloqueio/desafio de mudança de DOM
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Regressões da coleta de 25/07/2026 (600 registros de ML analisados)
# ---------------------------------------------------------------------------

# Card 3P puro, no formato que o ML serve hoje: reviews só como par de nós de
# texto ("4.8" + "(1.234)"), sem classe dedicada; seller comum, sem selo.
CARD_POLY_ATUAL_3P = """
<li class="ui-search-layout__item">
  <div class="poly-card">
    <a class="poly-component__title" href="https://www.mercadolivre.com.br/ar-tcl/p/MLB55">
      Ar Condicionado TCL 12000 Btus
    </a>
    <span class="poly-component__seller">Por KARZEN ELETRO</span>
    <div class="poly-reviews">
      <span>4.7</span>
      <span>(1.234)</span>
    </div>
    <div class="andes-money-amount">
      <span class="andes-money-amount__fraction">2.499</span>
    </div>
  </div>
</li>
"""


class TestReviewsEstrutural:
    """Avaliação ficou 0% em 35.445 registros: as classes Poly mudaram de novo."""

    def test_par_nota_e_contagem_sem_classe_conhecida(self):
        rating, count = MLScraper._extract_reviews(_item(CARD_POLY_ATUAL_3P))
        assert rating == 4.7
        assert count == 1234

    def test_nota_isolada_sem_contagem(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <span>4,9</span>
        </div></li>"""
        rating, count = MLScraper._extract_reviews(_item(html))
        assert rating == 4.9
        assert count is None

    @pytest.mark.parametrize("ruido", [
        '<span class="andes-money-amount__fraction">2.799</span>',  # preço
        "<span>12x R$ 233,25</span>",                               # parcela
        "<span>Ar Condicionado 9.000 Btus</span>",                  # título
        "<span>(2026)</span>",                                      # ano solto
        "<span>8.5</span>",                                         # fora de 0..5
    ])
    def test_nao_confunde_ruido_do_card_com_avaliacao(self, ruido):
        html = f'<li class="ui-search-layout__item"><div class="poly-card">{ruido}</div></li>'
        rating, count = MLScraper._extract_reviews(_item(html))
        assert rating is None
        assert count is None

    def test_seletor_dedicado_tem_prioridade(self):
        # com a classe Poly presente, a camada estrutural não deve interferir
        rating, count = MLScraper._extract_reviews(_item(CARD_ORGANIC_POLY))
        assert (rating, count) == (4.8, 1234)


class TestTipoSellerSemFalsoPositivo:
    """86% dos registros vinham como "Loja Oficial", incluindo 3P evidentes."""

    def test_seller_3p_comum_nao_vira_oficial(self):
        item = _item(CARD_POLY_ATUAL_3P)
        assert MLScraper._detect_tipo_seller(item, "KARZEN ELETRO") == "3P"

    def test_texto_solto_no_card_nao_basta(self):
        # a varredura antiga (item.find(string=...)) marcava o card inteiro
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <span class="poly-component__seller">Por Dox Comercio</span>
          <p>Compre também na loja oficial da marca</p>
        </div></li>"""
        assert MLScraper._detect_tipo_seller(_item(html), "Dox Comercio") == "3P"

    def test_cockade_fora_do_bloco_do_seller_nao_basta(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <span class="poly-component__seller">Por Bagatoli</span>
          <svg class="andes-cockade-icon"></svg>
        </div></li>"""
        assert MLScraper._detect_tipo_seller(_item(html), "Bagatoli") == "3P"

    def test_href_de_vitrine_oficial_conta(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <a href="https://www.mercadolivre.com.br/loja/midea">Midea</a>
        </div></li>"""
        assert MLScraper._detect_tipo_seller(_item(html), "Midea") == "Loja Oficial"

    def test_loja_no_slug_do_produto_nao_conta(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <a href="https://www.mercadolivre.com.br/ar-condicionado-loja/p/MLB9">x</a>
        </div></li>"""
        assert MLScraper._detect_tipo_seller(_item(html), "Joviki") == "3P"

    def test_evidencia_nomeia_a_camada(self):
        item = _item(CARD_LEGACY)
        assert MLScraper._official_store_evidence(item, "Elgin") == "label-legado"


class TestFulfillment:
    """Fulfillment ficou em ~1%: o selo FULL é ícone, não nó de texto."""

    def test_aria_label_do_icone(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <svg aria-label="Enviado pelo Mercado Envios Full"></svg>
        </div></li>"""
        assert MLScraper._is_fulfillment(_item(html)) is True

    def test_classe_de_fulfillment(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <span class="poly-component__shipping-fulfillment"></span>
        </div></li>"""
        assert MLScraper._is_fulfillment(_item(html)) is True

    def test_full_no_titulo_nao_e_fulfillment(self):
        # "Full DC Inverter" é nome de tecnologia, não selo de logística
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <a class="poly-component__title" href="/p/MLB1">Ar Condicionado Full DC Inverter 18000</a>
        </div></li>"""
        assert MLScraper._is_fulfillment(_item(html)) is False

    def test_card_sem_selo(self):
        assert MLScraper._is_fulfillment(_item(CARD_POLY_ATUAL_3P)) is False


class TestUrlDeAnuncio:
    """URL de patrocinado vinha como link de tracking (expira, não deduplica)."""

    def test_deriva_permalink_do_item_id(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <a class="poly-component__title"
             href="https://click1.mercadolivre.com.br/mclics/clicks/external/MLB/count?a=xyz&pdp_filters=item_id%3AMLB6968369576">
            Ar Condicionado Consul
          </a>
        </div></li>"""
        assert MLScraper._extract_url(_item(html)) == (
            "https://produto.mercadolivre.com.br/MLB-6968369576"
        )

    def test_prefere_ancora_direta_ao_link_de_ad(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <a class="ui-search-link" href="https://click1.mercadolivre.com.br/mclics/x?a=1">ad</a>
          <a class="poly-component__title" href="https://www.mercadolivre.com.br/ar-lg/p/MLB77">pdp</a>
        </div></li>"""
        assert MLScraper._extract_url(_item(html)) == (
            "https://www.mercadolivre.com.br/ar-lg/p/MLB77"
        )

    def test_vitrine_da_loja_nao_vira_url_do_produto(self):
        # patrocinado de loja oficial: o candidato genérico a[href*=mercadolivre]
        # casava o link da vitrine (que vem depois do título) e devolvia a loja
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <a class="poly-component__title"
             href="https://click1.mercadolivre.com.br/mclics/clicks/external/MLB/count?a=zz&pdp_filters=item_id%3AMLB6392032212">
            Ar Condicionado TCL Elite G2
          </a>
          <span class="poly-component__seller">
            <a href="https://www.mercadolivre.com.br/loja/webcontinental">Por Webcontinental</a>
          </span>
        </div></li>"""
        assert MLScraper._extract_url(_item(html)) == (
            "https://produto.mercadolivre.com.br/MLB-6392032212"
        )

    def test_organico_de_loja_oficial_mantem_o_pdp(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <a class="poly-component__title" href="https://www.mercadolivre.com.br/ar-midea/p/MLB42">x</a>
          <a href="https://www.mercadolivre.com.br/loja/midea">Por Midea</a>
        </div></li>"""
        assert MLScraper._extract_url(_item(html)) == (
            "https://www.mercadolivre.com.br/ar-midea/p/MLB42"
        )

    def test_ad_sem_item_id_preserva_o_link(self):
        html = """
        <li class="ui-search-layout__item"><div class="poly-card">
          <a class="poly-component__title" href="https://click1.mercadolivre.com.br/mclics/x?a=1">ad</a>
        </div></li>"""
        assert MLScraper._extract_url(_item(html)) == (
            "https://click1.mercadolivre.com.br/mclics/x?a=1"
        )


class TestPaginacao:
    """`_Desde_` fixo em 48 repetia os itens 49..60 quando a SERP passou a 60."""

    def test_primeira_pagina_sem_offset(self):
        assert MLScraper._build_url("ar condicionado") == (
            "https://lista.mercadolivre.com.br/ar-condicionado"
        )

    @pytest.mark.parametrize("vistos,esperado", [(48, 49), (60, 61), (107, 108)])
    def test_offset_segue_os_itens_realmente_vistos(self, vistos, esperado):
        assert MLScraper._build_url("ar condicionado", vistos).endswith(
            f"_Desde_{esperado}"
        )


class TestPosicoesEntrePaginas:
    """Posição Orgânica reiniciava em 1 na página 2 (dois produtos na mesma posição)."""

    @staticmethod
    def _pagina(n: int, inicio: int) -> str:
        cards = "".join(
            f"""<li class="ui-search-layout__item"><div class="poly-card">
                  <a class="poly-component__title" href="/p/MLB{inicio+i}">Ar Midea {inicio+i}</a>
                </div></li>"""
            for i in range(n)
        )
        return f'<ol class="ui-search-layout">{cards}</ol>'

    def _scraper(self):
        scraper = MLScraper.__new__(MLScraper)
        scraper._last_screenshot_busca = None
        return scraper

    def test_posicoes_continuam_na_segunda_pagina(self):
        scraper = self._scraper()
        cursor = _SerpCursor()

        p1 = scraper._parse_results(self._pagina(60, 1), "kw", {}, cursor=cursor)
        assert cursor.items_seen == 60
        assert [r["Posição Geral"] for r in p1][:3] == [1, 2, 3]
        assert p1[-1]["Posição Geral"] == 60
        assert p1[-1]["Posição Orgânica"] == 60

        p2 = scraper._parse_results(self._pagina(60, 61), "kw", {}, cursor=cursor)
        assert p2[0]["Posição Geral"] == 61
        assert p2[0]["Posição Orgânica"] == 61

        todas = [r["Posição Orgânica"] for r in p1 + p2]
        assert len(todas) == len(set(todas)), "posição orgânica duplicada entre páginas"

    def test_page_offset_ainda_funciona_sem_cursor(self):
        # compatibilidade: chamadas antigas passam page_offset em vez de cursor
        scraper = self._scraper()
        recs = scraper._parse_results(self._pagina(2, 1), "kw", {}, page_offset=48)
        assert [r["Posição Geral"] for r in recs] == [49, 50]


class TestSellerNaoInventado:
    """Seller ausente virava "Mercado Livre" — 13,5% do buy box era artefato."""

    def test_card_sem_seller_fica_nulo(self):
        scraper = MLScraper.__new__(MLScraper)
        scraper._last_screenshot_busca = None
        recs = scraper._parse_results(
            f'<ol class="ui-search-layout">{CARD_BARE}</ol>', "kw", {}
        )
        assert recs[0]["Seller / Vendedor"] is None
        assert recs[0]["Buy Box Seller"] is None

    def test_card_com_seller_preserva_o_nome(self):
        scraper = MLScraper.__new__(MLScraper)
        scraper._last_screenshot_busca = None
        recs = scraper._parse_results(
            f'<ol class="ui-search-layout">{CARD_POLY_ATUAL_3P}</ol>', "kw", {}
        )
        assert recs[0]["Seller / Vendedor"] == "KARZEN ELETRO"
        assert recs[0]["Buy Box Seller"] == "KARZEN ELETRO"


class TestCobertura:
    """Campo zerado precisa virar WARNING na própria coleta."""

    def test_conta_campos_extraidos(self):
        scraper = MLScraper.__new__(MLScraper)
        scraper._last_screenshot_busca = None
        cursor = _SerpCursor()
        scraper._parse_results(
            f'<ol class="ui-search-layout">{CARD_POLY_ATUAL_3P}</ol>',
            "kw", {}, cursor=cursor,
        )
        assert cursor.coverage["rating"] == 1
        assert cursor.coverage["review_count"] == 1
        assert cursor.coverage["seller"] == 1
        assert "oficial" not in cursor.coverage

    def test_keyword_sem_ads_nao_e_tratada_como_quebra(self, tmp_path, monkeypatch):
        # SERP de cauda longa pode não ter anúncio: isso não é DOM quebrado, e
        # não pode gerar WARNING nem sobrescrever o card de amostra
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        scraper = MLScraper.__new__(MLScraper)
        scraper._last_screenshot_busca = None
        cursor = _SerpCursor()
        scraper._parse_results(
            f'<ol class="ui-search-layout">{CARD_POLY_ATUAL_3P}</ol>',
            "kw sem ads", {}, cursor=cursor,
        )
        assert "sponsored" not in cursor.coverage
        scraper._log_coverage("kw sem ads", cursor, _item(CARD_POLY_ATUAL_3P))
        assert not (tmp_path / "logs" / "ml_card_sample.html").exists()

    def test_run_sem_nenhum_ad_avisa_no_fechamento(self, caplog):
        scraper = MLScraper.__new__(MLScraper)
        scraper._run_keywords, scraper._run_items, scraper._run_sponsored = 30, 1800, 0
        mensagens = []
        handler_id = logger.add(lambda m: mensagens.append(m), level="WARNING")
        try:
            scraper._log_run_summary()
        finally:
            logger.remove(handler_id)
        assert any("ZERO patrocinados" in m for m in mensagens)

    def test_run_com_ads_nao_avisa(self):
        scraper = MLScraper.__new__(MLScraper)
        scraper._run_keywords, scraper._run_items, scraper._run_sponsored = 30, 1800, 470
        mensagens = []
        handler_id = logger.add(lambda m: mensagens.append(m), level="WARNING")
        try:
            scraper._log_run_summary()
        finally:
            logger.remove(handler_id)
        assert mensagens == []

    def test_dump_de_amostra_quando_campo_zera(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        scraper = MLScraper.__new__(MLScraper)
        scraper._last_screenshot_busca = None
        cursor = _SerpCursor()
        scraper._parse_results(
            f'<ol class="ui-search-layout">{CARD_BARE}</ol>', "kw", {}, cursor=cursor
        )
        scraper._log_coverage("kw", cursor, _item(CARD_BARE))
        assert (tmp_path / "logs" / "ml_card_sample.html").exists()


class _FakePage:
    """Página mínima: devolve um sinal de gate diferente a cada checagem."""

    def __init__(self, sinais):
        self.sinais = list(sinais)
        self.url = "https://lista.mercadolivre.com.br/kw"
        self.gotos = []
        self.checagens = 0

    def content(self):
        self.checagens += 1
        idx = min(self.checagens - 1, len(self.sinais) - 1)
        return (
            "<html>Para continuar, acesse sua conta</html>"
            if self.sinais[idx] else "<html>ok</html>"
        )

    def goto(self, url, **_):
        self.gotos.append(url)

    def wait_for_timeout(self, _ms):
        pass


def _scraper_com_pagina(sinais):
    scraper = MLScraper.__new__(MLScraper)
    scraper._page = _FakePage(sinais)
    scraper._wait_for_network_idle = lambda: None
    scraper._random_delay = lambda *a, **k: None
    return scraper


class TestLoginGateTransitorio:
    """Gate 1-2s após o goto encerrava a keyword com 0 produtos (25/07 12:28)."""

    def test_gate_transitorio_nao_conta_como_bloqueio(self):
        # 1ª checagem acusa, 2ª (após assentar) não — é navegação, não bloqueio
        scraper = _scraper_com_pagina([True, False])
        assert scraper._is_login_gate(confirm=True) is False

    def test_gate_persistente_e_confirmado(self):
        scraper = _scraper_com_pagina([True, True])
        assert scraper._is_login_gate(confirm=True) is True

    def test_sem_confirm_mantem_o_sinal_cru(self):
        scraper = _scraper_com_pagina([True, False])
        assert scraper._is_login_gate() is True

    def test_serp_retenta_e_supera_o_desafio(self):
        # falha nas duas primeiras tentativas, carrega na terceira — exatamente
        # o que aconteceu com 'ar condicionado 12000 btus'
        scraper = _scraper_com_pagina([True, True, True, True, False, False])
        assert scraper._goto_serp("https://lista.mercadolivre.com.br/kw", "kw", 1) is True
        assert len(scraper._page.gotos) == 3

    def test_serp_desiste_apos_o_limite(self):
        scraper = _scraper_com_pagina([True])
        assert scraper._goto_serp("https://lista.mercadolivre.com.br/kw", "kw", 1) is False
        assert len(scraper._page.gotos) == 3  # 1 + _GATE_RETRIES

    def test_pagina_limpa_nao_gasta_tentativa(self):
        scraper = _scraper_com_pagina([False])
        assert scraper._goto_serp("https://lista.mercadolivre.com.br/kw", "kw", 1) is True
        assert len(scraper._page.gotos) == 1


class TestWarmUp:
    """ML ia direto para /lista com sessão fria — 7 keywords seguidas no gate."""

    def test_aquece_a_home_uma_vez_por_run(self):
        scraper = MLScraper.__new__(MLScraper)
        scraper._page = _FakePage([False])
        scraper._local_active = False
        scraper._wait_for_network_idle = lambda: None
        scraper._dismiss_cep_popup = lambda: None
        scraper._human_scroll = lambda **k: None

        scraper._warm_session()
        scraper._warm_session()
        assert scraper._page.gotos == ["https://www.mercadolivre.com.br/"]

    def test_falha_no_warmup_nao_aborta_a_coleta(self):
        scraper = MLScraper.__new__(MLScraper)
        scraper._local_active = False

        def explode(*_a, **_k):
            raise RuntimeError("timeout na home")

        scraper._page = _FakePage([False])
        scraper._page.goto = explode
        scraper._warm_session()  # não pode propagar
        assert scraper._warmed is True


class TestBlockSignals:
    @pytest.mark.parametrize("html", [
        "<html><body>Para continuar, acesse sua conta</body></html>",
        '<div class="g-recaptcha"></div>',
        "<p>Access Denied</p>",
        "<p>We detected unusual traffic from your network</p>",
        '<script src="/gz/webdevice/account-verification"></script>',
        "<p>Please complete the robot challenge to continue</p>",
    ])
    def test_sinais_reais_de_bloqueio(self, html):
        assert _BLOCK_SIGNALS_RE.search(html) is not None

    @pytest.mark.parametrize("html", [
        # texto benigno que a regex antiga (robot|verifica|blocked) marcava
        "<h2>Robô Aspirador Inteligente 12000</h2>",
        "<p>Verifique a voltagem antes de comprar</p>",
        "<meta name='robots' content='index,follow'>",
        "<p>Ar Condicionado com filtro que bloqueia poeira</p>",
    ])
    def test_texto_benigno_nao_marca(self, html):
        assert _BLOCK_SIGNALS_RE.search(html) is None


# ---------------------------------------------------------------------------
# Preço (regressão — extração existente não pode quebrar)
# ---------------------------------------------------------------------------

class TestExtractPrice:
    def test_fracao_e_centavos(self):
        assert MLScraper._extract_price(_item(CARD_ORGANIC_POLY)) == 2799.90

    def test_ignora_preco_riscado(self):
        html = """
        <li class="ui-search-layout__item">
          <s class="andes-money-amount andes-money-amount--previous">
            <span class="andes-money-amount__fraction">3.299</span>
          </s>
          <div class="andes-money-amount">
            <span class="andes-money-amount__fraction">2.599</span>
            <span class="andes-money-amount__cents">00</span>
          </div>
        </li>"""
        assert MLScraper._extract_price(_item(html)) == 2599.0
