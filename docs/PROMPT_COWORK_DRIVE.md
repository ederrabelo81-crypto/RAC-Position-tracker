# Prompt do Claude Cowork — ler a coleta do Google Drive no lugar do Supabase

> **Para quê:** enquanto o Supabase estiver restrito por cota (HTTP 402) ou fora
> do ar, o Cowork continua produzindo as mesmas seções de coleta — cenário
> digital, patrocinados, profundidade de posição, vitrine própria, buy box —
> lendo o histórico frio em Parquet no Drive.
>
> **Critérios:** os mesmos de `pipeline_dados.md` da skill
> `midea-rac-weekly-report`. Este documento **traduz** cada filtro do PostgREST
> para o equivalente em pandas. Nenhum recorte novo, nenhum recorte a menos.
>
> **Arquitetura de origem:** [`HISTORICO_DRIVE.md`](HISTORICO_DRIVE.md).

---

## Como usar

1. Abra o Cowork com o conector do **Google Drive** ativo (conta
   `ederrabelo81@gmail.com`, dona da pasta).
2. Cole o bloco inteiro da seção seguinte como primeira mensagem.
3. Emende o pedido do dia depois do prompt, por exemplo:
   *"Fecha a W31 com a coleta de 25/07"* ou *"Só a tabela de presença por marca
   do dia 25/07"*.

O prompt é auto-contido: ele não depende de o Cowork ter o repositório em mãos.

---

## O prompt (copiar daqui até o fim do bloco)

````markdown
Você vai gerar as seções de coleta do RAC Position Tracker (Midea Carrier
Brasil) lendo os dados do **Google Drive**, não do Supabase. O banco está fora
do ar / restrito por cota. Os critérios analíticos são exatamente os mesmos que
você aplicaria no Supabase — muda só a origem do dado.

## 1. Onde estão os dados

Conector Google Drive, pasta `RAC Position Tracker - Historico/coletas/`
(a raiz tem id `1KX1Cto9huc3SGF972peUOEwqebp9EsCE`; `coletas` tem id
`1XCxLYOLBzF61mIhBcgdLxZmUVvw8id92`).

Cada arquivo é uma partição imutável em Parquet:

```
data=YYYY-MM-DD__run-<run_id>.parquet
```

- Um dia pode ter **várias partições** (uma por execução da coleta: manhã,
  noite, reprocessos).
- Ignore a pasta `_setup_check/` — é marcador de saúde da credencial.
- Não existe pasta `pricetrack/`. Ver a seção 6.

## 2. Como carregar

Baixe as partições do dia alvo e leia com pandas (`pyarrow` instalado). Não
tente ler o arquivo pelo texto: é binário.

```python
import pandas as pd

COLS = [  # nunca carregue as colunas de screenshot: são base64 pesadas
    "data", "turno", "horario", "plataforma", "tipo", "keyword", "categoria",
    "marca", "produto", "produto_normalizado", "posicao_organica",
    "posicao_patrocinada", "posicao_geral", "patrocinado", "buy_box_seller",
    "qtd_sellers", "tipo_seller", "reputacao_seller", "preco", "seller",
    "fulfillment", "avaliacao", "qtd_avaliacoes", "tag", "url_produto", "run_id",
]

def carregar(paths):
    frames = [pd.read_parquet(p) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    return df[[c for c in COLS if c in df.columns]]
```

Tipos: `data` é **string ISO** (`"2026-07-25"`), `patrocinado` e `fulfillment`
são booleanos anuláveis, `preco`/`avaliacao` são float, as posições e
`qtd_sellers`/`qtd_avaliacoes` são inteiros anuláveis. Trate `<NA>`/`None`
explicitamente — a mediana e a média precisam de `.dropna()`.

## 3. Quais partições entram (equivale ao `WHERE data = 'YYYY-MM-DD'`)

1. **Um único dia por relatório.** Use o último dia da semana que tenha
   partição. Se o dia pedido não existir no Drive, diga qual é o dia mais
   recente disponível e pergunte — não escorregue para outro dia em silêncio.
