# Auditoria da Coleta — Preço, Oferta e Identidade de Produto

> **Escopo:** pipeline de coleta Python (7 marketplaces) vs. Price Track
> **Período analisado:** 18/08/2026 – 24/08/2026 (161.079 linhas, 39 runs)
> **Método:** leitura do código + reprodução das anomalias contra a base de produção (`coletas`)
> **Status:** Etapa 1 — diagnóstico. Nenhum arquivo de coleta foi alterado.

---

## 0. Resumo executivo

Cinco achados, em ordem de impacto sobre o número que chega ao executivo:

| # | Achado | Evidência | Severidade |
|---|--------|-----------|------------|
| **A** | **Casas Bahia grava um preço errado e congelado** para parte das ofertas — o `sellers[]` da VTEX nunca chega, e o fallback pega um campo `price` que não é o preço vigente | 42EFVCA12M5: mediana CB **R$ 5.869** vs **R$ 1.804–2.149** nas outras 5 plataformas | 🔴 Crítica |
| **B** | **Nenhum identificador de oferta é persistido.** ASIN, MLB, itemid Shopee, productId Magalu e `idLojista` da CB são extraídos, usados para montar URL e **descartados** | `_build_record` não tem campo de ID; `amazon.py:373` faz `record.pop("_asin")` | 🔴 Crítica |
| **C** | **82,8% das linhas são reobservações da mesma oferta no mesmo turno.** Não há deduplicação entre keywords para marketplaces | 161.079 linhas → 27.745 ofertas únicas/turno (medido pela `offer_key` após a Fase 1); Amazon chega a **146 linhas** para um produto num turno | 🟠 Alta |
| **D** | **Kits bi/multi-split entram na cesta como se fossem aparelhos unitários.** O título normalizado apaga o sinal de kit | Kits: mediana **R$ 5.639–17.399** vs **R$ 2.205–2.658** dos unitários. 1.673 linhas sem rótulo | 🟠 Alta |
| **E** | **A validação de preço só existe no dashboard, em tempo de leitura, e apaga a linha** em vez de classificá-la. A coleta não valida nada | `app.py` `_is_placeholder_price` / `_is_implausible_price`; nada equivalente em `scrapers/` | 🟠 Alta |

**Resultado honesto e contra-intuitivo:** deduplicar por oferta **não** reduz a
volatilidade da mediana diária (ver §6). A redundância é um problema de
**ponderação e custo**, não a causa da volatilidade. A volatilidade por marca
vem de **composição de vitrine** (D) e de **preço errado** (A) — e é isso que
os indicadores da Etapa 2 precisam decompor.

---

## 1. Mapa do fluxo: busca → extração → normalização → matching → validação → persistência

```
config.py              KEYWORDS_LIST (40 keywords), ACTIVE_PLATFORMS, BRANDS
   │
main.py::main()        gera RUN_ID (uuid, 1 por execução — main.py:541)
   │
main.py::_run_scraper()  for categoria → for keyword → scraper.search(keyword)
   │                     ⚠️ agrega com records.extend() — SEM dedup entre keywords
   │
scrapers/<plataforma>.py::search()
   │   ├── _fetch/_browser_search_page   → HTML ou payload JSON
   │   ├── _parse_results / _parse_api_products / _parse_dom
   │   │      · título   → seletor CSS ou campo do payload
   │   │      · preço    → seletor CSS ou campo do payload   ⚠️ (§3)
   │   │      · seller   → seletor CSS ou sellers[]
   │   │      · IDs      → extraídos e DESCARTADOS            ⚠️ (§2)
   │   └── _build_record(...)   ← scrapers/base.py:586
   │
scrapers/base.py::_build_record()
   │   · Data/Turno/Horário  = now_brt() no instante da linha  ⚠️ (§7)
   │   · Marca               = utils/brands.py::extract_brand()
   │   · Produto / SKU       = utils/normalize_product.py::normalize_product_name()
   │   · Produto Normalizado = normalize_product_name_v2()
   │   · Preço (R$)          = utils/text.py::parse_price()
   │   ⚠️ NÃO grava: offer_id, product_id, seller_id, price_original,
   │      price_raw_text, status de validação, snapshot de origem
   │
main.py::_dump_raw_records()   → output/raw_<RUN_ID>.csv   (rede de segurança)
main.py::_export_csv()         → output/rac_monitoramento_*.csv  (COLUMN_ORDER)
   │
utils/supabase_client.py::upload   → tabela `coletas` (_COLUMN_MAP + run_id)
   │
utils/admin_automation.py          → resolução de SKU pós-coleta (utils/sku_matcher)
   │
app.py (dashboard)                 → ⚠️ ÚNICO ponto de validação de preço,
                                       em tempo de leitura, DESCARTANDO linhas
```

