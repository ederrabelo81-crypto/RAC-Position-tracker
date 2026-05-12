# Phase 2 Implementation Guide — Critical AC Dealers

**Status:** Implementation Phase 2 (Critical Dealers)  
**Date:** 2026-05-12  
**Target Dealers:** Frigelar, CentralAr, Leveros, Dufrio

---

## Overview

Phase 2 focuses on implementing and validating scraping for the 4 highest-priority AC dealers. Each dealer has unique platform characteristics requiring specific handling:

| Dealer | Platform | Issue | Solution |
|--------|----------|-------|----------|
| **Frigelar** | Oracle OCC | CEP required; JS rendering | Playwright + CEP injection |
| **CentralAr** | SAP Hybris | .pdc_product-item selector | Custom DOM parsing |
| **Leveros** | VTEX IO | 118 products in JSON-LD | JSON-LD priority extraction |
| **Dufrio** | VTEX | Split price (182900 → 1829,00) | VTEX split parsing + JSON-LD |

---

## Implementation Checklist

### ✅ Phase 2.0: Configuration Validation (DONE)

- [x] `DEALER_CONFIGS` updated with all critical dealer configs
- [x] Frigelar: `requires_cep=True`, `default_cep="01310-100"`, `wait_for_js=True`
- [x] CentralAr: `item_selector=".pdc_product-item"`, `prefer_jsonld=False`
- [x] Leveros: `prefer_jsonld=True`, `item_selector_candidates` with `[data-sku]`
- [x] Dufrio: `vtex_split_price=True`, `prefer_jsonld=True`, `item_selector=".product-item"`
- [x] 26 unit tests created and passing

### 🔲 Phase 2.1: Frigelar (Oracle OCC) — 3 days

**Acceptance Criteria:**
- [ ] Scrape ≥10 products/page without WAF block
- [ ] CEP injection triggers and unlocks prices
- [ ] VTEX __RUNTIME__ extraction works OR fallback DOM parsing succeeds
- [ ] Output: 15-30 products minimum per collection

**Implementation Steps:**

1. **CEP Flow Validation**
   ```bash
   # Test CEP detection and injection
   python -c "
   from scrapers.dealers import DealerScraper
   s = DealerScraper()
   s._launch()
   s._page.goto('https://www.frigelar.com.br/split-inverter/c')
   s._page.wait_for_timeout(3000)
   # Check if CEP prompt appears
   html = s._page.content()
   if 'CEP' in html or 'cep' in html.lower():
       print('✅ CEP prompt detected')
       s._inject_cep('01310-100')
   s._close()
   "
   ```

2. **Price Rendering**
   - Wait for `.vtex-product-price-1-x-sellingPriceValue` or `[class*='sellingPrice']`
   - Verify VTEX __RUNTIME__ accessible via `window.__RUNTIME__`
   - If not, fallback to DOM with `_SELECTORS["price_candidates"]`

3. **Test Locally**
   ```bash
   python main.py --platforms dealers --no-headless --keywords "Frigelar"
   ```

4. **Validation Metrics**
   - Products extracted: ≥15
   - Average price: R$ 1000–5000 (realistic for AC units)
   - No junk titles (UI elements, buttons)

---

### 🔲 Phase 2.2: CentralAr (SAP Hybris) — 2-3 days

**Acceptance Criteria:**
- [ ] `.pdc_product-item` selector finds 15-25 products
- [ ] Prices extracted via VTEX JSON-LD or DOM fallback
- [ ] Output: 20-40 products minimum per collection

**Implementation Steps:**

1. **Selector Validation**
   ```bash
   python -c "
   from scrapers.dealers import DEALER_CONFIGS, DealerScraper
   from bs4 import BeautifulSoup
   
   # Would need actual HTML from CentralAr to test
   cfg = DEALER_CONFIGS['CentralAr']
   item_sel = cfg.get('item_selector')
   print(f'CentralAr item selector: {item_sel}')
   # Should be: .pdc_product-item
   "
   ```

2. **Price Extraction**
   - Try VTEX split price extraction (currencyInteger + separator + digits)
   - Fallback to DOM seletores: `[class*='price']`, `[data-price]`, etc.
   - **NOTE:** CentralAr JSON-LD is Organization, not Product → skip prefer_jsonld

3. **Test Locally**
   ```bash
   python main.py --platforms dealers --no-headless --keywords "CentralAr"
   ```

4. **Validation Metrics**
   - Products extracted: ≥20
   - Price found: ≥80% of products
   - No duplicate titles (dedup working)

---

### 🔲 Phase 2.3: Leveros (VTEX IO) — 1-2 days

**Acceptance Criteria:**
- [ ] JSON-LD extraction returns 100+ products
- [ ] DOM parsing fallback captures remaining products
- [ ] Output: 100-150 products minimum per collection

**Implementation Steps:**

