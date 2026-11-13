# MAPPING_auto — tools do MCP Nubimetrics × fontes (heurístico; revisado em MAPPING.md)

Gerado por `python -m nubimetrics.discover` em 13/11/2026 15:33 a partir de `tools/list` real (24 tools). Categoria de teste `MLB1646`, mês atual.

> A coluna **Fonte** é heurística (nome/descrição da tool) — **a confirmar** na revisão.

## Tools

| Tool | Fonte (seção 2) | Teste | Registros | Grão | Paginação | Campos retornados (1º registro) |
|---|---|---|---|---|---|---|
| `meli_category_detail` | C, D, I, N | ok | — | mês | — | `data`, `data_available`, `currency`, `period_applied`, `period_fallback` |
| `meli_category_explorer` | C, D, G, H, I, M | ok | 50 | mês | — | `name`, `path`, `scoring`, `gmv_scoring`, `visits_scoring`, `listings_scoring`, `item_quantity_scoring`, `catalog_scoring`, `growth_ytd`, `growth_yoy`, `visits`, `competitivity`, `has_sales`, `saturation_platinum`, `saturation_gold`, `saturation_silver`, `saturation_normal`, `peak_month`, `low_month`, `sellers`, `successful_items`, `avg_ticket` |
| `nubi_category_forecast` | H, I, M | ok | 12 | mês | — | `date`, `successful_items_forecasted`, `successful_items_growth_yoy`, `gmv_usd_forecasted`, `gmv_local_forecasted`, `gmv_usd_growth_yoy`, `gmv_local_growth_yoy`, `gmv_local_before_forecasted` |
| `nubi_category_market_size` | I, M | ok | — | mês | — | `category_id`, `currency`, `date_from`, `date_to`, `data_available`, `gmv_local`, `gmv_usd`, `successful_items`, `avg_ticket_local`, `avg_ticket_usd`, `averages` |
| `nubi_category_market_structure` | I, M | ok | — | mês | — | `category_id`, `date_from`, `date_to`, `data_available`, `visits`, `sellers`, `sellers_professional`, `listings`, `successful_listings`, `averages` |
| `nubi_competition_publication_detail` | L | sem args: member_owner, member_code (dependência sem dado) | — | dia | — | — |
| `nubi_competition_publication_evolution` | L | sem args: member_owner, member_code (dependência sem dado) | — | dia | — | — |
| `nubi_competition_publication_group_members` | L | ok | 0 | — | — | — |
| `nubi_competition_publication_group_metrics` | F, L | ok | — | dia | — | `total_gmv`, `total_units`, `total_visits`, `avg_price`, `member_count`, `date_from`, `date_to`, `currency` |
| `nubi_competition_publication_groups` | J, L | ok | 1 | — | — | `group_id`, `group_name`, `group_description`, `is_favorite`, `member_count` |
| `nubi_competition_publication_log` | L | sem args: member_owner, member_code (dependência sem dado) | — | — | — | — |
| `nubi_competition_seller_evolution` | G, J | ok | 5 | dia | — | `name`, `points` |
| `nubi_competition_seller_group_members` | J, M | ok | 1 | — | — | `member_code`, `active`, `tracking_information`, `is_self` |
| `nubi_competition_seller_group_metrics` | J | ok | — | dia | — | `total_gmv`, `total_units`, `avg_ticket`, `member_count`, `date_from`, `date_to`, `currency` |
| `nubi_competition_seller_groups` | J, L | ok | 1 | — | — | `group_id`, `group_name`, `group_description`, `is_favorite`, `member_count` |
| `nubi_competition_seller_items` | J | ok | 10 | dia | — | `item_id`, `title`, `gmv`, `units`, `price`, `condition`, `is_catalog`, `category_id`, `free_shipping`, `mercado_envios`, `listing_type_name`, `brand` |
| `nubi_competition_seller_metrics` | G, J | ok | — | dia | — | `gmv`, `visits`, `units`, `items`, `avg_ticket`, `convertion`, `category_id`, `last_update`, `currency`, `category_breakdown`, `pareto` |
| `nubi_evaluate_categories` | C, I | ok | 0 | mês | — | — |
| `meli_market_rankings` | C, I | ok (5/5 variantes: items, brands, catalog, seller, official_store) | 99 | mês | — | `position`, `name`, `gmv`, `units`, `is_catalog`, `ranking_trend`, `full`, `growth_gmv`, `growth_si`, `sellers_quantity`, `items_quantity`, `catalog_percentage`, `share` |
| `meli_publication_explorer` | G, I, M | ok | 10 | — | — | `title`, `gmv`, `units`, `cum_units`, `price`, `is_catalog`, `full`, `flex`, `free_shipping`, `brand`, `seller`, `creation_date`, `exposition`, `installments`, `model`, `official_store_id`, `official_store_alias`, `item_id`, `seller_id_hash`, `sku`, `gtin`, `part_number`, `oem`, `category_path`, `category_name` … (+1) |
| `meli_search_trends_rankings` | C, G, I | ok | 76 | mês | — | `position`, `search_term`, `ranking_trend`, `position_before` |
| `nubi_seller_info` | M | ok | — | — | — | `seller_id`, `seller_nickname`, `site_id`, `plan_id`, `plan_name`, `due_date`, `currency`, `account_id`, `competition_slots`, `category_segment`, `allowed_categories` |
| `nubi_user_feedback` | F, M | pulada (grava do lado da Nubimetrics) | — | — | — | — |
| `nubi_user_info` | M | omitida (e-mail/conta — não versionada) | — | — | — | — |

