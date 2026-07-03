"""Normalização de preços: nulls, AVAILABLE/UNAVAILABLE, melhor à vista."""
import pytest

from pricetrack_api.models import Offer
from pricetrack_api.normalize import (
    clean_price,
    effective_price,
    normalize_prices,
)

from .conftest import offer_payload


def _offer(**overrides) -> Offer:
    return Offer.from_api(offer_payload(**overrides))


class TestCleanPrice:
    @pytest.mark.parametrize("value,expected", [
        (1999.906, 1999.91),
        (0, None),            # zero não é preço
        (-10.0, None),
        (None, None),
        (float("nan"), None),
        ("1999.90", 1999.90),
        ("abc", None),
    ])
    def test_saneamento(self, value, expected):
        assert clean_price(value) == expected


class TestNormalizePrices:
    def test_todos_os_campos(self):
        prices = normalize_prices(_offer(
            spotPrice=2000.0, forwardPrice=2100.0, pixPrice=1900.0,
            priceFrom=2500.0,
        ))
        assert prices.spot == 2000.0
        assert prices.forward == 2100.0
        assert prices.pix == 1900.0
        assert prices.rrp == 2500.0
        assert prices.available is True

    def test_pix_null_usa_spot_como_melhor_a_vista(self):
        prices = normalize_prices(_offer(spotPrice=2000.0, pixPrice=None))
        assert prices.pix is None
        assert prices.best_cash == 2000.0

    def test_pix_menor_vence(self):
        prices = normalize_prices(_offer(spotPrice=2000.0, pixPrice=1850.0))
        assert prices.best_cash == 1850.0

    def test_sem_nenhum_preco(self):
        prices = normalize_prices(_offer(spotPrice=None, pixPrice=None,
                                         forwardPrice=None, priceFrom=None))
        assert prices.best_cash is None
        assert prices.discount_vs_rrp_pct is None

    def test_desconto_sobre_rrp(self):
        prices = normalize_prices(_offer(spotPrice=2000.0, pixPrice=None,
                                         priceFrom=2500.0))
        assert prices.discount_vs_rrp_pct == 20.0

    def test_preco_zero_tratado_como_ausente(self):
        prices = normalize_prices(_offer(spotPrice=0, pixPrice=0))
        assert prices.spot is None and prices.pix is None
        assert prices.best_cash is None


class TestEffectivePrice:
    def test_available_devolve_melhor_a_vista(self):
        assert effective_price(_offer(spotPrice=2000.0, pixPrice=1900.0)) == 1900.0

    def test_unavailable_devolve_none_mesmo_com_preco(self):
        offer = _offer(status="UNAVAILABLE", spotPrice=1500.0)
        assert offer.is_available is False
        assert effective_price(offer) is None

    def test_status_lowercase_normalizado(self):
        offer = _offer(status="available")
        assert effective_price(offer) is not None
