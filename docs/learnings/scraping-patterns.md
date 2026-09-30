# Scraping Patterns — RAC Position Tracker

## CSS Selector Fallback Chains

Every scraper uses ordered lists of CSS selectors. The first match with enough items wins.
Pattern: `_first_match(item, candidates)` returns first non-None `select_one()`.

### Item detection — `_detect_items()`
- Tries each selector in `_SELECTORS["item_candidates"]`
- Requires `_MIN_ITEMS` (3) to `max_items` (120) results
- Per-dealer overrides via `item_selector` (string) or `item_selector_candidates` (list)

### Title extraction priority
1. CSS selectors (productNameContainer, ProductName, h2, h3)
2. `img[alt]` attribute
3. `a[title]` attribute
4. `_fix_brand_concat()` — strips leading brand without space ("ElginAr..." → "Ar...")

### Price extraction — `_extract_price_el()`
5-level fallback (see QUICK_REFERENCE for full chain).
Critical: VTEX IO splits prices into 3 child elements (Integer+Separator+Digits).

## VTEX-Specific Patterns

### `__RUNTIME__` JS state
VTEX IO stores product data in `window.__RUNTIME__.queryData`.
Structure: `queryData[hash].data.productSearch.products[]`
Each product has: `productName`, `items[].sellers[].commertialOffer.Price`.
Misspelling "commertialOffer" is in VTEX's actual code.

### Pagination
VTEX uses `?page=N` (1-indexed). Default 24-48 items per page.
Some stores have "OrderBy" params (e.g., Climario: `?order=OrderByTopSaleDESC`).

## WooCommerce Patterns

### Items
`ul.products li.product` is the standard container.
Pagination: `/page/N/` path segment.

### Gotcha: Brand + Title concatenation
WooCommerce themes sometimes put brand and title in the same parent element.
`get_text()` concatenates them without space → "ElginAr Condicionado..."
Fix: `_fix_brand_concat()` checks BRANDS list for prefix without trailing space.

## JSON-LD (schema.org/Product)

Many e-commerce sites embed structured data in `<script type="application/ld+json">`.
Contains: `@type: "Product"`, `name`, `offers.price`, `offers.lowPrice`.

### Matching challenge
JSON-LD names may differ from DOM titles (formatting, abbreviations, accents).
Solution: `_jsonld_match()` uses 3 strategies:
1. Exact match after normalization (remove accents, punctuation, lowercase)
2. Containment (one string contains the other, both >15 chars)
3. Word-intersection (Jaccard ≥ 60% on words >2 chars)

### Index fallback
When record count and JSON-LD count are within ±15%, prices are assigned by position
for any remaining unmatched records. This catches edge cases where names differ too much.

## Google Shopping — Leaf-Div Strategy

Google Shopping PLAs have no stable CSS classes.
Container: `div.rwVHAc`. Title: first `<div>` with:
- No child elements (`div.find()` returns None)
- No CSS class (`div.get("class")` is None)
- Text 15-200 chars, no "R$", no "\n", no "\xa0"

Price in `span.VbBaOe` with non-breaking space: "R$\xa02.184,05".

## Magalu — nm-* Design System

Migrated 2024/2025. Old `data-testid` selectors are all gone.
Current: `li[class*="nm-product-card"]` for items.
API intercept: `/api/product-search/v3/queries/search` returns JSON.
Seller field is polymorphic: string OR dict with `.name` key.

## Leroy Merlin — Seller ID opaco (Algolia) → PDP

O índice Algolia `production_products` expõe o seller **apenas** como ObjectId
opaco em `marketplaceSellers` (ex: `["5e6fd1d90a8aa474fe271e83"]`). Não existe
nome de lojista em campo algum do hit: nada de `sellers[]`, `installmentsBySeller`,
`sellerName` ou escalares equivalentes.

Duas armadilhas já pagas:

1. **A API de seller da VTEX não existe aqui.** Leroy Merlin BR roda Next.js +
   Algolia, não VTEX — `/api/catalog_system/pub/seller/{id}` nunca devolve nome.
   Em produção o contador `resolved_via_vtex_api` ficou em **0 sobre 1.707
   registros**. Camada removida.
