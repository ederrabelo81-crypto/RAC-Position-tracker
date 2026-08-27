# RAC Position Tracker — Development Guidelines & Standards

> **Project:** RAC Position Tracker — Retail Analytics & Competitive Intelligence  
> **Domain:** Buy box, sellers & competitive insights for the air conditioning market in Brazil  
> **Stack:** Python 3.10+, Playwright, curl_cffi, BeautifulSoup, Pandas, Supabase, Streamlit  
> **Sub-projeto:** `magalu_shopee/` — Node.js/TypeScript + Puppeteer (Shopee — fallback)  
> **Status:** ✅ Production | Oracle Cloud VM (Brazil East) + GitHub Actions (manual backup)

---

## 🎯 Foco da coleta (Mai/2026): Buy Box & Sellers, não preço

O protagonista da coleta deixou de ser **preço** e passou a ser **competição
por buy box e inteligência de sellers**. Preço continua coletado, porém como
campo secundário. Campos de insight em todo registro: `Buy Box Seller`,
`Qtd Sellers`, `Tipo Seller`, `Reputação Seller`, `Patrocinado?`.

**7 plataformas (dealers saíram do foco):** Mercado Livre, Amazon, Google
Shopping, Magalu, Casas Bahia, Shopee, Leroy Merlin. APIs JSON são preferidas
ao DOM — expõem o array de sellers e o vencedor da buy box diretamente.

```bash
# Coleta padrão (todas as 7 plataformas ativas em ACTIVE_PLATFORMS)
python main.py --platforms all --pages 2

# Plataformas individuais
python main.py --platforms casasbahia --pages 1   # VTEX IS + warm-up Akamai
python main.py --platforms shopee --pages 1        # API v4 (requer sessão)
python main.py --platforms magalu --pages 2        # curl_cffi/browser

# Shopee — capturar sessão antes (cookies SPC_*/csrftoken expiram em horas)
python utils/session_grabber.py --site shopee
```

### Mais Vendidos — a única variável de RESULTADO (Ago/2026) 🆕

A coleta de OFERTA mede preço, posição e buy box; ela **não contém volume de
venda**. As listas "Mais Vendidos" dos varejistas são a **única variável de
resultado** disponível no nível do SKU, alimentando análises diárias, semanais
e mensais de **ganho/perda de share de topo de ranking**.

**Módulo:** `bestsellers/` — 6 sources artesanais (Amazon, ML, Magalu, Casas
Bahia, Shopee, Leroy Merlin) **+ 14 dealers genéricos** (Ago/2026) via dois
coletores dirigidos por `config.COLETA`: VTEX (`sources/vtex_generic.py`, API
de catálogo) e HTML/JSON-LD (`sources/html_generic.py`, para lojas não-VTEX).

```bash
python scripts/collect_bestsellers.py                    # coleta do dia + brief diário
python scripts/collect_bestsellers.py --plataformas dufrio webcontinental  # dealers avulsos
python scripts/collect_bestsellers.py --relatorio semanal  # evolução (não coleta)
python scripts/collect_bestsellers.py --relatorio mensal --ultimos 6
python scripts/collect_bestsellers.py --import arquivo.xlsx # backfill
```

Tabela `bestsellers` — migrações `011_bestsellers_diario.sql` +
`013_bestsellers_referencia.sql` (coluna `referencia`).
Cadência obrigatória: todo dia útil **09:30 BRT** (Amazon recalcula ranking de hora em hora).

**Buy box da Amazon — PDP a cada execução (Ago/2026) 🆕** A SERP de mais
vendidos não imprime "Vendido por": o PDP é o único lugar onde a buy box
existe. `bestsellers/sources/amazon.py` abre o PDP de **cada item do ranking,
toda execução** — `seller` é a **observação do dia**, não atributo fixo do
produto, e é assim que se vê a buy box mudar de dono. **Regra dura:** esta
fonte **não** usa o cache `data/amazon_sellers.json` (o `AmazonSellerCache` não
tem validade e devolveria para sempre o vendedor da 1ª resolução — do 2º dia em
diante a série gravaria um vencedor que ninguém observou). O único cache é por
execução: ASIN repetido entre páginas custa um PDP só. Ligado por padrão;
`RAC_BESTSELLERS_AMAZON_PDP=0` desliga e `RAC_BESTSELLERS_AMAZON_PDP_BUDGET`
impõe teto. **Custo:** ~30–60 PDPs por run (2–5s de intervalo cada), somando
alguns minutos à rotina das 09:30 — dimensione a janela do Task Scheduler.

