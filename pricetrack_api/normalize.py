"""
Normalização de preços das ofertas PriceTrack.

Regras:
  * ``spotPrice``/``forwardPrice``/``pixPrice``/``priceFrom`` são saneados
    individualmente: None, não-numérico ou ≤ 0 viram None (preço ausente) —
    nunca 0.0, que contaminaria mínimos e médias.
  * ``pixPrice`` e ``priceFrom`` são nullable no schema; os demais são
    tratados defensivamente da mesma forma.
  * Ofertas UNAVAILABLE mantêm os preços coletados para histórico, mas
    ``effective_price`` devolve None — indisponível não compete no buy box
    nem entra em mínimos de mercado.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Offer, STATUS_AVAILABLE


def clean_price(value: Optional[float]) -> Optional[float]:
    """Saneia um preço: None/NaN/≤0 → None; senão arredonda a 2 casas."""
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price != price or price <= 0:  # NaN ou não-positivo
        return None
    return round(price, 2)


@dataclass(frozen=True, slots=True)
class NormalizedPrices:
    """Visão normalizada dos preços de uma oferta."""

    spot: Optional[float]           # à vista (cartão)
    forward: Optional[float]        # a prazo
    pix: Optional[float]            # PIX (nullable na API)
    rrp: Optional[float]            # priceFrom — preço "de" (RRP)
    available: bool

    @property
    def best_cash(self) -> Optional[float]:
        """Menor preço à vista efetivo (PIX vs spot)."""
        candidates = [p for p in (self.pix, self.spot) if p is not None]
        return min(candidates) if candidates else None

    @property
    def discount_vs_rrp_pct(self) -> Optional[float]:
        """Desconto % do melhor preço à vista sobre o RRP (priceFrom)."""
        best = self.best_cash
        if best is None or self.rrp is None or self.rrp <= 0:
            return None
        return round((1 - best / self.rrp) * 100, 2)


def normalize_prices(offer: Offer) -> NormalizedPrices:
    """Extrai e saneia os 4 campos de preço de uma oferta."""
    return NormalizedPrices(
        spot=clean_price(offer.spot_price),
        forward=clean_price(offer.forward_price),
        pix=clean_price(offer.pix_price),
        rrp=clean_price(offer.price_from),
        available=offer.status == STATUS_AVAILABLE,
    )


def effective_price(offer: Offer) -> Optional[float]:
    """Preço competitivo da oferta: melhor à vista, e só se AVAILABLE."""
    prices = normalize_prices(offer)
    if not prices.available:
        return None
    return prices.best_cash
