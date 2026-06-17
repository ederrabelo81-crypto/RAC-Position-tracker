# De-Para / SKU Resolution — Baseline (FASE 0)

> Diagnóstico **somente leitura** do estado atual da resolução de SKU em
> `public.coletas`, antes de qualquer escrita. Reproduz e atualiza os números do
> relatório de validação citado no escopo (`validacao_discrepancias_20260616.md`,
> que **não** está versionado neste repo).
>
> - **Projeto Supabase:** `ailbsczkrympslpjwwko` (RAC, sa-east-1, Postgres 17)
> - **Gerado em:** 2026-06-17
> - **Janela dos dados:** `coletas` 2026-03-26 → 2026-06-17 · `pricetrack_daily` 2026-01-01 → 2026-06-16

---

## 1. Como o `sku_resolvido` é populado hoje (mecanismo)

Cadeia de resolução atual (toda baseada em **igualdade de string de título**):

```
scrapers → coletas.produto (título, em grande parte já normalizado)
                 │
   scripts/montar_depara.py  (classifica nomes DISTINTOS)
        └── utils/depara_resolver.py::resolve_depara()
              • detecta marca/BTU/ciclo (utils/normalize_product.py)
              • estado = MAPEADO | FORA_ESCOPO | NAO_AC | REVISAR
              • familia = <MARCA>-<BTU>-<CICLO> (ou familia_linha do catálogo)
              • sku = **SEMPRE None**  ← política "família-linha, sem SKU"
                 │
   public.produtos_depara_nome (nome_coletado → estado/familia/sku/marca_norm)
        • sku preenchido só por SEED manual / public.produtos_aliases
                 │
   RPC public.resolver_coletas_pendentes()  (migration 004, PASSO 6)
        UPDATE coletas SET familia_resolvida=d.familia, sku_resolvido=d.sku,
                            estado_match=d.estado
        FROM produtos_depara_nome d
        WHERE c.produto = d.nome_coletado   ← **match exato de string**
```

**Conclusão de mecanismo:** o `sku_resolvido` **não é derivado por atributos**.
Ele é apenas copiado de `produtos_depara_nome.sku`, que por sua vez foi **semeado
manualmente** (origem `seed`/`produtos_aliases`) por título. Isso explica os dois
defeitos:

- **Falso negativo (NULL):** título sem entrada-semente com `sku` → fica NULL.
- **Falso positivo (fusão):** vários títulos DISTINTOS foram semeados apontando
  para o MESMO `sku`, inclusive títulos de modelos diferentes.

Arquivos-chave: `utils/depara_resolver.py`, `utils/normalize_product.py`,
`scripts/montar_depara.py`, `scripts/resolver_diario.py`,
`docs/migrations/004_produto_normalizado.sql`.

---

## 2. Defeito A — SKU nulo (falso negativo)

`coletas`: **417.235** linhas · **197.283** com `sku_resolvido` NULL → **47,28 %**.
*(O relatório original media 45,98 % sobre uma base menor de 109.198 linhas; a base
cresceu, o percentual se manteve ~46–47 %.)*

### Por estado de classificação (`estado_match`)

| estado_match | linhas | sku NULL | distinct produto | distinct sku |
|---|--:|--:|--:|--:|
| **MAPEADO** | 321.782 | **101.830** | 509 | 133 |
| FORA_ESCOPO | 93.009 | 93.009 | 1.243 | 0 |
| NAO_AC | 2.403 | 2.403 | 126 | 0 |
| (null) | 30 | 30 | 0 | 0 |
| REVISAR | 11 | 11 | 7 | 0 |

> **Universo endereçável = MAPEADO.** Dos 197.283 nulos, **101.830 (51,6 %)** são
> MAPEADO sem SKU (são ACs do catálogo, resolvíveis por atributo). Os outros
> 95.442 são FORA_ESCOPO/NAO_AC — **legitimamente nulos** (janela, portátil,
> cassete, ≥36k, marcas fora do catálogo, não-AC); não devem ser "chutados".

