# Sistema de Melhoria Contínua Adaptativa para Scrapers

## Visão Geral

Este módulo (`utils/adaptive_scraper.py`) implementa um sistema de **aprendizado contínuo** que registra o resultado de cada coleta e usa esses dados para recomendar automaticamente as melhores estratégias para cada plataforma.

## Por Que Existe

Cada plataforma (Magalu, Casas Bahia, Shopee, ML) tem diferentes níveis de proteção antibot que variam conforme:
- Horário do dia
- Dia da semana  
- Condições de rede (IP residencial vs datacenter)
- Estado da sessão (logado/expirado)
- Atualizações no layout/WAF

Um sistema rígido com parâmetros fixos sempre terá problemas quando as condições mudam. Este sistema **aprende com cada execução** e converge para os melhores parâmetros ao longo do tempo.

## Como Funciona

### 1. Coleta de Métricas
Cada scraper registra automaticamente após cada keyword:
- ✅ Sucesso ou falha
- 📊 Quantidade de itens coletados
- ⏱️ Duração da coleta
- 🎯 Estratégia usada (local_chrome, browser_first, api_only, api_oauth)
- 🚫 Tipo de erro (akamai_block, login_required, captcha, etc.)
- 🕐 Horário e dia da semana

### 2. Cálculo de Score
O sistema calcula um **score ponderado** para cada estratégia:
```python
score = sucesso_base + bonus_itens + bonus_eficiencia - penalidades
```

### 3. Recomendação Automática
Após N execuções, o sistema recomenda:
- Melhor estratégia para cada plataforma
- Wait time ótimo entre ações
- Timeout ideal
- Melhor horário para coletar

## Uso

### Consultar Configuração Recomendada

```python
from utils.adaptive_scraper import AdaptiveScraperConfig

config = AdaptiveScraperConfig.get_for_platform("Casas Bahia")
print(f"Estratégia: {config.preferred_strategy}")
print(f"Wait time: {config.recommended_wait_time_ms}ms")
print(f"Confiança: {config.confidence_level*100:.0f}%")
```

### Ver Estatísticas

```bash
# Resumo geral
python -m utils.adaptive_scraper

# Plataforma específica
python -m utils.adaptive_scraper "Casas Bahia"
python -m utils.adaptive_scraper "Magalu"
python -m utils.adaptive_scraper "Shopee"
python -m utils.adaptive_scraper "Mercado Livre"
```

### Override Manual

Se quiser forçar uma estratégia temporariamente:

```python
AdaptiveScraperConfig.set_manual_override(
    platform="Casas Bahia",
    strategy="local_chrome",
    min_wait_time_ms=1000,
    max_wait_time_ms=2000,
    timeout_sec=60
)

# Voltar ao aprendizado automático
AdaptiveScraperConfig.clear_override("Casas Bahia")
```

## Estratégias Disponíveis

| Estratégia | Descrição | Quando Usar |
|------------|-----------|-------------|
| `local_chrome` | Chrome real logado (RAC_LOCAL_CHROME=1) | IP residencial, sessões persistentes |
| `browser_first` | Browser Playwright + fallback API | Quando precisa de JavaScript |
| `api_only` | Apenas APIs (curl_cffi/requests) | Mais rápido, menos detecção |
| `api_oauth` | API oficial com OAuth (ML) | Quando há gate de captcha |

## Armazenamento

Os dados são salvos em SQLite local:
```
data/adaptive_scraper.db
```

Tabelas:
- `executions`: Histórico de todas as coletas
- `platform_config`: Overrides manuais
- `waf_events`: Bloqueios de WAF/Akamai

## Exemplo de Saída

```
============================================================
RELATÓRIO ADAPTATIVO: Casas Bahia
============================================================
Estratégia recomendada: local_chrome
Confiança: 75%
Wait time recomendado: 1500ms
Timeout recomendado: 45s
Notas: Baseado em 15 execuções (80% sucesso)

DESEMPENHO POR ESTRATÉGIA:
------------------------------------------------------------
  local_chrome ★ RECOMENDADA
    Execuções: 15
    Sucesso: 80.0%
    Média itens: 120.5
    Score: 45.23
    
  browser_first
    Execuções: 8
    Sucesso: 50.0%
    Média itens: 45.2
    Score: 12.10
    
  api_only
    Execuções: 5
    Sucesso: 20.0%
    Média itens: 0.0
    Score: -15.00
============================================================
```

## Integração com Scrapers

Os scrapers já estão integrados e registram automaticamente:
- ✅ `scrapers/casas_bahia.py`
- ✅ `scrapers/magalu.py`
- ✅ `scrapers/shopee.py`
- ✅ `scrapers/mercado_livre.py`

Basta rodar as coletas normalmente que o sistema aprende sozinho.

## Sobre IP Residencial (Sua Pergunta)

**Sim, o WiFi do notebook roteado do celular CONTA como IP residencial brasileiro.**

Operadoras móveis usam NAT carrier-grade, então seu IP aparece como:
- **Tipo**: Residential/Mobile (não datacenter)
- **Localização**: Brasil (da operadora)
- **Reputação**: Geralmente boa para scraping

**Por que ainda pode bloquear:**
1. **Fingerprint do browser**: Navigator.webdriver, WebGL, fonts, etc.
2. **Comportamento**: Requests muito rápidos/padronizados
3. **Cookies/sessão**: Ausência de cookies válidos do Akamai
4. **TLS fingerprint**: JA3/JA4 diferente do Chrome real

**Solução já implementada:**
- ✅ `RAC_LOCAL_CHROME=1` usa Chrome real com fingerprint nativo
- ✅ Perfil dedicado persiste cookies e login
- ✅ Warm-up do sensor.js valida sessão antes da busca
- ✅ Agora: sistema adaptativo aprende quais estratégias funcionam melhor

**Dica**: Após ~20 execuções, o sistema vai convergir automaticamente para a melhor combinação de:
- Estratégia (local_chrome deve vencer)
- Wait time ideal
- Horário ótimo

## Próximos Passos Sugeridos

1. **Colete normalmente por 1-2 semanas** - O sistema precisa de dados
2. **Monitore os reports** - `python -m utils.adaptive_scraper "Casas Bahia"`
3. **Ajuste manual se necessário** - Use `set_manual_override()` para testes A/B
4. **Considere proxy residencial** - Se mesmo com Chrome real o bloqueio persistir

## FAQ

**Q: Machine Learning de verdade?**
R: É um algoritmo de *multi-armed bandit* simples - estatística bayesiana básica, não rede neural. Funciona bem pra este caso e é interpretável.

**Q: Posso exportar os dados?**
R: Sim, o SQLite pode ser lido por pandas/R/excel. Futuramente podemos adicionar export CSV.

**Q: Os dados sobem pra nuvem?**
R: Não, fica tudo local em `data/adaptive_scraper.db`. Privacidade total.

**Q: E se eu reinstalar?**
R: Copie o arquivo `.db` ou comece do zero (o sistema converge em ~20 execuções).