### 1.1 Onde cada campo é definido

| Campo | Arquivo | Função |
|-------|---------|--------|
| Preço | `utils/text.py:32` | `parse_price_brazil()` → `parse_price()` |
| Preço (CB, API) | `scrapers/casas_bahia.py:691` | `_extract_vtex_sellers()` |
| Preço (CB, DOM) | `scrapers/casas_bahia.py:1269` | `_parse_dom()` via `_SELECTORS["price_candidates"]` |
| Preço (ML) | `scrapers/mercado_livre.py:686` | `_extract_price()` |
| Preço (Amazon) | `scrapers/amazon.py:146` | `_extract_price()` |
| Marca | `utils/brands.py` | `extract_brand()` |
| Produto | `utils/normalize_product.py` | `normalize_product_name{,_v2}()` |
| SKU | `utils/sku_matcher.py:143` | `resolve_sku()` — **pós-coleta**, não na coleta |
| Seller | por scraper | `_extract_seller()` / `sellers[]` |
| Keyword | `main.py:_run_scraper` | laço sobre `KEYWORDS_LIST` |
| Turno | `utils/text.py:454` | `get_turno()` — relógio do instante da linha |
| Posição | por scraper | contadores `pos_general` / `organic` / `sponsored` |
| Tag | por scraper | seletor `tag_destaque` |

### 1.2 Auditoria de origem (HTML/payload)

| Existe | O quê |
|--------|-------|
| ✅ | `run_id` (UUID por execução) propagado ao Supabase |
| ✅ | `output/raw_<RUN_ID>.csv` — dump pré-tipagem |
| ❌ | **Snapshot de HTML/payload por registro.** `_dump_debug_html()` só dispara quando a página retorna **0 itens** — exatamente o caso em que não há preço para auditar |
| ❌ | `price_raw_text` — o texto que originou o float não é preservado |
| ❌ | `parser_version` / `collector_version` |

**Consequência:** quando um preço sai errado (§3), **não há como provar** de qual
nó do HTML ou de qual campo do payload ele veio. A auditoria abaixo teve de ser
feita por inferência cruzada entre plataformas.

---

## 2. Hipótese 3.2 — Ausência de identificador de oferta ✅ **CONFIRMADA**

Todos os coletores **já extraem** um ID estável e **todos o jogam fora**:

| Plataforma | ID disponível | Onde | Destino |
|-----------|---------------|------|---------|
| Amazon | ASIN (`data-asin`) | `amazon.py:382` `_extract_asin()` | `record.pop("_asin")` — **descartado** (`amazon.py:373`) |
| Mercado Livre | `item_id=MLB…` | `mercado_livre.py:211` `_AD_ITEM_ID_RE` | só monta URL |
| Shopee | `itemid` + `shopid` | `shopee.py:907-911` | só monta URL |
| Magalu | `product_id` | `magalu.py:2043` | vira chave de dedup local |
| Casas Bahia | `productId` + **`idLojista`** | payload VTEX / query string da URL | **nunca lido** |
| Leroy Merlin | `objectID` + `sellerId` | `leroy_merlin.py:617` | resolvido para nome, ID descartado |

`_build_record()` (`scrapers/base.py:586`) não tem parâmetro algum de ID. A
única âncora de identidade que sobrevive é `URL Produto` — e ela **não é
canônica**: a mesma oferta aparece com e sem `?idLojista=19937`, o que faz uma
oferta 1P e uma 3P do mesmo produto colidirem ou divergirem conforme o dia.

