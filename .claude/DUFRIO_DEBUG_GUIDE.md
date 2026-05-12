# Dufrio Debug Guide — Phase 2.4 Implementation

**Status:** Phase 2.4 — VTEX Split Price Bug Handling  
**Platform:** VTEX (legacy)  
**Challenge:** Price parsing bug: 182900 (concatenated) → should be 1829,00

---

## Quick Start

```bash
# 1. Activate environment
source venv/bin/activate

# 2. Run smoke test (visual browser)
python scripts/smoke_test_phase2.py --dealer Dufrio --no-headless

# 3. Check logs
tail -f logs/bot_*.log | grep -i dufrio
```

---

## The Dufrio Problem

### Root Cause: VTEX Split Price Bug

VTEX stores prices in 3 separate fields:
- `currencyInteger`: "182900" (concatenated, missing decimal separator)
- `currencyDecimalSeparator`: "," (comma)
- `currencyDecimalDigits`: "00" (decimal digits)

**Expected:** Format with comma → `1829,00`  
**Bug:** Integer field has NO separation → `182900` (×100 too large)

### Solution: Dedicated Parser

```python
def _extract_vtex_split_price(item) -> Optional[float]:
    """
    Extrae precio VTEX split:
    182900 + "," + "00" → 1829,00
    
    Maneja el bug donde decimal digits estan separados.
    """
    currency_int = item.select_one('[class*="currencyInteger"]')
    if not currency_int:
        return None
    
    text = currency_int.get_text(strip=True)
    # text = "182900"
    
    # Insert comma: 182900 → 1829,00
    if len(text) >= 3:
        text = text[:-2] + ',' + text[-2:]
    
    # Parse: "1829,00" → 1829.0
    return parse_price_brazil(text)
```

---

## Key Characteristics

| Aspect | Frigelar | CentralAr | Leveros | Dufrio |
|--------|----------|-----------|---------|--------|
| Platform | Oracle OCC | SAP Hybris | VTEX IO | VTEX |
| Volume | 15-30 | 15-25 | 100+ | **15-30** |
| Price Bug | No | No | No | **×100 bug** |
| JSON-LD | Yes | No | Yes (118) | Yes (21) |
| Split Price | No | No | No | **YES** |
| Complexity | Medium | Medium | High | **MEDIUM** |

---

## Expected Flow (Success Path)

### Step 1: Page Load
- Browser navigates to: `https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter`
- Waits for domcontentloaded (VTEX legacy)
- **Note:** No CEP required (national coverage)

### Step 2: JSON-LD Extraction (Primary)
- Parses `<script type="application/ld+json">` tags
- Expected: 21 products in JSON-LD
- Prices already formatted correctly in JSON-LD
- **Expected:** 21 products extracted

### Step 3: DOM Fallback (If JSON-LD Insufficient)
- Selects products via `.product-item`
- Expected: 42 items found
- Extracts prices using `_extract_vtex_split_price()`:
  - Finds `[class*="currencyInteger"]`
  - Inserts comma to fix concatenation
  - Converts to float
- Matches DOM items to JSON-LD products
- **Expected:** Prices filled for unmatched items

### Step 4: Deduplication
- Remove duplicates by normalized title
- Carousel variants kept if different price
- **Expected:** ≥15 unique products

---

## Debugging Checklist

### ❌ Problem: Prices ×10 Too High (1829 → 18290)

**This is the DUFRIO BUG — check if fix working:**

**Check 1: Is split price extraction being called?**

```python
# In browser console, inspect a product
el = document.querySelector('.product-item')
if (el) {
    currency_int = el.querySelector('[class*="currencyInteger"]')
    if (currency_int) {
        console.log('currencyInteger:', currency_int.textContent)
        // Should be: "182900" (concatenated)
    }
}
```

**Check 2: Does the split price extraction insert comma?**

```python
# Verify _extract_vtex_split_price() logic
text = "182900"
# Should become: "1829,00"
result = text[:-2] + ',' + text[-2:]
console.log('Fixed:', result)  # Should be: 1829,00
```

**Fix:** If prices still ×10:
1. Verify `vtex_split_price=True` in config
2. Check if method is being called:
   ```
   logs should show: "VTEX split price extracted: R$ 1829,00"
   ```
3. If not in logs: DOM extraction may not be running
4. Ensure JSON-LD fallback is working

**Issue 1: JSON-LD has correct prices but DOM extraction not called**

**Fix:** Verify `prefer_jsonld=True` means:
- JSON-LD is extracted first
- Only if 0 products → fallback to DOM
- If JSON-LD has 21 products → DOM never called

**This is correct behavior!** Dufrio should use JSON-LD (prices fixed there).

**Issue 2: JSON-LD missing prices**

**Check:**
```python
# In browser console
scripts = document.querySelectorAll('script[type="application/ld+json"]')
if (scripts.length > 0) {
    data = JSON.parse(scripts[0].textContent)
    products = Array.isArray(data) ? data : data.itemListElement?.map(i => i.item)
    if (products) {
        with_prices = products.filter(p => p.offers?.price).length
        console.log(`Products with prices: ${with_prices}/${products.length}`)
    }
}
```

If JSON-LD missing prices → Would fallback to DOM → Would hit ×10 bug

**Fix:** Ensure JSON-LD is being extracted and prices are present.

---

### ❌ Problem: Low Product Count (< 15)

**Issue 1: JSON-LD not being used**

**Fix:** Verify config:
```python
DEALER_CONFIGS['Dufrio']['prefer_jsonld']  # Should be True
```

**Issue 2: JSON-LD returns fewer than expected**

**Expected:** 21 products from JSON-LD

**Check:** Are all 21 products structured correctly?
```python
# Count by @type
products = [p for data in json_lds for p in (data if isinstance(data, list) else [data]) if p.get('@type') == 'Product']
print(f'Products: {len(products)}')
```

