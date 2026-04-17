# Dashboard Filters — Lógica de Seleção e Conexão

Este documento descreve como os filtros do dashboard funcionam em cada página
analítica, como eles se conectam entre si e como interagem com o banco de dados.

---

## Visão Geral

Todas as três páginas analíticas (**Price Evolution**, **BuyBox Position**,
**Availability**) compartilham o **mesmo conjunto de filtros** e o **mesmo
mecanismo de consulta** (`query_coletas`). A diferença entre elas está no que
é feito com os dados após a consulta.

```
Sidebar (filtros)
        │
        ▼
  query_coletas()          ← consulta única ao Supabase (tabela "coletas")
        │
        ▼
  DataFrame filtrado
        │
        ├─── Price Evolution  → agrega por preço (mediana por data/grupo)
        ├─── BuyBox Position  → filtra posicao_geral ≤ Top-N escolhido
        └─── Availability     → conta aparições (todas as posições)
```

---

## Filtros da Sidebar — Referência Completa

| Filtro | Tipo | Coluna no DB | Comportamento |
|---|---|---|---|
| **Date range** | Intervalo de datas | `data` | `>= start_date AND <= end_date` |
| **Tipo Plataforma** | Multiselect | `tipo` | IN (lista selecionada) |
| **Platforms** | Multiselect | `plataforma` | IN (com expansão de aliases — veja abaixo) |
| **Sellers** | Multiselect | `seller` | IN (lista exata) |
| **Brands** | Multiselect | `marca` | IN (com expansão de aliases — veja abaixo) |
| **Keywords** | Multiselect | `keyword` | IN (lista exata) |
| **Capacity (BTU)** | Multiselect | `produto` (ILIKE) | `produto ILIKE '%9000%' OR '%9.000%'` |
| **Tipo Produto** | Multiselect | `produto` (ILIKE) | `produto ILIKE '%inverter%' OR '%on/off%'` |
| **Product / SKU** | Multiselect dinâmico | `produto` | IN (lista exata, dinâmica) |
| **Top-N** | Slider (1–5) | `posicao_geral` | `<= N` (só na BuyBox) |

> **Nota:** todos os filtros são opcionais. Quando nenhum valor é selecionado,
> o filtro correspondente **não é aplicado** — o equivalente a "todos".

---

## Cadeia de Seleção Detalhada

### 1. Date Range

```
Usuário escolhe [start_date, end_date]
     │
     └─► query: .gte("data", start_date) AND .lte("data", end_date)
```

- Padrão: últimos 30 dias.
- Limite máximo: data de hoje.
- Quanto maior o intervalo, mais registros são retornados (até o limite de 50.000).

---

### 2. Tipo Plataforma

```
Usuário seleciona ["Marketplace", "Dealer"]
     │
     └─► query: .in_("tipo", ["Marketplace", "Dealer"])
```

- Opções populadas a partir dos últimos 90 dias do banco (coluna `tipo`).
- Valores típicos: `Marketplace`, `Dealer`.
- Age de forma **independente** do filtro **Platforms**.

---

### 3. Platforms (com normalização de aliases)

```
Usuário seleciona ["Ferreira Costa"]
     │
     ▼
_expand_platforms(["Ferreira Costa"])
     │
     └─► ["FerreiraCosta", "FerreiraCoasta"]   ← todos os valores brutos no DB
          │
          └─► query: .in_("plataforma", ["FerreiraCosta", "FerreiraCoasta"])
```

**Aliases configurados** (`_PLATFORM_ALIASES` em `app.py`):

| Nome no Dropdown | Valores no DB |
|---|---|
| `Ferreira Costa` | `FerreiraCosta`, `FerreiraCoasta` |
| `WebContinental` | `WebContinental`, `Webcontinental` |

- Para todas as outras plataformas, o nome exibido = o valor bruto no DB.
- As opções do dropdown são normalizadas via `_normalize_platform()` ao serem
  carregadas, então o usuário nunca vê duplicatas.

---

### 4. Brands (com normalização e expansão canônica)

```
Usuário seleciona ["Midea"]
     │
     ▼
_expand_brands(["Midea"])
     │
     └─► ["Midea", "Springer Midea", "Midea Carrier", "Springer"]
          │
          └─► query: .in_("marca", [...todos os variants...])
```

O sistema usa dois mapas de normalização construídos a partir de `config.BRANDS`
e `utils/normalize_product._BRAND_ALIASES`:

- **`_CANONICAL_TO_MARCAS`**: nome canônico → todos os valores brutos do DB
- **`_MARCA_TO_CANONICAL`**: valor bruto → nome canônico

