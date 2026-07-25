# Diagnóstico de Coleta — Buy Box / Sellers (Jun/2026)

> Investigação dos campos de buy box/seller (`buy_box_seller`, `tipo_seller`,
> `qtd_sellers`, `reputacao_seller`, `patrocinado`) por plataforma, motivada
> pela revisão geral de Jun/2026. Baseado em consultas diretas à tabela
> `coletas` do Supabase (janelas de 7 e 21 dias até 2026-06-01).

## Método

Foram cruzadas três visões: (1) preenchimento por coluna/plataforma em 21 dias,
(2) tendência diária de `buy_box_seller` por plataforma em 10 dias, e
(3) valores distintos de `tipo_seller`. Onde uma visão agregada divergiu, a
visão diária/explícita prevaleceu (mais granular e reproduzível).

## Resumo por plataforma

| Plataforma | Buy box | Tipo Seller (1P/3P) | Qtd Sellers | Status da coleta | Causa raiz |
|---|---|---|---|---|---|
| Mercado Livre | ✅ 100% (desde ~30/05) | ⚠️ só "3P" | ❌ não coletado | Ativa | Rollout OK; **"Loja Oficial" nunca dispara** |
| Amazon | ✅ 100% (desde ~30/05) | ✅ "1P" | 🟡 ~3% | Ativa | OK; `qtd_sellers` parcial ("X ofertas") |
| Leroy Merlin | ✅ 100% (desde ~31/05) | ✅ 1P+3P | ❌ não coletado | Ativa | **OK — sem bug** |
| Magalu | ❌ 0% | ❌ | ❌ | **Quebrada** | Bloqueio Akamai (volume 1.046 → 1) |
| Casas Bahia | ❌ 0% | ❌ | ❌ | **Parada** (desde 26/05) | Bloqueio (IP datacenter) |
| Shopee | ❌ 0% | ❌ | ❌ | **Parada** (desde 27/05) | Sessão expirada **+** `shop_name` não extraído |
| Google Shopping | 🟡 ~20% | ❌ (n/a) | 🟡 ~1% | Ativa | Comparador — sem seller único |

`reputacao_seller`: **100% vazio em todas as plataformas, todos os dias.**

## Detalhe e causa raiz

### ✅ ML, Amazon, Leroy — funcionando (rollout recente)
A coleta de buy box é um rollout do fim de Maio/2026. Antes de ~29/05 todas as
plataformas tinham `buy_box_seller` nulo; de 30/05 em diante ML/Amazon/Leroy
atingem ~100% de preenchimento. O código de extração está correto e o dado
está fresco e confiável nessas três — é onde as novas análises de buy box do
dashboard têm valor imediato.

**Lacuna menor (ML):** `scrapers/mercado_livre.py` define
`tipo_seller = "Loja Oficial" if is_official else "3P"`, mas o banco só contém
`"3P"` para ML (4.646 linhas, zero "Loja Oficial"). A detecção de loja oficial
(`is_official`) não está disparando — o seletor/flag de "Loja oficial" na SERP
do ML provavelmente mudou. Impacto: perde-se a distinção 1P/oficial no ML.

### ❌ Magalu — bloqueio Akamai (não é bug de código)
`scrapers/magalu.py:1361` faz `seller = self._extract_seller(prod) or "Magalu"`
(nunca nulo) e passa `buy_box_seller=seller`. O código está correto. O problema
é volume: a coleta despencou de **1.046 registros (27/05) para 1 (01/06)** — os
~19k registros históricos sem buy box são de builds anteriores ao rollout. Sinal
clássico de bloqueio Akamai do alvo Magalu. **Ação = operacional** (proxy
residencial BR / re-tunar warm-up / modo CDP), não edição de extração.

### ❌ Casas Bahia — bloqueio (não é bug de código)
`scrapers/casas_bahia.py:467-537` extrai o vencedor da buy box corretamente do
array `sellers[]` (`sellerDefault`), incluindo `qtd_sellers` e `tipo_seller`.
A coleta simplesmente **parou em 26/05** — consistente com bloqueio por IP de
datacenter descrito no `CLAUDE.md`. **Ação = operacional** (warm-up Akamai /
proxy BR).