> Sem `marketplace_offer_id + seller_id` **não existe série histórica de oferta**.
> Toda a §11 do briefing (same-offer price change, offer churn, seller churn)
> é hoje impossível de calcular.

---

## 3. Hipótese 3.3 — Extração incorreta de preço ✅ **CONFIRMADA (Casas Bahia)**

### 3.1 Reprodução do R$ 6.019

Produto: **Midea AI Airvolution 12.000 BTUs Frio — 42EFVCA12M5**
(CB `productId=1582007658`)

Mesmo SKU, mesma semana, todas as plataformas:

| Plataforma | n | Preços distintos | Mediana | Máx |
|-----------|---|------------------|---------|-----|
| Shopee | 1.347 | 18 | **R$ 1.804,55** | 2.878,68 |
| Amazon | 1.380 | 23 | **R$ 1.989,00** | 2.825,10 |
| Magalu | 641 | 30 | **R$ 2.033,10** | 3.189,00 |
| Mercado Livre | 658 | 19 | **R$ 2.115,00** | 3.439,00 |
| Leroy Merlin | 753 | 16 | **R$ 2.149,00** | 2.612,26 |
| **Casas Bahia** | **125** | **6** | **R$ 5.869,00** | **6.019,00** |

Decomposição das 125 observações da Casas Bahia:

| Preço | Obs | Dias distintos | `qtd_sellers` nulo | Leitura |
|-------|-----|----------------|--------------------|---------|
| 2.392,00 | 17 | 4 | 17 | ✅ plausível |
| 2.718,00 | 6 | 1 | 6 | ✅ plausível |
| 2.808,00 | 6 | 1 | 6 | ✅ plausível |
| 2.898,00 | 4 | 1 | 4 | ✅ plausível |
| **5.869,00** | **37** | **7 (todos)** | **37** | ❌ **congelado** |
| **6.019,00** | **55** | **7 (todos)** | **55** | ❌ **congelado** |

**73,6% das observações** carregam um preço que **não se moveu um centavo em 7
dias e 2 turnos** — enquanto o preço plausível varia diariamente (2.392 → 2.718
→ 2.808 → 2.898), como um preço real de vitrine competitiva varia. Preço
congelado + 2,8× o consenso de mercado = **não é preço vigente**.

### 3.2 Causa raiz no código

**Todas as 5.325 linhas de Casas Bahia da semana têm `qtd_sellers` e
`buy_box_seller` nulos — 0,0% de cobertura.** O `sellers[]` da VTEX **nunca
chegou em produção**. Logo `_extract_vtex_sellers()` sempre devolveu
`price_float=None` e o código caiu no fallback:

```python
# scrapers/casas_bahia.py:977-982
price_float = sellers_info["price_float"]
if price_float is None:
    # Fallback para o campo price simples (IS endpoint às vezes traz)
    price_float = float(str(prod.get("price"))) if prod.get("price") else None
```

`prod["price"]` na VTEX Intelligent Search **não é o preço vigente da buy box** —
é o preço do item de referência do produto, frequentemente o "de" (riscado) ou o
preço de um lojista 3P indisponível. É o campo que devolve 6.019 quando a
vitrine mostra ~2.400.

Dois defeitos independentes reforçam isso quando o `sellers[]` volta:

```python
# casas_bahia.py:716 — cai no ListPrice (preço RISCADO) quando Price=0/ausente
price = offer.get("Price") or offer.get("ListPrice")

# casas_bahia.py:733-737 — último recurso é um seller ARBITRÁRIO do array,
# possivelmente indisponível e com preço obsoleto
or (all_sellers[0] if all_sellers else None)
```

O vencedor da buy box também é escolhido como "qualquer seller disponível", não
o de **menor preço** — que é a regra real da vitrine VTEX.

### 3.3 Caminho DOM — risco latente

```python
# casas_bahia.py:95-101
"price_candidates": [
    "[data-testid='price-best-price']",
    ".vtex-product-price-1-x-sellingPrice",
    "[class*='sellingPrice']", "[class*='bestPrice']",
    "[class*='productPrice']",
    "[class*='price']",          # ⚠️ pega listPrice, oldPrice, installmentPrice
]
```

