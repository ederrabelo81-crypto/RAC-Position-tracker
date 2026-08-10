"""
tests/test_casas_bahia_sellers.py — extração de buy box / sellers da VTEX.

Cobre `_extract_vtex_sellers` sobre o payload do endpoint catalog_system
(`items[].sellers[]` com `commertialOffer`), que é a fonte que o
`_vtex_fetch_in_page` passou a buscar para destravar o gap de seller da Casas
Bahia (buy box real + split 1P/3P, em vez do fallback de DOM).

Rode: pytest tests/test_casas_bahia_sellers.py
"""
import pytest

from scrapers.casas_bahia import CasasBahiaScraper


@pytest.fixture(scope="module")
def scraper():
    return CasasBahiaScraper()


def _seller(sid, name, default, price, available=True):
    return {
        "sellerId": sid,
        "sellerName": name,
        "sellerDefault": default,
        "commertialOffer": {"Price": price, "IsAvailable": available},
    }


class TestExtractVtexSellers:
    def test_buy_box_1p_com_competicao(self, scraper):
        """sellerDefault vence a buy box; conta todos os sellers disponíveis."""
        prod = {
            "productName": "Ar Condicionado Split 12000 BTUs",
            "items": [{"sellers": [
                _seller("1", "Casas Bahia", True, 2599.0),
                _seller("loja123", "Refri Center", False, 2700.0),
            ]}],
        }
        info = scraper._extract_vtex_sellers(prod)
        assert info["buy_box_seller"] == "Casas Bahia"
        assert info["qtd_sellers"] == 2
        assert info["tipo_seller"] == "1P"
        assert info["price_float"] == 2599.0

    def test_buy_box_3p(self, scraper):
        """Quando o vencedor é um parceiro (sellerId != 1), tipo = 3P."""
        prod = {
            "productName": "Ar Condicionado Inverter 9000",
            "items": [{"sellers": [
                _seller("parceiroX", "Loja Terceiro", True, 1999.0),
            ]}],
        }
        info = scraper._extract_vtex_sellers(prod)
        assert info["buy_box_seller"] == "Loja Terceiro"
        assert info["qtd_sellers"] == 1
        assert info["tipo_seller"] == "3P"

    def test_sem_sellers_retorna_desconhecido(self, scraper):
        """Payload sem sellers[] → buy box desconhecida (None), não vitória 1P."""
        prod = {"productName": "X", "items": [{"sellers": []}]}
        info = scraper._extract_vtex_sellers(prod)
        assert info["buy_box_seller"] is None
        assert info["tipo_seller"] is None
        assert info["qtd_sellers"] is None

    def test_seller_indisponivel_nao_conta(self, scraper):
        """Ofertas indisponíveis não entram na contagem de competição."""
        prod = {
            "productName": "Y",
            "items": [{"sellers": [
                _seller("1", "Casas Bahia", True, 2599.0, available=True),
                _seller("loja9", "Fora de Estoque", False, 2400.0, available=False),
            ]}],
        }
        info = scraper._extract_vtex_sellers(prod)
        assert info["buy_box_seller"] == "Casas Bahia"
        assert info["qtd_sellers"] == 1  # só o disponível

    def test_default_indisponivel_cede_para_disponivel(self, scraper):
        """sellerDefault indisponível, aparecendo PRIMEIRO no array, não pode
        travar o vencedor — a buy box vai para o seller disponível, mesmo que
        ele não seja o default. Regressão de um bug onde a ordem do array
        importava (default indisponível "poisoning" o estado do loop)."""
        prod = {
            "productName": "Z",
            "items": [{"sellers": [
                _seller("1", "Casas Bahia", True, 2599.0, available=False),
                _seller("loja7", "Loja Disponível", False, 2650.0, available=True),
            ]}],
        }
        info = scraper._extract_vtex_sellers(prod)
        assert info["buy_box_seller"] == "Loja Disponível"
        assert info["tipo_seller"] == "3P"
        assert info["qtd_sellers"] == 1  # só a disponível conta na competição

    def test_todos_indisponiveis_cai_no_default(self, scraper):
        """Sem NENHUM seller disponível, best-effort: mostra o sellerDefault
        mesmo indisponível, em vez de não mostrar nada."""
        prod = {
            "productName": "W",
            "items": [{"sellers": [
                _seller("loja7", "Loja X", False, 2650.0, available=False),
                _seller("1", "Casas Bahia", True, 2599.0, available=False),
            ]}],
        }
        info = scraper._extract_vtex_sellers(prod)
        assert info["buy_box_seller"] == "Casas Bahia"
        assert info["qtd_sellers"] is None  # nenhum disponível


