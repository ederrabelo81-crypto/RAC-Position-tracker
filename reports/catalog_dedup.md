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
| **depois (refinado + canônico)** | 6381 | 5765 | **90.35%** |

## Grupos de SKUs duplicados colapsados (48 grupos, 69 SKUs absorvidos)

| canônico | absorvidos | modelo |
|---|---|---|
| `UI12F` (canônico) | `QI12F` | ELECTROLUX-COLOR-ADAPT-12000-F 220V |
| `UI18F` (canônico) | `JI18F/JE18F` | ELECTROLUX-COLOR-ADAPT-18000-F 220V |
| `JI18R/JE18R` (canônico) | `UI18R/UE18R` | ELECTROLUX-COLOR-ADAPT-18000-QF  |
| `UI24F` (canônico) | `JI24F/JE24F` | ELECTROLUX-COLOR-ADAPT-24000-F 220V |
| `UI09F` (canônico) | `JI09F/JE09F` | ELECTROLUX-COLOR-ADAPT-9000-F 220V |
| `QI09R` (canônico) | `JI09R/JE09R` | ELECTROLUX-COLOR-ADAPT-9000-QF 220V |
| `45HJQI18C2WC` (canônico) | `HJQI18C2WB` | ELGIN-ECO-INVERTER-II-18000-QF 220V |
| `HJQI24C2WC` (canônico) | `HJQI24C2WB` | ELGIN-ECO-INVERTER-II-24000-QF 220V |
| `HJFI12C2WD` (canônico) | `HJFI12C2IA` · `HJFI12C2WC` | ELGIN-ECO-INVERTER-III-12000-F 220V |
| `GWH12ATC` (canônico) | `GWH12AGC` | GREE-G-TOP-AUTO-12000-QF 220V |
| `GWC18ATD` (canônico) | `GWC18AGD` | GREE-G-TOP-AUTO-18000-F 220V |
| `GWH18ATD` (canônico) | `GWH18AGD` | GREE-G-TOP-AUTO-18000-QF 220V |
| `GWH30ATEXF-S6DNA1A` (canônico) | `GWH30AGE` | GREE-G-TOP-AUTO-30000-QF 220V |
| `GWC09ATB-D6DNA1A` (canônico) | `GWC09AGA` | GREE-G-TOP-AUTO-9000-F 220V |
| `S3-Q12JA31K` (canônico) | `S3-Q12JA31E` · `S4-Q12JA315` · `S4-Q12JA31C` | LG-DUAL-INVERTER-AI-VOICE-12000-F 220V |
| `S4-Q18KL31B` (canônico) | `S3-Q18KL33B` | LG-DUAL-INVERTER-AI-VOICE-18000-F 220V |
| `S3-W18KL31A` (canônico) | `S4-W18KL31A` | LG-DUAL-INVERTER-AI-VOICE-18000-QF 220V |
| `S3-Q09AA31A` (canônico) | `S3-Q09AA31F` · `S3-Q09AA33A` · `S3-Q09JA31E` · `S4-Q09AA31B` | LG-DUAL-INVERTER-AI-VOICE-9000-F 220V |
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
| `AR18DYFAAWK/AZ` (canônico) | `AR18CVFAAWK/AZ` · `AR60F18D1AWNAZ` · `F-AR60F18D1AW` | SAMSUNG-WINDFREE-AI-18000-F 220V |
| `AR24DYFABWKNAZ` (canônico) | `AR60F24D1AWN/AZ` | SAMSUNG-WINDFREE-AI-24000-F 220V |
| `F-AR24DXFAAWK` (canônico) | `AR24TSHCBWKN/AZ` | SAMSUNG-WINDFREE-AI-24000-QF 220V |
| `TAC12CSG` (canônico) | `TAC-12CGV-INV` | TCL-ELITE-GV-12000-F 220V |
| `TAC-09CSA1` (canônico) | `TAC-09CTG1` | TCL-SERIE-A1-9000-F 220V |
| `TAC-24CHTG2` (canônico) | `TAC-24CHTG1` · `TAC-24CTG1` | TCL-T-PRO-2-0-24000-QF 220V |

> CSV completo do catálogo refinado: `reports/sku_catalog_refined.csv`.
> Aplicação em produção (atualizar `produtos_catalogo.familia_linha` + coluna
> `sku_canonico`) é **gated** — revisar este relatório antes.


## Erros remanescentes (dedup voltagem-tolerante) — por que ~90% e não 98%

Cravado **6.381** · exato **5.765 (90,35%)** · família-correta **5.768 (90,4%)**.

| categoria de erro | qtde |
|---|--:|
| confusão de SUB-LINHA (linhas irmãs) | 496 |
| mesma família, SKU não colapsado (voltagem) | 3 |
| SKU-gabarito sem linha detectável | 117 |

Maiores confusões de sub-linha (o título de marketplace embaralha os termos —
não são fusão Inverter/On-Off, são linhas irmãs Dual Inverter/Eco/Airvolution):

- LG `Dual Inverter Voice` ⟷ `Compact +AI`
- Elgin `Eco Inverter II` ⟷ `III`
- Midea `AI Airvolution` ⟷ `AI Airvolution Connect`
- Midea `Xtreme Save Connect` ⟷ `…Black Edition`

### Conclusão (option 2 — dedup do catálogo)

Dedup + refino levou o **SKU-exato de 81% → 90,3%** e a cobertura de
**2.279 → 6.381** (2,8x); **48 grupos** de SKUs duplicados colapsados (69 SKUs).
**98% de SKU-exato NÃO é alcançável só por dedup**: o resíduo (496) é
ambiguidade de SUB-LINHA, em parte irredutível no título. O grão **família**
segue sendo o confiável (90,4%); cravar SKU só onde a sub-linha é inequívoca.

Subir além de ~90% exige endurecer o vocabulário de sub-linha em
normalize_product._LINE_PATTERNS — delicado, ganho marginal, risco de regressão.
Recomendo iterar com testes, em PR à parte.