## Cobertura das fontes da seção 2

| # | Fonte | Tool(s) | Situação |
|---|---|---|---|
| A | Visão global (evolução das categorias) | — | **indisponível via MCP** |
| B | Produtos mais vendidos | — | **indisponível via MCP** |
| C | Rankings de mercado (demanda/anúncios/catálogo/marcas/vendedores/lojas) | `meli_category_detail`, `meli_category_explorer`, `nubi_evaluate_categories`, `meli_market_rankings`, `meli_search_trends_rankings` | candidata — confirmar |
| D | Analisar uma categoria (subcategorias) | `meli_category_detail`, `meli_category_explorer` | candidata — confirmar |
| E | Evolução Brandshare (13 meses) | — | **indisponível via MCP** |
| F | Relatórios dinâmicos (publicações/categorias/atributos) | `nubi_competition_publication_group_metrics`, `nubi_user_feedback` | candidata — confirmar |
| G | Top buscas do mês | `meli_category_explorer`, `nubi_competition_seller_evolution`, `nubi_competition_seller_metrics`, `meli_publication_explorer`, `meli_search_trends_rankings` | candidata — confirmar |
| H | Forecasting | `meli_category_explorer`, `nubi_category_forecast` | candidata — confirmar |
| I | Explorador de anúncios/categorias | `meli_category_detail`, `meli_category_explorer`, `nubi_category_forecast`, `nubi_category_market_size`, `nubi_category_market_structure`, `nubi_evaluate_categories`, `meli_market_rankings`, `meli_publication_explorer`, `meli_search_trends_rankings` | candidata — confirmar |
| J | Grupo de vendedores / analisar concorrente | `nubi_competition_publication_groups`, `nubi_competition_seller_evolution`, `nubi_competition_seller_group_members`, `nubi_competition_seller_group_metrics`, `nubi_competition_seller_groups`, `nubi_competition_seller_items`, `nubi_competition_seller_metrics` | candidata — confirmar |
| K | Lojas oficiais | — | **indisponível via MCP** |
| L | Grupo de anúncios | `nubi_competition_publication_detail`, `nubi_competition_publication_evolution`, `nubi_competition_publication_group_members`, `nubi_competition_publication_group_metrics`, `nubi_competition_publication_groups`, `nubi_competition_publication_log`, `nubi_competition_seller_groups` | candidata — confirmar |
| M | Meu negócio (conta própria) | `meli_category_explorer`, `nubi_category_forecast`, `nubi_category_market_size`, `nubi_category_market_structure`, `nubi_competition_seller_group_members`, `meli_publication_explorer`, `nubi_seller_info`, `nubi_user_feedback`, `nubi_user_info` | candidata — confirmar |
| N | Oportunidades | `meli_category_detail` | candidata — confirmar |
| O | Reportes Estratégicos IA | — | **indisponível via MCP** |

