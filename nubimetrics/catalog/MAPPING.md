# MAPPING — MCP Nubimetrics × fontes do módulo Sell-out (revisado)

**Base:** `tools/list` real (24 tools, servidor `nubimetrics-mcp 20261029-c5c7e44`) e
uma chamada de teste por tool, conta **Midea Brasil**, categoria `MLB1646`,
executada em 13/11/2026 no PC do mantenedor. Amostras em `samples/`, tabela
gerada automaticamente em `MAPPING_auto.md`. Nada aqui é simulado.

**Conta:** plano `BUSINESS CORPORATE FULL`, segmento **b2b**, vencimento 04/11/2027,
**1 slot de concorrência**. Categorias liberadas: Refrigeração, Fornos e Fogões,
Bebedouros e Purificadores, **Ar e Ventilação (`MLB252358`, mãe de `MLB1646`)**,
Lavadores e Pequenos Eletrodomésticos.

---

## 1. Tool × fonte da seção 2

| # | Fonte (UI) | Tool MCP | Situação | Grão / janela | Paginação / limite |
|---|---|---|---|---|---|
| A | Visão global | `nubi_category_market_size` + `meli_category_detail` | ✅ | mês; série de 13 meses com YoY; **inclui o mês corrente parcial** | — |
| B | Produtos mais vendidos | `meli_market_rankings` topic=`items` / `catalog` | ✅ parcial | mês **fechado** | top 100 (`limit`/`offset` < 100) |
| C | Rankings de mercado: Marcas | `meli_market_rankings` topic=`brands` | ✅ parcial | mês fechado | top 100 (62 marcas em out/26) |
| C | Rankings: Vendedores | `meli_market_rankings` topic=`seller` | ✅ parcial | mês fechado | top 100 |
| C | Rankings: Lojas oficiais | `meli_market_rankings` topic=`official_store` | ✅ parcial (nomes mascarados) | mês fechado | top 100 |
| C | Rankings: Demanda | `meli_search_trends_rankings` | ✅ | mês fechado | top 100 (76 termos) |
| D | Analisar categoria (subcategorias) | `meli_category_explorer` | ⚠️ só indicadores de categoria-folha | média de 12 meses fechados | `page`/`page_size`, `fetch_all` |
| E | Evolução Brandshare (13 meses) | `meli_market_rankings` brands × 13 chamadas (1 por mês) | ✅ por backfill | mês fechado | 13 chamadas |
| F | Relatórios dinâmicos (anúncio + atributos) | — (substituto: `meli_publication_explorer`) | ❌ **indisponível via MCP** | — | — |
| G | Top buscas do mês | `meli_search_trends_rankings` (já filtrado em AC) | ✅ | mês fechado | top 100 |
| H | Forecasting | `nubi_category_forecast` | ✅ | 12 meses projetados + acumulado do ano | — |
| I | Explorador de anúncios | `meli_publication_explorer` | ✅ | **sem parâmetro de data** (janela do backend) | `page` 1.., `page_size` ≤ 50; 4.057 anúncios para "ar condicionado" em `MLB1646` |
| J | Análise de concorrente | `nubi_competition_seller_metrics` / `_evolution` / `_items` + grupos | ✅ **limitado a 1 slot** | `days` até hoje; série diária | `limit`/`offset` (items); sem paginação no servidor |
| K | Lojas oficiais (análise) | — (só pelo ranking C, com nome mascarado) | ❌ indisponível via MCP | — | — |
| L | Grupo de anúncios | `nubi_competition_publication_*` (5 tools) | ✅ tools existem, **grupo vazio** | `days`; série diária | `limit`/`offset` |
| M | Meu negócio | `nubi_evaluate_categories` | ⚠️ conta Midea Brasil sem vendas próprias → vazio | período | — |
| N | Oportunidades | `meli_category_detail` / `meli_category_explorer` | ⚠️ só nível categoria | mês | — |
| O | Reportes Estratégicos IA | — | ❌ indisponível via MCP | — | — |
| — | Feedback ao Nubimetrics | `nubi_user_feedback` | 🚫 **nunca chamada** (grava do lado deles) | — | — |
| — | Conta / plano | `nubi_user_info`, `nubi_seller_info` | ✅ apoio | — | — |

## 2. Campos por tool (o que alimenta as tabelas da seção 4)

**`meli_market_rankings`** (todas as dimensões, mesmo formato):
`position, name, gmv, units, is_catalog, ranking_trend (Stable/GoingUp/GoingDown), full, growth_gmv, growth_si, sellers_quantity, items_quantity, catalog_percentage, share` + `period_fallback`, `currency`.
- `brands` → `nubi_brand_ranking`: tem share, Δ%, nº de sellers e tendência. **Não tem `saturation` nem `demand_rank`.**
- `seller` → `nubi_seller_ranking`: nickname, share e % catálogo. **Não tem medalha nem % Premium/Clássico.**
- `items`: título **truncado** (40 caracteres), **sem item_id e sem seller**. Use `catalog` para o nome completo.
- `official_store`: muitas vêm como `LOJA.OFICIAL.<n>` (mascaradas). Algumas trazem nome (Webcontinental, Dufrio).

