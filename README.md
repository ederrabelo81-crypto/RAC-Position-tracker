# RAC Position Tracker — Retail Analytics & Competitive Intelligence

Monitoramento de **buy box, sellers e posicionamento** de ar condicionado nos marketplaces brasileiros, com preço diário consolidado via **PriceTrack** e inteligência competitiva via Claude API.

**Status:** ✅ Produção — arquitetura híbrida Supabase + Drive | **Última atualização:** 8 de Agosto de 2026 (v4.7)

> ### 🗄️ As duas bases, e o que cada uma faz
>
> O Supabase é a **base de dados principal**. Como o plano free termina em
> 500 MB, ele guarda apenas a **janela quente** (`RAC_HOT_WINDOW_DAYS`, hoje
> 15 dias) — é o lado com SQL, RPCs e o de-para aplicado pela automação Admin.
> Todo o histórico que não cabe ali vive em **Parquet no Google Drive**, que
> não tem teto prático (um ano ≈ 0,23 GB contra 15 GB gratuitos).
>
> **No dashboard as duas aparecem juntas.** `query_coletas()` e
> `query_pricetrack_daily()` leem o Supabase primeiro e **completam com o
> Drive os dias que o banco não devolveu** — seja porque já migraram, seja
> porque o banco está fora. Nenhuma página precisa saber de onde veio o dado;
> a coluna `_origem` marca a procedência para quem quiser sinalizar.
>
> ```
> coleta ──┬─► CSV local           (sempre)
>          ├─► Parquet no Drive    (sempre — independente do banco)
>          └─► Supabase            (janela quente)
>
> dashboard ──┬── Supabase: janela quente ─┐
>             └── Drive: todo o resto ─────┴─► uma série só
> ```
>
> Setup, operação e troubleshooting: **[`docs/HISTORICO_DRIVE.md`](docs/HISTORICO_DRIVE.md)**.
> A conta que justifica o desenho: **[`docs/ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md`](docs/ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md)**.

> ### ⚠️ Antes de religar a coleta no Supabase — 3 verificações
>
> 1. **`SUPABASE_KEY` dos GitHub Secrets.** Em 08/08/2026 todas as execuções
>    do `collect.yml` desde 04/08 falhavam com HTTP 401 `Unregistered API key`
>    — a chave do repositório não é válida para o projeto (a do `.env` do PC
>    coletor é, e por isso só ele gravou). Reponha o secret com uma
>    `service_role` válida antes de contar com o Actions.
> 2. **Espaço.** O banco fica dentro do free tier enquanto a migração rodar:
>    `python scripts/history_cli.py tier --dataset all --confirm` (mensal).
>    Sem ela o teto volta em semanas — `pricetrack_daily` sozinha responde por
>    quase metade do banco.
> 3. **Secrets de alerta.** `TELEGRAM_BOT_TOKEN` e `N8N_TELEGRAM_CHAT_ID`
>    estavam vazios no repositório, então as 8 falhas seguidas não notificaram
>    ninguém. O workflow avisa por `::warning`, que ninguém lê.

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

Dados → CSV → **histórico Parquet (Drive)** + Supabase (`coletas` +
`pricetrack_daily`, janela quente de 15 dias) → dashboard Streamlit (19 páginas)
→ notificações Telegram (API direta).

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
- **Import intra-dia — APOSENTADO em 08/08/2026.** O import do dia corrente rodava de hora em hora (`pricetrack_intraday.yml` + cron `refresh` na VM). Cada run criava um export; quando o run era morto antes de terminar, o export ficava **órfão segurando um dos 3 slots** da organização. Dois zumbis assim travaram toda a importação com HTTP 429 — inclusive as manuais. Ficou só o **D-1 das 06:00 BRT**. A Manhã/Tarde de hoje voltam a vir do fallback de Coletas até o D-1 do dia seguinte. O modo continua disponível para uso manual: `pricetrack_import_linux.sh today`
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
│   ├── history/                  # 🆕 Histórico frio em Parquet (Drive/disco)
│   │   ├── backends.py           #    LocalBackend + GoogleDriveBackend
│   │   └── store.py              #    Partições por dia, cache, união frio+quente
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
# Suíte completa — 948 testes (validado em 08/08/2026)
pytest tests/ pricetrack_api/tests pricetrack_importer/tests -q

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
| `exceed_db_size_quota` / HTTP 402 no upload | Banco no teto da cota — libere espaço (`scripts/retention_cleanup.sql` + `VACUUM FULL`) ou troque de plano; depois reenvie os CSVs com `python scripts/upload_csv.py <arquivo.csv>`. Contexto: `docs/ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md` |
| Coleta "verde" mas sem dado novo no banco | O workflow não falha quando o upload é rejeitado — rode `python scripts/daily_status_check.py` e agende-o |
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

