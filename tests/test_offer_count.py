"""
tests/test_offer_count.py — contagem de sellers competindo (`Qtd Sellers`).

Regressão de 10/08/2026: ML e Magalu gravavam `Qtd Sellers` 100% vazio no
Supabase — nenhum dos dois passava o campo ao `_build_record`, embora buy box
já funcionasse. Cobre os dois extratores criados para fechar esse gap:

  - `utils.text.parse_offer_count`  → ML (texto PT-BR do card)
  - `MagaluScraper._extract_seller_count` → Magalu (campos tipados do JSON)

Regra central em ambos: ausência de sinal devolve None, nunca 1.

Rode: pytest tests/test_offer_count.py
"""
import pytest

from scrapers.magalu import MagaluScraper
from utils.text import parse_offer_count


class TestParseOfferCountTexto:
    @pytest.mark.parametrize("texto,esperado", [
        ("3 vendedores", 3),
        ("5 ofertas a partir de R$ 2.199", 5),
        ("2 lojas", 2),
        ("12 anúncios", 12),
    ])
    def test_contagem_explicita(self, texto, esperado):
        assert parse_offer_count(texto) == esperado

    @pytest.mark.parametrize("texto", [
        "Outras opções de compra: a partir de R$ 2.199",
        "Mais opções de compra",
        "Novo a partir de R$ 1.899",
    ])
    def test_mencao_sem_numero_vira_piso_2(self, texto):
        """Competição existe mas o tamanho é desconhecido → piso honesto de 2."""
        assert parse_offer_count(texto) == 2

    @pytest.mark.parametrize("texto", [
        None, "", "Frete grátis", "Ar Condicionado 12000 BTUs Inverter",
        "Chega amanhã", "R$ 2.199,90 em 10x sem juros",
    ])
    def test_sem_sinal_devolve_none(self, texto):
        """Desconhecido != seller único: nunca devolver 1 por omissão."""
        assert parse_offer_count(texto) is None

    def test_numero_absurdo_e_lixo_de_parse(self):
        """999 'ofertas' é número colado de outro campo, não competição real."""
        assert parse_offer_count("999 ofertas") is None

    def test_case_insensitive(self):
        assert parse_offer_count("3 VENDEDORES") == 3

    def test_nao_confunde_btu_com_oferta(self):
        assert parse_offer_count("Split 12000 BTUs 220V") is None


@pytest.fixture(scope="module")
def scraper():
    return MagaluScraper()


class TestMagaluSellerCount:
    def test_lista_de_sellers(self, scraper):
        prod = {"sellers": [{"name": "Magalu"}, {"name": "Refri Center"}]}
        assert scraper._extract_seller_count(prod) == 2

    def test_lista_de_offers(self, scraper):
        assert scraper._extract_seller_count({"offers": [{}, {}, {}]}) == 3

    def test_campo_escalar_int(self, scraper):
        assert scraper._extract_seller_count({"sellerCount": 4}) == 4

    def test_campo_escalar_string_numerica(self, scraper):
        assert scraper._extract_seller_count({"offersCount": "7"}) == 7

    def test_payload_sem_sinal(self, scraper):
        prod = {"title": "Ar Condicionado", "price": 2199.0}
        assert scraper._extract_seller_count(prod) is None

    def test_lista_vazia_nao_conta(self, scraper):
        assert scraper._extract_seller_count({"sellers": []}) is None

    def test_booleano_nao_vira_contagem(self, scraper):
        """True é int em Python — não pode virar '1 seller'."""
        assert scraper._extract_seller_count({"sellerCount": True}) is None

    def test_valor_absurdo_rejeitado(self, scraper):
        assert scraper._extract_seller_count({"sellerCount": 5000}) is None