**Duas referências (Ago/2026):** cada lista carrega `referencia` =
`mais_vendidos` (loja expõe ordenação por vendas) **ou** `relevancia` (a loja
só tem a ordem de destaque do algoritmo — proxy, NÃO é venda). **Regra dura da
separação:** as duas referências **nunca se misturam** num mesmo número —
séries próprias, segmentos próprios no painel. Dealers de relevância: Dufrio,
Central Ar, Leveros, Ferreira Costa, Engage. Os demais dealers novos (Web
Continental, Frio Peças, Clima Rio, Ar Certo, Polo Ar, Bel Micro, Fast Shop,
Bemol, Frigelar) são `mais_vendidos`. **Todos são novos e sem as 3 leituras de
validação** (regra de `sources/__init__.py`) — tratar como provisório; de IP
de datacenter muitos vêm bloqueados, a coleta oficial roda do PC coletor.

**Regra dura:** ranking é ORDINAL — nunca soma entre plataformas, nunca vira
share de mercado (isso vem de GfK/Neotrust). KPI: % Midea no top 10 por
plataforma + delta vs período anterior.

**Adicionar dealer VTEX/HTML:** registrar a fonte em `bestsellers/config.py`
(`SOURCES` com a `referencia` certa) e a `ColetaSpec` em `COLETA` — o coletor
genérico e o registro em `SOURCE_CLASSES` se resolvem sozinhos, sem novo
arquivo.

**Agendamento Windows (Task Scheduler):** `RAC_Bestsellers` 09:30 seg-sex +
catch-up no logon. Roda no PC porque Amazon/ML/Shopee precisam de browser real
e sessão logada — em IP de datacenter do Oracle/GitHub, 2–3 das 6 listas somem.
Uma execução por dia: recoletar **substitui** a entrada do dia (idempotência),
então **não ligue cron da VM**. Setup: `scripts\setup_local_scheduler.ps1`
(registra as 3 tarefas em paralelo: Bestsellers + Local Autenticada + ML).

**Valição:** Filtros anti-spam (título vazio, caracteres proibidos, duplicado
entre SKUs, marca fora do escopo). Parser por plataforma em `bestsellers/sources/`.
Relatório Telegram + resumo JSON em `logs/bestsellers_*.json`.

**Leitura no dashboard:** página **🥇 Mais Vendidos** (`streamlit run app.py`,
grupo INSIGHTS) — KPI do dia com delta contra o mesmo dia da semana, evolução
semanal/mensal, ranking por plataforma, mapa competitivo, portões de validação
e o brief. Mostra a UNIÃO de Supabase + `master_bestsellers.csv` (dedup por
data+plataforma+rank, banco vence; CSV bruto do dia como último recurso) e diz
quantos dias vieram de cada fonte. **RLS:** leitura pela `anon`
liberada em 18/08/2026 (migração 012 aplicada); a **escrita** segue exigindo
`service_role` — coleta com chave `anon` grava CSV e master e deixa o banco
para trás em silêncio (`scripts\check_local_scheduler.ps1` confere a chave).

### Magalu — automatizado (não mais via extensão Chrome) 🆕 Local Browser + Playwright Runtime

`scrapers/magalu.py` (curl_cffi + browser persistente, Akamai bypass) é o
caminho oficial. Roda sem intervenção via `python main.py --platforms magalu`.

**Novo (Ago/2026):** `scrapers/local_browser.py` gerencia o browser local (CDP via
`rebrowser-playwright`) com **auto-recuperação** e fallback inteligente:

1. **LocalBrowser singleton:** uma única instância do Chrome+CDP reutilizável
2. **Auto-recuperação:** Se o CDP morre, reconecta automaticamente
3. **Handle compartilhado:** um único handle Playwright para toda a sessão
4. **Fallback automático:** CDP bloqueado → curl_cffi; curl_cffi bloqueado → HTTP 403 silencioso
5. **PlaywrightRuntime:** sincronização de acesso ao sync_playwright em ambiente multi-thread

