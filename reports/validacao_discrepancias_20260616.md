# Validação de Discrepâncias `coletas` × `pricetrack` — Buy Box & Sellers

**Período analisado:** `2026-06-01` a `2026-06-16`
**Projeto Supabase:** `ailbsczkrympslpjwwko`
**Gerado em:** 2026-06-16
**Modo:** somente leitura (nenhum dado alterado)
**SKUs do caso base (Ecomaster 12k):** `42EZVCA12M5` (Frio) · `42EZVQA12M5` (Quente/Frio)

---

## 0. Realidade do schema (correção importante ao briefing)

O briefing assumia **uma tabela `rac_monitoramento` com uma coluna `source`**. Isso **não corresponde ao banco atual**:

| Premissa do briefing | Realidade no banco |
|---|---|
| Tabela única `rac_monitoramento` | `rac_monitoramento` existe mas está **vazia no período (0 linhas)** — tabela legada |
| Coluna `source` distingue fontes | **Não existe.** As duas fontes são **duas tabelas separadas** |
| `data` é TEXTO | Nas tabelas vivas `data`/`collection_date` são **`DATE`** (filtro `BETWEEN` funciona direto, sem cast) |
| `preco` pode vir como texto | `coletas.preco` é `numeric`; pricetrack usa `min/mode/max/avg_price` `numeric` |

As duas fontes reais:

| `source` lógico | Tabela | Linhas (período) | Granularidade de 1 linha |
|---|---|---|---|
| `coletas` (scraper Python de busca) | **`public.coletas`** | 109.198 | (data, turno, plataforma, **keyword**, produto) — o mesmo produto se repete em dezenas de keywords |
| `pricetrack` (scraper de caixa de ofertas) | **`public.pricetrack_daily`** | 181.426 | (collection_date, marketplace, sku, **seller**) — uma linha por oferta/seller na caixa, já agregada com `min/mode/max/avg_price` |

**Mapeamento de colunas usado em todo o relatório:**

| Conceito | `coletas` | `pricetrack_daily` |
|---|---|---|
| data | `data` | `collection_date` |
| sku | `sku_resolvido` (SKU resolvido pelo de-para) | `sku` |
| produto/título | `produto` | `title` |
| plataforma | `plataforma` | `marketplace` |
| preço por oferta | `preco` | `min_price` * |
| seller | `seller` / `buy_box_seller` | `seller` / `seller_canonical` |
| turno | `turno` | — (não existe) |

\* **Escolha do preço por oferta no pricetrack:** uso `min_price` como o preço efetivo da linha (piso do seller). Em 99% das linhas `min_price = mode_price = max_price`; `min≠mode` em apenas 1.841/181.426 (1,0%) e `min≠max` em 24.375 (13,4%). Para a **MODA diária** da caixa uso `mode() WITHIN GROUP (ORDER BY min_price)` sobre todas as ofertas do dia; para o **MÍNIMO diário**, `min(min_price)`.

**Chaves de junção entre fontes:** `sku` é compartilhado (mesmo namespace Midea/etc. — 128/128 SKUs de `coletas` também existem em `pricetrack`). Plataformas exigem normalização (ver Apêndice A): `coletas` usa "Amazon"/"Magalu"/"Casas Bahia"; `pricetrack` usa "AMAZON"/"MAGAZINE LUIZA"/"CASAS BAHIA" + dezenas de dealers extras.

> ⚠️ **Segurança (advisor do Supabase):** todas as 10 tabelas do schema `public` estão com **RLS desabilitado** — qualquer um com a anon key lê/escreve tudo. Fora do escopo desta análise, mas reportado conforme exigência.

---

## 1. Placar das hipóteses