### Por plataforma (top de nulos)

| plataforma | total | sku NULL | % nulo |
|---|--:|--:|--:|
| Mercado Livre | 115.169 | 59.109 | 51,32 % |
| Amazon | 113.794 | 50.468 | 44,35 % |
| Leroy Merlin | 77.105 | 34.935 | 45,31 % |
| Magalu | 43.165 | 20.247 | 46,91 % |
| Google Shopping | 30.428 | 16.293 | 53,55 % |
| Leveros | 6.016 | 3.036 | 50,47 % |
| *(demais dealers)* | — | ~11.000 | 6–77 % |

---

## 3. Defeito B — Fusão de modelos (falso positivo)

Dos **133** SKUs em uso no `coletas`, **85 (63,9 %)** agregam **mais de um título
distinto** em `produto` (máx. **7** títulos sob um único SKU).
*(O relatório original media 53,1 %; subiu para 63,9 % com mais dados.)*

| recorte | qtde |
|---|--:|
| SKUs com >1 título distinto (fundidos) | **85 / 133** |
| …que misturam **modelos distintos** (não só cor) | **85** (100 %) |
| …que misturam **Inverter + On/Off** (tecnologia diferente) | **31** |
| …que diferem **apenas por cor** | **0** |

**Nenhuma fusão é benigna (cor):** todas as 85 colapsam modelos diferentes; 31
chegam a juntar tecnologias diferentes (Inverter com On/Off) sob o mesmo SKU.

### Os 5 SKUs de fusão citados no escopo (confirmados)

| SKU | títulos distintos fundidos (resumo) |
|---|---|
| **TAC-12CFG3W-INV** | TCL Serie A1 **On/Off** · FreshIN 3.0 Inverter · FreshIN 3.0 **Black** · TCL 12k Inverter · TCL 12k **On/Off** · T-Pro 2.0 |
| **GWC09AGA** | Gree G-Top Auto · Gree 9k Inverter · G-Top Connection · G-Clima · Gree 9k **On/Off** |
| **TAC-09CSA1** | TCL Serie A1 **On/Off** · TCL Elite Inverter · TCL Elite **On/Off** · TCL Serie A2 Inverter · TCL 9k Inverter |
| **S3-Q18KLR1B** | LG Dual Inverter AI **Voice** · **ARTCOOL** · **UV Nano** · Dual Inverter · LG 18k Q/F |
| **GWC18ATD** | Gree G-Top Auto · G-Clima · Gree 18k Inverter · Gree 18k **Preto** · G-Top Connection |

Cada um destes deve, após a correção, **separar cada modelo no seu próprio SKU**
ou ir para pendências — **nenhum** pode permanecer fundido.

---

## 4. Alavanca de referência — `pricetrack_daily`

| métrica | valor | observação |
|---|--:|---|
| linhas | 1.163.370 | histórico desde 2026-01-01 |
| SKUs distintos | **583** | *(escopo citava 526; cresceu)* |
| títulos distintos | **12.184** | **≠ 1 título por SKU** |
| SKUs com >1 título | **512 / 583** | máx. **142** títulos/SKU |
| SKUs com título único | 71 | — |

> ⚠️ **Correção de premissa do escopo.** O `pricetrack_daily` **não** tem
> "1 título canônico por SKU". Ele é limpo **no nível de SKU** (todo registro tem
> um `sku` confiável, vindo do PriceTrack upstream), mas o `title` agrega muitas
> variantes de marketplace por SKU. Logo, ele serve como:
> 1. **gabarito de rótulo** (pares *título → SKU confiável*) para validação cruzada;
> 2. **fonte de vocabulário** (sinônimos por atributo) minerável dos títulos;
> 3. **catálogo de SKUs** (universo de SKUs válidos).