O último candidato é um coringa que casa **qualquer** elemento com `price` na
classe — inclusive parcela e preço riscado. Não há guarda que rejeite um nó cujo
texto contenha `"x de"` ou que esteja dentro de `<s>`/`<del>`.

**Contraste (implementação correta, usar como referência):** o Mercado Livre
resolve isso explicitamente —
`".andes-money-amount:not(.andes-money-amount--previous)"`
(`mercado_livre.py:697-699`) exclui o container do preço riscado antes de ler.

---

## 4. Hipótese 3.5 — Mistura de famílias não comparáveis ✅ **CONFIRMADA**

Kits **bi-split / multi-split** (1 condensadora + 2–3 evaporadoras) entram na
base como aparelhos unitários porque o normalizador de título apaga o sinal:

| URL de origem | `produto` gravado | Preço |
|---------------|-------------------|-------|
| `ar-condicionado-bi-split-…-lg-21000-btus-hi-wall-12000-hi-wall-12000-…` | "Ar Condicionado LG 21.000 BTUs Inverter Quente/Frio" | R$ 8.967,60 |
| `ar-condicionado-bi-split-…-samsung-windfree-18000-btus-hi-wall-12000-hi-wall-12000-…` | "Ar Condicionado Samsung WindFree AI 18.000 BTUs …" | R$ 6.686,10 |
| `ar-condicionado-bi-split-…-daikin-18000-btus-2x-evap-12000-…` | "Ar Condicionado Daikin 18.000 BTUs Inverter Quente/Frio" | R$ 7.469,10 |

Impacto medido na semana:

| Plataforma | Linhas de kit | % do total | Mediana kit | Mediana unitário | Razão |
|-----------|---------------|-----------|-------------|------------------|-------|
| Magalu | 515 | 2,21% | R$ 6.499 | R$ 2.439 | **2,7×** |
| Shopee | 453 | 1,66% | R$ 7.107 | R$ 2.205 | **3,2×** |
| Leroy Merlin | 316 | 1,31% | R$ 7.793 | R$ 2.484 | **3,1×** |
| Mercado Livre | 296 | 1,04% | R$ 5.809 | R$ 2.599 | **2,2×** |
| Amazon | 53 | 0,10% | R$ 17.399 | R$ 2.249 | **7,7×** |
| Casas Bahia | 32 | 0,60% | R$ 5.639 | R$ 2.658 | **2,1×** |
| Google Shopping | 8 | 1,55% | R$ 6.362 | R$ 2.425 | **2,6×** |
| **Total** | **1.673** | — | — | — | — |

> **Filtro usado** (idêntico para contagem e para as duas medianas — a
> inconsistência entre os dois é fácil de introduzir e falseia a razão):
> `url_produto ~* 'bi-split|bisplit|multi-split|multisplit|2x-evap|3x-evap'`
> **OU** `produto ~* 'bi.?split|multi.?split'`. É uma detecção por texto, logo
> um **piso**: kits que não trazem o termo na URL nem no título não entram na
> contagem. O número real de kits mal classificados é maior que 1.673.


O valor **R$ 5.528** citado no briefing é exatamente o **mínimo do balde de kits**
da Casas Bahia — ou seja, aquela anomalia específica **não é erro de captura de
preço: é um kit bi-split corretamente precificado e incorretamente classificado.**

> São dois problemas distintos com sintomas idênticos. Tratar os dois como
> "erro de preço" levaria a jogar fora dado bom (kit) e a manter dado ruim
> (preço congelado da §3).

Não há hoje campo para `product_type`, `kit_or_bundle`, `includes_installation`,
`voltage`, `cycle` ou `technology` no registro persistido — só o que couber no
texto de `Produto / SKU`.

---

## 5. Hipótese 3.1 — Duplicação entre keywords ✅ **CONFIRMADA**

`main.py::_run_scraper()` agrega com `records.extend(result)` e **não deduplica**.
Deduplicação só existe em `scrapers/dealers.py::_deduplicate()` (plataforma fora
do foco) e, parcialmente, por página no Magalu.

Semana de 18–24/08:

