# Documentation Index — RAC Position Tracker

> Comece pelo `README.md` da raiz (estado atual: o que é, como instalar e usar)
> e pelo `CLAUDE.md` (regras duras). Este índice aponta o documento certo por
> tarefa. Atualizado em 24/10/2026.

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

### "I want to speed up PDP reads / evaluate a scraping library"
1. Load `docs/AVALIACAO_SCRAPLING.md` (Scrapling: what to adapt, why not a dependency; Amazon AOD; Leroy seller)

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
1. **Notebook (recomendado, Jul/2026):** `docs/COLETA_LOCAL_AUTENTICADA.md`
   — Chrome real logado (perfil dedicado), sem CDP/porta de debug.
   Key files: `scrapers/local_browser.py`, `scripts/setup_local_profile.py`,
   `scripts/collect_local_authenticated.bat`, `scripts/setup_local_scheduler.ps1`
   Liga com `RAC_LOCAL_CHROME=1`.
2. **Legado (CDP + perfil copiado):** `docs/AUTOMACAO_COLETAS_AUTENTICADAS.md`
   e `docs/cdp_magalu_collection.md`. Falhava por Chrome 136+ ignorar
   `--remote-debugging-port` no perfil padrão e a cópia deslogar as contas —
   ver a seção "Por que as tentativas anteriores falhavam" do doc novo.

### "I need to run a manual collection via the Claude Chrome Extension"
1. Load the platform guide: `docs/manual_magalu_collection.md`,
   `docs/manual_shopee_collection.md` or `docs/manual_casasbahia_collection.md`
2. Each guide has the extraction prompt + the "Economia de tokens" section
3. Prefer the automated path above when the CDP Chrome is available

### "I need to measure sales / share gain or loss (Mais Vendidos)"
1. Load `docs/BESTSELLERS.md` (KPI, regras duras, portões de validação)
2. Entry point: `python scripts/collect_bestsellers.py`
3. Key files: `bestsellers/config.py` (registro das listas),
   `bestsellers/metrics.py` (KPI e séries diária/semanal/mensal),
   `bestsellers/sources/` (um coletor por varejista)
4. Leitura no painel: dashboard page 🥇 Mais Vendidos (`app.py`)
5. Regra dura: ranking é ORDINAL — nunca vira share de mercado

### "A collection did not run / the briefing got stale data / where should X run?"
1. Load `docs/MAPA_COLETAS.md` (what runs on the PC, on Actions and on the VM,
   and why — plus the alerting and containment design)
2. Contract in code: `utils/pipeline_registry.py` (`JOBS`) — the single source
   of truth for schedule, owner platform, deadline and remediation
3. Commands:
   - `python scripts/pipeline_watch.py` — who promised to run and did not
   - `python scripts/briefing_gate.py` — is D-1 data actually fresh?
   - `python scripts/pipeline_heal.py --dry-run` — what containment would do
4. Ledger: `pipeline_heartbeat` (migration 015). Missing beat past the job
   deadline = NOT EXECUTED — silence is what fires the alarm
5. Hard rule: one owner per (platform, turno); an orphan platform is billed to
   nobody (that is how dealers went unnoticed)

### "I need to change the dashboard home (Cockpit do Trade) or the global filters"
1. Load `docs/COCKPIT_TRADE.md` (metric definitions + the three rules: dedup,
   brand-neutral share, silence is not market change)
2. Pure metrics: `utils/shelf_insights.py` (tests: `tests/test_shelf_insights.py`)
3. Keyword taxonomy: `utils/keyword_taxonomy.py` (genérica / marca própria /
   marca concorrente / vitrine de dealer)
4. Filter wiring: `docs/DASHBOARD_FILTERS.md` (global filters now apply to
   every page via `_page_period()` / `_inherit_global()`)

### "I need to work with PriceTrack data (price source of truth)"
1. Load `docs/PRICETRACK_INSIGHTS.md` (pipeline + insight/improvement roadmap)
2. Key files: `pricetrack_api/` (typed API client — see `pricetrack_api/README.md`),
   `scripts/pricetrack_api_import.py`, `pricetrack_importer/`

### "I need to diagnose broken field coverage (buy box, rating, sponsored)"
1. Load `docs/DIAGNOSTICO_COLETA_JUN2026.md` (root causes per platform)
2. Dashboard page 🩺 Data Health (field × platform matrix)
3. ML live check: `python scripts/diagnose_ml.py`

### "I need to read collection data with Supabase offline (quota / downtime)"
1. Load `docs/HISTORICO_DRIVE.md` (cold history in Parquet on Google Drive)
2. For a Cowork/agent session that must produce the report sections from the
   Drive instead of the database: `docs/PROMPT_COWORK_DRIVE.md` (ready-to-paste
   prompt + Supabase→Drive filter equivalences)
3. CLI: `python scripts/history_cli.py stats` / `export --cold-only`

### "I need to work on the seller panel (Track Position Seller)"
1. Operation/deploy: `seller_app/README.md`; strategy & architecture: `docs/TRACK_POSITION_SELLER.md`
2. Data: `seller_offer_daily` / `seller_coverage_daily` / `v_seller_buybox_share`
   (`docs/migrations/016_seller_offer_daily.sql`, `017_seller_offer_daily_correcoes.sql`,
   `018_seller_offer_daily_amazon_buybox.sql`, `020_share_view_indices.sql`), built by `scripts/build_seller_offer_daily.py`
   (job `local_seller_fact`, stage C of every PC turn)
