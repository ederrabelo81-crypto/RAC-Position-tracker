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
docs/learnings/scraping-patterns.md        ← CSS selectors, parsing, fallbacks
docs/learnings/anti-bot-strategies.md      ← Stealth, CAPTCHA, rotation
docs/learnings/dealer-configs.md           ← Per-dealer reference
docs/learnings/testing-debugging.md        ← Debug workflows
docs/archive/                              ← Historical docs
```