2. **Mapa estático não escala.** Com 1 ID mapeado à mão, ~60% dos registros
   caíam em `"3P (não identificado)"`.

Solução: a única fonte do nome é o **PDP** ("Vendido e entregue por X"). A chave
de resolução é o *seller ID*, não o produto — 1.707 registros colapsam em algumas
dezenas de IDs únicos, então basta **1 PDP por seller novo**. O resultado é
persistido em `data/leroy_sellers.json`, e a partir da 2ª coleta o custo de rede
tende a zero.

Camadas em `scrapers/leroy_merlin.py` (custo zero → custo de rede):
`LEROY_SELLER_ID_MAP` → cache em disco → cache do run → campos inline do hit →
campos escalares → **PDP** → sentinela `3P (não identificado)`.

Detalhes que importam:
- A resolução é **batch por página** (`_resolve_pending_sellers`): classifica os
  24 hits primeiro, junta os IDs únicos pendentes, só então abre PDPs.
- IDs que falham entram em quarentena (`retry_days=7`) — não se gasta um PDP por
  run com o mesmo ID morto.
- Fetch do PDP tenta `requests` e cai para o browser Playwright já aberto. O
  caminho leve **não é conclusivo**: Akamai responde HTTP 200 com interstitial
  de JS, que passa em qualquer teste de tamanho. Por isso a escada tenta o
  browser sempre que o fetch leve não produzir um *nome*, não apenas quando ele
  falha — do contrário um run bloqueado jogaria todos os sellers na quarentena
  de 7 dias sem nunca acionar o fallback.
- Os knobs precisam de `load_dotenv()` no próprio módulo: o `.env` do projeto só
  é carregado tarde (por `utils/supabase_client`, no fim do `main.py`), depois
  dos imports dos scrapers.
- Knobs: `LEROY_PDP_RESOLVE=0` desliga, `LEROY_PDP_MAX_PER_RUN` (padrão 40) limita.
- Diagnóstico: `python scripts/leroy_seller_probe.py --scan "ar condicionado lg"`
  lista os IDs de uma busca e marca quais ainda são desconhecidos.

### Buy box por produto (Set/2026)

Nome do seller e vencedor da buy box são perguntas diferentes. Evidência de
30/09/2026 (item 3962339062): `marketplaceSellers == [um 3P]`, e o PDP mostra
"Vendido e entregue por LEROY MERLIN". **A oferta 1P da Leroy não aparece em
`marketplaceSellers`.** Ter 3P listado não quer dizer que a buy box é 3P, e com
2+ sellers o índice também não diz quem vence.

- `_resolve_buybox_por_produto` abre o PDP de cada produto **ambíguo**, a cada
  execução, sem cache persistente (o vencedor é observação do turno). Ambíguo =
  tem `marketplaceSellers`, exceto código de marketplace (10 dígitos começando
  por "1") com um único seller: ali a Leroy não vende (0 de 201 produtos 1P em
  29–30/09 tinham esse código) e a buy box é do único 3P.
- O vencedor sai só do **rótulo** (`extract_buybox_winner`): texto do DOM
  hidratado, depois o payload do App Router. JSON de ofertas lista todos os
  sellers e não diz quem venceu.
- Leroy vence → linha 1P, `seller_id` None, `Qtd Sellers` + 1. 3P com um único
  ID listado → o vencedor é esse ID, e o nome vai para o cache de graça. 3P com
  2+ IDs → `seller_id` só se algum ID listado já tem esse nome; senão None.
- Sem leitura (bloqueio, orçamento, abort) → fica a classificação do índice.
  Cinco PDPs seguidos sem rótulo abortam a passada no run. O caminho `requests`
  é abandonado depois de 3 PDPs íntegros sem rótulo (shell sem hidratar).
- Knobs: `LEROY_BUYBOX_PDP=0` desliga, `LEROY_BUYBOX_MAX_PER_RUN` (padrão 200).
  Custo: ~130 PDPs por turno, com o mesmo espaçamento da resolução de nome.
- Métricas no log de cada keyword: `buybox_pdp_abertos`, `buybox_leroy`,
  `buybox_3p`, `buybox_sem_leitura`, `resolved_via_buybox_pdp`.
