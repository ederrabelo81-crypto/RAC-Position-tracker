# PriceTrack — fidelidade do preço à fonte

> **Investigação:** 02/09/2026 · **Caso de entrada:** Midea AI Ecomaster 12.000
> BTU, Magazine Luiza, coleta de 01/09/2026 — os números do painel do PriceTrack
> e os do dashboard Streamlit não fechavam.
> **Veredito:** o dashboard estava **fiel ao banco**; o **banco não estava fiel
> à API**. Três causas independentes, uma dominante.

---

## TL;DR

| # | Divergência | Efeito | Status |
|---|-------------|--------|--------|
| **A** | O import gravava `spotPrice` (à vista cheio), não o **menor entre à vista e PIX** que o painel exibe | **~10% acima do preço real** em marketplace com desconto PIX (Magazine Luiza) | ✅ corrigido |
| **A2** | Sem à vista, o fallback caía em `forward_price` (**a prazo**) | Preço parcelado entrava na mesma série de mín/média/moda do à vista | ✅ corrigido |
| **B** | Nenhum filtro de `status` — oferta **UNAVAILABLE** entrava em mín/média/moda/máx | Piso de mercado puxado por oferta que ninguém pode comprar | ✅ corrigido |
| **C** | O dashboard lia `min_price` (piso da janela) como se fosse "o preço"; o painel mostra a **última coleta** | Divergência em ~20% das listagens (as que mudam de preço no dia) | ✅ corrigido |
| **D** | ~~Turno (Abertura/Manhã/Tarde)~~ | **Não era a causa** — ver abaixo | ✅ descartado, e agora selecionável |

**O histórico de 28/07 a 01/09/2026 (1.004.567 linhas) foi gravado na base
errada** e precisa de reimport. Ele está carimbado como `price_basis =
'spot_legacy'` pela migração 006 para que ninguém o compare com dado corrigido
sem perceber.

---

## O caso, número a número

Painel do PriceTrack (01/09, Magazine Luiza, turno Tarde, À Vista + PIX +
"Menor") × `pricetrack_daily` × dashboard, para o SKU `42EZVCA12M5`:

| Seller | Painel | `min_price` no banco | `max_price` | Painel ÷ banco |
|--------|-------:|---------------------:|------------:|---------------:|
| DUFRIO | 2.229 | 2.287,78 | 2.476,67 | **2.476,67 × 0,90 = 2.229,00** |
| WEB CONTINENTAL | 2.599 | 2.443,33 | 2.887,78 | **2.887,78 × 0,90 = 2.599,00** |
| FRIOPEÇAS | 2.260 | 2.512,00 | 3.256,00 | **2.512,00 × 0,90 = 2.260,80** |
| LEVEROS | 2.249 | 2.499,00 | 2.659,00 | **2.499,00 × 0,90 = 2.249,10** |
| GO COMPRAS | 2.384 | 2.649,00 | 2.649,00 | **2.649,00 × 0,90 = 2.384,10** |
| FRIGELAR | 2.307 | 2.430,18 | 2.983,32 | 2.563,33 × 0,90 (coleta intermediária) |
| AR CERTO | 2.879 | 2.449,00 | 3.031,00 | dentro da faixa, sem desconto PIX |

O fator **0,90 é o desconto PIX da Magazine Luiza**. O painel mostra o preço
com PIX; o banco guardava o preço sem PIX. Não é arredondamento nem defasagem
de coleta — é **base de preço diferente**.

**Confirmação estatística** (01/09, turno Diário, todas as marcas): a fração de
preços cujo valor × 0,90 dá um número redondo é de **22,3% na Magazine Luiza**
contra **2,3–8,4% em todos os outros marketplaces**. Só na Magalu a base
gravada é sistematicamente o valor "antes do PIX".

### O dashboard estava certo

Todos os números da tela saem exatamente de `min_price`:

| Tela | Valor | De onde vem |
|------|------:|-------------|
| Midea piso 12K High | 2.287,78 | `min(min_price)` dos 7 sellers ✓ |
| Média | 2.467,18 | média dos 7 `min_price` ✓ |
| Mediana | 2.449,00 | mediana dos 7 ✓ |
| Máximo | 2.649,00 | **máximo dos mínimos** — não é o máximo observado (3.256,00) |
| Ofertas | 7 | 7 **linhas** de seller, não 7 coletas |

