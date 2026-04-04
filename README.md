# RAC Position Tracker

Bot de monitoramento de preços e posições de produtos em marketplaces brasileiros (RAC - Retail Analytics & Competitive Intelligence).

## 📋 Visão Geral

Este projeto realiza scraping automatizado de múltiplas plataformas de e-commerce para monitorar:
- **Posicionamento** de produtos (orgânico e patrocinado)
- **Preços** competitivos
- **Informações de sellers** e fulfillment
- **Avaliações** e ratings de produtos

### Plataformas Suportadas

| Plataforma | Status | Observações |
|------------|--------|-------------|
| Mercado Livre | ✅ Funcional | Popup de CEP tratado automaticamente |
| Amazon | ✅ Funcional | - |
| Magazine Luiza | ✅ Funcional | Seletores confirmados via diagnóstico |
| Google Shopping | ✅ Funcional | - |
| Leroy Merlin | ✅ Funcional | Algolia API, preço via averagePromotionalPrice |
| Fast Shop | ✅ Ativo | - |
| Shopee | ⏸️ Stand by | Requer sessão autenticada via session_grabber |
| Casas Bahia | ⏸️ Stand by | WAF Akamai, requer sessão via session_grabber |

## 🚀 Instalação

### Pré-requisitos

- Python 3.9+
- Playwright browsers instalados

### Passos

```bash
# Clonar o repositório
cd rac-position-tracker

# Instalar dependências
pip install -r requirements.txt

# Instalar browsers do Playwright
playwright install
```

## 📖 Uso

### Execução Básica

```bash
# Demo com Mercado Livre (padrão)
python main.py

# Todas as plataformas ativas, 3 páginas
python main.py --platforms all --pages 3

# Plataformas específicas
python main.py --platforms ml magalu amazon --pages 2

# Keyword personalizada
python main.py --platforms ml --keywords "ar condicionado inverter 12000"
```

### Opções de Linha de Comando

| Opção | Descrição | Padrão |
|-------|-----------|--------|
| `--platforms` | Lista de plataformas (ml, amazon, magalu, shopee, leroy, fast, google_shopping, dealers, all) | Definido em config.py |
| `--pages` | Número máximo de páginas por busca | 3 |
| `--keywords` | Keywords personalizadas (separadas por vírgula) | KEYWORDS_LIST do config |
| `--headless` | Executar browser sem interface | True |

### Agendamento Automático

#### Windows

Use o arquivo `run-tracker.bat` com o Agendador de Tarefas:

1. Abra `taskschd.msc`
2. Crie uma tarefa chamada "RAC Position Tracker - Manhã"
3. Disparador: Diariamente às 08:00
4. Ação: Executar `run-tracker.bat`
5. Repita para "RAC Position Tracker - Tarde" às 17:30

#### Linux/Mac

```bash
# Adicionar ao crontab (executar às 08:00 e 17:30)
crontab -e

# Linhas para adicionar:
0 8 * * * cd /path/to/rac-position-tracker && python main.py --platforms all --pages 3
30 17 * * * cd /path/to/rac-position-tracker && python main.py --platforms all --pages 3
```

## 📁 Estrutura do Projeto

```
rac-position-tracker/
├── main.py              # Ponto de entrada principal
├── config.py            # Configurações globais (keywords, brands, plataformas)
├── diagnostico.py       # Ferramenta de diagnóstico de seletores CSS
├── teste.py             # Scripts de teste
├── requirements.txt     # Dependências Python
├── run-tracker.bat      # Script Windows para agendamento
├── setup-scheduler.ps1  # PowerShell script para configurar scheduler
├── scrapers/
│   ├── base.py          # Classe base abstrata para scrapers
│   ├── mercado_livre.py # Scraper do Mercado Livre
│   ├── amazon.py        # Scraper da Amazon
│   ├── magalu.py        # Scraper do Magazine Luiza
│   ├── shopee.py        # Scraper da Shopee
│   ├── casas_bahia.py   # Scraper das Casas Bahia
│   ├── google_shopping.py
│   ├── leroy_merlin.py
│   ├── fast_shop.py
│   └── dealers.py       # Scraper genérico para dealers configuráveis
├── utils/
│   ├── brands.py        # Extração de marcas por regex
│   ├── text.py          # Normalização de texto e preços
│   ├── session_grabber.py
│   └── discover_shopee_api.py
├── output/              # CSVs exportados
├── logs/                # Logs de execução
└── diagnostico/         # Output da ferramenta de diagnóstico
```

