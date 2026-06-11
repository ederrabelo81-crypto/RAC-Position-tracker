# RAC Position Tracker — Retail Analytics & Competitive Intelligence

Monitoramento de **buy box, sellers e posicionamento** de ar condicionado nos marketplaces brasileiros, com preço diário consolidado via **PriceTrack** e inteligência competitiva via Claude API.

**Status:** ✅ Produção | **Última atualização:** 11 de Junho de 2026 (v4.0)

---

## 📋 Visão Geral

> **Foco desde Mai/2026: buy box & sellers — preço virou campo secundário.**
> O preço "oficial" dos dashboards vem da API da Price Track (importação
> diária); a coleta própria entrega o que o PriceTrack não tem: posição na
> busca, patrocinado, buy box, tipo/reputação de seller e avaliações.

O projeto monitora em 7 marketplaces:
- **Buy box & sellers** — quem vence a oferta (`Buy Box Seller`), quantos competem (`Qtd Sellers`), 1P/3P/Loja Oficial (`Tipo Seller`), reputação
- **Posicionamento** orgânico × patrocinado (Share of Voice de mídia)
- **Avaliações** — rating e volume de reviews por produto/marca
- **Preços** — coleta própria (secundário) + PriceTrack (fonte de verdade)
- **Análise competitiva via IA** (Claude API) com relatório executivo

Dados → CSV → Supabase (`coletas` + `pricetrack_daily`) → dashboard Streamlit (20 páginas) → notificações Telegram (N8N ou API direta).

---

## 🏗️ Arquitetura de Coleta — 3 canais + PriceTrack

```
Oracle Cloud VM (Brazil East — São Paulo)              [canal primário]
  ├─ Cron 10:00 BRT → plataformas ativas, alta+media, 2 páginas
  ├─ Cron 21:00 BRT → plataformas ativas, alta, 1 página
  └─ Cron 06:00 BRT → import PriceTrack (D-1) — espelho do GH Actions

GitHub Actions                                          [backup agendado]
  ├─ collect.yml          → cron 13:00/00:00 UTC (10:00/21:00 BRT) + manual
  │                         (sem ML — IPs do GitHub bloqueados; Magalu via xvfb)
  └─ pricetrack_daily.yml → cron 09:00 UTC (06:00 BRT) + auto-heal de gaps (14d)

PC pessoal Windows (IP residencial)                     [coleta autenticada]
  └─ Task Scheduler 10:05/21:05 → collect_authenticated_cdp.bat
       Chrome real (CDP :9222) → renova sessões Shopee/Casas Bahia
       → coleta Magalu + Shopee + Casas Bahia → upload
       Ver docs/AUTOMACAO_COLETAS_AUTENTICADAS.md
```

Após cada coleta: upload automático ao Supabase + notificação Telegram.
Watchdog: `python scripts/daily_status_check.py` (PASS/FAIL por plataforma +
cobertura de campos de insight com alerta de regressão).

---

## 🌐 Plataformas (foco buy box/seller — Jun/2026)

| Plataforma | Status | Canal | Observações |
|------------|--------|-------|-------------|
| Mercado Livre | ✅ | VM (xvfb) / local | Buy box ✓; avaliação/patrocinado/Loja Oficial **corrigidos em Jun/2026** (estavam 0% — ver `docs/DIAGNOSTICO_COLETA_JUN2026.md`). Complemento opcional `--platforms ml_api` (API oficial OAuth) preenche `reputacao_seller` |
| Amazon | ✅ | VM / GH Actions | Buy box via "Vendido por"; `Qtd Sellers` de "X ofertas"; 1P vs 3P |
| Leroy Merlin | ✅ | VM / GH Actions | Algolia API; 1P vs 3P marketplace |
| Magalu | ✅ | **PC via CDP** (primário), VM best-effort | Akamai: Chrome real + `rebrowser-playwright` + busca orgânica + circuit breaker (aborta após 5 keywords 100% bloqueadas) |
| Casas Bahia | ✅ via CDP/sessão | **PC via CDP** | VTEX Intelligent Search (`sellers[]` → buy box); IP datacenter bloqueado → sessão renovada automaticamente no PC |
| Shopee | 🟡 via CDP/sessão | **PC via CDP** | API v4 + cookies de conta logada (`SPC_*` expiram em horas → `refresh_sessions_cdp.py` renova antes de cada coleta) |
| Google Shopping | ⚠️ | VM / GH Actions | reCAPTCHA em headless; `Qtd Sellers` = nº de lojas comparando |
| Fast Shop | ⏸️ | — | Bloqueio total PerimeterX |
| Dealers (13+) | ⏸️ | — | Fora do foco (`ACTIVE_PLATFORMS["dealers"]=False`); scraper mantido |

