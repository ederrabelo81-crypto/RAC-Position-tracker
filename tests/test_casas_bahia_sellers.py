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