## 🧊 Histórico frio — Parquet no Google Drive

Arquitetura híbrida (Jul/2026): o Supabase guarda a **janela quente** e o
histórico completo vive em Parquet no Drive.

```
coleta ──┬─► CSV local            (sempre)
         ├─► Histórico Parquet    (sempre — Drive, independente do banco)
         └─► Supabase             (janela quente de 15 dias)

dashboard ─┬─ query_coletas()          ─┬─ Supabase: últimos 15 dias
           └─ query_pricetrack_daily()  └─ Histórico: todo o resto
```

Os **dois datasets** (`coletas` e `pricetrack`) percorrem o mesmo caminho: cada
um tem sua subpasta no Drive, **escrita dupla** no momento da gravação, sua
migração e sua costura no dashboard. A escrita do Parquet acontece **antes** do
Supabase e não depende dele — é o que faz o dia sobreviver a um banco fora do ar
ou restrito por cota.

> Os workflows do PriceTrack precisam dos secrets `GDRIVE_*` para isso valer no
> Actions; sem eles o Parquet fica só no artifact do run (14 dias).

A gravação do histórico acontece **antes** do Supabase e não depende dele: com
o banco restrito por cota, o dia entra no histórico mesmo assim. A leitura é
costurada — `query_coletas()` completa com o histórico os dias que o banco não
devolveu, então nenhuma página do dashboard precisou mudar.

**Por que Parquet** (benchmark medido, 5.651 linhas no formato de produção):

| Formato | 1 dia | Relativo |
|---------|-------|----------|
| Postgres | 4.916 KB | 1× |
| CSV | 1.750 KB | 2,8× menor |
| **Parquet (zstd)** | **104 KB** | **47× menor** |

Um ano de coleta ≈ **0,23 GB** — os 15 GB gratuitos do Drive comportam décadas.

```bash
# Setup (uma vez) — cria a pasta e imprime as linhas do .env
python scripts/gdrive_setup.py --client-secrets client_secret.json
python scripts/gdrive_setup.py --check          # testa gravar/reler/limpar

# Operação
python scripts/history_cli.py stats             # dias, volume e buracos na série
python scripts/history_cli.py import-csv output/rac_monitoramento_*.csv
python scripts/history_cli.py tier              # coletas: Supabase → Drive (não apaga)
python scripts/history_cli.py tier --confirm    # apaga o já verificado

# PriceTrack — a maior tabela do banco tem o mesmo caminho desde Ago/2026.
# `--dataset all` migra coletas + pricetrack_daily de uma vez (rode mensalmente).
python scripts/history_cli.py tier --dataset pricetrack --confirm
python scripts/history_cli.py tier --dataset all --confirm

python scripts/history_cli.py export --start 2026-01-01 --end 2026-07-25 \
    -o reports/historico.csv
```

A ordem de segurança da migração é sempre a mesma: **lê do banco → grava a
partição → relê o Parquet para conferir a contagem → só então apaga**. Um dia
que não passa na releitura não é apagado.

**No dashboard:** `query_coletas()` e `query_pricetrack_daily()` completam com o
histórico os dias que o Supabase não devolveu — é isso que faz as duas bases
aparecerem numa série só. Partições de `coletas` ainda sem de-para (gravadas
pela coleta ou importadas de CSV) aparecem pelo interruptor **Filtros Globais →
"Incluir histórico do Drive sem de-para"**, ligado por padrão — sem ele o filtro
`estado_match = MAPEADO` esconderia todo o histórico recuperado.

Detalhes, credenciais e troubleshooting: **[`docs/HISTORICO_DRIVE.md`](docs/HISTORICO_DRIVE.md)**.

## 🐕 Watchdog — `.github/workflows/watchdog.yml`