2. **Todas as partições daquele dia entram**, exatamente como o Supabase
   devolvia os dois turnos numa consulta por data. Se precisar de um turno só,
   filtre `turno` e declare isso no rodapé.
3. **Precedência da migração:** se existir uma partição do dia cujo `run_id`
   começa com `tier` (veio do Supabase já normalizado), use **só ela** e ignore
   as demais daquele dia. Somar as duas origens duplicaria o dia.
4. Registre no cabeçalho da seção a data da coleta e quantas partições/linhas
   foram lidas ("coleta do dia 25/07, 2 partições, 5.651 linhas").

## 4. Recortes — idênticos aos do Supabase

Estas são as mesmas regras do pipeline do banco, escritas em pandas. Não
invente variação.

**Presença de prateleira (seção "Cenário digital")**
- `categoria in ("Capacidade BTU", "Genérica", "Capacidade + Tipo")`
- `plataforma in ("Amazon", "Mercado Livre", "Leroy Merlin")`
- Presença da marca = cards da marca ÷ total de cards do recorte no dia × 100.
  Denominador inclui os cards de marca `Desconhecida`; a tabela lista as marcas
  identificadas e a linha Midea vai destacada, ordenada por presença.

**Cards patrocinados**
- Só faz sentido em `plataforma in ("Amazon", "Mercado Livre")` — nas outras a
  flag não é confiável e não deve ser reportada.
- Contagem = linhas com `patrocinado == True` por marca, nas buscas por
  capacidade.

**Profundidade de posição (canais abertos)**
- Buscas abertas por capacidade, canais Amazon, Mercado Livre e Leroy Merlin.
- Coluna de posição: **`posicao_geral`** (é a que o dashboard usa). Reporte
  **posição média** (`.mean()` sobre não nulos) e **% top 3**
  (`posicao_geral <= 3` ÷ linhas com posição não nula).

**Vitrine própria (site do parceiro)**
- `categoria == "Dealers"`. Posição média e % top 3 por parceiro.
- Parceiro com amostra pequena no dia fica fora da tabela e é citado no rodapé,
  com o n.

**Contaminação de BTU cruzado**
- Isolar 9K/12K exige **exclusão explícita** das outras capacidades no texto do
  produto. Filtrar por marca ou por keyword não basta.
```python
INCLUI_9K  = r"9\.?000\s*BTU"
EXCLUI_9K  = r"(7\.?000|12\.?000|18\.?000|22\.?000|24\.?000|30\.?000|36\.?000)\s*BTU"
alvo = df[df.produto.str.contains(INCLUI_9K, case=False, na=False)
          & ~df.produto.str.contains(EXCLUI_9K, case=False, na=False)]
```

**Buy box e sellers**
- `buy_box_seller` é consistentemente **nulo no Amazon** — ausência ali é
  limitação de coleta, não sinal de mercado. No Mercado Livre vem populado.
- `qtd_sellers`, `tipo_seller` (1P/3P) e `reputacao_seller` seguem a mesma
  leitura de sempre.

## 5. A diferença que você PRECISA declarar: de-para

As partições gravadas pela coleta **não têm** as colunas `estado_match`,
`familia_resolvida`, `sku_resolvido` e `voltagem_resolvida` — elas são
preenchidas pela automação Admin depois, dentro do Supabase.

- Se a coluna `estado_match` **não existir** no DataFrame: **não filtre por
  ela**. Essas linhas estão *não classificadas*, não rejeitadas. O filtro
  padrão do Supabase (`estado_match = 'MAPEADO'`) esconderia o dia inteiro.
- O filtro de produto de ar-condicionado já foi aplicado na gravação (mesma
  regra do upload ao banco), então a base não tem lixo de outras categorias.
- **Rodapé obrigatório** nas tabelas geradas assim: *"base: coleta do Drive sem
  de-para aplicado; denominador é o total de cards do dia, não só os mapeados"*.
  Sem isso, alguém compara com uma semana cujo denominador era só `MAPEADO` e
  lê como movimento de mercado o que é troca de base.
