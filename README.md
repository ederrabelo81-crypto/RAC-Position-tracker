# RAC Position Tracker — Retail Analytics & Competitive Intelligence

Monitoramento de **buy box, sellers e posicionamento** de ar condicionado nos marketplaces brasileiros, com preço diário consolidado via **PriceTrack** e inteligência competitiva via Claude API.

**Status:** ✅ Produção | **Última atualização:** 11 de Julho de 2026 (v4.4)

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

Dados → CSV → Supabase (`coletas` + `pricetrack_daily`) → dashboard Streamlit (19 páginas) → notificações Telegram (API direta).

---

## 🏗️ Arquitetura de Coleta — 3 canais + PriceTrack

```
Oracle Cloud VM (Brazil East — São Paulo)              [canal primário]
  ├─ Cron 10:00 BRT → plataformas ativas (sem ML), alta+media, 2 páginas
  ├─ Cron 21:00 BRT → plataformas ativas (sem ML), alta, 1 página
  └─ Cron 06:00 BRT → import PriceTrack (D-1) — espelho do GH Actions

GitHub Actions                                          [backup agendado]
  ├─ collect.yml          → cron 13:00/00:00 UTC (10:00/21:00 BRT) + manual
  │                         (sem ML — IPs do GitHub bloqueados; Magalu via xvfb)
  └─ pricetrack_daily.yml → cron 09:00 UTC (06:00 BRT) + auto-heal de gaps (14d)

PC pessoal Windows (IP residencial)                     [ML + coleta autenticada]
  ├─ Task Scheduler 09:00/20:00 + catch-up no logon (RAC_Local_Manha/Noite)
  │    → run_local_scheduled.bat (git pull self-update)
  │    → local_scheduled_collect.bat (janela de turno 9-12h/20-23h + marcador
  │      diário + alerta Telegram em falha) → collect_local_authenticated.bat
  │    Chrome COMUM logado (perfil dedicado, RAC_LOCAL_CHROME=1) → ataque via CDP
  │    → coleta Magalu + Shopee + Casas Bahia → upload
  │    Setup: scripts\setup_local_scheduler.ps1 · Diagnóstico: check_local_scheduler.ps1
  │    Detalhes: docs/COLETA_LOCAL_AUTENTICADA.md
  └─ Task Scheduler 10:00/21:00 (RAC_Coleta_Manha/Tarde) → collect_manha.bat / collect_tarde.bat
       → coleta Mercado Livre (IP de datacenter da VM é bloqueado pelo ML)
       + Shopee de reforço, se houver sessão capturada → upload
       Setup: scripts\install_tasks.bat
```

Mercado Livre roda **exclusivamente** no PC local (IP residencial) — foi
removido da VM/GitHub Actions porque o IP de datacenter é bloqueado pelo ML.
Magalu, Shopee e Casas Bahia rodam tanto na VM (best-effort/warm-up) quanto no
PC (canal primário, mais estável).

Após cada coleta: upload automático ao Supabase + notificação Telegram.
Watchdog: `python scripts/daily_status_check.py` (PASS/FAIL por plataforma +
cobertura de campos de insight com alerta de regressão).

---

## 🌐 Plataformas (foco buy box/seller — Jun/2026)

| Plataforma | Status | Canal | Observações |
|------------|--------|-------|-------------|
| Mercado Livre | ✅ | **PC local** (Task Scheduler 10:00/21:00) | Buy box ✓; avaliação/patrocinado/Loja Oficial **corrigidos em Jun/2026** (estavam 0% — ver `docs/DIAGNOSTICO_COLETA_JUN2026.md`). Removido da VM (IP de datacenter bloqueado pelo ML). Complemento opcional `--platforms ml_api` (API oficial OAuth) preenche `reputacao_seller` |
| Amazon | ✅ | VM / GH Actions | Buy box via "Vendido por"; `Qtd Sellers` de "X ofertas"; 1P vs 3P |
| Leroy Merlin | ✅ | VM / GH Actions | Algolia API; 1P vs 3P marketplace |
| Magalu | ✅ | **PC local** (primário, 09:00/20:00), VM best-effort | Akamai: Chrome comum + perfil dedicado, ataque via CDP (`rebrowser-playwright`) + busca orgânica + circuit breaker (aborta após 5 keywords 100% bloqueadas) |
| Casas Bahia | ✅ | **PC local** (primário, 09:00/20:00), VM best-effort | VTEX Intelligent Search (`sellers[]` → buy box); IP datacenter também destrava via warm-up Akamai, mas o PC (IP residencial) é mais estável |
| Shopee | 🟡 | **PC local** (primário, 09:00/20:00), VM se houver sessão | API v4 + cookies de conta logada (`SPC_*` expiram em horas); no PC a sessão fica persistida no Chrome comum logado (`setup_local_profile.py`) |
| Google Shopping | ⚠️ | VM / GH Actions | reCAPTCHA em headless; `Qtd Sellers` = nº de lojas comparando |
| Fast Shop | ⏸️ | — | Bloqueio total PerimeterX |
| Dealers (13+) | ⏸️ | — | Fora do foco (`ACTIVE_PLATFORMS["dealers"]=False`); scraper mantido |

