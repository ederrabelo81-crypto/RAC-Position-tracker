# Catálogo canônico de SKU — dedup + split (FASE 1, data-driven)

Re-deriva a LINHA de cada SKU dos títulos reais no `pricetrack_daily` e:
- **SPLIT** famílias grossas do `produtos_catalogo` (Serie A1 ≠ Elite GV ≠ …);
- **DEDUP** SKUs do mesmo produto (mesma marca+BTU+ciclo+linha+voltagem) num
  `sku_canonico` (o de maior volume no pricetrack).

Gerado por `scripts/build_sku_catalog.py` (offline) · 2026-06-17.

## Impacto na precisão (vs gabarito pricetrack, no subconjunto cravado)

| catálogo | cravado | exato | **precisão** |
|---|--:|--:|--:|
| antes (curado, familia_linha original) | 2279 | 1847 | 81.04% |
| **depois (refinado + canônico)** | 6178 | 5455 | **88.3%** |

## Grupos de SKUs duplicados colapsados (42 grupos, 60 SKUs absorvidos)

| canônico | absorvidos | modelo |
|---|---|---|
| `UI12F` (canônico) | `QI12F` | ELECTROLUX-COLOR-ADAPT-12000-F 220V |
| `JI18R/JE18R` (canônico) | `UI18R/UE18R` | ELECTROLUX-COLOR-ADAPT-18000-QF  |
| `45HJQI18C2WC` (canônico) | `HJQI18C2WB` | ELGIN-ECO-INVERTER-II-18000-QF 220V |
| `HJQI24C2WC` (canônico) | `HJQI24C2WB` | ELGIN-ECO-INVERTER-II-24000-QF 220V |
| `HJFI12C2WD` (canônico) | `HJFI12C2IA` · `HJFI12C2WC` | ELGIN-ECO-INVERTER-III-12000-F 220V |
| `GWH18ATD` (canônico) | `GWH18AGD` | GREE-G-TOP-AUTO-18000-QF 220V |
| `GWH30ATEXF-S6DNA1A` (canônico) | `GWH30AGE` | GREE-G-TOP-AUTO-30000-QF 220V |
| `GWC09ATB-D6DNA1A` (canônico) | `GWC09AGA` | GREE-G-TOP-AUTO-9000-F 220V |
| `S3-Q12JA31K` (canônico) | `S3-Q12JA31E` · `S4-Q12JA315` · `S4-Q12JA31C` | LG-DUAL-INVERTER-AI-VOICE-12000-F 220V |
| `S4-Q18KL31B` (canônico) | `S3-Q18KL33B` | LG-DUAL-INVERTER-AI-VOICE-18000-F 220V |
| `S3-W18KL31A` (canônico) | `S4-W18KL31A` | LG-DUAL-INVERTER-AI-VOICE-18000-QF 220V |
| `S3-Q09AA31A` (canônico) | `S3-Q09AA31F` · `S3-Q09AA33A` · `S3-Q09JA31E` | LG-DUAL-INVERTER-AI-VOICE-9000-F 220V |
| `S3-W18KLR7A` (canônico) | `S3-Q18KLR1B` · `S4-W18KLRXC` | LG-DUAL-INVERTER-ARTCOOL-18000-QF 220V |
| `S3-W24K2R7A` (canônico) | `S4-W24K2RXD` | LG-DUAL-INVERTER-ARTCOOL-24000-QF 220V |
| `S3-Q12JAQAL` (canônico) | `S3-Q12JA31L` · `S4-Q12JA3A5` · `S4-Q12JA3AD` | LG-DUAL-INVERTER-COMPACT-AI-12000-F 220V |
| `S3-Q18KLQAL` (canônico) | `S3-Q18KL31B` | LG-DUAL-INVERTER-COMPACT-AI-18000-F 220V |
| `S3-Q09AAQAL` (canônico) | `S4-Q09WA5AA` | LG-DUAL-INVERTER-COMPACT-AI-9000-F 220V |
| `42AFVCI12S5` (canônico) | `42AFFCI12S5` · `42EFVCA12M5` | MIDEA-AI-AIRVOLUTION-12000-F 220V |
| `42AFVCI18S5` (canônico) | `42AFFCI18S5` · `42EFVCA18M5` | MIDEA-AI-AIRVOLUTION-18000-F 220V |
| `42AFVCI22S5` (canônico) | `42AFFCI22S5` · `42EFVCA22M5` | MIDEA-AI-AIRVOLUTION-22000-F 220V |
| `42EFVCA09M5` (canônico) | `42AFFCI09S5` | MIDEA-AI-AIRVOLUTION-9000-F 220V |
| `42EZVCA12M5` (canônico) | `38EZVCA12M5` | MIDEA-AI-ECOMASTER-12000-F 220V |
| `42EZVQA12M5` (canônico) | `38EZVQA12M5` | MIDEA-AI-ECOMASTER-12000-QF 220V |
| `42EZVCA18M5` (canônico) | `38EZVCA18M5` | MIDEA-AI-ECOMASTER-18000-F 220V |
| `42EZVCA24M5` (canônico) | `38EZVCA24M5` | MIDEA-AI-ECOMASTER-24000-F 220V |
| `42EZVCA09M5` (canônico) | `38EZVCA09M5` | MIDEA-AI-ECOMASTER-9000-F 220V |
| `42EZVQA09M5` (canônico) | `38EZVQA09M5` | MIDEA-AI-ECOMASTER-9000-QF 220V |
| `42EBVCA12M5` (canônico) | `38TBVCA12M5` | MIDEA-AIRVOLUTION-LITE-12000-F 220V |
| `42EBVCA09M5` (canônico) | `38TBVCA09M5` | MIDEA-AIRVOLUTION-LITE-9000-F 220V |
| `PAC12FB` (canônico) | `PAC12000IFM15` · `PAC12FC` · `PAC12FI` | PHILCO-ECO-INVERTER-12000-F 220V |
| `PAC12QC` (canônico) | `PAC12000IQFM15` | PHILCO-ECO-INVERTER-12000-QF 220V |
| `PAC18000IFM15` (canônico) | `PAC18FC` | PHILCO-ECO-INVERTER-18000-F 220V |
| `PAC18QA` (canônico) | `PAC18QI` | PHILCO-ECO-INVERTER-18000-QF 220V |
| `PAC9FB` (canônico) | `PAC9000IFM15` · `PAC9000ITFM9W` · `PAC9000TFM9` · `PAC9FC` · `PAC9FI` | PHILCO-ECO-INVERTER-9000-F 220V |
| `PAC9000IQFM15E` (canônico) | `PAC9000TQFM12` | PHILCO-ECO-INVERTER-9000-QF 220V |
| `AR12DYFAAWK/AZ` (canônico) | `F-AR12DYFABWK` | SAMSUNG-WINDFREE-AI-12000-F 220V |
| `AR18DYFAAWK/AZ` (canônico) | `AR18CVFAAWK/AZ` · `AR60F18D1AWNAZ` | SAMSUNG-WINDFREE-AI-18000-F 220V |
| `AR24DYFABWKNAZ` (canônico) | `AR60F24D1AWN/AZ` | SAMSUNG-WINDFREE-AI-24000-F 220V |
| `F-AR24DXFAAWK` (canônico) | `AR24TSHCBWKN/AZ` | SAMSUNG-WINDFREE-AI-24000-QF 220V |
| `TAC12CSG` (canônico) | `TAC-12CGV-INV` | TCL-ELITE-GV-12000-F 220V |
| `TAC-09CSA1` (canônico) | `TAC-09CTG1` | TCL-SERIE-A1-9000-F 220V |
| `TAC-24CHTG2` (canônico) | `TAC-24CHTG1` | TCL-T-PRO-2-0-24000-QF 220V |

