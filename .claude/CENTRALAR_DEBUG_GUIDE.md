# CentralAr Debug Guide — Phase 2.2 Implementation

**Status:** Phase 2.2 — SAP Hybris Implementation  
**Platform:** SAP Hybris commerce (not VTEX)  
**Challenge:** Platform-specific selectors, no VTEX __RUNTIME__, minimal JSON-LD

---

## Quick Start

```bash
# 1. Activate environment
source venv/bin/activate

# 2. Run smoke test (visual browser)
python scripts/smoke_test_phase2.py --dealer CentralAr --no-headless

# 3. Check logs
tail -f logs/bot_*.log | grep -i centralar
```

---

## Key Differences from Frigelar

| Aspect | Frigelar | CentralAr |
|--------|----------|-----------|
| Platform | Oracle OCC | SAP Hybris |
| CEP Required | Yes ✅ | No ❌ |
| VTEX __RUNTIME__ | Yes | No |
| JSON-LD Products | Yes | No (only Organization) |
| Item Selector | `.product-box-container` | `.pdc_product-item` |
| Extraction | VTEX → DOM | Pure DOM |

---

## Expected Flow (Success Path)

### Step 1: Page Load
- Browser navigates to: `https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER`
- Waits for domcontentloaded (no JS rendering needed)
- **Note:** No CEP prompt (national coverage)

### Step 2: Product Detection
- Selector `.pdc_product-item` finds SAP product containers
- **Expected:** ≥20 products visible

### Step 3: Price Extraction
- Looks for price in SAP structure:
  - `[class*="price"]` (generic)
  - `[data-price]` (data attribute)
  - `.pdc_product-price` (SAP-specific)
  - `[itemprop="price"]` (schema.org)
- **Expected:** ≥60% of products have prices

### Step 4: DOM Extraction
- Selects products via `.pdc_product-item`
- Extracts title + price per item
- Deduplicates by normalized title
- **Expected:** ≥15 products on page 1

---

## Debugging Checklist

### ❌ Problem: 0 Products Found

**Check 1: Did page load correctly?**
```python
# In browser console (F12 → Console tab)
window.location.href
# Should be: https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER
```

**Check 2: Does selector exist in DOM?**
```python
# In browser console
document.querySelectorAll('.pdc_product-item').length
# Should be: ≥20
```

If 0: Selector may have changed or page structure different

**Fix:** Update selector or add fallbacks
```python
# If different, identify actual container class:
document.querySelectorAll('[class*="product-item"]').length
document.querySelectorAll('[class*="pdc"]').length
document.querySelectorAll('[data-product-id]').length
```

**Check 3: Is page a 404 or error?**
```python
# In browser console
document.title
document.body.innerText.includes('não encontrada') || document.body.innerText.includes('404')
```

If error detected: Page may be unavailable or URL changed

**Fix:**
1. Visit URL directly in browser: https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER
2. Verify category page loads with products
3. Update URL in DEALER_CONFIGS if needed

---

### ❌ Problem: Low Product Count (< 15)

**Issue 1: Selector too narrow**

**Fix:** Add item_selector_candidates for fallback chain
```python
# In DEALER_CONFIGS['CentralAr'], add:
"item_selector_candidates": [
    ".pdc_product-item",           # Primary
    "[class*='pdc'][class*='product']",  # Broader match
    "[class*='product-item']",     # Generic fallback
    "[class*='product'][class*='card']",  # Card style
    "div[data-product-id]",        # Data attribute
],
```

**Issue 2: Pagination not working**

**Fix:** Verify pagination type
```python
# Check if VTEX pagination works
# URL should change to: ?page=2, &page=2, or similar
# If different pagination structure, update pagination type
```

**Issue 3: Same products on page 2**

**Fix:** This indicates carousel or duplicate rendering
- Deduplication should handle this
- Check logs: "X records deduplicated"

---

### ❌ Problem: Prices Wrong or Missing

**Prices missing (0% or < 60% filled):**

1. Check if SAP uses different price structure:
```python
# In browser console, inspect a product:
el = document.querySelector('.pdc_product-item')
console.log(el.innerHTML)  # See full HTML structure

# Look for price patterns:
el.textContent.match(/\d+,\d{2}/)  # Brazilian format R$ 1.234,56
el.querySelectorAll('[class*="price"]')  # All price-like elements
```

2. Update price selector in config:
```python
# Add to DEALER_CONFIGS['CentralAr']:
"price_selector": "[class*='pdc'][class*='price'], [data-price], [itemprop='price']",
"price_wait_selector": "[class*='price']",
```

**Prices unrealistic (< R$ 500 or > R$ 10,000):**
- AC units should be R$ 1500–4500 range
- If outside: Check if extraction grabbed wrong element (e.g., quantity, rating)
- Debug: Print extracted price element

**Prices wrong format (showing as text like "1234.56" instead of "1234,56"):**
- SAP may use different decimal format
- Check parsing in `utils/text.py` parse_price_brazil()
- May need locale-specific fix

---

### ❌ Problem: WAF Block or Access Denied

**Issue:** Page shows "Acesso Negado" or similar