> **Causa raiz dos bloqueios** (Shopee/CB/Magalu na VM): IP de datacenter
> marcado pelo antibot antes do fingerprint. Solução em produção: coleta
> autenticada no PC com IP residencial, Chrome comum + perfil dedicado
> (`docs/COLETA_LOCAL_AUTENTICADA.md`). Evolução planejada: proxy residencial
> BR na VM.

---

## 💰 PriceTrack — fonte de verdade de preço

Import diário (06:00 BRT) do export da API Price Track: preços
min/avg/mode/max por `(data, turno, marca, sku, marketplace, seller)` da
categoria AR CONDICIONADO → tabela `pricetrack_daily`.

- **Pipeline:** `scripts/pricetrack_api_import.py` (export assíncrono → NDJSON.gz → agrega → upsert) + `--gaps-only` auto-heal dos últimos 14 dias
- **Camada de API — pacote `pricetrack_api/` (Jul/2026):** cliente tipado da API Externa PriceTrack v1.2.0 (schemas `Offer`/`Shipping`, `PriceTrackClient`, `SmartCollector`), com paginação sempre via `meta.hasNextPage` + guarda anti-loop por assinatura completa de página, `ExportManager` com até 3 exports em voo e renovação automática de `downloadUrl` (TTL 1h), retry com backoff exponencial + jitter e erros tipados (400/401/409/429 — 429 honra `Retry-After` com teto), `SmartCollector` decide paginado × export em massa por threshold configurável, métricas estruturadas + alertas Telegram/log. 88 testes sem rede. `scripts/pricetrack_api_import.py` delega os exports do import diário a essa camada (`--concurrent` agora funciona de verdade). Docs: `pricetrack_api/README.md`; variáveis `PRICETRACK_*` opcionais no `.env.example`
- **Turnos intra-dia (Jun/2026):** `aggregate_offers()` deriva o turno do `collection_hour` e emite linhas **Manhã** (08–12h BRT) e **Tarde** (18–22h BRT) além do agregado **Diário** (dia inteiro), alimentando os turnos manhã/tarde do dashboard. Migration `migrations/003_pricetrack_turno.sql`
- **Import intra-dia do dia corrente (Jun/2026):** além do D-1 (06:00 BRT, definitivo), o PriceTrack de **hoje** é importado provisoriamente às **13:10** (após a manhã) e **23:10 BRT** (após a tarde) — `.github/workflows/pricetrack_intraday.yml` (e `pricetrack_import_linux.sh today` na VM). Assim, passado o meio-dia, a Manhã de hoje já vem do PriceTrack (não mais do fallback de Coletas). As linhas provisórias são sobrescritas pela versão completa no D-1 do dia seguinte (`--force`); `_should_redownload()` re-baixa hoje/ontem para não reaproveitar export parcial em cache
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
- Conta Anthropic (opcional — camada LLM da Automação Admin)

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

# PriceTrack (import diário de preços) — ver pricetrack_api/README.md p/ tuning opcional
# (PRICETRACK_BASE_URL, PRICETRACK_EXPORT_THRESHOLD_ROWS, PRICETRACK_MAX_RETRIES, PRICETRACK_MAX_CONCURRENT_EXPORTS…)
PRICETRACK_API_KEY=...

# Anthropic (opcional — camada LLM da Automação Admin)
ANTHROPIC_API_KEY=sk-ant-...

# Mercado Livre API oficial (opcional — coleta complementar ml_api p/ reputação)
ML_APP_ID=...
ML_APP_SECRET=...

# Nome do analista nos relatórios
ANALYST_NAME="Bot Automático Python"

# Notificações Telegram (API direta)
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

### Coleta local agendada (PC Windows, IP residencial)

O notebook/PC do analista roda **dois agendamentos** no Task Scheduler,
cobrindo as 4 plataformas que dependem de IP residencial (Mercado Livre) ou se
beneficiam dele (Magalu, Shopee, Casas Bahia):