**Por que isso importa:**
A coleta pode registrar o mesmo fabricante com grafias diferentes
(`Springer Midea`, `Midea Carrier`, `Springer`). O filtro expande o nome
canônico para garantir que todos os registros relacionados sejam retornados.

**Normalização pós-consulta:** após retornar os dados, `query_coletas` aplica
`_MARCA_TO_CANONICAL` sobre a coluna `marca` do DataFrame — assim os gráficos
e tabelas mostram sempre o nome canônico, nunca os aliases.

---

### 5. Sellers

```
Usuário seleciona ["Midea Brasil", "Magazine Luiza"]
     │
     └─► query: .in_("seller", ["Midea Brasil", "Magazine Luiza"])
```

- Sem expansão de aliases: os valores usados são exatamente os do banco.
- Opções populadas dos últimos 90 dias (coluna `seller`).

---

### 6. Keywords

```
Usuário seleciona ["ar condicionado 12000 btu inverter"]
     │
     └─► query: .in_("keyword", ["ar condicionado 12000 btu inverter"])
```

- Representa o termo de busca usado durante a coleta.
- Útil para isolar uma keyword específica ao analisar posicionamento.

---

### 7. Capacity (BTU) — filtro por nome do produto

```
Usuário seleciona ["12000", "18000"]
     │
     ▼
Gera partes de ILIKE para cada valor + sua forma pontuada:
  "12000" → produto ILIKE '%12000%' OR produto ILIKE '%12.000%'
  "18000" → produto ILIKE '%18000%' OR produto ILIKE '%18.000%'
     │
     └─► query: .or_("produto.ilike.%12000%,produto.ilike.%12.000%,
                      produto.ilike.%18000%,produto.ilike.%18.000%")
```

- Valores disponíveis: `9000`, `12000`, `18000`, `24000`, `36000`, `48000`, `60000`.
- Busca dentro do nome do produto (coluna `produto`).
- Trata automaticamente ambos os formatos: `12000` e `12.000`.

---

### 8. Tipo Produto — filtro por padrão de texto

```
Usuário seleciona ["Inverter", "Hi-Wall"]
     │
     ▼
Expande para padrões de texto:
  "Inverter" → produto ILIKE '%inverter%'
  "Hi-Wall"  → produto ILIKE '%hi-wall%' OR '%hi wall%' OR '%hiwall%'
     │
     └─► query: .or_("produto.ilike.%inverter%,produto.ilike.%hi-wall%,...")
```

**Opções e seus padrões de busca:**

| Label | Padrões buscados no nome do produto |
|---|---|
| Inverter | `inverter` |
| On/Off | `on/off`, `on-off`, `convencional` |
| Hi-Wall | `hi-wall`, `hi wall`, `hiwall` |
| Janela | `janela`, `janeleiro`, `window` |
| Cassete | `cassete`, `cassette` |
| Piso-Teto | `piso-teto`, `piso teto` |
| Portátil | `portátil`, `portatil` |

> **Atenção:** BTU e Tipo Produto se combinam com **AND** entre si (um produto
> precisa passar pelos dois filtros), mas internamente cada filtro usa **OR**
> entre suas variantes.

---

### 9. Product / SKU — seleção dinâmica (drill-down)

```
Estados dos filtros: sel_brands=["Midea"], sel_btu=["12000"], sel_ptype=["Inverter"]
     │
     ▼
get_sku_options(brands=("Midea",), btu_filter=("12000",), product_types=("Inverter",))
     │  ← consulta independente ao Supabase (últimos 90 dias)
     │  ← aplica os mesmos filtros de marca/BTU/tipo para narrowing
     ▼
Lista de SKUs disponíveis (ex.: 23 disponíveis)
     │
     ▼
Dropdown: "Product / SKU  (23 available)"
     │
Usuário seleciona ["Midea MAC12CS1 12000 BTU Inverter"]
     │
└─► query principal: .in_("produto", ["Midea MAC12CS1 12000 BTU Inverter"])
```

**Comportamento de narrowing:**
- **Sem filtros** → mostra todos os SKUs dos últimos 90 dias
- **Com Brand** → mostra somente SKUs daquela(s) marca(s)
- **Com BTU** → filtra SKUs que contenham a capacidade no nome
- **Com Tipo** → filtra SKUs que contenham o padrão (ex.: "inverter")
- **Combinados** → AND entre os três critérios

O dropdown é **re-computado** a cada mudança nos filtros Brand/BTU/Tipo,
garantindo que a lista sempre reflita o escopo atual.

> **Nota de cache:** `get_sku_options` usa `@st.cache_data(ttl=300)` — os
> resultados são reutilizados por 5 minutos enquanto os parâmetros forem iguais.

