"""
Analytics do dashboard PriceTrack — classifica ofertas no peer e agrega preços.

Funções puras sobre uma coleção de ``Offer`` (ou dicts crus da API). Toda a
matemática de preço fica aqui, sem rede e sem Streamlit, para ser testável.

Duas saídas, espelhando o pedido:

1. **Tiers Low/Mid/High** (por capacidade): modal (moda) e piso (mínimo) do
   mercado da faixa + quebra por marca, no espírito do briefing diário.
2. **Variação Midea** (por capacidade × linha): mínimo, máximo, moda e média
   dos preços Midea daquele modelo.

Regra de preço: usa ``effective_price`` (melhor à vista, só se AVAILABLE) —
oferta indisponível não entra em mínimo nem em média (senão o piso mente).
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from pricetrack_api.models import Offer
from pricetrack_api.normalize import effective_price

from .peer import (
    CAP_ORDER,
    TIER_MIDEA_LINE,
    TIER_ORDER,
    all_tiers,
    match_haystack,
)


@dataclass(frozen=True, slots=True)
class PriceStats:
    """Estatísticas de um conjunto de preços à vista (R$)."""

    count: int
    minimum: Optional[float]        # piso
    maximum: Optional[float]
    mean: Optional[float]           # média
    mode: Optional[float]           # modal (preço mais frequente)
    median: Optional[float]

    @property
    def is_empty(self) -> bool:
        return self.count == 0

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.mean,
            "mode": self.mode,
            "median": self.median,
        }


def compute_stats(prices: Iterable[float]) -> PriceStats:
    """Agrega uma lista de preços em ``PriceStats``.

    Moda: preço mais frequente após arredondar a centavos; empate resolve pelo
    **menor** preço (determinístico e conservador — não superestima o modal).
    """
    values = [round(float(p), 2) for p in prices if p is not None]
    if not values:
        return PriceStats(0, None, None, None, None, None)

    counter = Counter(values)
    top = max(counter.values())
    mode = min(price for price, freq in counter.items() if freq == top)

    return PriceStats(
        count=len(values),
        minimum=min(values),
        maximum=max(values),
        mean=round(statistics.fmean(values), 2),
        mode=mode,
        median=round(statistics.median(values), 2),
    )


@dataclass(frozen=True, slots=True)
class BrandResult:
    """Preços de uma marca dentro de um (tier, capacidade)."""

    brand: str
    is_midea: bool
    stats: PriceStats


@dataclass(frozen=True, slots=True)
class TierResult:
    """Resultado agregado de um (tier, capacidade)."""

    tier: str                       # Low | Mid | High
    capacity: str                   # 9K | 12K
    midea_line: str                 # Inverter Lite | AI AirVolution | AI Ecomaster
    market: PriceStats              # todas as ofertas casadas no peer da faixa
    midea: PriceStats               # só Midea
    brands: List[BrandResult]       # quebra por marca, Midea primeiro

    @property
    def midea_vs_market_delta(self) -> Optional[float]:
        """Modal Midea − modal mercado (negativo = Midea mais barata)."""
        if self.midea.mode is None or self.market.mode is None:
            return None
        return round(self.midea.mode - self.market.mode, 2)


@dataclass(frozen=True, slots=True)
class Coverage:
    """Diagnóstico de casamento: quantos modelos do peer tiveram oferta."""

    matched_offers: int             # ofertas casadas no peer
    total_offers: int               # ofertas vistas (após filtro de preço)
    peer_models: int                # modelos no peer (9K/12K, 3 tiers)
    peer_models_with_data: int      # modelos que casaram ao menos 1 oferta
    missing: List[str]              # "Tier Cap Marca (raw)" sem nenhuma oferta


@dataclass(frozen=True, slots=True)
class Analysis:
    """Saída completa consumida pela UI."""

    collection_date: Optional[str]
    tiers: List[TierResult]
    coverage: Coverage

    def tier(self, tier: str, capacity: str) -> Optional[TierResult]:
        for tr in self.tiers:
            if tr.tier == tier and tr.capacity == capacity:
                return tr
        return None


def analyze(
    offers: Iterable[Offer],
    collection_date: Optional[str] = None,
) -> Analysis:
    """Classifica ofertas no peer e devolve tiers + variação Midea + cobertura.

    Cada oferta é casada por código de modelo (``peer.match_haystack``). Ofertas
    que não casam nenhum modelo do peer são ignoradas nas estatísticas (mas
    contam em ``coverage.total_offers``).
    """
    # (tier, cap) -> lista de preços do mercado; (tier, cap, brand) -> preços
    market_prices: Dict[tuple, List[float]] = defaultdict(list)
    brand_prices: Dict[tuple, List[float]] = defaultdict(list)
    matched_offers = 0
    total_offers = 0
    models_with_data: set = set()

    for offer in offers:
        total_offers += 1
        price = effective_price(offer)
        if price is None:
            continue
        hit = match_haystack(offer.sku, offer.title, offer.product_name)
        if hit is None:
            continue
        matched_offers += 1
        key = (hit.tier, hit.capacity)
        market_prices[key].append(price)
        brand_prices[(hit.tier, hit.capacity, hit.brand.upper())].append(price)
        models_with_data.add(hit.code)

    # Modelos do peer e quais casaram
    peer_models = 0
    missing: List[str] = []
    for pt in all_tiers():
        for m in pt.models:
            peer_models += 1
            if not any(code in models_with_data for code in m.codes):
                missing.append(f"{pt.tier} {pt.capacity} {m.brand} ({m.raw})")

    tiers: List[TierResult] = []
    for tier in TIER_ORDER:
        for cap in CAP_ORDER:
            key = (tier, cap)
            market = compute_stats(market_prices.get(key, []))
            midea = compute_stats(brand_prices.get((tier, cap, "MIDEA"), []))

            brands: List[BrandResult] = []
            seen_brands = {
                b for (t, c, b) in brand_prices if t == tier and c == cap
            }
            for brand in sorted(seen_brands, key=lambda b: (b != "MIDEA", b)):
                stats = compute_stats(brand_prices[(tier, cap, brand)])
                brands.append(
                    BrandResult(
                        brand=brand,
                        is_midea=(brand == "MIDEA"),
                        stats=stats,
                    )
                )

            tiers.append(
                TierResult(
                    tier=tier,
                    capacity=cap,
                    midea_line=TIER_MIDEA_LINE[tier],
                    market=market,
                    midea=midea,
                    brands=brands,
                )
            )

    coverage = Coverage(
        matched_offers=matched_offers,
        total_offers=total_offers,
        peer_models=peer_models,
        peer_models_with_data=len(
            [1 for pt in all_tiers() for m in pt.models
             if any(code in models_with_data for code in m.codes)]
        ),
        missing=missing,
    )
    return Analysis(collection_date=collection_date, tiers=tiers, coverage=coverage)