### ❌ Shopee — dois problemas
1. **Sessão expirada / bloqueio:** sem coletas desde 27/05. Cookies `SPC_*`
   expiram em horas; precisa re-capturar com `session_grabber.py --site shopee`.
2. **Lacuna de código:** mesmo nas 2.842 linhas que entraram (com preço), o
   campo `seller` veio **vazio em 100%**. `scrapers/shopee.py:277` lê
   `item.get("shop_name")`, mas o endpoint `search_items` da API v4 aparentemente
   **não retorna `shop_name`** no item — ele costuma vir em outro campo
   (`shop_location`) ou exigir uma chamada de detalhe da loja. Sem isso,
   `buy_box_seller`/`tipo_seller` ficam nulos mesmo quando a coleta funciona.

### ❌ reputacao_seller — coluna morta
Só é populada por `scrapers/mercado_livre_api.py` (API OAuth do ML), que **não
roda em produção** (não é importado em `main.py` — confirmado na revisão). O
scraper de ML ativo é o de browser (`mercado_livre.py`), que não extrai
reputação. Resultado: a coluna é 100% nula.

## Lacunas de código (independentes de bloqueio)

| # | Item | Arquivo | Observação |
|---|---|---|---|
| D1 | `shop_name`/seller não extraído | `scrapers/shopee.py:277` | Validar campo correto na resposta da API v4 (precisa testar contra a API real) |
| D2 | "Loja Oficial" do ML não dispara | `scrapers/mercado_livre.py:349` | `is_official` sempre falso — revisar seletor da flag "Loja oficial" |
| D3 | `qtd_sellers` ausente em ML/Leroy/Magalu | respectivos scrapers | Hoje só Amazon/Google; exigiria extrair nº de ofertas por listagem |
| D4 | `reputacao_seller` morta | `mercado_livre_api.py` (não usado) | Decidir: wirear a API oficial OU remover a promessa da coluna |

> **Por que não corrigi D1/D2 agora:** ambas dependem de inspecionar a resposta
> real da plataforma (API Shopee / DOM do ML), o que exige rodar o scraper com
> browser/sessão — indisponível neste ambiente. Aplicar mudanças "às cegas" na
> extração seria entregar código não verificado. Recomendo corrigir num ambiente
> com coleta ativa, validando com `--no-headless` / dump da resposta.

## Recomendações priorizadas

1. **Operacional (maior ganho):** proxy residencial/móvel BR para destravar
   Magalu, Casas Bahia e Shopee — é a causa raiz comum dos três.
2. **Shopee (D1):** dumpar 1 resposta de `search_items` e mapear o campo real do
   nome da loja; ajustar `scrapers/shopee.py`.
3. **ML (D2):** revisar o seletor de "Loja oficial" para recuperar o split
   1P/oficial vs 3P.
4. **`reputacao_seller` (D4):** ativar `MLAPIScraper` (requer `ML_APP_ID`/
   `ML_APP_SECRET`) ou parar de expor a coluna no dashboard/CI.
5. **Monitoramento:** acompanhar a nova página **🩺 Data Health** para flagrar
   regressões (coleta parada / buy box 0%) antes de virarem dias de buraco.

---

## Adendo (11/06/2026) — avaliação/patrocinado do ML: 0% desde sempre

Investigação complementar (consulta mensal ao Supabase) para as páginas novas
⭐ Reputação & Avaliações e 📣 SOV Patrocinado, que não exibiam Mercado Livre:

| Mês | Registros ML | `avaliacao` | `qtd_avaliacoes` | `patrocinado` | `tag` |
|-----|-------------:|:-----------:|:----------------:|:-------------:|:-----:|
| Mar/2026 | 1.033 | 0% | 0% | 0% | 14,6% |
| Abr/2026 | 10.869 | 0% | 0% | 0% | 16,2% |
| Mai/2026 | 76.908 | 0% | 0% | 0% | 1,5% |
| Jun/2026 | 13.266 | 0% | 0% | 0% | 0,0% |

**Causa raiz:** os seletores Poly do `scrapers/mercado_livre.py` para reviews
(`.poly-component__reviews-rating`/`-count`) **nunca existiram no DOM real** —
os nomes corretos do sistema Poly são `.poly-reviews__rating`/`__total`. A
detecção de patrocinado dependia de rótulo textual que o card atual não expõe
como nó de texto, e `tag` degradou junto com o rollout do Poly.