| Hipótese | Confirmada? | Evidência numérica (período) | SKUs afetados | Plataformas afetadas |
|---|---|---|---|---|
| **H1 — Fragmentação de produto** | ✅ **Sim** (só `coletas`) | `coletas`: **68/128 SKUs (53,1%)** têm >1 nome em `produto` (máx 6). `pricetrack`: **0/526** (títulos canônicos). Base `42EZVCA12M5`: "…Inverter Frio" (1.848 linhas) + "…Inverter Frio **Preto**" (4) ✓ | 68 (coletas) | 23 (todas coletas) |
| **H2 — SKU nulo / agregador** | ✅ **Sim** (muito > referência) | `coletas`: **50.205/109.198 linhas = 45,98%** com `sku_resolvido` nulo. `pricetrack`: **0**. "Ecomaster Pro": **4 linhas, Google Shopping, sku nulo** ✓ | n/a (linhas não resolvidas) | 23 (Amazon 17k, ML 13k, Leroy 9k, Google 4,7k…) |
| **H3 — Congelamento (stale)** | ✅ **Sim** (forte no pricetrack) | Combos congelados (1 valor de mín diário em ≥10 dias): `coletas` **85/561 elegíveis (15,2%)**; `pricetrack` **3.465/5.325 (65,1%)**. Base Bemol `42EZVCA12M5`=**2559 por 16 dias** ✓. *CentralAr-no-pricetrack: refutado* (ver §2/H3) | ver §2 | ambas |
| **H4 — Placeholder / outlier** | ✅ **Sim** | Preços terminando em `999.00`: `coletas` 1.928 linhas/91 combos; `pricetrack` 4.039/339 (+121 linhas em `9999`). >1,5× mediana do SKU: dezenas (top 4,74×). Base Frigelar `42EZVQA12M5`=**3999 fixo** vs Frio ~2.386–2.580 ✓ (pricetrack mostra o mesmo a 2.579,57 → 3999 é placeholder) | ≥120 | ambas |
| **H5 — Moda vs Buy Box (gap moda−min)** | ✅ **Sim** | Gap moda−min por (sku,dia): `pricetrack` média **R$ 802** / mediana **R$ 237** / p90 R$ 2.346; `coletas` média R$ 359 / mediana R$ 108. Base `42EZVQA12M5` pricetrack: **moda 2999,90 / min ~2199–2289 / gap ~700–800** ✓ (cluster MAP de 5–7 sellers) | universo | pricetrack ≫ coletas |
| **H6 — Concordância de buy box entre fontes** | ⚠️ **Parcial / refutada como enunciada** | 4.329 (plat,sku,dia) em ambas. Mediana \|gap do **MÍN**\| = **R$ 271** vs mediana \|gap da **MODA**\| = **R$ 307**. O mín concorda só **marginalmente** melhor (não "muito mais"). Mesmo o mín diverge: ML R$ 458, Amazon R$ 250, Leroy R$ 301 (melhor: Leveros R$ 52) | 122 em comum | 18 normalizadas |
| **H7 — Cobertura** | ✅ **Sim** | `coletas`: **16 dias (01–16 completo)**, 23 plataformas, 2 turnos. `pricetrack`: **15 dias (SEM 16/06)**, 66 marketplaces, sem turno ✓. Coletas raso: **Casas Bahia só 1 dia (12/06, 94 linhas)**; **Magalu só 11–12/06** substancial ✓ | — | — |

**Veredito sobre as referências manuais do solicitante:** H1, H2 (Ecomaster Pro), H3 (Bemol), H4 (Frigelar), H5 e H7 — **todas batem**. A única referência **refutada** é "**CentralAr travado no pricetrack**": nos 2 SKUs do caso base, CENTRAL AR (como marketplace **ou** como seller) **não** está congelado (ver §2/H3).

---

## 2. Detalhe por hipótese (caso base + universo)

