# Phase 2.2 Implementation Readiness — CentralAr

**Date:** 2026-05-12  
**Status:** ✅ Code Complete — Enhanced Configuration + Debugging Guide  
**Platform:** SAP Hybris (not VTEX)  
**Blocker:** Same as Phase 2.1 — Network restrictions prevent Playwright browser download

---

## What's New in Phase 2.2

### Configuration Enhancement
- Added `item_selector_candidates` — 5-level fallback chain for SAP redesigns
- Added `price_selector` — SAP-specific price extraction selectors
- Added `price_wait_selector` — Wait for price rendering before extraction
- Set `wait_timeout: 8000` — SAP may need slightly longer than Frigelar
- Clarified `prefer_jsonld: False` — JSON-LD in CentralAr is Organization, not Product

### Key Differences from Frigelar

| Aspect | Frigelar | CentralAr |
|--------|----------|-----------|
| Platform | Oracle OCC | SAP Hybris |
| CEP Required | ✅ Yes | ❌ No |
| VTEX __RUNTIME__ | ✅ Available | ❌ N/A |
| JSON-LD Products | ✅ Yes | ❌ Organization only |
| Fallback Selectors | Simple | 5-level chain |
| Price Extraction | VTEX-based | Pure DOM CSS |

---

## Current State

### ✅ Code Complete
- **Configuration** — Enhanced with SAP-specific selectors and fallbacks
- **Price Extraction** — 3-level CSS selector chain for SAP Hybris
- **Fallback Selectors** — 5 strategies to handle SAP redesigns
- **DOM Extraction** — Pure CSS parsing (no VTEX __RUNTIME__)
- **Deduplication** — Carousel-aware by normalized title

### ✅ Testing Infrastructure
- **Unit Tests** — 26 tests covering all 4 dealers (already passing from Phase 2.0)
- **Smoke Test** — Framework ready in `scripts/smoke_test_phase2.py`
- **Debug Guide** — Comprehensive `.claude/CENTRALAR_DEBUG_GUIDE.md` for troubleshooting

### ⚠️ Blocker (Same as Phase 2.1)
Network restrictions prevent Playwright browser download

---

## Configuration Reference

### Original (Minimal)
```python
"CentralAr": {
    "url": "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER",
    "pagination": "vtex",
    "max_pages": 5,
    "item_selector": ".pdc_product-item",
    "prefer_jsonld": False,
}
```

### Enhanced (Phase 2.2)
```python
"CentralAr": {
    "url": "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER",
    "pagination": "vtex",
    "max_pages": 5,

    # Primary selector
    "item_selector": ".pdc_product-item",

    # Fallback chain (SAP redesign resilience)
    "item_selector_candidates": [
        ".pdc_product-item",                    # Primary
        "[class*='pdc'][class*='product']",     # Broad SAP match
        "[class*='product-item']",              # Generic
        "[class*='product'][class*='card']",    # Card style
        "div[data-product-id]",                 # Data attribute
    ],

    # Price extraction (SAP-specific)
    "price_selector": "[class*='pdc'][class*='price'], [data-price], [itemprop='price']",
    "price_wait_selector": "[class*='price']",

    # Strategy
    "prefer_jsonld": False,
    "requires_cep": False,
    "wait_timeout": 8000,
}
```

---

## Expected Behavior

### Happy Path
```
1. Navigate → https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER
2. Wait for domcontentloaded (no JS rendering)
3. Detect items: .pdc_product-item (≥20 expected)
4. Extract titles + prices (CSS selectors)
5. Deduplicate by normalized title
6. Return: ≥15 products, ≥60% with prices
7. SUCCESS
```

### Key Differences from Frigelar
- **No CEP injection** (national coverage)
- **No VTEX __RUNTIME__** (pure DOM extraction)
- **No JSON-LD products** (only Organization schema)
- **Fallback chain crucial** (SAP redesigns common)

---

## Files Modified

