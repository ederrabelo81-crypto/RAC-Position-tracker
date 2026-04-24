# RAC Price Monitor — Retail Analytics & Competitive Intelligence

Bot de monitoramento de preços e posicionamento de produtos de ar condicionado em marketplaces brasileiros e varejistas especializados.

**Status:** ✅ Produção | **Última atualização:** Abril 2026

---

## 📋 Visão Geral

Este projeto realiza scraping automatizado de múltiplas plataformas de e-commerce para monitorar:
- **Posicionamento** de produtos (orgânico e patrocinado)
- **Preços** competitivos em tempo real
- **Informações de sellers** e fulfillment
- **Avaliações** e ratings de produtos
- **Análise competitiva via IA** (Claude API)

Os dados são exportados em CSV e enviados para Supabase para visualização em dashboard Streamlit com inteligência competitiva.

---

## 🏗️ Arquitetura de Coleta

```
Oracle Cloud VM (Brazil East — São Paulo)
  └─ Cron 10:00 BRT → todas as plataformas, prioridade alta+media, 2 páginas
  └─ Cron 21:00 BRT → todas as plataformas, prioridade alta, 1 página
  └─ Supabase upload automático após cada coleta

GitHub Actions
  └─ Manual (workflow_dispatch) apenas — backup e testes pontuais
```

---

## 🌐 Plataformas Suportadas

| Plataforma | Status | Tipo | Observações |
|------------|--------|------|-------------|
| Mercado Livre | ✅ Funcional | Nacional Retail | Popup CEP tratado automaticamente |
| Amazon | ✅ Funcional | Nacional Retail | Extração de seller via "Vendido por" |
| Magazine Luiza | ✅ Funcional | Nacional Retail | Seletores nm-*, rotação anti-Radware |
| Google Shopping | ✅ Funcional | Comparador de Preços | div.rwVHAc + leaf-div title |
| Leroy Merlin | ✅ Funcional | Varejo Especializado | Algolia API direta |
| Dealers (13+) | ✅ Funcional | Regional/Nacional | JSON-LD, VTEX, DOM fallback |
| Shopee | ⏸️ Stand by | Nacional Marketplace | Requer sessão autenticada |
| Casas Bahia | ⏸️ Stand by | Nacional Retail | WAF Akamai |
| Fast Shop | ⏸️ Stand by | Nacional Varejo | Pendente validação |

### Dealers Incluídos

**Nacional:** Carrefour  
**Regional Médio Porte:** Grupo Mateus, Eletrozema, Angeloni, Império Digital, Bemol  
**Regional Pequeno Porte:** Frigelar, CentralAr, PoloAr, Belmicro, GoCompras, FrioPecas, WebContinental, Dufrio, Leveros, ArCerto, Ferreira Costa, Climario, EngageEletro, NossoLar, Casas D'Água, TVLar, Zenir, CenterKennedy, Norte Refrigeração, Armazém Paraíba, A.Dias, Carajás, Quero-Quero, Edimil, Única AR, Top Móveis

---

## 🚀 Instalação Local

### Pré-requisitos

- Python 3.10+
- Playwright browsers instalados
- Supabase configurado (obrigatório para dashboard)
- Conta Anthropic (opcional, para Competitive Intelligence)

### Passos

```bash
# Clonar o repositório
git clone https://github.com/ederrabelo81-crypto/rac-position-tracker.git
cd rac-position-tracker

# Criar e ativar ambiente virtual
python -m venv .venv

# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# Instalar browsers do Playwright
python -m playwright install chromium
```

### Arquivo `.env`

Crie um arquivo `.env` na raiz do projeto:

```env
# Supabase (obrigatório para upload e dashboard)
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_KEY=sua_service_role_key

# Anthropic (opcional — página Competitive Intelligence)
ANTHROPIC_API_KEY=sk-ant-...

# Nome do analista nos relatórios
ANALYST_NAME="Bot Automático Python"
```

> **Atenção:** use a **service_role key** do Supabase (não a anon key). Encontre em: Supabase → Project Settings → API → service_role.

---

## 📖 Uso

### Execução Básica