- **161.079 linhas** → **27.745** ofertas únicas (data+turno+plataforma+`offer_key`)
- **82,8% das linhas são reobservação da mesma oferta no mesmo turno**

> **Correção (Ago/2026, após a Fase 1).** A primeira medição desta auditoria deu
> 66,7%, usando a URL sem query string como chave. Era um **piso**: a mesma
> oferta aparece com slugs de path diferentes (o título do produto entra na URL
> e varia), então uma comparação por string tratava como distintas linhas que a
> `offer_key` — ancorada no id do marketplace — reconhece como a mesma oferta.
> Com a identidade real preenchida no histórico, o número correto é **82,8%**.
> O achado é maior do que o relatado inicialmente.

| Plataforma | Linhas/produto/turno | Keywords/produto | Máx linhas | % grupos com preço divergente no mesmo turno |
|-----------|----------------------|------------------|------------|---------------------------------------------|
| Amazon | 9,6 | 5,0 | **146** | 17,0% |
| Shopee | 8,0 | 4,3 | 91 | 15,5% |
| Mercado Livre | 6,8 | 3,5 | 82 | 23,8% |
| Leroy Merlin | 5,7 | 2,8 | 72 | 9,3% |
| Magalu | 5,3 | 3,0 | 54 | **32,0%** |
| Casas Bahia | 3,5 | 1,9 | 24 | 13,0% |

A coluna final é o achado mais incômodo: no Magalu, **32% dos grupos
produto+turno têm mais de um preço distinto** — no mesmo turno. Ou são ofertas
de sellers diferentes colapsadas sob o mesmo título normalizado (falta de
`offer_id`, §2), ou inconsistência de extração. **Sem `offer_id` não é possível
distinguir as duas hipóteses** — é a dependência que trava o resto da análise.

---

## 6. Duplicação **não** é a causa da volatilidade — evidência contrária

Recalculei a mediana diária dos produtos 12k com e sem deduplicação por oferta
(coeficiente de variação da mediana diária, semana de 18–24/08). A tabela abaixo
usa a **`offer_key`** como chave de dedup — ver a nota de método logo em seguida:

| Plataforma | Volatilidade bruta | Volatilidade deduplicada |
|-----------|--------------------|--------------------------|
| Leroy Merlin | 1,33% | **0,28%** ↓ |
| Shopee | 2,06% | **1,38%** ↓ |
| Amazon | 1,00% | 1,03% ≈ |
| Magalu | 1,30% | 1,34% ≈ |
| Mercado Livre | 1,00% | 1,19% ↑ |
| Casas Bahia | 1,15% | **3,11%** ↑ |

Deduplicar ajuda em duas plataformas, é neutro em duas e **piora em duas** — ao
remover a repetição, a mediana passa a ser sustentada por menos ofertas e fica
mais sensível a entrada/saída de anúncio.

> **Nota de método (Ago/2026).** A primeira versão desta tabela usava a URL sem
> query string como chave de dedup — a mesma que a §5 passou a chamar de piso.
> Deixar a conclusão central do relatório apoiada numa chave que o próprio
> relatório desqualifica seria incoerente, então recomputei tudo com a
> `offer_key` depois do backfill (`014b`). **A conclusão não muda; o efeito
> fica mais nítido.** O que se moveu: Casas Bahia foi de 2,04% para **3,11%** e
> Amazon de 1,31% para 1,03%. O caso da Casas Bahia é o mais informativo — com
> a chave certa, deduplicar **quase triplica** a volatilidade dela — 1,15% →
> 3,11%, fator **2,7×**. Faz sentido: é a
> plataforma com preço congelado errado (§3), e a dedup tira o peso das
> reobservações que diluíam esses valores, deixando a mediana diária
> descansar sobre menos ofertas — várias delas com preço não-vigente.

**Leitura:** a volatilidade por marca reportada no briefing (Samsung 10,23%) é
dominada por **efeito composição** (§4) e por **preço errado** (§3), não por
redundância. A deduplicação continua necessária — por ponderação, custo e
integridade de série — mas **não deve ser vendida como a correção da
volatilidade**. Quem corrige volatilidade é a cesta fixa de SKUs (§11.3 do
briefing) e a separação de kits.

---

