# De-Para / SKU v2 — Dry-Run (FASES 2–3)

> Resolução de SKU por **igualdade de atributos** (`utils/sku_matcher.py`) sobre
> os **509 títulos distintos** MAPEADO do `coletas` (que cobrem **321,782** linhas).
> **Nada foi escrito em produção** para gerar este relatório: a resolução vai
> para colunas/tabelas NOVAS (`*_v2`); `produto`, `sku_resolvido` e
> `familia_resolvida` legados ficam **intactos**. Promoção só após aprovação.
>
> - Gerado por `scripts/resolve_sku_v2.py` (modo offline) · 2026-06-17
> - Universo: `estado_match='MAPEADO'` (universo endereçável; ver baseline)

---

## 1. Resumo executivo — o "SKU cheio" legado era 79% chute

| métrica (linhas MAPEADO = 321,782) | legado | v2 |
|---|--:|--:|
| SKU preenchido | 219,952 (68.4%) | **47,914 (14.9%)** |
| SKU nulo | 101,830 (31.6%) | 273,868 (85.1%) |
| **Família preenchida** | parcial/contaminada | **321,782 (100,0%)** |

Cruzando linha a linha o SKU legado com o v2 (atributos estritos):

| transição | linhas | leitura |
|---|--:|---|
| legado == v2 (confirma) | 47,020 | SKU legado defensável |
| **legado ≠ ∅ → v2 = ∅** | **172,927** | **SKU legado era chute/fusão → retirado** |
| legado = ∅ → v2 ≠ ∅ (resgate) | 889 | título nulo agora crava SKU |
| legado ≠ v2 (re-aponta) | 5 | SKU trocado por outro |

> **Achado central.** O legado cravava SKU em 219,952 linhas, mas só
> **47,020 (21%)** sobrevivem ao match
> por atributos. **172,927 linhas (79% do "SKU cheio")**
> eram modelos distintos fundidos sob um SKU (defeito B) — o v2 as devolve para
> a **família** (granularidade honesta), sem chutar SKU.

**Consequência para o dashboard:** a queda de nulo NÃO vem de cravar mais SKU
(isso seria voltar a chutar), e sim de passar a agrupar por **`familia_v2`
(100% de cobertura no MAPEADO)**, mostrando SKU só onde ele é unívoco. O filtro
"descartar SKU nulo" do Price Evolution deixa de derrubar metade da base ao
trocar a chave de agrupamento de `sku` para `COALESCE(sku_v2, familia_v2)`.

---

## 2. NULL → resolvido e cobertura, por plataforma

| plataforma | linhas MAPEADO | SKU nulo (legado) | SKU cravado v2 | resgate nulo→SKU | família v2 |
|---|--:|--:|--:|--:|--:|
| Amazon | 90,450 | 27,124 | 14,741 | 238 | 90,450 (100%) |
| Mercado Livre | 82,959 | 26,899 | 8,932 | 465 | 82,959 (100%) |
| Leroy Merlin | 62,822 | 20,652 | 10,684 | 3 | 62,822 (100%) |
| Magalu | 33,568 | 10,650 | 4,864 | 50 | 33,568 (100%) |
| Google Shopping | 20,364 | 6,229 | 3,200 | 65 | 20,364 (100%) |
| Leveros | 4,360 | 1,380 | 1,155 | 0 | 4,360 (100%) |
| FerreiraCosta | 4,687 | 1,295 | 373 | 0 | 4,687 (100%) |
| GoCompras | 1,467 | 1,046 | 320 | 0 | 1,467 (100%) |
| Frigelar | 3,835 | 944 | 867 | 0 | 3,835 (100%) |
| Dufrio | 2,401 | 943 | 555 | 0 | 2,401 (100%) |
| Shopee | 2,285 | 846 | 259 | 0 | 2,285 (100%) |
| WebContinental | 2,157 | 841 | 203 | 0 | 2,157 (100%) |
| CentralAr | 2,380 | 796 | 631 | 0 | 2,380 (100%) |
| EngageEletro | 704 | 448 | 64 | 0 | 704 (100%) |
| Belmicro | 941 | 294 | 219 | 0 | 941 (100%) |
| ADias | 453 | 221 | 14 | 0 | 453 (100%) |
| FrioPecas | 555 | 213 | 78 | 0 | 555 (100%) |
| Eletrozema | 331 | 199 | 21 | 0 | 331 (100%) |
| Casas Bahia | 746 | 197 | 101 | 7 | 746 (100%) |
| ArCerto | 1,145 | 185 | 12 | 0 | 1,145 (100%) |
| GBarbosa | 737 | 129 | 122 | 0 | 737 (100%) |
| Fujioka | 306 | 124 | 80 | 61 | 306 (100%) |
| PoloAr | 572 | 84 | 168 | 0 | 572 (100%) |
| Bemol | 966 | 60 | 120 | 0 | 966 (100%) |
| Climario | 591 | 31 | 131 | 0 | 591 (100%) |