## Detalhe por tool

### `meli_category_detail`

Qualitative snapshot of one MercadoLibre category for one month: High/Medium/Low levels of opportunity, growth, saturation, catalog and volume; average price and its change versus the previous month; the best and worst sales months with their events; the share of listings with free shipping, Mercado Envíos, catalog and discount; and how sales split by seller medal.

Use when: the user wants to judge a category's health or competitive pressure before entering it, or needs its price level, logistics mix or seasonality peak.
Not for: revenue, units or top sellers — it returns none of them. Use nubi_category_market_size for revenue and units, and meli_market_rankings for who leads.
Requires: a category id or path — call meli_category_explorer if unknown.
Limitation: closed months only; if the requested month is the current one and has no data yet, it falls back to the previous one (period_fallback=true, period_applied says which month).
Note: data_available=false means the backend has no measurement for this category and month — data is null, not zero.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"category_id": "MLB1646", "date": "2026-11-01"}}`

### `meli_category_explorer`

Lists MercadoLibre leaf categories with market-wide indicators, to compare categories or to find a category's path before calling a category tool.

Figures are monthly averages over the last 12 closed months, except growth_ytd and growth_yoy — there is no date parameter. The *_scoring fields are Nubimetrics 1–10 scores relative to the leaf categories of the same top-level branch (10 = highest in its branch), not to the whole site: they are not comparable across branches and are not percentages, amounts or ranking positions. To compare across branches use the raw figures (visits, sellers, successful_items, avg_ticket). growth_ytd (January to the last closed month vs the same months a year earlier) and growth_yoy (last closed month vs the same month a year earlier) are unit-sales growth, capped at 1000.

Use when: the user wants to discover or compare categories (competition, growth, visits, medal mix, peak and low month, sellers, ticket), or you need the 'path' of a category for meli_category_detail, meli_market_rankings, meli_search_trends_rankings, nubi_category_market_size, nubi_category_market_structure or nubi_category_forecast.
Not for: the seller's own performance — use nubi_evaluate_categories. For one month of one category use meli_category_detail; for a monthly series use nubi_category_market_size.
Note: results can include categories this account is not allowed to query — check the path against nubi_seller_info's allowed_categories before calling a category tool.
Note: 'total' is the size of the site's whole category tree, not the number of matches — count the returned rows instead.
Note: only_my_categories narrows to categories where the seller has listings; for an account with none it returns the whole site.
Caution: fetch_all=true loads the whole tree (~10K rows) with many requests — prefer search_term, category_id_l1 or the min_/max_ filters.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"search_term": "Ar Condicionado"}}`

### `nubi_category_forecast`

Demand forecast for one MercadoLibre category: one projected point per month for the 12 months after period_applied (the requested month, or the previous one on fallback) — units and revenue (local and USD), each with its growth versus the same month of the previous year, plus the previous year's local-currency revenue for that month — and a summary for the whole calendar year (actual closed months plus projected ones).

