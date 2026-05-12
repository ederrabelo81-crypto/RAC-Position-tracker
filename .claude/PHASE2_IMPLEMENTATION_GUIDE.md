# Phase 2 Implementation Guide — Critical AC Dealers

**Status:** Ready for Testing  
**Date:** 2026-05-12  
**Branch:** `claude/validate-ac-dealer-scraping-poMKt`

---

## Overview

Phase 2 implements scraping for the 4 critical AC dealers, addressing their specific WAF blocks, rendering requirements, and data extraction patterns:

| Dealer | Priority | Issue | Solution | Status |
|--------|----------|-------|----------|--------|
| **Frigelar** | 🔴 Critical | Oracle OCC + CEP validation | Playwright + CEP injection | Ready |
| **CentralAr** | 🔴 Critical | SAP Hybris + Akamai WAF | Playwright + `.pdc_product-item` selector | Ready |
| **Leveros** | 🔴 Critical | VTEX IO + 118 JSON-LD products | Playwright + JSON-LD priority | Ready |
| **Dufrio** | 🔴 Critical | VTEX split price + preço concatenado | Playwright + split price extraction | Ready |

---

## Configuration Summary

### 1. Frigelar (Oracle OCC + CEP Injection)

**URL:** `https://www.frigelar.com.br/split-inverter/c`

**Key Config:**
```python
DEALER_CONFIGS["Frigelar"] = {
    "url": "https://www.frigelar.com.br/split-inverter/c",
    "pagination": "vtex",
    "requires_cep": True,           # ← Requires CEP for price access
    "default_cep": "01310-100",     # ← Av. Paulista default
    "item_selector": ".product-box-container",  # ← OCC container
    "wait_for_js": True,            # ← Oracle OCC uses Knockout.js
    "wait_timeout": 10000,          # ← 10s for JS rendering
    "block_indicators": [
        "Valide seu acesso",
        "Insira um CEP do Brasil",
        "Código de acesso expirado",
    ],
}
```

**Extraction Flow:**
1. Playwright loads URL with `wait_until="networkidle"`
2. Detects CEP prompt via block_indicators
3. Calls `_inject_cep("01310-100")` to fill & submit
4. Awaits price reload via `_wait_for_prices()`
5. Extracts via VTEX `__RUNTIME__` or DOM `.product-box-container`

**Testing:**
```bash
# Test CEP injection method (unit)
python test_phase2_critical_dealers.py TestPhase2CriticalDealers

# Test full flow with visible browser
python main.py --platforms dealers --no-headless --dealer-name Frigelar
```

---

### 2. CentralAr (SAP Hybris + .pdc_product-item)

**URL:** `https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER`

**Key Config:**
```python
DEALER_CONFIGS["CentralAr"] = {
    "url": "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER",
    "pagination": "vtex",
    "item_selector": ".pdc_product-item",  # ← SAP Hybris selector
    "prefer_jsonld": False,  # ← JSON-LD is Organization, not Product
}
```

**Extraction Flow:**
1. Playwright loads URL with `wait_until="domcontentloaded"`
2. No CEP required (national coverage)
3. Calls `_wait_for_products()` with `.pdc_product-item` selector
4. Detects ~20 items in DOM
5. Extracts via CSS selectors (no VTEX `__RUNTIME__`)
6. Fallback to JSON-LD for prices (minimal data available)

**Testing:**
```bash
python test_phase2_critical_dealers.py TestPhase2Integration.test_all_critical_dealers_configured
```

**Note:** CentralAr's .pdc_product-item selector is platform-specific (SAP Hybris). If redesign changes selectors, add to `item_selector_candidates` list.

---

### 3. Leveros (VTEX IO + JSON-LD Priority)

**URL:** `https://www.leveros.com.br/ar-condicionado/inverter`

**Key Config:**
```python
DEALER_CONFIGS["Leveros"] = {
    "url": "https://www.leveros.com.br/ar-condicionado/inverter",
    "pagination": "vtex",
    "prefer_jsonld": True,  # ← JSON-LD as primary (118 products detected)
    "item_selector_candidates": [
        "[data-sku]",  # ← Primary selector
        "main [class*='product-item']",
        ".products-grid [class*='product-item']",
        # ... fallbacks for redesigns
    ],
}
```

**Extraction Flow:**
1. Playwright loads URL
2. Calls `_extract_jsonld_products()` first
3. Parses JSON-LD `<script type="application/ld+json">` tags
4. Extracts 118 Product objects with prices
5. Fallback to DOM with `[data-sku]` selector if JSON-LD empty

**Testing:**
```bash
# Unit test JSON-LD extraction
python -c "from test_phase2_critical_dealers import *; \
    t = TestPhase2CriticalDealers(); \
    t.test_extract_jsonld_prices_leveros(); \
    t.test_jsonld_match_exact(); \
    print('✅ Leveros JSON-LD tests pass')"
```

**Why JSON-LD Priority:** Leveros' DOM has 775+ elements (UI noise) but JSON-LD has clean 118 products. This avoids false positives and improves accuracy.

---

### 4. Dufrio (VTEX + Split Price)

**URL:** `https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter`

**Key Config:**
```python
DEALER_CONFIGS["Dufrio"] = {
    "url": "https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter",
    "pagination": "vtex",
    "vtex_split_price": True,  # ← Use split price extraction
    "prefer_jsonld": True,     # ← JSON-LD as backup (21 products)
    "item_selector": ".product-item",  # ← 42 items in DOM
}
```

