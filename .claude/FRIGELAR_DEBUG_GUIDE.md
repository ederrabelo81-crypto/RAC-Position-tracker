# Frigelar Debug Guide — Phase 2.1 Implementation

**Status:** Phase 2.1 — Ready for scraping validation  
**Platform:** Oracle Commerce Cloud (OCC) + VTEX frontend  
**Challenge:** CEP injection required to unlock prices

---

## Quick Start

```bash
# 1. Activate environment
source venv/bin/activate
python -m playwright install chromium

# 2. Run smoke test (visual browser)
python scripts/smoke_test_phase2.py --dealer Frigelar --no-headless

# 3. Check logs
tail -f logs/bot_*.log | grep -i frigelar
```

---

## Expected Flow (Success Path)

### Step 1: Page Load
- Browser navigates to: `https://www.frigelar.com.br/split-inverter/c`
- Waits for networkidle (JS rendering)
- **Issue:** CEP prompt appears (blocks product visibility)

### Step 2: CEP Injection
- Block detector finds: "Valide seu acesso" OR "Insira um CEP do Brasil"
- Searcher finds CEP input:
  - `input[placeholder*="CEP"]`
  - `input[name*="cep"]`
  - `input[id*="cep"]`
  - `input[type="text"][maxlength="8"]`
- Injects CEP: `01310-100` (Av. Paulista, SP)
- Presses Enter
- **Expected:** Prices become visible, page reloads

### Step 3: Price Rendering
- Waits for any of:
  - `.vtex-product-price-1-x-sellingPriceValue`
  - `[class*='sellingPrice']`
  - `[class*='currencyInteger']`
  - `[class*='skuBestPrice']`
- Timeout: 10 seconds (wait_timeout from config)

### Step 4: DOM Extraction
- Selects products via `.product-box-container`
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
# Should be: https://www.frigelar.com.br/split-inverter/c?...
```

**Check 2: CEP injection triggered?**
- Look in logs for: `[Frigelar] CEP 01310-100 injetado`
- If NOT present: Block detector may not be working
  - Check: Does HTML contain "Valide seu acesso" or "Insira um CEP"?
  - Debug: Add `logger.info(html_snippet)` in _is_blocked_page()

**Check 3: Did selector find items?**
```python
# In browser console
document.querySelectorAll('.product-box-container').length
# Should be: ≥15
```

If 0: Selector may have changed. Check actual DOM:
```bash
# Save debug HTML
python -c "
from scrapers.dealers import DealerScraper
s = DealerScraper()
s._launch()
s._page.goto('https://www.frigelar.com.br/split-inverter/c')
s._page.wait_for_load_state('networkidle')
# Look for CEP prompt
html = s._page.content()
print('CEP prompt found' if 'CEP' in html else 'No CEP prompt')
# Find actual item selectors
from bs4 import BeautifulSoup
soup = BeautifulSoup(html, 'html.parser')
print(f'Items via .product-box-container: {len(soup.select(\".product-box-container\"))}')
print(f'Items via [data-sku]: {len(soup.select(\"[data-sku]\"))}')
print(f'Items via .product-item: {len(soup.select(\".product-item\"))}')
s._close()
"
```

**Check 4: Prices visible?**
```python
# In browser console after CEP injection
document.querySelectorAll('[class*="sellingPrice"]').length
# Should be: ≥15
# If 0: Prices may be behind separate fetch
```

---

### ❌ Problem: CEP Injection Not Working

**Issue 1: CEP input not found**

**Fix:**
1. Verify selector finds input:
```python
s._page.wait_for_selector('input[placeholder*="CEP"]', timeout=5000)
# If timeout: Try others
s._page.wait_for_selector('input[name*="cep"]', timeout=5000)
```

2. If none found: Update `cep_input_selectors` in `_inject_cep()` with new selectors

**Issue 2: Enter key doesn't submit form**

**Fix:**
1. Check if different submit mechanism:
```python
# Look for submit button
s._page.wait_for_selector('button[type="submit"]', timeout=3000)
# If found: Click it instead of pressing Enter
```

2. Update `_inject_cep()` to handle submit button:
```python
# After el.fill(cep_clean):
submit_btn = self._page.query_selector('button[type="submit"]')
if submit_btn:
    submit_btn.click()
else:
    el.press("Enter")