> O "resgate nulo→SKU" é pequeno (889 linhas) **de propósito**:
> a maioria dos títulos nulos é genérica ("Gree 9.000 Inverter Frio") e não
> determina 1 SKU. Eles passam a ter **família** (não-nulo), não um SKU chutado.

---

## 3. Defeito B — fusões que se separam

Dos **85** SKUs legados que fundiam >1 título, **42 se dividem em ≥2 destinos v2**
distintos; os demais colapsam para **uma única família** (sem cravar SKU errado).
**Nenhum** SKU v2 passa a cobrir modelos distintos (Inverter≠On/Off, linhas≠).

### Os 5 SKUs de fusão do escopo — agora separados

| SKU legado (fundia) | destinos v2 (linhas) |
|---|---|
| `TAC-12CFG3W-INV` | FAMILIA:TCL-12000-F=1,533 · TAC-12CFG3W-INV=1,184 · TAC-12CTG2-INV=5 |
| `GWC09AGA` | FAMILIA:GREE-G-TOP-9000-F=2,530 · FAMILIA:GREE-9000-F=1,073 |
| `TAC-09CSA1` | FAMILIA:TCL-9000-F=1,712 · FAMILIA:TCL-ELITE-9000-F=979 |
| `S3-Q18KLR1B` | FAMILIA:LG-18000-QF=2,007 · FAMILIA:LG-DUAL-INVERTER-ARTCOOL-18000-QF=1,253 |
| `GWC18ATD` | FAMILIA:GREE-18000-F=1,544 · FAMILIA:GREE-G-TOP-18000-F=1,257 |

### Maiores fusões por volume

| SKU legado | nº destinos v2 | principais destinos (linhas) |
|---|--:|---|
| `42EZVCA12M5` | 1 | FAMILIA:MIDEA-ECOMASTER-12000-F=6,358 |
| `42EZVCA09M5` | 1 | FAMILIA:MIDEA-ECOMASTER-9000-F=6,337 |
| `HJFI09C2WC` | 2 | FAMILIA:ELGIN-9000-F=5,145 · FAMILIA:ELGIN-ECO-INVERTER-II-9000-F=1,173 |
| `42EFVCA12M5` | 1 | FAMILIA:MIDEA-12000-F=5,247 |
| `UI12F` | 2 | UI12F=4,374 · FAMILIA:ELECTROLUX-COLOR-ADAPT-12000-F=1 |
| `42EFVCA09M5` | 1 | FAMILIA:MIDEA-9000-F=4,337 |
| `PAC12000IFM15` | 1 | FAMILIA:PHILCO-12000-F=4,259 |
| `F-AR12DYFABWK` | 1 | FAMILIA:SAMSUNG-12000-F=4,239 |
| `UI09F` | 2 | FAMILIA:ELECTROLUX-COLOR-ADAPT-9000-F=4,089 · FAMILIA:ELECTROLUX-9000-F=52 |
| `42AFFCI12S5` | 1 | FAMILIA:MIDEA-12000-F=4,126 |
| `42AFVCI12S5` | 1 | FAMILIA:MIDEA-12000-F=3,738 |
| `PAC9000IFM15` | 1 | FAMILIA:PHILCO-9000-F=3,735 |

