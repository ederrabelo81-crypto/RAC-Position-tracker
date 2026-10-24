# Quick Start — RAC Position Tracker

> Estado atual (Out/2026): oferta/posição roda no **PC coletor** em 3 turnos
> (08/14/20h) com todas as plataformas **exceto Amazon**, que roda no GitHub
> Actions (`collect_amazon_sellers.yml`). Visão completa no `README.md`.

## Essential Commands

```bash
# Varredura do PC (o que as tarefas agendadas rodam: 2 páginas, todas as keywords)
python main.py --platforms ml magalu shopee casasbahia google_shopping leroy dealers --pages 2

# Plataformas ON em ACTIVE_PLATFORMS (sem argumento nenhum, `python main.py` roda a demo de 1 keyword no ML)
python main.py --pages 2

# `all` = todo o registry exceto ml_api — ignora ACTIVE_PLATFORMS (inclui Fast Shop, bloqueada)
python main.py --platforms all --pages 2

# Amazon como no Actions (buy box do PDP, sem cache, turno cravado)
RAC_TURNO=Abertura RAC_AMAZON_PDP_FRESH=1 python main.py --platforms amazon --pages 2

# Plataformas específicas / debug com browser visível
python main.py --platforms ml magalu --pages 1
python main.py --platforms dealers --pages 1 --no-headless

# Keywords customizadas
python main.py --platforms ml --keywords "ar condicionado inverter 12000"

# Disparo manual idêntico ao agendado (Windows): manha | tarde | noite
scripts\run_local_scheduled.bat manha
```

No PowerShell, teste manual de ML/Magalu/Google só reproduz o turno agendado
com `$env:RAC_LOCAL_CHROME="1"` (Chrome real logado do perfil dedicado).

## Supervisão e dados

```bash
python scripts/pipeline_watch.py            # quem não bateu ponto hoje
python scripts/briefing_gate.py --json      # dado de D-1 está fresco?
python scripts/daily_status_check.py        # PASS/FAIL por plataforma
python scripts/collect_bestsellers.py       # Mais Vendidos (manual no PC)
python scripts/build_seller_offer_daily.py  # fato do seller_app (service_role)
python scripts/history_cli.py stats         # histórico frio no Drive
streamlit run app.py                        # dashboard interno
streamlit run seller_app/app.py             # painel do lojista
```

## Virtual Environment

```bash
venv\Scripts\activate          # Windows (ou .venv)
source venv/bin/activate       # Linux/Mac
pip install -r requirements.txt
python -m playwright install chromium
scripts\sync_windows.bat       # PC coletor: repo + deps + Drive num comando
```

## Output

- CSV: `output/rac_monitoramento_YYYYMMDD_HHMM.csv` (+ `<csv>.run_id`)
- Logs: `logs/bot_*.log`, `logs/heartbeat.jsonl`, `logs/scheduler.log`
- Debug HTML: `logs/dealer_debug_<name>_p<N>.html`, `logs/ml_gate_*.html`

## Testes

```bash
pip install pytest
pytest tests/ pricetrack_api/tests pricetrack_importer/tests pricetrack_dashboard/tests -q
# 24/10/2026: 2.505 passed, 62 skipped (~75 s)

python scripts/smoke_test.py   # 1 keyword × 1 página por plataforma (rede real)
```

---

## Common Workflows

### Debug a dealer with 0 products
1. Run with `--no-headless` to see the browser
2. Check `logs/dealer_debug_<name>_p1.html` for the captured HTML
3. Inspect HTML to find the correct CSS selectors
4. Update `DEALER_CONFIGS` in `scrapers/dealers.py`

### Add a new dealer
1. Add entry to `DEALER_CONFIGS` in `scrapers/dealers.py` (`on_hold: True` exclui da coleta)
2. Add `"DealerName": "Regional Especializado"` to `PLATFORM_TYPE` in `config.py`
3. Run: `python main.py --platforms dealers --pages 1`

### Add a Mais Vendidos dealer (VTEX/HTML)
1. Register in `bestsellers/config.py` — `SOURCES` (with the right `referencia`) + `ColetaSpec` in `COLETA`
2. No new file needed: the generic collectors resolve it

### Fix price extraction for a dealer
1. Open the debug HTML in a browser
2. Check if JSON-LD `<script type="application/ld+json">` has prices
3. Add site-specific selector to `DEALER_CONFIGS` or `_SELECTORS["price_candidates"]`

### Add a new keyword
1. Edit `KEYWORDS_LIST` in `config.py` (40 keywords today)
2. Set `category` and `priority` ("alta", "media", "baixa")
3. Check `utils/keyword_taxonomy.py` classifies it right (genérica / marca / dealer)

### Add a platform or job
1. One owner per (platform, turno) in `utils/pipeline_registry.py` (`JOBS`)
2. `pytest tests/test_pipeline_registry.py` must pass

## Platform Registry (main.py `SCRAPER_REGISTRY`)

```
ml, ml_api, amazon, magalu, casasbahia, google_shopping, leroy, shopee, fast, dealers
```

`ml_api` is explicit-only (not in `all`). `fast` is off in `ACTIVE_PLATFORMS`.