As duas últimas linhas mostram o problema **C**: uma linha de
`pricetrack_daily` já é um agregado (min/média/moda/máx de N coletas do dia), e
o dashboard tratava cada linha como se fosse uma observação. "Máximo" virou
"máximo dos mínimos" e "modal" virou "moda dos pisos".

### Por que não era o turno

A hipótese natural (Último do dia / Madrugada / Manhã / Tarde / Noite) foi
testada contra o banco e **descartada**:

- Diário, Manhã e Tarde de 01/09 têm **exatamente os mesmos 9.856 grupos**;
- `min_price` difere entre Diário e Tarde em **6 linhas de 9.856** (0,06%);
- 7.903 das 9.856 listagens (80%) tiveram **preço constante o dia inteiro**;
  nas 1.953 que variaram, o spread médio foi de 2,6%.

O PriceTrack recolhe cada listagem em várias horas do dia, e o preço quase não
se move entre manhã e noite. O turno não explicava a diferença de ~10%.

Ainda assim a página **não tinha seletor de turno** e lia `"Diário"` fixo — não
havia como reproduzir a tela do painel. Agora tem.

---

## O que a API entrega (pergunta 1)

A API do PriceTrack **não devolve "um preço"**. Cada observação (uma listagem,
numa hora de coleta) traz:

| Campo | Significado |
|-------|-------------|
| `spotPrice` | à vista no cartão / preço cheio |
| `pixPrice` | à vista no PIX (nullable) |
| `forwardPrice` | a prazo (total parcelado) |
| `priceFrom` | preço "de" / RRP |
| `status` | `AVAILABLE` \| `UNAVAILABLE` |
| `collectionHour` | hora BRT do crawl — há **várias por dia, por listagem** |

O preço competitivo — o que o comprador paga e o que o painel exibe com
**À Vista + PIX + "Menor"** — é `min(spotPrice, pixPrice)`, **só se
`AVAILABLE`**. Isso já estava escrito e testado em
`pricetrack_api/normalize.py` (`NormalizedPrices.best_cash` /
`effective_price`) — o importador é que não usava.

**Confira você mesmo**, no PC coletor (onde vivem a key e os NDJSON):

```bash
# o dia inteiro: quanto o PIX barateia, por marketplace
python scripts/pricetrack_price_audit.py --data 2026-09-01

# o caso deste documento, oferta a oferta, e confrontado com o banco
python scripts/pricetrack_price_audit.py --data 2026-09-01 \
    --sku 42EZVCA12M5 --marketplace "MAGAZINE LUIZA" --comparar
```

O script não escreve nada: lê o export bruto e imprime hora, status e os quatro
preços de cada coleta.

---

## O que mudou

### Ingestão — `scripts/pricetrack_api_import.py`

- Preço = `min(spotPrice, pixPrice)`, saneados (≤ 0 vira ausente).
  **`forwardPrice` nunca vira preço** — quem só tem preço a prazo é rejeitado
  com o motivo `FORWARD_PRICE_ONLY` no `rejection_log`.
- **`status` filtra**: só observação `AVAILABLE` entra em preço. Grupo 100%
  indisponível **permanece** na tabela com preços `NULL` e
  `unavailable_count > 0` — a listagem existiu (share of shelf), só não
  competiu por preço.
- Colunas novas: `price_basis`, `last_price`, `last_hour`, `spot_min_price`,
  `pix_min_price`, `obs_count`, `unavailable_count`.
- `_mode` de grupo vazio devolve `NULL`, não `0.0` (moda R$ 0,00 seria lida
  como preço real por todo consumidor a jusante).
- O log de cada dia diz em quantas ofertas o PIX venceu — a medida direta do
  erro que a base antiga cometia.

### Banco — `migrations/006_pricetrack_price_basis.sql`

Aditiva. Carimba todo o histórico como `price_basis = 'spot_legacy'`; o
importador corrigido grava `'best_cash'`. Sem esse carimbo não há como separar
linha certa de linha errada, e a série de evolução emendaria as duas — o degrau
da virada pareceria movimento de mercado.

```bash
psql "$SUPABASE_DSN" -f migrations/006_pricetrack_price_basis.sql
```

> O importador **não quebra** se a migração ainda não rodou: ele detecta as
> colunas ausentes, grava só as legadas (o preço já sai corrigido) e loga o
> comando da migração. Mas sem `price_basis` a linha corrigida fica
> indistinguível do histórico errado — aplique antes do reimport.

