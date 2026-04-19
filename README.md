# RAC Price Monitor — Retail Analytics & Competitive Intelligence

Bot de monitoramento de preços e posições de produtos em marketplaces brasileiros e varejistas especializados.

**Status:** ✅ Produção | **Última atualização:** Abril 2025

## 📋 Visão Geral

Este projeto realiza scraping automatizado de múltiplas plataformas de e-commerce para monitorar:
- **Posicionamento** de produtos (orgânico e patrocinado)
- **Preços** competitivos em tempo real
- **Informações de sellers** e fulfillment
- **Avaliações** e ratings de produtos
- **Disponibilidade** de estoque

Os dados são exportados em CSV e opcionalmente enviados para Supabase para análise em dashboard Streamlit.

### Plataformas Suportadas

| Plataforma | Status | Tipo | Observações |
|------------|--------|------|-------------|
| Mercado Livre | ✅ Funcional | Nacional Retail | Popup de CEP tratado automaticamente |
| Amazon | ✅ Funcional | Nacional Retail | Extração de seller corrigida |
| Magazine Luiza | ✅ Funcional | Nacional Retail | Seletores nm-* atualizados, Radware mitigado |
| Google Shopping | ✅ Funcional | Comparador de Preços | Limpeza de título otimizada |
| Leroy Merlin | ✅ Funcional | Nacional Varejo Especializado | Algolia API direta |
| Dealers (13+) | ✅ Funcional | Regional/Nacional | VTEX, WooCommerce, custom APIs |
| Shopee | ⏸️ Stand by | Nacional Marketplace | Requer sessão autenticada |
| Casas Bahia | ⏸️ Stand by | Nacional Retail | WAF Akamai, requer sessão |
| Fast Shop | ⏸️ Stand by | Nacional Varejo | Bloqueio PerimeterX |

### Dealers/Varejistas Especializados Incluídos

**Nacional/Grande Porte:** Carrefour

**Regional Médio Porte:** Grupo Mateus, Eletrozema, Angeloni, Império Digital, Bemol

**Regional Pequeno Porte:** Frigelar, CentralAr, PoloAr, Belmicro, GoCompras, FrioPecas, WebContinental, Dufrio, Leveros, ArCerto, Ferreira Costa, Climario, EngageEletro, NossoLar, Casas D'Água, TVLar, Zenir, CenterKennedy, Norte Refrigeracao, Armazém Paraíba, A.Dias, Carajás, Quero-Quero, Fijioka, Edimil, Única AR, Top Móveis

---

## 🚀 Instalação

### Pré-requisitos

- Python 3.9+
- Playwright browsers instalados
- (Opcional) Supabase configurado para dashboard

### Passos

```bash
# Clonar o repositório
cd rac-position-tracker

# Criar ambiente virtual (recomendado)
python -m venv venv

# Ativar ambiente virtual
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# Instalar browsers do Playwright
playwright install chromium
```

### Variáveis de Ambiente (Opcional)

Crie um arquivo `.env` na raiz para configurações opcionais:

```env
# Nome do analista (para relatórios)
ANALYST_NAME="Seu Nome"

# Modo headless do browser
HEADLESS=True

# Delays entre ações (segundos)
MIN_DELAY=4
MAX_DELAY=7

# Supabase (opcional, para upload automático)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-publishable-key
```

---

## 📖 Uso

### Execução Básica

```bash
# Demo rápida (Mercado Livre, 1 keyword, 1 página)
python main.py

# Todas as plataformas ativas (configuradas em config.py), 3 páginas
python main.py --platforms all --pages 3

# Plataformas específicas
python main.py --platforms ml magalu amazon --pages 2

# Apenas dealers/varejistas especializados
python main.py --platforms dealers --pages 2

# Keyword personalizada
python main.py --platforms ml --keywords "ar condicionado inverter 12000"

# Browser visível (útil para depuração)
python main.py --platforms dealers --pages 1 --no-headless
```

### Opções de Linha de Comando

| Opção | Descrição | Padrão |
|-------|-----------|--------|
| `--platforms` | Lista de plataformas: `ml`, `magalu`, `amazon`, `shopee`, `casasbahia`, `google_shopping`, `leroy`, `fast`, `dealers`, `all` | ACTIVE_PLATFORMS do config.py |
| `--pages` | Número máximo de páginas por busca | 3 |
| `--keywords` | Keywords personalizadas (substitui as do config.py) | KEYWORDS_LIST do config |
| `--headless` | Executar browser sem interface | True |
| `--no-headless` | Exibir browser visualmente | False |
| `--output-dir` | Diretório de saída dos CSVs | output/ |