---

## Como os Filtros se Combinam (lógica AND/OR)

```
Todos os filtros se combinam com AND entre si:

  data BETWEEN start AND end
  AND tipo IN [...]             (se Tipo Plataforma selecionado)
  AND plataforma IN [...]       (se Platforms selecionado, com aliases)
  AND marca IN [...]            (se Brands selecionado, com expansão)
  AND seller IN [...]           (se Sellers selecionado)
  AND keyword IN [...]          (se Keywords selecionado)
  AND (produto ILIKE %btu1% OR produto ILIKE %btu2%)   (se BTU selecionado)
  AND (produto ILIKE %tipo1% OR produto ILIKE %tipo2%)  (se Tipo Produto)
  AND produto IN [...]          (se SKU selecionado)
  AND posicao_geral <= N        (apenas na BuyBox, se Top-N < 5)
```

Filtros não selecionados são simplesmente omitidos da consulta — sem efeito.

---

## Ordem de Dependência dos Filtros (do mais geral ao mais específico)

```
1. Date range          → define a janela temporal base
2. Tipo Plataforma     → restringe o tipo de canal (Marketplace vs. Dealer)
3. Platforms           → restringe para plataformas específicas
4. Brands              → restringe marcas (expande aliases automaticamente)
5. Sellers             → restringe vendedores dentro das marcas/plataformas
6. Keywords            → restringe por termo de busca da coleta
7. Capacity (BTU)      → restringe por capacidade no nome do produto
8. Tipo Produto        → restringe por tipo/forma (Inverter, Hi-Wall, etc.)
9. Product / SKU       ← narrowing dinâmico: lista depende de 4+7+8
```

> **Recomendação de uso:** selecione primeiro os filtros mais amplos (data,
> tipo, plataforma, marca) para reduzir o universo, e então use BTU/Tipo/SKU
> para aprofundar a análise.

---

## Diferenças Entre as Três Páginas

### Price Evolution — `page_price_evolution()`

| Aspecto | Detalhe |
|---|---|
| **O que analisa** | Evolução de preços ao longo do tempo |
| **Filtro exclusivo** | Nenhum — mesmos filtros das outras páginas |
| **Group by** | Radio: `Product`, `Brand`, `Platform` |
| **Agregação** | Mediana do preço por (data, grupo) |
| **Tab: Price Chart** | Gráfico de linha — preço mediano por data |
| **Tab: Summary** | Tabela com Min/Mediana/Média/Máx por grupo |
| **Tab: Detail** | Todos os registros brutos (colunas: data, turno, plataforma, marca, produto, posição, preço, seller, keyword, tag) |
| **Download** | CSV com os registros do Detail (`rac_price_evolution_YYYY-MM-DD_YYYY-MM-DD.csv`) |

**Fluxo interno:**
```
df (todos registros) → dropna(preco, data) → agg mediana por (data, group_col)
                     → Tab Chart: px.line
                     → Tab Summary: groupby(group_col)[preco].agg(...)
                     → Tab Detail: df bruto com display_cols
```

---

### BuyBox Position — `page_buybox_position()`

| Aspecto | Detalhe |
|---|---|
| **O que analisa** | Quem ocupa as primeiras posições |
| **Filtro exclusivo** | **Top-N** (slider 1–5): define o limiar de posição |
| **Envio server-side** | `max_position=top_n` é enviado ao Supabase (`.lte("posicao_geral", N)`) |
| **Filtro local** | `df_top = df[posicao_geral <= top_n]` (defesa em profundidade) |
| **Tab: Win Rate** | Bar chart (top 15 marcas) + pie chart por plataforma |
| **Tab: Timeline** | Gráfico de linha diário — wins por Brand ou Platform (radio interno) |
| **Tab: Detail** | Registros com posição ≤ N (colunas extras: posicao_organica, posicao_patrocinada) |
| **Download** | CSV com registros do Detail (`rac_buybox_YYYY-MM-DD_YYYY-MM-DD.csv`) |

**Fluxo interno:**
```
df (posicao_geral <= N) → df_top
     │
     ├─ Tab Win Rate: groupby(marca).size() → bar + pie
     ├─ Tab Timeline: groupby(data, grupo).size() → line
     └─ Tab Detail:   df_top[display_cols]
```

---

### Availability — `page_availability()`