class TestHasSellerData:
    """Gate que decide se vale buscar a API VTEX depois do DOM.

    Regressão de 10/08/2026: o gatilho do fallback rico era `not records`, e o
    parser de DOM quase sempre devolve cards — então a etapa da API nunca era
    alcançada e `Buy Box Seller`/`Qtd Sellers` ficaram 0% no banco.
    """

    def test_lista_vazia_nao_tem_seller(self, scraper):
        assert scraper._has_seller_data([]) is False

    def test_none_nao_tem_seller(self, scraper):
        assert scraper._has_seller_data(None) is False

    def test_registros_do_dom_nao_tem_seller(self, scraper):
        """O parser de DOM zera Buy Box Seller de propósito."""
        dom_records = [
            {"Produto / SKU": "Split 9000", "Preço (R$)": 2199.0, "Buy Box Seller": None},
            {"Produto / SKU": "Split 12000", "Preço (R$)": 2599.0, "Buy Box Seller": None},
        ]
        assert scraper._has_seller_data(dom_records) is False

    def test_um_registro_com_buy_box_basta(self, scraper):
        mistos = [
            {"Produto / SKU": "A", "Buy Box Seller": None},
            {"Produto / SKU": "B", "Buy Box Seller": "Refri Center"},
        ]
        assert scraper._has_seller_data(mistos) is True

    def test_string_vazia_nao_conta_como_seller(self, scraper):
        assert scraper._has_seller_data([{"Buy Box Seller": ""}]) is False




class TestMergeSellerFields:
    """
    Enriquecer o DOM em vez de substituí-lo.

    O recorte da API (`_from`/`_to` sobre o catálogo) e a vitrine renderizada
    não devolvem sempre o mesmo conjunto de produtos. Numa troca integral, os
    cards que só existiam no DOM sumiriam da coleta levando junto preço e
    posição — que estavam corretos. A posição do DOM é, ainda por cima, a que
    o usuário viu na busca orgânica.
    """

    @staticmethod
    def _dom(url, pos, preco):
        return {
            "URL Produto": url, "Posição Geral": pos, "Preço (R$)": preco,
            "Buy Box Seller": None, "Qtd Sellers": None, "Tipo Seller": None,
        }

    @staticmethod
    def _api(url, seller, qtd):
        return {
            "URL Produto": url, "Buy Box Seller": seller,
            "Qtd Sellers": qtd, "Tipo Seller": "3P",
        }

    def test_dom_ganha_seller_sem_perder_registros(self):
        dom = [self._dom("https://cb.com/p/1", 1, 2199.0),
               self._dom("https://cb.com/p/2", 2, 2599.0)]
        api = [self._api("https://cb.com/p/1", "LojaX", 4),
               self._api("https://cb.com/p/2", "LojaY", 2)]

        n = CasasBahiaScraper._merge_seller_fields(dom, api)

        assert n == 2
        assert len(dom) == 2
        assert dom[0]["Buy Box Seller"] == "LojaX"
        assert dom[0]["Qtd Sellers"] == 4
        # Preço e posição do DOM preservados — é o que a busca mostrou.
        assert dom[0]["Posição Geral"] == 1
        assert dom[0]["Preço (R$)"] == 2199.0

    def test_api_parcial_nao_encolhe_a_pagina(self):
        """O caso que a troca integral quebrava: API menor que o DOM."""
        dom = [self._dom(f"https://cb.com/p/{i}", i, 1000.0 + i) for i in range(1, 6)]
        api = [self._api("https://cb.com/p/2", "LojaX", 3)]

        n = CasasBahiaScraper._merge_seller_fields(dom, api)

        assert n == 1
        assert len(dom) == 5, "nenhum card do DOM pode ser descartado"
        assert dom[1]["Buy Box Seller"] == "LojaX"
        assert dom[0]["Buy Box Seller"] is None

    def test_casa_ignorando_query_string(self):
        dom = [self._dom("https://cb.com/p/1?utm_source=busca", 1, 99.0)]
        api = [self._api("https://cb.com/p/1", "LojaX", 2)]
        assert CasasBahiaScraper._merge_seller_fields(dom, api) == 1
        assert dom[0]["Buy Box Seller"] == "LojaX"

    def test_sem_casamento_devolve_zero(self):
        dom = [self._dom("https://cb.com/p/9", 1, 99.0)]
        api = [self._api("https://cb.com/p/1", "LojaX", 2)]
        assert CasasBahiaScraper._merge_seller_fields(dom, api) == 0
        assert dom[0]["Buy Box Seller"] is None

    def test_nao_sobrescreve_seller_ja_presente(self):
        dom = [self._dom("https://cb.com/p/1", 1, 99.0)]
        dom[0]["Buy Box Seller"] = "JaTinha"
        api = [self._api("https://cb.com/p/1", "LojaX", 2)]

        CasasBahiaScraper._merge_seller_fields(dom, api)

        assert dom[0]["Buy Box Seller"] == "JaTinha"
        assert dom[0]["Qtd Sellers"] == 2  # campo vazio ainda é preenchido

    def test_api_sem_seller_nao_polui_o_dom(self):
        dom = [self._dom("https://cb.com/p/1", 1, 99.0)]
        api = [{"URL Produto": "https://cb.com/p/1", "Buy Box Seller": None}]
        assert CasasBahiaScraper._merge_seller_fields(dom, api) == 0
        assert dom[0]["Buy Box Seller"] is None


