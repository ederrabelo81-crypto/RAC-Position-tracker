# Architecture Map — RAC Position Tracker

> Atualizado em 24/10/2026. Onde rodar o quê: `docs/MAPA_COLETAS.md` e o
> contrato em `utils/pipeline_registry.py`.

## Directory Structure

Árvore com os pontos de entrada principais — não é exaustiva (`ls scripts/ utils/` para o resto).

```
RAC-Position-tracker/
├── config.py                  # Central config: keywords (40), platforms, brands (43), delays, turnos
├── main.py                    # CLI entry point, scraper registry, CSV, Drive, Supabase, heartbeat
├── app.py                     # Dashboard interno (🏠 Cockpit do Trade + 20 páginas)
├── requirements.txt           # Python dependencies (requirements_app.txt = subset do dashboard)
│
├── scrapers/
│   ├── base.py                # BaseScraper ABC (Playwright lifecycle, stealth, _build_record)
│   ├── playwright_runtime.py  # Handle ÚNICO do Playwright por thread (acquire/release)
│   ├── local_browser.py       # Chrome real compartilhado via CDP (RAC_LOCAL_CHROME) + auto-reconexão
│   ├── mercado_livre.py       # MLScraper — gate detection, freio, repescagem, fallback estrutural
│   ├── mercado_livre_api.py   # MLAPIScraper — API oficial (fallback do gate; `ml_api` opt-in)
│   ├── amazon.py              # AmazonScraper — SERP + PDP buy box (roda no GitHub Actions)
│   ├── magalu.py              # MagaluScraper — LocalBrowser → curl_cffi; __NEXT_DATA__ → RSC → DOM
│   ├── google_shopping.py     # GoogleShoppingScraper — PLAs, reCAPTCHA manual no Chrome real
│   ├── leroy_merlin.py        # LeroyMerlinScraper — Algolia + PDP (seller 3P e buy box)
│   ├── shopee.py              # ShopeeScraper — API v4 + sessão (best-effort)
│   ├── casas_bahia.py         # CasasBahiaScraper — VTEX IS + warm-up Akamai (sellers[])
│   ├── dealers.py             # DealerScraper — DEALER_CONFIGS (33 ativos, 3 on_hold)
│   └── fast_shop.py           # FastShopScraper — ⏸️ stand-by (PerimeterX)
│
├── bestsellers/               # Mais Vendidos: sources artesanais + vtex_generic/html_generic
├── seller_app/                # Track Position Seller (app separado, chave anon + RLS)
├── pricetrack_api/            # Cliente tipado da API PriceTrack (exports, journal, normalize)
├── pricetrack_importer/       # Importador md/xlsx do PriceTrack
├── pricetrack_dashboard/      # Dashboard de preço 9K/12K por tier (peer.py)
│
├── utils/
│   ├── text.py                # parse_price, parse_rating, now_brt(), get_turno() (RAC_TURNO)
│   ├── brands.py              # extract_brand() — regex contra BRANDS
│   ├── session_grabber.py     # Captura manual de sessão (Shopee/Casas Bahia)
│   ├── discover_shopee_api.py # Descoberta da API da Shopee
│   ├── seller_names.py        # SELLER_GROUPS — nome canônico de seller
│   ├── seller_surface.py      # marketplace × loja própria (seller_app)
│   ├── offer_identity.py      # offer_key versionada + ids de marketplace
│   ├── offer_kind.py          # UNIDADE / ACESSORIO / PARCIAL / MULTIPLO / COM_INSTALACAO
│   ├── keyword_taxonomy.py    # Genérica / marca própria / concorrente / vitrine de dealer
│   ├── shelf_insights.py      # Métricas puras do Cockpit (dedup, share, buy box, alertas)
│   ├── price_series.py        # Série de preço = uma fonte; recorte BTU/hi-wall
│   ├── run_sidecar.py         # run_id viaja com o CSV (sem gravação dupla)
│   ├── pipeline_registry.py   # JOBS — contrato de execução (um dono por plataforma/turno)
│   ├── heartbeat.py           # Batida de ponto em pipeline_heartbeat (+ logs/heartbeat.jsonl)
│   ├── volume_drop.py         # Plataforma DEGRADADA (< 20% da mediana em ≥ 2 turnos)
│   ├── db.py                  # Adaptador supabase-py ↔ psycopg2 (RAC_DB_DSN)
│   ├── supabase_client.py     # Upload / leitura (fail-fast em 402 de cota)
│   ├── supabase_maintenance.py # Limpeza/normalização (aceita since_id incremental)
│   ├── admin_automation.py    # Motor automação ADMIN: etapas, watermark, LLM
│   ├── history/               # Histórico frio em Parquet (Drive/disco) + costura com o banco
│   └── n8n_notify.py          # Telegram (API direta)
│
├── scripts/                   # Agendamento Windows, supervisão, PriceTrack, histórico, diagnóstico
│   └── admin_auto.py          # CLI da automação ADMIN (roda sozinha pós-coleta; --full sob demanda — sem agendamento)
├── migrations/                # SQL PriceTrack (001–006)
├── docs/migrations/           # SQL coletas/bestsellers/heartbeat/seller (001–020)
├── .github/workflows/         # tests, collect_amazon_sellers, pricetrack_daily, pipeline_guard, watchdog, collect
├── output/                    # Generated CSVs (rac_monitoramento_*.csv + .run_id)
└── logs/                      # Loguru logs + heartbeat.jsonl + debug HTML
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
            _parse_results(html)  ← CSS selectors / API JSON / JSON-LD
            ↓
            _build_record(...)    ← normalize, extract_brand, seller canônico, offer_key
    ↓  (ML: keywords adiadas pelo freio → repescagem no fim do turno)
all_records → DataFrame
    ├─► CSV (output/) + <csv>.run_id
    ├─► Parquet no Drive (histórico frio — ANTES do banco, independente dele)
    └─► Supabase `coletas` (janela quente, RAC_HOT_WINDOW_DAYS=2)
    ↓
Telegram (resumo) → automação ADMIN pós-coleta → heartbeat de fim (RAC_JOB_ID)
(a batida de INÍCIO é gravada antes do loop de coleta)

PC coletor, após cada turno (local_scheduled_collect.bat):
  estágio C → build_seller_offer_daily.py --heartbeat   (seller_app)
  estágio D → history_cli.py tier --dataset all --confirm --heartbeat   (só noite)
```

