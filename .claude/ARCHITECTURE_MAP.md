# Architecture Map — RAC Position Tracker

## Directory Structure

```
RAC-Position-tracker/
├── config.py                  # Central config: keywords, platforms, brands, delays
├── main.py                    # CLI entry point, scraper orchestration, CSV export
├── diagnostico.py             # Debug/testing utilities
├── teste.py                   # Test suite
├── requirements.txt           # Python dependencies
│
├── scrapers/
│   ├── __init__.py
│   ├── base.py                # BaseScraper ABC (Playwright lifecycle, stealth, _build_record)
│   ├── mercado_livre.py       # MLScraper — Mercado Livre
│   ├── amazon.py              # AmazonScraper — Amazon BR
│   ├── magalu.py              # MagaluScraper — Magazine Luiza (nm-* design + Radware)
│   ├── google_shopping.py     # GoogleShoppingScraper — Google Shopping PLAs
│   ├── leroy_merlin.py        # LeroyMerlinScraper — Algolia API direct
│   ├── shopee.py              # ShopeeScraper — ⏸️ stand-by (auth needed)
│   ├── casas_bahia.py         # CasasBahiaScraper — ⏸️ stand-by (Akamai WAF)
│   ├── fast_shop.py           # FastShopScraper — ⏸️ stand-by
│   └── dealers.py             # DealerScraper — 13 dealers (VTEX/WooCommerce/custom)
│
├── utils/
│   ├── text.py                # parse_price, parse_rating, parse_review_count, normalize_text
│   ├── brands.py              # extract_brand() — regex matching against BRANDS list
│   ├── session_grabber.py     # Auth session capture for Shopee/Casas Bahia
│   └── discover_shopee_api.py # Shopee API discovery
│
├── output/                    # Generated CSVs (rac_monitoramento_*.csv)
└── logs/                      # Loguru logs + debug HTML dumps
```

## Data Flow

```
config.py (keywords, platforms)
    ↓
main.py (argparse → resolve platforms → loop)
    ↓
_run_scraper(scraper_cls, keywords_map, page_limit)
    ↓
with Scraper(headless) as s:      ← BaseScraper.__enter__ → _launch()
    for keyword in keywords:
        s.search(keyword, ...)    ← platform-specific implementation
            ↓
            _page.goto(url)
            _wait_for_products()
            _human_scroll()
            ↓
            _parse_results(html)  ← CSS selectors / API intercept / JSON-LD
            ↓
            _build_record(...)    ← normalize, extract_brand, parse_price
    ↓
all_records → DataFrame → CSV (output/)
```

## Key Classes & Methods

### BaseScraper (scrapers/base.py)
- `_launch()` — Playwright start, Chrome→msedge→Chromium fallback, stealth JS
- `_close()` — Clean browser shutdown
- `_rotate_browser()` — Close + relaunch with new User-Agent (Radware mitigation)
- `_build_record()` — Standardized dict for DataFrame row
- `_random_delay()`, `_human_scroll()`, `_wait_for_network_idle()`

### DealerScraper (scrapers/dealers.py)
- `DEALER_CONFIGS` — Dict with URL, pagination type, max_pages, item_selector
- `_detect_items()` — CSS selector chain with max_items sanity check
- `_extract_price_el()` — 5-level price fallback (CSS→VTEX split→data-price→meta→regex)
- `_extract_jsonld_prices()` — schema.org/Product from `<script type="application/ld+json">`
- `_jsonld_match()` — Word-intersection matching (normalize + Jaccard ≥60%)
- `_deduplicate()` — By (platform, title), reassigns positions after dedup
- `_is_blocked_page()` — Detects reCAPTCHA/Cloudflare

### Config (config.py)
- `KEYWORDS_LIST` — `List[Keyword]` with term, category, priority
- `KEYWORDS` — Dict `{category: [terms]}` (legacy compat)
- `ACTIVE_PLATFORMS` — `Dict[str, bool]`
- `BRANDS` — Ordered list (most specific first: "Springer Midea" before "Midea")
- `PLATFORM_TYPE` — Maps platform name → type string for CSV

## File Location Quick Lookup

| Need to change... | File |
|---|---|
| Keywords | `config.py` KEYWORDS_LIST |
| Active platforms | `config.py` ACTIVE_PLATFORMS |
| Brand list | `config.py` BRANDS |
| Delay/timeout values | `config.py` MIN_DELAY, MAX_DELAY, PAGE_TIMEOUT |
| Dealer URLs/selectors | `scrapers/dealers.py` DEALER_CONFIGS |
| Price parsing | `utils/text.py` parse_price() |
| Brand detection | `utils/brands.py` extract_brand() |
| CSV columns | `main.py` COLUMN_ORDER |
| User-Agent list | `config.py` USER_AGENTS |
| Stealth JS patches | `scrapers/base.py` _STEALTH_JS |
