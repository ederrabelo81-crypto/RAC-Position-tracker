# Quick Reference — RAC Position Tracker

## Session Start Checklist

1. Activate venv
2. Check `config.py` ACTIVE_PLATFORMS for which platforms are enabled
3. `python scripts/pipeline_watch.py --no-notify` — which jobs ran / didn't run today
4. Check recent logs in `logs/` (`bot_*.log`, `heartbeat.jsonl`) and CSVs in `output/`

Where things run (Out/2026): PC collector at 08/14/20h BRT for every platform
except Amazon; Amazon, PriceTrack and supervision on GitHub Actions. See
`docs/MAPA_COLETAS.md`.

## Platform Aliases (for --platforms flag)

```
ml, ml_api, amazon, magalu, casasbahia, google_shopping, leroy, shopee, fast, dealers, all
```

`ml_api` só roda quando pedido explicitamente (não entra em `all`).

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
| Akamai Bot Manager (Magalu) | `"Não é possível acessar a página"` no HTML | Abortar imediatamente, log 🚫, sem retry |
| Radware (Magalu — histórico) | `<title>Radware Bot Manager Captcha</title>` | `_rotate_browser()` + retry |
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
- Turno: `Abertura` (≤11h), `Tarde` (≤17h), `Fechamento` — BRT, ou `RAC_TURNO`
- Sidecar `<csv>.run_id`: `scripts/upload_csv.py` e `scripts/history_cli.py` reusam o run_id; `reenviar_csv.py` e `scripts/upload_magalu_csv.py` ainda geram outro (duplicam)
