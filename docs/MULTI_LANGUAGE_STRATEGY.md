# Estratégia Multi-Linguagem — RAC Position Tracker

> **Contexto:** O projeto usa Python como linguagem principal e Node.js/TypeScript como
> solução alternativa para Magalu (Akamai Bot Manager). Este documento avalia outras
> linguagens para cenários de falha de coleta ou ganho de performance.

---

## Stack Atual

| Linguagem | Componente | Status |
|-----------|-----------|--------|
| **Python 3.10+** | ML, Amazon, Google Shopping, Leroy, Dealers, Shopee* | ✅ Principal |
| **TypeScript/Node.js** | Magalu, Shopee (Puppeteer-stealth) | ✅ Ativo (`magalu_shopee/`) |

---

## Por Que Adicionar Outras Linguagens?

Três cenários justificam o uso de linguagens alternativas:

1. **Bloqueio por WAF/Bot Manager** — Python Playwright detectável em alguns sites (Akamai, PerimeterX)
2. **Performance** — APIs REST simples não precisam de browser; Go é 5–10× mais rápido
3. **Resiliência** — Se a linguagem principal falha, o fallback continua coletando

---

## Avaliação por Linguagem

### 1. Go (Golang) — ⭐⭐⭐⭐⭐ RECOMENDADO

**Casos de uso ideais no projeto:**
- **Leroy Merlin** — já usa Algolia REST API; Python faz o request, Go faria 10× mais rápido
- **VTEX APIs** (dealers) — muitos dealers usam VTEX; a API de produtos é REST pura
- **Coleta concorrente** — goroutines permitem coletar 40 dealers em paralelo real

**Vantagens:**
- Binário único: compila para Linux ARM64 (Oracle Cloud) sem dependências
- Memória muito baixa (~5 MB vs ~150 MB do Python + Playwright)
- `go-rod` ou `chromedp` para automação de browser quando necessário
- TLS fingerprinting nativo com `uTLS` — pode bypassar Akamai

**Desvantagens:**
- Aprendizado: sintaxe diferente de Python
- Não substitui Playwright para sites com JS pesado

**Exemplo de uso — Leroy Merlin via Algolia:**
```go
// leroy/main.go
package main

import (
    "encoding/json"
    "fmt"
    "net/http"
)

func searchAlgolia(keyword string) []Product {
    url := fmt.Sprintf("https://klxr2k97he-dsn.algolia.net/1/indexes/*/queries")
    // Chama a API Algolia diretamente (mesmo que Python, mas ~10× mais rápido em batch)
    resp, _ := http.Post(url, "application/json", buildPayload(keyword))
    // ...
}
```

**Como integrar:**
```bash
# Compila para Linux ARM64 (Oracle Cloud)
GOARCH=arm64 GOOS=linux go build -o bin/leroy_go ./leroy/
# Chama do collect_manha_linux.sh
./bin/leroy_go --keywords alta --pages 2 >> "$LOG" 2>&1
```

---

### 2. Bun — ⭐⭐⭐⭐ RECOMENDADO (substitui Node.js)

**O que é:** Runtime JavaScript moderno, drop-in replacement para Node.js.

**Vantagens sobre Node.js atual:**
- 3–5× mais rápido na inicialização (importante para coletas frequentes)
- TypeScript nativo (sem `ts-node`)
- Compatível com `puppeteer-extra` e plugins Stealth
- Inclui runtime + bundler + test runner em um único binário

**Migração do magalu_shopee:**
```bash
# Instala Bun
curl -fsSL https://bun.sh/install | bash

# Roda sem transpilar (TypeScript direto)
bun src/index.ts --platforms magalu --pages 2

# package.json: substituir "ts-node" por "bun"
"scrape:magalu": "bun src/index.ts --platforms magalu --pages 2"
```

**Quando migrar:** Quando a performance do Node.js for gargalo, ou para simplificar
o toolchain (elimina `ts-node` + `typescript` como devDependency).

---

### 3. curl + jq (Shell) — ⭐⭐⭐ ÚTIL para APIs simples

**Casos de uso no projeto:**
- Leroy Merlin (Algolia) — a API é um POST simples; nenhum browser necessário
- Verificação de preços em VTEX APIs públicas
- Health checks e notificações Telegram