class TestChaveDeCasamentoReal:
    """
    A chave só funciona se `URL Produto` existir de verdade.

    A primeira versão do merge casava por `URL Produto`, mas nem `_parse_dom`
    nem `_parse_api_products` passavam `url_produto` ao `_build_record` — o
    campo era sempre None em produção e a chave caía no fallback por título
    normalizado, que diverge entre DOM (img[alt]) e API (productName). Os
    testes não pegaram porque usavam registros fictícios com a URL preenchida:
    provavam a função, não o fluxo. Aqui a extração é exercitada a partir da
    forma real de cada fonte.
    """

    def test_url_extraida_do_card_do_dom(self):
        from bs4 import BeautifulSoup
        card = BeautifulSoup(
            '<div><a href="/ar-condicionado-midea/p/123">x</a></div>',
            "html.parser",
        ).div
        url = CasasBahiaScraper._extract_dom_url(card)
        assert url == "https://www.casasbahia.com.br/ar-condicionado-midea/p/123"

    def test_url_absoluta_no_dom_e_preservada(self):
        from bs4 import BeautifulSoup
        card = BeautifulSoup(
            '<div><a href="https://www.casasbahia.com.br/x/p/9">x</a></div>',
            "html.parser",
        ).div
        assert CasasBahiaScraper._extract_dom_url(card).endswith("/x/p/9")

    def test_url_do_payload_vtex_por_link(self):
        url = CasasBahiaScraper._extract_api_url({"link": "/ar-cond/p/1"})
        assert url == "https://www.casasbahia.com.br/ar-cond/p/1"

    def test_url_do_payload_vtex_por_linktext(self):
        url = CasasBahiaScraper._extract_api_url({"linkText": "ar-cond-midea"})
        assert url == "https://www.casasbahia.com.br/ar-cond-midea/p"

    def test_payload_sem_url_devolve_none(self):
        assert CasasBahiaScraper._extract_api_url({"productName": "x"}) is None

    def test_dom_com_id_casa_com_api_sem_id(self):
        """
        A divergência REAL entre as duas fontes.

        A vitrine renderiza `/slug/p/12345678` (com id de catálogo); o payload
        VTEX expõe só `linkText`, que vira `/slug/p`. Comparar URL crua nunca
        casaria, e o merge cairia a zero acertos em silêncio — desfazendo na
        prática a correção que ele implementa. Os primeiros testes usavam
        `/x/p/1` dos dois lados, uma igualdade idealizada que escondia isso.
        """
        dom = [{"URL Produto":
                "https://www.casasbahia.com.br/ar-midea-12000/p/12345678",
                "Buy Box Seller": None, "Seller / Vendedor": "Casas Bahia"}]
        api = [{"URL Produto":
                "https://www.casasbahia.com.br/ar-midea-12000/p",
                "Buy Box Seller": "LojaX", "Seller / Vendedor": "LojaX"}]

        assert CasasBahiaScraper._merge_seller_fields(dom, api) == 1
        assert dom[0]["Buy Box Seller"] == "LojaX"

    def test_query_string_nao_atrapalha(self):
        dom = [{"URL Produto":
                "https://www.casasbahia.com.br/ar-x/p/999?utm_source=busca",
                "Buy Box Seller": None}]
        api = [{"URL Produto": "https://www.casasbahia.com.br/ar-x/p",
                "Buy Box Seller": "LojaX"}]
        assert CasasBahiaScraper._merge_seller_fields(dom, api) == 1

    def test_slugs_diferentes_nao_casam(self):
        """A chave não pode ser tão frouxa que case produtos distintos."""
        dom = [{"URL Produto": "https://www.casasbahia.com.br/ar-midea/p/1",
                "Buy Box Seller": None}]
        api = [{"URL Produto": "https://www.casasbahia.com.br/ar-elgin/p/2",
                "Buy Box Seller": "LojaX"}]
        assert CasasBahiaScraper._merge_seller_fields(dom, api) == 0

    @pytest.mark.parametrize("url", [
        "https://www.casasbahia.com.br/ar-midea-12000/p/12345678",
        "https://www.casasbahia.com.br/ar-midea-12000/p",
        "/ar-midea-12000/p/12345678",
        "https://www.casasbahia.com.br/ar-midea-12000/p/12345678?utm=x",
    ])
    def test_todas_as_formas_dao_o_mesmo_slug(self, url):
        assert CasasBahiaScraper._product_key(
            {"URL Produto": url}
        ) == "ar-midea-12000"


