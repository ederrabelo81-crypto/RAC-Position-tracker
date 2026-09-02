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
    PEER,
    TIER_MIDEA_LINE,
    TIER_ORDER,
    all_tiers,
    match_haystack,
    pretty_first_token,
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
class ModelResult:
    """Preços de um modelo EXATO do peer — uma linha da lista peer-to-peer.

    Diferente de ``BrandResult`` (que soma todos os modelos de uma marca no
    tier), aqui a chave é o modelo (``model_raw``): uma marca pode ter mais de
    um modelo no mesmo tier/capacidade (ex.: Philco PAC9FC e PAC9FB no
    Low/9K), e cada um vira sua própria linha.
    """

    tier: str
    capacity: str
    brand: str
    model_raw: str                  # grafia crua da célula do peer (chave)
    model_label: str                # primeiro código — rótulo de exibição
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
    peers: PriceStats               # todas as ofertas casadas, exceto Midea
    brands: List[BrandResult]       # quebra por marca, Midea primeiro
    models: List[ModelResult]       # quebra por modelo exato (lista peer-to-peer)

    @property
    def midea_vs_market_delta(self) -> Optional[float]:
        """Modal Midea − modal mercado (negativo = Midea mais barata)."""
        if self.midea.mode is None or self.market.mode is None:
            return None
        return round(self.midea.mode - self.market.mode, 2)

    @property
    def midea_vs_peers_delta(self) -> Optional[float]:
        """Modal Midea − mediana dos peers (negativo = Midea mais barata)."""
        if self.midea.mode is None or self.peers.median is None:
            return None
        return round(self.midea.mode - self.peers.median, 2)


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
    # (tier, cap) -> lista de preços do mercado; (tier, cap, brand) -> preços;
    # (tier, cap, brand, model_raw) -> preços do modelo exato (peer-to-peer).
    market_prices: Dict[tuple, List[float]] = defaultdict(list)
    peer_only_prices: Dict[tuple, List[float]] = defaultdict(list)
    brand_prices: Dict[tuple, List[float]] = defaultdict(list)
    model_prices: Dict[tuple, List[float]] = defaultdict(list)
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
        if not hit.is_midea:
            peer_only_prices[key].append(price)
        brand_prices[(hit.tier, hit.capacity, hit.brand.upper())].append(price)
        model_prices[(hit.tier, hit.capacity, hit.brand.upper(), hit.model_raw)].append(price)
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
            peers = compute_stats(peer_only_prices.get(key, []))

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

            # Lista peer-to-peer: uma linha por modelo EXATO do peer definido
            # para este tier/capacidade — inclui modelos sem oferta casada
            # (stats vazio) para nunca esconder um modelo do contrato, no
            # espírito de `coverage.missing`.
            pt_def = PEER[tier][cap]
            models: List[ModelResult] = []
            for m in pt_def.models:
                stats = compute_stats(model_prices.get((tier, cap, m.brand.upper(), m.raw), []))
                models.append(
                    ModelResult(
                        tier=tier, capacity=cap, brand=m.brand,
                        model_raw=m.raw, model_label=pretty_first_token(m.raw),
                        is_midea=m.is_midea, stats=stats,
                    )
                )
            # Midea primeiro (âncora); peers restantes por modal crescente
            # (convenção do projeto — ver skills/midea-rac-weekly-report).
            models.sort(key=lambda mr: (
                not mr.is_midea,
                mr.stats.mode if mr.stats.mode is not None else float("inf"),
            ))

            tiers.append(
                TierResult(
                    tier=tier,
                    capacity=cap,
                    midea_line=TIER_MIDEA_LINE[tier],
                    market=market,
                    midea=midea,
                    peers=peers,
                    brands=brands,
                    models=models,
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


# ── Série temporal — Midea (moda) × Peers (mediana) ─────────────────────────


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """Um dia da série: modal Midea e mediana dos peers do mesmo tier/cap."""

    date: str
    midea_mode: Optional[float]
    peers_median: Optional[float]
    midea_count: int
    peers_count: int


@dataclass(frozen=True, slots=True)
class TierSeries:
    """Série temporal de um (tier, capacidade) — para o gráfico Midea × Peers."""

    tier: str
    capacity: str
    midea_line: str
    points: List[SeriesPoint]        # ordenado por data crescente

    @property
    def has_data(self) -> bool:
        return any(p.midea_mode is not None for p in self.points)

    def delta_pct(self) -> Optional[float]:
        """Variação % do modal Midea entre o 1º e o último ponto com dado.

        None se houver menos de 2 pontos com dado ou se o primeiro for 0.
        """
        vals = [p.midea_mode for p in self.points if p.midea_mode is not None]
        if len(vals) < 2 or not vals[0]:
            return None
        return round((vals[-1] / vals[0] - 1) * 100, 1)

    def gap_last(self) -> Optional[float]:
        """Midea − mediana dos peers no último dia com AMBOS os dados.

        Negativo = Midea mais barata que a mediana dos peers.
        """
        for p in reversed(self.points):
            if p.midea_mode is not None and p.peers_median is not None:
                return round(p.midea_mode - p.peers_median, 2)
        return None


def daily_series(rows_by_date: Dict[str, Iterable[Offer]]) -> List[TierSeries]:
    """Calcula, para cada (tier, capacidade), a série diária de modal Midea ×
    mediana dos peers (não-Midea).

    Args:
        rows_by_date: ``{data ISO: ofertas daquele dia}``. Um dia ausente do
            dict simplesmente não gera ponto (o gráfico pula o dia sem dado,
            não interpola).

    Reusa ``analyze()`` por dia — mesma classificação/preço de sempre, sem
    duplicar lógica.
    """
    per_tier: Dict[tuple, List[SeriesPoint]] = defaultdict(list)
    for d in sorted(rows_by_date.keys()):
        a = analyze(rows_by_date[d], collection_date=d)
        for tr in a.tiers:
            per_tier[(tr.tier, tr.capacity)].append(
                SeriesPoint(
                    date=d,
                    midea_mode=tr.midea.mode,
                    peers_median=tr.peers.median,
                    midea_count=tr.midea.count,
                    peers_count=tr.peers.count,
                )
            )

    result: List[TierSeries] = []
    for tier in TIER_ORDER:
        for cap in CAP_ORDER:
            result.append(
                TierSeries(
                    tier=tier, capacity=cap, midea_line=TIER_MIDEA_LINE[tier],
                    points=per_tier.get((tier, cap), []),
                )
            )
    return result
