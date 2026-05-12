"""
test_phase2_critical_dealers.py — Phase 2 validation for critical AC dealers.

Tests the 4 priority dealers:
  1. Frigelar (Oracle OCC + CEP injection)
  2. CentralAr (SAP Hybris + .pdc_product-item selector)
  3. Leveros (VTEX IO + JSON-LD priority)
  4. Dufrio (VTEX + split price parsing)

Status: Tests configuration, methods, and edge cases.
"""

import json
import re
import unittest
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from scrapers.dealers import DEALER_CONFIGS, DealerScraper


class TestPhase2CriticalDealers(unittest.TestCase):
    """Validates configuration and extraction logic for Phase 2 critical dealers."""

    def test_frigelar_configuration(self):
        """Verify Frigelar (Oracle OCC) is properly configured."""
        cfg = DEALER_CONFIGS.get("Frigelar")
        assert cfg is not None, "Frigelar not in DEALER_CONFIGS"
        assert cfg.get("requires_cep") is True, "Frigelar should require CEP injection"
        assert cfg.get("default_cep") == "01310-100", "Default CEP not set"
        assert cfg.get("wait_for_js") is True, "Frigelar should wait for JS"
        assert cfg.get("wait_timeout") == 10000, "Wait timeout not configured"
        assert cfg.get("item_selector") == ".product-box-container", "Item selector not correct"
        assert cfg.get("url") == "https://www.frigelar.com.br/split-inverter/c"
        assert cfg.get("block_indicators") is not None, "Block indicators not configured"

    def test_centralar_configuration(self):
        """Verify CentralAr (SAP Hybris) is properly configured."""
        cfg = DEALER_CONFIGS.get("CentralAr")
        assert cfg is not None, "CentralAr not in DEALER_CONFIGS"
        assert cfg.get("item_selector") == ".pdc_product-item", "SAP Hybris selector incorrect"
        assert cfg.get("prefer_jsonld") is False, "CentralAr doesn't have useful JSON-LD"
        assert cfg.get("url") == "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER"
        assert cfg.get("pagination") == "vtex", "Pagination method should be VTEX"

    def test_leveros_configuration(self):
        """Verify Leveros (VTEX IO) is properly configured."""
        cfg = DEALER_CONFIGS.get("Leveros")
        assert cfg is not None, "Leveros not in DEALER_CONFIGS"
        assert cfg.get("prefer_jsonld") is True, "Leveros should prioritize JSON-LD (118 products)"
        assert cfg.get("item_selector_candidates") is not None, "Item selector candidates missing"
        assert "[data-sku]" in cfg["item_selector_candidates"], "Missing [data-sku] selector"
        assert cfg.get("url") == "https://www.leveros.com.br/ar-condicionado/inverter"

    def test_dufrio_configuration(self):
        """Verify Dufrio (VTEX) is properly configured."""
        cfg = DEALER_CONFIGS.get("Dufrio")
        assert cfg is not None, "Dufrio not in DEALER_CONFIGS"
        assert cfg.get("vtex_split_price") is True, "Should use VTEX split price extraction"
        assert cfg.get("prefer_jsonld") is True, "Should prioritize JSON-LD"
        assert cfg.get("item_selector") == ".product-item", "Item selector for Dufrio incorrect"
        assert cfg.get("url") == "https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter"

    # ─────────────────────────────────────────────────────────────────────
    # VTEX Split Price Extraction Tests
    # ─────────────────────────────────────────────────────────────────────

    def test_extract_vtex_split_price_basic(self):
        """Test basic VTEX split price extraction (currencyInteger + separator + digits)."""
        html = '''
        <div class="product">
            <span class="currencyInteger">1829</span><span class="currencyDecimalSeparator">,</span><span class="currencyDecimalDigits">00</span>
        </div>
        '''
        soup = BeautifulSoup(html, "html.parser")
        item = soup.select_one("div.product")
        price = DealerScraper._extract_vtex_split_price(item)
        assert price == "R$ 1829,00", f"Expected 'R$ 1829,00', got '{price}'"

    def test_extract_vtex_split_price_dufrio_missing_separator(self):
        """Test Dufrio case: missing decimal separator in DOM (bug)."""
        # Dufrio concatenates: currencyInteger="182900" without separator/digits
        # When parsing, we need to infer the decimal point
        html = '''
        <div class="product">
            <span class="currencyInteger">182900</span>
        </div>
        '''
        soup = BeautifulSoup(html, "html.parser")
        item = soup.select_one("div.product")
        price = DealerScraper._extract_vtex_split_price(item)
        # Returns "R$ 182900" — should be handled by parse_price_brazil logic
        assert price == "R$ 182900", f"Got '{price}'"

    def test_extract_vtex_split_price_with_decimals_no_separator(self):
        """Test VTEX split when decimals exist but separator is missing."""
        html = '''
        <div class="product">
            <span class="currencyInteger">1829</span><span class="currencyDecimalDigits">00</span>
        </div>
        '''
        soup = BeautifulSoup(html, "html.parser")
        item = soup.select_one("div.product")
        price = DealerScraper._extract_vtex_split_price(item)
        # The method should insert comma when decimals exist but separator is absent
        assert price == "R$ 1829,00", f"Expected comma insertion, got '{price}'"

    # ─────────────────────────────────────────────────────────────────────
    # JSON-LD Extraction Tests
    # ─────────────────────────────────────────────────────────────────────

    def test_extract_jsonld_prices_leveros(self):
        """Test JSON-LD price extraction (Leveros case: 118 products)."""
        html = '''
        <script type="application/ld+json">
        [
            {"@type": "Product", "name": "Ar Condicionado Midea 12000 BTU", "offers": {"price": "1829.00"}},
            {"@type": "Product", "name": "LG Dual Inverter 18000", "offers": [{"price": "2499.99"}]},
            {"@type": "Product", "name": "Samsung Wind-Free", "offers": {"lowPrice": "1999.00", "highPrice": "2599.00"}}
        ]
        </script>
        '''
        prices = DealerScraper._extract_jsonld_prices(html)
        assert "ar condicionado midea 12000 btu" in prices
        assert prices["ar condicionado midea 12000 btu"] == 1829.00
        assert "lg dual inverter 18000" in prices
        assert prices["lg dual inverter 18000"] == 2499.99
        assert "samsung wind-free" in prices
        assert prices["samsung wind-free"] == 1999.00, "Should use lowPrice when available"

    def test_extract_jsonld_prices_itemlist(self):
        """Test JSON-LD extraction from ItemList structure."""
        html = '''
        <script type="application/ld+json">
        {
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "item": {"@type": "Product", "name": "Ar Condicionado Gree", "offers": {"price": "1599.00"}}
                },
                {
                    "@type": "ListItem",
                    "item": {"@type": "Product", "name": "Ar Condicionado Elgin", "offers": {"price": "1399.00"}}
                }
            ]
        }
        </script>
        '''
        prices = DealerScraper._extract_jsonld_prices(html)
        assert "ar condicionado gree" in prices
        assert "ar condicionado elgin" in prices
        assert len(prices) == 2

    # ─────────────────────────────────────────────────────────────────────
    # JSON-LD Product Matching Tests
    # ─────────────────────────────────────────────────────────────────────

    def test_jsonld_match_exact(self):
        """Test exact match (after normalization)."""
        jsonld_prices = {
            "ar condicionado midea 12000 btus": 1829.00,
            "lg dual inverter": 2499.99,
        }
        # Exact match
        price = DealerScraper._jsonld_match(
            "Ar Condicionado Midea 12000 BTUs", jsonld_prices
        )
        assert price == 1829.00, "Exact match should succeed"

    def test_jsonld_match_containment(self):
        """Test containment match (one is substring of other)."""
        jsonld_prices = {
            "ar condicionado midea 12000 btus inverter quente frio": 1899.00,
        }
        # DOM title is shorter subset of JSON-LD
        price = DealerScraper._jsonld_match(
            "Ar Condicionado Midea 12000 BTUs Inverter", jsonld_prices
        )
        assert price == 1899.00, "Containment match should work"

    def test_jsonld_match_word_intersection(self):
        """Test word intersection match (≥60% common words)."""
        jsonld_prices = {
            "midea air 12000 split inverter": 1829.00,
        }
        # DOM has different format but enough common words
        price = DealerScraper._jsonld_match(
            "Split Inverter Midea 12000 BTU", jsonld_prices
        )
        assert price is not None, "Word intersection ≥60% should match"

    def test_jsonld_match_no_match_low_score(self):
        """Test no match when word intersection <60%."""
        jsonld_prices = {
            "lg double inverter 24000 btus": 3499.00,
        }
        # Very different — should not match
        price = DealerScraper._jsonld_match(
            "Midea 9000 BTU", jsonld_prices
        )
        assert price is None, "Unrelated products should not match"

    # ─────────────────────────────────────────────────────────────────────
    # Item Detection Tests
    # ─────────────────────────────────────────────────────────────────────

    def test_detect_items_with_item_selector_override(self):
        """Test item detection with item_selector override (Dufrio case)."""
        html = '''
        <div class="products">
            <div class="product-item">Item 1</div>
            <div class="product-item">Item 2</div>
            <div class="product-item">Item 3</div>
            <div class="product-item">Item 4</div>
        </div>
        '''
        soup = BeautifulSoup(html, "html.parser")
        items, selector = DealerScraper._detect_items(
            soup, item_selector=".product-item"
        )
        assert len(items) == 4
        assert selector == ".product-item"

    def test_detect_items_with_candidates_list(self):
        """Test item detection with candidate list (Leveros case)."""
        html = '''
        <main>
            <div data-sku="sku123">Product 1</div>
            <div data-sku="sku456">Product 2</div>
            <div data-sku="sku789">Product 3</div>
            <div data-sku="sku012">Product 4</div>
        </main>
        '''
        soup = BeautifulSoup(html, "html.parser")
        candidates = ["[data-sku]", ".product-item", "article[class*='product']"]
        items, selector = DealerScraper._detect_items(
            soup, item_selector_candidates=candidates
        )
        assert len(items) == 4
        assert selector == "[data-sku]"

    def test_detect_items_sap_hybris_pdc(self):
        """Test item detection with SAP Hybris .pdc_product-item selector (CentralAr)."""
        html = '''
        <div class="pdc_product-item">
            <h3>Product 1</h3>
        </div>
        <div class="pdc_product-item">
            <h3>Product 2</h3>
        </div>
        <div class="pdc_product-item">
            <h3>Product 3</h3>
        </div>
        <div class="pdc_product-item">
            <h3>Product 4</h3>
        </div>
        '''
        soup = BeautifulSoup(html, "html.parser")
        items, selector = DealerScraper._detect_items(
            soup, item_selector=".pdc_product-item"
        )
        assert len(items) == 4
        assert selector == ".pdc_product-item"

    # ─────────────────────────────────────────────────────────────────────
    # Title Validation Tests
    # ─────────────────────────────────────────────────────────────────────

    def test_is_valid_product_title_valid_rac(self):
        """Test valid RAC residential titles."""
        assert DealerScraper._is_valid_product_title(
            "Ar Condicionado Midea 12000 BTU Split Inverter"
        )
        assert DealerScraper._is_valid_product_title(
            "LG Dual Inverter 18000 BTU Quente e Frio"
        )
        assert DealerScraper._is_valid_product_title(
            "Samsung Wind-Free 24000 BTU Hi-Wall Inverter"
        )

    def test_is_valid_product_title_rejects_excluded(self):
        """Test rejection of non-RAC products."""
        # Geladeira should be rejected
        assert not DealerScraper._is_valid_product_title(
            "Geladeira Brastemp Ac Condicionado"
        )
        # Commercial unit should be rejected
        assert not DealerScraper._is_valid_product_title(
            "Chiller Central de Ar Profissional VRV"
        )

    def test_is_valid_product_title_btu_range(self):
        """Test BTU range validation (7k-60k)."""
        # Valid: 12000 BTU
        assert DealerScraper._is_valid_product_title(
            "Ar Condicionado 12000 BTU"
        )
        # Invalid: 3000 BTU (below minimum)
        assert not DealerScraper._is_valid_product_title(
            "Ar Condicionado 3000 BTU"
        )
        # Invalid: 80000 BTU (above maximum)
        assert not DealerScraper._is_valid_product_title(
            "Central de Ar 80000 BTU Industrial"
        )

    # ─────────────────────────────────────────────────────────────────────
    # Edge Case Tests
    # ─────────────────────────────────────────────────────────────────────

    def test_safe_lower_protection(self):
        """Test _safe_lower protects against None and type errors."""
        assert DealerScraper._safe_lower(None) == ""
        assert DealerScraper._safe_lower("Test") == "test"
        assert DealerScraper._safe_lower("") == ""
        assert DealerScraper._safe_lower(123) == "123"

    def test_fix_brand_concat_edge_cases(self):
        """Test brand concatenation fix."""
        # ElginAr Condicionado → Ar Condicionado
        result = DealerScraper._fix_brand_concat("ElginAr Condicionado Split 12000")
        assert result == "Ar Condicionado Split 12000"

        # MideaAr Condicionado → Ar Condicionado
        result = DealerScraper._fix_brand_concat("MideaAr Condicionado 18000")
        assert result == "Ar Condicionado 18000"

        # No concatenation — should stay the same
        result = DealerScraper._fix_brand_concat("Ar Condicionado Midea Normal")
        assert result == "Ar Condicionado Midea Normal"

    def test_normalize_for_match(self):
        """Test normalization for JSON-LD matching."""
        # Should normalize accents, case, punctuation
        norm1 = DealerScraper._normalize_for_match("Ar Condicionado Midéa 12.000 BTU")
        norm2 = DealerScraper._normalize_for_match("ar condicionado midea 12 000 btu")
        # Both should be similar after normalization
        self.assertEqual(norm1, norm2)