## 7. Hipótese 3.6 — Turnos ⚠️ **PARCIAL**

`Turno` existe e é gravado, mas é derivado do relógio **no instante de cada
linha** (`utils/text.py:454`, chamado dentro de `_build_record`):

```python
def get_turno(hora=None):
    h = hora if hora else now_brt()
    return "Abertura" if h.hour <= TURNO_ABERTURA_MAX_HOUR else "Fechamento"
```

Um run que atravessa a hora de corte é **fatiado em dois turnos no meio da
execução** — as primeiras keywords viram "Abertura" e o resto "Fechamento", no
mesmo `run_id`. O turno deveria ser propriedade do **run**, não da linha.

Confirmado na base: 39 `run_id` distintos na semana para 7 plataformas × 7 dias
× 2 turnos.

---

## 8. Hipótese 3.4 — Matching de SKU ⚠️ **PARCIALMENTE DESCARTADA — a lógica é boa**

`utils/sku_matcher.py::resolve_sku()` é sólido e **não** comete o erro do
briefing (colidir PAC12FC / PAC12FB / PAC12FI por serem "Philco 12k"):

- casa por **igualdade de atributos**, nunca por `contains`;
- guarda de tecnologia (On/Off não cai em catálogo inverter-only);
- desempate por voltagem com aliases normalizados;
- **não chuta**: >1 candidato → `sku_v2=None`, confiança `ambigua`, mantém família;
- já registra `metodo`, `confianca`, `motivo`, `candidatos` — os campos
  `matching_method` / `matching_score` / `matching_reason` do briefing.

**O problema não é a lógica — é onde ela roda.** `resolve_sku` é chamado só em
`utils/admin_automation.py` e nos scripts, **depois** da coleta. Resultado:

| Plataforma | % com SKU resolvido |
|-----------|---------------------|
| Casas Bahia | 54,4% |
| Amazon | 50,9% |
| Shopee | 50,3% |
| Mercado Livre | 47,4% |
| Leroy Merlin | 46,9% |
| Magalu | 46,8% |
| Google Shopping | 20,3% |
| **Média** | **49,0%** |

Falta o degrau de **Prioridade 1** do briefing (ID oficial do marketplace ligado
a catálogo) — que é justamente o que a §2 descartou.

---

## 9. Estado dos campos de insight (foco declarado do projeto)

O foco oficial desde Mai/2026 é buy box e sellers. Cobertura real na semana:

| Plataforma | `buy_box_seller` | `qtd_sellers` |
|-----------|------------------|---------------|
| Leroy Merlin | 100,0% | 100,0% |
| Shopee | 100,0% | 100,0% |
| Magalu | 100,0% | **0,0%** |
| Mercado Livre | 87,5% | **0,2%** |
| Google Shopping | 97,7% | 5,8% |
| Amazon | **0,0%** | 19,9% |
| **Casas Bahia** | **0,0%** | **0,0%** |

Amazon a 0% é **por desenho** (buy box só existe no PDP; o projeto opta por
vazio em vez de vitória 1P fantasma — decisão correta, documentada). Casas
Bahia a 0% **não é por desenho**: é o `sellers[]` que nunca chega, e é a mesma
falha que produz o preço errado da §3.

---

## 10. Ranking de fragilidade por marketplace

| # | Plataforma | Diagnóstico |
|---|-----------|-------------|
| 1 | **Casas Bahia** | Preço vindo de campo errado, congelado; `sellers[]` a 0%; coringa `[class*='price']` no DOM; `idLojista` ignorado |
| 2 | **Magalu** | 32% dos grupos produto+turno com preço divergente; `qtd_sellers` a 0%; 2,21% de kits sem rótulo (a maior taxa) |
| 3 | **Amazon** | `_extract_price` sem guarda contra "outras ofertas"; ASIN extraído e descartado; 9,6 linhas/produto/turno |
| 4 | **Leroy Merlin** | Melhor cobertura de insight (100/100); `objectID`/`sellerId` descartados após resolver nome |
| 5 | **Mercado Livre** | **Extração de preço é a referência** (exclui riscado explicitamente); falta persistir o MLB |
| 6 | **Shopee** | Cobertura boa; dependente de sessão; `itemid`/`shopid` descartados |
| 7 | **Google Shopping** | Volume marginal (516 linhas); SKU a 20,3% |