**Exemplo — Leroy Merlin:**
```bash
#!/usr/bin/env bash
# Busca preços via Algolia sem browser, sem Python
curl -s -X POST \
  "https://klxr2k97he-dsn.algolia.net/1/indexes/*/queries" \
  -H "x-algolia-application-id: KLXR2K97HE" \
  -H "x-algolia-api-key: <key>" \
  -d '{"requests":[{"indexName":"products","query":"ar condicionado","hitsPerPage":20}]}' \
  | jq '.results[0].hits[] | {title: .name, price: .price.value, seller: .brand}'
```

**Vantagem:** Zero dependências adicionais — `curl` e `jq` já existem no Oracle Cloud VM.

---

### 4. C# (.NET Core) — ⭐⭐ SITUACIONAL

**Quando considerar:**
- Casas Bahia / Fast Shop (Akamai/PerimeterX) — .NET Playwright tem fingerprint diferente
- Playwright for .NET tem exatamente a mesma API que Python

**Por que não priorizar:**
- .NET em Oracle Cloud ARM64 funciona mas é complexo de configurar
- A diferença de fingerprint não garante bypass (Akamai analisa comportamento, não só TLS)
- Node.js com Puppeteer-stealth cobre o mesmo caso com menos overhead

**Exemplo — mesmo código, diferente fingerprint:**
```csharp
// Casas Bahia via .NET Playwright
using Microsoft.Playwright;
var browser = await Playwright.CreateAsync().Chromium.LaunchAsync();
var page = await browser.NewPageAsync();
await page.GotoAsync("https://www.casasbahia.com.br/...");
```

---

### 5. Rust — ⭐⭐ OVERKILL para este projeto

**Por que não:**
- Curva de aprendizado alta para ganho marginal
- `reqwest` + `scraper` crate são excelentes mas demandam ~2× mais código que Python
- Melhor retorno de Go para os casos de uso existentes

**Quando faria sentido:** Se o projeto evoluir para processar >1 milhão de registros/dia
e a normalização de dados for o gargalo (não a coleta).

---

## Matriz de Decisão

| Plataforma | Bloqueio | Solução Atual | Alternativa Recomendada |
|-----------|----------|--------------|------------------------|
| Mercado Livre | Nenhum | Python ✅ | — |
| Amazon | Nenhum | Python ✅ | — |
| Leroy Merlin | Nenhum | Python (Algolia) ✅ | **Go** (performance) |
| Google Shopping | reCAPTCHA leve | Python + delays ✅ | — |
| Dealers (VTEX) | Nenhum | Python ✅ | **Go** (concorrência) |
| Magalu | Akamai | **Node.js** ✅ | **Bun** (performance) |
| Casas Bahia | Akamai WAF | ⏸️ Pausado | **C# Playwright** ou proxy residencial |
| Fast Shop | PerimeterX | ⏸️ Pausado | Proxy residencial BR (única opção viável) |
| Shopee | Auth necessária | ⏸️ Pausado | Node.js com session capture ✅ |

---

## Roadmap de Implementação

### Sprint Imediata (já implementado)
- [x] Node.js Magalu integrado nos scripts Linux (`collect_manha_linux.sh`, `collect_noite_linux.sh`)
- [x] Node.js Magalu no GitHub Actions (`collect.yml`)
- [x] Scripts de sync para Windows e Linux (`sync_windows.bat`, `sync_linux.sh`)

### Sprint 1 (próximas 2 semanas)
- [ ] Migrar `magalu_shopee/` de `ts-node` para **Bun** (zero configuração TypeScript)
- [ ] Criar `go/leroy/` — scraper Leroy Merlin em Go (benchmark vs Python)
- [ ] Ativar Shopee: sessão via `npm run session:shopee` + coleta Node.js

### Sprint 2 (próximo mês)
- [ ] Expandir Go para VTEX APIs dos dealers (Dufrio, PoloAr, WebContinental)
- [ ] Avaliar C# Playwright para Casas Bahia como experimento

---

## Como Adicionar uma Nova Linguagem

1. **Crie uma pasta dedicada** na raiz: `go/`, `dotnet/`, etc.
2. **Saída em JSON/CSV** compatível com o schema de 19 colunas do projeto
3. **Integre no script de coleta** (`collect_*_linux.sh`, `.bat`, GitHub Actions)
4. **Documente no ARCHITECTURE_MAP.md** a nova entrada no fluxo de dados

---

*Última atualização: Mai 2026*
