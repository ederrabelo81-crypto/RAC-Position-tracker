# Diagnóstico de Scrapers de Dealers — Maio 2026

**Data:** 2026-05-12  
**Status:** 🚫 CRÍTICO — **11/12 dealers bloqueados**  
**Causa Raiz:** WAF (Web Application Firewall) bloqueando HTTP simples

---

## 📊 Resumo Executivo

| Status | Dealers | % |
|--------|---------|---|
| 🚫 Bloqueado WAF | 11 | 91.7% |
| ⏸️  Em HOLD | 1 | 8.3% |
| ✅ Funcionando | 0 | 0% |

### Dealers Afetados (Ordem de Prioridade)

**CRÍTICOS (Maior volume):**
- Frigelar (Oracle OCC)
- CentralAr (SAP Hybris)
- Leveros (VTEX IO)
- Dufrio (VTEX)

**ALTOS (Médio volume):**
- WebContinental (VTEX)
- FrioPecas (VTEX)
- Climario (VTEX)

**MÉDIOS (Menor volume):**
- PoloAr (Custom search)
- GoCompras (WooCommerce)
- ArCerto (WooCommerce + CF)
- NorteRefrigeracao (Custom)

**EM HOLD:**
- UnicaAR/Livaar (Domínio não resolvido)

---

## 🔍 Análise Detalhada por Dealer

### 1. **Frigelar** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** Oracle Commerce Cloud (OCC) + Knockout.js
- **URL:** `https://www.frigelar.com.br/split-inverter/c`
- **Problema:** Requer Playwright para renderização JavaScript
- **Exigência Especial:** CEP/Validação de sessão (injeção via JS)
- **Solução:** Implementar com Playwright + injetar CEP via JS

### 2. **CentralAr** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** VTEX IO (SAP Hybris) + `.pdc_product-item` seletor
- **URL:** `https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER`
- **Problema:** WAF Akamai bloqueia requisiçõessimples
- **JSON-LD:** Não disponível (schema é Organization, não Product)
- **Solução:** Usar Playwright + esperar seletor `.pdc_product-item`

### 3. **PoloAr** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** Custom (Radix Search Engine ou similar)
- **URL:** `https://www.poloar.com.br/ar-condicionado/inverter?...`
- **Problema:** Preços carregados via XHR após renderização
- **Paginação:** `page=0` (0-indexed)
- **Solução:** Playwright + esperar carregamento XHR (extended_wait: 15s)

### 4. **GoCompras** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** WooCommerce
- **URL:** `https://www.gocompras.com.br/ar-condicionado/split-hi-wall/`
- **Problema:** WAF simples, mas requer navegador
- **Solução:** Playwright com stealth mode

### 5. **FrioPecas** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** VTEX
- **URL:** `https://www.friopecas.com.br/ar-condicionado/ar-condicionado-split-inverter`
- **Solução:** Playwright + seletores VTEX padrão

### 6. **WebContinental** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** VTEX
- **URL:** `https://www.webcontinental.com.br/climatizacao/ar-condicionado/ar-condicionado-split-hi-wall`
- **Solução:** Playwright + seletores VTEX

### 7. **Dufrio** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** VTEX (com preços concatenados)
- **URL:** `https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter`
- **Problema:** Preços extraídos como "182900" em vez de "1829,00"
- **Seletor:** `.product-item` (42 itens detectados em análise anterior)
- **Solução:** Playwright + usar `_extract_vtex_split_price()` + JSON-LD como fallback

### 8. **Leveros** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** VTEX IO
- **URL:** `https://www.leveros.com.br/ar-condicionado/inverter`
- **Seletor:** `[data-sku]` (118 preços em JSON-LD)
- **Solução:** Playwright + priorizar JSON-LD (fonte mais confiável)

### 9. **ArCerto** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** WooCommerce com Cloudflare
- **URL:** `https://www.arcerto.com/categoria/ar-condicionado-inverter/`
- **Nota:** Página 2+ dispara Cloudflare challenge
- **Solução:** Playwright + limitar a 1 página (max_pages: 1)

### 10. **Climario** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** VTEX
- **URL:** `https://www.climario.com.br/ar-condicionado?...`
- **Problema:** URL com muitos parâmetros VTEX
- **Solução:** Playwright + seletores VTEX

### 11. **NorteRefrigeracao** 🚫
- **Status:** Bloqueado WAF (HTTP 403)
- **Platform:** Custom
- **URL:** `https://www.norterefrigeracao.com.br/ar-condicionado/`
- **Solução:** Playwright + análise de seletores DOM customizados

### 12. **UnicaAR (Livaar)** ⏸️
- **Status:** Em HOLD
- **Platform:** VTEX (esperado)
- **URL (Atual):** `https://www.unicaarcondicionado.com.br/ar-condicionado`
- **Problema:** Domínio não resolveu em 2026-04-26, mantém em HOLD
- **Ação Necessária:** Validar se domínio foi movido ou descontinuado
- **Candidatos Alternativos:**
  - `https://www.livaar.com.br/` (rebrand possível)
  - Verificar com fornecedor

---

## 🔧 Causa Raiz do Bloqueio WAF

### Por que TODOS retornam HTTP 403?

1. **Request HTTP simples é detectada como bot:**
   - Headers padrão de `requests` library
   - Sem cookies de sessão
   - User-Agent básico
   - Sem JavaScript