1. **JSON-LD Extraction**
   ```bash
   python -c "
   from scrapers.dealers import DealerScraper
   
   # Mock HTML with 118 products in JSON-LD (per diagnostic)
   html_sample = '''<script type=\"application/ld+json\">[
   {\"@type\": \"Product\", \"name\": \"Ar Condicionado XYZ\", \"offers\": {\"price\": \"1829.00\"}},
   ...
   ]</script>'''
   
   prices = DealerScraper._extract_jsonld_prices(html_sample)
   print(f'JSON-LD prices extracted: {len(prices)}')
   # Should be ≥100
   "
   ```

2. **DOM Fallback (if prefer_jsonld extraction missing items)**
   - Use `item_selector_candidates` with `[data-sku]` primary selector
   - Fallback chain: main → grid → custom VTEX selectors

3. **Test Locally**
   ```bash
   python main.py --platforms dealers --no-headless --keywords "Leveros"
   ```

4. **Validation Metrics**
   - JSON-LD products: ≥100
   - DOM products (if fallback): ≥50
   - Total: ≥100
   - No price mismatches (matching by name + position)

---

### 🔲 Phase 2.4: Dufrio (VTEX + Split Price) — 2 days

**Acceptance Criteria:**
- [ ] Split price extraction handles "182900" → "1829,00" correctly
- [ ] JSON-LD as primary source (prefer_jsonld=True)
- [ ] Output: 20-40 products minimum per collection

**Implementation Steps:**

1. **Split Price Validation**
   ```bash
   python -c "
   from scrapers.dealers import DealerScraper
   from bs4 import BeautifulSoup
   
   # Dufrio example: currencyInteger=182900 (concatenated)
   html = '''
   <div class=\"product-item\">
     <span class=\"currencyInteger\">182900</span>
   </div>
   '''
   
   soup = BeautifulSoup(html, 'html.parser')
   item = soup.select_one('.product-item')
   price = DealerScraper._extract_vtex_split_price(item)
   print(f'Split price result: {price}')
   # Should be: R$ 182900 (or with inserted comma if decimals missing)
   "
   ```

2. **JSON-LD Matching**
   - Extract JSON-LD prices
   - Match DOM titles with JSON-LD via `_jsonld_match()` (word intersection ≥60%)
   - Fallback: assign prices by index if matching fails

3. **Test Locally**
   ```bash
   python main.py --platforms dealers --no-headless --keywords "Dufrio"
   ```

4. **Validation Metrics**
   - Products extracted: ≥20
   - Prices filled: ≥80%
   - Price range realistic: R$ 1000–5000 (not ×10 bug)

---

## Testing Strategy

### Unit Tests (Already Passing)
- Run `python test_phase2_critical_dealers.py` to validate configuration
- All 26 tests should pass (split price, JSON-LD, matching, etc.)

### Smoke Tests (To Create)
```bash
python scripts/smoke_test_phase2.py
```

Should test:
1. Frigelar: CEP injection flow + price rendering
2. CentralAr: .pdc_product-item detection + price extraction
3. Leveros: JSON-LD extraction ≥100 products
4. Dufrio: Split price parsing + JSON-LD matching

### Integration Test (Full Collection)
```bash
python main.py --platforms dealers --pages 2 --keywords "Frigelar,CentralAr,Leveros,Dufrio"
```

Expected output:
- 4 dealers × 15–150 products each = 60–600 total products
- CSV saved: `output/rac_monitoramento_YYYY-MM-DD_HHMMSS.csv`
- Supabase upload succeeds
- Telegram notification sent

---

## Common Issues & Fixes

### Frigelar Issues

**Issue:** CEP injection not triggering
- **Check:** Is `block_indicators` including CEP prompts?
- **Fix:** Add to `block_indicators`: `["Valide seu acesso", "Insira um CEP do Brasil"]`

**Issue:** Prices still not visible after CEP
- **Check:** Is `_wait_for_prices()` being called post-injection?
- **Fix:** Already implemented; verify `wait_for_js=True` is set

---

### CentralAr Issues

**Issue:** 0 products found with `.pdc_product-item`
- **Check:** Run DevTools inspector on centralar.com.br → confirm selector exists
- **Fix:** Update `item_selector` if SAP Hybris structure changed

**Issue:** Prices empty for all products
- **Check:** SAP Hybris may use different price structure than VTEX
- **Fix:** Add custom price selector to `_SELECTORS["price_candidates"]` for SAP

---

### Leveros Issues

**Issue:** JSON-LD extraction returns 0 products
- **Check:** Is `<script type="application/ld+json">` present in HTML?
- **Fix:** Fallback to DOM with `item_selector_candidates`

**Issue:** Price matching fails (high unmatched rate)
- **Check:** Are titles in JSON-LD vs. DOM similar enough (≥60% word overlap)?
- **Fix:** Lower matching threshold or use index-based fallback

---

### Dufrio Issues

**Issue:** Prices 10x too high (1829 → 18290)
- **Check:** Is split price method returning string without comma?
- **Fix:** `_extract_vtex_split_price()` should insert comma; pass `price_float` (not `price_raw`) to avoid parse_price bug

**Issue:** Split price returns None
- **Check:** Does DOM have `currencyInteger` element?
- **Fix:** Fallback to regex extraction or JSON-LD matching