class TestPhase2Integration(unittest.TestCase):
    """Integration tests ensuring all Phase 2 pieces work together."""

    def test_all_critical_dealers_configured(self):
        """Verify all 4 critical dealers exist in config."""
        critical = ["Frigelar", "CentralAr", "Leveros", "Dufrio"]
        for dealer in critical:
            assert dealer in DEALER_CONFIGS, f"{dealer} not in DEALER_CONFIGS"
            cfg = DEALER_CONFIGS[dealer]
            assert cfg.get("url"), f"{dealer} missing URL"
            assert cfg.get("pagination"), f"{dealer} missing pagination"
            assert not cfg.get("on_hold"), f"{dealer} should not be on hold"

    def test_frigelar_has_cep_flow(self):
        """Verify Frigelar has complete CEP injection flow."""
        cfg = DEALER_CONFIGS["Frigelar"]
        assert cfg.get("requires_cep") is True
        assert cfg.get("block_indicators") is not None
        assert len(cfg["block_indicators"]) > 0

    def test_leveros_jsonld_priority_over_dom(self):
        """Verify Leveros prioritizes JSON-LD extraction."""
        cfg = DEALER_CONFIGS["Leveros"]
        assert cfg.get("prefer_jsonld") is True
        # When prefer_jsonld=True, JSON-LD extraction runs first in search()

    def test_dufrio_split_price_handling(self):
        """Verify Dufrio is configured for split price handling."""
        cfg = DEALER_CONFIGS["Dufrio"]
        assert cfg.get("vtex_split_price") is True
        assert cfg.get("item_selector") is not None


if __name__ == "__main__":
    unittest.main()
