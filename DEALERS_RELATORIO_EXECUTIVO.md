# Relatório Executivo: Diagnóstico de Dealers AC — Maio 2026

**Relatório Gerado:** 2026-05-12  
**Período Analisado:** 2026-04-26 a 2026-05-12  
**Responsável:** Claude Code v4.5  
**Status:** 🚫 CRÍTICO

---

## 1. Situação Atual

### Dealers Testados: 12
- ✅ **Funcionando:** 0 (0%)
- 🚫 **Bloqueados:** 11 (91.7%)
- ⏸️  **Em HOLD:** 1 (8.3%)

### Impacto Financeiro / Operacional
- **Cobertura de SKUs:** ~0% (sem captura de dados)
- **Downtime:** ~15+ dias (desde 26/04/2026)
- **Dealers Críticos Afetados:** Frigelar, CentralAr, Leveros
- **Implicação:** Dashboard vazio, sem monitoramento de preços AC especializado

---

## 2. Raiz Causa — Análise Técnica

### Problema
```
Todos os 11 dealers retornam HTTP 403 (Forbidden)
  ↓
Bloqueio por Web Application Firewall (WAF)
  ↓
Detecta requisições HTTP simples como bot
  ↓
Resultado: Acesso negado permanente
```

### Evidência Técnica

| Método | Resultado | Motivo |
|--------|-----------|--------|
| `requests` library | HTTP 403 | Headers básicos detectados como bot |
| `curl` com User-Agent | HTTP 403 | WAF bloqueia antes de servir HTML |
| Browser simulado (Playwright) | ✅ Esperado (não testado ainda) | Chromium real engana WAF |

### Dealers Bloqueados por Tipo

| Tipo WAF | Dealers | Nível |
|----------|---------|-------|
| Cloudflare | ArCerto, alguns VTEX | Alto |
| Akamai | CentralAr, VTEX IO | Crítico |
| Imperva | Possível em alguns | Médio |
| Custom | PoloAr, GoCompras | Médio |

---

## 3. Dealers Críticos — Análise Individual

### 🔴 Frigelar (Oracle OCC)
- **Status:** Bloqueado WAF
- **Importância:** ⭐⭐⭐⭐⭐ (maior especializado)
- **Complexidade:** Alta (CEP + OCC + Knockout.js)
- **Solução:** Playwright + injeção de CEP
- **ETA:** 3 dias

### 🔴 CentralAr (SAP Hybris)
- **Status:** Bloqueado WAF
- **Importância:** ⭐⭐⭐⭐⭐ (maior do Brasil)
- **Complexidade:** Alta (SAP Hybris + seletor `.pdc_product-item`)
- **Solução:** Playwright + seletor específico
- **ETA:** 2-3 dias

### 🔴 Leveros (VTEX IO)
- **Status:** Bloqueado WAF
- **Importância:** ⭐⭐⭐⭐ (médio-grande)
- **Complexidade:** Média (JSON-LD disponível: 118 produtos)
- **Solução:** Playwright + priorizar JSON-LD
- **ETA:** 1-2 dias

### 🔴 Dufrio (VTEX)
- **Status:** Bloqueado WAF
- **Importância:** ⭐⭐⭐⭐ (médio)
- **Complexidade:** Média (preço concatenado: 182900 → 1829,00)
- **Solução:** Playwright + parsing especial + JSON-LD
- **ETA:** 2 dias

---

## 4. Cronograma de Implementação

### Fase 1: Infra Base (2-3 dias)
```
[ ] Adicionar Playwright como método principal
[ ] Implementar stealth mode em BaseScraper
[ ] Configurar timeouts e delays anti-bot
[ ] Testes unitários iniciais
```
**Deliverable:** BaseScraper pronto para Playwright

### Fase 2: Dealers Críticos (3-5 dias)
```
[ ] Frigelar (OCC + CEP injection)
[ ] CentralAr (SAP Hybris + .pdc_product-item)
[ ] Leveros (JSON-LD priority)
[ ] Dufrio (VTEX split price)
```
**Deliverable:** 4 maiores dealers funcionando

### Fase 3: Dealers Altos (2-3 dias)
```
[ ] WebContinental, FrioPecas, Climario (VTEX padrão)
```
**Deliverable:** 7 dealers VTEX funcionando

### Fase 4: Dealers Médios + Validação (3-4 dias)
```
[ ] PoloAr (Custom + XHR)
[ ] GoCompras (WooCommerce)
[ ] ArCerto (WooCommerce + CF)
[ ] NorteRefrigeracao (Custom)
[ ] Validar UnicaAR / Livaar (HOLD status)
```
**Deliverable:** Todos 11 dealers testados

### Fase 5: Deploy (1 dia)
```
[ ] Merge para main
[ ] Deploy em Oracle VM
[ ] Monitoramento 24h
[ ] Rollback plan se falhar
```
**Deliverable:** Produção

**Timeline Total:** 10-14 dias  
**Start:** ASAP (prioridade máxima)  
**End:** ~2026-05-26

---

## 5. Recursos Necessários

