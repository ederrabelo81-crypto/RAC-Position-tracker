# Leveros Debug Guide — Phase 2.3 Implementation

**Status:** Phase 2.3 — VTEX IO with JSON-LD Priority  
**Platform:** VTEX IO (modern SPA)  
**Challenge:** 118 products in JSON-LD, requires sophisticated matching strategy

---

## Quick Start

```bash
# 1. Activate environment
source venv/bin/activate

# 2. Run smoke test (visual browser)
python scripts/smoke_test_phase2.py --dealer Leveros --no-headless

# 3. Check logs
tail -f logs/bot_*.log | grep -i leveros
```

---

## Key Characteristics

| Aspect | Frigelar | CentralAr | Leveros |
|--------|----------|-----------|---------|
| Platform | Oracle OCC | SAP Hybris | VTEX IO |
| Volume | 15-30 | 15-25 | **100+ products** |
| JSON-LD | Yes | No | **Yes (118 detected)** |
| CEP | Yes | No | No |
| Extraction | VTEX → DOM | Pure DOM | **JSON-LD PRIMARY** |
| Complexity | Medium | Medium | **HIGH** |

---

## Expected Flow (Success Path)

### Step 1: Page Load
- Browser navigates to: `https://www.leveros.com.br/ar-condicionado/inverter`
- Waits for networkidle (VTEX IO renders dynamically)
- **Note:** No CEP required (national coverage)

### Step 2: JSON-LD Extraction
- Parses `<script type="application/ld+json">` tags
- Expects ≥100 products in structured format
- Each product has: name, description, offers.price
- **Expected:** 118 products extracted from JSON-LD

### Step 3: Price Matching
- For each DOM item, matches against JSON-LD by:
  1. **Exact match** (normalized comparison)
  2. **Containment** (substring relationship)
  3. **Word intersection** (≥60% word overlap)
- **Critical:** Multiple JSON-LD products may match one DOM item (carousel)

### Step 4: Deduplication
- Remove duplicates by normalized title
- Keep highest-priced variant (handles carousel variants)
- **Expected:** ≥100 unique products after dedup

---

## Debugging Checklist

### ❌ Problem: JSON-LD Not Extracting (0 Products)

**Check 1: Is JSON-LD present in HTML?**
```python
# In browser console
document.querySelectorAll('script[type="application/ld+json"]').length
# Should be: ≥1
```

**Check 2: Can we parse the JSON?**
```python
# In browser console
scripts = document.querySelectorAll('script[type="application/ld+json"]')
if (scripts.length > 0) {
    try {
        data = JSON.parse(scripts[0].textContent)
        console.log('Valid JSON, keys:', Object.keys(data).slice(0, 5))
    } catch(e) {
        console.log('Invalid JSON:', e)
    }
}
```

**Check 3: Are products in the JSON?**
```python
# In browser console
scripts = document.querySelectorAll('script[type="application/ld+json"]')
data = JSON.parse(scripts[0].textContent)

// If it's an array
if (Array.isArray(data)) {
    console.log('Products:', data.filter(p => p['@type'] === 'Product').length)
}

// If it's ItemList (wrapped products)
if (data.itemListElement) {
    console.log('Items in ItemList:', data.itemListElement.length)
}

// If it's Product with offers
if (data['@type'] === 'Product') {
    console.log('Single product detected')
}
```

**Check 4: Do products have prices?**
```python
# In browser console
scripts = document.querySelectorAll('script[type="application/ld+json"]')
data = JSON.parse(scripts[0].textContent)
products = Array.isArray(data) ? data : data.itemListElement?.map(i => i.item)

if (products) {
    with_prices = products.filter(p => p.offers?.price).length
    console.log(`Products with prices: ${with_prices}/${products.length}`)
}
```

**Fix:** If JSON-LD missing:
1. Fallback to DOM extraction via `item_selector_candidates`
2. Check logs: "JSON-LD returned 0 products — fallback to DOM"
3. DOM should find [data-sku] elements

---

### ❌ Problem: Low Product Count (< 50 Products)

**Issue 1: JSON-LD extraction not being called**

**Fix:** Verify `prefer_jsonld=True` in config:
```python
# Check in scrapers/dealers.py
DEALER_CONFIGS['Leveros']['prefer_jsonld']  # Should be True
```

**Issue 2: JSON-LD matching too strict**