| File | Changes | Purpose |
|------|---------|---------|
| `scrapers/dealers.py` | Enhanced `DEALER_CONFIGS['CentralAr']` | Added 4 fallback selectors, price extraction, wait timeout |
| `.claude/CENTRALAR_DEBUG_GUIDE.md` | Created | Comprehensive debugging guide specific to SAP Hybris |
| `.claude/PHASE2.2_READINESS.md` | This file | Readiness status and setup guide |

---

## Testing Strategy (When Environment Ready)

### Step 1: Run Unit Tests
```bash
python test_phase2_critical_dealers.py
# Expected: 26 tests passed
```

### Step 2: Run Smoke Test
```bash
python scripts/smoke_test_phase2.py --dealer CentralAr --no-headless
# Expected: 15+ products, 60%+ with price
```

### Step 3: Manual Debug (If Needed)
```bash
# Check selector
python -c "
from bs4 import BeautifulSoup
from scrapers.dealers import DealerScraper

s = DealerScraper()
s._launch()
s._page.goto('https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER')
s._page.wait_for_load_state('domcontentloaded')

html = s._page.content()
soup = BeautifulSoup(html, 'html.parser')
print(f'Items found: {len(soup.select(\".pdc_product-item\"))}')

s._close()
"
```

### Step 4: Manual Integration Test
```bash
python main.py --platforms dealers --pages 1 --no-headless --keywords "CentralAr"
# Check CSV for ≥15 products with realistic prices
```

---

## Success Criteria

✅ **Code Ready:** Configuration enhanced + debug guide created  
✅ **Tests Ready:** Unit tests passing from Phase 2.0  
⏳ **Smoke Test:** Awaiting environment (browser download)  
⏳ **Integration Test:** Awaiting smoke test pass  

**Expected:**
- Products: ≥15 per page
- Price fill: ≥60% (lower than Frigelar because SAP may not always expose prices)
- Prices realistic: R$ 1500–4500
- No WAF blocks

---

## Rollback Plan

If CentralAr implementation causes regression:

```bash
# Revert to previous state
git reset --hard <commit-before-centralar>

# Or disable temporarily
# Edit DEALER_CONFIGS['CentralAr']['on_hold'] = True
```

---

## Environment Setup (Same as Frigelar)

When network allows browser installation:

```bash
# Option A: System Chromium (recommended)
sudo apt-get install chromium-browser
export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/snap/bin/chromium

# Option B: When CDN accessible
python -m playwright install chromium

# Then test
python scripts/smoke_test_phase2.py --dealer CentralAr --no-headless
```

---

## Phase 2.2 vs 2.1 Comparison

### Complexity Difference
- **Frigelar:** CEP injection adds complexity but VTEX __RUNTIME__ simplifies extraction
- **CentralAr:** No CEP but no __RUNTIME__ — pure DOM extraction more fragile

### Risk Assessment
- **Frigelar:** Lower risk (VTEX is market-standard)
- **CentralAr:** Higher risk (SAP Hybris less common, redesigns more frequent)

### Mitigation
- **CentralAr:** 5-level fallback selector chain reduces redesign risk

---

## Next Actions

1. **[IMMEDIATE]** When environment fixed:
   - Run smoke test: `python scripts/smoke_test_phase2.py --dealer CentralAr --no-headless`
   - Use `.claude/CENTRALAR_DEBUG_GUIDE.md` for troubleshooting
   - Debug selector/price issues if any

2. **[AFTER PASS]** Commit implementation:
   - `git commit -m "feat(dealers): Implement CentralAr scraping"`

3. **[NEXT]** Move to Phase 2.3 (Leveros):
   - JSON-LD priority extraction (100+ products)
   - Multi-page DOM fallback

---

## Debugging Resources

- **Debug Guide:** `.claude/CENTRALAR_DEBUG_GUIDE.md` (detailed checklist)
- **Implementation Guide:** `.claude/PHASE2_IMPLEMENTATION_GUIDE.md` (general reference)
- **Unit Tests:** `test_phase2_critical_dealers.py` (validation suite)
- **Smoke Test:** `scripts/smoke_test_phase2.py --dealer CentralAr --no-headless`

---

*Last Updated: 2026-05-12*  
*Phase 2.2 CentralAr — Code Complete, Configuration Enhanced*