Use when: the user asks about expected demand, future size or seasonality ahead for a category.
Requires: a category id or path — call meli_category_explorer if unknown — and date = first day of the last closed month.
Note: is_generated=false means there is no forecast model for that category — projection is empty and the accumulated_annual zeros are not real values.
Note: projections are model estimates, not measurements.
Note: not available for B2C accounts — the call is rejected whatever the category.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"category_id": "MLB1646", "date": "2026-10-01"}}`

### `nubi_category_market_size`

Monthly series for one MercadoLibre category over a month range: revenue and average ticket (each in local currency and in USD) and units sold, with every point carrying the same month of the previous year; plus the averages of each series.

Use when: the user asks how big a category is or how its revenue and units evolved, including year-over-year.
Not for: counting sellers or listings — use nubi_category_market_structure.
Requires: a category id or path — call meli_category_explorer if unknown.
Note: data_available=false means no measurement for that category and range — the zeros are not real values.
Note: this backend checks category access from a different source than nubi_seller_info, so a category listed there as allowed can still be rejected here. A rejection is stable, not a transient error — do not retry.
Note: not available for B2C accounts — the call is rejected whatever the category.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"category_id": "MLB1646", "date_from": "2025-11-01", "date_to": "2026-11-01"}}`

### `nubi_category_market_structure`

Monthly series for one MercadoLibre category over a month range: active sellers, professional sellers and listings, with every point carrying the same month of the previous year; plus the averages of each series.

Use when: the user asks how many sellers or listings compete in a category and how that changed.
Not for: visits — this backend never returns them; use meli_category_explorer. Not for revenue or units — use nubi_category_market_size.
Requires: a category id or path — call meli_category_explorer if unknown.
Note: data_available=false means no measurement for that category and range — the zeros are not real values.
Note: category access is checked from a different source than nubi_seller_info; a rejection is stable, do not retry.
Note: not available for B2C accounts — the call is rejected whatever the category.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"category_id": "MLB1646", "date_from": "2025-11-01", "date_to": "2026-11-01"}}`

### `nubi_competition_publication_detail`

Snapshot of one followed publication: title, seller, official store, category path, price, original price and discount, condition, status, Full/Flex/catalog/free-shipping flags, listing type, brand, model, SKU and last update; plus revenue, units, visits, conversion and questions over a day window ending today.

Use when: the user wants the current state and recent performance of one followed publication.
Requires: member_code and member_owner from nubi_competition_publication_group_members.
Note: gmv and units are rounded display strings such as '1.2M'. For a daily series use nubi_competition_publication_evolution; for its change history use nubi_competition_publication_log.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **sem args: member_owner, member_code (dependência sem dado)**
Args: `{"inp": {"days": 30}}`

### `nubi_competition_publication_evolution`

Daily series for one followed publication: revenue (gmv), units (si), price, visits and conversion.

Use when: the user wants to see how a followed publication evolved, or compare it with the previous period.
Requires: member_code and member_owner from nubi_competition_publication_group_members.
Note: with no data for the publication it returns no series. For a closed publication it returns only the requested window (5 series). Otherwise it returns the requested window (ending today, today included) and the previous window of the same length, 5 series each, with the names repeated — tell them apart by the dates of their points.
Note: points are strings 'YYYY-MM-DD value', ascending by date — split on the space.
Note: unlike nubi_competition_seller_evolution, this series has 'price' instead of 'items'.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **sem args: member_owner, member_code (dependência sem dado)**
Args: `{"inp": {"days": 30}}`

### `nubi_competition_publication_group_members`

Lists the publications inside one followed-publication group. Each one comes with two hashes that the publication tools need together: member_code (the publication) and member_owner (the seller who owns it).

Use when: you need to see which publications a group follows, or need member_code/member_owner for nubi_competition_publication_detail, nubi_competition_publication_evolution or nubi_competition_publication_log.
Requires: a group_id from nubi_competition_publication_groups.
Note: both values are obfuscated hashes, not MercadoLibre ids, and the output has no title — call nubi_competition_publication_detail for it.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"group_id": 549473}}`

### `nubi_competition_publication_group_metrics`

Totals for a whole followed-publication group over a day window: combined revenue, units and visits, and the average price, plus the window's dates. One summary for the group, not a per-publication breakdown.