Como funciona o `scrapers/magalu.py`:
1. Tenta via **LocalBrowser (Chrome+CDP)** para máxima compatibilidade
2. Se bloqueado: `curl_cffi` com `impersonate="chrome124"` (TLS handshake real)
3. Warm-up na home → Akamai emite cookies frescos
4. Extrai BUILD_ID do Next.js (`__NEXT_DATA__`)
5. Bate em `_next/data/{BUILD_ID}/busca/{slug}.json` — JSON puro
6. Fallback HTML: scraping + extração de `__NEXT_DATA__` embutido

Detecção de bloqueio fail-fast: HTTP 403, response <1KB, ou strings Akamai.
Circuit breaker: aborta após 5 keywords 100% bloqueadas (evita spin).

**Setup Windows:**
```powershell
python scripts\setup_local_profile.py  # Lança o Chrome do perfil dedicado
PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1
```

**Troubleshooting:**
- Chrome fechou? Diagnóstico: `scripts\check_local_scheduler.ps1`
- Mode local falha de forma consistente? Prefira IP residencial + curl_cffi

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
├── app.py                       # Streamlit dashboard (20 pages + CI with Claude)
├── diagnostico.py               # Debug utilities
├── requirements.txt             # Python dependencies
│
├── magalu_shopee/               # Sub-projeto Node.js/TS — Magalu & Shopee (Puppeteer)
│   └── src/index.ts             # Entry point: ts-node src/index.ts --platforms magalu
│
├── bestsellers/                 # 🆕 Módulo de Mais Vendidos (rankings por plataforma)
│   ├── __init__.py              # Orquestração CLI
│   ├── base.py                  # BaseBestsellerSource ABC
│   ├── config.py                # SOURCES (+ referencia) + COLETA (dealers VTEX/HTML)
│   ├── models.py                # Dataclasses (Bestseller, Metric, Report)
│   ├── metrics.py               # KPIs (rank delta, share top 10, etc.)
│   ├── report.py                # Renderização HTML + Telegram
│   ├── storage.py               # Supabase + JSON persistence
│   ├── validate.py              # Portões (ordenação referência-aware, anti-spam)
│   ├── importer_xlsx.py         # Backfill via XLSX
│   └── sources/                 # Scrapers por plataforma
│       ├── amazon.py, casas_bahia.py, leroy_merlin.py
│       ├── magalu.py, mercado_livre.py, shopee.py
│       ├── vtex_generic.py      # 🆕 coletor VTEX genérico (dealers, dirigido por COLETA)
│       └── html_generic.py      # 🆕 coletor HTML/JSON-LD genérico (dealers não-VTEX)
│
├── scrapers/
│   ├── __init__.py
│   ├── base.py                  # BaseScraper ABC (Playwright lifecycle, stealth)
│   ├── local_browser.py         # 🆕 LocalBrowser singleton + auto-recovery + CDP
│   ├── playwright_runtime.py    # 🆕 PlaywrightRuntime (sync, singleton)
│   ├── mercado_livre.py         # MLScraper (browser + fallback)
│   ├── amazon.py                # AmazonScraper (+ PDP sellers cache)
│   ├── google_shopping.py       # GoogleShoppingScraper
│   ├── leroy_merlin.py          # LeroyMerlinScraper (Algolia API)
│   ├── magalu.py                # MagaluScraper (LocalBrowser + curl_cffi fallback)
│   ├── casas_bahia.py           # CasasBahiaScraper (VTEX IS)
│   ├── shopee.py                # ShopeeScraper (API v4 + sessão)
│   ├── dealers.py               # DealerScraper (⏸️ fora do foco)
│   └── fast_shop.py             # ⏸️ PerimeterX
│
├── utils/
│   ├── text.py                  # parse_price, parse_rating, now_brt(), normalize
│   ├── brands.py                # extract_brand() regex matching
│   ├── session_grabber.py       # Auth session capture
│   ├── supabase_client.py       # Upload, cleanup, maintenance
│   ├── amazon_sellers.py        # 🆕 Cache de sellers Amazon (PDP)
│   ├── admin_automation.py      # Motor da automação ADMIN (zero interação)
│   └── n8n_notify.py            # Telegram notifications (API direta)
│
├── scripts/
│   ├── oracle_setup.sh          # VM setup script
│   ├── collect_manha_linux.sh   # Morning collection (10:00 BRT)
│   ├── collect_noite_linux.sh   # Night collection (21:00 BRT)
│   ├── admin_auto.py            # CLI da automação ADMIN (cron/debug)
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
**PR review state:** Sempre abrir/deixar PRs como **ready for review** (não draft).
Preferência do mantenedor (Jun/2026) — vale para todas as sessões.  
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