### Comandos Frequentes

```bash
# Coleta completa rápida (marketplaces + dealers, 1 página)
python main.py --platforms ml magalu amazon google_shopping leroy dealers --pages 1

# Coleta apenas das plataformas ativas no config
python main.py --pages 1

# Debug com browser visível
python main.py --platforms ml --pages 1 --no-headless
```

---

## 📊 Output e Dashboard

### Arquivos Gerados

**CSV de Monitoramento:** `output/rac_monitoramento_YYYYMMDD_HHMM.csv`
- Encoding: UTF-8 BOM (compatível com Excel PT-BR)
- Separador: `;` (semicolon)
- Colunas padronizadas para análise

**Logs de Execução:** `logs/bot_YYYYMMDD_HHMMSS.log`
- Logs estruturados com Loguru
- Rotação a cada 50 MB, retenção de 7 dias

**HTML de Debug:** `logs/dealer_debug_<nome>_p<N>.html`
- Capturado quando 0 produtos são encontrados
- Essencial para diagnóstico de seletores CSS

### Colunas do DataFrame

| Coluna | Descrição | Exemplo |
|--------|-----------|---------|
| Data | Data da coleta | 2025-04-17 |
| Turno | Abertura (≤12h) ou Fechamento | Abertura |
| Horário | Hora exata da coleta | 08:30:00 |
| Analista | Nome configurado | Bot Automático Python |
| Plataforma | Nome do marketplace | Mercado Livre |
| Tipo Plataforma | Categoria da plataforma | Nacional Retail |
| Keyword Buscada | Termo de busca utilizado | ar condicionado inverter |
| Categoria Keyword | Categoria da keyword | Genérica |
| Marca Monitorada | Marca identificada | Midea |
| Produto / SKU | Nome completo do produto | Ar Condicionado Midea Inverter 12000 BTUs |
| Posição Orgânica | Posição nos resultados orgânicos | 3 |
| Posição Patrocinada | Posição nos anúncios | - |
| Posição Geral | Posição combinada | 3 |
| Preço (R$) | Preço extraído (float) | 2499.00 |
| Seller / Vendedor | Nome do vendedor | Midea Official Store |
| Fulfillment? | Tipo de entrega | FULL |
| Avaliação | Rating do produto (0-5) | 4.5 |
| Qtd Avaliações | Número de reviews | 1234 |
| Tag Destaque | Tags especiais | Frete Grátis |

### Dashboard Streamlit (Opcional)

Visualize os dados coletados em um dashboard interativo:

```bash
# Iniciar dashboard localmente
streamlit run app.py

# Acessar remotamente (expor em todas as interfaces)
streamlit run app.py --server.address=0.0.0.0 --server.port=8501
# Acesse: http://<seu-ip>:8501
```

**Recursos do Dashboard:**
- Gráficos de evolução de preços
- Comparativo de posicionamento por plataforma
- Tabela filtrável de produtos
- Exportação de relatórios

---

## 🔧 Configuração

Edite `config.py` para personalizar o comportamento:

### Keywords de Busca

```python
KEYWORDS_LIST: List[Keyword] = [
    # Head terms genéricos (alto volume)
    Keyword("ar condicionado split", "Genérica", "alta"),
    Keyword("ar condicionado inverter", "Genérica", "alta"),
    
    # Capacidade / BTU
    Keyword("ar condicionado 9000 btus", "Capacidade BTU", "alta"),
    Keyword("ar condicionado 12000 btus", "Capacidade BTU", "alta"),
    Keyword("ar condicionado 18000 btus", "Capacidade BTU", "alta"),
    
    # Marcas
    Keyword("ar condicionado midea", "Marca", "alta"),
    Keyword("lg dual inverter", "Marca", "alta"),
    Keyword("samsung windfree", "Marca", "alta"),
    
    # Intenção de compra
    Keyword("melhor ar condicionado custo benefício", "Intenção Compra", "alta"),
]
```

**Prioridades:**
- `"alta"`: Coleta diária recomendada
- `"media"`: 3x/semana
- `"baixa"`: Semanal