---

## 11. Hipóteses ainda não verificáveis

Não é possível confirmar ou descartar sem mudança no schema:

- **Preço de parcela capturado como preço cheio** — sem `price_raw_text` não há como saber se um float veio de "10x R$ 601,90". Só detectável por proxy.
- **Preço de kit/instalação embutido** — parcialmente inferido pela URL (§4); sem `kit_or_bundle` no registro, não é mensurável de forma confiável.
- **Preço de outro card / oferta selecionada divergente do título** — exigiria snapshot do HTML por registro.
- **Efeito seller vs. efeito anúncio** — bloqueado pela ausência de `offer_id`/`seller_id` (§2).
- **Same-offer price change / offer churn / seller churn** (§11.1, 11.5, 11.6 do briefing) — idem.

Todas destravam com o mesmo pré-requisito: **§2**.

---

## 12. Plano de alteração priorizado

Ordem por (impacto no número final ÷ risco de regressão). Cada fase é
independente e aditiva — **nenhuma coluna existente é removida**.

### Fase 1 — Identidade da oferta ✅ **IMPLEMENTADA** (Ago/2026)
Módulo `utils/offer_identity.py` + 5 colunas no fim de `COLUMN_ORDER`:
`ID Produto Marketplace`, `ID Oferta Marketplace`, `ID Seller`, `URL Canônica`,
`Offer Key`. Migração `014` + `_OPTIONAL_DEST_COLS` (degradação graciosa em
banco não migrado). `_build_record()` ganhou 3 parâmetros opcionais; ids
passados pelo coletor têm precedência sobre os derivados da URL.

Decisões que valem registro:
- **`marketplace_offer_id` nunca é sintetizado.** Só ML e Shopee expõem um id
  de oferta real; nas outras o campo fica vazio. Um id inventado pareceria
  autoridade que o dado não tem.
- **`offer_key` é versionada** (`v1|`): se a regra de derivação mudar, a versão
  sobe e séries antigas param de casar com novas — em vez de casarem errado.
- **O seller entra também nos degraus derivados** da chave. Sem isso, dois
  lojistas na mesma página de produto colapsavam numa série só — bug real,
  pego por teste durante a implementação.
- Regex de identidade validado contra **URLs reais de produção**, não formato
  imaginado (`tests/test_offer_identity.py`).

Cobertura obtida (smoke test das 7 plataformas): product id nas 6 que têm um;
offer id nativo em ML e Shopee; seller id em CB, Magalu, Shopee e Leroy.

**Backfill do histórico** (migração `014b`, aplicada em 26/08/2026): a
identidade foi derivada retroativamente da `url_produto` já gravada —
**376.289 de 379.848 linhas (99,06%)**. Cobertura resultante:

| Plataforma | Linhas | `offer_key` | product id | offer id nativo | seller id | Ofertas distintas |
|-----------|--------|-------------|-----------|-----------------|-----------|-------------------|
| Amazon | 105.055 | 100,0% | 89,8% | — | — | 876 |
| Shopee | 77.817 | 100,0% | 100,0% | **100,0%** | 100,0% | 1.311 |
| Mercado Livre | 81.094 | 100,0% | 53,1% | **22,5%** | — | 1.439 |
| Magalu | 50.277 | 100,0% | 100,0% | — | 82,6% | 2.160 |
| Leroy Merlin | 48.815 | 100,0% | 100,0% | — | — | 649 |
| Casas Bahia | 15.170 | 76,5% | 76,5% | — | 18,9% | 494 |
| Google Shopping | 1.620 | 100,0% | — | — | — | **1** |

Duas leituras que só ficam visíveis agora:

- **Google Shopping tem 1 oferta distinta em 1.620 linhas.** Não é
  concentração de mercado: as 1.620 linhas apontam todas para a mesma página
  de ajuda do Google (`support.google.com/googleshopping/answer/9128904`). O
  extrator de URL da plataforma está pegando o link de rodapé em vez do
  produto — defeito que estava invisível enquanto não havia identidade.