**Key Issue:** Dufrio's VTEX layout splits price into 3 elements:
- `<span class="currencyInteger">1829</span>` (1829)
- `<span class="currencyDecimalSeparator">,</span>` (comma) — **often missing!**
- `<span class="currencyDecimalDigits">00</span>` (00)

**Extraction Flow:**
1. Playwright loads URL
2. Calls `_extract_vtex_split_price()` in DOM parsing
3. If separator missing, **inserts comma automatically** (`1829` + `,` + `00` → `1829,00`)
4. Fallback to JSON-LD matching for prices
5. Final fallback: index-based matching (Dufrio: 42 DOM items ≈ 21 JSON-LD products)

**Testing:**
```bash
python -c "from test_phase2_critical_dealers import *; \
    t = TestPhase2CriticalDealers(); \
    t.test_extract_vtex_split_price_dufrio_missing_separator(); \
    t.test_extract_vtex_split_price_with_decimals_no_separator(); \
    print('✅ Dufrio split price tests pass')"
```

---

## Running Phase 2 Tests

### Unit Tests (Fast, No Network)
```bash
# All 26 unit tests
python test_phase2_critical_dealers.py

# Specific test class
python test_phase2_critical_dealers.py TestPhase2CriticalDealers

# Specific test
python test_phase2_critical_dealers.py TestPhase2CriticalDealers.test_frigelar_configuration
```

### Integration Tests (Live, Network Required)

```bash
# Single dealer
python main.py --platforms dealers --dealer-name Frigelar --pages 1

# All critical dealers
for dealer in Frigelar CentralAr Leveros Dufrio; do
    echo "Testing $dealer..."
    python main.py --platforms dealers --dealer-name "$dealer" --pages 1
done

# With visible browser (debug)
python main.py --platforms dealers --no-headless --dealer-name Frigelar --pages 1
```

### Performance Baseline
Expected times per dealer (single page, no headless):
- **Frigelar:** 45-120s (CEP injection adds 10-20s)
- **CentralAr:** 30-60s
- **Leveros:** 25-50s (JSON-LD priority saves time)
- **Dufrio:** 35-70s

---

## Validation Checklist

Before marking Phase 2 complete, validate:

- [ ] Frigelar: ≥10 products, 100% with prices, CEP injection triggers
- [ ] CentralAr: ≥15 products, <20% without prices
- [ ] Leveros: ≥90 products (JSON-LD source), 100% with prices
- [ ] Dufrio: ≥30 products, split price parsing works (no ×10 bug)
- [ ] All 26 unit tests pass: `python test_phase2_critical_dealers.py`
- [ ] No WAF blocks detected (check logs for "Blocked by")
- [ ] No duplicate products after dedup
- [ ] CSV export columns: all required fields present

---

## Troubleshooting Phase 2

### Frigelar: CEP Injection Fails
**Symptom:** "CEP injection failed" in logs, 0 products extracted  
**Solution:**
1. Check CEP input selector: `input[placeholder*="CEP" i]` or `input[name*="cep" i]`
2. Run with `--no-headless` to see prompt visually
3. Verify default CEP "01310-100" is valid (try different CEP in config)
4. Check if page layout changed (Frigelar may have redesigned)

### CentralAr: No Products Found
**Symptom:** 0 products, but page loads fine  
**Solution:**
1. Verify `.pdc_product-item` still exists (SAP Hybris may update selectors)
2. Check if Akamai bot-manager is detecting Playwright (add longer delays)
3. Fallback: add more selectors to detection chain

### Leveros: JSON-LD Empty, DOM Has Junk
**Symptom:** JSON-LD empty, DOM fallback returns 775 items (noise)  
**Solution:**
1. Verify `[data-sku]` selector still targets products (not UI)
2. Check `_MIN_ITEMS=3` and `max_items=120` bounds (may filter valid products)
3. Add specific selectors to `item_selector_candidates` for next redesign

### Dufrio: ×10 Price Bug
**Symptom:** Prices like 18290.0 instead of 1829.0  
**Solution:**
1. Ensure `_extract_vtex_split_price()` inserts comma for missing separator
2. Use `price_float` parameter in `_build_record()` to pass float directly
3. Never use `parse_price_brazil("1829.0")` — returns 18290.0 (dot as thousands separator)

---

## Phase 3 Preview

After Phase 2 stabilizes (48+ hours production):

**Phase 3 Dealers** (7 VTEX standard + 2 custom):
- WebContinental, FrioPecas, Climario (VTEX standard)
- PoloAr (Custom search + XHR prices)
- GoCompras (WooCommerce)
- ArCerto (WooCommerce + Cloudflare)
- NorteRefrigeração (Custom layout)

---

## References

- **Diagnostic:** `.claude/DEALERS_DIAGNOSTICO_MAIO_2026.md`
- **Solutions:** `.claude/DEALERS_SOLUCOES_TECNICAS.md`
- **Base Scraper:** `scrapers/base.py` (Playwright lifecycle, stealth JS)
- **Dealer Scraper:** `scrapers/dealers.py` (platform-specific extractors)
- **Unit Tests:** `test_phase2_critical_dealers.py` (26 tests, all critical methods)

---

**Next Step:** Run full Phase 2 integration test on all 4 dealers, validate CSV output, prepare Phase 3 planning.