### Plataformas Ativas

```python
ACTIVE_PLATFORMS = {
    "ml":             True,   # Mercado Livre
    "magalu":         True,   # Magazine Luiza
    "amazon":         True,   # Amazon
    "shopee":         False,  # Stand by
    "casasbahia":     False,  # Stand by
    "google_shopping":True,   # Google Shopping
    "leroy":          True,   # Leroy Merlin
    "fast":           False,  # Bloqueado
    "dealers":        True,   # Dealers especializados
}
```

### Marcas Monitoradas

```python
BRANDS: List[str] = [
    "Springer Midea",  # Mais específicas primeiro
    "Midea Carrier",
    "Midea",
    "LG",
    "Samsung",
    "Carrier",
    "Gree",
    "Elgin",
    # ... (lista completa em config.py)
]
```

### Parâmetros Operacionais

```python
MAX_PAGES: int = 3           # Páginas por keyword
MIN_DELAY: float = 4.0       # Delay mínimo (segundos)
MAX_DELAY: float = 7.0       # Delay máximo (segundos)
PAGE_TIMEOUT: int = 45_000   # Timeout de página (ms)
PRIORITY_FILTER: Optional[List[str]] = None  # Ex: ["alta"] para coletas rápidas
```

---

## 🛠️ Ferramentas

### Diagnóstico de Seletores

O script `diagnostico.py` ajuda a identificar seletores CSS quando sites mudam seu DOM:

```bash
# Diagnosticar todas as plataformas
python diagnostico.py

# Plataforma específica
python diagnostico.py --platform ml amazon

# Browser visível para depuração
python diagnostico.py --visible

# Keyword personalizada
python diagnostico.py --keyword "midea 12000 btus"
```

**Output em `./diagnostico/`:**
- `{plataforma}_screenshot.png` — Screenshot da página
- `{plataforma}_page.html` — HTML completo para inspeção
- `{plataforma}_analysis.json` — Seletores detectados com contagens

---

## 📁 Estrutura do Projeto

```
rac-position-tracker/
├── main.py                  # Ponto de entrada principal (CLI)
├── app.py                   # Dashboard Streamlit
├── config.py                # Configurações globais
├── diagnostico.py           # Ferramenta de diagnóstico
├── requirements.txt         # Dependências Python
├── run-tracker.bat          # Script Windows para agendamento
├── setup-scheduler.ps1      # PowerShell: configurar scheduler
│
├── scrapers/
│   ├── __init__.py
│   ├── base.py              # BaseScraper ABC (Playwright, stealth)
│   ├── mercado_livre.py     # MLScraper
│   ├── amazon.py            # AmazonScraper
│   ├── magalu.py            # MagaluScraper (Radware mitigation)
│   ├── google_shopping.py   # GoogleShoppingScraper
│   ├── leroy_merlin.py      # LeroyMerlinScraper (Algolia API)
│   ├── shopee.py            # ShopeeScraper (stand by)
│   ├── casas_bahia.py       # CasasBahiaScraper (stand by)
│   ├── fast_shop.py         # FastShopScraper (stand by)
│   └── dealers.py           # DealerScraper (13+ varejistas)
│
├── utils/
│   ├── __init__.py
│   ├── text.py              # parse_price, parse_rating, normalize
│   ├── brands.py            # extract_brand() regex matching
│   ├── session_grabber.py   # Captura de sessão autenticada
│   ├── supabase_client.py   # Upload para Supabase
│   └── discover_shopee_api.py
│
├── output/                  # CSVs exportados
├── logs/                    # Logs + debug HTML
└── docs/                    # Documentação técnica
    ├── INDEX.md
    ├── QUICK_REFERENCE.md
    └── learnings/
```

---

## 🔄 Agendamento Automático

### Windows (Agendador de Tarefas)

Use o arquivo `run-tracker.bat`:

1. Abra `taskschd.msc`
2. Crie tarefa: **"RAC Position Tracker - Manhã"**
3. Disparador: Diariamente às 08:00
4. Ação: Executar `run-tracker.bat`
5. Repita para **"RAC Position Tracker - Tarde"** às 17:30

### Linux/Mac (cron)

```bash
# Editar crontab
crontab -e

# Adicionar linhas (executar às 08:00 e 17:30):
0 8 * * * cd /path/to/rac-position-tracker && venv/bin/python main.py --pages 1
30 17 * * * cd /path/to/rac-position-tracker && venv/bin/python main.py --pages 1
```

