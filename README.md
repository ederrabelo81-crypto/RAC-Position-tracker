# RAC Position Tracker — Retail Analytics & Competitive Intelligence

Monitoramento diário de **buy box, sellers e posicionamento** de ar
condicionado nos marketplaces e dealers brasileiros, com preço consolidado via
**PriceTrack** e painéis Streamlit para a indústria (Midea Carrier) e para o
lojista.

**Status:** ✅ Produção | **Versão:** v5.8 | **Atualizado em:** 07/11/2026

> O histórico de mudanças, incidentes e validações operacionais até
> Set/2026 foi preservado em
> [`docs/archive/README_HISTORICO_ATE_SET2026.md`](docs/archive/README_HISTORICO_ATE_SET2026.md).
> Este README descreve **o estado atual** do projeto.

---

## Sumário

1. [O que o projeto faz](#-o-que-o-projeto-faz)
2. [Como a coleta roda hoje](#-como-a-coleta-roda-hoje)
3. [Plataformas](#-plataformas)
4. [Instalação](#-instalação)
5. [Uso — coleta de oferta/posição](#-uso--coleta-de-ofertaposição)
6. [Mais Vendidos (bestsellers)](#-mais-vendidos-bestsellers)
7. [PriceTrack — preço de referência](#-pricetrack--preço-de-referência)
8. [Painéis](#-painéis)
9. [Armazenamento: Supabase + Parquet no Drive](#-armazenamento-supabase--parquet-no-drive)
10. [Confiabilidade: quem rodou, quem não rodou](#-confiabilidade-quem-rodou-quem-não-rodou)
11. [GitHub Actions](#-github-actions)
12. [Saída (CSV)](#-saída-csv)
13. [Testes](#-testes)
14. [Estrutura do repositório](#-estrutura-do-repositório)
15. [Troubleshooting](#-troubleshooting)
16. [Documentação](#-documentação)

---

## 🎯 O que o projeto faz

O protagonista da coleta é a **competição por buy box e a inteligência de
sellers**; preço é campo secundário (o preço "oficial" vem do PriceTrack).
Para cada busca relevante de ar condicionado, em cada plataforma, três vezes
por dia, o projeto registra:

- **Buy box & sellers** — quem vence a oferta (`Buy Box Seller`), quantos
  disputam (`Qtd Sellers`), 1P/3P/Loja Oficial (`Tipo Seller`), reputação.
- **Posicionamento** — posição orgânica × patrocinada (share of voice de mídia).
- **Avaliações** — nota e volume de reviews.
- **Preço de vitrine** — secundário; a série de preço de referência é do PriceTrack.
- **Mais Vendidos** — a única variável de *resultado* (ranking ordinal) no nível do SKU.
- **Diagnóstico de preço e sortimento** — reprodução da aba Diagnóstico do
  PriceTrack, só ar-condicionado, padrão Split Hi-Wall Inverter.
- **Clima × vendas** — temperatura por região contra o proxy de venda dos Mais Vendidos.

Fluxo de dados:

```
config.py (keywords, plataformas, marcas)
   ↓
main.py  →  scrapers/*.py  →  _build_record()  (marca, seller canônico, offer_key)
   ↓
CSV (output/)  ─┬─► Parquet do histórico frio (Google Drive com GDRIVE_*; sem isso, disco local)
                └─► Supabase `coletas`        (janela quente de 2 dias)
   ↓
heartbeat (pipeline_heartbeat) → supervisor → Telegram
   ↓
app.py (🏠 Cockpit do Trade + 22 páginas) · seller_app/ (lojista) · pricetrack_dashboard/
```

---

## 🏗️ Como a coleta roda hoje

| Executor | O que roda | Quando (BRT) |
|----------|-----------|--------------|
| **PC coletor Windows** (IP residencial) | Oferta/posição de **todas as plataformas exceto Amazon** + 33 dealers | 08:00 (Abertura), 14:00 (Tarde), 20:00 (Fechamento) |
| **PC coletor** — estágios pós-coleta | Materialização do `seller_offer_daily` (todo turno) e poda Supabase → Drive (só à noite) | após cada turno |
| **GitHub Actions** — `collect_amazon_sellers.yml` | **Amazon-only**, abrindo o PDP de cada item para ler a buy box | 08:00 / 14:00 / 20:00 (+ resgate às :40) |
| **GitHub Actions** — `pricetrack_daily.yml` | Import do PriceTrack D-1 (+ subcategoria do produto) e auto-heal | 00:01, 04:20, 05:20 (escada de tentativas) — o cron do Actions chega a atrasar ~7 h |
| **GitHub Actions** — `clima_daily.yml` | Radar meteorológico (Open-Meteo, 17 cidades) → `clima_diario` | 05:47 |
| **GitHub Actions** — `pipeline_guard.yml` / `watchdog.yml` | Supervisor de execução / validação de dado | 06:35, 12:35, 22:35 / 20:30 |
| **PC coletor** — `RAC_Briefing_0700` | Briefing diário (Claude Code headless → Notion + painel estático) | 07:00 |
| Oracle VM | ⏸️ standby — não coleta | — |

**As três tarefas do PC** (`RAC_Local_Manha`, `RAC_Local_Tarde`,
`RAC_Local_Noite`) rodam a **mesma varredura completa**: todas as keywords,
2 páginas, plataformas `ml magalu shopee casasbahia google_shopping leroy
dealers`. Elas se distinguem só pelo turno gravado. Cada uma tem catch-up no
logon dentro da sua janela (8–11h / 12–17h / 18–23h), faz `git pull` e
atualiza as dependências antes de coletar.

**Por que a Amazon roda na nuvem:** a buy box da Amazon só existe no PDP (um
acesso por item), o que é caro e lento; isolada no Actions, deixa o PC leve.
O turno é cravado por `RAC_TURNO` (o cron do Actions atrasa) e o modo
`RAC_AMAZON_PDP_FRESH=1` lê o vendedor do dia, sem cache.

**Regra do contrato:** cada plataforma tem **um dono por turno** em
`utils/pipeline_registry.py` (`JOBS`) — o teste `tests/test_pipeline_registry.py`
reprova órfã ou dono duplicado. Mapa completo: [`docs/MAPA_COLETAS.md`](docs/MAPA_COLETAS.md).

---

## 🌐 Plataformas

| Plataforma | Status | Executor | Como coleta |
|------------|--------|----------|-------------|
| Mercado Livre | ✅ | PC | Browser com perfil Chrome dedicado; buy box + Loja Oficial. Gate de login detectado por evidência, freio após 4 páginas gateadas e repescagem no fim do turno. Fallback automático para a API oficial (`ML_APP_ID`/`ML_APP_SECRET`). Sessão expirada: **relogin automático** uma vez por turno com `ML_EMAIL`/`ML_PASSWORD` (`utils/ml_login.py`; `RAC_ML_AUTO_RELOGIN=0` desliga) — desfecho no heartbeat (`sessão: RENOVADA no turno` ou `EXPIRADA (…)`) |
| Amazon | ✅ | Actions | SERP + PDP de cada item (buy box), `Qtd Sellers` de "X ofertas", 1P vs 3P |
| Magalu | ✅ | PC | Chrome local via CDP (`RAC_LOCAL_CHROME=1`) com auto-recuperação → fallback `curl_cffi`; parsers `__NEXT_DATA__` → RSC → DOM |
| Casas Bahia | ✅ | PC | VTEX Intelligent Search + warm-up Akamai; `sellers[]` → buy box |
| Leroy Merlin | ✅ | PC | API Algolia; seller 3P resolvido no PDP (cache `data/leroy_sellers.json`) e buy box por produto no PDP. Preço lido do JSON-LD do PDP (`utils/leroy_price.py`), não da média do índice; com `RAC_LOCAL_CHROME=1` os PDPs abrem no Chrome real |
| Google Shopping | ⚠️ | PC | reCAPTCHA no headless; com `RAC_LOCAL_CHROME=1` usa o Chrome real logado |
| Shopee | 🟡 | PC | API v4 + sessão do perfil logado; best-effort sem proxy BR |
| Dealers (33 ativos) | ✅ | PC | `DealerScraper` + `DEALER_CONFIGS` (VTEX, WooCommerce, custom); 3 em `on_hold` |
| Fast Shop | ⏸️ | — | Bloqueio total PerimeterX |

Bloqueios de Shopee/Casas Bahia vêm do IP de datacenter — por isso a coleta
oficial roda do IP residencial do PC coletor.

---

## 🚀 Instalação

**Pré-requisitos:** Python 3.10+ (CI usa 3.11), Google Chrome (no PC
coletor), projeto Supabase. Opcionais: conta Google Drive (histórico frio),
chave PriceTrack, bot do Telegram, chave Anthropic.

```bash
git clone https://github.com/ederrabelo81-crypto/RAC-Position-tracker.git
cd RAC-Position-tracker

python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux/Mac

pip install -r requirements.txt
python -m playwright install chromium
```

No PC coletor Windows, `scripts\sync_windows.bat` faz o catch-up de repo +
dependências + Drive num comando (`scripts\ensure_deps.bat --force` só as
dependências).

### `.env`

Copie `.env.example` e preencha:

```env
# Supabase — obrigatório. Use a service_role: com a chave anon a coleta grava
# CSV e Parquet e deixa o banco para trás EM SILÊNCIO.
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_KEY=service_role_key

# Banco alternativo (rollback Aiven). Vazio = Supabase. Preenchido = Postgres direto.
RAC_DB_DSN=

# Janela quente no Supabase (padrão 2 dias)
RAC_HOT_WINDOW_DAYS=2
# Retenção própria dos fatos do seller_app no banco (padrão 62, piso 16)
RAC_SELLER_RETENTION_DAYS=62

# Histórico frio no Google Drive — gerado por scripts/gdrive_setup.py
GDRIVE_CLIENT_ID=...
GDRIVE_CLIENT_SECRET=...
GDRIVE_REFRESH_TOKEN=...
GDRIVE_FOLDER_ID=...

# PriceTrack (import diário de preço)
PRICETRACK_API_KEY=...

# Telegram (alertas e resumos)
TELEGRAM_BOT_TOKEN=...
N8N_TELEGRAM_CHAT_ID=...

# Mercado Livre — API oficial (fallback automático do gate) e login do perfil
# (com RAC_LOCAL_CHROME=1 — perfil persistente — ML_EMAIL/ML_PASSWORD também ligam
#  o relogin automático do turno quando a sessão expira)
ML_APP_ID=...
ML_APP_SECRET=...
ML_EMAIL=...
ML_PASSWORD=...

# Chrome real logado via CDP (PC coletor)
RAC_LOCAL_CHROME=1

# Opcionais
ANTHROPIC_API_KEY=sk-ant-...        # camada LLM da automação ADMIN
ANALYST_NAME="Bot Automático Python"
```

### Setup do PC coletor (uma vez)

```powershell
# Login nos sites no perfil Chrome dedicado (data\chrome_profile)
python scripts\setup_local_profile.py                       # Shopee
python scripts\setup_local_profile.py --site mercadolivre
python scripts\setup_local_profile.py --site magalu

# Drive do histórico frio
python scripts\gdrive_setup.py --client-secrets CAMINHO\client_secret.json
python scripts\gdrive_setup.py --check

# Agenda as 3 tarefas de coleta (Admin; auto-eleva via UAC)
PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1

# Agenda o briefing das 07:00 (ver docs/BRIEFING_LOCAL_SETUP.md)
PowerShell -ExecutionPolicy Bypass -File scripts\setup_briefing_scheduler.ps1

# Diagnóstico (sem Admin): tarefas, último log, chave service_role
PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1
```

Detalhes: [`docs/COLETA_LOCAL_AUTENTICADA.md`](docs/COLETA_LOCAL_AUTENTICADA.md).

---

## 📖 Uso — coleta de oferta/posição

```bash
python main.py                                     # demo fixa: Mercado Livre, 1 keyword, 1 página
python main.py --pages 2                           # plataformas ON em ACTIVE_PLATFORMS (config.py)
python main.py --platforms all --pages 2           # TODO o registry exceto ml_api — inclui Fast Shop (OFF/bloqueada)
python main.py --platforms ml magalu --pages 1
python main.py --platforms dealers --pages 1 --no-headless   # browser visível (debug)
python main.py --platforms ml --keywords "ar condicionado inverter 12000"
python main.py --platforms ml_api --pages 1        # complemento de reputação via API oficial (opt-in)

# Amazon como roda no Actions (buy box do dia, sem cache)
RAC_TURNO=Abertura RAC_AMAZON_PDP_FRESH=1 python main.py --platforms amazon --pages 2

# Disparo manual idêntico ao agendado (Windows)
scripts\run_local_scheduled.bat manha              # manha | tarde | noite
```

| Opção | Valores | Padrão |
|-------|---------|--------|
| `--platforms` | `ml`, `ml_api`, `amazon`, `magalu`, `casasbahia`, `google_shopping`, `leroy`, `shopee`, `fast`, `dealers`, `all` (= todas exceto `ml_api`, **ignora** `ACTIVE_PLATFORMS`) | `ACTIVE_PLATFORMS` em `config.py` (sem nenhum argumento, roda a demo) |
| `--pages` | páginas por keyword | `3` (`MAX_PAGES`); o agendado usa `2` |
| `--keywords` | termos customizados | `KEYWORDS_LIST` (40 keywords) |
| `--priority` | `alta`, `media`, `baixa` | todas |
| `--headless` / `--no-headless` | | headless |
| `--output-dir` | | `output/` |
| `--debug-hits N` | salva N hits Algolia brutos (Leroy) | — |

**Variáveis úteis:** `RAC_TURNO` (crava o turno), `RAC_LOCAL_CHROME=1`
(Chrome real), `RAC_RETRY_PASS=0` / `RAC_RETRY_COOLDOWN_MIN` (repescagem do
ML), `LEROY_BUYBOX_PDP=0` (desliga PDP da Leroy), `RAC_JOB_ID` (exportado pelo
lançador para a batida de ponto).

**Onde mexer:** keywords/plataformas/marcas/delays em `config.py`; dealers em
`scrapers/dealers.py` (`DEALER_CONFIGS`); colunas em `main.py`
(`COLUMN_ORDER`); preço em `utils/text.py`; marca em `utils/brands.py`; nome
canônico de seller em `utils/seller_names.py` (`SELLER_GROUPS`).

---

## 🥇 Mais Vendidos (bestsellers)

As listas "Mais Vendidos" dos varejistas são a **única variável de resultado**
no nível do SKU. Módulo `bestsellers/`: 6 fontes artesanais (Amazon, ML,
Magalu, Casas Bahia, Shopee, Leroy) + 14 dealers via coletores genéricos
(VTEX e HTML/JSON-LD), cada lista marcada com `referencia` = `mais_vendidos`
ou `relevancia` (proxy — **as duas nunca se misturam**).

```bash
python scripts/collect_bestsellers.py                           # coleta do dia + brief
python scripts/collect_bestsellers.py --plataformas dufrio webcontinental
python scripts/collect_bestsellers.py --relatorio semanal        # evolução (não coleta)
python scripts/collect_bestsellers.py --relatorio mensal --ultimos 6
python scripts/collect_bestsellers.py --importar-xlsx ./uploads --data 2026-08-10   # backfill (diretório de .xlsx + data)
```

- **Status:** coleta e grava no Supabase (tabela `bestsellers`) quase todo
  dia, rodada no PC coletor (`scripts\collect_bestsellers.bat`). O job
  `local_bestsellers` **já está** no `pipeline_registry.py` (dias úteis, 09:30
  BRT), então um dia sem coleta vira alerta; **a tarefa `RAC_Bestsellers` não
  está no Task Scheduler** — a execução ainda depende de disparo manual.
- **Fontes mudas:** 7 das 20 nunca gravaram (Casas Bahia + 6 sites próprios).
  Só **Casas Bahia** é prioridade (marketplace).
- **Amazon:** o PDP de cada item do ranking é aberto a cada execução
  (`RAC_BESTSELLERS_AMAZON_PDP=0` desliga).
- **Cobertura fonte a fonte** (`bestsellers/cobertura.py`): lista fora de ≥ 2
  dias de coleta seguidos — em dias em que outras fontes gravaram — vira alerta
  no `pipeline_watch.py`. Fontes que **nunca** gravaram (ex.: Casas Bahia) não
  geram esse alerta: ficam só no relatório (`nunca_coletaram`).
- **📡 Radar de marcas** (`bestsellers/radar.py`): share de topo de **todas** as
  marcas, por plataforma, nos últimos 7 dias contra os 28 anteriores; status
  SUBINDO / ATENÇÃO / MISTO / CAINDO. Aba 📡 Radar na página 🥇 Mais Vendidos e
  `python scripts/collect_bestsellers.py --relatorio radar [--notificar]`.
- **Regra dura:** ranking é ORDINAL — nunca soma entre plataformas, nunca vira
  share de mercado. KPI: % Midea no top 10 por plataforma + delta.

Detalhes: [`docs/BESTSELLERS.md`](docs/BESTSELLERS.md).

---

## 💰 PriceTrack — preço de referência

Import diário do export da API PriceTrack → tabela `pricetrack_daily` (preços
min/avg/mode/max por data, turno, marca, SKU, marketplace, seller).

```bash
python scripts/pricetrack_api_import.py                       # D-1
python scripts/pricetrack_api_import.py --gaps-only           # auto-heal dos últimos 14 dias
python scripts/pricetrack_api_import.py --force --start 2026-07-28 --end 2026-09-01
python -m pricetrack_api exports                              # quem segura os 3 slots de export (429)
python scripts/pricetrack_audit.py                            # coletas Python × PriceTrack (ontem)
python scripts/pricetrack_capacity_audit.py --desde 2026-10-23 --ate 2026-10-23   # cobertura 9K/12K (sku+title)
python -m pricetrack_importer arquivo.md                      # import manual md/xlsx
python scripts/pricetrack_discovery.py                        # descoberta só leitura do export (ontem, V2)
```

- **Base de preço explícita (`price_basis`):** `best_cash` = menor entre à
  vista e PIX, só ofertas `AVAILABLE`. Preço a prazo nunca tapa buraco.
- **Reimport interrompível:** o id do export fica em
  `imports/pricetrack/api/exports_state.json` e a execução seguinte adota o
  export em voo em vez de duplicá-lo.
- **Subcategoria do produto** (migração `007_pricetrack_subcategory.sql`): o
  import grava `subcategory` (HI-WALL, CASSETE, TETO, JANELA, PORTÁTIL, PAREDE,
  DUTO) e `family`. O nome do produto **não** identifica Hi-Wall — o AI
  Ecomaster da Midea se chama só "SPLIT" —, por isso o campo vem da API.
- **Auto-heal sem reimportar o que foi podado:** o `--gaps-only` (05:20) não
  trata como buraco um dia fora da janela quente que já está no Parquet do
  Drive; se o Drive estiver ilegível, cura só a janela quente. O Supabase é o
  recurso crítico — antes disso o heal devolvia ao banco ~12 dias por manhã.
- **Descoberta do export** (só leitura): `python scripts/pricetrack_discovery.py`
  (precisa de `PRICETRACK_API_KEY`) ou o workflow manual
  **PriceTrack — Descoberta do export** (relatório no resumo do run). Mostra
  colunas, marketplaces, categorias/subcategorias e ofertas por marketplace.
- ⏳ **Pendente:** reimport dos 36 dias com base antiga (roda no PC coletor).
- **Série de preço = uma fonte:** um gráfico nunca costura PriceTrack e
  coletas na mesma linha (`utils/price_series.py`).
- **Auditoria:** as coletas Python servem de auditoria do PriceTrack; só
  anúncio de UNIDADE entra na comparação (`utils/offer_kind.py`).

Detalhes: [`pricetrack_api/README.md`](pricetrack_api/README.md),
[`docs/PRICETRACK_FIDELIDADE.md`](docs/PRICETRACK_FIDELIDADE.md),
[`docs/PRICETRACK_INSIGHTS.md`](docs/PRICETRACK_INSIGHTS.md).

---

## 📊 Painéis

### `app.py` — dashboard interno (indústria)

```bash
pip install -r requirements_app.txt   # ou requirements.txt completo
streamlit run app.py
```

Home **🏠 Cockpit do Trade** (share de prateleira da Midea Carrier nas buscas
genéricas, batalha por keyword, defesa/conquista de marca, buy box das ofertas
Midea, preço de vitrine por BTU, alertas e cobertura) + 22 páginas:

- **INSIGHTS:** 📅 Daily Price Vision · 🚨 Top Movers · 📊 Results · 📈 Price
  Evolution · 📊 Market Analytics · 💰 Preços 9K/12K · 🩻 Diagnóstico ·
  🥇 Mais Vendidos · 🌡️ Clima × Vendas · 🗂️ Ficha do Produto · 🏆 BuyBox
  Position · 👑 Share of Buy Box · ⭐ Reputação & Avaliações · 📣 SoV
  Patrocinado · 🛡️ Price Compliance · 📦 Availability
- **OPERAÇÕES:** 📧 Email Digest · 🔔 Price Anomalies · 📂 Import History · 🩺 Data Health
- **ADMIN:** 🤖 Automação (limpeza/normalização/de-para sem interação) · 🧬 Família & SKU

Os filtros globais (Período, Canal, Plataformas, Marcas) valem para **todas**
as páginas. Definições: [`docs/COCKPIT_TRADE.md`](docs/COCKPIT_TRADE.md) e
[`docs/DASHBOARD_FILTERS.md`](docs/DASHBOARD_FILTERS.md).

#### 🩻 Diagnóstico (Nov/2026)

Reprodução da aba **Diagnóstico** do PriceTrack, **só ar-condicionado**, com
recorte padrão **Split Hi-Wall Inverter até 30.000 BTU** (outras subcategorias
de AC só por escolha no filtro; o teto de 30.000 BTU vale sempre).

- **Fonte:** histórico Parquet de `pricetrack_daily` no Drive, turno Diário —
  o Supabase guarda só 2 dias. Período = filtro global da barra lateral.
- **Filtros:** marketplace foco (padrão Casas Bahia), marca foco (padrão
  Midea), subcategoria, tecnologia, BTU, ciclo, produtos × marca. Dos filtros
  globais, **só o Período** vale aqui: Plataformas e Marcas globais não se
  aplicam, porque o painel compara o foco com o mercado inteiro.
- **Blocos:** ofertas foco × mercado (top 10 + foco) · anúncios por marca
  (Qtd./%) · preço mínimo por marca · profundidade vs. mediana de mercado ·
  produtos mais agressivos (com sparkline) · comparador · export CSV/XLSX.
  Clique num ponto abre os anúncios daquele dia.
- **Grupo Midea Carrier = MIDEA:** `MIDEA CARRIER`, `SPRINGER CARRIER MIDEA`,
  `SPRINGER MIDEA`, `CARRIER` e `SPRINGER` aparecem como uma marca só; a grafia
  do PriceTrack segue em "marca no PriceTrack" (drill-down e export).
- **Mediana de mercado:** por padrão sem o foco e sem lojas próprias de
  fabricante (MIDEA, LG, SAMSUNG…), configurável na página.
- **Cache:** o cabeçalho mostra `regras vN` (`utils.diagnostico.FRAME_VERSAO`);
  mudou a regra, mudou a versão, e o cache se renova.

Código: `utils/diagnostico.py` (contas), `utils/ac_attributes.py` (BTU,
tecnologia, ciclo, voltagem, escopo, marca), `pricetrack_dashboard/diagnostico_page.py`
(página). Fórmulas: [`docs/diagnostico.md`](docs/diagnostico.md); descoberta e
decisões: [`docs/diagnostico_fase0.md`](docs/diagnostico_fase0.md).

#### 🌡️ Clima × Vendas (Out/2026)

Temperatura por região (17 cidades, Open-Meteo) e anomalia contra a normal dos
anos anteriores, cruzada com o proxy de venda dos Mais Vendidos (Spearman com
defasagem de 0–14 dias). Correlação fraca (< 60 pontos ou |r| < 0,3) sai como
hipótese, não regra.

```bash
python scripts/clima_collect.py                  # últimos 10 dias + previsão
python scripts/clima_collect.py --backfill       # ERA5 desde 2016 (uma vez; sem ele não há normal)
python scripts/clima_collect.py --correlacao [--vendas-csv gfk.csv]
```

### `seller_app/` — Track Position Seller (painel do lojista)

App separado e **só-leitura** (chave `anon` + RLS): "como EU estou indo contra
quem vende a mesma coisa que eu?". Lê `seller_offer_daily`,
`seller_coverage_daily` e `v_seller_buybox_share` (migrações 016–018, 020),
materializadas pelo estágio C de cada turno do PC (job `local_seller_fact`).

```bash
python scripts/build_seller_offer_daily.py                 # ontem e hoje (requer service_role)
python scripts/build_seller_offer_daily.py --desde 2026-08-28

# O painel lê .streamlit/secrets.toml com SUPABASE_URL e SUPABASE_ANON_KEY
# (chave anon — NUNCA a service_role); SELLER é opcional. Ver seller_app/README.md.
pip install -r seller_app/requirements.txt
streamlit run seller_app/app.py
```

5 abas: Share de buy box · Ganhos e perdas · Marcas e posição · Ranking na
plataforma · Cobertura. Secret `SELLER` (opcional) trava a instância num
lojista e é comparado **literalmente** com o nome canônico. Deploy:
[`seller_app/README.md`](seller_app/README.md); estratégia:
[`docs/TRACK_POSITION_SELLER.md`](docs/TRACK_POSITION_SELLER.md).

### `pricetrack_dashboard/` — preços 9K/12K por tier

```bash
streamlit run pricetrack_dashboard/app.py
```

Ver [`pricetrack_dashboard/README.md`](pricetrack_dashboard/README.md).

---

## 🗄️ Armazenamento: Supabase + Parquet no Drive

- **Supabase** (free tier, 500 MB) guarda só a **janela quente**
  (`RAC_HOT_WINDOW_DAYS`, padrão **2 dias**): `coletas`, `pricetrack_daily`,
  `bestsellers`, `pipeline_heartbeat`, tabelas do `seller_app` e de-para.
- **Parquet do histórico frio** guarda todo o histórico. É gravado **antes** do
  banco e não depende dele — um dia sobrevive a banco fora do ar ou restrito
  por cota. Vai para o **Google Drive só com `GDRIVE_*` configurado**
  (`RAC_HISTORY_BACKEND=auto`, o padrão); sem isso, ou com o Drive
  falhando, cai para **disco local** — e aí não há cópia fora da máquina. O
  log avisa (`[Histórico] … → local:`) e `python scripts/gdrive_setup.py --check`
  confirma.
- **Leitura costurada:** `query_coletas()` e `query_pricetrack_daily()` leem o
  Supabase e completam com o Drive os dias que faltam.
- **Poda automática:** toda noite o estágio D roda
  `history_cli.py tier --dataset all --confirm --heartbeat` (lê → grava
  Parquet → relê e confere → só então apaga). Job `local_tier_migration`.
  Os fatos do `seller_app` têm retenção **própria** de 62 dias
  (`RAC_SELLER_RETENTION_DAYS`), e o estágio termina com a RPC
  `compactar_indices` (migração 022): `DELETE` não devolve disco nem encolhe
  índice.
- **Import do PriceTrack no Actions** grava o dia também no Parquet do Drive
  quando os secrets `GDRIVE_*` estão configurados e o Drive responde (o
  workflow instala `pyarrow` e `google-api-python-client`); aí o painel
  🩻 Diagnóstico vê D-1 sem esperar a poda. Sem Drive, a cópia cai no disco do
  runner, que é descartado — o dia só chega ao Parquet pela poda noturna.
- **Banco intercambiável:** `utils/db.py` imita a API do `supabase-py` sobre
  psycopg2; `RAC_DB_DSN` preenchido aponta tudo para um Postgres direto (hoje
  só rollback para a Aiven).

```bash
python scripts/history_cli.py stats                         # dias, volume, buracos
python scripts/history_cli.py import-csv output/rac_monitoramento_*.csv --mirror
python scripts/history_cli.py tier --dataset all --confirm  # poda manual
python scripts/history_cli.py export --start 2026-01-01 --end 2026-07-25 -o reports/historico.csv
```

Cota estourada (HTTP 402 `exceed_db_size_quota`): siga
[`docs/RETORNO_SUPABASE.md`](docs/RETORNO_SUPABASE.md) §5.1. Mais em
[`docs/HISTORICO_DRIVE.md`](docs/HISTORICO_DRIVE.md).

---

## 🩺 Confiabilidade: quem rodou, quem não rodou

Cada executor grava início e fim em `pipeline_heartbeat` (migração 015), com
contagem por plataforma e as que vieram ZERADAS. **Ausência de batida até o
deadline = NÃO EXECUTOU** (o silêncio é que dispara o alarme). O supervisor
também marca plataforma **DEGRADADA** quando fica abaixo de 20% da mediana do
mesmo turno em ≥ 2 turnos do dia (`utils/volume_drop.py`).

```bash
python scripts/pipeline_watch.py                  # quem não bateu ponto hoje (+ Telegram)
python scripts/pipeline_watch.py --no-notify --json logs/pipeline_status.json
python scripts/briefing_gate.py --json            # o dado de D-1 está fresco?
python scripts/briefing_gate.py --curar           # tenta consertar antes de reprovar
python scripts/pipeline_heal.py --dry-run         # o que a contenção faria
python scripts/daily_status_check.py              # PASS/FAIL de dado por plataforma
```

Regras: um dono por plataforma por turno; cron do Actions é best effort;
coleta de marketplace nunca é auto-disparada; falha do livro-razão nunca
derruba a coleta (espelho em `logs/heartbeat.jsonl`).

---

## 🔄 GitHub Actions

| Workflow | Gatilho | Função |
|----------|---------|--------|
| `tests.yml` | todo PR + push em `main` | `pytest` (Python 3.11, sem secrets) |
| `collect_amazon_sellers.yml` | cron 3 turnos (+ resgate) + manual | Coleta Amazon-only com buy box do PDP |
| `pricetrack_daily.yml` | cron 00:01 / 04:20 / 05:20 BRT + manual | Import PriceTrack D-1 + auto-heal de 14 dias (sem reimportar dia já podado) |
| `pricetrack_discovery.yml` | **só manual** | Descoberta só leitura do export PriceTrack (relatório no resumo do run) |
| `clima_daily.yml` | cron 05:47 BRT + manual (`backfill=true`) | Radar meteorológico → `clima_diario` |
| `pipeline_guard.yml` | cron 06:35 / 12:35 / 22:35 BRT | Supervisor de execução (`pipeline_watch`, portão do briefing) |
| `watchdog.yml` | cron 20:30 BRT | Validação de dado (`daily_status_check.py`) |
| `collect.yml` | **só manual** | Backup de coleta (cron desligado desde Set/2026) |

O cron do Actions é *best effort* e tem atrasado horas (o degrau das 00:01
chegou a rodar às 07:39 BRT). Para não esperar, dispare à mão em Actions →
**Run workflow**. `pricetrack_daily.yml` e `pricetrack_discovery.yml` dividem o
grupo de concorrência `pricetrack-exports` (3 exports por organização na API):
não dispare a descoberta perto dos degraus do import.

Secrets usados: `SUPABASE_URL`, `SUPABASE_KEY` (service_role),
`PRICETRACK_API_KEY`, `TELEGRAM_BOT_TOKEN`, `N8N_TELEGRAM_CHAT_ID`, `GDRIVE_*`.

---

## 📄 Saída (CSV)

`output/rac_monitoramento_YYYYMMDD_HHMM.csv` — separador `;`, UTF-8 com BOM
(Excel PT-BR). O `main.py` grava ao lado um `<csv>.run_id`; `scripts/upload_csv.py`
e `scripts/history_cli.py` reusam esse `run_id` (sem gravação dupla).
`reenviar_csv.py` e `scripts/upload_magalu_csv.py` ainda geram um novo — reenviar
por eles duplica o turno.

```
Data; Turno; Horário; Analista; Plataforma; Tipo Plataforma;
Keyword Buscada; Categoria Keyword; Marca Monitorada; Produto / SKU;
Posição Orgânica; Posição Patrocinada; Posição Geral; Patrocinado?;
Buy Box Seller; Qtd Sellers; Tipo Seller; Reputação Seller;
Seller / Vendedor; Fulfillment?; Avaliação; Qtd Avaliações; Tag Destaque;
Preço (R$); URL Produto; Screenshot Busca; Screenshot Produto;
ID Produto Marketplace; ID Oferta Marketplace; ID Seller; URL Canônica; Offer Key
```

- `Turno`: `Abertura` (até 11h), `Tarde` (até 17h), `Fechamento` — sempre em
  BRT (`now_brt()`), ou cravado por `RAC_TURNO`.
- `ID Oferta Marketplace` só quando o marketplace expõe um (ML, Shopee) —
  nunca sintetizado. `Offer Key` é sempre preenchida, derivada e versionada
  (`v1|…`, `utils/offer_identity.py`).
- Seller é gravado no nome canônico (`utils/seller_names.py`).
- Linhas antigas do banco/Parquet podem estar duplicadas por turno (até
  30/09/2026): quem lê deduplica (`utils.shelf_insights.dedup_snapshot`).

Logs: `logs/bot_*.log`, `logs/heartbeat.jsonl`, HTML de debug em
`logs/*_debug_*.html` / `logs/ml_gate_*.html`.

---

## 🧪 Testes

```bash
pip install pytest
pytest tests/ pricetrack_api/tests pricetrack_importer/tests pricetrack_dashboard/tests -q
```

Validado em 07/11/2026: **2.774 passaram, 62 pulados** (~80 s). A suíte é
hermética — nenhum teste abre browser e os de integração se pulam sem
`SUPABASE_URL`. Antes de mexer num scraper, rode também a plataforma com
`--no-headless` e `python scripts/smoke_test.py` (1 keyword × 1 página por
plataforma).

---

## 📁 Estrutura do repositório

```
main.py                 CLI de coleta (registry de scrapers, CSV, Drive, Supabase, heartbeat)
config.py               keywords, plataformas ativas, marcas, delays
app.py                  dashboard interno (Cockpit + 22 páginas)
scrapers/               um scraper por plataforma + base.py, local_browser.py, playwright_runtime.py, dealers.py
bestsellers/            Mais Vendidos (sources artesanais + vtex_generic/html_generic, radar, cobertura)
seller_app/             Track Position Seller (app separado, só-leitura)
pricetrack_api/         cliente tipado da API PriceTrack
pricetrack_importer/    importador md/xlsx do PriceTrack
pricetrack_dashboard/   dashboard de preço 9K/12K + página 🩻 Diagnóstico (diagnostico_page.py)
utils/                  text, brands, seller_names, offer_identity, offer_kind, shelf_insights,
                        price_series, keyword_taxonomy, pipeline_registry, volume_drop, db, history/,
                        diagnostico, ac_attributes, clima, leroy_price, ml_login …
scripts/                agendamento Windows (.bat/.ps1), supervisão, PriceTrack, histórico, diagnóstico
migrations/             SQL do PriceTrack (001–007)
docs/migrations/        SQL de coletas/bestsellers/heartbeat/seller/clima (001–022)
.github/workflows/      tests, collect_amazon_sellers, pricetrack_daily, pricetrack_discovery,
                        clima_daily, pipeline_guard, watchdog, collect
magalu_shopee/          sub-projeto Node/TS (fallback Shopee)
docs/                   documentação técnica (índice em docs/INDEX.md)
.claude/                guias para sessões de IA (erros comuns, quick start, mapa)
```

Arquivos na raiz como `CORRECAO_CASAS_BAHIA.md`, `DIAGNOSTICO_GOOGLE_LEROY.md`,
`DEALERS_RELATORIO_EXECUTIVO.md` e os scripts `teste.py`, `cleanup_supabase.py`,
`normalize_supabase.py` são registros/utilitários antigos — mantidos por
referência, fora do fluxo atual.

---

## 🐛 Troubleshooting

| Problema | Solução |
|----------|---------|
| Coleta agendada não rodou | `PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1`; depois `python scripts/pipeline_watch.py` |
| CSV gerado, banco sem dado | `SUPABASE_KEY` é `anon`? Troque pela `service_role` |
| HTTP 402 `exceed_db_size_quota` | Banco no teto: `docs/RETORNO_SUPABASE.md` §5.1; depois `python scripts/upload_csv.py <csv>` |
| ML 0 linhas / gate de login | `python scripts/setup_local_profile.py --site mercadolivre`; HTML em `logs/ml_gate_*.html`. Teste manual com `$env:RAC_LOCAL_CHROME="1"` |
| Magalu bloqueado / muro de login | `python scripts/setup_local_profile.py --site magalu`; ver `docs/cdp_magalu_collection.md` |
| Shopee 403 em tudo | Sessão vencida: `python utils/session_grabber.py --site shopee` ou rode com `RAC_LOCAL_CHROME=1` |
| `Target page, context or browser has been closed` | Janela do Chrome foi fechada; o LocalBrowser reconecta — se persistir, reabra o perfil |
| `Sync API inside the asyncio loop` | Use `scrapers/playwright_runtime.acquire()/release()`, nunca um 2º `sync_playwright()` |
| PriceTrack preso em 429 | `python -m pricetrack_api exports` (o import adota exports órfãos) |
| Log diz `[Histórico] … → local:` | `.env` sem `GDRIVE_*`: `python scripts/gdrive_setup.py --client-secrets CAMINHO.json` |
| Dealer com 0 produtos | `logs/dealer_debug_<nome>_p1.html` + `--no-headless` |
| Turno errado | `python scripts/fix_turno.py --confirm` |
| `seller_app` vazio sem erro | Secret `SELLER` numa grafia aposentada — use o nome canônico atual |
| Playwright sem browser | `python -m playwright install chromium` |
| 🩻 Diagnóstico mostra regra antiga (ex.: Midea em várias grafias) | O app ainda roda o código anterior: Streamlit Cloud → **Manage app → Reboot**; local → pare o `streamlit run app.py`, `git pull` e suba de novo. Confira `regras vN` no cabeçalho |
| 🩻 Diagnóstico "Sem dado do PriceTrack no período" | Histórico no Drive inacessível ou import de D-1 atrasado: veja o status do histórico no rodapé da barra lateral e o run do `pricetrack_daily.yml` |
| `pricetrack_daily` com muitos dias / banco acima da cota | O heal reimportou dias podados (Drive ilegível no Actions). Poda imediata no PC: `python scripts/history_cli.py tier --dataset pricetrack --confirm` |

---

## 📚 Documentação

| Documento | Para quê |
|-----------|----------|
| [`CLAUDE.md`](CLAUDE.md) | Regras duras, decisões e padrões do projeto (leitura obrigatória para quem desenvolve, humano ou IA) |
| [`docs/INDEX.md`](docs/INDEX.md) | Navegação por tarefa |
| [`docs/MAPA_COLETAS.md`](docs/MAPA_COLETAS.md) | O que roda onde, quando e por quê |
| [`docs/COLETA_LOCAL_AUTENTICADA.md`](docs/COLETA_LOCAL_AUTENTICADA.md) | Coleta no PC com Chrome real logado |
| [`docs/BESTSELLERS.md`](docs/BESTSELLERS.md) | Mais Vendidos: KPI, regras, validação |
| [`docs/COCKPIT_TRADE.md`](docs/COCKPIT_TRADE.md) / [`docs/DASHBOARD_FILTERS.md`](docs/DASHBOARD_FILTERS.md) | Métricas do Cockpit e filtros globais |
| [`docs/TRACK_POSITION_SELLER.md`](docs/TRACK_POSITION_SELLER.md) | Estratégia e arquitetura do `seller_app` |
| [`docs/HISTORICO_DRIVE.md`](docs/HISTORICO_DRIVE.md) | Histórico frio em Parquet |
| [`docs/RETORNO_SUPABASE.md`](docs/RETORNO_SUPABASE.md) | Backend atual, janela de 2 dias, emergência de cota |
| [`docs/MIGRACAO_AIVEN.md`](docs/MIGRACAO_AIVEN.md) | Rollback para Postgres direto (Aiven) |
| [`docs/BRIEFING_LOCAL_SETUP.md`](docs/BRIEFING_LOCAL_SETUP.md) | Briefing diário local (Claude Code headless) |
| [`docs/PRICETRACK_FIDELIDADE.md`](docs/PRICETRACK_FIDELIDADE.md) | Fidelidade do preço PriceTrack |
| [`docs/diagnostico.md`](docs/diagnostico.md) | Fórmulas do painel 🩻 Diagnóstico |
| [`docs/diagnostico_fase0.md`](docs/diagnostico_fase0.md) | Descoberta do export PriceTrack e decisões do Diagnóstico (escopo, armazenamento) |
| [`docs/pricetrack_openapi_1.2.0.yaml`](docs/pricetrack_openapi_1.2.0.yaml) | Spec da API Externa PriceTrack 1.2.0 |
| [`.claude/COMMON_MISTAKES.md`](.claude/COMMON_MISTAKES.md) | Anti-padrões que já quebraram produção |
| [`docs/learnings/`](docs/learnings/) | Padrões de scraping, anti-bot, dealers, debug |