### H1 — Fragmentação de produto ✅
**Query (núcleo):**
```sql
SELECT sku_resolvido, count(DISTINCT produto) names
FROM coletas
WHERE data BETWEEN '2026-06-01' AND '2026-06-16'
  AND sku_resolvido IS NOT NULL AND sku_resolvido<>''
GROUP BY sku_resolvido;            -- flag: names > 1
-- pricetrack: GROUP BY sku, count(DISTINCT title)
```
- **coletas:** 68/128 SKUs (53,1%) com >1 nome; máximo 6.
- **pricetrack:** 0/526 — cada SKU tem exatamente 1 título canônico (a fragmentação é exclusiva do `coletas`).
- **Caso base** `42EZVCA12M5`: `Ar Condicionado Midea AI Ecomaster 12.000 BTUs Inverter Frio` (1.848 linhas) **+** `…Inverter Frio Preto` (4 linhas). `42EZVQA12M5`: nome único.
- **Gravidade > cosmético:** vários SKUs misturam **modelos distintos** sob o mesmo `sku_resolvido` (erro de de-para), não só cor:

| sku_resolvido | nomes | plats | exemplo A | exemplo B |
|---|---|---|---|---|
| `TAC-12CFG3W-INV` | 6 | 9 | TCL 12.000 **Inverter** Frio | TCL 12.000 **On/Off** Frio |
| `GWC09AGA` | 5 | 12 | Gree 9.000 Inverter Frio | Gree 9.000 On/Off Frio |
| `TAC-09CSA1` | 5 | 7 | TCL 9.000 Inverter Frio | TCL **Elite** 9.000 Inverter Frio |
| `S3-Q18KLR1B` | 5 | 8 | LG 18.000 Q/F | LG **Dual Inverter** 18.000 Q/F |
| `GWC18ATD` | 5 | 10 | Gree 18.000 Inverter Frio | Gree 18.000 Inverter Frio **Preto** |

### H2 — SKU nulo / agregador ✅ (muito além da referência)
```sql
SELECT count(*) FILTER (WHERE sku_resolvido IS NULL OR sku_resolvido='') , count(*)
FROM coletas WHERE data BETWEEN '2026-06-01' AND '2026-06-16';
```
- **coletas: 50.205/109.198 = 45,98%** das linhas têm `sku_resolvido` nulo (de-para não resolve ~metade da coleta). **pricetrack: 0**.
- Por plataforma (top): Amazon 17.047 · Mercado Livre 12.978 · Leroy Merlin 9.006 · Google Shopping 4.699 · Magalu 1.739 · Leveros 1.420.
- **Caso base "Ecomaster Pro"** (referência): exatamente **4 linhas, todas Google Shopping, sku nulo**, 3 dias, preço R$ 1.999–3.099. ✓ — porém é uma fração ínfima do problema sistêmico de 46%.

### H3 — Congelamento (stale) ✅
```sql
WITH dm AS (  -- mín diário por (plataforma, sku)
  SELECT plataforma, sku_resolvido sku, data d, min(preco) dmin
  FROM coletas WHERE data BETWEEN '2026-06-01' AND '2026-06-16'
    AND sku_resolvido IS NOT NULL AND sku_resolvido<>'' AND preco IS NOT NULL
  GROUP BY 1,2,3)
SELECT plataforma, sku, count(DISTINCT d) ndays, count(DISTINCT dmin) ndist
FROM dm GROUP BY 1,2;   -- congelado = ndist=1 AND ndays>=10
-- pricetrack: marketplace / min(min_price) / collection_date
```
| source | combos totais | elegíveis (≥10 dias) | **congelados** | % dos elegíveis |
|---|---|---|---|---|
| coletas | 816 | 561 | **85** | 15,2% |
| pricetrack | 6.250 | 5.325 | **3.465** | **65,1%** |