Use when: the user wants the overall activity of a followed-publication group.
Requires: a group_id from nubi_competition_publication_groups. For one publication use nubi_competition_publication_detail.
Note: days is an exact day count ending today. total_gmv and total_units are rounded display strings such as '1.2M'. avg_price is null when no member reports a price.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"group_id": 549473, "days": 30, "category_id": "MLB1646"}}`

### `nubi_competition_publication_groups`

Lists the seller's saved groups of PUBLICATIONS they follow — each group can mix the seller's own listings and competitors' listings: group id, name, description, favorite flag and member count. The full list always includes the default 'Publicaciones Favoritas' group.

Use when: the user mentions followed or tracked publications, or you need a group_id for nubi_competition_publication_group_members or nubi_competition_publication_group_metrics.
Not for: groups of sellers — use nubi_competition_seller_groups.
Note: pass group_id exactly as returned, never the list position.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {}}`

### `nubi_competition_publication_log`

Full change history of one followed publication: one entry per change with its date, what changed (e.g. price, status, title) and the old and new values, newest first.

Use when: the user asks what changed on a followed publication and when — price moves, pauses, title edits.
Requires: member_code and member_owner from nubi_competition_publication_group_members.
Note: there is no date filter; it always returns the whole history. old_value and new_value are text even for prices.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **sem args: member_owner, member_code (dependência sem dado)**
Args: `{"inp": {}}`

### `nubi_competition_seller_evolution`

Daily series for one competitor over a day window: visits, revenue (gmv), units (si), items and conversion — one point per day, today excluded because it is still in progress.

Use when: the user asks how a competitor is trending.
Requires: seller_id_competitor from nubi_competition_seller_group_members. To compare several competitors call once per competitor.
Note: points are strings 'YYYY-MM-DD value', ascending by date — split on the space.
Note: unlike nubi_competition_publication_evolution, this series has 'items' instead of 'price' and does not include the previous period.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"seller_id_competitor": "2DA447C9EBABE53E0E8177A452EAD3C8A36826D89F05C8C14645012A52293BB6BD57B0FE291D2FA7A4AA2F62F930E0282E3B028510EA4D4AD413C567D9D9F723", "days": 30, "category_id": "MLB1646"}}`

### `nubi_competition_seller_group_members`

Lists the sellers inside one saved seller group, with the competitor hash needed by nubi_competition_seller_metrics, nubi_competition_seller_items and nubi_competition_seller_evolution.

Use when: you need to see who is in a seller group, or need a seller_id_competitor.
Requires: a group_id from nubi_competition_seller_groups.
Note: member_code is an obfuscated hash, not a MercadoLibre seller id, and the output has no seller nickname.
Note: is_self=true marks the seller's own account added to the group — nubi_competition_seller_metrics and nubi_competition_seller_evolution reject its member_code.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"group_id": 549471}}`

### `nubi_competition_seller_group_metrics`

Totals for a whole saved seller group over a day window: combined revenue, combined units and average ticket, plus the window's dates. One summary for the group, not a per-seller breakdown.

Use when: the user wants the overall size of a competitor group's activity.
Requires: a group_id from nubi_competition_seller_groups. For one competitor use nubi_competition_seller_metrics.
Note: days is an exact day count ending today; date_from/date_to echo the window.
Note: total_gmv and total_units are rounded display strings such as '539.7M' or '23.9K', not exact numbers.
See nubi://nubi_rules/obfuscation — competitor figures are approximate.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"group_id": 549471, "days": 30, "category_id": "MLB1646"}}`

### `nubi_competition_seller_groups`

Lists the seller's saved groups of OTHER SELLERS (competitors / stores they follow): group id, name, description, favorite flag and member count.

