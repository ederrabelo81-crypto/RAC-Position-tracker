# RAC Position Tracker — Midea Brasil E-Commerce

Ferramenta de monitoramento automatizado de posicionamento em buscas nos principais varejistas e marketplaces do Brasil, com exportação para Google Sheets e Excel.

## Estrutura do Projeto

```
rac-position-tracker/
├── index.js                    # Entry point — orquestrador principal
├── config.js                   # Configuração central (plataformas, keywords, marcas)
├── package.json
├── credentials.json            # ← Você cria (Google Service Account)
├── run-tracker.bat             # Script para Agendador de Tarefas Windows
├── setup-scheduler.ps1         # Setup automático do agendador
├── scrapers/
│   └── dispatcher.js           # Scraping genérico por plataforma
├── exporters/
│   ├── google-sheets.js        # Exportação Google Sheets API v4
│   └── excel-local.js          # Exportação Excel local (ExcelJS)
├── utils/
│   ├── logger.js               # Logger com cores + gravação em arquivo
│   └── report.js               # Relatório resumido pós-coleta
├── output/                     # ← Arquivos Excel gerados aqui
└── logs/                       # ← Logs diários aqui
```

---

## Instalação

### Pré-requisitos
- **Node.js 18+** — [nodejs.org](https://nodejs.org)
- **Windows 10/11** (para agendamento automático)

### Passo a passo

```bash
# 1. Clonar/copiar o projeto para sua máquina
cd C:\Users\%USERNAME%
# (copie a pasta rac-position-tracker aqui)

# 2. Instalar dependências
cd rac-position-tracker
npm install

# 3. Instalar o browser Chromium para Playwright
npx playwright install chromium

# 4. Testar com dry-run (não salva dados)
npm run dry-run
```

---

## Configuração do Google Sheets (Opcional)

Se quiser exportar automaticamente para uma planilha Google:

### 1. Criar projeto no Google Cloud

1. Acesse [console.cloud.google.com](https://console.cloud.google.com)
2. Crie um novo projeto (ex: "RAC Position Tracker")
3. Vá em **APIs e Serviços** → **Biblioteca**
4. Busque e ative **Google Sheets API**

### 2. Criar Service Account

1. Vá em **APIs e Serviços** → **Credenciais**
2. **Criar credenciais** → **Conta de serviço**
3. Nome: "rac-tracker-bot"
4. Após criar, clique na conta → aba **Chaves**
5. **Adicionar chave** → **Criar nova chave** → **JSON**
6. Salve o arquivo como `credentials.json` na raiz do projeto

### 3. Compartilhar a planilha

1. Crie ou abra sua planilha no Google Sheets
2. Copie o ID da URL:
   ```
   https://docs.google.com/spreadsheets/d/ESTE_É_O_ID/edit
   ```
3. Compartilhe a planilha com o email do Service Account
   (encontrado no `credentials.json` como `client_email`)
4. Edite `config.js` e cole o ID:
   ```javascript
   googleSheets: {
     spreadsheetId: 'COLE_O_ID_AQUI',
   }
   ```

---

## Uso

### Execução manual

```bash
# Coleta completa (todas as plataformas, salva em ambos)
node index.js

# Apenas Mercado Livre
node index.js --platform meli

# Apenas Amazon
node index.js --platform amazon

# Salvar apenas em Excel local
node index.js --output excel

# Salvar apenas em Google Sheets
node index.js --output sheets

# Dry-run (testa sem salvar)
node index.js --dry-run
```

### Scripts prontos

```bash
npm start          # Coleta completa
npm run morning    # Coleta matinal
npm run evening    # Coleta vespertina
npm run meli-only  # Só Mercado Livre
npm run dry-run    # Teste
```

---

## Agendamento Automático (Windows)

### Opção A: Setup automático (recomendado)

Execute como Administrador no PowerShell:

```powershell
cd C:\Users\%USERNAME%\rac-position-tracker
powershell -ExecutionPolicy Bypass -File setup-scheduler.ps1
```

Isso cria automaticamente duas tarefas:
- **RAC Position Tracker - Manhã** → 08:00
- **RAC Position Tracker - Tarde** → 17:30

### Opção B: Setup manual

1. Pressione `Win+R`, digite `taskschd.msc`
2. **Criar Tarefa Básica...**
3. Nome: "RAC Position Tracker - Manhã"
4. Disparador: Diariamente às 08:00
5. Ação: Iniciar programa → `run-tracker.bat`
6. Definir início em: caminho do projeto
7. Repetir para o turno da tarde (17:30)

---

## Configuração de Keywords e Plataformas

Edite `config.js` para ajustar:

### Adicionar keyword

```javascript
keywords: [
  // ... existentes ...
  { term: 'sua nova keyword', category: 'Genérica', priority: 'alta' },
]
```

### Adicionar plataforma

```javascript
platforms: [
  {
    id: 'nova_plataforma',
    name: 'Nome Exibição',
    type: 'Tipo',
    baseUrl: 'https://...',
    searchPattern: '{baseUrl}?q={keyword}',
    active: true,
    selectors: {
      resultsList: 'CSS_DO_CONTAINER_DE_RESULTADOS',
      productName: 'CSS_DO_NOME_PRODUTO',
      price: 'CSS_DO_PRECO',
      // ... demais seletores
    },
  },
]
```

### Ajustar velocidade / anti-bloqueio

```javascript
scraping: {
  delayBetweenRequests: 5000,  // aumentar se for bloqueado
  randomDelayMax: 3000,
  pageTimeout: 45000,
}
```

---

## Monitoramento e Logs

- Logs diários em `./logs/tracker_YYYY-MM-DD.log`
- Histórico de execuções do agendador: `./logs/scheduler_history.log`
- Relatório resumido impresso no console após cada coleta

---

## Solução de Problemas

### "Browser não inicia"
```bash
npx playwright install chromium
```

### "Timeout em plataforma X"
- Aumente `pageTimeout` no config
- Verifique se a plataforma não mudou seus seletores CSS
- Execute com `browser.headless: false` para debug visual

### "Google Sheets: erro de autenticação"
- Verifique se `credentials.json` está na raiz
- Confirme que a planilha foi compartilhada com o email do Service Account
- Verifique se a Sheets API está ativa no projeto Google Cloud

### "Resultados vazios em plataforma X"
- Seletores CSS mudam com frequência — inspecione a página no browser
- Atualize os seletores no `config.js` → `platforms[x].selectors`
- Use `--platform X --dry-run` com `headless: false` para investigar

---

## Notas Importantes

1. **Seletores CSS são frágeis**: Plataformas de e-commerce mudam seu HTML periodicamente. Quando uma plataforma parar de retornar resultados, inspecione a página e atualize os seletores no `config.js`.

2. **Anti-bot**: Mercado Livre e Shopee possuem proteções robustas. O Playwright com stealth mode ajuda, mas coletas muito agressivas podem ser bloqueadas. Respeite os delays configurados.

3. **Rate limiting**: O padrão de 3-5s entre requests é conservador. Não reduza abaixo de 2s.

4. **Google Sheets API**: O tier gratuito permite 300 requests/minuto e 60 requests/minuto por usuário. Mais que suficiente para esta aplicação.

5. **Custo**: Todas as dependências são gratuitas. O Google Cloud oferece tier gratuito para a Sheets API. O Playwright é open-source.