### Leitura — `pricetrack_dashboard/`

- O preço da oferta sintética passa a ser `last_price` (**última coleta da
  janela** — o que o painel exibe), caindo para `min_price` só em linha legada.
- **Seletor de turno** (Diário / Manhã / Tarde) — para comparar número com
  número contra o painel.
- Aviso na tela quando a janela lida traz linhas `spot_legacy`, e **erro** (não
  aviso) quando ela mistura as duas bases.
- Legenda explicando que uma linha de `pricetrack_daily` é uma listagem-dia
  agregada, não uma oferta.

---

## Backfill do histórico

### Migração 006 — ✅ aplicada em 02/09/2026

Rodou no projeto `ailbsczkrympslpjwwko` (RAC). As 7 colunas entraram e o
histórico inteiro foi carimbado:

| `price_basis` | linhas | dias | período |
|---------------|-------:|-----:|---------|
| `spot_legacy` | 1.004.567 | 36 | 28/07 → 01/09/2026 |
| `best_cash` | 0 | — | — |

`price_basis` entrou com `DEFAULT 'spot_legacy'` — em Postgres 11+ isso é
operação de metadados, então 1M de linhas ficaram carimbadas sem reescrita. O
default também protege escritas futuras que não se declarem: **base
desconhecida se lê como base antiga**, nunca como "provavelmente está certa".

### Reimport — pendente, roda no PC coletor

O reimport **não pode rodar deste ambiente nem por SQL**: o PIX simplesmente
**não existe em `pricetrack_daily`** — a tabela guarda só o agregado do preço
que o import escolheu. Não há como derivar `min(spot, pix)` de `spot`. A única
fonte é o NDJSON bruto, que vive junto da `PRICETRACK_API_KEY`, no PC coletor.

```bash
# 1. quanto ainda está errado, e o que vai ser re-baixado
python scripts/pricetrack_price_audit.py --status-backfill

# 2. reimportar os 36 dias (uma passada só)
python scripts/pricetrack_api_import.py --force --start 2026-07-28 --end 2026-09-01

# 3. conferir — a meta é spot_legacy zerado
python scripts/pricetrack_price_audit.py --status-backfill
```

**`--force` não re-baixa o que já está em cache.** Ele força o
*reprocessamento* da data; o download continua obedecendo `_should_redownload`,
que só rebaixa hoje e ontem (dias antigos são imutáveis no PriceTrack). Ou
seja: se os `imports/pricetrack/api/raw/offers-*.ndjson.gz` dos 36 dias ainda
estão no disco, o backfill é uma **re-agregação local** — rápida, sem tocar na
API. `--status-backfill` diz, dia a dia, quais têm cache e quais serão
re-baixados (esses são lentos: no máximo 3 exports em voo).

O reimport é idempotente (upsert por
`collection_date,turno,brand,sku,marketplace,seller`) e pode ser fatiado por
intervalo — dá para começar por 01/09 e conferir contra o painel antes de
soltar o resto.

⚠️ **Enquanto o reimport não roda, todo número de preço derivado do PriceTrack
está ~10% alto onde há desconto PIX** — e isso inclui o dashboard principal
(`app.py`: Buy Box, moda, mediana, análise de violação de MAP), não só a página
9K/12K. Um dia reimportado passa a valer imediatamente; a mistura fica visível
por `price_basis`, e o dashboard 9K/12K passa a **dar erro na tela** quando a
janela pega as duas bases.

---

## Regras duras que saíram daqui

1. **Base de preço nunca é implícita.** Toda linha de preço carimba de qual
   base veio. Ausência de carimbo se lê como "base antiga", nunca como
   "provavelmente está certo".
2. **Preço a prazo não preenche buraco de preço à vista.** São bases
   diferentes; misturá-las num `min`/`avg`/`mode` produz um número que não
   existe em vitrine nenhuma. Sem à vista, a linha é rejeitada — com motivo.
3. **Indisponível não compete.** `UNAVAILABLE` fica fora de piso e média, mas a
   linha sobrevive com preço `NULL` — apagar a linha perderia o share of shelf.
4. **Uma linha de `pricetrack_daily` não é uma oferta.** É N coletas do dia
   colapsadas. Quem trata a linha como observação produz "moda dos pisos"
   achando que produziu "moda do mercado" — e o rótulo na tela mente.
5. **O que o painel mostra é a última coleta.** Piso da janela é outra
   pergunta, legítima, mas com outro nome.