2. **Dealers usam diferentes WAF:**
   - **Cloudflare:** ArCerto, muitos VTEX
   - **Akamai:** CentralAr, alguns VTEX IO
   - **Imperva:** Possível em alguns
   - **Custom WAF:** PoloAr, GoCompras

3. **Solução: Usar Playwright**
   - Renderiza com Chromium real
   - Executa JavaScript
   - Simula comportamento humano
   - WAF vê como navegador legítimo

---

## ✅ Soluções Recomendadas

### Prioridade 1: Implementar Playwright em DealerScraper

**Arquivo:** `scrapers/dealers.py`

```python
# Adicionar ao método search():

# 1. Usar Playwright em vez de requests
async def search(self, keyword: str, page_limit: int = 1) -> List[Dict]:
    url = DEALER_CONFIGS[self._current_dealer]["url"]
    
    # Usar Playwright para renderizar
    page = await self._page  # Browser context já aberto em BaseScraper
    await page.goto(url, wait_until="networkidle")
    
    # Aguardar JS rendering
    await page.wait_for_selector(".product-item, [data-sku], article[class*='product']", timeout=10000)
    
    # Extrair HTML renderizado
    html = await page.content()
    
    # Processar normalmente
    items = self._parse_results(html)
    return items
```

### Prioridade 2: Configurar Stealth Mode

```python
# Em scrapers/base.py, adicionar stealth ao Playwright:

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
Object.defineProperty(navigator, 'plugins', { get: () => [] });
"""

# Injetar em cada página
await page.add_init_script(STEALTH_JS)
```

### Prioridade 3: Tratar Casos Especiais

| Dealer | Caso Especial | Solução |
|--------|---------------|----------|
| **Frigelar** | Requer CEP | `await page.fill('input[name="cep"]', '01310-100')` |
| **Dufrio** | Preço concatenado | Usar `_extract_vtex_split_price()` ou JSON-LD |
| **Leveros** | JSON-LD priority | Priorizar JSON-LD (118 produtos) sobre DOM |
| **ArCerto** | CF challenge p2+ | Limitar a 1 página |
| **CentralAr** | SAP Hybris `.pdc_` | Seletor especial + JSON-LD não disponível |

### Prioridade 4: Adicionar Delays Anti-Bot

```python
# Em BaseScraper._human_scroll():

# Entre páginas
await page.wait_for_timeout(random.randint(3000, 8000))

# Entre dealers
await page.wait_for_timeout(random.randint(5000, 12000))

# Simular scroll natural
for _ in range(random.randint(2, 5)):
    await page.evaluate("window.scrollBy(0, window.innerHeight)")
    await page.wait_for_timeout(random.randint(500, 1500))
```

---

## 📋 Plano de Implementação

### Fase 1: Infra Básica (2-3 dias)
- [ ] Adicionar Playwright como método principal (não fallback)
- [ ] Implementar stealth mode
- [ ] Adicionar retry decorator com exponential backoff
- [ ] Testes unitários para cada dealer

### Fase 2: Dealers Críticos (3-5 dias)
- [ ] Frigelar (OCC + CEP injection)
- [ ] CentralAr (SAP Hybris + `.pdc_product-item`)
- [ ] Leveros (JSON-LD priority)
- [ ] Dufrio (VTEX split price)

### Fase 3: Dealers Altos (2-3 dias)
- [ ] WebContinental, FrioPecas, Climario (VTEX padrão)

### Fase 4: Dealers Médios + Validação (3-4 dias)
- [ ] PoloAr, GoCompras, ArCerto, NorteRefrigeracao
- [ ] Testes de carga e performance
- [ ] Validar UnicaAR / Livaar

### Fase 5: Deploy (1 dia)
- [ ] Merge para main
- [ ] Deploy em Oracle VM
- [ ] Monitoramento 24h

**Timeline Total:** ~10-14 dias

---

## 🎯 Métricas de Sucesso

| Métrica | Meta | Atual |
|---------|------|-------|
| % Dealers Funcionando | 100% | 0% |
| Tempo médio por dealer | <60s | - |
| Produtos/dealer/coleta | 10-30 | 0 |
| Taxa de erro < | 5% | 100% |
| Uptime > | 95% | 0% |

---

## 📝 Checklist Técnico

### Para cada dealer, validar:

- [ ] URL acessível via Playwright
- [ ] JavaScript renderiza corretamente
- [ ] Seletores DOM encontram 10+ produtos
- [ ] JSON-LD extrai preços (se disponível)
- [ ] Fallback para seletores genéricos
- [ ] Paginação funciona
- [ ] Sem falsos positivos (spam/UI)
- [ ] Delays anti-bot implementados
- [ ] Testes passam

---

## ⚠️ Alertas e Restrições

1. **Cloudflare:** Alguns dealers podem ter reCAPTCHA → testar com headless off
2. **Akamai Bot Manager:** CentralAr conhecida por ter validações estritas → delay agressivo
3. **Limites de taxa:** Respeitar `wait_for_timeout` entre requisições
4. **Oracle VM:** 1GB RAM → Playwright usa ~150-200MB por browser → Max 4-5 concurrent

---

## 🔗 Referências Relacionadas

- `.claude/COMMON_MISTAKES.md` → Anti-patterns de scraping
- `docs/learnings/anti-bot-strategies.md` → WAF bypass patterns
- `scrapers/base.py` → Implementação de stealth + browser pooling
- `config.py` → Delays e timeouts configuráveis

---

*Relatório gerado: 2026-05-12 / Claude Code v4.5*
