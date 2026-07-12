# Common Mistakes — RAC Position Tracker

## 1. VTEX Price Not Extracted (0% price)

**Wrong:** Rely only on CSS selectors for VTEX sites.
**Why:** VTEX IO loads prices via separate fetch AFTER DOMContentLoaded. CSS selectors find empty elements.
**Right:** Use 5-level fallback: CSS selectors -> VTEX split price (currencyInteger+Decimal) -> [data-price] -> meta[itemprop="price"] -> JSON-LD schema.org/Product -> regex R$.
**Files:** `scrapers/dealers.py` `_extract_price_el()`, `_extract_jsonld_prices()`, `_jsonld_match()`

## 2. Google Shopping Title — Web Component Shadow DOM (atualizado mai/2026)

**Wrong:** Use `aria-label` ou CSS selectors como `.gkQHve` via `select_one()` para títulos.
**Why (original):** aria-label concatena "nome + R$ preço + seller".
**Why (mai/2026):** Google encapsula o título em `<product-viewer-entrypoint>` (Web Component). BeautifulSoup NÃO consegue navegar dentro de custom elements via CSS selectors — `item.select_one('.gkQHve')` retorna None mesmo que o elemento exista no HTML serializado pelo Playwright.
**Right:** Use leaf-div relaxado: `<div>` sem filhos-div (independente de ter classe ou não), 15-200 chars, sem R$/\n/\xa0. Isso captura `.gkQHve` e `.SsM98d` que ficam dentro do Web Component mas são acessíveis via `find_all("div")`.
**Files:** `scrapers/google_shopping.py` `_extract_title()` estratégias 1 e 1b.

## 3. Selector Returns Too Many Items (Leveros 775 bug)

**Wrong:** Use broad selectors like `[class*="product-card"]` without validation.
**Why:** Many sites use "product-card" class for UI components beyond the main grid (sidebars, recommendations, carousels).
**Right:** `_detect_items()` has max_items=120 sanity check. Use `item_selector_candidates` (list) in DEALER_CONFIGS for sites with known layouts.
**Files:** `scrapers/dealers.py` `_detect_items()`

## 4. parse_price Fails on Non-Breaking Space

**Wrong:** Assume `\s` in regex matches all whitespace.
**Why:** Google Shopping uses `\xa0` (non-breaking space) in prices like "R$\xa02.184,05".
**Right:** Include `\xa0` explicitly: `re.sub(r"[R$\s\xa0]", "", raw)`.
**Files:** `utils/text.py` `parse_price()`

## 5. Magalu CAPTCHA After ~25 Keywords

**Wrong:** Use same browser context for all keywords.
**Why:** Radware Bot Manager builds fingerprint profile across requests; triggers CAPTCHA at ~25.
**Right:** `_rotate_browser()` every 15 keywords (proactive) + detect `<title>Radware Bot Manager Captcha</title>` and rotate on detection.
**Files:** `scrapers/magalu.py` `_is_radware_blocked()`, `scrapers/base.py` `_rotate_browser()`

## 6. One Scraper Crash Kills Entire Run

**Wrong:** No try/except around `_run_scraper()` in the main loop.
**Why:** If browser launch fails for one scraper (e.g., Leroy Merlin), `__enter__` raises and stops everything.
**Right:** Each `_run_scraper()` call is wrapped in try/except in `main.py main()`.
**Files:** `main.py` lines 329-345

## 7. Dealer Carousel Duplicates

**Wrong:** Dedup key includes position_organic — carousel images get different positions.
**Why:** Sites like Leveros show N gallery images per product as N DOM elements with same title.
**Right:** `seen_titles_this_page` set in `_parse_results_dom()` + `_deduplicate()` key is (platform, title) WITHOUT position. Positions reatributed after dedup.
**Files:** `scrapers/dealers.py`

## 8b. Casas Bahia — Session curl_cffi nova por request (warm-up perdido)

**Wrong:** Criar uma `_cffi_requests.Session()` nova dentro de cada GET de API.
**Why:** O Akamai vincula o cookie `_abck` à sessão TLS que o emitiu. O warm-up
na home só serve se a chamada de API seguinte reusar a MESMA session com os
cookies frescos. Session nova por request descarta o warm-up → 403/HTML.
**Right:** `_get_warmed_session()` cria UMA session, injeta cookies manuais
(opcional), faz GET na home (Akamai emite `_abck`/`bm_sz`/`ak_bmsc`) e cacheia
por ~10min. Todas as chamadas de API reusam essa session.
**Files:** `scrapers/casas_bahia.py` `_get_warmed_session()`, `_vtex_cffi_search()`

## 8c. Shopee — API v4 sem sessão/proxy (90309999)

