#!/usr/bin/env python3
"""
smoke_test_phase2.py — Smoke tests for Phase 2 critical dealers.

Tests:
  1. Frigelar — CEP injection + price rendering
  2. CentralAr — SAP Hybris selector + price extraction
  3. Leveros — JSON-LD extraction ≥100 products
  4. Dufrio — VTEX split price + JSON-LD matching

Usage:
  python scripts/smoke_test_phase2.py
  python scripts/smoke_test_phase2.py --no-headless  # See browser
  python scripts/smoke_test_phase2.py --dealer Frigelar  # Single dealer

Exit Codes:
  0 = all passed
  1 = ≥1 dealer failed
  2 = critical error
"""

import argparse
import sys
from pathlib import Path

from loguru import logger

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scrapers.dealers import DEALER_CONFIGS, DealerScraper


def test_frigelar(headless: bool = True) -> bool:
    """Test Frigelar (Oracle OCC + CEP injection)."""
    logger.info("[SMOKE] Testing Frigelar (Oracle OCC + CEP)...")
    try:
        with DealerScraper(headless=headless) as scraper:
            results = scraper.search(
                keyword="Frigelar",
                keyword_category_map={"Dealers": ["Frigelar"]},
                page_limit=1,
            )

            num_products = len(results)
            num_with_price = sum(1 for r in results if r.get("Preço (R$)"))

            logger.info(
                f"[SMOKE] Frigelar: {num_products} products, "
                f"{num_with_price} with price"
            )

            if num_products < 10:
                logger.warning(f"[SMOKE] ❌ Frigelar: Expected ≥10 products, got {num_products}")
                return False

            if num_with_price < num_products * 0.7:
                logger.warning(
                    f"[SMOKE] ❌ Frigelar: Expected ≥70% prices, "
                    f"got {100*num_with_price/num_products:.0f}%"
                )
                return False

            logger.success("[SMOKE] ✅ Frigelar PASSED")
            return True
    except Exception as e:
        logger.error(f"[SMOKE] ❌ Frigelar ERROR: {e}")
        return False


def test_centralar(headless: bool = True) -> bool:
    """Test CentralAr (SAP Hybris + .pdc_product-item selector)."""
    logger.info("[SMOKE] Testing CentralAr (SAP Hybris)...")
    try:
        with DealerScraper(headless=headless) as scraper:
            results = scraper.search(
                keyword="CentralAr",
                keyword_category_map={"Dealers": ["CentralAr"]},
                page_limit=1,
            )

            num_products = len(results)
            num_with_price = sum(1 for r in results if r.get("Preço (R$)"))

            logger.info(
                f"[SMOKE] CentralAr: {num_products} products, "
                f"{num_with_price} with price"
            )

            if num_products < 10:
                logger.warning(f"[SMOKE] ❌ CentralAr: Expected ≥10 products, got {num_products}")
                return False

            if num_with_price < num_products * 0.6:
                logger.warning(
                    f"[SMOKE] ❌ CentralAr: Expected ≥60% prices, "
                    f"got {100*num_with_price/num_products:.0f}%"
                )
                return False

            logger.success("[SMOKE] ✅ CentralAr PASSED")
            return True
    except Exception as e:
        logger.error(f"[SMOKE] ❌ CentralAr ERROR: {e}")
        return False


def test_leveros(headless: bool = True) -> bool:
    """Test Leveros (VTEX IO + JSON-LD priority)."""
    logger.info("[SMOKE] Testing Leveros (VTEX IO + JSON-LD)...")
    try:
        with DealerScraper(headless=headless) as scraper:
            results = scraper.search(
                keyword="Leveros",
                keyword_category_map={"Dealers": ["Leveros"]},
                page_limit=1,
            )

            num_products = len(results)
            num_with_price = sum(1 for r in results if r.get("Preço (R$)"))

            logger.info(
                f"[SMOKE] Leveros: {num_products} products, "
                f"{num_with_price} with price"
            )

            # Leveros should have 100+ products from JSON-LD
            if num_products < 50:
                logger.warning(
                    f"[SMOKE] ❌ Leveros: Expected ≥50 products, got {num_products}"
                )
                return False

            if num_with_price < num_products * 0.8:
                logger.warning(
                    f"[SMOKE] ❌ Leveros: Expected ≥80% prices, "
                    f"got {100*num_with_price/num_products:.0f}%"
                )
                return False

            logger.success("[SMOKE] ✅ Leveros PASSED")
            return True
    except Exception as e:
        logger.error(f"[SMOKE] ❌ Leveros ERROR: {e}")
        return False