---

## Success Criteria

**End of Phase 2:**
- ✅ All 4 critical dealers scrape successfully (≥15 products each)
- ✅ No WAF blocks detected
- ✅ Prices extracted and validated (±10% of competitor benchmarks)
- ✅ CSV exports cleanly (no truncated records)
- ✅ Unit tests + smoke tests pass
- ✅ Integration test runs to completion
- ✅ Supabase upload succeeds
- ✅ Telegram notifications delivery confirmed

**Metrics Target:**
- Avg. execution time: 45–120s per dealer
- Price fill rate: ≥80%
- Deduplication accuracy: 100%
- Error rate: <5% (failed pages)

---

## Next Steps (After Phase 2)

1. **Phase 3:** Implement remaining VTEX dealers (WebContinental, FrioPecas, Climario)
2. **Phase 4:** Implement remaining dealers (PoloAr, GoCompras, ArCerto, NorteRefrigeracao)
3. **Phase 5:** Production deployment + monitoring

---

## Files Modified / Created

- `scrapers/dealers.py` — Enhanced with Phase 1 infrastructure
- `scrapers/base.py` — Added WAF bypass helpers
- `test_phase2_critical_dealers.py` — 26 unit tests (all passing)
- `scripts/smoke_test_phase2.py` — Integration smoke test (to create)
- `.claude/PHASE2_IMPLEMENTATION_GUIDE.md` — This document

---

## Quick Reference: Configuration Deep-Dive

### Frigelar Config
```python
"Frigelar": {
    "url": "https://www.frigelar.com.br/split-inverter/c",
    "pagination": "vtex",
    "max_pages": 5,
    "requires_cep": True,                         # ← Unique to Frigelar
    "default_cep": "01310-100",                   # ← Av. Paulista, SP
    "price_wait_selector": ".vtex-product-price-1-x-sellingPriceValue, [class*='sellingPrice']",
    "block_indicators": [                         # ← CEP/session blocks
        "Valide seu acesso",
        "Insira um CEP do Brasil",
        "Código de acesso expirado"
    ],
    "item_selector": ".product-box-container",    # ← OCC container
    "wait_for_js": True,
    "wait_timeout": 10000,                        # 10s for Knockout.js render
}
```

**Flow in search():**
1. Goto URL → wait for networkidle
2. Detect CEP block indicator
3. Inject CEP via `_inject_cep()`
4. Re-detect blocks
5. If clear → parse VTEX __RUNTIME__ or DOM

---

### CentralAr Config
```python
"CentralAr": {
    "url": "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER",
    "pagination": "vtex",
    "max_pages": 5,
    "item_selector": ".pdc_product-item",         # ← SAP Hybris, NOT VTEX standard
    "prefer_jsonld": False,                       # ← JSON-LD is Organization, not Product
}
```

**Why .pdc_product-item?**
- CentralAr uses SAP Hybris commerce platform (not VTEX)
- Standard VTEX selectors (vtex-product-summary-2-x) don't match
- Diagnostic found 20 items via `.pdc_product-item` in April 2026

---

### Leveros Config
```python
"Leveros": {
    "url": "https://www.leveros.com.br/ar-condicionado/inverter",
    "pagination": "vtex",
    "max_pages": 5,
    "prefer_jsonld": True,                        # ← 118 products in JSON-LD
    "item_selector_candidates": [
        "[data-sku]",                             # Primary selector
        "main [class*='product-item']",           # Secondary
        # ... fallback chain
    ],
}
```

**Why prefer_jsonld=True?**
- Diagnostic found 118 products in JSON-LD
- DOM parsing is slower + less reliable
- JSON-LD is schema.org/Product (standard structure)

---

### Dufrio Config
```python
"Dufrio": {
    "url": "https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter",
    "pagination": "vtex",
    "max_pages": 5,
    "vtex_split_price": True,                     # ← Use split price extraction
    "prefer_jsonld": True,                        # ← 21 products in JSON-LD (primary)
    "item_selector": ".product-item",             # 42 items found via this selector
}
```

**Why vtex_split_price + prefer_jsonld?**
- Split price bug: "182900" concatenated (should be 1829,00)
- `_extract_vtex_split_price()` handles this via DOM parsing
- But JSON-LD has prices already parsed (source of truth)
- Strategy: JSON-LD first, fallback to split price parsing

---

## Debugging Commands

```bash
# Test CEP injection flow
python -c "from scrapers.dealers import DealerScraper; ..." # (see examples above)

# Run unit tests
python test_phase2_critical_dealers.py -v

# Check config for a dealer
python -c "from scrapers.dealers import DEALER_CONFIGS; import json; print(json.dumps(DEALER_CONFIGS['Frigelar'], indent=2))"

# Manual smoke test (one dealer)
python main.py --platforms dealers --no-headless --keywords "Frigelar" --pages 1

# Full integration test
python main.py --platforms dealers --pages 1 --keywords "Frigelar,CentralAr,Leveros,Dufrio"
```

---

*Last Updated: 2026-05-12*  
*Phase 2 Implementation — In Progress*
