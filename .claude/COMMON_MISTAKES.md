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
`RAC_LOCAL_CHROME=1`. Só a Shopee precisa de login; ML/CB/Magalu não (Jul/2026:
o Mercado Livre também passou a usar este Chrome real via `RAC_LOCAL_CHROME` —
seu "login gate" era acionado pelo Playwright lançado do zero, não por conta).
**Files:** `scrapers/local_browser.py`, `scripts/setup_local_profile.py`,
`scrapers/mercado_livre.py` `_launch()`/`_close()`, `docs/COLETA_LOCAL_AUTENTICADA.md`

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

## 13. Caractere não-ASCII em .ps1 quebra o parse no Windows PowerShell 5.1

**Wrong:** Travessão (—), aspas tipográficas ou acentos em STRINGS de `.ps1`
(em `.bat` vale para o arquivo inteiro — ver commit 33a2af6).
**Why:** O PowerShell 5.1 lê `.ps1` SEM BOM como ANSI (cp1252). O travessão
UTF-8 (`E2 80 94`) vira `â€"` — e o byte `0x94` decodifica como ASPA
tipográfica (U+201D), que o parser aceita como delimitador de string: a string
fecha no meio, o resto vira "código" e as chaves desbalanceiam
(`MissingEndCurlyBrace` apontando para um bloco correto). Em comentário `#` é
inofensivo (só vira mojibake) — por isso o bug passa despercebido até alguém
usar o caractere numa string. Incidente 12/07: um travessão numa `Write-Error`
do setup impediu o re-registro das tarefas no notebook.
**Right:** Scripts Windows (`.ps1`/`.bat`) 100% ASCII — sem acento, travessão
vira `-`. Validar antes de commitar: `grep -nP '[^\x00-\x7F]' script.ps1`.
**Files:** `scripts/setup_local_scheduler.ps1`, `scripts/check_local_scheduler.ps1`

## 8. Amazon Seller Field Captures Rating

**Wrong:** Use `.a-size-small.a-color-base` selector for seller name.
**Why:** Rating text "4,5 de 5 estrelas" matches the same class.
**Right:** Use `_extract_seller()` with text pattern matching: "Vendido por" split, `por ` prefix, length guards.
**Files:** `scrapers/amazon.py` `_extract_seller()`

## 14. Mercado Livre Login Gate — UA de Firefox num browser Chromium (Jul/2026)

**Wrong:** Manter um User-Agent de Firefox em `config.USER_AGENTS`.
**Why:** `BaseScraper._launch()` só lança engines Chromium reais (`channel`
"chrome" -> "msedge" -> chromium puro) — nunca Firefox. Quando o UA Firefox
era sorteado (~1/5 execuções, escolhido 1x em `__init__` e reusado pela run
inteira), o contexto tinha TLS handshake + `window.chrome`/plugins/permissions
de Chromium real mas `navigator.userAgent` anunciando Firefox — mismatch
clássico de bot detection. Resultado: `_is_login_gate()` disparava em quase
100% das keywords da run inteira ("Login gate detectado").
**Right:** `USER_AGENTS` só com UAs Chrome/Edge (Chromium-family), consistente
com o engine que `_launch()` de fato sempre lança.
**Files:** `config.py` `USER_AGENTS`, `scrapers/base.py` `_launch()`

## 15. Detectar bloqueio por STRING no HTML sem olhar se a página tem produto

**Wrong:** `return "Para continuar, acesse sua conta" in page.content()` (ou
qualquer `in html`) como veredito de gate/bloqueio.
**Why:** `page.content()` é o DOM inteiro — header, modais ocultos, JSON de
estado e `<script src="/gz/webdevice/...">` (device fingerprint que o ML carrega
em TODA SERP). A SERP normal casa essas strings. Em 31/07/2026 a coleta do ML
deu **0 produto em 100% das keywords** com Chrome real, perfil dedicado e IP
residencial: a página vinha cheia de cards e era descartada como "login gate",
3 tentativas por keyword, 40 keywords.
**Right:** **evidência antes de string.** 1) URL de gate → é gate; 2) tem card
de produto no HTML (`_SERP_CARD_RE`) → NÃO é gate, ignore qualquer frase; 3) sem
card + frase de login/captcha → gate. E todo gate persistente grava
`logs/ml_gate_<kw>_p<N>.html` — sem evidência salva não dá para distinguir
bloqueio real de falso positivo do detector.
**Files:** `scrapers/mercado_livre.py` `_gate_reason()`, `_has_serp_cards()`,
`_dump_gate_evidence()`; `scripts/diagnose_ml.py` `detect_block()`

## 16. Instruir o usuário a capturar uma sessão que o scraper não lê

**Wrong:** o log do gate mandava rodar `session_grabber.py --site mercadolivre`,
mas nenhum ponto do `MLScraper` abria `utils/sessions/mercadolivre.json`.
**Why:** remédio que não é consumido por ninguém vira tempo perdido e esconde
a causa real — o usuário faz o passo, nada muda, e a hipótese "sessão" é
descartada por engano.
**Right:** `_inject_saved_session()` aplica os cookies salvos no contexto
(fora do modo Chrome local, onde quem guarda o login é o perfil), com
`_sanitize_cookies()` normalizando `sameSite` do CDP. O log de início diz se a
sessão está **ANÔNIMA** ou **AUTENTICADA** — antes disso, nem dava para saber.
**Files:** `scrapers/mercado_livre.py` `_inject_saved_session()`,
`_log_session_state()`, `scripts/setup_local_profile.py --site mercadolivre`

