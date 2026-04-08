# Common Mistakes — RAC Position Tracker

## 1. VTEX Price Not Extracted (0% price)

**Wrong:** Rely only on CSS selectors for VTEX sites.
**Why:** VTEX IO loads prices via separate fetch AFTER DOMContentLoaded. CSS selectors find empty elements.
**Right:** Use 5-level fallback: CSS selectors -> VTEX split price (currencyInteger+Decimal) -> [data-price] -> meta[itemprop="price"] -> JSON-LD schema.org/Product -> regex R$.
**Files:** `scrapers/dealers.py` `_extract_price_el()`, `_extract_jsonld_prices()`, `_jsonld_match()`

## 2. Google Shopping Title Concatenation

**Wrong:** Use `aria-label` or container text for titles.
**Why:** Google Shopping concatenates "Product name + R$ price + seller" in aria-label.
**Right:** Use leaf-div strategy: first `<div>` with no children AND no class, 15-200 chars, no R$/\n/\xa0.
**Files:** `scrapers/google_shopping.py` `_extract_title()`

## 3. Selector Returns Too Many Items (Leveros 775 bug)

**Wrong:** Use broad selectors like `[class*="product-card"]` without validation.
**Why:** Many sites use "product-card" class for UI components beyond the main grid (sidebars, recommendations, carousels).
**Right:** `_detect_items()` has max_items=120 sanity check. Use `item_selector_candidates` (list) in DEALER_CONFIGS for sites with known layouts.
**Files:** `scrapers/dealers.py` `_detect_items()`

## 4. parse_price Fails on Non-Breaking Space

**Wrong:** Assume `\s` in regex matches all whitespace.
**Why:** Google Shopping uses `\xa0` (non-breaking space) in prices like "R$\xa02.184,05".
**Right:** Include `\xa0` explicitly: `re.sub(r"[R$\s\xa0]", "", raw)`.
**Files:** `utils/text.py` `parse_price()`

## 5. Magalu CAPTCHA After ~25 Keywords

**Wrong:** Use same browser context for all keywords.
**Why:** Radware Bot Manager builds fingerprint profile across requests; triggers CAPTCHA at ~25.
**Right:** `_rotate_browser()` every 15 keywords (proactive) + detect `<title>Radware Bot Manager Captcha</title>` and rotate on detection.
**Files:** `scrapers/magalu.py` `_is_radware_blocked()`, `scrapers/base.py` `_rotate_browser()`

## 6. One Scraper Crash Kills Entire Run

**Wrong:** No try/except around `_run_scraper()` in the main loop.
**Why:** If browser launch fails for one scraper (e.g., Leroy Merlin), `__enter__` raises and stops everything.
**Right:** Each `_run_scraper()` call is wrapped in try/except in `main.py main()`.
**Files:** `main.py` lines 329-345

## 7. Dealer Carousel Duplicates

**Wrong:** Dedup key includes position_organic — carousel images get different positions.
**Why:** Sites like Leveros show N gallery images per product as N DOM elements with same title.
**Right:** `seen_titles_this_page` set in `_parse_results_dom()` + `_deduplicate()` key is (platform, title) WITHOUT position. Positions reatributed after dedup.
**Files:** `scrapers/dealers.py`

## 8. Amazon Seller Field Captures Rating

**Wrong:** Use `.a-size-small.a-color-base` selector for seller name.
**Why:** Rating text "4,5 de 5 estrelas" matches the same class.
**Right:** Use `_extract_seller()` with text pattern matching: "Vendido por" split, `por ` prefix, length guards.
**Files:** `scrapers/amazon.py` `_extract_seller()`
