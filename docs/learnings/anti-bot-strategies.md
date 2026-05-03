# Anti-Bot Strategies — RAC Position Tracker

## Stealth Configuration (scrapers/base.py)

### Browser launch
- Chrome real → msedge → Chromium (in order of stealth)
- `--disable-blink-features=AutomationControlled`
- `--no-sandbox`, `--disable-infobars`

### JavaScript patches (_STEALTH_JS)
- `navigator.webdriver` = undefined
- `window.chrome` with loadTimes, csi, runtime stubs
- `navigator.plugins` = array with 5 items
- `navigator.languages` = ['pt-BR', 'pt', 'en-US', 'en']
- `navigator.permissions.query` override for notifications

### Context settings
- Random User-Agent from config.USER_AGENTS (5 modern UAs)
- Viewport: 1366x768
- Locale: pt-BR, timezone: America/Sao_Paulo

## Human Behavior Simulation

- `_random_delay(min_s, max_s)` — random sleep between actions
- `_human_scroll(steps, step_px)` — smooth scroll in increments with pauses
- `_wait_for_network_idle()` — waits for pending requests to finish
- Delays: MIN_DELAY=4s, MAX_DELAY=7s (config.py)

## Platform-Specific Anti-Bot

### Akamai Bot Manager (Magalu — ❌ bloqueado desde ~Mai/2026)
- **Status**: Magalu migrou de Radware para Akamai Bot Manager
- **Comportamento**: Retorna HTTP 200 + HTML de erro 403 "Não é possível acessar a página"
- **Detecção** (implementada): `_is_akamai_blocked()` detecta em <10s, aborta sem retry
  ```python
  # scrapers/magalu.py — check no HTML retornado
  "Não é possível acessar a página" in html or "akamai" in html.lower()
  ```
- **Log**: `🚫 Akamai Bot Manager detectado — proxy residencial brasileiro necessário`
- **Performance atual**: ~3 min até detecção e abort (antes: 10+ min em retry inútil)
- **Solução definitiva**: Proxy residencial BR — Bright Data (~$500/mês), Smartproxy, Oxylabs
- **Alternativa**: Session hijacking via CDP (captura cookies de sessão real do browser)

### Radware Bot Manager (Magalu — histórico, substituído por Akamai)
- Anteriormente ativo; substituído por Akamai em Mai/2026
- Triggers after ~25 requests in same browser context
- Detection: `<title>Radware Bot Manager Captcha</title>` or `rdaformdiv`
- Mitigation: `_rotate_browser()` every 15 keywords (proactive)
- On detection: rotate browser + retry once, skip keyword if persists

### reCAPTCHA (Frigelar)
- Category pages trigger reCAPTCHA v2 in headless
- Detection: `grecaptcha.render` in HTML (>10 occurrences)
- Mitigation: use search URL instead of category URL
- `_is_blocked_page()` detects and breaks the loop

### Google Shopping reCAPTCHA
- Dispara em coletas headless sem delays suficientes
- **Em coletas reais** (delays 25-45s, shuffle de keywords, UA rotation) funciona normalmente
- Não requer ação — comportamento esperado em smoke test

### Cloudflare (ArCerto page 2+)
- Challenges on pagination (page 1 works, page 2 blocks)
- Detection: title "Um momento" / "Just a moment" / `cf-challenge`
- Mitigation: limit max_pages=1 for affected dealers

### PerimeterX (Magalu alternate)
- Detection: `#px-captcha`, `_pxAppId` in HTML
- Response: log warning, return empty results

### Akamai Bot Manager (Casas Bahia — stand-by)
- Requires session cookies with Akamai tokens (AKA_A2, ak_bmsc, bm_sz)
- Cookie domain leading dot issue: `.lstrip(".")` before setting in curl_cffi
- Currently bypassed via session_grabber manual auth

## Browser Rotation (scrapers/base.py)

```python
def _rotate_browser(self):
    self._close()
    self._user_agent = random.choice(USER_AGENTS)  # new fingerprint
    time.sleep(random.uniform(3.0, 7.0))            # cooling period
    self._launch()
```

Called by MagaluScraper every `_ROTATION_INTERVAL=15` keywords.
Resets: cookies, TLS fingerprint, User-Agent, browser context.

## Dealer-Specific: _is_blocked_page()

Checks page title and HTML snippet for:
- reCAPTCHA: `grecaptcha.render` in HTML
- Cloudflare: title contains "um momento" / "just a moment" / `challenge-platform`
- On detection: breaks the pagination loop, dumps debug HTML, logs warning
