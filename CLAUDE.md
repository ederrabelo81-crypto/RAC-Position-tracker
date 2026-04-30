# RAC Position Tracker — Development Guidelines & Standards

> **Project:** RAC Price Monitor — Retail Analytics & Competitive Intelligence  
> **Domain:** E-commerce price scraping & competitive intelligence for air conditioning market in Brazil  
> **Stack:** Python 3.10+, Playwright, BeautifulSoup, Pandas, Supabase, Streamlit  
> **Status:** ✅ Production | Oracle Cloud VM (Brazil East) + GitHub Actions (manual backup)

---

## Table of Contents

1. [Session Start Protocol](#session-start-protocol)
2. [Coding Standards and Preferences](#coding-standards-and-preferences)
3. [Project Architecture Overview](#project-architecture-overview)
4. [Git Workflow Rules](#git-workflow-rules)
5. [Testing Requirements](#testing-requirements)
6. [Documentation Standards](#documentation-standards)
7. [Deployment & Infrastructure](#deployment--infrastructure)
8. [Quick Reference](#quick-reference)

---

## Session Start Protocol

**MANDATORY** — Load these 4 files at session start (~1,250 tokens):

```markdown
1. CLAUDE.md                          ← This file
2. .claude/COMMON_MISTAKES.md         ⚠️ CRITICAL — 8 recurring anti-patterns
3. .claude/QUICK_START.md             ← Essential commands & workflows
4. .claude/ARCHITECTURE_MAP.md        ← File locations & data flow
```

**Then load task-specific docs** (~500-1,500 tokens):
- See `docs/INDEX.md` for navigation by task type

**NEVER auto-load:**
- `.claude/completions/**` — Only on explicit request
- `.claude/sessions/**` — Only on explicit request
- `docs/archive/**` — Historical docs only when needed

---

## Coding Standards and Preferences

### Python Style Guide

**Target:** Python 3.10+ with strict type hints

```python
# ✅ Good — Explicit types, docstrings, proper naming
from typing import List, Dict, Optional
from loguru import logger

def parse_price_brazil(raw_text: Optional[str]) -> Optional[float]:
    """
    Parser robusto de preço brasileiro com regex.
    
    Args:
        raw_text: String bruta do HTML (ex: "R$ 1.994,91")
    
    Returns:
        Float parseado ou None se inválido
    
    Raises:
        ValueError: Se formato não reconhecido
    """
    if not raw_text:
        return None
    # Implementation...
```

```python
# ❌ Bad — No types, vague names, missing docs
def parse_price(t):
    if not t:
        return None
    # What format? What exceptions?
```

### Naming Conventions

| Type | Convention | Example |
|------|-----------|---------|
| Variables | snake_case | `user_profile`, `price_list` |
| Functions | snake_case | `calculate_total()`, `extract_brand()` |
| Classes | PascalCase | `BaseScraper`, `DealerScraper` |
| Constants | UPPER_SNAKE_CASE | `MAX_PAGES`, `USER_AGENTS` |
| Private methods | Leading underscore | `_launch()`, `_parse_results()` |

### Function Design Principles

**Rule of 3:** Maximum 3 parameters. Use dataclasses or dicts for more.

```python
# ✅ Good — Using dataclass for complex options
from dataclasses import dataclass

@dataclass
class ScraperConfig:
    headless: bool = True
    page_limit: int = 3
    priority_filter: Optional[List[str]] = None

async def run_scraper(config: ScraperConfig) -> List[Dict]:
    pass
```

```python
# ❌ Bad — Too many parameters
async def run_scraper(headless, page_limit, priority_filter, 
                      output_dir, log_level, retry_attempts, 
                      timeout, user_agent):
    pass
```

### Error Handling Strategy

```python
# ✅ Good — Specific exceptions with context
class ScraperBlockedException(Exception):
    """Raised when anti-bot detection blocks the scraper."""
    pass

try:
    results = await scraper.search(keyword)
except ScraperBlockedException as e:
    logger.warning(f"Blocked by {scraper.platform_name}: {e}")
    return []
except TimeoutError as e:
    logger.error(f"Timeout searching '{keyword}': {e}")
    raise
```

```python
# ❌ Bad — Bare except, no context
try:
    results = await scraper.search(keyword)
except:
    print("Error")
    return []
```

### Logging Standards

**Use Loguru exclusively** — No print statements in production code.

```python
from loguru import logger

# ✅ Good — Structured logging with levels
logger.info(f"Starting collection for {platform} ({len(keywords)} keywords)")
logger.debug(f"Parsed {len(items)} items from page {page}")
logger.warning(f"CAPTCHA detected on {dealer_name}")
logger.error(f"Failed to upload to Supabase: {error}")
logger.success(f"CSV exported: {csv_path}")
```

### Anti-Patterns to Avoid

See `.claude/COMMON_MISTAKES.md` for critical examples:

1. **VTEX Price Extraction** — Never rely only on CSS selectors; use 5-level fallback
2. **Google Shopping Titles** — Never use aria-label; use leaf-div strategy
3. **Magalu CAPTCHA** — Never skip browser rotation; rotate every 15 keywords
4. **Price Parsing** — Always handle non-breaking space (`\xa0`) explicitly
5. **Deduplication** — Never include position in dedup key for carousel products

---

## Project Architecture Overview

### Directory Structure

```
rac-position-tracker/
├── config.py                    # Central configuration: keywords, platforms, brands
├── main.py                      # CLI entry point, orchestration, CSV export
├── app.py                       # Streamlit dashboard (6 pages + CI with Claude)
├── diagnostico.py               # Debug utilities
├── requirements.txt             # Python dependencies
│
├── scrapers/
│   ├── __init__.py
│   ├── base.py                  # BaseScraper ABC (Playwright lifecycle, stealth)
│   ├── mercado_livre.py         # MLScraper
│   ├── amazon.py                # AmazonScraper
│   ├── magalu.py                # MagaluScraper (Radware mitigation)
│   ├── google_shopping.py       # GoogleShoppingScraper
│   ├── leroy_merlin.py          # LeroyMerlinScraper (Algolia API)
│   ├── dealers.py               # DealerScraper (13+ dealers, JSON-LD, VTEX)
│   └── [shopee, casas_bahia, fast_shop].py  # Stand-by
│
├── utils/
│   ├── text.py                  # parse_price, parse_rating, now_brt(), normalize
│   ├── brands.py                # extract_brand() regex matching
│   ├── session_grabber.py       # Auth session capture
│   ├── supabase_client.py       # Upload, cleanup, maintenance
│   └── n8n_notify.py            # Telegram notifications (N8N + fallback)
│
├── scripts/
│   ├── oracle_setup.sh          # VM setup script
│   ├── collect_manha_linux.sh   # Morning collection (10:00 BRT)
│   ├── collect_noite_linux.sh   # Night collection (21:00 BRT)
│   ├── fix_turno.py             # Database cleanup utilities
│   └── monitor.sh               # Log monitoring
│
├── n8n/
│   └── rac_coleta_monitor.json  # N8N workflow (Webhook → Telegram)
│
├── .github/workflows/
│   └── collect.yml              # GitHub Actions (manual dispatch only)
│
├── .claude/                     # AI assistant documentation
│   ├── COMMON_MISTAKES.md
│   ├── QUICK_START.md
│   ├── ARCHITECTURE_MAP.md
│   └── templates/
│
├── docs/                        # Technical documentation
│   ├── INDEX.md                 # Navigation by task
│   ├── QUICK_REFERENCE.md
│   ├── DASHBOARD_FILTERS.md
│   └── learnings/
│       ├── scraping-patterns.md
│       ├── anti-bot-strategies.md
│       ├── dealer-configs.md
│       └── testing-debugging.md
│
├── output/                      # Generated CSVs
├── logs/                        # Loguru logs + debug HTML dumps
└── .env                         # Environment variables (gitignored)
```

### Data Flow Architecture

```
config.py (keywords, platforms, brands)
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
            _parse_results(html)  ← CSS / API / JSON-LD extraction
            ↓
            _build_record(...)    ← Normalize, extract_brand, parse_price
    ↓
all_records → DataFrame → CSV (output/) → Supabase → Telegram notification
```

### Layer Responsibilities

```python
# Controller Layer (main.py) — CLI handling, orchestration
def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    records = _run_scraper(DealerScraper, keywords, pages)
    export_to_csv(records)
    upload_to_supabase(records)
    send_telegram_notification(summary)

# Service Layer (scrapers/*.py) — Scraping business logic
class DealerScraper(BaseScraper):
    def search(self, keyword: str, page_limit: int) -> List[Dict]:
        for page in range(1, page_limit + 1):
            html = self._fetch_page(keyword, page)
            items = self._parse_results_dom(html)
            prices = self._extract_jsonld_prices(html)
            return self._deduplicate(items)

# Repository Layer (utils/supabase_client.py) — Data persistence
def upload_to_supabase(records: List[Dict]) -> bool:
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return client.table("monitoramento").insert(records).execute()

# Utility Layer (utils/*.py) — Pure functions, helpers
def parse_price_brazil(raw: str) -> Optional[float]:
    # Regex parsing logic
    pass
```

### Key Configuration Points

| Need to Change | File | Location |
|---------------|------|----------|
| Keywords | `config.py` | `KEYWORDS_LIST` |
| Active Platforms | `config.py` | `ACTIVE_PLATFORMS` |
| Brand List | `config.py` | `BRANDS` |
| Dealer URLs/Selectors | `scrapers/dealers.py` | `DEALER_CONFIGS` |
| Price Parsing | `utils/text.py` | `parse_price_brazil()` |
| Brand Detection | `utils/brands.py` | `extract_brand()` |
| CSV Columns | `main.py` | `COLUMN_ORDER` |
| User-Agents | `config.py` | `USER_AGENTS` |
| Stealth JS | `scrapers/base.py` | `_STEALTH_JS` |
| Delays/Timeouts | `config.py` | `MIN_DELAY`, `MAX_DELAY`, `PAGE_TIMEOUT` |

---

## Git Workflow Rules

### Branch Strategy

```
main ─────────────────────────────────────► (Production)
  ├─ feature/add-dealer-zenir
  ├─ bugfix/magalu-nm-selectors
  ├─ hotfix/supabase-upload-timeout
  └─ chore/update-dependencies-april-2026
```

### Branch Naming Convention

| Prefix | Purpose | Example |
|--------|---------|---------|
| `feature/` | New features or scrapers | `feature/add-carrefour-scraper` |
| `bugfix/` | Bug fixes | `bugfix/google-shopping-title-concat` |
| `hotfix/` | Critical production fixes | `hotfix/price-parser-x10-bug` |
| `chore/` | Maintenance, deps, configs | `chore/bump-playwright-1.50` |
| `docs/` | Documentation updates | `docs/add-dealer-config-guide` |

### Commit Message Format

**Use Conventional Commits:**

```
<type>(<scope>): <subject>

<body (optional)>

<footer (optional)>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `refactor`: Code restructuring (no behavior change)
- `docs`: Documentation changes
- `test`: Adding/updating tests
- `chore`: Build/config/maintenance

**Examples:**

```bash
# ✅ Good commits
feat(dealers): add Zenir and CenterKennedy dealers
fix(magalu): update nm-* selectors after redesign
fix(utils): handle non-breaking space in parse_price
refactor(scrapers): extract common JSON-LD logic to base class
docs(readme): clarify Supabase service_role key requirement
test(dealers): add unit tests for VTEX price extraction
chore(deps): bump playwright from 1.49 to 1.50

# ❌ Bad commits
update code
fix stuff
minor changes
wip
```

### Pull Request Guidelines

**PR Title:** Follow conventional commit format  
**PR Description Template:**

```markdown
## Changes
- Brief description of what changed

## Why
- Reason for the change (bug, feature, improvement)

## Testing
- [ ] Tested locally with --no-headless
- [ ] Verified CSV output columns
- [ ] Checked logs for errors

## Screenshots/Logs (if applicable)
```

### Pre-commit Checklist

```bash
# Before committing:
✅ Code runs without errors: python main.py --platforms ml --pages 1
✅ No print statements (use logger)
✅ Type hints added for new functions
✅ Docstrings for public functions
✅ Logs tested at appropriate levels
```

---

## Testing Requirements

### Testing Pyramid

```
        /\
       /  \      E2E Tests (10%)
      /----\     Full collection runs, Oracle VM validation
     /      \    
    /--------\   Integration Tests (20%)
   /          \  Supabase upload, Telegram notifications
  /------------\ 
 /              \ Unit Tests (70%)
/________________\ parse_price, extract_brand, JSON-LD matching
```

### Unit Test Examples

```python
# tests/test_price_parser.py
import pytest
from utils.text import parse_price_brazil

class TestParsePriceBrazil:
    def test_standard_format(self):
        assert parse_price_brazil("R$ 1.994,91") == 1994.91
    
    def test_no_space(self):
        assert parse_price_brazil("R$1.709,91") == 1709.91
    
    def test_non_breaking_space(self):
        assert parse_price_brazil("R$\xa02.184,05") == 2184.05
    
    def test_python_float_notation(self):
        assert parse_price_brazil("R$ 1829.0") == 1829.0
    
    def test_empty_string(self):
        assert parse_price_brazil("") is None
    
    def test_none_input(self):
        assert parse_price_brazil(None) is None
```

```python
# tests/test_brand_extraction.py
import pytest
from utils.brands import extract_brand

class TestExtractBrand:
    def test_exact_match(self):
        assert extract_brand("Ar Condicionado Midea 12000 BTUs") == "Midea"
    
    def test_word_boundary(self):
        # Should NOT match "Carrier" inside "portacarrier"
        assert extract_brand("Porta-carrier para ar condicionado") == "Desconhecida"
    
    def test_multiple_brands_first_wins(self):
        # BRANDS order matters: specific before general
        assert extract_brand("Springer Midea AI Ecomaster") == "Springer Midea"
```

### Integration Test Examples

```python
# tests/integration/test_supabase_upload.py
import pytest
from utils.supabase_client import upload_to_supabase

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("SUPABASE_URL"), reason="No Supabase credentials")
class TestSupabaseUpload:
    def test_upload_success(self):
        records = [{
            "Data": "2026-04-29",
            "Plataforma": "Mercado Livre",
            "Preço (R$)": 1994.91,
            # ... other required fields
        }]
        result = upload_to_supabase(records)
        assert result is True
    
    def test_upload_invalid_schema(self):
        records = [{"invalid_field": "value"}]
        with pytest.raises(Exception):
            upload_to_supabase(records)
```

### Manual Testing Workflows

**Before deploying any scraper change:**

```bash
# 1. Run with visible browser for visual confirmation
python main.py --platforms dealers --pages 1 --no-headless

# 2. Check debug HTML for zero-product dealers
ls -la logs/dealer_debug_*.html

# 3. Validate CSV output
head -5 output/rac_monitoramento_*.csv

# 4. Check logs for errors/warnings
grep -E "(ERROR|WARNING)" logs/bot_*.log | tail -20
```

### Test Data Requirements

- Minimum 3 keywords per category
- At least 1 dealer from each platform type (VTEX, WooCommerce, custom)
- Edge cases: empty prices, missing ratings, special characters

---

## Documentation Standards

### README Requirements

Every repository must have a README.md with:

```markdown
# Project Name
Brief description (1-2 sentences)

**Status:** ✅ Production | 🧪 Beta | ⏸️ Stand-by

## Features
- Feature 1
- Feature 2

## Quick Start
```bash
# Installation
pip install -r requirements.txt

# Basic usage
python main.py
```

## Configuration
Required environment variables in `.env`:
- `VAR_NAME`: Description

## Output Format
Description of generated files and their structure.

## Troubleshooting
Common issues and solutions.
```

### JSDoc/Docstring Standard

```python
def function_name(param1: Type, param2: Type) -> ReturnType:
    """
    One-line summary.
    
    Extended description if needed (multiple lines).
    
    Args:
        param1: Description of param1
        param2: Description of param2
    
    Returns:
        Description of return value
    
    Raises:
        ExceptionType: When this exception is raised
    
    Example:
        >>> function_name("value", 42)
        expected_result
    
    Note:
        Any additional notes or warnings
    """
```

### Documentation Updates

**When to update docs:**
- Adding/removing platforms → Update README platform table
- Changing CSV columns → Update README + docs/QUICK_REFERENCE.md
- New anti-bot pattern → Update docs/learnings/anti-bot-strategies.md
- Breaking changes → Update CLAUDE.md + .claude/COMMON_MISTAKES.md

---

## Deployment & Infrastructure

### Oracle Cloud VM Setup

**VM Specs:** Standard.E2.1.Micro (1 GB RAM, ARM64)  
**Location:** Brazil East (São Paulo)  
**Swap:** 2 GB (critical for avoiding OOM)

```bash
# SSH into VM
ssh -i ~/.ssh/oracle_key ubuntu@<vm-public-ip>

# Check swap status
free -h
sudo swapon --show

# Monitor cron execution
tail -f /var/log/syslog | grep CRON

# View bot logs
cd ~/rac-position-tracker
tail -f logs/bot_*.log
```

### Cron Schedule (BRT)

| Script | Time (BRT) | Platforms | Priority | Pages |
|--------|-----------|-----------|----------|-------|
| `collect_manha_linux.sh` | 10:00 | All | alta + media | 2 |
| `collect_noite_linux.sh` | 21:00 | All | alta | 1 |

### Environment Variables

**.env (local and VM):**

```env
# Supabase (required)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_service_role_key

# Anthropic (optional — Competitive Intelligence)
ANTHROPIC_API_KEY=sk-ant-...

# Analyst name for reports
ANALYST_NAME="Bot Automático Python"

# Telegram Notifications
TELEGRAM_BOT_TOKEN=7730291785:AAF...
N8N_TELEGRAM_CHAT_ID=123456789

# Optional N8N webhook
N8N_WEBHOOK_URL=http://localhost:5678/webhook/coleta
```

### GitHub Actions (Manual Backup)

Workflow: `.github/workflows/collect.yml`

**Trigger:** Manual dispatch only (no cron)  
**Purpose:** Backup when Oracle VM unavailable, testing

```yaml
# Usage: GitHub → Actions → RAC Price Collection → Run workflow
inputs:
  platforms: 'ml magalu amazon google_shopping leroy dealers'
  pages: '2'
  priority: ''  # empty = all priorities
```

---

## Quick Reference

### Essential Commands

```bash
# Activate virtual environment
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
python -m playwright install chromium

# Run collection
python main.py                                    # Demo (ML, 1 keyword)
python main.py --platforms dealers --pages 2      # All dealers
python main.py --platforms all --pages 1          # All active platforms
python main.py --no-headless --platforms ml       # Visible browser (debug)

# Dashboard
streamlit run app.py

# Database maintenance
python scripts/fix_turno.py --confirm             # Fix inverted turno
python utils/supabase_client.py                   # Run cleanup functions
```

### Platform Status (April 2026)

| Platform | Status | Notes |
|----------|--------|-------|
| Mercado Livre | ✅ | CEP popup handled |
| Amazon | ✅ | Seller via "Vendido por" |
| Magalu | ✅ | nm-* selectors + Radware rotation |
| Google Shopping | ✅ | Leaf-div title strategy |
| Leroy Merlin | ✅ | Algolia API direct |
| Dealers (13+) | ✅ | JSON-LD + VTEX + DOM fallback |
| Shopee | ⏸️ | Needs authenticated session |
| Casas Bahia | ⏸️ | Akamai WAF |
| Fast Shop | ⏸️ | PerimeterX total block |

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Playwright browsers not found | `python -m playwright install chromium` |
| Supabase upload ignored | Check `.env` has `SUPABASE_KEY` (service_role) |
| Dealer returns 0 products | Check `logs/dealer_debug_<name>_p1.html` |
| Wrong turno (Abertura/Fechamento) | Run `python scripts/fix_turno.py --confirm` |
| VM Oracle OOM | Verify swap: `free -h`, `sudo swapon --show` |
| Telegram notification fails | Test token: `curl https://api.telegram.org/bot<TOKEN>/getMe` |

### CSV Output Columns

```
Data; Turno; Horário; Analista; Plataforma; Tipo Plataforma;
Keyword Buscada; Categoria Keyword; Marca Monitorada; Produto/SKU;
Posição Orgânica; Posição Patrocinada; Posição Geral; Preço (R$);
Seller/Vendedor; Fulfillment?; Avaliação; Qtd Avaliações; Tag Destaque
```

### Token Cost Estimates (for AI assistants)

| Document | Tokens | Auto-loaded? |
|----------|--------|-------------|
| CLAUDE.md | ~400 | Yes |
| .claude/COMMON_MISTAKES.md | ~350 | Yes |
| .claude/QUICK_START.md | ~200 | Yes |
| .claude/ARCHITECTURE_MAP.md | ~300 | Yes |
| **Session start total** | **~1,250** | |
| docs/learnings/*.md | ~400-600 | No (task-specific) |
| **Typical task total** | **~1,850** | |

---

## Appendix: Do's and Don'ts

### Do's ✅

- Use type hints on all function signatures
- Log with appropriate levels (debug/info/warning/error/success)
- Handle edge cases explicitly (None, empty strings, missing elements)
- Test scraper changes with `--no-headless` before deploying
- Update documentation when adding/changing features
- Use the retry decorator (`@retry`) on network operations
- Rotate browser proactively for Radware-protected sites
- Use UTF-8 BOM for CSV output (Excel PT-BR compatibility)

### Don'ts ❌

- Never use bare `except:` clauses
- Never skip error handling for network requests
- Never assume CSS selectors are stable across deployments
- Never hardcode credentials or API keys
- Never commit `.env` files or debug HTML dumps
- Never ignore CAPTCHA/blocking detection
- Never use print() instead of logger
- Never assume timezone is BRT — always use `now_brt()`

---

*Last updated: April 2026 (v3.1)*  
*Maintained by: RAC Position Tracker Team*