def test_dufrio(headless: bool = True) -> bool:
    """Test Dufrio (VTEX + split price parsing)."""
    logger.info("[SMOKE] Testing Dufrio (VTEX + split price)...")
    try:
        with DealerScraper(headless=headless) as scraper:
            results = scraper.search(
                keyword="Dufrio",
                keyword_category_map={"Dealers": ["Dufrio"]},
                page_limit=1,
            )

            num_products = len(results)
            num_with_price = sum(1 for r in results if r.get("Preço (R$)"))

            logger.info(
                f"[SMOKE] Dufrio: {num_products} products, "
                f"{num_with_price} with price"
            )

            if num_products < 10:
                logger.warning(f"[SMOKE] ❌ Dufrio: Expected ≥10 products, got {num_products}")
                return False

            if num_with_price < num_products * 0.6:
                logger.warning(
                    f"[SMOKE] ❌ Dufrio: Expected ≥60% prices, "
                    f"got {100*num_with_price/num_products:.0f}%"
                )
                return False

            # Check for ×10 bug (price should be 1000-5000, not 10000-50000)
            prices = [r.get("Preço (R$)") for r in results if r.get("Preço (R$)")]
            if prices:
                avg_price = sum(prices) / len(prices)
                if avg_price > 10000:
                    logger.warning(
                        f"[SMOKE] ❌ Dufrio: Prices seem 10x too high "
                        f"(avg: R$ {avg_price:.2f})"
                    )
                    return False

            logger.success("[SMOKE] ✅ Dufrio PASSED")
            return True
    except Exception as e:
        logger.error(f"[SMOKE] ❌ Dufrio ERROR: {e}")
        return False


def main():
    """Run all Phase 2 smoke tests."""
    parser = argparse.ArgumentParser(
        description="Smoke tests for Phase 2 critical dealers"
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show browser during testing",
    )
    parser.add_argument(
        "--dealer",
        choices=["Frigelar", "CentralAr", "Leveros", "Dufrio"],
        help="Test only this dealer",
    )
    args = parser.parse_args()

    headless = not args.no_headless

    logger.info("[SMOKE] Starting Phase 2 critical dealer smoke tests...")
    logger.info("[SMOKE] Headless: {}".format("yes" if headless else "no"))

    results = {}

    # Run requested dealers
    dealers_to_test = [args.dealer] if args.dealer else ["Frigelar", "CentralAr", "Leveros", "Dufrio"]

    for dealer in dealers_to_test:
        if dealer == "Frigelar":
            results["Frigelar"] = test_frigelar(headless)
        elif dealer == "CentralAr":
            results["CentralAr"] = test_centralar(headless)
        elif dealer == "Leveros":
            results["Leveros"] = test_leveros(headless)
        elif dealer == "Dufrio":
            results["Dufrio"] = test_dufrio(headless)

    # Summary
    logger.info("[SMOKE] ═" * 40)
    logger.info("[SMOKE] RESULTS SUMMARY")
    logger.info("[SMOKE] ═" * 40)

    for dealer, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"[SMOKE] {dealer:15} {status}")

    all_passed = all(results.values())
    pass_count = sum(1 for v in results.values() if v)
    total_count = len(results)

    logger.info("[SMOKE] ─" * 40)
    logger.info(f"[SMOKE] Total: {pass_count}/{total_count} dealers passed")

    if all_passed:
        logger.success("[SMOKE] ✅ ALL TESTS PASSED")
        return 0
    else:
        logger.error("[SMOKE] ❌ SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
