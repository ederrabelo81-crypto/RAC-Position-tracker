# RAC Position Tracker — Claude Code Guide

Bot Python de monitoramento de posicionamento e precos de ar condicionado (RAC)
em marketplaces e dealers especializados do Brasil. Coleta dados de 8 marketplaces
+ 13 dealers via Playwright headless, exporta CSV padronizado para analise competitiva.

---

## Session Start Protocol

**MANDATORY** — load these 4 files (~800 tokens):

```
1. CLAUDE.md                          (this file)
2. .claude/COMMON_MISTAKES.md         ⚠️ CRITICAL — erros recorrentes
3. .claude/QUICK_START.md             comandos essenciais
4. .claude/ARCHITECTURE_MAP.md        onde esta cada coisa
```

**Then load task-specific docs** (~500-1500 tokens):
- See `docs/INDEX.md` for navigation by task type

**NEVER auto-load:**
- `.claude/completions/**` — only on explicit request
- `.claude/sessions/**` — only on explicit request
- `docs/archive/**` — only on explicit request

---

## Quick Start

```bash
# Run completo (marketplaces ativos + dealers)
python main.py --platforms ml magalu amazon google_shopping leroy dealers --pages 1

# Somente dealers
python main.py --platforms dealers --pages 2

# Demo rapido (ML, 1 keyword, 1 pagina)
python main.py

# Com browser visivel (debug)
python main.py --platforms dealers --pages 1 --no-headless
```

---

## Architecture Quick Reference

```
config.py              ← Keywords, ACTIVE_PLATFORMS, BRANDS, delays, User-Agents
main.py                ← CLI (argparse), scraper loop, CSV export (pandas)
scrapers/base.py       ← BaseScraper ABC: Playwright lifecycle, stealth JS, _build_record()
scrapers/dealers.py    ← DealerScraper: 13 sites, DEALER_CONFIGS, JSON-LD, VTEX __RUNTIME__
scrapers/<platform>.py ← MLScraper, AmazonScraper, MagaluScraper, etc.
utils/text.py          ← parse_price(), parse_rating(), normalize_text()
utils/brands.py        ← extract_brand() — regex word boundary contra BRANDS list
output/                ← CSVs datados: rac_monitoramento_YYYYMMDD_HHMM.csv
logs/                  ← Loguru logs + dealer_debug_*.html (diagnostico)
```

---

## CSV Output Columns

```
Data; Turno; Horario; Analista; Plataforma; Tipo Plataforma;
Keyword Buscada; Categoria Keyword; Marca Monitorada; Produto/SKU;
Posicao Organica; Posicao Patrocinada; Posicao Geral; Preco (R$);
Seller/Vendedor; Fulfillment?; Avaliacao; Qtd Avaliacoes; Tag Destaque
```

---

## Code Style

- Python 3.10+ (type hints, `Optional`, `List[Dict]`)
- Loguru for all logging (`logger.info/warning/error/success/debug`)
- BeautifulSoup `html.parser` (not lxml)
- Playwright sync API (not async)
- `tenacity` `@retry` decorator on `search()` methods
- Prices: `parse_price()` returns `Optional[float]`, handles R$/\xa0
- CSV: UTF-8 BOM (`;` separator) — Excel PT-BR compatible

---

## Key Patterns

### Adding a new marketplace scraper
1. Create `scrapers/new_site.py` inheriting `BaseScraper`
2. Implement `search(keyword, keyword_category_map, page_limit)` -> `List[Dict]`
3. Register in `main.py` `SCRAPER_REGISTRY`
4. Add to `config.py` `ACTIVE_PLATFORMS` and `PLATFORM_TYPE`

### Adding a new dealer
1. Add entry to `DEALER_CONFIGS` in `scrapers/dealers.py`
2. Add to `config.py` `PLATFORM_TYPE`
3. No changes to main.py needed

### Anti-bot strategy
- Playwright stealth JS (webdriver=undefined, chrome.loadTimes, plugins)
- Chrome real > msedge > Chromium fallback
- `_random_delay()`, `_human_scroll()`, `_wait_for_network_idle()`
- Magalu: `_rotate_browser()` every 15 keywords (Radware Bot Manager)
- Dealers: `_is_blocked_page()` detects reCAPTCHA/Cloudflare

---

## Documentation Navigation

| Need | Load |
|------|------|
| Fix scraper bugs | `docs/learnings/scraping-patterns.md` |
| Anti-bot / CAPTCHA | `docs/learnings/anti-bot-strategies.md` |
| Dealer config | `docs/learnings/dealer-configs.md` |
| Debug / test | `docs/learnings/testing-debugging.md` |
| Full reference | `docs/QUICK_REFERENCE.md` |
| Task navigation | `docs/INDEX.md` |

---

## Platform Status (Apr 2026)

| Platform | Status | Notes |
|----------|--------|-------|
| Mercado Livre | ✅ | Popup CEP handled |
| Amazon | ✅ | Seller via "Vendido por" pattern |
| Magalu | ✅ | nm-* selectors + Radware rotation |
| Google Shopping | ✅ | div.rwVHAc + leaf-div title |
| Leroy Merlin | ✅ | Algolia API direct |
| Dealers (13) | ✅ | JSON-LD + VTEX + DOM fallback |
| Shopee | ⏸️ | Needs authenticated session |
| Casas Bahia | ⏸️ | Akamai WAF |
| Fast Shop | ⏸️ | Pending validation |