> **Causa raiz dos bloqueios** (Shopee/CB/Magalu): IP de datacenter marcado
> pelo antibot antes do fingerprint. Solução em produção: coleta autenticada
> no PC com IP residencial (`docs/AUTOMACAO_COLETAS_AUTENTICADAS.md`).
> Evolução planejada: proxy residencial BR na VM.

---

## 💰 PriceTrack — fonte de verdade de preço

Import diário (06:00 BRT) do export da API Price Track: preços
min/avg/mode/max por `(data, marca, sku, marketplace, seller)` da categoria
AR CONDICIONADO → tabela `pricetrack_daily`.

- **Pipeline:** `scripts/pricetrack_api_import.py` (export assíncrono → NDJSON.gz → agrega → upsert) + `--gaps-only` auto-heal dos últimos 14 dias
- **Importador manual** (md/xlsx): `python -m pricetrack_importer arquivo.md`
- **Precedência (28/05/2026):** para cada `(data, sku_resolvido)` presente no PriceTrack, os dashboards de preço descartam a linha equivalente das coletas
- **Reconciliação:** de-para de marketplace (`_PT_TO_CANONICAL_PLATFORM` no `app.py`) e de seller (`pricetrack_importer/seller_map.py`, ~103 variantes → ~30 canônicos)
- **Env:** `PRICETRACK_API_KEY` no `.env` / GitHub Secrets

📄 Insights e roadmap de melhorias: `docs/PRICETRACK_INSIGHTS.md`

---

## 🚀 Instalação Local

### Pré-requisitos

- Python 3.10+
- Playwright browsers instalados (`rebrowser-playwright` para CDP/Akamai)
- Supabase configurado (obrigatório para dashboard)
- Conta Anthropic (opcional, para Competitive Intelligence)

```bash
git clone https://github.com/ederrabelo81-crypto/RAC-Position-tracker.git
cd RAC-Position-tracker

python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux/Mac

pip install -r requirements.txt
python -m playwright install chromium
```

### Arquivo `.env`

```env
# Supabase (obrigatório para upload e dashboard) — usar service_role key!
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_KEY=sua_service_role_key

# PriceTrack (import diário de preços)
PRICETRACK_API_KEY=...

# Anthropic (opcional — página Competitive Intelligence)
ANTHROPIC_API_KEY=sk-ant-...

# Mercado Livre API oficial (opcional — coleta complementar ml_api p/ reputação)
ML_APP_ID=...
ML_APP_SECRET=...

# Nome do analista nos relatórios
ANALYST_NAME="Bot Automático Python"

# Notificações Telegram — N8N (opcional) + fallback direto
N8N_WEBHOOK_URL=http://localhost:5678/webhook/coleta
N8N_TELEGRAM_CHAT_ID=123456789
TELEGRAM_BOT_TOKEN=7730291785:AAF...
```

---

## 📖 Uso

```bash
# Demo rápida (Mercado Livre, 1 keyword, 1 página)
python main.py

# Todas as plataformas ativas, 2 páginas
python main.py --platforms all --pages 2

# Plataformas individuais
python main.py --platforms casasbahia --pages 1   # VTEX IS + warm-up Akamai
python main.py --platforms shopee --pages 1       # API v4 (requer sessão)
python main.py --platforms magalu --pages 2       # CDP/browser persistente

# Coleta complementar de reputação de seller (fora do "all"; requer OAuth ML)
python main.py --platforms ml_api --pages 1

# Browser visível (debug)
python main.py --platforms ml --pages 1 --no-headless
```

### Opções de Linha de Comando