`FAMILIA:X` = resolvido até a família X (SKU fica pendente, não chutado).

---

## 4. O que vira pendência (revisão humana)

Títulos MAPEADO sem SKU único caem em `public.depara_pendencias` com atributos +
candidatos + motivo. Motivos (no nível de TÍTULO, dos 509):

- `familia_sem_sku` (369): linha genérica ou linha sem SKU único no catálogo.
- `ambiguo_multi_sku` (63): família com >1 SKU; voltagem não desempata.
- `tec_conflito` (11): título On/Off; catálogo curado é inverter-only.
- `familia_linha_unica` (66): **resolveu** (alta) — não é pendência.

Nada é chutado: o resíduo é explicável e fica para curadoria (ex.: cadastrar o
SKU On/Off no catálogo, ou desambiguar voltagem na coleta).

---

## 5. Próximos passos (gated)

1. **Validação cruzada vs pricetrack** (FASE 4) — ver seção no fim deste arquivo.
2. **Popular** `sku_catalog`, `coletas_sku_resolucao`, `depara_pendencias` (aditivo).
3. **Revisão humana** da amostra de maior impacto (seção 3) — exigida pelo escopo.
4. **Promoção** (somente após aprovação): apontar `vw_coletas_resolvida` para o
   dashboard usando `COALESCE(sku_v2, familia_v2)` como chave.


---

## 6. FASE 4 — Validação cruzada vs `pricetrack` (gabarito)

Rodando o **mesmo matcher** sobre os títulos do próprio `pricetrack_daily`
(cujo `sku` é confiável) e comparando com o SKU-gabarito, no subconjunto de SKUs
do catálogo curado (**8.856** pares título→SKU distintos):

| métrica | valor | alvo |
|---|--:|--:|
| cobertura (títulos que cravam SKU) | 2.279 (25,7%) | — |
| **precisão exata** (`sku_v2 == gabarito`) | **81,04%** | ≥ 98% ❌ |
| precisão de **modelo** (mesmo marca+BTU+ciclo) | **95,30%** | — |
| erro grosseiro (cruza marca/BTU/ciclo) | 4,70% | — |

> **Por que 81% e não ≥98%?** Os erros quase nunca são fusão de modelo: **325 dos
> 432** "erros" cravam **outro SKU do MESMO modelo/capacidade**. Causa raiz: o
> `produtos_catalogo` tem **SKUs duplicados para o mesmo produto real** — um com
> `familia_linha` preenchida, outro com `familia_linha = NULL` (ex.: Elgin Eco
> Inverter II 18k QF = `HJQI18C2WB` *e* `45HJQI18C2WC`; Gree G-Top 12k QF =
> `GWH12AGC` *e* `GWH12ATC`). O matcher escolhe o que tem `familia_linha`; o
> pricetrack rotulou com o outro. **É defeito de catálogo (FASE 1), não do
> matcher.** Restam ~107 (4,7%) erros reais (ex.: linha "Voice" caindo em
> "Compact") a endurecer no vocabulário.

**Conclusão da validação:**
- **Família/modelo: confiável** (95,3% de acerto de modelo; defeito B resolvido).
  -> **seguro promover o agrupamento por `familia_v2`** no dashboard.
- **SKU exato: ainda não atinge o alvo (81%)** -- bloqueado por **deduplicação do
  catálogo** (1 SKU canônico por modelo+voltagem) + endurecimento do vocabulário
  de linha. -> **não promover cravar SKU** antes desse passo.

### Assertivas do escopo

| assertiva | status |
|---|---|
| 5 SKUs de fusão separados (cada modelo no seu destino, ou pendência) | OK (seção 3) |
| Nenhum SKU v2 com atributos conflitantes (Inverter+On/Off) sob ele | OK (matcher garante) |
| 133/133 SKUs do coletas existem no pricetrack | OK (baseline) |
| `sku_v2` nulo cai para < 10% | família: 0% no MAPEADO (OK); SKU: alto por honestidade (ver acima) |
| precisão >= 98% no pareado | NÃO: 81% exato / 95% modelo -- depende da dedup do catálogo |