**1. Magalu + Shopee + Casas Bahia — 09:00/20:00 (`RAC_Local_Manha/Noite`)**
Chrome comum + perfil dedicado, atacado via CDP (`rebrowser-playwright`). As
tarefas também disparam no **logon** (catch-up com janela de turno 9–12h/20–23h
e marcador diário — cobre notebook desligado no horário sem duplicar coleta) e
alertam no Telegram quando a coleta agendada falha.

```powershell
# Setup (1x): perfil dedicado + login Shopee + agendamento 09:00/20:00
python scripts\setup_local_profile.py     # abre o Chrome do perfil: logar 1x na Shopee
PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1

# Manual
scripts\collect_local_authenticated.bat 1                          # ciclo completo

# A tarefa agendada não rodou? Diagnóstico completo (sem Admin):
PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1
```

📄 Detalhes e troubleshooting: `docs/COLETA_LOCAL_AUTENTICADA.md`

**2. Mercado Livre (+ Shopee de reforço) — 10:00/21:00 (`RAC_Coleta_Manha/Tarde`)**
ML roda só aqui — foi removido da VM/GitHub Actions porque o IP de datacenter
é bloqueado pelo Mercado Livre.

```powershell
# Setup (1x, como Administrador): agenda collect_manha.bat / collect_tarde.bat
scripts\install_tasks.bat
```

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
Migrations do banco: `migrations/` (PriceTrack: 001→004, inclui turno e RPC de
piso por marca) + `docs/migrations/` (coletas: 001→009).

### Dashboard Streamlit — 19 páginas

```bash
streamlit run app.py
```

> **Filtros Globais (Jun/2026):** seletor global de **Fonte de Dados** (Coletas / PriceTrack / Combinado) + filtros enxutos no topo da sidebar — escolha uma vez e todas as páginas reagem. As páginas legadas **Run Collection** e **Competitive Intelligence** foram removidas (coleta agora é exclusivamente via cron/CLI; CI segue como camada de relatório no Overview).

**INSIGHTS (13):**
- **🏠 Overview** — métricas consolidadas, evolução de preços, tendências
- **📅 Daily Price Vision** 🆕 — menor preço por marketplace consolidado por
  marca (default) / marca+capacidade / SKU, com recorte de turno **Manhã /
  Tarde / Diário** (PriceTrack como autoridade, coletas preenchem lacunas).
  Visual fiel ao mockup: KPIs em cards com gradiente, tabela HTML (`st.html`
  via DOMPurify) com chips de logo por marca/marketplace, headers de MP
  coloridos, sparkline SVG 7d por marca (verde=caiu/vermelho=subiu), destaque
  do MP vencedor + match ±2%, badge de Gap 1º→2º, delta vs ontem, drill-down
  (SKU/seller por MP) e export CSV
- **🚨 Top Movers** — SKUs com maior variação (janelas comparativas, confiança, sparkline)
- **📊 Results** — detalhamento de coletas com filtros avançados
- **📈 Price Evolution** — séries temporais por **SKU** com métrica selecionável (**Buy Box** [default] / Moda / Mediana / Médio), guarda "Dados limpos", flag de série congelada e modo "Comparar fontes" (Coletas × PriceTrack)
- **📊 Market Analytics** — share de marcas, posicionamento, benchmarking
- **🗂️ Ficha do Produto** — SKU específico + screenshots
- **🏆 BuyBox Position** — quem vence a posição #1 por produto/plataforma
- **👑 Share of Buy Box** — vencedor da oferta por seller/marca/período
- **⭐ Reputação & Avaliações** — rating, reviews, reputação × buy box, fulfillment
- **📣 SoV Patrocinado** — quem compra mídia, keywords disputadas, dupla presença
- **🛡️ Price Compliance** — aderência ao preço sugerido por SKU/plataforma
- **📦 Availability** — presença por posição + Visibility Score ponderado

**OPERAÇÕES (4):**
- **📧 Email Digest** — relatórios HTML/texto por email
- **🔔 Price Anomalies** — variações suspeitas (>50%)
- **📂 Import History** — histórico de CSVs + upload via Streamlit
- **🩺 Data Health** — cobertura de coleta + matriz campo × plataforma (regressões)