- **Casas Bahia a 76,5%** é o teto possível: as 3.559 linhas restantes (23,5%)
  não têm `url_produto` nenhuma. Ficam com `offer_key` NULL de propósito — ver
  a nota da migração `014b` sobre por que não fabricar chave a partir do
  título normalizado.

### Fase 2 — Correção do preço Casas Bahia 🔴
1. `Price or ListPrice` → usar `Price`; **nunca** cair em `ListPrice`.
2. Buy box = **menor preço entre os disponíveis**, não "qualquer disponível".
3. Remover o fallback para `all_sellers[0]` arbitrário → devolver `None`.
4. Fallback `prod["price"]`: marcar `price_selection_rule="vtex_product_price_fallback"` e `price_validation_status="insufficient_information"` em vez de tratar como preço vigente.
5. Guarda no DOM: rejeitar nó com `"x de"`/`<s>`/`<del>`; restringir o coringa `[class*='price']`.
6. **Fixtures** com os valores reais: 5.999 / 6.019 / 5.528 / 5.869 / 2.392.

### Fase 3 — Camada de validação na coleta
`utils/price_validation.py`: `price_validation_status` + `price_validation_reason`
com os estados do briefing (`valid`, `valid_promotional`,
`suspected_wrong_price_field`, `suspected_installment_price`, `suspected_bundle`,
`suspected_product_mismatch`, `suspected_stale_price`, `quarantined`,
`insufficient_information`). Limites **configuráveis** (15/30/60% + IQR), não
fixos no código. Regra nova de **preço congelado**: mesma oferta, mesmo centavo,
≥5 dias, >1,8× a mediana do SKU → `suspected_stale_price`. **Classifica, não apaga.**

### Fase 4 — Atributos de produto e rótulo de kit
`utils/attr_parser.py` já entrega tecnologia/voltagem/cor/form_factor. Estender
para `kit_or_bundle`, `includes_installation`, `product_type`, e persistir os
atributos hoje calculados e descartados. Rotular as 1.673 linhas de kit.

### Fase 5 — Camadas de saída
`raw_search_observations` (o que existe hoje, intacto) →
`normalized_offers` (1 linha/oferta/turno, dedup por `offer_key`) →
`price_history` (só `valid`/`valid_promotional`) + `search_market_snapshot`.
Implementar como **views** sobre `coletas`, sem migrar dado histórico.

### Fase 6 — Indicadores da §11 do briefing
Habilitados pelas fases 1–5. `stable_basket_brand_index` e `search_mix_effect`
são os que respondem à pergunta original: **quanto da variação é preço e quanto
é composição de vitrine.**

### Fase 7 — Turno como propriedade do run
Congelar o turno no início do run e propagar, em vez de recalcular por linha.

---

## 13. Testes propostos (§12 do briefing)

- `parse_price` pt-BR: milhar/decimal, `\xa0`, notação float Python, múltiplos valores na string.
- Preço à vista **e** parcelado no mesmo card → deve escolher o à vista.
- Preço riscado presente → deve ser ignorado (regressão do padrão ML).
- **Fixtures Casas Bahia** com 5.999 / 6.019 / 5.528 comprovando de qual campo cada um veio.
- SKUs quase idênticos: PAC12FC / PAC12FB / PAC12FI / PAC12000IFM15 / PAC12000IQFM15 / PAC12000ITFM12W → nunca colapsam.
- Kit bi-split → `kit_or_bundle=True`, fora da cesta unitária.
- Dedup: mesma oferta em 5 keywords → 5 linhas em `raw`, 1 em `normalized_offers`.
- Preço congelado 7 dias a 2,8× a mediana → `suspected_stale_price`.

---

## 14. Recomendação

Aprovar **Fase 1 + Fase 2** para execução imediata: são as que corrigem número
errado chegando ao executivo, e a Fase 1 é pré-requisito de tudo que vem depois.

Enquanto a Fase 2 não entra, **as séries de preço da Casas Bahia não devem
alimentar indicadores executivos** — 73,6% das observações do SKU auditado
carregam preço não-vigente.

---

*Auditoria executada em 26/08/2026. Consultas de reprodução no anexo do PR.*