**Wrong:** Bater na API v4 sem cookies ou de IP de datacenter e esperar dados.
**Why:** error=90309999 = anti-fraude; falta o header `af-ac-enc-dat` (gerado
pela JS) e/ou o IP é datacenter (marcado antes do fingerprint).
**Right:** Carregar sessão capturada (`session_grabber.py --site shopee`:
cookies `SPC_*`+`csrftoken`), replay via curl_cffi (impersonate chrome124),
throttle 3-7s/página. É **best-effort sem proxy BR** — re-capturar sessão
periodicamente. `captcha_hit=True` aborta keywords restantes.
**Files:** `scrapers/shopee.py` `_fetch_page()`, `_log_api_error()`

## 9. Insights de buy box — usar `_build_record` novos campos

**Wrong:** Continuar só com `seller`/`price` ao adicionar um scraper.
**Why:** O foco agora é buy box/seller. `_build_record` aceita `buy_box_seller`,
`qtd_sellers`, `tipo_seller`, `reputacao_seller`; o DB tem essas colunas
(migration 003) e o upload degrada gracioso se faltarem.
**Right:** Preencher os campos de insight quando a plataforma os expõe (VTEX
`sellers[]`, official_store_id do ML, is_official_shop da Shopee, etc.).
**Files:** `scrapers/base.py` `_build_record()`, `utils/supabase_client.py` `_COLUMN_MAP`

## 10. Coleta local: Chrome COMUM + CDP (não perfil copiado, não Playwright launch)

**Wrong:** (a) `--remote-debugging-port` no perfil PADRÃO (Chrome 136+ ignora);
(b) COPIAR o perfil pra outra pasta (proteção "perfil realocado" DESLOGA →
Shopee 403); (c) abrir o Chrome via `launch_persistent_context` do Playwright
(sobe com flags de automação/`navigator.webdriver` → Akamai 403 e Google recusa
o login).
**Why:** Google só bloqueia login em browser AUTOMATIZADO; Akamai detecta o CDP
`Runtime.enable` do Playwright stock.
**Right:** Abrir um Chrome COMUM (sem flags de automação) num perfil DEDICADO e
estável (`data/chrome_profile`, não é cópia) com a porta de debug, e ATACAR via
`connect_over_cdp` com **rebrowser-playwright** (oculta o `Runtime.enable`). No
login, nenhum cliente CDP está conectado → Google passa. Ligue com
`RAC_LOCAL_CHROME=1`. Só a Shopee precisa de login; CB/Magalu não.
**Files:** `scrapers/local_browser.py`, `scripts/setup_local_profile.py`,
`docs/COLETA_LOCAL_AUTENTICADA.md`

## 11. Task Scheduler — Action `cmd /c "..." >> "log"` morre com espaço no caminho

**Wrong:** Registrar tarefa com Action `cmd.exe /c "C:\...\script.bat" args >> "C:\...\log" 2>&1`.
**Why:** Com 4 aspas + `>>`, o cmd.exe descarta a PRIMEIRA e a ÚLTIMA aspas do `/c`.
O caminho do projeto tem espaço (`C:\Users\Eder Rabelo\...`) → o comando vira
`C:\Users\Eder ...` → a tarefa falha na hora (LastTaskResult=1) **sem escrever
log nenhum**. Foi a causa de RAC_Local_* (Magalu/Shopee/CB) "não rodar" enquanto
a tarefa do ML (bat direto via schtasks) sempre funcionou.
**Right:** Action = o próprio `.bat` (Execute com aspas embutidas, Argument só o
slot `manha`/`noite`), e o log feito DENTRO do .bat (`>> logs\scheduler.log`),
como `collect_manha.bat`. Diagnóstico: `scripts/check_local_scheduler.ps1`.
**Files:** `scripts/setup_local_scheduler.ps1`, `scripts/run_local_scheduled.bat`

## 12. .bat que dá `git pull` em si mesmo corrompe o parse do cmd

**Wrong:** Rodar `git pull` num .bat e deixar linhas executáveis DEPOIS do pull
no mesmo arquivo (ou alterar esse .bat no repo achando que é inofensivo).
**Why:** O cmd.exe lê o .bat em execução por offset de bytes; o pull troca o
arquivo no meio e o parse corrompe ("- foi inesperado neste momento.").
**Right:** Estágio A estável (`run_local_scheduled.bat`): um ÚNICO bloco entre
parênteses (o cmd parseia o bloco inteiro ANTES de executar → sobrevive ao
próprio pull) que faz o pull e chama o estágio B
(`local_scheduled_collect.bat`), lido SÓ depois do pull. Toda lógica que evolui
(janela de turno, marcador, alerta) mora no estágio B — nunca no A.
**Files:** `scripts/run_local_scheduled.bat`, `scripts/local_scheduled_collect.bat`

## 8. Amazon Seller Field Captures Rating

**Wrong:** Use `.a-size-small.a-color-base` selector for seller name.
**Why:** Rating text "4,5 de 5 estrelas" matches the same class.
**Right:** Use `_extract_seller()` with text pattern matching: "Vendido por" split, `por ` prefix, length guards.
**Files:** `scrapers/amazon.py` `_extract_seller()`