**ADMIN (2):**
- **🤖 Automação** — manutenção 100% automática (sem cliques): limpeza de
  não-AC, preços suspeitos, normalizações (produto/marca/plataforma), seed +
  resolução da fila REVISAR (regras → LLM → heurística) e refresh de cache.
  Dispara pós-coleta (`main.py`), via cron (`scripts/admin_auto.py`) e em
  auto-run ao abrir a página (>24h). **Mutex via `pg_try_advisory_lock`**
  serializa execuções concorrentes e elimina timeouts 57014 (Jun/2026).
  Auditoria em `admin_automation_runs` (migration 006) + resumo no Telegram.
- **🧬 Família & SKU** — auditoria/override pontual do de-para. **Catálogo
  refinado data-driven (Jun/2026):** dedup voltagem-tolerante elevou SKU-exato
  de **88,3% → 90,3%**; resolver v2 com `attr_parser` + `sku_matcher` (FASES
  0-4) tem dry-run e validação antes do `--apply`.

---

## 📲 Notificações Telegram

Resumo executivo automático após cada coleta: volume/duração/plataformas,
matriz de preço Midea por linha × capacidade, ranking top 5 por keyword
estratégica, maiores quedas/altas, ganhos/perdas de buy box Midea.

Envio direto via API do Telegram (`TELEGRAM_BOT_TOKEN` + `N8N_TELEGRAM_CHAT_ID`
no `.env`). A antiga orquestração via n8n foi descontinuada — sem uso desde
meados de Jun/2026, o caminho direto é o único ativo em produção.

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
| `pricetrack_import_linux.sh` | 06:00 | Import PriceTrack D-1 definitivo (`--force`; espelho do GH Actions) |
| `pricetrack_import_linux.sh today` | 13:10 / 23:10 | Import PriceTrack do dia corrente (intra-dia: manhã/tarde) |
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
| `pricetrack_daily.yml` | cron 09:00 UTC + manual | Import PriceTrack D-1 (agendado `--force`) + auto-heal `--gaps-only` (14 dias); inputs: start/end/force |
| `pricetrack_intraday.yml` | cron 16:10/02:10 UTC + manual | Import PriceTrack do dia corrente (intra-dia: 13:10/23:10 BRT) p/ a Manhã/Tarde de hoje virem do PriceTrack |

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
├── app.py                        # Dashboard Streamlit (19 páginas + CI Claude)
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
├── pricetrack_api/               # Cliente tipado da API PriceTrack (client/collector/exports/store) 🆕
├── pricetrack_importer/          # Importador md/xlsx (parser/validator/seller_map)
├── scripts/
│   ├── pricetrack_api_import.py  # Import diário via API PriceTrack
│   ├── setup_local_profile.py    # Login 1x na Shopee (Chrome comum, perfil dedicado) 🆕
│   ├── collect_local_authenticated.bat  # Magalu+Shopee+CB no PC (Chrome comum+CDP) 🆕
│   ├── run_local_scheduled.bat   # Estágio A agendado (estável): git pull + estágio B 🆕
│   ├── local_scheduled_collect.bat # Estágio B: janela de turno + marcador + alerta 🆕
│   ├── setup_local_scheduler.ps1 # Task Scheduler 09:00/20:00 + logon (Magalu+Shopee+CB) 🆕
│   ├── check_local_scheduler.ps1 # Diagnóstico: por que a tarefa não rodou? 🆕
│   ├── collect_manha.bat / collect_tarde.bat  # Coleta ML (+Shopee) no PC, 10:00/21:00
│   ├── install_tasks.bat         # Task Scheduler p/ collect_manha/tarde.bat (ML)
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
│   └── n8n_notify.py             # Telegram (API direta)
│
├── tests/                        # pytest (parser ML, de-para, normalização v2)
├── migrations/ + docs/migrations/ # SQL: pricetrack, buy box, índices, depara
├── .github/workflows/            # collect.yml + pricetrack_daily.yml
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
| `pricetrack_api/README.md` | Cliente tipado da API PriceTrack — arquitetura, uso, config, robustez 🆕 |
| `docs/COLETA_LOCAL_AUTENTICADA.md` | Coleta local Magalu+Shopee+CB — Chrome comum + perfil dedicado, agendamento 🆕 |
| `docs/AUTOMACAO_COLETAS_AUTENTICADAS.md` | ⚠️ Superado — caminho antigo via CDP + perfil copiado (referência histórica) |
| `docs/PRICETRACK_INSIGHTS.md` | Pipeline PriceTrack + roadmap de insights 🆕 |
| `docs/DIAGNOSTICO_COLETA_JUN2026.md` | Diagnóstico de cobertura por campo/plataforma |
| `docs/cdp_magalu_collection.md` | Setup Chrome CDP (Windows + Task Scheduler) |
| `.claude/` + `docs/learnings/` | Guias para sessões de IA (anti-patterns, padrões) |