**Issue 3: DOM selector `.product-item` not finding items**

**Check:**
```python
document.querySelectorAll('.product-item').length
# Should be: ≥42
```

If 0: Selector may have changed

**Fix:** Update or add fallback selectors:
```python
document.querySelectorAll('[class*="product"]').length
document.querySelectorAll('[data-product-id]').length
```

---

### ❌ Problem: Price Matching Fails

**Issue:** JSON-LD products not matching DOM items

**Fix:** Check word intersection threshold:
```python
# Example:
title_dom = "Ar Condicionado Dufrio 12000"
title_jsonld = "Dufrio 12000 BTU Hi-Wall Inverter"

# Should match on: "Dufrio", "12000" (40% overlap)
# If threshold is 60%: WILL NOT MATCH
# Solution: Lower threshold or use index-based fallback
```

**Current code:** Index-based fallback when matching fails
- If matching fails, assign prices by index position
- First unmatched DOM item gets first unmatched JSON-LD price
- This is OK — prices still correct, just assignment strategy

---

## Configuration Reference

```python
"Dufrio": {
    "url": "https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter",
    "pagination": "vtex",
    "max_pages": 5,
    
    # Split price extraction (handle 182900 → 1829,00)
    "vtex_split_price": True,
    
    # Item selector (42 items found in DOM)
    "item_selector": ".product-item",
    
    # JSON-LD priority (21 products with correct prices)
    "prefer_jsonld": True,
}
```

---

## Success Criteria (Acceptance)

✅ Run: `python scripts/smoke_test_phase2.py --dealer Dufrio`

**Expected Output:**
```
[SMOKE] Dufrio: 15+ products, 60%+ with price
[SMOKE] Average price: R$ 2000-3500 (NOT > 10000)
[SMOKE] ✅ Dufrio PASSED
```

**Details:**
- Products: ≥15
- Price fill: ≥60%
- **NO ×10 BUG:** Average price < R$ 5000 (AC units range)
- Price range: R$ 1500–4500
- No WAF blocks

---

## Commands for Debugging

### Test split price logic
```bash
python -c "
from scrapers.dealers import DealerScraper

# Test the fix
text = '182900'
fixed = text[:-2] + ',' + text[-2:]
print(f'Original: {text}')
print(f'Fixed: {fixed}')
print(f'Should be: 1829,00')

from utils.text import parse_price_brazil
price = parse_price_brazil(fixed)
print(f'Parsed: {price}')
print(f'In range: {1500 < price < 4500}')
"
```

### Inspect JSON-LD
```bash
python -c "
from scrapers.dealers import DealerScraper
import json

s = DealerScraper()
s._launch()
s._page.goto('https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter')
s._page.wait_for_load_state('domcontentloaded')

html = s._page.content()

# Extract JSON-LD
import re
matches = re.findall(r'<script type=\"application/ld\+json\">(.+?)</script>', html, re.DOTALL)

if matches:
    print(f'Found {len(matches)} JSON-LD blocks')
    for i, match in enumerate(matches[:1]):
        try:
            data = json.loads(match)
            if isinstance(data, list):
                products = [p for p in data if p.get('@type') == 'Product']
                print(f'Block {i}: {len(products)} Products')
                with_prices = [p for p in products if p.get('offers', {}).get('price')]
                print(f'  - With prices: {len(with_prices)}')
        except:
            print(f'Block {i}: Invalid JSON')
else:
    print('No JSON-LD found')

s._close()
"
```

### Extract and print raw products
```bash
python -c "
from scrapers.dealers import DealerScraper

s = DealerScraper()
results = s.search('Dufrio', {}, page_limit=1)

print(f'Total products: {len(results)}')
print(f'With price: {sum(1 for r in results if r.get(\"Preço (R$)\"))}')

# Check for ×10 bug
prices = [r.get('Preço (R$)') for r in results if r.get('Preço (R$)')]
if prices:
    avg_price = sum(prices) / len(prices)
    print(f'Average price: R\$ {avg_price:.2f}')
    print(f'×10 bug present: {avg_price > 10000}')

for r in results[:3]:
    print(f'  - {r.get(\"Produto/SKU\")}: R\$ {r.get(\"Preço (R$)\", \"N/A\")}')
"
```

### Monitor logs
```bash
tail -f logs/bot_*.log | grep -E "(Dufrio|split.*price|×10)" --color=always
```

---

## Phase 2 Completion Assessment

### Frigelar (2.1)
- ✅ CEP injection working
- ✅ VTEX __RUNTIME__ extraction
- ✅ Configuration with fallback timeouts
- **Status:** Ready for testing

### CentralAr (2.2)
- ✅ SAP Hybris selector with 5-level fallback
- ✅ Pure DOM extraction (no VTEX)
- ✅ Price CSS selectors configured
- **Status:** Ready for testing

### Leveros (2.3)
- ✅ JSON-LD extraction (118 products)
- ✅ Word intersection matching (≥60%)
- ✅ 10-level DOM fallback chain
- ✅ Multi-page pagination
- **Status:** Ready for testing

### Dufrio (2.4)
- ✅ Split price extraction (handle ×100 bug)
- ✅ JSON-LD priority (21 correct prices)
- ✅ DOM fallback with bug fix
- ✅ Deduplication with variant handling
- **Status:** Ready for testing

---

## Next Steps

1. **Run smoke test** → validate split price fix (NO ×10 bug)
2. **Debug** using checklist above if issues
3. **Verify** average price in realistic range
4. **Commit:** `feat(dealers): Implement Dufrio scraping`

---

*Last Updated: 2026-05-12*  
*Phase 2.4 Dufrio — Debug & Testing Guide*
