# Métricas: definição, fórmula e limite de uso

Todas são calculadas por `bestsellers/metrics.py`. Este documento explica o que
cada número significa e onde ele para de valer.

## % do top 10 (KPI principal)

Número de posições entre as 10 primeiras ocupadas por SKU do grupo Midea,
dividido pelo total de itens no escopo dentro dessas 10 posições, por
plataforma, por dia. Escopo: apenas `tipo = SPLIT_HW`.

**Serve para:** comparar a mesma plataforma ao longo do tempo, no mesmo dia da
semana.
**Não serve para:** comparar plataformas entre si (a lista da Amazon tem 30
itens, a do ML tem 20), nem virar share de mercado.

O denominador (`itens_topN`) é publicado junto do percentual de propósito: numa
lista contaminada, "50%" pode significar 1 split em 2 — e essa diferença muda
completamente a leitura.

## Ganho e perda (delta em pontos percentuais)

Diferença do KPI entre dois períodos consecutivos, por plataforma. É o número
que a rotina existe para produzir.

- **Diário:** contra a última data com o MESMO dia da semana. Quando não existe
  nenhuma, o delta sai marcado como não comparável.
- **Semanal/mensal:** média das leituras diárias do período — média, nunca
  soma: posição é ordinal.

`direcao = endpoint mudou` significa que a URL da lista mudou entre os
períodos. São populações diferentes e o delta fica vazio.

**Isto não é share de mercado.** É participação nas posições do topo da lista
de mais vendidos daquela plataforma — uma medida de prateleira que antecipa
share, e que só vira leitura de share quando confrontada com GfK/Neotrust.

## Velocidade declarada

Soma de `vendidos` onde `base_vendidos = 'mes'`. Hoje só a Shopee entrega isso.
Reportar sempre como "share dentro da lista capturada", nunca como share de
mercado. `base_vendidos = 'acumulado'` (Mercado Livre) é outra unidade.
**Nunca somar as duas.**

## Piso de preço

Menor `preco` observado por plataforma × marca × BTU. Preço praticado, nunca o
preço "de". Movimento relevante: 2% ou mais. Comparar apenas o mesmo dia da
semana.

**Armadilha registrada:** entre sábado 08/08 e segunda 10/08/2026, 30 de 36
células que se moveram subiram, mediana +8,6%. Era rollback de promoção de fim
de semana, não movimento competitivo. Britânia 12K no ML saiu de R$ 1.493 para
R$ 2.184 e continuou em #1.

## Estabilidade do ranking

% de itens que permaneceram exatamente na mesma posição entre duas coletas, e
deslocamento mediano em posições. O pareamento é pelo identificador do anúncio
(ASIN, MLB, itemid) quando existe, com fallback para o título — sellers editam
título com frequência, e parear por texto inventa "itens novos".

Acima de 35% parados sinaliza curadoria e não venda. Referência de 48h em
agosto de 2026: Amazon 19%, Mercado Livre 19%, Magalu 12%, Leroy Merlin 41%.

## Tendência

Uma leitura não forma tendência. A agregação semanal/mensal marca
`tendencia_valida = False` abaixo de 3 leituras no período — o número existe,
mas não sustenta conclusão.

## Avaliações

Contagem de reviews do anúncio rankeado.

**Achado que exige cautela:** entre os SKUs que rankeiam, a Midea tem mais
avaliações que a LG na Amazon (mediana 112 contra 23) e no Magalu (86 contra
29), e mesmo assim a LG ocupa o #1. Correlação de Spearman entre avaliações e
posição: Amazon -0,44, Magalu -0,25, Leroy -0,31. A relação existe mas é fraca,
e a amostra é condicionada ao resultado (só observamos quem rankeou). Não usar
este dado sozinho para justificar ou matar programa de reviews incentivados.

---

## O que nunca fazer

1. Somar posições de plataformas diferentes.
2. Converter ranking em share de mercado ou em unidades.
3. Comparar sábado com segunda.
4. Comparar duas datas cujo endpoint de coleta mudou.
5. Somar `vendidos` mensal com `vendidos` acumulado.
6. Apresentar o percentual do topo sem o denominador.
7. Preencher campo vazio com estimativa.
8. Tirar conclusão de tendência com menos de 3 leituras.