| Opção | Descrição | Padrão |
|-------|-----------|--------|
| `--platforms` | `ml`, `ml_api`, `amazon`, `magalu`, `casasbahia`, `google_shopping`, `leroy`, `shopee`, `fast`, `dealers`, `all` | `ACTIVE_PLATFORMS` do config.py |
| `--pages` | Páginas por keyword | 3 |
| `--keywords` | Keywords customizadas (substitui config.py) | `KEYWORDS_LIST` |
| `--priority` | Filtro: `alta`, `media`, `baixa` | todas |
| `--headless` / `--no-headless` | Browser sem/com interface | headless |
| `--output-dir` | Diretório de saída dos CSVs | `output/` |
| `--debug-hits` | Salva N hits Algolia brutos (diagnóstico Leroy) | — |

> `ml_api` não entra no `all` (duplicaria os registros do ML) — é uma coleta
> complementar para `reputacao_seller`. Setup único: `python scripts/ml_oauth_setup.py`.

### Coleta autenticada (Magalu + Shopee + Casas Bahia) — PC Windows

```powershell
# Setup (1x): perfil CDP + login Shopee + agendamento 10:05/21:05
scripts\setup_cdp_profile.bat
scripts\start_chrome_cdp.bat        # logar 1x na Shopee neste Chrome
PowerShell -ExecutionPolicy Bypass -File scripts\setup_authenticated_scheduler.ps1

# Manual
python scripts\refresh_sessions_cdp.py --sites shopee casasbahia   # só sessões
scripts\collect_authenticated_cdp.bat 1                            # ciclo completo
```

📄 Detalhes e alternativas (proxy residencial, Tailscale): `docs/AUTOMACAO_COLETAS_AUTENTICADAS.md`

---

## 📊 Output e Dashboard

### Arquivos Gerados

- **CSV:** `output/rac_monitoramento_YYYYMMDD_HHMM.csv` — UTF-8 BOM, separador `;`
- **Logs:** `logs/bot_YYYYMMDD_HHMMSS.log` (rotação 50 MB, retenção 7 dias)
- **Screenshots SERP:** capturados por keyword/página
- **HTML de debug:** `logs/dealer_debug_<nome>_p<N>.html` e `logs/ml_debug_*.html`

### Colunas do CSV (schema Jun/2026)

```
Data; Turno; Horário; Analista; Plataforma; Tipo Plataforma;
Keyword Buscada; Categoria Keyword; Marca Monitorada; Produto / SKU;
Produto Normalizado; Posição Orgânica; Posição Patrocinada; Posição Geral;
Patrocinado?; Buy Box Seller; Qtd Sellers; Tipo Seller; Reputação Seller;
Seller / Vendedor; Fulfillment?; Avaliação; Qtd Avaliações; Tag Destaque;
Preço (R$); URL Produto; Screenshot Busca; Screenshot Produto
```

Campos de insight (protagonistas desde Mai/2026): `Patrocinado?`,
`Buy Box Seller`, `Qtd Sellers`, `Tipo Seller`, `Reputação Seller`.
Migrations do banco: `migrations/` + `docs/migrations/` (001→005).

### Dashboard Streamlit — 20 páginas

```bash
streamlit run app.py
```

**INSIGHTS (12):**
- **🏠 Overview** — métricas consolidadas, evolução de preços, tendências
- **🚨 Top Movers** — SKUs com maior variação (janelas comparativas, confiança, sparkline)
- **📊 Results** — detalhamento de coletas com filtros avançados
- **📈 Price Evolution** — séries temporais (preço = PriceTrack)
- **📊 Market Analytics** — share de marcas, posicionamento, benchmarking
- **🗂️ Ficha do Produto** — SKU específico + screenshots
- **🏆 BuyBox Position** — quem vence a posição #1 por produto/plataforma
- **👑 Share of Buy Box** — vencedor da oferta por seller/marca/período
- **⭐ Reputação & Avaliações** — rating, reviews, reputação × buy box, fulfillment
- **📣 SoV Patrocinado** — quem compra mídia, keywords disputadas, dupla presença
- **📦 Availability** — presença por posição + Visibility Score ponderado
- **🧠 Competitive Intelligence** — relatório IA (Claude) com download Markdown

