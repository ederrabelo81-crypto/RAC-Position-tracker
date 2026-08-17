# Correção do Scraper Casas Bahia - Dezembro 2026

## Problema Identificado

O scraper da Casas Bahia estava falhando devido a múltiplos problemas:

1. **Erros de Protocolo do Playwright**: `ProtocolError: Internal server error, session closed`
   - Causa: Browser sendo fechado ou reconectado durante a execução
   - Sintoma: Mensagens `[rebrowser-patches][frames._context] cannot get world`

2. **Bloqueio Akamai WAF**: IP identificado como bot após algumas keywords
   - Causa: Sessão não aquecida corretamente antes das requisições
   - Sintoma: "Ops! Algo deu errado" + novavp-a.akamaihd.net

3. **Circuit Breaker Ativado Prematuramente**: Coleta abortada após 3 keywords bloqueadas
   - Causa: Warm-up inconsistente entre keywords
   - Sintoma: `Circuit breaker: 3 keywords seguidas bloqueadas pelo Akamai`

## Correções Aplicadas

### 1. `_check_blocked()` - Proteção contra browser morto

**Arquivo**: `/workspace/scrapers/casas_bahia.py` (linha 596)

**Problema anterior**: Acessava `self._page.url` sem verificar se o browser estava vivo
```python
current_url = self._page.url  # Podia explodir se _page fosse None ou estivesse fechada
```

**Correção**: Adicionou verificação de segurança
```python
def _check_blocked(self, html: str) -> bool:
    """Detecta se a página é um bloqueio WAF/Akamai ou erro de bot."""
    if not html:
        return False

    # Verifica URL atual (se disponível) — protege contra browser morto
    try:
        if self._page and not self._page.is_closed():
            current_url = self._page.url
            if any(pat in current_url.lower() for pat in _BLOCKED_URL_PATTERNS):
                logger.warning(f"[{self.platform_name}] Redirecionado para bloqueio: {current_url}")
                return True
    except Exception:
        pass  # Browser morreu, não conseguimos checar URL
    
    # ... resto da lógica de detecção
```

### 2. `_warmup_cdp_session()` - Warm-up idempotente

**Arquivo**: `/workspace/scrapers/casas_bahia.py` (linha 1345)

**Problema anterior**: Retornava `self._cdp_warmed` mesmo quando `False`, fazendo com que 
o warm-up nunca acontecesse na primeira keyword.

**Correção**: Lógica clara de estado
```python
def _warmup_cdp_session(self) -> bool:
    if not self._real_browser_active or self._page is None:
        return False
    
    # Se já aquecido nesta sessão, não reaquece
    if self._cdp_warmed:
        return True

    # Executa warm-up...
```

### 3. Fluxo de Degradação Graceful

Quando o browser morre durante a coleta, o scraper agora:
1. Detecta via `_browser_lost = True`
2. Chama `_degrade_to_http()` para desligar modo browser
3. Continua coletando via APIs VTEX (curl_cffi)
4. Não tenta mais usar browser morto nas próximas keywords

## Como Usar Corretamente

### Opção 1: Chrome Real Local (RECOMENDADO)

Esta é a solução mais eficaz contra o Akamai:

```bash
# No Windows, onde está o Chrome instalado:
set RAC_LOCAL_CHROME=1
set RAC_CHROME_PROFILE_DIR=C:\Users\SEU_USUARIO\AppData\Local\Google\Chrome\User Data\RAC_Profile

# Execute o scraper
python main.py
```

**Vantagens**:
- Fingerprint nativo do Chrome real
- Cookies persistentes entre execuções
- Menos detectável pelo Akamai

### Opção 2: Session Grabber (Bypass Manual)

Se o Chrome local não estiver disponível:

```bash
# 1. Capture a sessão manualmente
python utils/session_grabber.py --site casasbahia

# 2. Navegue no browser que abrir até ver produtos
# 3. Resolva qualquer CAPTCHA se aparecer
# 4. Volte ao terminal e pressione ENTER

# 5. Execute o scraper (ele usará os cookies salvos)
python main.py
```

**Cookies críticos para Casas Bahia**:
- `_abck` - Cookie principal do Akamai
- `bm_sz` - Tamanho da janela (Akamai)
- `ak_bmsc` - Cookie de sessão Akamai
- `AKA_A2` - Outro cookie Akamai

### Opção 3: Proxy Residencial (Solução Definitiva)

Para produção em larga escala:

```bash
# Configure proxy residencial brasileiro no .env
RAC_PROXY_TYPE=residential
RAC_PROXY_HOST=seu-proxy.com
RAC_PROXY_PORT=8080
RAC_PROXY_USER=usuario
RAC_PROXY_PASS=senha
```

## Diagnóstico de Problemas

### Erro: "ProtocolError: Internal server error, session closed"

**Causa**: Browser foi fechado ou perdeu conexão CDP

**Solução**:
1. Use Chrome local (`RAC_LOCAL_CHROME=1`)
2. Não feche a janela do Chrome durante a coleta
3. Se usar CDP remoto, mantenha a porta 9222 acessível

### Erro: "Akamai WAF bloqueou a requisição"

**Causa**: IP/datacenter identificado como bot

**Soluções em ordem de eficácia**:
1. ✅ Chrome real local (perfil logado)
2. ✅ Session grabber renovado (< 24h)
3. ⚠️ Proxy residencial brasileiro

### Erro: "Circuit breaker: 3 keywords seguidas bloqueadas"

**Causa**: Múltiplas keywords bloqueadas consecutivamente

**Solução**:
1. Renove a sessão: `python utils/session_grabber.py --site casasbahia`
2. Aguarde 5-10 minutos antes de rodar novamente
3. Use Chrome local ou proxy residencial

## Logs de Diagnóstico

O scraper agora gera logs detalhados:

```
logs/casasbahia_debug_p1_ar_condicionado.html  # HTML da página bloqueada
```

Estes arquivos contêm o HTML retornado pelo Akamai e ajudam a identificar:
- Se foi bloqueio WAF
- Se houve redirecionamento
- Qual endpoint API falhou

## Monitoramento

Indicadores de saúde da coleta:

✅ **Coleta saudável**:
- `_abck validado pelo sensor.js ✓`
- `VTEX curl_cffi catalog: X produtos`
- `Buy box recuperada via API VTEX`

⚠️ **Atenção necessária**:
- `_abck não validou em 20s`
- `in-page VTEX sem produtos`
- `Warm-up CDP falhou`

❌ **Ação requerida**:
- `Akamai WAF bloqueou a requisição`
- `Circuit breaker: X keywords seguidas bloqueadas`
- `Browser indisponível`

## Próximos Passos

1. **Imediato**: Testar com `RAC_LOCAL_CHROME=1` no Windows
2. **Curto prazo**: Configurar proxy residencial para produção
3. **Longo prazo**: Implementar rotação de user-agents e delays mais humanos

## Contato/Suporte

Para issues relacionadas ao Akamai:
- Verificar logs em `logs/casasbahia_debug_*.html`
- Rodar session_grabber e validar cookies
- Considerar proxy residencial para escala
