# Scraping Patterns — RAC Position Tracker

## CSS Selector Fallback Chains

Every scraper uses ordered lists of CSS selectors. The first match with enough items wins.
Pattern: `_first_match(item, candidates)` returns first non-None `select_one()`.

### Item detection — `_detect_items()`
- Tries each selector in `_SELECTORS["item_candidates"]`
- Requires `_MIN_ITEMS` (3) to `max_items` (120) results
- Per-dealer overrides via `item_selector` (string) or `item_selector_candidates` (list)

### Title extraction priority
1. CSS selectors (productNameContainer, ProductName, h2, h3)
2. `img[alt]` attribute
3. `a[title]` attribute
4. `_fix_brand_concat()` — strips leading brand without space ("ElginAr..." → "Ar...")

### Price extraction — `_extract_price_el()`
5-level fallback (see QUICK_REFERENCE for full chain).
Critical: VTEX IO splits prices into 3 child elements (Integer+Separator+Digits).

## VTEX-Specific Patterns

### `__RUNTIME__` JS state
VTEX IO stores product data in `window.__RUNTIME__.queryData`.
Structure: `queryData[hash].data.productSearch.products[]`
Each product has: `productName`, `items[].sellers[].commertialOffer.Price`.
Misspelling "commertialOffer" is in VTEX's actual code.

### Pagination
VTEX uses `?page=N` (1-indexed). Default 24-48 items per page.
Some stores have "OrderBy" params (e.g., Climario: `?order=OrderByTopSaleDESC`).

## WooCommerce Patterns

### Items
`ul.products li.product` is the standard container.
Pagination: `/page/N/` path segment.

### Gotcha: Brand + Title concatenation
WooCommerce themes sometimes put brand and title in the same parent element.
`get_text()` concatenates them without space → "ElginAr Condicionado..."
Fix: `_fix_brand_concat()` checks BRANDS list for prefix without trailing space.

## JSON-LD (schema.org/Product)

Many e-commerce sites embed structured data in `<script type="application/ld+json">`.
Contains: `@type: "Product"`, `name`, `offers.price`, `offers.lowPrice`.

### Matching challenge
JSON-LD names may differ from DOM titles (formatting, abbreviations, accents).
Solution: `_jsonld_match()` uses 3 strategies:
1. Exact match after normalization (remove accents, punctuation, lowercase)
2. Containment (one string contains the other, both >15 chars)
3. Word-intersection (Jaccard ≥ 60% on words >2 chars)

### Index fallback
When record count and JSON-LD count are within ±15%, prices are assigned by position
for any remaining unmatched records. This catches edge cases where names differ too much.

## Google Shopping — Leaf-Div Strategy

Google Shopping PLAs have no stable CSS classes.
Container: `div.rwVHAc`. Title: first `<div>` with:
- No child elements (`div.find()` returns None)
- No CSS class (`div.get("class")` is None)
- Text 15-200 chars, no "R$", no "\n", no "\xa0"

Price in `span.VbBaOe` with non-breaking space: "R$\xa02.184,05".

## Magalu — nm-* Design System

Migrated 2024/2025. Old `data-testid` selectors are all gone.
Current: `li[class*="nm-product-card"]` for items.
API intercept: `/api/product-search/v3/queries/search` returns JSON.
Seller field is polymorphic: string OR dict with `.name` key.