---

## 🐛 Troubleshooting

### Playwright não encontra browsers

```bash
playwright install chromium
```

### Site bloqueia scraping / CAPTCHA

1. Use `diagnostico.py --visible` para depuração visual
2. Verifique logs em `logs/` por indicadores de bloqueio
3. Para Magalu: o sistema rotaciona browser automaticamente a cada 15 keywords
4. Considere usar proxies ou sessões autenticadas (`utils/session_grabber.py`)

### Erro de timeout

Aumente timeouts em `config.py`:

```python
PAGE_TIMEOUT = 60000  # 60 segundos
NETWORK_IDLE_TIMEOUT = 10000
```

### Dealer retorna 0 produtos

1. Execute com `--no-headless` para observar o comportamento
2. Verifique `logs/dealer_debug_<nome>_p1.html` para inspecionar o HTML
3. Atualize seletores em `scrapers/dealers.py` `DEALER_CONFIGS`
4. Consulte `docs/learnings/scraping-patterns.md` para padrões de extração

### Preço não extraído (sites VTEX)

Sites VTEX carregam preços via JavaScript após DOMContentLoaded. O scraper usa fallback em 5 níveis:
1. CSS selectors
2. VTEX split price (currencyInteger + Decimal)
3. `[data-price]` attribute
4. `meta[itemprop="price"]`
5. JSON-LD schema.org/Product

Consulte `.claude/COMMON_MISTAKES.md` item #1 para detalhes.

---

## 📝 Requisitos Técnicos

| Dependência | Versão | Finalidade |
|-------------|--------|------------|
| playwright | >=1.50.0 | Automação de browser com stealth |
| beautifulsoup4 | ==4.12.3 | Parsing de HTML |
| pandas | >=2.2.2 | Manipulação de DataFrames |
| loguru | ==0.7.2 | Logging estruturado |
| requests | >=2.31.0 | Requisições HTTP |
| curl-cffi | >=0.6.0 | Requests com TLS fingerprint |
| supabase | >=2.3.0 | Upload para banco de dados |
| streamlit | >=1.35.0 | Dashboard web |
| plotly | >=5.20.0 | Visualizações interativas |
| fake-useragent | ==1.5.1 | Rotação de User-Agents |
| tenacity | ==8.3.0 | Retry logic |

---

## 📚 Documentação Técnica

Para desenvolvedores e mantenedores:

| Documento | Finalidade |
|-----------|------------|
| `.claude/QUICK_START.md` | Comandos essenciais e workflows |
| `.claude/ARCHITECTURE_MAP.md` | Estrutura do projeto e fluxo de dados |
| `.claude/COMMON_MISTAKES.md` | Anti-padrões críticos e correções |
| `docs/INDEX.md` | Navegação por tarefa |
| `docs/QUICK_REFERENCE.md` | Consulta rápida de padrões |
| `docs/learnings/` | Padrões de scraping, anti-bot, configs de dealers |

---

## 🔐 Segurança e Boas Práticas

- **Delays humanizados:** 4-7 segundos entre ações para evitar detecção
- **Rotação de User-Agent:** Lista de navegadores modernos atualizada
- **Stealth JS:** Patches aplicados via Playwright para bypass de detecção
- **Retry com backoff:** Tentativas automáticas em caso de falha temporária
- **Fallback chains:** Múltiplas estratégias de extração por tipo de dado

---

## 📄 Licença

Uso interno. Consulte o proprietário do projeto para termos de licenciamento.

---

## 👤 @ederrabelo

Desenvolvido para monitoramento competitivo de preços no varejo brasileiro de climatização.

**Tecnologias:** Python, Playwright, BeautifulSoup, Pandas, Streamlit, Supabase

---

## 📞 Suporte

Para issues relacionadas a:
- **Seletores CSS quebrados:** Execute `diagnostico.py` e abra issue com HTML de debug
- **Bloqueios anti-bot:** Verifique logs e consulte `.claude/COMMON_MISTAKES.md`
- **Adição de dealers:** Siga padrão em `scrapers/dealers.py` `DEALER_CONFIGS`
- **Dashboard:** Execute `streamlit run app.py` e reporte erros no console

---

**Última atualização:** Abril 2025 | **Versão:** 2.0