> CSV completo do catálogo refinado: `reports/sku_catalog_refined.csv`.
> Aplicação em produção (atualizar `produtos_catalogo.familia_linha` + coluna
> `sku_canonico`) é **gated** — revisar este relatório antes.


## Erros remanescentes (após refino) — por que 88% e não 98%

No subconjunto cravado (6.178), os 723 erros vs gabarito se distribuem em:

| categoria | qtde | natureza |
|---|--:|---|
| **confusão de sub-linha** | 492 | linhas próximas que os títulos misturam |
| voltagem / dedup (mesma família) | 126 | null vs 220V não colapsados — follow-up barato |
| SKU-gabarito sem linha detectável | 105 | título do próprio gabarito é genérico |

Principais confusões de sub-linha (não são fusão Inverter/On-Off — são linhas
irmãs Dual Inverter, que o título de marketplace embaralha):

- **LG `Dual Inverter Voice` ⟷ `Compact +AI`** (~186): títulos trazem "Voice",
  "+AI" e "Compact" juntos; a linha modal fica ambígua.
- **Elgin `Eco Inverter II` ⟷ `III`** (~42).
- **Midea `AI Airvolution` ⟷ `AI Airvolution Connect`** (~26).
- **Midea `Xtreme Save Connect` ⟷ `…Black Edition`** (~25).

### Conclusão

A deduplicação do catálogo **subiu o SKU-exato de 81% → 88,3%** e quase
**triplicou a cobertura** (2.279 → 6.178), mas **98% de SKU-exato não é
alcançável só por dedup**: o teto restante é **ambiguidade de sub-linha**, que
em parte é irredutível no título do marketplace.

**Recomendação mantida:** o grão confiável é **família/linha** (Voice e Compact
viram famílias distintas — não há fusão). Cravar SKU exato só onde a sub-linha é
inequívoca; o resto resolve até família ou vai a pendência. Para o
price-evolution, agrupar coletas E pricetrack por **família** (não por SKU) evita
o teto de sub-linha por completo.

### Próximos passos para subir o SKU-exato (incremental)
1. Dedup tolerante a voltagem (null ≡ 220V quando é o único par) → +~2%.
2. Endurecer vocabulário LG Voice/Compact/+AI e Elgin II/III em
   `utils/normalize_product._LINE_PATTERNS` (ordem e desempate).
3. Re-rodar `build_sku_catalog.py` e medir; iterar até o platô.