**OPERAÇÕES (5):**
- **🚀 Run Collection** — coleta manual (plataformas/keywords/páginas)
- **📧 Email Digest** — relatórios HTML/texto por email
- **🔔 Price Anomalies** — variações suspeitas (>50%)
- **📂 Import History** — histórico de CSVs + upload via Streamlit
- **🩺 Data Health** — cobertura de coleta + matriz campo × plataforma (regressões)

**ADMIN (3):**
- **🧹 Data Cleanup** — remove registros não-AC
- **🔤 Normalize SKUs** — re-normalização em batch
- **🧬 Família & SKU** — resolve fila REVISAR do de-para (catálogo)

---

## 📲 Notificações Telegram

Resumo executivo automático após cada coleta: volume/duração/plataformas,
matriz de preço Midea por linha × capacidade, ranking top 5 por keyword
estratégica, maiores quedas/altas, ganhos/perdas de buy box Midea.

Configuração no `.env` (N8N opcional; fallback direto via `TELEGRAM_BOT_TOKEN`).
Workflow importável: `n8n/rac_coleta_monitor.json` · Guia: `docs/n8n_orchestration.md`

---

## ☁️ Infraestrutura — Oracle Cloud Free Tier

```bash
# Setup completo da VM (Python, Playwright, swap 2GB, crons):
curl -fsSL https://raw.githubusercontent.com/ederrabelo81-crypto/RAC-Position-tracker/main/scripts/oracle_setup.sh -o oracle_setup.sh
chmod +x oracle_setup.sh
./oracle_setup.sh --supabase-url "https://xxxx.supabase.co" --supabase-key "service_role_key"
```

| Script | Horário BRT | Função |
|--------|-------------|--------|
| `collect_manha_linux.sh` | 10:00 | Coleta alta+media, 2 páginas (xvfb p/ ML/Magalu) |
| `collect_noite_linux.sh` | 21:00 | Coleta alta, 1 página |
| `pricetrack_import_linux.sh` | 06:00 | Import PriceTrack D-1 (espelho do GH Actions) |
| `daily_status_check.py` | diário | PASS/FAIL por plataforma + cobertura de campos → Telegram |

```bash
# Monitoramento
python scripts/daily_status_check.py                  # hoje, ambos turnos
python scripts/daily_status_check.py --turno Abertura
python scripts/daily_status_check.py --data 2026-05-14 --no-notify
```

---

## 🔄 GitHub Actions

| Workflow | Trigger | Função |
|----------|---------|--------|
| `collect.yml` | cron 13:00/00:00 UTC + manual | Coleta (sem ML — IP bloqueado); Magalu com `MAGALU_HEADLESS=false` + xvfb; inputs: platforms/pages/priority |
| `pricetrack_daily.yml` | cron 09:00 UTC + manual | Import PriceTrack D-1 + auto-heal `--gaps-only` (14 dias); inputs: start/end/force |

---

## 🔧 Configuração

- **Keywords:** 31 em `config.py` (`KEYWORDS_LIST`) — head terms, capacidade BTU (9/12/18/24k), marca própria Midea, concorrentes, intenção de compra. Prioridades: `alta` (2 turnos), `media` (manhã), `baixa` (sob demanda)
- **Marcas:** 43 em `config.py` (`BRANDS`) — MCJV (Midea/Springer Midea/Springer) + LG, Samsung, Elgin, Gree, TCL, Philco, Electrolux, Agratto, emergentes…
- **Plataformas ativas:** `ACTIVE_PLATFORMS` (7 on; `fast`/`dealers` off)
- **Turno:** `TURNO_ABERTURA_MAX_HOUR=12` — timestamps sempre BRT via `now_brt()` (independe do relógio do SO)

| Preciso mudar… | Arquivo |
|----------------|---------|
| Keywords / plataformas / marcas / delays | `config.py` |
| Seletores ML (Poly) | `scrapers/mercado_livre.py` `_SELECTORS` |
| Dealer URLs/seletores | `scrapers/dealers.py` `DEALER_CONFIGS` |
| Parser de preço | `utils/text.py` `parse_price_brazil()` |
| Colunas CSV | `main.py` `COLUMN_ORDER` |
| De-para PriceTrack↔coletas | `app.py` `_PT_TO_CANONICAL_PLATFORM` / `pricetrack_importer/seller_map.py` |

---

## 📁 Estrutura do Projeto