- **Caso base ✓:** `coletas` **Bemol `42EZVCA12M5` = R$ 2.559,00 por 16 dias** (ndist=1).
- **No pricetrack**, congelados para `42EZVCA12M5` incluem 15 marketplaces (Casas Bahia 2149, Extra 2023,12, Fast Shop 1999,83, Web Continental 2249,10, Leroy 1998,48, Center Kennedy/Havan 2999,90…).
- **Referência REFUTADA — "CentralAr travado no pricetrack":** para os 2 SKUs base, CENTRAL AR **não** congela:
  - como **marketplace**: `42EZVCA12M5` 2289 em só 8 dias (não elegível); `42EZVQA12M5` varia 2409→2559.
  - como **seller**: 12–13 valores distintos (R$ 1.999,83–2.889 / 2.289,32–2.662).
  - (No `coletas`, CentralAr também **não** congela: ndist=5.) A hipótese H3 em si está confirmada; apenas **esse exemplo específico** não procede.
- **Ressalva metodológica:** parte dos 65% do pricetrack pode ser **estabilidade real de preço/MAP** numa janela de 15 dias, não necessariamente scraper travado. Ainda assim, 65% é alto e merece auditoria do importador.

### H4 — Placeholder / outlier ✅
```sql
-- redondos: preço termina em 999.00  ->  (v*100)::bigint % 100000 = 99900
-- redondos: termina em 9999          ->  v=floor(v) AND v::bigint % 10000 = 9999
-- outlier: v > 1.5 * mediana_do_sku  (percentile_cont(0.5) por source,sku)
```
- Redondos `…999.00`: coletas **1.928 linhas / 91 combos**; pricetrack **4.039 / 339** (+ **121** linhas em `9999`, todas pricetrack).
- **>1,5× mediana do SKU — top candidatos:**

| source | sku | plataforma | preço | mediana SKU | razão |
|---|---|---|---|---|---|
| pricetrack | `S3-Q12JA31E` | LG | 12.000,00 | 2.529,00 | **4,74×** |
| coletas | `PAC12000IQFM15` | Amazon | 7.898,98 | 1.959,00 | 4,03× |
| pricetrack | `TAC-09CHTG2` | Leroy Merlin | 8.309,92 | 2.148,00 | 3,87× |
| coletas | `45HJQI18C2WC` | Mercado Livre | 11.962,00 | 3.698,00 | 3,23× |
| coletas | `S3-W09AAQAL` | Leveros | 7.729,00 | 2.574,00 | 3,00× |

- **Caso base ✓:** `coletas` Frigelar `42EZVQA12M5` = **R$ 3.999,00 fixo** (termina em 999.00), enquanto o Frio do mesmo dealer roda R$ 2.386–2.580. O `pricetrack` mostra o **mesmo Frigelar Q/F a R$ 2.579,57** → confirma que **3999 é placeholder** do `coletas`.

### H5 — Moda vs Buy Box (gap moda − mín) ✅
```sql
WITH dp AS (
  SELECT sku, collection_date d,
         mode() WITHIN GROUP (ORDER BY min_price) md, min(min_price) mn
  FROM pricetrack_daily WHERE collection_date BETWEEN '2026-06-01' AND '2026-06-16'
  GROUP BY 1,2)
SELECT avg(md-mn), percentile_cont(0.5) WITHIN GROUP (ORDER BY md-mn) FROM dp;
-- coletas análogo com preco
```
| source | sku-dias | gap médio | gap mediano | gap p90 | sku-dias com gap ≥ R$300 |
|---|---|---|---|---|---|
| coletas | 1.911 | R$ 359,1 | R$ 108,5 | R$ 1.100 | 646 |
| **pricetrack** | 7.256 | **R$ 802,2** | **R$ 237,4** | **R$ 2.346** | 3.453 |

- **Caso base ✓:** pricetrack `42EZVQA12M5`: **moda = 2999,90 todos os 15 dias**, mín ~2199–2289 → gap ~R$ 700–800. A moda 2999,90 é um **cluster de MAP** de apenas 5–7 sellers (de ~80 ofertas/dia) — é o valor único mais frequente, não maioria.
- O gap do pricetrack é ~2× o do coletas → a **moda do pricetrack é inflada por clusters de preço-cheio/MAP de 3P** acima do verdadeiro piso (buy box).