**Fix (Jun/2026):** extração multi-camada em `scrapers/mercado_livre.py` —
seletores Poly corretos + fallback via texto acessível ("Avaliação 4,8 de 5"),
patrocinado em 5 camadas (incl. âncora de ad-tracking `click1.mercadolivre`/
`mclics`), Loja Oficial via texto/selo (fecha o D2) e tags por texto conhecido.
Testes em `tests/test_ml_parse.py`; validação viva com
`python scripts/diagnose_ml.py` (taxa de acerto por campo/seletor).

**D4 (reputacao_seller):** `MLAPIScraper` agora é invocável como coleta
complementar: `python main.py --platforms ml_api` (fora do `all`; requer
`ML_APP_ID`/`ML_APP_SECRET` + `scripts/ml_oauth_setup.py`).

*Gerado na revisão geral de Jun/2026. Dados: Supabase `coletas` até 2026-06-01;
adendo com dados até 2026-06-11.*

---

## Adendo (14/07/2026) — Shopee: "0 produtos" com API respondendo (fecha D1)

Sintoma: coleta local (Chrome real logado, `RAC_LOCAL_CHROME=1`) logava
`Pág 1/1: 0 produtos (browser local)` para todas as keywords — **sem** os
sinais de bloqueio (`nenhuma resposta de search_items capturada`) nem de busca
vazia (`Sem mais resultados`).

**Causa raiz:** a API v4 estava respondendo com itens, mas `_parse_items` só
reconhecia o invólucro `item_basic`/`item`. A Shopee trocou o **wrapper de cada
item** do `search_items` (versões novas entregam os campos do produto direto no
wrapper — formato "flat" — ou sob `item_data`), então `wrapper.get("item_basic")`
caía em `{}` e **todos os itens eram descartados em silêncio**. É a materialização
do D1 (o "campo do item mudou" que não dava para corrigir às cegas em Jun).

**Fix (`scrapers/shopee.py`):**
1. `_extract_item_payload` — reconhece `item_basic`/`item`/`item_data`/`basic`/
   `data`, invólucro aninhado e o formato flat (wrapper com `itemid`/`item_id`).
2. Fallbacks de nome de campo: `itemid`↔`item_id`, `shopid`↔`shop_id`,
   `name`↔`title`, `shop_name`↔`shopName`↔`shop_data.*` (recupera o `seller`).
3. `_normalize_price` — trata a escala ×100000 e o valor já em reais.
4. **Dump de diagnóstico** (`logs/shopee_debug_<kw>_pN.json` + chaves reais do
   1º item) quando itens vêm mas nada parseia — futura troca de estrutura vira
   erro diagnosticável, não coleta vazia silenciosa.
5. `_search_via_browser` passa a escolher a resposta que **parseia mais**
   registros (não cegamente a 1ª), evitando que uma chamada de ads/prefetch
   mascare os resultados reais.

Cobertura: `tests/test_shopee_parse.py` (extrator de payload, normalização de
preço e parsing dos formatos antigo/novo/misto).

---

## Adendo (25/07/2026) — ML: coleta "funcionando" com metade dos campos ocos

Gatilho: CSV `rac_monitoramento_20260725_1115.csv` (600 registros de ML, 10
keywords). A coleta **não estava bloqueada** — 600/600 linhas com título, preço,
URL e posição. O que degradou foi a **extração de campos** e a **paginação**.

### O que o CSV e o Supabase mostraram

| Campo | Preenchimento | Veredito |
|---|---|---|
| `avaliacao` / `qtd_avaliacoes` | **0 de 35.445** registros de ML | quebrado desde sempre |
| `qtd_sellers` / `reputacao_seller` | 0% | nunca coletados pelo scraper de browser |
| `fulfillment` | ~1% | selo FULL não detectado |
| `tipo_seller = "Loja Oficial"` | **86%** | falso positivo em massa |
| `buy_box_seller = "Mercado Livre"` | 13,5% | valor inventado |
| `posicao_organica` | duplicada entre páginas | corrompe share/SOV |