```
rac-position-tracker/
├── main.py                       # CLI (argparse, registry de scrapers, CSV, upload)
├── app.py                        # Dashboard Streamlit (20 páginas + CI Claude)
├── config.py                     # Keywords, plataformas, marcas, delays
│
├── scrapers/
│   ├── base.py                   # BaseScraper ABC (Playwright, stealth, _build_record)
│   ├── mercado_livre.py          # MLScraper (browser; fix campos de insight Jun/2026)
│   ├── mercado_livre_api.py      # MLAPIScraper (API oficial OAuth — reputação; opt-in)
│   ├── amazon.py                 # AmazonScraper
│   ├── magalu.py                 # MagaluScraper (CDP/persistente, rebrowser-playwright)
│   ├── casas_bahia.py            # CasasBahiaScraper (VTEX IS + warm-up Akamai)
│   ├── shopee.py                 # ShopeeScraper (API v4 + sessão curl_cffi)
│   ├── google_shopping.py        # GoogleShoppingScraper
│   ├── leroy_merlin.py           # LeroyMerlinScraper (Algolia)
│   ├── dealers.py                # DealerScraper (⏸️ fora do foco)
│   └── fast_shop.py              # ⏸️ PerimeterX
│
├── pricetrack_importer/          # Importador md/xlsx (parser/validator/seller_map)
├── scripts/
│   ├── pricetrack_api_import.py  # Import diário via API PriceTrack
│   ├── refresh_sessions_cdp.py   # Renova sessões Shopee/CB/ML via Chrome CDP 🆕
│   ├── collect_authenticated_cdp.bat   # Magalu+Shopee+CB no PC (CDP) 🆕
│   ├── setup_authenticated_scheduler.ps1  # Task Scheduler 10:05/21:05 🆕
│   ├── start_chrome_cdp.bat / setup_cdp_profile.bat  # Chrome CDP (perfil real)
│   ├── daily_status_check.py     # Watchdog PASS/FAIL + cobertura de campos
│   ├── diagnose_ml.py            # Diagnóstico ML: taxa de acerto por campo/seletor
│   ├── ml_oauth_setup.py         # Setup OAuth da API oficial do ML (1x)
│   ├── collect_*_linux.sh        # Crons da VM Oracle
│   └── oracle_setup.sh           # Setup completo da VM
│
├── utils/
│   ├── text.py                   # parse_price, parse_rating, now_brt, turno
│   ├── brands.py                 # extract_brand()
│   ├── normalize_product.py      # normalização v1 + v2 (SKU-anchored)
│   ├── session_grabber.py        # Captura manual de sessões (fallback)
│   ├── supabase_client.py        # Upload (manutenção em supabase_maintenance.py)
│   └── n8n_notify.py             # Telegram (N8N + fallback direto)
│
├── tests/                        # pytest (parser ML, de-para, normalização v2)
├── migrations/ + docs/migrations/ # SQL: pricetrack, buy box, índices, depara
├── .github/workflows/            # collect.yml + pricetrack_daily.yml
├── n8n/                          # Workflow Telegram importável
├── magalu_shopee/                # Sub-projeto Node/TS (fallback Shopee)
├── docs/                         # Documentação técnica (ver docs/INDEX.md)
├── output/                       # CSVs
└── logs/                         # Loguru + HTML de debug
```

---

## 🧪 Testes & Diagnóstico

```bash
pytest tests/ -q                          # parser ML, de-para, normalização v2

# Antes de deployar mudança em scraper:
python main.py --platforms ml --pages 1 --no-headless
python scripts/diagnose_ml.py             # taxa de acerto por campo (ML)
python scripts/diagnose_ml.py --html logs/ml_debug_0.html   # analisa HTML salvo
python scripts/smoke_test.py              # smoke geral
```

---

## 🐛 Troubleshooting