### H6 — Concordância de buy box entre fontes ⚠️ Parcial
```sql
-- normaliza plataforma (Apêndice A) -> nplat; agrega mín e moda por (nplat,sku,dia)
-- em cada fonte; junta por (nplat,sku,dia); compara |min_c-min_p| vs |mode_c-mode_p|
```
- **4.329** (nplat, sku, dia) presentes nas **duas** fontes · 18 plataformas · 122 SKUs · 499 combos.

| métrica | gap do **MÍNIMO** | gap da **MODA** |
|---|---|---|
| média \|gap\| | R$ 460,6 | R$ 495,5 |
| **mediana \|gap\|** | **R$ 271,0** | **R$ 306,9** |
| dentro de ±R$50 | 614 dias | 560 dias |

- **Conclusão:** o mín concorda **só marginalmente** melhor que a moda (mediana 271 vs 307, ~12%). A hipótese "concordam **muito mais** no mínimo" **não se sustenta**. Mesmo o **buy box (mín)** diverge por uma mediana de R$ 271 — as fontes **não** medem o mesmo "menor preço" (coletas = preço exibido na busca; pricetrack = piso entre todos os 3P da caixa).
- **Concordância por plataforma (mediana):**

| plataforma | n | \|gap mín\| | \|gap moda\| | gap mín com sinal (coletas−pricetrack) |
|---|---|---|---|---|
| Mercado Livre | 1.045 | 458 | 500 | +60 |
| Amazon | 1.001 | 250 | 335 | −114 |
| Leroy Merlin | 836 | 301 | 307 | −165 |
| Web Continental | 281 | 168 | 214 | −130 |
| Magalu | 168 | 274 | 286 | −148 |
| **Leveros** | 163 | **52** | **52** | +50 |
| Ferreira Costa | 152 | 284 | 304 | +255 |
| Central Ar | 84 | 147 | 147 | −147 |

- Grande parte das **maiores** divergências de mín é **contaminação por placeholder/outlier** numa das fontes (ver §4), não desacordo genuíno de buy box.

### H7 — Cobertura ✅
| source | dias (n) | intervalo | plataformas | turnos |
|---|---|---|---|---|
| coletas | 16 | 2026-06-01 → **06-16** | 23 | Abertura / Fechamento |
| pricetrack | 15 | 2026-06-01 → **06-15** | 66 | (sem turno) |

- **pricetrack sem 16/06** ✓.
- **Coletas raso ✓:** **Casas Bahia** aparece em **1 único dia** (12/06: 94 linhas, 18 SKUs). **Magalu** só tem volume em 11–12/06 (06-01=1 linha, 06-05=16, 06-11=1.224, 06-12=2.528) — coleta esporádica.

---

## 3. Discrepâncias NOVAS (fora do Ecomaster 12k), por severidade

1. **[CRÍTICO] SKU nulo em 46% do `coletas`** — 50.205 linhas sem `sku_resolvido`. Inviabiliza qualquer análise por SKU em ~metade da base de busca. Concentrado em Amazon (17k), ML (13k), Leroy (9k), Google (4,7k).
2. **[CRÍTICO] Fragmentação que mistura modelos distintos** — `TAC-12CFG3W-INV` (Inverter **e** On/Off sob o mesmo SKU), `TAC-09CSA1` (TCL vs TCL **Elite**), `S3-Q18KLR1B` (LG vs LG **Dual Inverter**). De-para colapsando produtos diferentes → preços/posições poluídos. 68 SKUs no total.
3. **[ALTO] Congelamento massivo no `pricetrack` (65%)** — 3.465 combos (marketplace×sku) com mín diário imóvel por ≥10 dias. Ex.: Casas Bahia, Extra, Fast Shop, Web Continental, Leroy congelados nos próprios SKUs Ecomaster; auditar cadência do importador vs. estabilidade real de MAP.
4. **[ALTO] Outliers/placeholder ≥1,5× mediana** — `S3-Q12JA31E` (LG, pricetrack) R$ 12.000 (4,74×); `PAC12000IQFM15` (Amazon, coletas) R$ 7.898,98 (4,03×); `TAC-09CHTG2` (Leroy, pricetrack) R$ 8.309,92 (3,87×); `45HJQI18C2WC` (ML, coletas) R$ 11.962 (3,23×).
5. **[ALTO] Gap moda−mín sistêmico no pricetrack (alta capacidade)** — por SKU (≥10 dias), gap médio: `ZT-Q48GMLAA` R$ 8.532, `FBQ36AVL` R$ 7.146, `AC036DN6DKG/AZ` R$ 7.017, `RCI48B3IV` R$ 6.843. A moda fica muito acima do piso (caixas com muitos 3P em preço-cheio).
6. **[MÉDIO] Redondos suspeitos** — 339 combos pricetrack e 91 coletas com preço terminando em `999.00`; +121 linhas pricetrack em `9999`.
7. **[MÉDIO] Cobertura desigual** — Casas Bahia (1 dia) e Magalu (2 dias úteis) no `coletas` impedem série temporal; pricetrack sem 16/06.