3. Hard rule: secret `SELLER` is compared literally against `seller_canonical`

### "The Supabase hit the quota / I need to switch database backend"
1. Current backend and the emergency runbook: `docs/RETORNO_SUPABASE.md` (§5.1)
2. Rollback to a direct Postgres (Aiven): `docs/MIGRACAO_AIVEN.md`
   (`utils/db.py`, `RAC_DB_DSN`)
3. Background and numbers: `docs/ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md`;
   old retention policy (superseded): `docs/DB_RETENTION.md`

### "I need to run or fix the daily briefing (07:00)"
1. `docs/BRIEFING_LOCAL_SETUP.md` (local Claude Code headless, Supabase Session Pooler)
2. Versioned prompt: `docs/briefing_diario_prompt.md`; static panel: `docs/painel/`
3. Hard rule (CLAUDE.md): never write schema SQL from memory — use
   `briefing_gate.py`, `pricetrack_capacity_audit.py`, `pipeline_watch.py`

### "I need to check PriceTrack price fidelity"
1. `docs/PRICETRACK_FIDELIDADE.md` (`price_basis`, best_cash, pending reimport)
2. `python scripts/pricetrack_audit.py` — our collection as an audit of PriceTrack

### "I need to orchestrate or automate collection with n8n"
1. Load `docs/n8n_orchestration.md`
2. Key file: `n8n/rac_coleta_monitor.json` (workflow: notifications + CSV ingestion)

---

## Token Cost Estimates

| Document | Tokens | Auto-loaded? |
|----------|--------|-------------|
| CLAUDE.md | ~25,000 | Yes |
| .claude/COMMON_MISTAKES.md | ~8,000 | Yes |
| .claude/QUICK_START.md | ~1,100 | Yes |
| .claude/ARCHITECTURE_MAP.md | ~2,200 | Yes |
| **Session start total** | **~35,000** | |
| docs/learnings/*.md | ~1,000–3,000 each | No |

*(Estimativa: bytes / 4, medida em 24/10/2026.)*

---

## All Documentation Files

```
README.md                                  ← Current state: what it is, install, usage ⭐
CLAUDE.md                                  ← Hard rules + session protocol
.claude/COMMON_MISTAKES.md                 ← Critical anti-patterns (26 items)
.claude/QUICK_START.md                     ← Commands and workflows
.claude/ARCHITECTURE_MAP.md                ← File locations and data flow
.claude/LEARNINGS_INDEX.md                 ← Quick pointer to learnings
.claude/DOCUMENTATION_MAINTENANCE.md       ← When to update docs
.claude/completions/                       ← Task completion records
.claude/sessions/                          ← Session context files
.claude/templates/                         ← Templates for completions/sessions
docs/INDEX.md                              ← This file
docs/QUICK_REFERENCE.md                    ← Fast lookups
docs/COLETA_LOCAL_AUTENTICADA.md           ← Shopee/Magalu/CB no notebook (Chrome real logado) ⭐
docs/AUTOMACAO_COLETAS_AUTENTICADAS.md     ← Legado: automation via CDP + sessions
docs/MAPA_COLETAS.md                       ← Where each collection runs + execution monitoring ⭐
docs/PRICETRACK_INSIGHTS.md                ← PriceTrack pipeline + insight roadmap
docs/TRACK_POSITION_SELLER.md              ← Spin-off seller-side: strategy + architecture (Phase 1 live in seller_app/)
seller_app/README.md                       ← Run/deploy the seller panel
pricetrack_api/README.md                   ← PriceTrack API client
pricetrack_dashboard/README.md             ← 9K/12K price dashboard
docs/BESTSELLERS.md                        ← Mais Vendidos: KPI, rules, validation gates
docs/COCKPIT_TRADE.md                      ← Cockpit do Trade metric definitions
docs/DASHBOARD_FILTERS.md                  ← Global filters wiring
docs/RETORNO_SUPABASE.md                   ← Current DB backend (Supabase, 2-day hot window) + quota emergency ⭐
docs/MIGRACAO_AIVEN.md                     ← Rollback path: direct Postgres (Aiven)
docs/ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md ← Storage numbers and agent options
docs/DB_RETENTION.md                       ← Old retention policy (superseded)
docs/BRIEFING_LOCAL_SETUP.md               ← Daily briefing, local headless setup
docs/briefing_diario_prompt.md             ← Versioned briefing prompt
docs/PRICETRACK_FIDELIDADE.md              ← PriceTrack price-basis fidelity
docs/ADAPTIVE_SCRAPER.md                   ← Adaptive scraper (selector memory)
docs/AVALIACAO_SCRAPLING.md                ← Scrapling evaluation
docs/SECURITY_TODO_RLS.md                  ← RLS security backlog
docs/archive/README_HISTORICO_ATE_SET2026.md ← Old README with validation logs (history)
docs/HISTORICO_DRIVE.md                    ← Cold history in Parquet (Google Drive)
docs/PROMPT_COWORK_DRIVE.md                ← Cowork prompt: read the Drive instead of Supabase
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