> ⚠️ **Segunda correção.** O escopo assume "catálogo 100 % inverter, mande On/Off
> para pendências". Verdade só para o **curado** `produtos_catalogo` (inverter-only).
> O `pricetrack_daily` completo tem **27 SKUs On/Off** (7.267 títulos). Construindo
> o catálogo a partir do pricetrack, dá para **resolver corretamente** títulos
> On/Off para SKUs On/Off — melhor do que mandar para pendências.

### Namespace (consistência de SKU)

| checagem | resultado |
|---|---|
| SKUs do `coletas` que existem no `pricetrack` | **133 / 133** ✅ |
| SKUs do `coletas` que existem no `produtos_catalogo` | **133 / 133** ✅ |
| `produtos_catalogo` (241) ⊂ `pricetrack` | **241 / 241** ✅ |

O namespace é consistente: tudo que o `coletas` usa existe no pricetrack. O
`produtos_catalogo` é um subconjunto curado (241 de 583 SKUs).

### Ambiguidade legítima no catálogo (por que "não chutar" importa)

Há vários SKUs por `(marca, BTU, ciclo)` — diferem por **linha** e **voltagem**:

| (marca, BTU, ciclo) | nº SKUs | nº linhas | nº voltagens |
|---|--:|--:|--:|
| MIDEA 12000 FRIO | 10 | 6 | 2 |
| LG 12000 FRIO | 9 | 3 | 2 |
| MIDEA 9000 FRIO | 8 | 5 | 2 |
| LG 9000 FRIO | 7 | 2 | 1 |

→ Um título genérico ("LG Dual Inverter 9.000 Frio", sem linha/voltagem) casa com
**vários** SKUs. O correto é **pendência** (ambíguo) ou resolução só até
**família**, nunca cravar 1 SKU arbitrário (isso seria reintroduzir o defeito B).

---

## 5. Achado de segurança (reportado, não corrigido)

O *advisor* do Supabase aponta **RLS desabilitado** em 10 tabelas `public`
(`coletas`, `pricetrack_daily`, `produtos_catalogo`, `produtos_depara_nome`,
`produtos_aliases`, `rac_monitoramento`, `rac_products_magalu_shopee`,
`pricetrack_import_log`, `admin_automation_runs`, `admin_automation_lock`).
Com a `anon key`, qualquer um lê/escreve todas as linhas.

**Não corrigido aqui** (habilitar RLS sem policies derruba todo o acesso da app).
Ver `docs/SECURITY_TODO_RLS.md`. Decisão e policies ficam a cargo do mantenedor.

---

## 6. Implicações para o desenho da correção

1. **Resolver por atributos** (marca+BTU+ciclo+tecnologia+edição[+voltagem]),
   não por string — aplicando **o mesmo parser** (`utils/attr_parser.py`) ao
   `coletas.produto` e ao título canônico do catálogo.
2. **Alta confiança = 1 único SKU candidato.** 0 ou >1 candidato, ou atributo
   essencial faltando → **NULL + pendência** (com atributos e candidatos).
3. **Anti-fusão:** Inverter ≠ On/Off ≠ Dual Inverter; base ≠ Elite; linha/edição
   diferente = SKU diferente. As 31 fusões Inverter+On/Off e as 85 de modelo
   distinto têm de se separar.
4. **Meta de NULL (proposta):** medir sobre o **universo endereçável (MAPEADO)**.
   Reduzir o SKU-nulo de **101.830 / 321.782 (31,6 %)** para **< 10 %** do MAPEADO,
   com o resíduo em pendências (explicado, não chutado). Sobre a base total, o piso
   é ~23 % (FORA_ESCOPO/NAO_AC), que é nulo legítimo. Onde 1 SKU não é unívoco,
   resolver até **família** (não-nulo e honesto) em vez de forçar SKU.
5. **Tudo aditivo e reversível:** resultado em coluna/tabela NOVA (`*_v2`),
   `produto` e `sku_resolvido` legados intactos; promoção só após dry-run aprovado.