---

## 4. Top 20 maiores divergências de Buy Box (mín) entre fontes

> Mín diário por (plataforma normalizada, sku, dia), presente nas duas fontes. Quase todas são **contaminação por placeholder/outlier** numa das fontes.

| data | plataforma | sku | min_coletas | min_pricetrack | gap |
|---|---|---|---|---|---|
| 2026-06-09 | Mercado Livre | `AR24CSECABT/AZ` | 20.000,00 | 6.899,00 | **13.101,00** |
| 2026-06-10 | Mercado Livre | `AR24CSECABT/AZ` | 20.000,00 | 6.899,00 | 13.101,00 |
| 2026-06-11 | Mercado Livre | `AR24CSECABT/AZ` | 19.199,00 | 6.899,00 | 12.300,00 |
| 2026-06-08 | Mercado Livre | `AR24CSECABT/AZ` | 19.199,00 | 6.899,00 | 12.300,00 |
| 2026-06-05 | Mercado Livre | `AR24CSECABT/AZ` | 19.199,00 | 6.899,00 | 12.300,00 |
| 2026-06-07 | Mercado Livre | `AR24CSECABT/AZ` | 19.199,00 | 6.899,00 | 12.300,00 |
| 2026-06-05 | Mercado Livre | `S3-W24K231A` | 12.999,00 | 4.599,00 | 8.400,00 |
| 2026-06-09 | Mercado Livre | `S3-W24K231A` | 12.999,00 | 4.599,00 | 8.400,00 |
| 2026-06-07 | Mercado Livre | `S3-W24K231A` | 12.999,00 | 4.599,00 | 8.400,00 |
| 2026-06-06 | Mercado Livre | `S3-W24K231A` | 12.999,00 | 4.599,00 | 8.400,00 |
| 2026-06-10 | Mercado Livre | `S3-W24K231A` | 12.999,00 | 4.599,00 | 8.400,00 |
| 2026-06-11 | Mercado Livre | `S3-W24K231A` | 12.999,00 | 4.599,00 | 8.400,00 |
| 2026-06-08 | Mercado Livre | `S3-W24K231A` | 12.999,00 | 5.199,00 | 7.800,00 |
| 2026-06-10 | Amazon | `TAC-24CHTG1` | 3.734,10 | 7.739,10 | 4.005,00 |
| 2026-06-02 | Amazon | `TAC-24CHTG1` | 3.734,10 | 7.739,10 | 4.005,00 |
| 2026-06-11 | Amazon | `TAC-24CHTG1` | 3.734,10 | 7.739,10 | 4.005,00 |
| 2026-06-01 | Mercado Livre | `S3-W24K231A` | 8.538,00 | 4.599,00 | 3.939,00 |
| 2026-06-01 | Mercado Livre | `GWC30ATE` | 10.385,00 | 6.620,00 | 3.765,00 |
| 2026-06-07 | Mercado Livre | `GWC30ATE` | 10.385,00 | 6.620,00 | 3.765,00 |
| 2026-06-06 | Mercado Livre | `GWC30ATE` | 10.385,00 | 6.620,00 | 3.765,00 |