Roda `scripts/daily_status_check.py` às 20:30 BRT e alerta no Telegram. Existe
porque entre 16 e 25/07/2026 a coleta rodou, os workflows ficaram **verdes** e
nada foi gravado — o watchdog não olha se o job terminou bem, olha se o **dado
chegou**. Secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `TELEGRAM_BOT_TOKEN`,
`N8N_TELEGRAM_CHAT_ID`.

---

## 🛠️ Manutenção do Banco (Supabase)

### ⚠️ Cota — o limite estrutural do plano free

Medição de **08/08/2026** (org `Mydea`, plano **free**, limite 500 MB), após a
poda do intra-dia antigo do PriceTrack + `VACUUM FULL`:

| Medida | Valor |
|--------|-------|
| `pg_database_size` | **393 MB** (78,7% do teto) — era 486 MB antes da poda |
| `coletas` | 209 MB · 237.255 linhas · 26/06 → 06/08 (25 dias com dado) |
| `pricetrack_daily` | ~130 MB · 324.646 linhas (só `Diário` + intra-dia ≤30d) |
| `rac_monitoramento` (legado) | 33 MB · 38.509 linhas |
| Crescimento observado | ≈ **19 MB/dia** somando as duas tabelas quentes |

> **A conta que decide a operação:** a ~19 MB/dia, os 107 MB livres duram
> poucos dias. O plano free só se sustenta com a **migração rodando** —
> `history_cli.py tier --dataset all --confirm`, mensal ou quando a cota
> apertar. Sem ela o 402 volta. As alternativas de fundo (Supabase Pro a
> US$ 25/mês, que compra ~14 meses de janela quente) estão comparadas com
> custo e esforço em
> [`docs/ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md`](docs/ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md).

Quando a API devolve 402, `utils/supabase_client.py` faz **fail-fast** com
`is_quota_restricted_error()` — aborta os lotes restantes, preserva o CSV local
e a automação Admin é pulada (as etapas falhariam igual). O dado do dia **não**
se perde: o histórico no Drive é gravado antes e não depende do banco.

⚠️ `docs/DB_RETENTION.md` descreve a política "Equilibrada" de 14/07 (todo o
histórico `Diário` + 90 dias de `coletas`) — **essa não é mais a realidade do
banco**: a poda emergencial levou o histórico anterior a 26/06, e desde
Ago/2026 o que sai da janela quente vai para o Drive em vez de ser apagado.

### Utilitários

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
| `docs/HISTORICO_DRIVE.md` | 🆕 Histórico frio em Parquet no Drive — setup, migração, relatórios |
| `docs/ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md` | Incidente de cota, agente no Chrome/n8n e onde guardar os dados — com as contas |
| `docs/COLETA_LOCAL_AUTENTICADA.md` | Coleta local Magalu+Shopee+CB — Chrome comum + perfil dedicado, agendamento |
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

## ✅ Validação Operacional — 08/08/2026 (volta do Supabase)

Checagem feita contra o projeto de produção (`RAC`, `sa-east-1`) antes de
retomar o Supabase como base principal.

**Verde:**

- ✅ **Projeto `ACTIVE_HEALTHY`, não restrito por cota.** A leitura e a escrita
  via API respondem normalmente — o 402 de 16/07 não está mais em vigor.
- ✅ **Espaço recuperado:** 486 MB → **393 MB** (78,7% do teto) pela poda do
  intra-dia do PriceTrack com mais de 30 dias (230.298 linhas) + `VACUUM FULL`.
  Todo o histórico `Diário` — a fonte de verdade de preço — foi preservado.
- ✅ **Nenhum dado perdido no buraco de 17/07 → 02/08.** Esses 17 dias não
  estão no Supabase, mas **estão no Drive**: as partições existem e o
  dashboard já as costura. Não é preciso resgatar artifact nenhum.
- ✅ **Histórico frio íntegro**, cobrindo de 01/06/2026 a 07/08/2026 em
  `coletas/`, mais o espelho de CSV cru em `csv_coletas/`.
- ✅ **777 testes passando**, incluindo os 37 novos da costura do PriceTrack
  (25) e da escrita dupla do import (12).

**Vermelho — resolver antes de confiar no Actions:**

- ❌ **`SUPABASE_KEY` do GitHub inválida.** Todas as execuções do `collect.yml`
  desde 04/08 (8 seguidas) falham com HTTP 401 `Unregistered API key`. A chave
  legada do projeto continua válida, então o problema é o **secret**, não o
  banco. O PC coletor tem a chave certa no `.env` — foi ele quem gravou os
  dias 04–06/08.