```bash
# Demo rápida (Mercado Livre, 1 keyword, 1 página)
python main.py

# Todas as plataformas, 2 páginas
python main.py --platforms ml magalu amazon google_shopping leroy dealers --pages 2

# Apenas dealers
python main.py --platforms dealers --pages 2

# Browser visível (debug)
python main.py --platforms dealers --pages 1 --no-headless
```

### Opções de Linha de Comando

| Opção | Descrição | Padrão |
|-------|-----------|--------|
| `--platforms` | Plataformas: `ml`, `magalu`, `amazon`, `google_shopping`, `leroy`, `dealers`, `all` | ACTIVE_PLATFORMS do config.py |
| `--pages` | Páginas por keyword | 3 |
| `--keywords` | Keywords customizadas (substitui config.py) | KEYWORDS_LIST do config |
| `--priority` | Filtro por prioridade: `alta`, `media`, `baixa` | todas |
| `--headless` | Browser sem interface (padrão) | True |
| `--no-headless` | Exibir browser (debug visual) | — |
| `--output-dir` | Diretório de saída dos CSVs | `output/` |

### Exemplos por Caso de Uso

```bash
# Coleta rápida só prioridade alta (para testes)
python main.py --platforms ml magalu --pages 1 --priority alta

# Coleta manhã completa
python main.py --platforms ml magalu amazon google_shopping leroy dealers --pages 2 --priority alta media

# Coleta noite rápida
python main.py --platforms ml magalu amazon google_shopping leroy dealers --pages 1 --priority alta
```

---

## 📊 Output e Dashboard

### Arquivos Gerados

**CSV de Monitoramento:** `output/rac_monitoramento_YYYYMMDD_HHMM.csv`
- Encoding: UTF-8 BOM (compatível com Excel PT-BR)
- Separador: `;`

**Logs:** `logs/bot_YYYYMMDD_HHMMSS.log`
- Rotação a cada 50 MB, retenção de 7 dias

**HTML de Debug:** `logs/dealer_debug_<nome>_p<N>.html`
- Salvo automaticamente quando dealer retorna 0 produtos

### Colunas do CSV

| Coluna | Descrição |
|--------|-----------|
| Data | Data da coleta (YYYY-MM-DD) |
| Turno | `Abertura` (≤12h BRT) ou `Fechamento` (>12h BRT) |
| Horário | Hora da coleta em BRT |
| Analista | Nome configurado no `.env` |
| Plataforma | Nome do marketplace |
| Tipo Plataforma | Categoria (Nacional Retail, Comparador, etc.) |
| Keyword Buscada | Termo de busca |
| Categoria Keyword | Genérica, Capacidade BTU, Marca, Intenção Compra |
| Marca Monitorada | Marca extraída do título |
| Produto / SKU | Nome normalizado do produto |
| Posição Orgânica | Posição nos resultados orgânicos |
| Posição Patrocinada | Posição nos anúncios patrocinados |
| Posição Geral | Posição combinada |
| Preço (R$) | Float parseado (vírgula/ponto tratados) |
| Seller / Vendedor | Nome do vendedor |
| Fulfillment? | `Sim` ou `Não` |
| Avaliação | Rating 0–5 |
| Qtd Avaliações | Número de reviews |
| Tag Destaque | Tags especiais (Mais Vendido, Frete Grátis, etc.) |

### Dashboard Streamlit

```bash
# Local
streamlit run app.py

# Ou acesse: https://rac-position-tracker-ygnmxhmemn6zwse5rp7uxf.streamlit.app
```

**Páginas disponíveis:**
- **Visão Geral** — métricas agregadas, evolução de preços, posicionamento por plataforma
- **Análise por Plataforma** — comparativo detalhado entre marketplaces
- **Análise por Marca** — share de mercado e evolução das marcas monitoradas
- **🧠 Competitive Intelligence** — relatórios gerados por IA (Claude) com insights competitivos, download em Markdown

---

## ☁️ Infraestrutura — Oracle Cloud Free Tier

A coleta automática roda em uma VM Oracle Cloud (Brazil East, São Paulo).

### Configuração da VM