O fix de Jun/2026 acertou `patrocinado` (0% → ~26%) e `tag` (0% → ~25%), mas
`avaliacao` continuou zerada: os nomes `.poly-reviews__rating`/`__total` também
não existem no DOM real. E a detecção de Loja Oficial passou de **0% para 86%** —
trocou um extremo errado por outro.

### Causas raiz e correções (`scrapers/mercado_livre.py`)

1. **Avaliação zerada** — as duas camadas anteriores dependem de nome de classe
   do Poly, que já mudou duas vezes. Nova camada **estrutural**: o widget de
   reviews renderiza dois nós de texto vizinhos, `4.8` e `(1.234)`; casamos o nó
   inteiro (`^[0-5][.,]\d$` + `^\(\d…\)$`), formato que nada mais no card produz
   (preço tem 3 decimais, parcela tem "x", título é longo). Imune à próxima
   renomeação de classe.

2. **"Loja Oficial" em 86%** — `item.find(string=/loja oficial/)` varria o card
   inteiro, marcando 3P evidentes ("KARZEN ELETRO", "REFRIGERAÇÃO MOTA") e até
   cards sem seller. Agora só conta sinal **explícito e escopado ao bloco do
   seller**: label legado, nome já rotulado, atributo acessível, href de vitrine
   (`/loja/<slug>`) ou cockade **dentro** do bloco do seller. A camada que
   disparou é registrada e logada por run.

3. **Fulfillment em ~1%** — o selo FULL é ícone, não texto. Passa a checar
   `[class*="fulfillment"]` e `aria-label`/`title`/`alt`. O fallback textual
   agora casa o nó **inteiro**: o `\bfull\b` solto marcaria "Ar Condicionado
   **Full** DC Inverter" como fulfillment.

4. **Paginação com passo fixo** — `_ITEMS_PER_PAGE = 48` ficou obsoleto: a SERP
   passou a servir ~60 cards, então a página 2 (`_Desde_49`) **recoletava os
   itens 49..60** da página 1. O offset agora vem de `_SerpCursor.items_seen`
   (contagem real de cards), e a constante foi removida para não voltar a
   mentir.

5. **Posição orgânica reiniciando por página** — os contadores viviam dentro de
   `_parse_results`, então a página 2 recomeçava em "Orgânica 1". Confirmado no
   banco (16/07, `ar condicionado inverter mais econômico`: duas linhas em cada
   posição de 2 a 32). Contadores passaram para o cursor da keyword.

6. **Seller inventado** — sem `.poly-component__seller` o código caía para
   `"Mercado Livre"`, o que fez o próprio ML virar o 2º maior "vencedor de buy
   box" da categoria. Agora fica nulo.

7. **URL de patrocinado** — vinha como link de tracking `click1…/mclics`, que
   expira e não casa com o catálogo. O `item_id` embutido é convertido em
   permalink canônico (`produto.mercadolivre.com.br/MLB-…`).

8. **Cobertura instrumentada** — cada keyword loga `title=…/n price=…/n
   rating=…/n …`; campo crítico zerado vira WARNING e grava
   `logs/ml_card_sample.html`. Foi a ausência disso que deixou `avaliacao`
   passar meses em 0% sem ninguém notar.

### Limite desta correção

Os itens 4–7 são deduzidos de dados observados (CSV + Supabase) e estão
cobertos por teste. Os itens 1–3 mexem em **como** se lê o card: a lógica está
testada, mas **não foi validada contra o DOM vivo** — o ambiente de
desenvolvimento não alcança `lista.mercadolivre.com.br` (403 em IP de
datacenter). A validação é a própria próxima coleta: o log de cobertura dirá se
`rating`/`oficial`/`fulfillment` saíram do zero, e o `ml_card_sample.html`
entrega o DOM real caso ainda não saiam.

### Ainda em aberto

- `qtd_sellers` e `reputacao_seller` **não existem na SERP** do ML — só no PDP
  (inviável: ~60 itens × 31 keywords × 2 turnos) ou na API oficial. Continua
  valendo o D4: `python main.py --platforms ml_api` com `ML_APP_ID`/
  `ML_APP_SECRET`.
- **Pipeline parado**: nenhuma plataforma grava no Supabase desde **16/07/2026**
  (última inserção 16/07 21:18 UTC). É operacional — agendador/host —, não
  extração.

*Adendo gerado em 25/07/2026.*