**Fix:** Check matching thresholds in `_jsonld_match()`:
- Exact match: lowercased normalization
- Containment: substring check
- Word intersection: ≥60% threshold

If very low match rate (< 50%), thresholds may need lowering:
```python
# In _jsonld_match(), line ~1100:
word_intersection_threshold = 0.50  # Lower from 0.60 if too strict
```

**Issue 3: DOM fallback has fewer items than expected**

**Fix:** Update `item_selector_candidates`:
```python
# In DEALER_CONFIGS['Leveros'], add if needed:
"item_selector_candidates": [
    "[data-sku]",                          # Primary
    "main [class*='product-item']",        # Current matches
    # Add new if visual inspection finds different selectors:
    "[class*='product'][class*='shelf']",
    ".product-details",                    # New pattern
],
```

---

### ❌ Problem: Price Matching Fails (High Unmatched Rate)

**Symptom:** Logs show:
```
[Leveros] {N} JSON-LD products matched, {X} unmatched
```

**Issue 1: Titles too different between DOM and JSON-LD**

**Example:**
- DOM: "Ar Condicionado Springer Midea 12000 BTU"
- JSON-LD: "Springer Midea 12000 BTU Hi-Wall Inverter"

**Fix:** Lower word intersection threshold:
```python
# In _jsonld_match() around line 1100-1120:
score = sum(1 for w in words1 if w in words2) / len(words1)
if score >= 0.50:  # Changed from 0.60
    return match_score
```

**Issue 2: Multiple JSON-LD products match one DOM item (carousel)**

**Example:**
- DOM: "Springer Midea 12000" appears 3 times
- JSON-LD: Has 3 variants (WiFi, Manual, Eco)
- Solution: Dedup should keep 1 (highest price)

**Check:** Look at logs for dedup messages:
```
{X} records deduplicated
```

If many deduplicated: Carousel detection working (good)

**Issue 3: DOM has more items than JSON-LD**

**Example:**
- JSON-LD: 118 products
- DOM: 145 items found
- Result: 27 items unmatched

**Fix:** Either:
1. Keep unmatched DOM items (may be stock indicators, variants)
2. Filter out unmatched items
3. Use JSON-LD count as truth source (118 expected)

Current code keeps both → Match then return all

---

### ❌ Problem: Prices Wrong Format or Duplicated

**Issue 1: Multiple variants with different prices**

**Example:**
```
"Ar Condicionado Springer 12000" 
  - Manual: R$ 1.500,00
  - WiFi: R$ 1.799,00
  - Eco: R$ 1.599,00
```

**Result:** Dedup by title keeps one (highest price)

**Check logs:**
```
[Leveros] Deduplicating by: (plataforma, título normalizado)
{3} records deduplicated to {1}
```

This is **CORRECT** behavior — dedup handles variants.

**Issue 2: Prices showing as floats instead of BR format**

**Example:** R$ 1500.00 instead of R$ 1500,00

**Fix:** This is in parse_price_brazil() utility:
```python
# In utils/text.py, parse_price_brazil():
# May need to normalize VTEX float format to BR format
```

---

### ❌ Problem: Slow Extraction (Timeout on large JSON-LD)

**Issue:** 118 products → slow parsing

**Fix:** Already handled — JSON-LD parsing is fast (< 1s typical)

If slow:
1. Check network idle timeout
2. Verify page actually has 118 items
3. Check if DOM extraction is bottleneck

---

## Configuration Reference

```python
"Leveros": {
    "url": "https://www.leveros.com.br/ar-condicionado/inverter",
    "pagination": "vtex",
    "max_pages": 5,
    
    # JSON-LD as primary source (118 products available)
    "prefer_jsonld": True,
    
    # DOM fallback with 10-level selector chain
    "item_selector_candidates": [
        "[data-sku]",                              # Primary
        "main [class*='product-item']",            # Main container
        ".products-grid [class*='product-item']",  # Grid layout
        "[class*='product-list'] [class*='product-item']",
        "[class*='shelf'] [class*='product-item']",
        "section[class*='shelf'] > div > div",
        "main [class*='product-card']",
        ".products-grid [class*='product-card']",
        ".shelf-item",
        ".product-list-item",
    ],
}
```

---

## Success Criteria (Acceptance)

✅ Run: `python scripts/smoke_test_phase2.py --dealer Leveros`

**Expected Output:**
```
[SMOKE] Leveros: 50+ products, 80%+ with price
[SMOKE] ✅ Leveros PASSED
```

