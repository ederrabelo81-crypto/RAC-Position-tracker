"""
tests/test_casas_bahia_embedded_json.py — extração de produtos VTEX embutidos
no HTML (SSR), sem depender de uma chamada de API separada.

Motivação: no modo Chrome local, os endpoints VTEX clássicos
(catalog_system, intelligent-search) passaram a responder 200/404 com ZERO
produtos, mesmo após navegação real + XHR interception não capturar nada —
sinal de que a Casas Bahia agora renderiza os cards no servidor (padrão
Next.js/VTEX FastStore) e embute o payload completo (incl. sellers[]) num
<script>, sem disparar uma API separada que o browser possa interceptar.
`_extract_embedded_products` varre o HTML já baixado (zero requests extras)
por esse blob, reconhecendo o SHAPE do produto em vez de um path/id fixo —
robusto a mudanças de nome do script (__NEXT_DATA__, __STATE__, etc).

Rode: pytest tests/test_casas_bahia_embedded_json.py
"""
import json

import pytest

from scrapers.casas_bahia import CasasBahiaScraper


@pytest.fixture(scope="module")
def scraper():
    return CasasBahiaScraper()


def _vtex_product(name="Ar Condicionado Split 12000", seller_id="1", seller_name="Casas Bahia"):
    return {
        "productName": name,
        "items": [{
            "sellers": [{
                "sellerId": seller_id,
                "sellerName": seller_name,
                "sellerDefault": True,
                "commertialOffer": {"Price": 2599.0, "IsAvailable": True},
            }],
        }],
    }


class TestFindVtexProductList:
    def test_lista_direta_no_topo(self, scraper):
        data = [_vtex_product(), _vtex_product("Outro AC")]
        found = scraper._find_vtex_product_list(data)
        assert found == data

    def test_aninhado_em_estrutura_graphql_like(self, scraper):
        """Payload SSR típico: produtos enterrados vários níveis abaixo."""
        data = {
            "props": {
                "pageProps": {
                    "data": {
                        "search": {
                            "products": [_vtex_product(), _vtex_product("B")],
                        }
                    }
                }
            }
        }
        found = scraper._find_vtex_product_list(data)
        assert found is not None
        assert len(found) == 2

    def test_produto_reconhecido_via_items_sellers(self, scraper):
        """Reconhece o shape mesmo quando 'sellers' está dentro de items[],
        não na raiz do produto (shape real do catalog_system/IS)."""
        data = {"blob": {"list": [_vtex_product()]}}
        found = scraper._find_vtex_product_list(data)
        assert found is not None
        assert found[0]["productName"] == "Ar Condicionado Split 12000"

    def test_sem_produto_reconhecivel_retorna_none(self, scraper):
        data = {"menu": ["Home", "Ofertas"], "banner": {"url": "x.jpg"}}
        assert scraper._find_vtex_product_list(data) is None

    def test_lista_vazia_nao_quebra(self, scraper):
        assert scraper._find_vtex_product_list([]) is None

    def test_profundidade_excessiva_nao_trava(self, scraper):
        """Estrutura recursiva profunda não deve estourar recursão nem travar."""
        deep = {}
        node = deep
        for _ in range(20):
            node["next"] = {}
            node = node["next"]
        assert scraper._find_vtex_product_list(deep) is None


class TestExtractEmbeddedProducts:
    def test_script_next_data_style(self, scraper):
        payload = {"props": {"pageProps": {"products": [_vtex_product()]}}}
        html = (
            "<html><body>"
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
            "</body></html>"
        )
        products = scraper._extract_embedded_products(html)
        assert products is not None
        assert len(products) == 1
        assert products[0]["productName"] == "Ar Condicionado Split 12000"

    def test_script_window_assignment_style(self, scraper):
        """Cobre `window.__STATE__ = {...};` (sem type=application/json)."""
        payload = {"state": {"catalog": [_vtex_product(), _vtex_product("B")]}}
        html = (
            "<html><body>"
            f"<script>window.__STATE__ = {json.dumps(payload)};</script>"
            "</body></html>"
        )
        products = scraper._extract_embedded_products(html)
        assert products is not None
        assert len(products) == 2

    def test_sem_script_relevante_retorna_none(self, scraper):
        html = (
            "<html><body>"
            '<script>console.log("oi");</script>'
            "<script>ga('send', 'pageview');</script>"
            "</body></html>"
        )
        assert scraper._extract_embedded_products(html) is None

    def test_script_curto_e_ignorado(self, scraper):
        """Scripts triviais (< 200 chars) não valem a pena tentar parsear."""
        html = '<html><body><script>{"a": 1}</script></body></html>'
        assert scraper._extract_embedded_products(html) is None

    def test_json_malformado_nao_quebra_e_continua(self, scraper):
        """Um <script> com JSON quebrado não deve abortar a varredura dos
        demais — o próximo script válido ainda deve ser encontrado."""
        payload = {"products": [_vtex_product()]}
        html = (
            "<html><body>"
            + "<script>" + "{" + "malformado sem fechar " * 20 + "</script>"
            + f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>'
            + "</body></html>"
        )
        products = scraper._extract_embedded_products(html)
        assert products is not None
        assert len(products) == 1