```bash
# Na VM, baixar e executar o script de setup:
curl -fsSL https://raw.githubusercontent.com/ederrabelo81-crypto/RAC-Position-tracker/main/scripts/oracle_setup.sh -o oracle_setup.sh
chmod +x oracle_setup.sh
./oracle_setup.sh \
  --supabase-url "https://xxxx.supabase.co" \
  --supabase-key "sua_service_role_key"
```

O script instala automaticamente:
- Python 3.11 + virtualenv
- Playwright Chromium (`--with-deps`)
- Todas as dependências Python
- **2 GB de swap** (essencial para VM.Standard.E2.1.Micro com 1 GB RAM)
- Cron jobs: 13:00 UTC (manhã) e 00:00 UTC (noite)

### Cron Jobs na VM

```bash
# Ver jobs configurados
crontab -l

# Monitorar logs
~/rac-position-tracker/scripts/monitor.sh

# Atualizar código após push
cd ~/rac-position-tracker && git pull origin main
```

### Scripts de Coleta

| Script | Horário BRT | Plataformas | Prioridade | Páginas |
|--------|-------------|-------------|------------|---------|
| `collect_manha_linux.sh` | 10:00 | Todas | alta + media | 2 |
| `collect_noite_linux.sh` | 21:00 | Todas | alta | 1 |

---

## 🔄 GitHub Actions (Backup Manual)

O workflow `.github/workflows/collect.yml` está configurado para **execução manual apenas** (não tem cron ativo). Use para testes pontuais ou se a VM Oracle estiver indisponível.

```
GitHub Actions → Actions → RAC Price Collection → Run workflow
```

Parâmetros disponíveis: `platforms`, `pages`, `priority`.

---

## 🔧 Configuração

### Keywords (`config.py`)

```python
KEYWORDS_LIST: List[Keyword] = [
    Keyword("ar condicionado split",      "Genérica",        "alta"),
    Keyword("ar condicionado inverter",   "Genérica",        "alta"),
    Keyword("ar condicionado 12000 btus", "Capacidade BTU",  "alta"),
    Keyword("ar condicionado midea",      "Marca",           "alta"),
    Keyword("melhor ar condicionado",     "Intenção Compra", "media"),
    # ...
]
```

**Prioridades:** `alta` (coleta diária), `media` (manhã), `baixa` (opcional)

### Filtro de Turno

```python
TURNO_ABERTURA_MAX_HOUR: int = 12  # ≤ 12h → "Abertura" | > 12h → "Fechamento"
```

> **Importante:** os timestamps usam sempre BRT (America/Sao_Paulo). A VM Oracle e o GitHub Actions têm `TZ=America/Sao_Paulo` configurado explicitamente.

---

## 📁 Estrutura do Projeto

```
rac-position-tracker/
├── main.py                      # CLI principal (argparse, loop de scrapers, CSV export)
├── app.py                       # Dashboard Streamlit (6 páginas + CI com Claude)
├── config.py                    # Keywords, plataformas, delays, User-Agents
├── requirements.txt             # Dependências bot
├── requirements_app.txt         # Dependências dashboard
│
├── scrapers/
│   ├── base.py                  # BaseScraper ABC (Playwright, stealth JS, _build_record)
│   ├── mercado_livre.py         # MLScraper
│   ├── amazon.py                # AmazonScraper
│   ├── magalu.py                # MagaluScraper (rotação anti-Radware)
│   ├── google_shopping.py       # GoogleShoppingScraper
│   ├── leroy_merlin.py          # LeroyMerlinScraper (Algolia API)
│   ├── dealers.py               # DealerScraper (JSON-LD + VTEX + DOM)
│   ├── shopee.py                # ⏸️ Stand by
│   ├── casas_bahia.py           # ⏸️ Stand by
│   └── fast_shop.py             # ⏸️ Stand by
│
├── utils/
│   ├── text.py                  # parse_price, get_turno, normalize_text
│   ├── brands.py                # extract_brand() regex word boundary
│   ├── normalize_product.py     # normalize_product_name()
│   └── supabase_client.py       # Upload, limpeza e manutenção do banco
│
├── scripts/
│   ├── oracle_setup.sh          # Setup completo da VM Oracle Cloud
│   ├── collect_manha_linux.sh   # Coleta manhã (VM Oracle)
│   ├── collect_noite_linux.sh   # Coleta noite (VM Oracle)
│   ├── collect_manha.bat        # Coleta manhã (Windows — backup)
│   ├── collect_tarde.bat        # Coleta noite (Windows — backup)
│   ├── fix_turno.py             # Limpeza pontual de turno invertido no Supabase
│   └── monitor.sh               # Tail dos logs de cron na VM
│
├── .github/workflows/
│   └── collect.yml              # GitHub Actions (manual apenas)
│
├── output/                      # CSVs exportados
├── logs/                        # Loguru logs + dealer_debug_*.html
└── docs/                        # Documentação técnica interna
```