## Key Classes & Methods

### playwright_runtime (scrapers/playwright_runtime.py)
- `acquire(prefer_rebrowser=False)` / `release()` — **um** `sync_playwright()`
  por thread, com contagem de referências. Dois handles vivos na mesma thread
  estouram "Sync API inside the asyncio loop" (COMMON_MISTAKES #21)
- `is_target_closed(exc)` — distingue browser morto de timeout/bloqueio

### LocalBrowser (scrapers/local_browser.py)
- `get_local_browser()` — singleton do Chrome real; auto-cura antes de devolver
- `is_alive()` / `reconnect()` / `new_page()` — a janela é do usuário e pode
  ser fechada no meio da run (COMMON_MISTAKES #22)

### BaseScraper (scrapers/base.py)
- `_launch()` — handle via `playwright_runtime.acquire()`, Chrome→msedge→Chromium, stealth JS
- `_close()` — Clean browser shutdown + `playwright_runtime.release()`
- `_rotate_browser()` — Close + relaunch with new User-Agent (Radware mitigation)
- `_build_record()` — Standardized dict for DataFrame row: campos de insight
  `buy_box_seller`, `qtd_sellers`, `tipo_seller`, `reputacao_seller`,
  `Patrocinado?`, identidade da oferta e seller canônico
- `_random_delay()`, `_human_scroll()`, `_wait_for_network_idle()`

### DealerScraper (scrapers/dealers.py)
- `DEALER_CONFIGS` — Dict with URL, pagination type, max_pages, item_selector, `on_hold`
- `_detect_items()` — CSS selector chain with max_items sanity check
- `_extract_price_el()` — 5-level price fallback (CSS→VTEX split→data-price→meta→regex)
- `_extract_jsonld_prices()` / `_jsonld_match()` — schema.org/Product (Jaccard ≥60%)
- `_deduplicate()` — By (platform, title), reassigns positions after dedup
- `_is_blocked_page()` — Detects reCAPTCHA/Cloudflare

### Config (config.py)
- `KEYWORDS_LIST` — `List[Keyword]` with term, category, priority
- `ACTIVE_PLATFORMS` — `Dict[str, bool]` (todas on, exceto `fast`)
- `BRANDS` — Ordered list (most specific first: "Springer Midea" before "Midea")
- `PLATFORM_TYPE` — Maps platform name → type string for CSV
- `TURNO_ABERTURA_MAX_HOUR=11`, `TURNO_TARDE_MAX_HOUR=17`

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
| Seller canonical names | `utils/seller_names.py` SELLER_GROUPS |
| Who runs what, when | `utils/pipeline_registry.py` JOBS |
| CSV columns | `main.py` COLUMN_ORDER |
| User-Agent list | `config.py` USER_AGENTS |
| Stealth JS patches | `scrapers/base.py` _STEALTH_JS |
| Mais Vendidos sources | `bestsellers/config.py` SOURCES / COLETA |
