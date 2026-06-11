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

from scrapers.mercado_livre import MLScraper


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