---

## 🛠️ Manutenção do Banco (Supabase)

Funções disponíveis em `utils/supabase_client.py`:

| Função | Finalidade |
|--------|------------|
| `upload_to_supabase(records)` | Upload de registros coletados |
| `fix_inverted_turno_in_supabase()` | Remove registros com turno invertido (bug pré-timezone-fix) |
| `delete_invalid_from_supabase()` | Remove produtos não relacionados a AC |
| `normalize_brands_in_supabase()` | Consolida variantes de marca (Springer → Midea) |
| `scan_fix_bad_prices_in_supabase()` | Remove preços suspeitos (bug parser ×10) |
| `normalize_all_products_in_supabase()` | Re-normaliza nomes de produto |

Todas as funções aceitam `dry_run=True` para preview antes de alterar dados.

```bash
# Limpeza pontual de turno invertido (se necessário):
python scripts/fix_turno.py           # dry-run (só conta)
python scripts/fix_turno.py --confirm  # executa a deleção
```

---

## 🐛 Troubleshooting

### Playwright não encontra browsers
```bash
python -m playwright install chromium
```

### Supabase upload ignorado localmente
Verifique se o arquivo `.env` existe na raiz com `SUPABASE_URL` e `SUPABASE_KEY` preenchidos com a **service_role key**.

### Dashboard mostra turno errado (Abertura/Fechamento invertidos)
Dados coletados antes do fix de timezone têm turno invertido. Execute a limpeza:
```bash
python scripts/fix_turno.py --confirm
```

### Dealer retorna 0 produtos
1. Verifique `logs/dealer_debug_<nome>_p1.html`
2. Execute com `--no-headless` para observar visualmente
3. Atualize seletores em `DEALER_CONFIGS` em `scrapers/dealers.py`

### VM Oracle sem memória (OOM)
O setup já configura 2 GB de swap. Se ocorrer OOM mesmo assim:
```bash
free -h                    # verificar uso atual
sudo swapon --show         # confirmar swap ativo
```

### Cron não executa na VM Oracle
```bash
sudo systemctl status cron
sudo systemctl start cron
crontab -l
```

---

## 📝 Dependências Principais

| Pacote | Finalidade |
|--------|------------|
| `playwright>=1.50` | Browser automation com stealth |
| `beautifulsoup4` | Parsing HTML |
| `pandas>=2.2` | DataFrames e export CSV |
| `loguru` | Logging estruturado |
| `supabase>=2.3` | Upload para banco de dados |
| `streamlit>=1.35` | Dashboard web |
| `plotly>=5.20` | Gráficos interativos |
| `anthropic>=0.40` | Claude API (Competitive Intelligence) |
| `tenacity` | Retry com backoff |
| `python-dotenv` | Carregamento de `.env` |

---

## 📚 Documentação Técnica

| Documento | Finalidade |
|-----------|------------|
| `.claude/QUICK_START.md` | Comandos essenciais |
| `.claude/ARCHITECTURE_MAP.md` | Fluxo de dados e estrutura |
| `.claude/COMMON_MISTAKES.md` | Anti-padrões críticos |
| `docs/INDEX.md` | Navegação por tarefa |
| `docs/learnings/` | Scraping patterns, anti-bot, dealer configs |

---

## 👤 @ederrabelo

Desenvolvido para monitoramento competitivo de preços no varejo brasileiro de climatização (RAC).

**Stack:** Python · Playwright · BeautifulSoup · Pandas · Streamlit · Supabase · Claude API · Oracle Cloud

---

**Versão:** 3.0 | **Última atualização:** Abril 2026