```

**Issue 3: Page doesn't reload after CEP**

**Fix:**
1. Add explicit wait:
```python
# After CEP injection in _inject_cep():
self._page.wait_for_load_state("networkidle", timeout=8000)
```

---

### ❌ Problem: Wrong Product Count

**Too few products (< 15):**
1. Check pagination: Are there multiple pages available?
   - Frigelar should have 30+ products across pages
   - Smoke test uses page_limit=1, so only page 1
   - If page 1 has 0-10 items: Site may have limited stock

2. Check deduplication: Is dedup too aggressive?
   - Look for duplicate titles in logs
   - Verify _deduplicate() logic keeps different products

**Too many products (> 100 per page):**
1. Check for carousel duplicates
   - If same product appears multiple times: Carousel artifact
   - Should be caught by dedup

---

### ❌ Problem: Prices Wrong (×10 Bug, Negative, $0)

**Prices ×10 too high (1829 → 18290):**
- This is a VTEX parsing bug, shouldn't occur in Frigelar (not affected)
- Check parse_price() in utils/text.py

**Prices $0 or missing:**
1. Check if price extraction ran:
   ```python
   # Logs should show:
   # [Frigelar] Preços renderizados ([class*="sellingPrice"])
   ```

2. If not visible: Prices may come from separate API call
   - Check config: `ajax_prices` flag
   - Or use JSON-LD as fallback (prefer_jsonld could help)

3. Update config if needed:
   ```python
   "Frigelar": {
       ...
       "prefer_jsonld": True,  # Try JSON-LD first
       "ajax_prices": False,   # No separate AJAX needed
   }
   ```

**Prices unrealistic (< R$ 500 or > R$ 10,000):**
- AC units should be R$ 1500–4500 range
- If outside: Extraction grabbed wrong element
- Debug: Print extracted price element details

---

## Success Criteria (Acceptance)

✅ Run: `python scripts/smoke_test_phase2.py --dealer Frigelar`

**Expected Output:**
```
[SMOKE] Frigelar: 15+ products, 70%+ with price
[SMOKE] ✅ Frigelar PASSED
```

**Details:**
- Products: ≥15 per page
- Price fill rate: ≥70%
- No WAF blocks (after CEP injection)
- No ×10 price bugs
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
s._page.goto('https://www.frigelar.com.br/split-inverter/c')
s._page.wait_for_load_state('networkidle')

html = s._page.content()
soup = BeautifulSoup(html, 'html.parser')

# Count items
print(f'Items: {len(soup.select(\".product-box-container\"))}')

# Show first item
item = soup.select_one('.product-box-container')
if item:
    print('First item HTML:')
    print(item.prettify()[:1000])

s._close()
"
```

### Extract and print raw products
```bash
python -c "
from scrapers.dealers import DealerScraper

s = DealerScraper()
results = s.search('Frigelar', {}, page_limit=1)

print(f'Total products: {len(results)}')
print(f'With price: {sum(1 for r in results if r.get(\"Preço (R$)\"))}')

for r in results[:3]:  # First 3
    print(f'  - {r.get(\"Produto/SKU\")}: R\$ {r.get(\"Preço (R$)\", \"N/A\")}')
"
```

### Monitor logs in real-time
```bash
tail -f logs/bot_*.log | grep -E "(Frigelar|CEP|BLOCKED)" --color=always
```

---

## Configuration Reference

```python
# Current config in DEALER_CONFIGS['Frigelar']
{
    "url": "https://www.frigelar.com.br/split-inverter/c",
    "pagination": "vtex",                    # VTEX pagination (?page=2)
    "max_pages": 5,
    
    "requires_cep": True,                    # ← CEP mandatory
    "default_cep": "01310-100",              # ← Av. Paulista, SP
    
    "price_wait_selector": ".vtex-product-price-1-x-sellingPriceValue, [class*='sellingPrice']",
    "block_indicators": [                    # ← Custom block detection
        "Valide seu acesso",
        "Insira um CEP do Brasil",
        "Código de acesso expirado"
    ],
    
    "item_selector": ".product-box-container",  # ← OCC container
    "wait_for_js": True,                    # ← Wait for Knockout.js render
    "wait_timeout": 10000,                  # ← 10s timeout
}
```

**To modify:** Edit `scrapers/dealers.py` line ~120, DEALER_CONFIGS dict.

---

## Next Steps

1. **Run smoke test** → identify actual issue
2. **Debug** using checklist above
3. **Fix** configuration or code as needed
4. **Iterate** until smoke test passes
5. **Commit:** `feat(dealers): Implement Frigelar scraping`

---

*Last Updated: 2026-05-12*  
*Phase 2.1 Frigelar — Debug & Testing Guide*