| Problema | Solução |
|----------|---------|
| Playwright não encontra browsers | `python -m playwright install chromium` |
| Upload Supabase ignorado | `.env` com `SUPABASE_URL`/`SUPABASE_KEY` (**service_role**) |
| Turno invertido | `python scripts/fix_turno.py --confirm` |
| Dealer/ML retorna 0 produtos | ver `logs/*_debug_*.html` + `--no-headless` |
| ML sem avaliação/patrocinado no banco | rodar `python scripts/diagnose_ml.py` e conferir 🩺 Data Health (fix Jun/2026 — seletores Poly) |
| Magalu 403 / `_abck` em challenge | `pip install rebrowser-playwright`; ver troubleshooting completo em `docs/cdp_magalu_collection.md` |
| Shopee `error=90309999` | sessão expirada → `python scripts/refresh_sessions_cdp.py --sites shopee` (ou `session_grabber.py` manual) |
| Casas Bahia parada | renovar sessão via CDP no PC (IP datacenter é bloqueado) |
| VM Oracle OOM | swap 2 GB: `free -h`, `sudo swapon --show` |
| Telegram não chega | testar `curl https://api.telegram.org/bot<TOKEN>/getMe` |

**Variáveis de ambiente do Magalu/CDP:**

```env
MAGALU_HEADLESS=false              # browser visível (obrigatório em produção)
MAGALU_CDP_URL=http://localhost:9222   # se setada, ativa modo CDP (Chrome real)
MAGALU_FORCE_CURL=true             # só curl_cffi (não funciona hoje; futuro)
RAC_CDP_URL=http://localhost:9222  # CDP p/ refresh_sessions_cdp.py (fallback: MAGALU_CDP_URL)
```

---

## 🛠️ Manutenção do Banco (Supabase)

Funções em `utils/supabase_maintenance.py` (todas com `dry_run=True`):
`fix_inverted_turno`, `delete_invalid` (não-AC), `normalize_brands`,
`scan_fix_bad_prices` (bug ×10), `normalize_all_products`.

Utilitários: `cleanup_supabase.py`, `normalize_supabase.py`,
`import_history.py`, `reenviar_csv.py`, `scripts/fix_turno.py`,
`scripts/auto_resolver_depara.py` (fila REVISAR do catálogo).

---

## 📚 Documentação Técnica

| Documento | Finalidade |
|-----------|------------|
| `docs/INDEX.md` | Navegação por tarefa |
| `docs/AUTOMACAO_COLETAS_AUTENTICADAS.md` | Automação Shopee/Magalu/CB (CDP + sessões) 🆕 |
| `docs/PRICETRACK_INSIGHTS.md` | Pipeline PriceTrack + roadmap de insights 🆕 |
| `docs/DIAGNOSTICO_COLETA_JUN2026.md` | Diagnóstico de cobertura por campo/plataforma |
| `docs/cdp_magalu_collection.md` | Setup Chrome CDP (Windows + Task Scheduler) |
| `docs/n8n_orchestration.md` | Orquestração n8n (validação CSV + Telegram) |
| `.claude/` + `docs/learnings/` | Guias para sessões de IA (anti-patterns, padrões) |

---

## 📝 Dependências Principais

`playwright>=1.50` + `rebrowser-playwright` (anti-detecção CDP) ·
`curl-cffi` (TLS impersonation) · `beautifulsoup4` · `pandas` · `loguru` ·
`tenacity` · `supabase>=2.3` · `streamlit>=1.35` · `plotly` ·
`anthropic>=0.40` · `openpyxl` (PriceTrack xlsx) · `Pillow` · `filelock`

Dashboard usa o subset `requirements_app.txt`.

---

## ✅ Validação Operacional — 11/06/2026

- ✅ **20 páginas** de dashboard (12 Insights + 5 Operações + 3 Admin)
- ✅ **7 plataformas ativas** com buy box/seller (rollout fim de Mai/2026)
- ✅ **PriceTrack diário** como fonte de verdade de preço (06:00 BRT + auto-heal)
- ✅ **Coleta autenticada automatizada** Magalu+Shopee+CB via CDP (Jun/2026)
- ✅ **Fix ML**: avaliação, reviews, patrocinado, Loja Oficial (Jun/2026)
- ✅ **Data Health** com matriz campo × plataforma + alerta de regressão
- ✅ 31 keywords · 43 marcas · catálogo de-para com auto-resolver

---

**Stack:** Python · Playwright/rebrowser · curl_cffi · BeautifulSoup · Pandas · Streamlit · Supabase · Claude API · Oracle Cloud · GitHub Actions

**Versão:** 4.0 | **Última atualização:** 11 de Junho de 2026 | @ederrabelo