class TestPlaceholderDoSellerCede:
    """
    "Casas Bahia" no `Seller / Vendedor` do DOM é chute, não observação.

    O DOM usa o nome da casa como fallback quando não acha vendedor nenhum.
    Se esse valor sobrevivesse ao merge, uma oferta 3P ficaria com
    `Seller / Vendedor = Casas Bahia` e `Buy Box Seller = LojaX` no MESMO
    registro — o relatório se contradizendo sozinho.
    """

    def test_placeholder_cede_ao_vendedor_da_api(self):
        dom = [{"URL Produto": "https://cb.com/p/1",
                "Seller / Vendedor": "Casas Bahia", "Buy Box Seller": None}]
        api = [{"URL Produto": "https://cb.com/p/1",
                "Seller / Vendedor": "LojaX", "Buy Box Seller": "LojaX"}]

        CasasBahiaScraper._merge_seller_fields(dom, api)

        assert dom[0]["Seller / Vendedor"] == "LojaX"
        assert dom[0]["Buy Box Seller"] == "LojaX"

    def test_vendedor_real_do_dom_nao_e_sobrescrito(self):
        dom = [{"URL Produto": "https://cb.com/p/1",
                "Seller / Vendedor": "LojaObservada", "Buy Box Seller": None}]
        api = [{"URL Produto": "https://cb.com/p/1",
                "Seller / Vendedor": "LojaAPI", "Buy Box Seller": "LojaAPI"}]

        CasasBahiaScraper._merge_seller_fields(dom, api)

        assert dom[0]["Seller / Vendedor"] == "LojaObservada"


class TestUrlChegaAoRegistroPeloParserReal:
    """
    Prova a fiação, não só a função.

    A regressão original foi exatamente esta: `_extract_api_url` podia existir
    e funcionar, mas se `_parse_api_products` não passasse `url_produto=` ao
    `_build_record`, o campo chegava vazio em produção e o merge caía no
    fallback por título. Testar só o extrator deixaria isso passar de novo —
    então aqui o caminho exercitado é o parser de verdade, e a asserção é
    sobre o registro que ele devolve.
    """

    def test_parse_api_products_preenche_url_produto(self):
        scraper = CasasBahiaScraper()
        produtos = [{
            "productName": "Ar Condicionado Midea 12000 BTUs",
            "linkText": "ar-condicionado-midea-12000",
            "items": [{"sellers": [{
                "sellerName": "LojaX",
                "commertialOffer": {"Price": 2199.0, "IsAvailable": True},
            }]}],
        }]

        registros = scraper._parse_api_products(
            "ar condicionado", {}, 0, products=produtos,
        )

        assert registros, "o parser precisa devolver ao menos um registro"
        assert registros[0]["URL Produto"], (
            "URL Produto vazio: `url_produto=` não está sendo passado ao "
            "_build_record — o merge DOM↔API volta a cair no fallback"
        )
        assert CasasBahiaScraper._product_key(registros[0]) == \
            "ar-condicionado-midea-12000"

    def test_registro_da_api_casa_com_registro_de_dom_equivalente(self):
        """Ponta a ponta da chave: parser real de um lado, href real do outro."""
        scraper = CasasBahiaScraper()
        produtos = [{
            "productName": "Ar Condicionado Midea 12000 BTUs",
            "linkText": "ar-condicionado-midea-12000",
            "items": [{"sellers": [{
                "sellerName": "LojaX",
                "commertialOffer": {"Price": 2199.0, "IsAvailable": True},
            }]}],
        }]
        api_records = scraper._parse_api_products(
            "ar condicionado", {}, 0, products=produtos,
        )

        # Href como a vitrine renderiza: com id de catálogo.
        dom = [{
            "URL Produto":
                "https://www.casasbahia.com.br/ar-condicionado-midea-12000/p/98765",
            "Buy Box Seller": None,
            "Seller / Vendedor": "Casas Bahia",
        }]

        assert CasasBahiaScraper._merge_seller_fields(dom, api_records) == 1
        assert dom[0]["Buy Box Seller"] == "LojaX"
