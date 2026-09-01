"""
Fonte de dados do dashboard — hook ao vivo na API do PriceTrack + modo demo.

``fetch_live`` descobre a data de coleta mais recente e puxa as ofertas das
marcas do peer (filtro server-side reduz o volume para bem abaixo do threshold
de export, então a estratégia paginada resolve em segundos). Roda onde há
acesso à API — o PC coletor ou qualquer host com egress liberado para
``api.pricetrack.com.br``.

``demo_offers`` gera uma amostra sintética cobrindo os modelos do peer, para a
página renderizar sem rede (ex.: sandboxes com egress bloqueado). Marcada como
demo na UI — nunca confundir com dado real.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional, Sequence

from pricetrack_api import PriceTrackClient, PriceTrackSettings
from pricetrack_api.exceptions import PriceTrackNoCollectionError
from pricetrack_api.models import CollectQuery, Offer

from .peer import all_tiers


def peer_brands() -> List[str]:
    """Marcas presentes no peer (para o filtro server-side de marca)."""
    brands = {m.brand.upper() for pt in all_tiers() for m in pt.models}
    return sorted(brands)


@dataclass(frozen=True, slots=True)
class LiveResult:
    """Resultado de um pull ao vivo."""

    offers: List[Offer]
    collection_date: Optional[str]
    days_back: int                  # 0 = hoje, 1 = ontem, ...


def find_latest_date(
    client: PriceTrackClient,
    reference: Optional[date] = None,
    max_days_back: int = 10,
) -> Optional[tuple]:
    """Acha a data mais recente com coleta. Retorna (iso, days_back) ou None."""
    reference = reference or date.today()
    for i in range(max_days_back + 1):
        d = reference - timedelta(days=i)
        try:
            total = client.count_offers(CollectQuery(d.isoformat(), take=1))
        except PriceTrackNoCollectionError:
            continue
        except Exception:
            continue
        if total and total > 0:
            return d.isoformat(), i
    return None


def fetch_live(
    settings: Optional[PriceTrackSettings] = None,
    collection_date: Optional[str] = None,
    brands: Optional[Sequence[str]] = None,
    reference: Optional[date] = None,
) -> LiveResult:
    """Puxa ofertas ao vivo da API.

    Args:
        settings: config já montada; se None, ``PriceTrackSettings.from_env()``
            (exige ``PRICETRACK_API_KEY`` no ambiente/secret).
        collection_date: data ISO específica; se None, usa a mais recente.
        brands: filtro de marca (default: as marcas do peer). Passe ``[]``/None
            explícito só se quiser puxar tudo e casar 100% client-side.
        reference: data-base da busca pela mais recente (default hoje) — testes.

    Raises:
        PriceTrackConfigError: key ausente.
        RuntimeError: nenhuma data com coleta encontrada.
    """
    settings = settings or PriceTrackSettings.from_env()
    client = PriceTrackClient(settings)

    days_back = 0
    if collection_date is None:
        found = find_latest_date(client, reference=reference)
        if found is None:
            raise RuntimeError(
                "Nenhuma coleta encontrada nos últimos dias na API do PriceTrack."
            )
        collection_date, days_back = found

    brand_filter = list(brands) if brands is not None else peer_brands()
    query = CollectQuery(
        collection_date,
        product_brand=brand_filter or None,
        status="AVAILABLE",
        take=settings.page_take,
    )
    offers = list(client.iter_offers(query))
    return LiveResult(
        offers=offers, collection_date=collection_date, days_back=days_back
    )


# ── Modo demo (offline) ──────────────────────────────────────────────────────

# Preços-âncora plausíveis por (tier, capacidade) em R$, à vista. Só para a
# amostra sintética renderizar — NÃO são referência de mercado.
_DEMO_ANCHOR = {
    ("Low", "9K"): 1750,
    ("Low", "12K"): 1950,
    ("Mid", "9K"): 2150,
    ("Mid", "12K"): 2450,
    ("High", "9K"): 2650,
    ("High", "12K"): 2990,
}
_DEMO_MARKETPLACES = [
    "MERCADO LIVRE", "AMAZON", "MAGALU", "CASAS BAHIA", "LEROY MERLIN",
]


def demo_offers(seed: int = 42) -> List[Offer]:
    """Amostra sintética cobrindo os modelos do peer (para render offline)."""
    rng = random.Random(seed)
    offers: List[Offer] = []
    oid = 0
    for pt in all_tiers():
        anchor = _DEMO_ANCHOR[(pt.tier, pt.capacity)]
        for model in pt.models:
            code = model.codes[0]
            # Midea um pouco mais barata; concorrentes espalhados em torno da âncora.
            base = anchor * (0.95 if model.is_midea else rng.uniform(0.9, 1.15))
            n_sellers = rng.randint(2, 6)
            for _ in range(n_sellers):
                oid += 1
                price = round(base * rng.uniform(0.93, 1.07), 2)
                offers.append(
                    Offer(
                        id=f"demo-{oid}",
                        sku=code,
                        title=f"Ar Condicionado {model.brand} {pt.capacity} {code}",
                        product_name=f"{model.brand} {pt.midea_line if model.is_midea else ''} {code}".strip(),
                        brand=model.brand,
                        category="AR CONDICIONADO",
                        subcategory="SPLIT",
                        family=pt.midea_line if model.is_midea else "",
                        color=None,
                        marketplace=rng.choice(_DEMO_MARKETPLACES),
                        seller=rng.choice(["WebContinental", "Leveros", "Dufrio", "Midea Store"]),
                        spot_price=price,
                        forward_price=round(price * 1.12, 2),
                        pix_price=round(price * 0.97, 2),
                        price_from=round(price * 1.2, 2),
                        installment_number=10,
                        installment_value=round(price / 10, 2),
                        status="AVAILABLE",
                        collection_date=None,
                        collection_hour=8,
                        image_url="",
                        screenshot_url=None,
                        url="https://example.com/demo",
                    )
                )
    return offers