### CI — a suíte roda em todo PR (Ago/2026) 🆕

`.github/workflows/tests.yml` roda `pytest tests/ pricetrack_api/tests
pricetrack_importer/tests -q` em **todo Pull Request** e em todo push para
`main` (~1 min, Python 3.11, sem secret — a suíte é hermética e os testes de
integração se auto-pulam sem `SUPABASE_URL`). Antes disso nenhum workflow
rodava em PR: `collect`, `pricetrack_daily` e `watchdog` são cron ou dispatch
manual. Foi essa lacuna que deixou entrar em `main` um `_parse` duplicado na
Amazon e um índice Algolia sombreado na Leroy (PRs #322/#323).

Os browsers do Playwright ficam **fora** do job de propósito — nenhum teste
abre browser, e `playwright install` somaria minutos a uma suíte de 1 min.

### Cobertura Atual (Ago/2026)

**1.555 testes** incluindo:
- 60+ novos testes de bestsellers (parsers, metrics, pipeline, validate)
- 40+ testes de browser local (LocalBrowser, PlaywrightRuntime, fallback chain)
- 20+ testes de Amazon sellers (cache, PDP)
- Testes de watchdog channels (Telegram alert routing)
- Testes de orçamento PriceTrack

Rodando: `pytest tests/ pricetrack_api/tests pricetrack_importer/tests -q`

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

# Anthropic (optional — Automação Admin LLM layer)
ANTHROPIC_API_KEY=sk-ant-...

# Analyst name for reports
ANALYST_NAME="Bot Automático Python"

# Telegram Notifications
TELEGRAM_BOT_TOKEN=7730291785:AAF...
N8N_TELEGRAM_CHAT_ID=123456789

# Optional N8N webhook
N8N_WEBHOOK_URL=http://localhost:5678/webhook/coleta

# Mercado Livre — API oficial (fallback automático quando a SERP leva gate)
ML_APP_ID=...
ML_APP_SECRET=...

# Mercado Livre — login do perfil Chrome dedicado com `setup_local_profile.py
# --site mercadolivre --auto` (opcional; o login manual não precisa disto)
ML_EMAIL=...
ML_PASSWORD=...
```

### GitHub Actions (Manual Backup)

Workflow: `.github/workflows/collect.yml`

**Trigger:** Manual dispatch only (no cron)  
**Purpose:** Backup when Oracle VM unavailable, testing

```yaml
# Usage: GitHub → Actions → RAC Price Collection → Run workflow
inputs:
  platforms: 'ml amazon google_shopping leroy dealers'
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

# Bestsellers (Mais Vendidos) — rankings diário/semanal/mensal
python scripts/collect_bestsellers.py              # Coleta do dia + relatório Telegram
python scripts/collect_bestsellers.py --relatorio semanal  # Evolução da semana
python scripts/collect_bestsellers.py --relatorio mensal --ultimos 6  # Últimos 6 meses
python scripts/collect_bestsellers.py --import arquivo.xlsx  # Backfill histórico

# Dashboard
streamlit run app.py

# Validação diária — relatório PASS/FAIL por plataforma no Telegram
python scripts/daily_status_check.py              # Hoje, ambos turnos
python scripts/daily_status_check.py --turno Abertura
python scripts/daily_status_check.py --data 2026-05-14 --no-notify

# Automação ADMIN — limpeza, normalização e de-para SEM interação humana.
# Roda sozinha pós-coleta (main.py) e por auto-run no dashboard (🤖 Automação);
# CLI para cron/debug:
python scripts/admin_auto.py                      # incremental (watermark)
python scripts/admin_auto.py --dry-run            # simula, não grava nada
python scripts/admin_auto.py --full               # varre o histórico inteiro

# Database maintenance
python scripts/fix_turno.py --confirm             # Fix inverted turno
python utils/supabase_client.py                   # Run cleanup functions

# PC coletor Windows — catch-up de repo + dependências + Drive (um comando)
scripts\sync_windows.bat
scripts\ensure_deps.bat --force                   # só as dependências (venv)
python scripts/gdrive_setup.py --check            # histórico/CSV vão ao Drive?
python scripts/history_cli.py import-csv output/rac_monitoramento_*.csv --mirror
```

### Platform Status (foco buy box/seller — Mai 2026)

> Dealers saíram do foco (marketplaces apenas). Preço é secundário.

| Platform | Status | Notes |
|----------|--------|-------|
| Mercado Livre | ✅ | Buy box + Loja Oficial; browser (default) ou `MLAPIScraper` (API oficial, requer `ML_APP_ID`/`ML_APP_SECRET` — usado **automaticamente** como fallback quando a keyword leva login gate). Gate: detecção é **evidência primeiro** (card na página vence a string); persistiu → HTML em `logs/ml_gate_*.html` e roteiro no log. Antídoto: `python scripts/setup_local_profile.py --site mercadolivre` |
| Amazon | ✅ | `Qtd Sellers` de "X ofertas"; 1P vs 3P. **Buy box só existe no PDP** (a SERP não traz "Vendido por") — campo vazio por padrão em vez de vitória 1P fantasma; resolução opcional com `RAC_AMAZON_PDP_BUYBOX=1` + cache em `data/amazon_sellers.json`. ⚠️ Não confundir com a coleta de **Mais Vendidos**, que lê o PDP de todo item **a cada run** e **sem** esse cache (`RAC_BESTSELLERS_AMAZON_PDP`) |
| Leroy Merlin | ✅ | Algolia API; 1P vs 3P marketplace. Seller 3P vem como **ObjectId opaco** — resolvido via PDP ("Vendido e entregue por") com cache persistente em `data/leroy_sellers.json` (1 PDP por seller novo, não por produto). Diagnóstico: `python scripts/leroy_seller_probe.py --scan "<keyword>"` |
| Google Shopping | ⚠️ | reCAPTCHA em headless; `Qtd Sellers` = nº de lojas comparando |
| Magalu | ✅ Python | `scrapers/magalu.py` — browser persistente (Akamai); seller 1P vs 3P. **Automatizado**. Extração em 3 parsers sobre o mesmo HTML: `__NEXT_DATA__` → RSC (`__next_f`, App Router) → cards do DOM. Muro de login (Ago/2026) é detectado e nomeado no log; antídoto: `python scripts/setup_local_profile.py --site magalu`. Diagnóstico pelo prefixo do dump em `logs/` (`login_`/`layout_`/`vazia_`) — ver `docs/cdp_magalu_collection.md` |
| Casas Bahia | ✅ | `scrapers/casas_bahia.py` — VTEX intelligent-search + **warm-up de cookies Akamai** (session curl_cffi persistente); `sellers[]` → buy box (`sellerDefault`) |
| Shopee | 🟡 Python | `scrapers/shopee.py` — API v4 + sessão capturada (curl_cffi). **Best-effort** sem proxy BR; flags Mall/Preferred+. Node em `magalu_shopee/` fica como fallback |
| Fast Shop | ⏸️ | PerimeterX total block |
| Dealers | ⏸️ | Fora do foco — `ACTIVE_PLATFORMS["dealers"]=False` |

**Bloqueios Shopee/Casas Bahia:** causa raiz é o IP de datacenter (Oracle/GH
Actions marcado pelo Akamai/anti-bot antes do fingerprint). Maior ganho =
proxy residencial/móvel BR. Sem proxy: Casas Bahia destrava via warm-up; Shopee
fica instável (re-capturar sessão com `session_grabber.py --site shopee`).

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Playwright browsers not found | `python -m playwright install chromium` |
| Supabase upload ignored | Check `.env` has `SUPABASE_KEY` (service_role) |
| Dealer returns 0 products | Check `logs/dealer_debug_<name>_p1.html` |
| Wrong turno (Abertura/Fechamento) | Run `python scripts/fix_turno.py --confirm` |
| VM Oracle OOM | Verify swap: `free -h`, `sudo swapon --show` |
| Log diz `[Histórico] … → local:C:\...` | `.env` daquele host sem `GDRIVE_*` — `python scripts/gdrive_setup.py --client-secrets CAMINHO.json` (caminho real do JSON do OAuth) |
| Notebook sem lib nova do `requirements.txt` | `scripts\ensure_deps.bat --force` (a coleta agendada já roda isso a cada run) |
| Telegram notification fails | Test token: `curl https://api.telegram.org/bot<TOKEN>/getMe` |
| `Sync API inside the asyncio loop` | Alguém abriu um 2º `sync_playwright()` na thread. Use `scrapers/playwright_runtime.acquire()/release()` — nunca `sync_playwright().start()` direto (ver COMMON_MISTAKES #21) |
| `Target page, context or browser has been closed` em série | A janela do Chrome do `RAC_LOCAL_CHROME` foi fechada. LocalBrowser reconecta sozinha (Ago/2026). Se persiste: reabra o perfil: `python scripts/setup_local_profile.py --site magalu` |
| Chrome CDP morto / reconexão infinita | LocalBrowser detecta e aborta (Ago/2026); fallback automático para curl_cffi. Se curl_cffi também bloqueado, retorna 0 produtos (circuit breaker após 5 keywords bloqueadas) |
| Shopee 403 em todas as keywords | Sessão vencida (>24h, agora avisada como WARNING no log): `python utils/session_grabber.py --site shopee` — ou rode com `RAC_LOCAL_CHROME=1` |

### CSV Output Columns

> **Foco (Mai/2026):** buy box, sellers e insights. Preço agora é **secundário**.
> Colunas novas: `Patrocinado?`, `Buy Box Seller`, `Qtd Sellers`, `Tipo Seller`,
> `Reputação Seller`. DB: `docs/migrations/003_add_buybox_seller_columns.sql`.
>
> **Identidade da oferta (Ago/2026):** `ID Produto Marketplace`,
> `ID Oferta Marketplace`, `ID Seller`, `URL Canônica`, `Offer Key` — anexadas
> no FIM do CSV. DB: `docs/migrations/014_offer_identity.sql`.

```
Data; Turno; Horário; Analista; Plataforma; Tipo Plataforma;
Keyword Buscada; Categoria Keyword; Marca Monitorada; Produto / SKU;
Posição Orgânica; Posição Patrocinada; Posição Geral; Patrocinado?;
Buy Box Seller; Qtd Sellers; Tipo Seller; Reputação Seller;
Seller / Vendedor; Fulfillment?; Avaliação; Qtd Avaliações; Tag Destaque;
Preço (R$); URL Produto; Screenshot Busca; Screenshot Produto;
ID Produto Marketplace; ID Oferta Marketplace; ID Seller; URL Canônica; Offer Key
```

### Identidade da oferta — `utils/offer_identity.py` (Ago/2026) 🆕

Antes da Fase 1 da auditoria, ASIN, MLB, `itemid` da Shopee, `productId` do
Magalu e `idLojista` da Casas Bahia eram extraídos, usados para montar a URL e
**descartados** — sem id de oferta não existe série histórica.

**Três conceitos, deliberadamente separados:**
- `marketplace_product_id` — id de PRODUTO (um produto tem N ofertas);
- `marketplace_offer_id` — id da OFERTA, **só quando o marketplace expõe um**
  (ML e Shopee). **Nunca sintetizado** — id inventado parece autoridade que o
  dado não tem;
- `offer_key` — chave DERIVADA e VERSIONADA (`v1|`), sempre preenchida. Escada
  de precedência: offer id → product+seller → product → hash da URL canônica →
  hash do título. O seller é anexado também aos degraus derivados, senão dois
  lojistas na mesma página colapsam numa série só.

**Regra dura:** ids passados pelo coletor têm precedência sobre os derivados da
URL — quem leu `data-asin` tem dado de primeira mão. Ao mexer nos regex de
identidade, valide contra URLs REAIS da base (`tests/test_offer_identity.py`
usa amostra de produção); regex escrito contra formato imaginado quebra na
primeira coleta.

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

*Last updated: August 21, 2026 (v4.9)*  
*Latest changes: Bestsellers — expansão para 14 dealers (coletores VTEX/HTML genéricos) e separação de referência Mais Vendidos × Relevância (coluna `referencia`, migração 013, segmento próprio no dashboard)*  
*Maintained by: RAC Position Tracker Team*