**Details:**
- Products: ≥50 (from JSON-LD, expect ~100-118)
- Price fill: ≥80% (JSON-LD has prices)
- Realistic range: R$ 1500–4500
- No WAF blocks
- Deduplication working (carousel handled)

---

## Commands for Debugging

### Inspect JSON-LD
```bash
python -c "
from scrapers.dealers import DealerScraper
import json

s = DealerScraper()
s._launch()
s._page.goto('https://www.leveros.com.br/ar-condicionado/inverter')
s._page.wait_for_load_state('networkidle')

html = s._page.content()

# Extract JSON-LD
import re
matches = re.findall(r'<script type=\"application/ld\+json\">(.+?)</script>', html, re.DOTALL)

if matches:
    print(f'Found {len(matches)} JSON-LD blocks')
    for i, match in enumerate(matches[:1]):  # First block
        try:
            data = json.loads(match)
            if isinstance(data, list):
                print(f'Block {i}: Array with {len(data)} items')
                products = [p for p in data if p.get('@type') == 'Product']
                print(f'  - Products: {len(products)}')
            elif isinstance(data, dict) and 'itemListElement' in data:
                print(f'Block {i}: ItemList with {len(data[\"itemListElement\"])} items')
        except:
            print(f'Block {i}: Invalid JSON')
else:
    print('No JSON-LD found')

s._close()
"
```

### Test matching logic
```bash
python -c "
from scrapers.dealers import DealerScraper

# Test word intersection matching
title_dom = 'Ar Condicionado Springer Midea 12000 BTU'
title_jsonld = 'Springer Midea 12000 BTU Hi-Wall Inverter'

result = DealerScraper._jsonld_match(title_dom, title_jsonld)
print(f'Match result: {result}')
print(f'  DOM: {title_dom}')
print(f'  JSON-LD: {title_jsonld}')
"
```

### Extract and print raw products
```bash
python -c "
from scrapers.dealers import DealerScraper

s = DealerScraper()
results = s.search('Leveros', {}, page_limit=1)

print(f'Total products: {len(results)}')
print(f'With price: {sum(1 for r in results if r.get(\"Preço (R$)\"))}')
print(f'Price fill rate: {100*sum(1 for r in results if r.get(\"Preço (R$)\"))/len(results):.0f}%')

# Show price distribution
prices = [r.get('Preço (R$)') for r in results if r.get('Preço (R$)')]
if prices:
    print(f'Price range: R\$ {min(prices):.2f} - R\$ {max(prices):.2f}')

for r in results[:3]:  # First 3
    print(f'  - {r.get(\"Produto/SKU\")}: R\$ {r.get(\"Preço (R$)\", \"N/A\")}')
"
```

### Monitor logs
```bash
tail -f logs/bot_*.log | grep -E "(Leveros|JSON-LD|matched|dedup)" --color=always
```

---

## VTEX IO Specifics

**What is VTEX IO?**
- Modern serverless commerce platform by VTEX
- Uses React SPA with dynamic rendering
- JSON-LD rich snippets for SEO (and our benefit!)
- Products in ItemList or Product arrays

**Why JSON-LD First?**
- Already parsed and structured
- Prices guaranteed (not behind XHR)
- Full product catalog included
- Faster than DOM extraction

**Why DOM Fallback?**
- Carousel variants not in JSON-LD (sometimes)
- Stock indicators only in DOM
- Future compatibility if JSON-LD removed

---

## Multi-Page JSON-LD Challenges

**Issue:** Leveros has 5 pages, JSON-LD has 118 products

**Solution:** Current code handles:
1. Page 1 JSON-LD (e.g., 30 products)
2. Page 2 JSON-LD (e.g., 30 products)
3. Etc... until max_pages or 0 products
4. Deduplication across all pages

**Verify in logs:**
```
[Leveros] Page 1/5: 30 products
[Leveros] Page 2/5: 25 products
[Leveros] Page 3/5: 30 products
...
[Leveros] Total before dedup: 118
[Leveros] After dedup: 115 (3 duplicates removed)
```

---

## Next Steps

1. **Run smoke test** → validate JSON-LD extraction (expect ≥50)
2. **Debug** using checklist above if issues
3. **Verify matching** logic working (80%+ with prices)
4. **Commit:** `feat(dealers): Implement Leveros scraping`

---

*Last Updated: 2026-05-12*  
*Phase 2.3 Leveros — Debug & Testing Guide*