## 📊 Output

Os dados são exportados em CSV na pasta `output/` com nome datado:

```
output/tracking_20260304_083000.csv
```

### Colunas do DataFrame

| Coluna | Descrição |
|--------|-----------|
| Data | Data da coleta |
| Turno | Abertura (≤10h) ou Fechamento |
| Horário | Hora exata da coleta |
| Analista | Nome configurado |
| Plataforma | Nome do marketplace |
| Tipo Plataforma | Categoria da plataforma |
| Keyword Buscada | Termo de busca utilizado |
| Categoria Keyword | Categoria da keyword |
| Marca Monitorada | Marca identificada no produto |
| Produto / SKU | Nome completo do produto |
| Posição Orgânica | Posição nos resultados orgânicos |
| Posição Patrocinada | Posição nos anúncios |
| Posição Geral | Posição combinada |
| Preço (R$) | Preço extraído |
| Seller / Vendedor | Nome do vendedor |
| Fulfillment? | Tipo de entrega (FULL, FBM, etc) |
| Avaliação | Rating do produto |
| Qtd Avaliações | Número de reviews |
| Tag Destaque | Tags especiais (Frete Grátis, etc) |

## 🔧 Configuração

Edite `config.py` para personalizar:

### Keywords de Busca

```python
KEYWORDS_LIST: List[Keyword] = [
    Keyword("ar condicionado split", "Genérica", "alta"),
    Keyword("ar condicionado inverter", "Genérica", "alta"),
    Keyword("ar condicionado 9000 btus", "Capacidade BTU", "alta"),
    # ... adicione suas keywords
]
```

### Marcas Monitoradas

```python
BRANDS = [
    "Midea",
    "Carrier",
    "LG",
    "Samsung",
    "Springer",
    # ... adicione suas marcas
]
```

### Plataformas Ativas

```python
ACTIVE_PLATFORMS = ["ml", "amazon", "magalu", "google_shopping", "leroy", "fast"]
```

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
- `{plataforma}_screenshot.png` - Screenshot da página
- `{plataforma}_page.html` - HTML completo
- `{plataforma}_analysis.json` - Seletores detectados

## 🔐 Variáveis de Ambiente

Opcionalmente, crie um `.env` na raiz:

```env
ANALYST_NAME=Seu Nome
HEADLESS=True
MIN_DELAY=2
MAX_DELAY=5
```

## 📝 Requisitos Técnicos

- **Playwright**: Automação de browser com stealth
- **BeautifulSoup4**: Parsing de HTML
- **Pandas**: Manipulação de dados
- **Loguru**: Logging estruturado
- **Requests/curl-cffi**: Requisições HTTP

## 🐛 Troubleshooting

### Playwright não encontra browsers

```bash
playwright install chromium
```

### Site bloqueia scraping

- Use `diagnostico.py --visible` para depuração
- Verifique se há CAPTCHA ou bloqueio de IP
- Considere usar proxies ou sessões autenticadas

### Erro de timeout

Aumente o timeout em `config.py`:

```python
PAGE_TIMEOUT = 60000  # 60 segundos
NETWORK_IDLE_TIMEOUT = 30000
```

## 📄 Licença

Uso interno. Consulte o proprietário do projeto para termos de licenciamento.

## 👤 Autor

Desenvolvido para monitoramento competitivo de preços no varejo brasileiro.

---

**Última atualização:** Março 2026
