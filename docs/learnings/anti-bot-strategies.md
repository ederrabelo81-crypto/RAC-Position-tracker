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

### Akamai Bot Manager (Magalu — ✅ resolvido Mai/2026 via curl_cffi)
- **Histórico**:
  - Out/2025: Radware Bot Manager — bypass via `_rotate_browser()` cada 15 keywords
  - Abr/2026: Migração Radware → Akamai com detecção JA3/JA4 (TLS fingerprint)
  - Abr-Mai/2026: Tentativa em Node.js+Puppeteer-stealth (`magalu_shopee/`) — falhou pelo mesmo motivo: TLS fingerprint do Chromium difere do Chrome real
  - Mai/2026: **Solução definitiva** — Python + `curl_cffi` com `impersonate="chrome124"`
- **Por que curl_cffi funciona**: replica o TLS handshake **byte por byte** do Chrome 124
  real (cipher suites, extensions, ALPN, GREASE values). Akamai inspeciona JA3/JA4 do
  ClientHello — com curl_cffi não há divergência detectável
- **Arquitetura** (scrapers/magalu.py):
  1. `cffi_session = curl_cffi.requests.Session()`
  2. Warm-up na home (`m.magazineluiza.com.br/`) — Akamai emite `_abck`/`bm_sz` válidos
  3. Extrai `buildId` do `__NEXT_DATA__`
  4. Bate em `/_next/data/{buildId}/busca/{slug}.json` (JSON puro, sem render JS)
  5. Fallback: HTML `/busca/{slug}/` + parser de `__NEXT_DATA__` embutido
- **Detecção fail-fast**: HTTP 403, response <1KB, "Pardon Our Interruption",
  "Reference #", "errors.edgesuite.net", "Acesso negado"
- **Rotação**: `_IMPERSONATIONS` rotaciona `chrome124/120/131/119` por sessão pra
  evitar repetição do mesmo JA3 fingerprint (Akamai monitora)
- **Sem browser = sem Playwright/Puppeteer**: zero overhead, ~5-10× mais rápido,
  zero detecção via JS sensor (Akamai sensor.js só roda em browser real)

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
