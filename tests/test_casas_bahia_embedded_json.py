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

    def test_prefere_maior_lista_entre_multiplos_candidatos(self, scraper):
        """Uma página SSR real pode ter VÁRIOS arrays no shape VTEX: a grade
        de busca (muitos itens) e widgets tipo "recomendados"/"vistos
        recentemente" (poucos itens). Parar no PRIMEIRO achado arriscaria
        devolver o widget errado pra keyword atual — o carrossel pequeno vem
        ANTES da grade real na árvore, de propósito, pra provar que não é só
        "o que aparece primeiro" que ganha."""
        data = {
            "widgets": {
                "recomendados": [_vtex_product("Recomendado A"), _vtex_product("Recomendado B")],
            },
            "search": {
                "results": [_vtex_product(f"Resultado {i}") for i in range(10)],
            },
        }
        found = scraper._find_vtex_product_list(data)
        assert found is not None
        assert len(found) == 10
        assert found[0]["productName"] == "Resultado 0"


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

    def test_script_com_raiz_em_array(self, scraper):
        """`<script type="application/json">[{...}, {...}]</script>` — a
        RAIZ do JSON é um array, não um objeto. Pegar do primeiro `{` (que
        cai DENTRO do array) quebraria o parse; o extrator precisa reconhecer
        `[` como possível início também."""
        payload = [_vtex_product("Item 1"), _vtex_product("Item 2")]
        html = (
            "<html><body>"
            f'<script type="application/json">{json.dumps(payload)}</script>'
            "</body></html>"
        )
        products = scraper._extract_embedded_products(html)
        assert products is not None
        assert len(products) == 2

    def test_script_com_multiplas_atribuicoes_js(self, scraper):
        """`window.__A__ = {...}; window.__B__ = {...};` — DUAS declarações
        JSON no mesmo <script>. Pegar do primeiro `{` até o fim do texto (só
        com rstrip(";")) deixaria o statement do meio no meio do JSON e
        quebraria o parse. O extrator precisa achar os DOIS blobs balanceados
        separadamente — e encontrar os produtos mesmo estando no SEGUNDO."""
        payload_a = {"irrelevante": True}
        payload_b = {"products": [_vtex_product("Do Segundo Statement")]}
        html = (
            "<html><body><script>"
            f"window.__A__ = {json.dumps(payload_a)}; "
            f"window.__B__ = {json.dumps(payload_b)};"
            "</script></body></html>"
        )
        products = scraper._extract_embedded_products(html)
        assert products is not None
        assert len(products) == 1
        assert products[0]["productName"] == "Do Segundo Statement"

    def test_script_com_wrapper_iife_nao_json(self, scraper):
        """`(function(){ var d = {...}; window.__STATE__ = d; })();` — o
        bloco EXTERNO (corpo da function) NÃO é JSON válido (tem `var`, `;`,
        atribuição) — só o objeto `d` interno é. Um extrator que desiste no
        primeiro bloco balanceado que falha o parse NUNCA acharia o payload,
        porque ele está aninhado dentro do bloco que falhou."""
        payload = {"products": [_vtex_product("Dentro da IIFE")]}
        html = (
            "<html><body><script>"
            "(function(){"
            f"var __data__ = {json.dumps(payload)};"
            "window.__STATE__ = __data__;"
            "})();"
            "</script></body></html>"
        )
        products = scraper._extract_embedded_products(html)
        assert products is not None
        assert len(products) == 1
        assert products[0]["productName"] == "Dentro da IIFE"

    def test_script_com_try_catch_wrapper(self, scraper):
        """Variante com try/catch — outro wrapper JS comum que embrulha um
        payload JSON válido dentro de um bloco que, por conta do `try`, não
        é JSON puro."""
        payload = {"products": [_vtex_product("Dentro do try")]}
        html = (
            "<html><body><script>"
            "try {"
            f"window.__NEXT_DATA__ = {json.dumps(payload)};"
            "} catch (e) { console.error(e); }"
            "</script></body></html>"
        )
        products = scraper._extract_embedded_products(html)
        assert products is not None
        assert len(products) == 1
        assert products[0]["productName"] == "Dentro do try"

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
