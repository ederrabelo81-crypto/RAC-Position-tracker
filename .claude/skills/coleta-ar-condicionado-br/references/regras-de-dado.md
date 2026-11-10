# Regras de dado — e o erro que deu origem a cada uma

O pior resultado de uma coleta não é o erro: é o número plausível e errado.
Todas as regras abaixo vieram de um caso assim.

## 1. Evidência antes de string

Uma coleta inteira do Mercado Livre deu zero produto em todas as keywords, com
Chrome real e IP residencial. As páginas vinham cheias de produto e eram
descartadas porque o HTML continha uma frase de login — que toda página do site
contém. Ordem certa: URL de gate → links de produto → só então texto.

## 2. Silêncio não é mercado

Se o site bloqueia às 14h e a coleta grava só o que conseguiu, o painel lê
"os concorrentes sumiram e eu ganhei a busca". Por isso toda coleta grava a
cobertura: o que foi observado e o que não foi. Regras práticas:

- compare dias só nas mesmas keywords e nos mesmos turnos;
- coleta parcial (menos de ~30% do volume normal) sai da comparação e vira
  ressalva;
- queda brusca de volume sem zerar é o sinal mais comum de sessão expirada.

## 3. Ausência de dado é dado ausente

Preencher vendedor desconhecido com o nome do marketplace fez o próprio
marketplace aparecer como segundo maior vendedor da categoria. Marcar como
"terceiro" todo card sem selo carimbou 40% da busca sem evidência. Campo não
observado fica vazio, e o `Fonte Buy Box` diz de onde veio o que foi observado.

## 4. Buy box é observação do dia

Quem vende um produto muda ao longo do dia. Guardar em cache o vendedor da
primeira leitura grava, do segundo dia em diante, um vencedor que ninguém viu.
Leia a página do produto a cada coleta; cache só dentro da mesma execução.

## 5. Só o aparelho entra em série de preço

Kit de instalação a R$ 255 já entrou na série de preço de um split de 9.000
BTUs. O classificador (`offer_kind.py`) separa os cinco primeiros tipos abaixo;
`FORA_ESCOPO` vem de outro teste (`tem_evidencia_ac`, regra 8):

| Tipo | Exemplo |
|---|---|
| `UNIDADE` | o aparelho completo |
| `ACESSORIO` | kit de instalação, suporte, controle, capa |
| `PARCIAL` | só a condensadora ou só a evaporadora |
| `MULTIPLO` | "kit 2 ar condicionado" |
| `COM_INSTALACAO` | aparelho + serviço |
| `FORA_ESCOPO` | não é ar condicionado (geladeira "Inverter" patrocinada) |

Na dúvida ele responde `UNIDADE`: só exclui com evidência. Todos os tipos
continuam na coleta, porque a posição que ocuparam é fato da página. Quem lê
filtra por `Tipo Anúncio`: preço só com `UNIDADE`; presença de marca com
qualquer tipo menos `FORA_ESCOPO`. O kit grava `Marca Monitorada` em todas as
linhas — o filtro é de quem consome.

## 6. Preço só no mesmo BTU e no mesmo formato

A mediana de preço de uma marca que mistura 9.000 e 60.000 BTUs, split e
portátil, muda sem o mercado mudar — basta o mix da busca mudar. Compare
sempre dentro de um BTU e só split hi-wall (`is_fora_hiwall` marca o resto).

## 7. Diga qual preço é

Uma oferta tem até quatro preços: à vista, no PIX, a prazo e o "de". Escolher
"o primeiro que aparecer" gravou por semanas um preço ~10% acima do que a tela
mostrava. O preço que o kit lê do card é "o primeiro que não é riscado nem
parcela" — serve para acompanhar, não para auditar. Preço a prazo nunca
substitui preço à vista ausente.

## 8. "Inverter" não prova ar condicionado

Geladeira, lavadora e micro-ondas também são "Inverter". Termo que não é
exclusivo de ar condicionado só classifica junto de "ar condicionado", "split"
ou da capacidade em BTU (`tem_evidencia_ac`). Título sem essa evidência fica
na coleta como `FORA_ESCOPO` e, pela regra 5, fora de preço e de presença de
marca.

## 9. Posição é ordinal

Posição 1 em duas buscas não é "2". Nunca some posições entre keywords ou
sites, e nunca chame presença no topo de participação de mercado. O indicador
honesto é "quantas das N primeiras posições são da marca X, nesta busca".

## 10. Tipo de busca muda a leitura

- Busca genérica ("ar condicionado 12000 btus"): é a disputa aberta.
- Busca pela própria marca: defesa.
- Busca pela marca concorrente: conquista.

Não some as três num número só.

## 11. Nome de loja não se agrupa por semelhança

O mesmo lojista aparece com grafias diferentes em cada site (apelido colado no
Mercado Livre, razão comercial na Amazon). Agrupar por string parecida transfere
buy box de uma loja para outra. Só junte duas grafias com identidade
confirmada.

## 12. Identidade da oferta

Sem id, não há série histórica. O kit grava o id do produto no marketplace e
uma `Offer Key` versionada (`v1|...`). Nunca invente um id que o site não
expõe.

## 13. Horário

A máquina pode estar em qualquer fuso. Use `now_brt()`. Turnos: Abertura (até
11h), Tarde (até 17h), Fechamento.
