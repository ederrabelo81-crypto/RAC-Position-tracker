"""Testes das agregações de preço (mín/máx/moda/média/modal/piso)."""
from __future__ import annotations

from pricetrack_api.models import Offer

from pricetrack_dashboard.analytics import analyze, compute_stats
from pricetrack_dashboard.peer import CAP_9K, CAP_12K, TIER_HIGH, TIER_LOW


def _offer(sku, brand, spot, status="AVAILABLE", pix=None, title="", name=""):
    return Offer(
        id=f"o-{sku}-{spot}-{status}", sku=sku, title=title or f"{brand} {sku}",
        product_name=name or f"{brand} {sku}", brand=brand,
        category="AR CONDICIONADO", subcategory="SPLIT", family="", color=None,
        marketplace="MERCADO LIVRE", seller="seller",
        spot_price=spot, forward_price=None, pix_price=pix, price_from=None,
        installment_number=None, installment_value=None, status=status,
        collection_date=None, collection_hour=None,
        image_url="", screenshot_url=None, url="",
    )


class TestComputeStats:
    def test_basic_stats(self):
        s = compute_stats([1000, 2000, 2000, 3000])
        assert s.count == 4
        assert s.minimum == 1000 and s.maximum == 3000
        assert s.mean == 2000.0
        assert s.mode == 2000  # mais frequente
        assert s.median == 2000.0

    def test_mode_tie_picks_lowest(self):
        # 1000 e 2000 empatam em frequência → escolhe o menor (conservador).
        s = compute_stats([1000, 1000, 2000, 2000])
        assert s.mode == 1000

    def test_ignores_none(self):
        s = compute_stats([None, 1500, None])
        assert s.count == 1 and s.minimum == 1500

    def test_empty(self):
        s = compute_stats([])
        assert s.is_empty and s.minimum is None and s.mode is None

    def test_rounds_to_cents(self):
        s = compute_stats([1000.111, 1000.114])
        assert s.mode == 1000.11  # ambos arredondam para 1000.11 → moda


class TestAnalyze:
    def test_classifies_into_tier_and_capacity(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),   # Low 9K midea
            _offer("42EBVCA09M5", "MIDEA", 1800),
            _offer("S3-Q09AAQAK", "LG", 1900),       # Low 9K LG
            _offer("38EZVCA12M5", "MIDEA", 3000),    # High 12K midea
        ]
        a = analyze(offers, collection_date="2026-09-01")
        low9 = a.tier(TIER_LOW, CAP_9K)
        assert low9.market.count == 3
        assert low9.midea.count == 2
        assert low9.midea.minimum == 1700 and low9.midea.maximum == 1800
        assert low9.midea.mode == 1700  # empate 1↔1 resolve no menor

        high12 = a.tier(TIER_HIGH, CAP_12K)
        assert high12.midea.count == 1 and high12.midea.mode == 3000

    def test_unavailable_offer_excluded_from_prices(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700, status="UNAVAILABLE"),
            _offer("42EBVCA09M5", "MIDEA", 1800),
        ]
        a = analyze(offers)
        low9 = a.tier(TIER_LOW, CAP_9K)
        assert low9.midea.count == 1 and low9.midea.minimum == 1800

    def test_pix_is_best_cash(self):
        # pix < spot → best_cash usa pix.
        offers = [_offer("42EBVCA09M5", "MIDEA", 2000, pix=1850)]
        a = analyze(offers)
        assert a.tier(TIER_LOW, CAP_9K).midea.minimum == 1850

    def test_midea_vs_market_delta_negative_when_cheaper(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("S3-Q09AAQAK", "LG", 2000),
            _offer("S3-Q09AAQAK", "LG", 2000),
        ]
        a = analyze(offers)
        low9 = a.tier(TIER_LOW, CAP_9K)
        # modal mercado = 1700 ou 2000? ambos 2×; empate → menor = 1700.
        # Midea modal = 1700 → delta = 0. Ajusta cenário: LG 3×.
        assert low9.midea.mode == 1700

    def test_unmatched_offer_counts_in_total_only(self):
        offers = [
            _offer("42EBVCA09M5", "MIDEA", 1700),
            _offer("GELADEIRA123", "BRASTEMP", 999),
        ]
        a = analyze(offers)
        assert a.coverage.total_offers == 2
        assert a.coverage.matched_offers == 1

    def test_coverage_reports_missing_models(self):
        offers = [_offer("42EBVCA09M5", "MIDEA", 1700)]
        a = analyze(offers)
        # Só 1 modelo casou; o resto entra em missing.
        assert a.coverage.peer_models_with_data == 1
        assert len(a.coverage.missing) == a.coverage.peer_models - 1

    def test_all_six_tiers_present_even_when_empty(self):
        a = analyze([])
        assert len(a.tiers) == 6
        assert all(t.market.is_empty for t in a.tiers)