Leitura: `coletas` em ML registra preços inflados/placeholder (R$ 20.000, R$ 12.999) que o `pricetrack` desmente (R$ 6.899, R$ 4.599); no caso `TAC-24CHTG1` é o **pricetrack** que está alto (R$ 7.739 vs R$ 3.734).

---

## 5. Placar final — % de combinações (plataforma × SKU) com ≥1 flag

> Flags por combo: **congelado** (H3) · **redondo `…999.00`** (H4) · **outlier >1,5× mediana** (H4) · **fragmentado** (>1 nome no combo, H1).

| source | combos (plat×sku) | congelado | redondo 999 | outlier | fragmentado | **com ≥1 flag** | **% sinalizado** |
|---|---|---|---|---|---|---|---|
| **coletas** | 816 | 85 | 91 | 113 | 188 | **366** | **44,9%** |
| **pricetrack** | 6.250 | 3.465 | 339 | 290 | 0 | **3.724** | **59,6%** |

- **coletas:** o maior contribuinte é **fragmentação** (188) — falha de de-para. (Não inclui as 50k linhas de SKU nulo, que sequer entram em combo.)
- **pricetrack:** dominado por **congelamento** (3.465). Sem fragmentação (títulos canônicos).
- **Conclusão de placar:** ambas as fontes têm taxa alta de sinalização, mas por **modos de falha diferentes** — `coletas` por **resolução de produto** (SKU nulo + fragmentação), `pricetrack` por **estática de preço** (congelamento + moda inflada por MAP).

---

## Apêndice A — Normalização de plataforma (cross-source)

`unaccent` não está instalado; uso `translate()` + remoção de não-alfanuméricos + 2 aliases manuais:

```sql
CASE regexp_replace(
       translate(upper(P),'ÇÃÂÀÁÄÉÊÈËÍÎÌÏÓÔÒÕÖÚÛÙÜÑ','CAAAAAEEEEIIIIOOOOOUUUUN'),
       '[^A-Z0-9]','','g')
  WHEN 'MAGAZINELUIZA' THEN 'MAGALU'   -- coletas 'Magalu' = pricetrack 'MAGAZINE LUIZA'
  WHEN 'ELETROZEMA'    THEN 'ZEMA'     -- coletas 'Eletrozema' = pricetrack 'ZEMA'
  ELSE regexp_replace(translate(upper(P), ...same...),'[^A-Z0-9]','','g')
END
```
Casa automaticamente: `Casas Bahia`↔`CASAS BAHIA`, `Mercado Livre`↔`MERCADO LIVRE`, `Leroy Merlin`↔`LEROY MERLIN`, `WebContinental`↔`WEB CONTINENTAL`, `CentralAr`↔`CENTRAL AR`, `FrioPecas`↔`FRIOPEÇAS`, `Climario`↔`CLIMA RIO`, `GBarbosa`↔`G BARBOSA`, `PoloAr`↔`POLO AR`, `ADias`↔`ADIAS`, `FerreiraCosta`↔`FERREIRA COSTA`. Sem par no pricetrack: Belmicro, EngageEletro, GoCompras; sem par no coletas: dezenas de dealers. **Google Shopping** é agregador e **não** existe no pricetrack (excluído de H6).

## Apêndice B — Notas de método
- Todos os números vêm de `mcp__Supabase__execute_sql` (read-only) no projeto `ailbsczkrympslpjwwko`, período `2026-06-01..2026-06-16`.
- "Combo elegível" para congelamento exige ≥10 dias distintos com dado.
- Mediana via `percentile_cont(0.5)`; moda via `mode() WITHIN GROUP`.
- Preço por oferta do pricetrack = `min_price` (justificativa em §0).