- Se a coluna **existir** (partição vinda da migração `tier`), aplique
  `estado_match == "MAPEADO"` normalmente, como no Supabase.

## 6. Preço 9K/12K (modal e piso)

A tabela `pricetrack_daily`, de onde saíam modal e piso da "coleta cheia",
**não está no Drive** — o histórico frio só tem `coletas`. Duas saídas, nessa
ordem de preferência:

1. **Marcar pendente:** *"Preço 9K/12K: base pricetrack indisponível nesta
   semana, a confirmar."* É o caminho padrão.
2. Se o pedido for explícito por um número, calcular a partir de
   `coletas.preco` (campo secundário da coleta), com **rodapé declarando a troca
   de base** e a proibição de comparar com o modal/piso das semanas anteriores:
   metodologias diferentes. Modal = valor mais frequente do dia
   (`.mode()`); piso = `.min()` com peças avulsas filtradas.

Nunca misture as duas bases na mesma série semana-a-semana.

## 7. Regra de ouro (anti-fabricação)

- Nunca inventar presença, contagem de patrocinado, posição, modal ou piso. O
  número vem da partição lida ou a seção sai marcada como pendente.
- Magalu e Casas Bahia aparecem de forma intermitente: lacuna é lacuna de
  monitoramento, **não** sinal de mercado. Diga isso quando ocorrer.
- Contagens diárias oscilam. Caveat fixo no rodapé das tabelas de coleta:
  *"leia direção, não casa decimal"*.
- Não misturar a lente card-visível do Streamlit com a coleta cheia.
- Formato numérico BR: milhar com ponto (`5.157`), decimal com vírgula
  (`23,7%`), moeda `R$ 1.952`. Sem travessão (em-dash) no texto.

## 8. O que entregar

Diga, em uma linha no começo: dia da coleta, nº de partições, nº de linhas e
que a origem foi o **Drive** (não o Supabase). Depois as tabelas/prosa pedidas,
com os rodapés das seções 5 e 7 quando se aplicarem. Se o pedido for o
relatório semanal completo, mantenha a estrutura, o tom e a ordem de seções de
sempre; estas regras substituem apenas a origem dos dados da Fonte B.
````

---

## Anexo — equivalências Supabase → Drive

| Regra no Supabase | Equivalente no Drive |
|---|---|
| `WHERE data = 'YYYY-MM-DD'` | carregar todas as partições `data=YYYY-MM-DD__run-*.parquet` |
| Nunca fazer range de datas (timeout) | range é barato no Parquet, mas a **lente do relatório segue sendo de dia único** por comparabilidade |
| Excluir colunas de screenshot do SELECT | não incluir `screenshot_busca` / `screenshot_produto` na projeção |
| `estado_match = 'MAPEADO'` | coluna ausente nas partições de coleta ⇒ **não filtrar** + rodapé de base |
| `patrocinado` só em Amazon/ML | idem, `df.plataforma.isin(["Amazon", "Mercado Livre"])` |
| `categoria IN (...)` para prateleira | `df.categoria.isin([...])` |
| `categoria = 'Dealers'` para vitrine | `df.categoria.eq("Dealers")` |
| Exclusão explícita de BTU cruzado | regex de inclusão + regex de exclusão sobre `produto` |
| `pricetrack_daily` (modal/piso) | **não existe no Drive** ⇒ pendente, ou `coletas.preco` com troca de base declarada |
| Precedência quente-sobre-frio | precedência da partição `run-tier*` sobre as partições da coleta no mesmo dia |

## O que muda na prática no relatório

Três coisas, e todas as três precisam aparecer em rodapé:

1. **Denominador da presença** — sem `estado_match`, a base é o total de cards
   de AC do dia, não só os mapeados.
2. **Preço 9K/12K** — sai pendente por padrão, porque o PriceTrack não migrou
   para o histórico.
3. **Origem** — "Drive (histórico frio)" em vez de "Supabase", para que a
   comparação semana-a-semana saiba o que está comparando.