Use when: the user mentions a saved group of sellers, or you need a group_id for nubi_competition_seller_group_members or nubi_competition_seller_group_metrics.
Not for: groups of publications — use nubi_competition_publication_groups.
Note: pass group_id exactly as returned, never the list position.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {}}`

### `nubi_competition_seller_items`

One competitor's publications over a day window, ordered by revenue: title, revenue, units, price, condition, catalog flag, leaf category id, shipping flags, listing type and brand.

Use when: the user wants to see what a specific competitor sells and which of their publications perform best.
Requires: seller_id_competitor from nubi_competition_seller_group_members (an obfuscated hash, not a MercadoLibre id).
Note: gmv and units are rounded display strings such as '1.9M'. total is how many publications the window returned; page with limit/offset.
Note: this is the heaviest competition call — large windows on big catalogs can hit the 120 s timeout.
Note: categories come as ids only; nubi_competition_seller_metrics names the top-level categories.
See nubi://nubi_rules/obfuscation — competitor figures are approximate.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"seller_id_competitor": "2DA447C9EBABE53E0E8177A452EAD3C8A36826D89F05C8C14645012A52293BB6BD57B0FE291D2FA7A4AA2F62F930E0282E3B028510EA4D4AD413C567D9D9F723", "category_id": "MLB1646", "days": 30}}`

### `nubi_competition_seller_metrics`

Snapshot of one competitor over a day window: revenue, units, visits, items, average ticket and conversion; revenue split by top-level category (with names and share %); and the Pareto split (how many publications make 80% of revenue and how many make the rest).

Use when: the user wants a quick performance summary of one competitor, or where their sales concentrate.
Requires: seller_id_competitor from nubi_competition_seller_group_members.
Next step: nubi_competition_seller_evolution for the daily trend, nubi_competition_seller_items for the publications.
Note: gmv and units are rounded display strings such as '58.7M'. days is an exact day count ending today.
See nubi://nubi_rules/pareto and nubi://nubi_rules/obfuscation.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"seller_id_competitor": "2DA447C9EBABE53E0E8177A452EAD3C8A36826D89F05C8C14645012A52293BB6BD57B0FE291D2FA7A4AA2F62F930E0282E3B028510EA4D4AD413C567D9D9F723", "category_id": "MLB1646", "days": 30}}`

### `nubi_evaluate_categories`

The authenticated seller's own categories for a date range: the seller's revenue in each, the seller's ranking position in that category, and a Pareto flag marking the categories that together make 80% of the seller's revenue.

Use when: the user asks about their own category performance — where they earn most, where they lead (position 1–2) and where they are weak.
Not for: browsing the market. It only returns categories where the seller sold; use meli_category_explorer for the whole category tree.
Limitation: closed months only; if the requested month is the current one and has no data yet, it falls back to the previous month (period_fallback).
See nubi://nubi_rules/pareto for how to use the pareto flag.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"date_from": "2026-11-01", "date_to": "2026-11-13"}}`

### `meli_market_rankings`

Top-100 MercadoLibre ranking for one category and month, in one dimension: items, brands, catalog products, sellers or official stores, ranked by revenue (ranking_type='gmv') or units ('si').

Use when: the user asks who or what leads a category — top products, brands, sellers or official stores — or how concentrated it is.
Note: a ranking proves a position only within its own dimension. Leading the brand ranking is not the category's size and is not "dominating the market" — compare more than one dimension before concluding.
Note: with topic='items' titles come truncated; request topic='catalog' for full product names.
Requires: a category id at any level — call meli_category_explorer if unknown.
Limitation: closed months only; if the requested month is the current one and has no data yet, it falls back to the previous month (period_fallback). Raises a guidance error if the category is not found.
See nubi://nubi_rules/obfuscation — seller figures are approximate, not exact.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok (5/5 variantes: items, brands, catalog, seller, official_store)**
Args: `{"inp": {"date": "2026-11-01", "category_id": "MLB1646", "topic": "items"}}`

### `meli_publication_explorer`