---

## 📝 Dependências Principais

`playwright>=1.50` + `rebrowser-playwright` (anti-detecção CDP) ·
`curl-cffi` (TLS impersonation) · `beautifulsoup4` · `pandas` · `loguru` ·
`tenacity` · `supabase>=2.3` · `streamlit>=1.35` · `plotly` ·
`anthropic>=0.40` · `openpyxl` (PriceTrack xlsx) · `Pillow` · `filelock`

Dashboard usa o subset `requirements_app.txt`.

---

## ✅ Validação Operacional — 12/07/2026

- ✅ **Agendamento local Windows corrigido de vez** 🆕 — a Action das tarefas
  `RAC_Local_*` era `cmd.exe /c "..." >> "..."`; com o espaço no caminho do
  projeto o cmd.exe descartava as aspas e a tarefa morria **sem escrever log**
  (por isso Magalu/Shopee/Casas Bahia "não rodavam"). Agora a Action é o
  próprio `.bat` (log interno), com catch-up no logon (janela de turno +
  marcador diário), alerta Telegram em falha e diagnóstico via
  `scripts\check_local_scheduler.ps1`. **Requer re-rodar
  `setup_local_scheduler.ps1` uma vez no notebook.**
- ✅ **Coleta local no PC Windows com self-update** — `run_local_scheduled.bat`
  roda `git pull` antes de cada coleta agendada (09:00/20:00), eliminando a
  defasagem entre o código do notebook e o do repositório
- ✅ **Chrome comum + perfil dedicado** (Jul/2026) — substitui o antigo CDP com
  perfil copiado (que deslogava as contas) para Magalu+Shopee+Casas Bahia;
  login via Google na Shopee volta a funcionar (`docs/COLETA_LOCAL_AUTENTICADA.md`)
- ✅ **Notificações Telegram simplificadas** — envio direto via API
  (`TELEGRAM_BOT_TOKEN`); orquestração via n8n descontinuada por falta de uso
  desde meados de Jun/2026
- ✅ **Cliente `pricetrack_api/`** 🆕 — camada tipada da API Externa PriceTrack v1.2.0 (paginação/export/retry/métricas, 88 testes); `pricetrack_api_import.py` delega os exports do import diário a ela e `--concurrent` passa a valer de fato
- ✅ **19 páginas** de dashboard (13 Insights + 4 Operações + 2 Admin) — removidas Run Collection e Competitive Intelligence
- ✅ **Daily Price Vision** — vista de menor preço por marketplace com turnos Manhã/Tarde/Diário, visual fiel ao mockup (KPIs, chips, sparkline 7d embutido como `<img>` base64, drill-down); drill-down corrigido com fonte "Coletas" isolada (schema `produto`↔`title` normalizado)
- ✅ **PriceTrack com turnos intra-dia** (Manhã 08–12h / Tarde 18–22h) derivados do `collection_hour` + RPC de piso por marca (sparkline server-side) + índice `(collection_date, id)` eliminando statement timeout
- ✅ **Filtros Globais enxutos** com seletor único de Fonte de Dados (Coletas / PriceTrack / Combinado); cache de preço/overview com TTL maior e chaves corrigidas para filtros globais de família/SKU (menor egress no Supabase)
- ✅ **7 plataformas ativas** com buy box/seller (rollout fim de Mai/2026)
- ✅ **PriceTrack diário** como fonte de verdade de preço (06:00 BRT + auto-heal)
- ✅ **Fix ML**: avaliação, reviews, patrocinado, Loja Oficial (Jun/2026)
- ✅ **Price Evolution** com métrica Buy Box-first + agrupamento por SKU + guarda de outliers
- ✅ **Catálogo SKU refinado** (dedup voltagem-tolerante): SKU-exato 88,3% → **90,3%**
- ✅ **De-para v2** com `attr_parser` + `sku_matcher` (FASES 0-4) — dry-run, validação e relatório consistente
- ✅ **Automação Admin com mutex** (`pg_try_advisory_lock`) — fim dos timeouts 57014
- ✅ **Data Health** com matriz campo × plataforma + alerta de regressão
- ✅ 31 keywords · 43 marcas · catálogo de-para com auto-resolver

---

**Stack:** Python · Playwright/rebrowser · curl_cffi · BeautifulSoup · Pandas · Streamlit · Supabase · Claude API · Oracle Cloud · GitHub Actions

**Versão:** 4.4 | **Última atualização:** 11 de Julho de 2026 | @ederrabelo