**Fix:**
1. Check if CentralAr blocks headless browsers
   - Try with --no-headless first
   - If works: May need stealth JS injection tuning

2. Add block indicators to detect early:
```python
# Add to DEALER_CONFIGS['CentralAr']:
"block_indicators": ["Acesso Negado", "Access Denied", "403"],
```

3. If persistent: May need rotating IPs or delays
```python
# Add to config:
"wait_for_js": True,
"wait_timeout": 8000,
"ajax_prices": False,
```

---

## Configuration Changes Needed

Current config is too minimal. Add these to DEALER_CONFIGS['CentralAr']:

```python
"CentralAr": {
    "url": "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER",
    "pagination": "vtex",
    "max_pages": 5,
    
    # Primary selector
    "item_selector": ".pdc_product-item",
    
    # Fallback selectors if SAP redesigns
    "item_selector_candidates": [
        ".pdc_product-item",
        "[class*='pdc'][class*='product']",
        "[class*='product-item']",
        "[class*='product'][class*='card']",
        "div[data-product-id]",
    ],
    
    # Price extraction (SAP Hybris specific)
    "price_selector": "[class*='pdc'][class*='price'], [data-price], [itemprop='price']",
    "price_wait_selector": "[class*='price']",
    
    # Extraction strategy
    "prefer_jsonld": False,  # JSON-LD is Organization, not Product
    
    # No CEP needed (national coverage)
    "requires_cep": False,
    
    # SAP may need slightly longer wait
    "wait_timeout": 8000,
}
```

---

## Success Criteria (Acceptance)

✅ Run: `python scripts/smoke_test_phase2.py --dealer CentralAr`

**Expected Output:**
```
[SMOKE] CentralAr: 15+ products, 60%+ with price
[SMOKE] ✅ CentralAr PASSED
```

**Details:**
- Products: ≥15 per page
- Price fill rate: ≥60% (lower than Frigelar because SAP may not always expose prices)
- No WAF blocks
- Realistic price range: R$ 1500–4500

---

## Commands for Debugging

### View HTML structure
```bash
python -c "
from scrapers.dealers import DealerScraper
from bs4 import BeautifulSoup

s = DealerScraper()
s._launch()
s._page.goto('https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER')
s._page.wait_for_load_state('domcontentloaded')

html = s._page.content()
soup = BeautifulSoup(html, 'html.parser')

# Count items
print(f'Items: {len(soup.select(\".pdc_product-item\"))}')

# Show first item
item = soup.select_one('.pdc_product-item')
if item:
    print('First item HTML:')
    print(item.prettify()[:1500])
    
    # Check price elements
    prices = item.select('[class*=\"price\"]')
    print(f'Price elements: {len(prices)}')
    if prices:
        for p in prices[:2]:
            print(f'  - {p.get(\"class\")}: {p.get_text(strip=True)[:100]}')

s._close()
"
```

### Extract and print raw products
```bash
python -c "
from scrapers.dealers import DealerScraper

s = DealerScraper()
results = s.search('CentralAr', {}, page_limit=1)

print(f'Total products: {len(results)}')
print(f'With price: {sum(1 for r in results if r.get(\"Preço (R$)\"))}')

for r in results[:3]:  # First 3
    print(f'  - {r.get(\"Produto/SKU\")}: R\$ {r.get(\"Preço (R$)\", \"N/A\")}')
"
```

### Monitor logs in real-time
```bash
tail -f logs/bot_*.log | grep -E "(CentralAr|pdc_product)" --color=always
```

---

## SAP Hybris Specifics

**What is SAP Hybris?**
- Commerce platform by SAP (acquired Hybris in 2013)
- Uses `.pdc_*` CSS classes (Product Display Container)
- No VTEX __RUNTIME__ object
- May have JSON-LD but often as Organization, not Product schema

**Why .pdc_product-item?**
- `pdc` = Product Display Container
- Standard SAP naming convention
- More stable than VTEX selectors (which change frequently)

**If selector changes:**
1. SAP may rebrand to different class prefix
2. Check for `[class*="product-item"]` as fallback
3. Or look for `[data-product-id]` attributes

---

## Configuration Reference

```python
# Current minimal config in DEALER_CONFIGS['CentralAr']
{
    "url": "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER",
    "pagination": "vtex",
    "max_pages": 5,
    "item_selector": ".pdc_product-item",
    "prefer_jsonld": False,
}

# Recommended full config (add to config above):
{
    "item_selector_candidates": [...],
    "price_selector": "...",
    "price_wait_selector": "...",
    "wait_timeout": 8000,
}
```

To modify: Edit `scrapers/dealers.py` line ~152, DEALER_CONFIGS dict.

---

## Next Steps

1. **Run smoke test** → identify actual selector/price issues
2. **Debug** using checklist above
3. **Update configuration** with fallback selectors
4. **Fix** code as needed for SAP-specific extraction
5. **Iterate** until smoke test passes
6. **Commit:** `feat(dealers): Implement CentralAr scraping`

---

*Last Updated: 2026-05-12*  
*Phase 2.2 CentralAr — Debug & Testing Guide*