## 17. ML `/gz/account-verification`: insistir com o MESMO device id

**Wrong:** Ao ver `lista.mercadolivre.com.br/...` redirecionar para
`www.mercadolivre.com.br/gz/account-verification?go=...`, recarregar a SERP
keyword após keyword com os mesmos cookies.
**Why:** `_d2id` é o id de DISPOSITIVO do ML. Uma vez marcado, TODA navegação
para `/lista` cai na verificação — a home continua abrindo normalmente, o que
faz parecer "site ok, scraper quebrado". Em 31/07 foram 40 keywords × 3
tentativas contra o mesmo cookie queimado.
**Right:** `_reset_anonymous_identity()` — sessão anônima não tem nada a
preservar: limpa os cookies do domínio ML (com filtro de `domain`, senão
apagaria a sessão da Shopee no Chrome compartilhado), reaquece a home e segue
com device id novo. Uma vez por run, e NUNCA quando logado (deslogaria o
usuário). Antídoto definitivo: logar o perfil
(`setup_local_profile.py --site mercadolivre [--auto]`) ou API oficial.
**Files:** `scrapers/mercado_livre.py` `_reset_anonymous_identity()`,
`_try_solve_verification()`

## 18. Persistência em cadeia: `astype("Int64")` derruba CSV, histórico e banco

**Wrong:** exportar CSV primeiro, sem `try/except`, e só depois gravar
histórico frio e Supabase — todos no mesmo fluxo linear. E tipar contagem com
`pd.to_numeric(col, errors="coerce").astype("Int64")`.
**Why:** `astype("Int64")` recusa **qualquer** valor que não sobreviva à ida e
volta por int64: fracionário, ou grande demais para caber em float64 sem perda.
Uma célula ruim vira `TypeError` no meio de `_export_csv`, a exceção sobe por
`main()` e nem histórico nem Supabase chegam a rodar. Run #174 (Ago/2026):
1h22m de scraping, 6.047 registros (960 Google Shopping + 3.500 Amazon + 1.587
Leroy Merlin), **tudo perdido** por causa de uma coluna.
**Right:** três coisas, juntas. 1) `_to_nullable_int()` — conversão tolerante
que arredonda o fracionário e descarta o que não cabe, em vez de levantar;
2) **dump bruto primeiro** (`output/raw_<RUN_ID>.csv`), sem transformação
nenhuma, para que o dado nunca dependa do sucesso da formatação; 3) cada etapa
de persistência em `try/except` próprio, com as falhas acumuladas e o exit code
!= 0 só **depois** de tentar todas. Na origem, `parse_review_count()` devolve
int sempre (resolve "mil"/"k" e ignora os dígitos da nota) e `_coerce_count()`
avisa com plataforma + keyword quando arredonda.
**Files:** `main.py` `_to_nullable_int()`, `_dump_raw_records()`, seção
"Exporta resultados"; `utils/text.py` `parse_review_count()`;
`scrapers/base.py` `_coerce_count()`; `tests/test_csv_int_cast.py`

## 19. Chave `anon` no `.env` — a coleta sobe, a automação morre em silêncio

**Wrong:** colar em `SUPABASE_KEY` a chave `anon`/`publishable` (é a que o
painel do Supabase mostra primeiro) e confiar no "✓ Conexão estabelecida".
**Why:** o `statement_timeout` é por PAPEL (migration 007: anon 3s ·
authenticated 8s · service_role 120s) e a RLS só é ignorada pelo `service_role`.
Como `coletas` está SEM RLS, a chave anon insere os 2.921 registros sem um
arranhão — o estrago aparece depois e sem relação aparente: `seed_depara`
morrendo com `57014 canceling statement due to statement timeout` em 3,1s
(os dois anti-joins do seed levam ~2,4s medidos: cabem em 120s, estouram em 3s)
e **`bestsellers`, a única tabela com RLS ligada e sem policy, recusando toda
escrita**. Lido de fora, parece "banco lento" — e leva a mexer em índice.
**Right:** `service_role` no `.env` de cada máquina. O papel agora é logado a
cada run (`[Supabase] Chave: service_role...` ou o aviso), o 57014 imprime a
causa provável, e `scripts\check_local_scheduler.ps1` reprova a chave errada.
**Files:** `utils/supabase_client.py` `_key_role()`, `_log_key_role()`;
`utils/admin_automation.py` `_log_timeout_hint()`;
`scripts/check_local_scheduler.ps1`

## 20. Afirmar "3P" quando o card do ML não diz quem vende

**Wrong:** `tipo_seller = "Loja Oficial" if evidence else "3P"`.
**Why:** o ML só imprime `.poly-component__seller` quando quer mostrar a loja —
na prática, quase sempre loja oficial. Na coleta de 11/08/2026 a cobertura de
`seller` bateu EXATAMENTE a de `oficial` em todas as keywords (35/60, 53/60,
59/60): todo card sem nome de vendedor também não tinha selo. O `else "3P"`
carimbava esses 40% da SERP como marketplace terceiro sem nenhuma evidência —
o mesmo erro que já tinha feito `buy_box_seller` cair para "Mercado Livre" e o
próprio ML aparecer como 2º maior buy box seller da categoria.
**Right:** `"Loja Oficial"` com selo, `"3P"` com seller nomeado e sem selo,
`None` quando o card não informa. Ausência de dado é dado ausente, não 3P.
**Files:** `scrapers/mercado_livre.py` `_detect_tipo_seller()`;
`tests/test_ml_parse.py::TestDetectTipoSeller`