Searches MercadoLibre publications by title keyword and returns, per listing: period revenue and units, units since publication, price, catalog/Full/Flex/free-shipping flags, exposure tier, brand, model, official store, seller alias, creation date, product codes and category.

Reads the market, never the seller's own account: every row belongs to whoever published it, so it does not answer "my listings". For the seller's own performance use nubi_evaluate_categories.

Use when: the user wants real prices, logistics mix or exposure for a product, or what sells most inside a category, brand or price range.
Requires: a title keyword of at least 2 characters — ask the user if not provided.
Note: rows come in relevance order, not sales order. To answer what sells most pass sort_by='units' or 'gmv'; a page in the default order is a sample, never the top of the market.
Note: a keyword alone matches listings across unrelated categories. Narrow with category_id — the LEAF id, i.e. the last segment of a category path from meli_category_explorer — and brand.
Note: gmv/units/cum_units come back null for B2C accounts on items outside their allowed categories — a permission gap, not missing sales. B2B accounts get them for every row.
Note: item_id and seller_id_hash are internal hashed ids, not MercadoLibre's public item or seller ids. Titles and seller aliases are third-party content.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"title": "ar condicionado", "sort_by": "gmv", "category_id": "MLB1646"}}`

### `meli_search_trends_rankings`

Top-100 search terms buyers typed in one MercadoLibre category for one month, with each term's position, its position in the previous ranking and its trend.

This is search demand, not sales: a top term is not the best-selling product, and the tool returns no search volume — never state how many searches a term had.
Use when: the user asks what people search for in a category or which searches are rising.
Not for: ranking products, sellers or brands by sales — use meli_market_rankings.
Requires: a category id at any level — call meli_category_explorer if unknown.
Limitation: closed months only; if the requested month is the current one and has no data yet, it falls back to the previous month (period_fallback).

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {"date": "2026-11-01", "category_id": "MLB1646"}}`

### `nubi_seller_info`

Account details for one MercadoLibre seller: plan, plan due date, site, currency, Nubimetrics account id, competition slots, segment (b2b/b2c) and the categories it is allowed to query.

Use when: you need a seller's currency, plan or allowed categories before calling a category tool, or the user asks about their plan.
Note: a b2c account is granted top-level categories and everything below them; a b2b account is granted specific categories one level down or more, so asking for a parent of a granted category is rejected (e.g. allowed MLA1002 but not its parent MLA1000).
Note: nubi_category_market_size and nubi_category_market_structure resolve access from another source and can reject a category listed here.
Use nubi_user_info to list the seller_ids linked to the user.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **ok**
Args: `{"inp": {}}`

### `nubi_user_feedback`

Records feedback about the product, a tool, or the data it returned, for the Nubimetrics team to review. Does not change or fix anything in this conversation.

Use when: the user explicitly complains, praises, reports a bug, or suggests something; OR you notice a problem yourself while using another tool (e.g. implausible, inconsistent, or contradictory data) — you do not need the user to say something first.

Proactively suggest this tool when the user describes a problem that looks like a product/data issue rather than something you can resolve by retrying or adjusting parameters — offer to log it before dropping the issue.

Before calling: tell the user the summary you're about to submit (it's filed under their account) and get a quick go-ahead — don't submit silently.

Do not use when: the issue is something you can fix directly (e.g. wrong category_path — correct it and retry instead of filing feedback).

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **pulada (grava do lado da Nubimetrics)**

### `nubi_user_info`

Identity of the authenticated user: email, name, user type, status, main seller account, and every MercadoLibre seller account linked to it (seller_id, nickname, site, which one is main).

Use when: you need to know who the user is, or which seller_id values exist before passing one to nubi_seller_info or to any tool's seller_id.
Not for: plan, currency or allowed categories — use nubi_seller_info.

Parâmetros:

- `inp` (object, obrigatório) — 

Teste: **omitida (e-mail/conta — não versionada)**
Args: `{"inp": {}}`

