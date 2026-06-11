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