### Pessoal
- 1x Developer sênior (Playwright + Python)
- 0.5x QA (testes manuais)

### Infraestrutura
- Oracle VM: Pode rodar Playwright (verificar RAM)
- Swap: 2GB existente é suficiente
- Storage: ~1GB para debug HTML

### Dependências
- `playwright>=1.50.0` (já no requirements.txt)
- Python 3.10+ (já instalado)

---

## 6. Métricas de Sucesso

| Métrica | Meta | Atual | Status |
|---------|------|-------|--------|
| % Dealers Funcionando | 100% | 0% | 🔴 |
| Tempo médio/dealer | <60s | ∞ | 🔴 |
| Produtos/coleta/dealer | 10-30 | 0 | 🔴 |
| Taxa de erro | <5% | 100% | 🔴 |
| Uptime | >95% | 0% | 🔴 |

**Após Implementação (esperado):**
- ✅ 11/12 dealers funcionando (91.7%)
- ✅ Tempo: 45-120s por dealer
- ✅ Volume: 100-200 produtos/coleta
- ✅ Taxa erro: <2%
- ✅ Uptime: >98%

---

## 7. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|---------------|--------|-----------|
| WAF evolui e bloqueia Playwright | Baixa | Alto | Monitorar blogs Cloudflare/Akamai |
| RAM insuficiente na Oracle VM | Baixa | Alto | Usar browser pooling (max 4-5) |
| ReCAPTCHA não resolvido | Média | Médio | Testar headless=False, desabilitar |
| Seletores mudam (redesign site) | Alta | Médio | Priorizar JSON-LD, fallbacks genéricos |
| Preços injetados via JS complexo | Baixa | Médio | Inspecionar cada site; fallback DOM |

---

## 8. Próximos Passos (Ações Imediatas)

### ✅ Já Completo
1. ✅ Diagnóstico técnico completo
2. ✅ Identificação de raiz causa
3. ✅ Análise de cada dealer
4. ✅ Plano de implementação detalhado

### 🔲 Próximo
1. **Aprovação do plano** (aguardando)
2. **Alocação de recursos** (1x dev sênior)
3. **Implementação Fase 1** (infra base Playwright)
4. **Testes Frigelar + CentralAr** (dealers críticos)
5. **Deploy progressivo** (fase por fase)

---

## 9. Documentação Relacionada

| Documento | Localização | Propósito |
|-----------|-------------|----------|
| Diagnóstico Completo | `.claude/DEALERS_DIAGNOSTICO_MAIO_2026.md` | Detalhes técnicos |
| Soluções Técnicas | `.claude/DEALERS_SOLUCOES_TECNICAS.md` | Código + implementação |
| Relatório JSON | `diagnostico_dealers_report.json` | Dados estruturados |
| Anti-patterns | `.claude/COMMON_MISTAKES.md` | O que evitar |

---

## 10. Conclusão

### Situação
- **Todos os 11 dealers estão bloqueados por WAF**
- Bloqueio é universal e afeta HTTP simples
- **Solução é tecnicamente viável:** Usar Playwright (navegador real)
- Implementação é **estimada em 10-14 dias**

### Recomendação
**Implementar Playwright imediatamente.** Este é o único método que contorna WAF de forma robusta e sustentável. Alternativas (proxy, headless evasion) são frágeis e frequentemente quebram.

### Impacto Esperado
- ✅ Retomar captura de dados de dealers
- ✅ Restaurar 91.7% da cobertura (11/12 dealers)
- ✅ Suprir necessidade de monitoramento AC especializado
- ✅ Base para expansão futura (mais dealers)

---

**Aprovado para implementação? Sim / Não**

**Assinado:**  
_Claude Code v4.5 (Gerado automaticamente)_  
**Data:** 2026-05-12

---

## Anexos

### A. Comandos de Teste
```bash
# Verificar status de um dealer
curl -s -w "%{http_code}" https://www.frigelar.com.br/split-inverter/c

# Testar com Playwright (após implementação)
python -c "from scrapers.dealers import DealerScraper; ..."

# Monitorar logs
tail -f logs/bot_*.log | grep -i dealer
```

### B. URLs dos Dealers
- Frigelar: https://www.frigelar.com.br/split-inverter/c
- CentralAr: https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER
- PoloAr: https://www.poloar.com.br/ar-condicionado/inverter?...
- GoCompras: https://www.gocompras.com.br/ar-condicionado/split-hi-wall/
- FrioPecas: https://www.friopecas.com.br/ar-condicionado/ar-condicionado-split-inverter
- WebContinental: https://www.webcontinental.com.br/climatizacao/ar-condicionado/ar-condicionado-split-hi-wall
- Dufrio: https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter
- Leveros: https://www.leveros.com.br/ar-condicionado/inverter
- ArCerto: https://www.arcerto.com/categoria/ar-condicionado-inverter/
- Climario: https://www.climario.com.br/ar-condicionado?initialMap=c...
- NorteRefrigeracao: https://www.norterefrigeracao.com.br/ar-condicionado/
- UnicaAR: https://www.unicaarcondicionado.com.br/ar-condicionado (HOLD)
