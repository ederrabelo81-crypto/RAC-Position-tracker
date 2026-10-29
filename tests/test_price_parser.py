"""Regressão de `utils.text.parse_price_brazil` / `parse_price`.

O fallback do parser casava só `\\d{1,3}(?:\\.\\d{3})*`: um inteiro sem
separador de milhar ("2339", formato comum do `price` em JSON-LD) virava
233.0 — preço de dealer gravado 10x abaixo.
"""

import pytest

from utils.text import parse_price, parse_price_brazil


@pytest.mark.parametrize("parser", [parse_price_brazil, parse_price])
class TestParsePriceBrazil:
    @pytest.mark.parametrize(
        "raw, esperado",
        [
            ("2339", 2339.0),
            ("12000", 12000.0),
            ("2100", 2100.0),
            ("999", 999.0),
            ("2339.00", 2339.0),
            ("2.339,00", 2339.0),
            ("2339,90", 2339.9),
            ("2.799", 2799.0),
        ],
    )
    def test_inteiro_sem_separador(self, parser, raw, esperado):
        assert parser(raw) == esperado

    @pytest.mark.parametrize(
        "raw, esperado",
        [
            ("1.994,91", 1994.91),
            ("R$ 1.994,91", 1994.91),
            ("13% OFFR$ 1.709,91no pix", 1709.91),
            ("R$ 2.309,90em 10x", 2309.9),
            ("R$ 1829.0", 1829.0),
            ("R$\xa02.184,05", 2184.05),
            ("R$ 2339", 2339.0),
            ("1.520,10", 1520.1),
            ("125.0", 125.0),
        ],
    )
    def test_formatos_existentes(self, parser, raw, esperado):
        assert parser(raw) == esperado

    @pytest.mark.parametrize("raw", ["", None, "sem preço"])
    def test_invalido(self, parser, raw):
        assert parser(raw) is None


def test_json_ld_inteiro_no_coletor_html():
    """O caminho que mordeu: `offers.price` em string inteira no JSON-LD."""
    from bestsellers.sources.html_generic import HtmlBestSellers

    assert HtmlBestSellers._preco({"offers": {"price": "2339"}}) == 2339.0
    assert HtmlBestSellers._preco({"offers": {"lowPrice": "12000"}}) == 12000.0
