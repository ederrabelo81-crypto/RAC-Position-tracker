# Dealer Configs — RAC Position Tracker

## DEALER_CONFIGS Reference (scrapers/dealers.py)

| Dealer | URL | Pagination | max_pages | Overrides | Status (Mai 2026) |
|--------|-----|------------|-----------|-----------|--------|
| Frigelar | /busca?q=ar+condicionado+split+inverter | vtex | 3 | — | ✅ Validado smoke test 03/05 |
| CentralAr | /ar-condicionado/inverter/c/INVERTER | vtex | 5 | — | ❌ Parado desde 26/04 — Sprint 1 |
| Eletrozema | — | vtex | 5 | — | ❌ Parado desde 26/04 — Sprint 1 (causa comum CentralAr) |
| Dufrio | /ar-condicionado/ar-condicionado-split-inverter | vtex | 5 | — | ❌ Parado desde 29/04 — Sprint 1 |
| PoloAr | /ar-condicionado/inverter?...page=0 | param_zero | 5 | — | ❌ Parado desde 13/04 — Sprint 2 |
| Climario | /ar-condicionado?order=OrderByTopSaleDESC | vtex | 5 | — | ❌ Parado desde 03/04 — Sprint 2 |
| FrioPecas | /ar-condicionado/ar-condicionado-split-inverter | vtex | 5 | — | ❌ Parado desde 03/04 — Sprint 2 |
| Leveros | /ar-condicionado/inverter | vtex | 5 | item_selector_candidates (5 options) | ❌ Parado desde 02/04 — Sprint 2 |
| WebContinental | /climatizacao/ar-condicionado/...hi-wall | vtex | 5 | — | ❌ Parado desde 02/04 — Sprint 2 |
| Belmicro | /climatizacao | vtex | 5 | — | ⚠️ Price via JSON-LD |
| GoCompras | /ar-condicionado/split-hi-wall/ | query | 5 | — | ✅ |
| ArCerto | /categoria/ar-condicionado-inverter/ | woocommerce | 1 | — | ⚠️ p2 Cloudflare |
| FerreiraCoasta | /Destaque/split-inverter-subcategoria | query | 5 | infinite_scroll: True | ✅ |
| EngageEletro | /ar-e-clima/ar-condicionado/ | query | 5 | item_selector: ".cardprod" | ⚠️ Custom platform |

## How to Add a New Dealer

```python
# In scrapers/dealers.py DEALER_CONFIGS:
"NewDealer": {
    "url":        "https://www.newdealer.com.br/ar-condicionado/",
    "pagination": "vtex",    # vtex | param_zero | woocommerce | query
    "max_pages":  5,
    # Optional overrides:
    "item_selector":           ".product-card",      # single CSS selector
    "item_selector_candidates": [".product-card", ".product-item"],  # list
    "infinite_scroll":         False,                 # for sites without pagination
}

# In config.py PLATFORM_TYPE:
"NewDealer": "Regional Especializado",
```

## Pagination Strategies

- **vtex**: Appends `?page=N` or `&page=N` to URL
- **param_zero**: Replaces existing `page=0` with `page=N-1` (0-indexed)
- **woocommerce**: Inserts `/page/N/` in the URL path
- **query**: Same as vtex (generic `?page=N`)

## VTEX Site Identification

VTEX sites typically have:
- URL patterns: `/c`, `/busca`, `?order=OrderBy*`
- `window.__RUNTIME__` JS object in the page
- Classes like `vtex-product-summary-2-x-*`
- JSON-LD with schema.org/Product structured data

## Known Problematic Patterns

### Price loaded via separate API (VTEX IO)
- Prices are fetched after DOMContentLoaded via separate XHR
- `_wait_for_prices()` waits up to 7s for price selectors
- JSON-LD is the most reliable fallback (preloaded in HTML)

### Carousel/gallery duplicates (Leveros)
- Product cards show N images each in DOM as separate elements
- `seen_titles_this_page` set deduplicates within a page
- `_deduplicate()` handles cross-page duplicates

### Custom platforms (EngageEletro)
- Uses `cardprod` class (not VTEX/WooCommerce)
- Requires `item_selector` override in config
- Product names may be in JS variables, not text elements
