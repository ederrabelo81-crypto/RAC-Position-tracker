# Documentation Index — RAC Position Tracker

## Navigation by Task

### "I need to fix a scraper that returns 0 products"
1. Load `docs/learnings/scraping-patterns.md` (~600 tokens)
2. Check `.claude/COMMON_MISTAKES.md` items #3, #6
3. Look at `logs/dealer_debug_<name>_p1.html`

### "I need to fix price extraction"
1. Load `docs/learnings/scraping-patterns.md` (~600 tokens)
2. Check `.claude/COMMON_MISTAKES.md` items #1, #4
3. Key file: `scrapers/dealers.py` `_extract_price_el()`

### "I need to handle a CAPTCHA / anti-bot block"
1. Load `docs/learnings/anti-bot-strategies.md` (~500 tokens)
2. Check `.claude/COMMON_MISTAKES.md` item #5
3. Key files: `scrapers/base.py`, `scrapers/magalu.py`

### "I need to add or configure a dealer"
1. Load `docs/learnings/dealer-configs.md` (~600 tokens)
2. Key file: `scrapers/dealers.py` DEALER_CONFIGS

### "I need to debug a run / analyze CSV quality"
1. Load `docs/learnings/testing-debugging.md` (~400 tokens)
2. Check logs in `logs/bot_*.log`
3. Check debug HTML in `logs/dealer_debug_*.html`

### "I need to add a new marketplace scraper"
1. Load `docs/learnings/scraping-patterns.md` (~600 tokens)
2. Reference: `scrapers/base.py` (BaseScraper interface)
3. Example: `scrapers/amazon.py` (well-structured marketplace scraper)

### "I need to automate Shopee / Magalu / Casas Bahia collection"
1. Load `docs/AUTOMACAO_COLETAS_AUTENTICADAS.md` (CDP + auto session refresh)
2. Key files: `scripts/refresh_sessions_cdp.py`,
   `scripts/collect_authenticated_cdp.bat`,
   `scripts/setup_authenticated_scheduler.ps1`
3. Background: `docs/cdp_magalu_collection.md` (CDP setup + troubleshooting)

### "I need to run a manual collection via the Claude Chrome Extension"
1. Load the platform guide: `docs/manual_magalu_collection.md`,
   `docs/manual_shopee_collection.md` or `docs/manual_casasbahia_collection.md`
2. Each guide has the extraction prompt + the "Economia de tokens" section
3. Prefer the automated path above when the CDP Chrome is available

### "I need to work with PriceTrack data (price source of truth)"
1. Load `docs/PRICETRACK_INSIGHTS.md` (pipeline + insight/improvement roadmap)
2. Key files: `scripts/pricetrack_api_import.py`, `pricetrack_importer/`

### "I need to diagnose broken field coverage (buy box, rating, sponsored)"
1. Load `docs/DIAGNOSTICO_COLETA_JUN2026.md` (root causes per platform)
2. Dashboard page 🩺 Data Health (field × platform matrix)
3. ML live check: `python scripts/diagnose_ml.py`

### "I need to orchestrate or automate collection with n8n"
1. Load `docs/n8n_orchestration.md`
2. Key file: `n8n/rac_coleta_monitor.json` (workflow: notifications + CSV ingestion)

---

## Token Cost Estimates

| Document | Tokens | Auto-loaded? |
|----------|--------|-------------|
| CLAUDE.md | ~400 | Yes |
| .claude/COMMON_MISTAKES.md | ~350 | Yes |
| .claude/QUICK_START.md | ~200 | Yes |
| .claude/ARCHITECTURE_MAP.md | ~300 | Yes |
| **Session start total** | **~1,250** | |
| docs/learnings/scraping-patterns.md | ~600 | No |
| docs/learnings/anti-bot-strategies.md | ~500 | No |
| docs/learnings/dealer-configs.md | ~600 | No |
| docs/learnings/testing-debugging.md | ~400 | No |
| **Typical task total** | **~1,850** | |

---

## All Documentation Files

```
CLAUDE.md                                  ← Project overview + session protocol
.claude/COMMON_MISTAKES.md                 ← Critical anti-patterns (8 items)
.claude/QUICK_START.md                     ← Commands and workflows
.claude/ARCHITECTURE_MAP.md                ← File locations and data flow
.claude/LEARNINGS_INDEX.md                 ← Quick pointer to learnings
.claude/DOCUMENTATION_MAINTENANCE.md       ← When to update docs
.claude/completions/                       ← Task completion records
.claude/sessions/                          ← Session context files
.claude/templates/                         ← Templates for completions/sessions
docs/INDEX.md                              ← This file
docs/QUICK_REFERENCE.md                    ← Fast lookups
docs/AUTOMACAO_COLETAS_AUTENTICADAS.md     ← Shopee/Magalu/CB automation (CDP + sessions)
docs/PRICETRACK_INSIGHTS.md                ← PriceTrack pipeline + insight roadmap
docs/DIAGNOSTICO_COLETA_JUN2026.md         ← Field coverage diagnosis (buy box, ML fix)
docs/cdp_magalu_collection.md              ← Chrome CDP setup (Windows + Task Scheduler)
docs/n8n_orchestration.md                  ← n8n workflow: scheduling + CSV ingestion
docs/manual_magalu_collection.md           ← Magalu collection via Claude Chrome Extension
docs/manual_shopee_collection.md           ← Shopee collection via Claude Chrome Extension
docs/manual_casasbahia_collection.md       ← Casas Bahia collection via Claude Chrome Extension
docs/learnings/scraping-patterns.md        ← CSS selectors, parsing, fallbacks
docs/learnings/anti-bot-strategies.md      ← Stealth, CAPTCHA, rotation
docs/learnings/dealer-configs.md           ← Per-dealer reference
docs/learnings/testing-debugging.md        ← Debug workflows
docs/archive/                              ← Historical docs
```