- ❌ **Alertas mudos.** `TELEGRAM_BOT_TOKEN` e `N8N_TELEGRAM_CHAT_ID` estão
  vazios no repositório: as 8 falhas não notificaram ninguém.
- ⚠️ **`pricetrack_daily` parada desde 16/07** — o import diário não roda há
  três semanas. Sem ele o preço de referência do dashboard congela.
- ⚠️ **Migração ainda não executada.** A `tier` precisa rodar de uma máquina
  com as credenciais `GDRIVE_*` (o PC coletor). Sem ela, os 107 MB livres
  duram poucos dias no ritmo atual.

---

## ✅ Validação Operacional — 25/07/2026

- ✅ **Histórico frio em Parquet (Drive) implementado** 🆕 — `utils/history/`,
  `scripts/history_cli.py` e `scripts/gdrive_setup.py`. Escrita dupla e
  independente do Supabase, leitura costurada no `query_coletas()`, migração
  verificada antes de apagar (`tier --confirm`) e janela quente de 15 dias.
  46 testes novos. Ver `docs/HISTORICO_DRIVE.md`.

**Repositório validado nesta data:** árvore limpa, **638 testes passando**
(`tests/` + `pricetrack_api/tests` + `pricetrack_importer/tests`), workflows
agendados executando (`collect.yml` 2×/dia, `pricetrack_daily.yml`).

> Nota (08/08/2026): o `pricetrack_intraday.yml` citado aqui foi aposentado —
> ver a seção do PriceTrack.

- 🚨 **Gravação bloqueada desde 16/07** — Supabase em `exceed_db_size_quota`
  (HTTP 402). A coleta de 25/07 produziu 5.651 registros e **0 foram gravados**.
  O workflow terminou **verde**: `scripts/daily_status_check.py` existe e
  notifica no Telegram, mas **não está agendado em nenhum workflow** — por isso
  9 dias de falha passaram despercebidos. Ver
  `docs/ARQUITETURA_ARMAZENAMENTO_E_AGENTES.md`.
- ⚠️ **Casas Bahia em 0 na VM/GitHub** — circuit breaker do Akamai após 3
  keywords bloqueadas (IP de datacenter). Caminho que funciona:
  `RAC_LOCAL_CHROME=1` no notebook (IP residencial) ou proxy residencial BR.
- ✅ **Mercado Livre — extração ancorada no DOM real do card** (fixture de
  produção `tests/fixtures/ml_card_grid_20260725.html`): corrigidos campos
  ocos, paginação sobreposta, Loja Oficial em 86%, vitrine da loja virando URL
  do produto, e warm-up da home contra o gate transitório (2/3 das keywords
  retornavam 0)
- ✅ **Leroy Merlin — seller 3P resolvido via PDP** com cache persistente
  (`data/leroy_sellers.json`): App Router + espera de hidratação, detecção de
  challenge varrendo o HTML inteiro, quarentena transitória com 1 tentativa por
  seller/run e ritmo entre PDPs. Diagnóstico: `scripts/leroy_seller_probe.py --scan`
- ✅ **Shopee — parser realinhado ao formato `search_items` de Jul/2026**:
  extração de `name`/`price`, tratamento de 0 vendas, posição contígua entre
  páginas via `start_offset` acumulado, `shop_location` deixa de ser usado como
  seller
- ✅ **Guard de cota no upload** (`is_quota_restricted_error`) — fail-fast com
  mensagem acionável no 402, sem tentar os lotes restantes; automação Admin
  pulada no mesmo estado
- ✅ **Política de retenção do banco** (`scripts/retention_cleanup.sql` +
  `docs/DB_RETENTION.md`) — re-executável, com `VACUUM FULL` obrigatório
- ✅ **Keywords rebalanceadas** — correção de viés de marca + queries
  conversacionais (IA)
- ✅ **Dashboard** — toggle de share neutro por marca nas páginas de share;
  buy box/reputação/patrocinado passam a considerar todos os marketplaces

---

## ✅ Validação Operacional — 12/07/2026

- ✅ **Agendamento local Windows corrigido de vez** — a Action das tarefas
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

**Versão:** 4.6 | **Última atualização:** 25 de Julho de 2026 | @ederrabelo
