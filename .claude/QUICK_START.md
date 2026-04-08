# Quick Start — RAC Position Tracker

## Essential Commands

```bash
# Full collection (marketplaces + dealers, 1 page)
python main.py --platforms ml magalu amazon google_shopping leroy dealers --pages 1

# Only dealers
python main.py --platforms dealers --pages 2

# Specific platforms
python main.py --platforms ml magalu --pages 1

# With visible browser (debugging)
python main.py --platforms dealers --pages 1 --no-headless

# Demo mode (ML only, 1 keyword)
python main.py

# All active platforms (respects ACTIVE_PLATFORMS in config.py)
python main.py --pages 1

# Custom keywords
python main.py --platforms ml --keywords "ar condicionado inverter 12000"
```

## Virtual Environment

```bash
# Activate (Windows)
venv\Scripts\activate

# Activate (Linux/Mac)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
python -m playwright install chromium
```

## Output

- CSV: `output/rac_monitoramento_YYYYMMDD_HHMM.csv`
- Logs: `logs/bot_YYYYMMDD_HHMMSS.log`
- Debug HTML: `logs/dealer_debug_<name>_p<N>.html`

## Common Workflows

### Debug a dealer with 0 products
1. Run with `--no-headless` to see the browser
2. Check `logs/dealer_debug_<name>_p1.html` for the captured HTML
3. Inspect HTML to find the correct CSS selectors
4. Update `DEALER_CONFIGS` in `scrapers/dealers.py`

### Add a new dealer
1. Add entry to `DEALER_CONFIGS` in `scrapers/dealers.py`
2. Add `"DealerName": "Regional Especializado"` to `PLATFORM_TYPE` in `config.py`
3. Run: `python main.py --platforms dealers --pages 1`

### Fix price extraction for a dealer
1. Open the debug HTML in a browser
2. Inspect where the price element is
3. Check if JSON-LD `<script type="application/ld+json">` has prices
4. Add site-specific selector to `DEALER_CONFIGS` or `_SELECTORS["price_candidates"]`

### Add a new keyword
1. Edit `KEYWORDS_LIST` in `config.py`
2. Set `category` and `priority` ("alta", "media", "baixa")

## Platform Registry (main.py)

```
ml, magalu, amazon, shopee, casasbahia, google_shopping, leroy, fast, dealers
```