**`meli_publication_explorer`** → `nubi_item_sales` (único grão de anúncio disponível):
`item_id` (**hash**, não MLB), `title`, `brand`, `model`, `sku`, `gtin` (9 de 10 preenchidos), `part_number`, `oem`, `seller` (nickname legível), `seller_id_hash`, `official_store_alias`, `price`, `gmv`, `units`, `cum_units`, `is_catalog`, `full`, `flex`, `free_shipping`, `exposition` (ex.: `gold_pro`), `creation_date`, `category_path`.
**Não tem:** visitas, conversão, preço original/desconto, cancelamentos, UF/cidade, atributos (BTU, inverter, voltagem, tipo).

**`nubi_category_market_size`**: séries `gmv_local`, `gmv_usd`, `successful_items`, `avg_ticket_local/usd`, cada ponto com o mesmo mês do ano anterior.
**`nubi_category_market_structure`**: `sellers`, `sellers_professional`, `listings`, `successful_listings` (`visits` volta vazio).
**`meli_category_detail`**: níveis de oportunidade/crescimento/**saturação**/catálogo/volume, preço médio e Δ, mês de pico e de vale, % frete grátis, % Mercado Envios, % catálogo, % com desconto e split de vendas por medalha (é aqui que existe "saturação", **no nível da categoria, não da marca**).
**`nubi_category_forecast`**: 12 meses projetados (unid., R$, US$, YoY) + acumulado do ano.
**`meli_search_trends_rankings`**: `position, search_term, ranking_trend, position_before` (**sem volume de busca**).
**`nubi_competition_seller_metrics`**: gmv/units (**strings arredondadas**, ex. `"3M"`, `"1.3K"`), `visits`, `items`, `avg_ticket`, `convertion`, quebra por categoria e Pareto 80/20.
**`nubi_competition_seller_evolution`**: série diária `visits`, `si`, `gmv`, `items`, `convertion` (pontos `"AAAA-MM-DD valor"`).
**`nubi_competition_seller_items`**: anúncios do concorrente por GMV, com `listing_type_name` (Premium/Clássico), catálogo, frete e marca.

## 3. Consistência verificada

- Soma das 62 marcas em out/2026 = **R$ 223,5 mi** = `market_size` de out/2026 (R$ 223,5 mi). Shares somam 100,0%.
- `market_size` nov/2026 parcial = **R$ 173,34 mi**, mesmo número da UI ("R$ 173,3M no mês atual").

## 4. Impactos no plano (decisões para o mantenedor)

1. **A amostra de validação da seção 2 não é alcançável pelo MCP.** Ela é de nov/2026 **parcial**, e os rankings só servem mês fechado (a chamada de nov voltou com `period_fallback=true`, ou seja, out/2026: LG R$ 44,6 mi, 19,96%; Midea R$ 29,7 mi, 13,3%; Clima Rio R$ 36,3 mi). Proposta: validar contra a UI de **outubro/2026**, e validar só o total da categoria contra o mês corrente.
2. **Não há grão de anúncio com atributo.** O relatório dinâmico (F) não está no MCP. O critério "≥ 85% classificado por atributo" **não é atingível**: o segmento High Wall Inverter e o BTU vão sair do **título** (regex), com `match_source='title_regex'` em ~100% das linhas. O painel mostra isso, como pedido.
3. **Sem MLB id.** `item_id` é hash. A junção com as coletas do Tracker passa a ser, nesta ordem: **GTIN** (se o Tracker passar a guardar EAN) → marca + modelo + BTU + voltagem (rapidfuzz ≥ 90) → título. A chave 1 (item_id) e a 2 (catalog_product_id) do prompt não existem.
4. **Base de anúncios = `meli_publication_explorer`.** 4.057 anúncios × 50 por página ≈ 82 chamadas por dia, sem data. Será um *snapshot* por dia de coleta (janela do backend), não uma série diária por anúncio.
5. **KPIs de seller (visitas, conversão, 80/20, % Premium) só para 1 seller por vez** (`competition_slots = 1`, hoje ocupado pelo grupo "Nicknames Favoritos"). Para o painel de parceiros, ou se contrata mais slots, ou o ETL roda os parceiros em rodízio, ou a aba Sellers fica com o que o ranking e o explorador dão (receita, share, catálogo, exposição e Full por seller).
6. **Saturação e ranking de demanda por marca não existem.** Saturação só por categoria (`meli_category_detail`). "Ranking de demanda #29" da UI não tem tool.
7. **Série diária:** só existe para concorrente/publicação seguidos (J/L). Mercado é mensal → `nubi_item_sales_daily` vira **snapshot diário do explorador** + tabelas mensais.

## 5. O que fica de fora do recorte

O grupo de publicações (`Publicaciones Favoritas`) está vazio. Por isso `nubi_competition_publication_detail/evolution/log` não foram chamadas: as tools existem e respondem, mas não houve membro para testar. Para testar, siga um anúncio no Nubimetrics e rode o discover de novo.
