# Quick Reference — RAC Position Tracker

## Session Start Checklist

1. Activate venv
2. Check `config.py` ACTIVE_PLATFORMS for which platforms are enabled
3. Check recent logs in `logs/` for last run status
4. Check recent CSVs in `output/` for data quality

## Platform Aliases (for --platforms flag)

```
ml, magalu, amazon, shopee, casasbahia, google_shopping, leroy, fast, dealers
```

## Scraper Extraction Patterns

### Marketplace scrapers (keyword-based)
```
search(keyword) → goto(url) → wait_for_products() → parse DOM/API → _build_record()
```

### DealerScraper (URL-based, no keywords)
```
search(dealer_name) → DEALER_CONFIGS[name] → goto(url) → scroll → wait_for_prices()
  → _try_vtex_runtime()        # VTEX JS state
  → _parse_results_dom()       # CSS fallback
    → _extract_price_el()      # CSS → VTEX split → data-price → meta → regex
    → _jsonld_match()          # schema.org/Product fallback
    → index fallback           # positional assignment when counts match
```

## Dealer Pagination Types

| Type | URL Pattern | Used by |
|------|------------|---------|
| vtex | `?page=2` / `&page=2` | Frigelar, CentralAr, Belmicro, FrioPecas, WebContinental, Dufrio, Leveros, Climario |
| param_zero | `page=0` → `page=1` (0-indexed) | PoloAr |
| woocommerce | `/page/2/` in path | ArCerto |
| query | `?page=2` (generic) | GoCompras, FerreiraCoasta, EngageEletro |

## Price Extraction Fallback Chain

```
1. CSS selectors (sellingPrice, spotPrice, skuBestPrice, etc.)
2. VTEX split (currencyInteger + currencyDecimalSeparator + currencyDecimalDigits)
3. [data-price] attribute
4. meta[itemprop="price"] content attribute
5. Regex R$ in full item text
6. JSON-LD schema.org/Product (word-intersection matching)
7. Index-based fallback (when record count ≈ JSON-LD count)
```

## Anti-Bot Detection Quick Reference

| Threat | Detector | Response |
|--------|----------|----------|
| Radware (Magalu) | `<title>Radware Bot Manager Captcha</title>` | `_rotate_browser()` + retry |
| reCAPTCHA | `grecaptcha.render` in HTML | Break, dump debug HTML |
| Cloudflare | Title "Um momento" / "Just a moment" | Break, dump debug HTML |
| PerimeterX (Magalu) | `#px-captcha` selector | Log warning, return empty |

## Brand Matching Order (config.py BRANDS)

Most specific first: `"Springer Midea"` before `"Midea"` before `"Springer"`.
`extract_brand()` uses regex word boundaries: `\bMidea\b` (case-insensitive).

## CSV Format

- Separator: `;` (semicolon)
- Encoding: UTF-8 BOM (`utf-8-sig`) for Excel PT-BR
- Price: float (`.` decimal), e.g., `2499.00`
- Missing values: empty string (pandas NaN → blank in CSV)