| Aspecto | Detalhe |
|---|---|
| **O que analisa** | Presença de marcas em **todas** as posições coletadas |
| **Filtro exclusivo** | Nenhum — sem filtro de posição (inclui todas) |
| **Diferença do BuyBox** | Não filtra por posição; mostra share de aparições totais |
| **Tab: Share** | Bar chart (top 15 marcas por aparições) + pie chart por plataforma |
| **Tab: Timeline** | Gráfico de linha diário — aparições por Brand ou Platform (radio interno) |
| **Tab: Detail** | Todos os registros com posicao_geral preenchida |
| **Download** | CSV com registros do Detail (`rac_availability_YYYY-MM-DD_YYYY-MM-DD.csv`) |

**Fluxo interno:**
```
df (posicao_geral not null) → df_all
     │
     ├─ Tab Share:    groupby(marca).size() → bar + pie
     ├─ Tab Timeline: groupby(data, grupo).size() → line
     └─ Tab Detail:   df_all[display_cols]
```

---

## Limite de Registros

Todas as páginas usam `limit=50000` na consulta ao Supabase. Esse limite é
aplicado **após** todos os filtros, então filtros mais específicos retornam
proporcionalmente mais dados dentro do período selecionado.

```
Número real de registros retornados
  = mín(registros que atendem todos os filtros no período, 50000)
```

O contador de registros carregados é exibido abaixo do título de cada página
após o carregamento.

---

## Carregamento dos Dados

Nenhuma página carrega dados automaticamente. Todas exigem que o usuário:

1. Configure os filtros na sidebar
2. Clique no botão de carregamento primário:
   - **Price Evolution** → `🔄 Load Chart`
   - **BuyBox Position** → `🔄 Load BuyBox`
   - **Availability** → `🔄 Load Availability`

Isso evita consultas desnecessárias ao banco ao navegar entre páginas ou ao
ajustar filtros sem intenção de carregar.

---

## Opções dos Dropdowns — Origem e Escopo

```
get_filter_options()
  └─► consulta ao Supabase: SELECT DISTINCT plataforma, tipo, marca, keyword, seller
      WHERE data >= hoje - 90 dias
      LIMIT 50000
```

Todas as opções de filtro refletem dados dos **últimos 90 dias**,
independentemente do date range selecionado pelo usuário. Isso garante que
os dropdowns estejam sempre populados com valores relevantes.

As opções são normalizadas antes de serem exibidas:
- **Marcas**: aliases resolvidos para nomes canônicos (`_MARCA_TO_CANONICAL`)
- **Plataformas**: variações resolvidas para nomes canônicos (`_normalize_platform`)

---

## Diagrama de Fluxo Completo

```
┌─────────────────────────────────────────────────────────────┐
│                         SIDEBAR                             │
│                                                             │
│  get_filter_options()  ──►  [Tipo Plataforma dropdown]     │
│  (últimos 90 dias)     ──►  [Platforms dropdown]           │
│                        ──►  [Sellers dropdown]             │
│                        ──►  [Brands dropdown]              │
│                        ──►  [Keywords dropdown]            │
│                                                             │
│  [Date range picker]                                        │
│  [BTU multiselect]  (valores estáticos)                     │
│  [Tipo Produto multiselect]  (valores estáticos)            │
│                                                             │
│  get_sku_options(brands, btu, tipo)  →  [SKU dropdown]     │
│  (cache 5 min, últimos 90 dias)                             │
│                                                             │
│  [Top-N slider]  ← apenas BuyBox                           │
│                                                             │
│  [🔄 Load Button]  ──────────────────────────────────┐     │
└──────────────────────────────────────────────────────│─────┘
                                                       │
                                                       ▼
                                            query_coletas(...)
                                                       │
                                         ┌─────────────┴──────────────────┐
                                         │   Supabase — tabela "coletas"  │
                                         │                                │
                                         │  .gte("data", start)           │
                                         │  .lte("data", end)             │
                                         │  .in_("tipo", [...])           │
                                         │  .in_("plataforma", expand())  │
                                         │  .in_("marca", expand())       │
                                         │  .in_("seller", [...])         │
                                         │  .in_("keyword", [...])        │
                                         │  .or_(produto ilike btus)      │
                                         │  .or_(produto ilike tipos)     │
                                         │  .in_("produto", [...])        │
                                         │  .lte("posicao_geral", N)      │
                                         │  .limit(50000)                 │
                                         └────────────────────────────────┘
                                                       │
                                                       ▼
                                              DataFrame filtrado
                                                       │
                          ┌────────────────────────────┼───────────────────────┐
                          │                            │                       │
                          ▼                            ▼                       ▼
               Price Evolution                 BuyBox Position           Availability
               ─────────────────              ─────────────────          ─────────────
               Tab: Price Chart               Tab: Win Rate              Tab: Share
               Tab: Summary                   Tab: Timeline              Tab: Timeline
               Tab: Detail + CSV              Tab: Detail + CSV          Tab: Detail + CSV
```
