# Mais Vendidos RAC — coleta diária e métricas de share de topo

> **Status:** ✅ Automatizado (Ago/2026) — substitui a rotina manual de exportar
> listas pelo Web Scraper em `.xlsx`.
> **Entrada:** `python scripts/collect_bestsellers.py`
> **Código:** `bestsellers/` · **Tabela:** `bestsellers` · **Migração:** `docs/migrations/011_bestsellers_diario.sql`

---

## Por que esta rotina existe

A coleta principal (`main.py`) mede **oferta, preço, posição e buy box**. Ela
não contém, e não pode conter, **volume de venda**. As listas de "Mais
Vendidos" dos varejistas são a única variável de **resultado** disponível no
nível do SKU, a custo zero — e o KPI que sai delas antecipa share antes do
GfK/Neotrust fechar o mês.

O que esta rotina protege contra: tratar cinco ordenações diferentes como se
fossem a mesma medida, e transformar ranking em share.

---

## KPI único

**% do top 10 ocupado pelo grupo Midea, por plataforma, por dia.**
Escopo: `tipo = SPLIT_HW`. Grupo Midea = Midea, Carrier, Springer, Comfee
(mesmo corte do GfK).

Baseline registrado em 10/08/2026 (segunda):

| Plataforma | % top 10 | Melhor posição |
|---|---|---|
| Magazine Luiza | 40% | #1 |
| Amazon | 20% | #3 |
| Mercado Livre | 11% | #3 |
| Leroy Merlin | 0% | #33 |
| Casas Bahia | 0% | ausente |
| Shopee | 0% | #14 |

Tudo o mais no brief é diagnóstico de apoio.

---

## Regras duras (codificadas, não sugeridas)

1. **Ranking é ordinal.** Posição #3 não é "3% de share" nem "3× melhor que
   #9". Nenhuma função soma posições entre plataformas ou converte ranking em
   share de mercado. O que a rotina produz é **share de topo de ranking** —
   medida de prateleira. Share de mercado vem de GfK/Neotrust.
2. **Comparação só entre o mesmo dia da semana.** `escolher_base_comparacao`
   procura a última data com o mesmo weekday; sem ela, o delta sai marcado
   como não comparável. Sábado promocional contra segunda produziu +8,6% de
   mediana de alta de preço em 10/08/2026 que era calendário, não competição.
3. **Nunca somar `vendidos` de bases diferentes.** Mensal (Shopee, "948
   Vendido/Mês") e acumulado vitalício (Mercado Livre, "+5mil vendidos") são
   unidades distintas. `velocidade_declarada` agrega apenas `base_vendidos = 'mes'`.
4. **Tendência exige 3 leituras.** Agregação semanal/mensal com menos que isso
   sai com `tendencia_valida = False`.
5. **Lacuna fica vazia.** Nada é interpolado ou estimado.

---

## Cadência

**Todo dia útil, entre 9h e 10h.** O ranking da Amazon é recalculado de hora em
hora — comparar sábado 22h com segunda 9h é ruído. Coletar sempre as seis
plataformas: faltou uma, o brief registra a ausência em vez de comparar bases
desiguais.

```bash
# cron (Oracle VM, BRT)
30 9 * * 1-5 cd ~/rac-position-tracker && ./venv/bin/python scripts/collect_bestsellers.py --notificar
```

---

## Uso

```bash
# Coleta do dia (todas as plataformas ativas) + brief
python scripts/collect_bestsellers.py

# Uma plataforma, browser visível (depurar seletores)
python scripts/collect_bestsellers.py --plataformas amazon --no-headless

# Duas páginas da lista (Amazon vai até #50 com --paginas 2)
python scripts/collect_bestsellers.py --plataformas amazon --paginas 2

# Relatórios agregados — leem o histórico, não coletam
python scripts/collect_bestsellers.py --relatorio semanal
python scripts/collect_bestsellers.py --relatorio mensal --ultimos 6

# Backfill do histórico manual (.xlsx do Web Scraper)
python scripts/collect_bestsellers.py --importar-xlsx ./uploads --data 2026-08-10
```

Saídas:

| Arquivo | Conteúdo |
|---|---|
| `output/bestsellers/bestsellers_<data>.csv` | Evidência **bruta**: tudo o que foi coletado, inclusive o que a validação reprovou |
| `output/bestsellers/brief_bestsellers_<data>.md` | Brief diário |
| `data/bestsellers/master_bestsellers.csv` | Série histórica — **só o que passou nos portões** |
| Supabase `bestsellers` | Mesma série validada, para dashboard/SQL |

A ordem é deliberada: o CSV bruto sai primeiro (nada depois dele custa a
coleta do dia e é o arquivo que se abre para entender por que uma plataforma
caiu); a validação roda em seguida; e só então o que sobrou entra no histórico
e no banco. Uma lista sem o parâmetro de ordenação gravada no master
contaminaria a série para sempre — quem a lesse meses depois não teria como
saber que aquele dia mediu relevância.

O histórico é **idempotente por (data, plataforma)**: recoletar depois de
consertar um parser substitui aquelas linhas em vez de duplicá-las.

---

## As seis listas

| Plataforma | Ordenação canônica | Mecânica | O que pode enganar |
|---|---|---|---|
| **Amazon** | `gp/bestsellers` | velocidade (recalculado de hora em hora) | O nó 17125373011 fica no departamento Casa: mistura split, janela e portátil |
| **Mercado Livre** | `mais-vendidos/MLB1646` | acumulado | "+5mil vendidos" é vitalício do anúncio; favorece anúncio velho. Traz seller (leitura de buy box) |
| **Magazine Luiza** | `sortType=soldQuantity` | acumulado | É busca, não categoria — sensível ao algoritmo de busca; viés para anúncio velho |
| **Shopee** | `sortBy=sales` / API `by=sales` | vendas/mês | **Sem a ordenação, a lista é RELEVÂNCIA** — universo diferente. Única fonte de velocidade real |
| **Leroy Merlin** | índice `production_products_most_sales` | declarado (**sob suspeita**) | 41% dos itens parados em 48h contra 12–19% nas demais. Não sustenta corte de verba sozinho |
| **Casas Bahia** | `orders_desc` (site: `ordenacao=maisvendidos`) | acumulado | Contaminação alta: 6 de 20 itens não-RAC em 10/08/2026, inclusive #3, #4 e #5 |

A Amazon e o Mercado Livre coletam por browser (Playwright + stealth do
`BaseScraper`); Magalu reaproveita o browser persistente + curl_cffi do
`MagaluScraper` (Akamai); Shopee, Leroy e Casas Bahia vão direto na API
(v4, Algolia e VTEX Intelligent Search).

---

## Portões de validação

Rodam **antes** de qualquer análise. Existem porque uma lista de relevância
parseia exatamente como uma lista de vendas — só o número no fim fica
mentiroso.

| Portão | Regra | Ação |
|---|---|---|
| Ordenação por vendas | parâmetro canônico ausente do **endpoint chamado** | **QUARENTENA** — plataforma sai da análise |
| Escopo de categoria | >15% de itens não-RAC | AVISO (itens ficam na base; o KPI os ignora) |
| Volume mínimo | abaixo de `max(10, 60% da lista esperada)` — na prática ≥18 para Amazon/Shopee/Leroy e ≥12 para Magalu/ML/Casas Bahia | AVISO de coleta truncada |
| Cobertura de campo | `preco` ou `titulo` <85% | AVISO de parser quebrado |
| Posições duplicadas | `rank` repetido | ERRO |
| Plataforma ausente | fonte pedida sem linhas | AVISO com o motivo da falha |
| Endpoint mudou | diferente da leitura anterior | AVISO — plataforma sai dos deltas |
| Dia da semana | base com weekday diferente | AVISO — delta não comparável |

**QUARENTENA é o único nível que remove dados.** A evidência é o `endpoint`
efetivamente chamado, nunca a URL declarada no registro — aceitar a URL
declarada tornaria o portão incapaz de reprovar qualquer coisa.

---

## O que procurar no brief

- **`veredito = CURADORIA SUSPEITA`** na tabela de estabilidade. Ranking com
  mais de 35% dos itens parados em 48h não é ranking de venda.
- **Movimento de piso acima de 10%** em marca concorrente: fim ou início de
  campanha.
- **Marca fora da lista monitorada aparecendo no top 10.** Em 10/08/2026: HQ,
  Hisense, Consul, Britânia, Daikin, Fujitsu. Cada aparição recorrente é um
  pedido de inclusão no painel.
- **Faixa de preço do líder.** #1 abaixo de R$ 1.800 em 9K ou 12K: a disputa do
  dia é de entrada — a linha de entrada é a arma, não a premium.
- **`itens_topN` pequeno.** Numa lista contaminada, "50%" pode ser 1 split em 2.

O script produz os números; **a leitura de negócio é do analista**. Não entregar
o markdown cru sem comentário.

---

## Adicionar um varejista novo

1. Achar a URL com a ordenação **por vendas** e testá-la no navegador. Se a
   ordenação não existir de forma explícita, **não adicionar** — relevância
   não serve e não é comparável com o resto da série.
2. Registrar a fonte em `bestsellers/config.py`: URL pública, parâmetros de
   ordenação aceitos, mecânica e base de `vendidos`.
3. Implementar a classe em `bestsellers/sources/` e registrá-la em
   `SOURCE_CLASSES`.
4. Rodar 3 dias antes de usar em decisão.

Prioridade de expansão, por volume de ofertas no painel PriceTrack de
04/08/2026: Extra (2.442), Ponto (2.064), Carrefour (1.843), Americanas
(1.164), Web Continental (684).

---

## Diagnóstico

| Sintoma | Onde olhar |
|---|---|
| Plataforma com 0 posições | `logs/bestsellers_*.log` + dump HTML em `logs/bestsellers_<plat>_*.html` |
| Amazon vazia | CAPTCHA em IP de datacenter — rodar com `--no-headless` local |
| Mercado Livre em gate | `logs/ml_gate_*.html`; antídoto: `python scripts/setup_local_profile.py --site mercadolivre` |
| Magalu com muro de login | `python scripts/setup_local_profile.py --site magalu` |
| Shopee HTTP 403 | sessão expirada: `python utils/session_grabber.py --site shopee` |
| Casas Bahia com HTML no lugar de JSON | Akamai — sessão: `python utils/session_grabber.py --site casasbahia` |
| Leroy HTTP 404 no Algolia | índice renomeado — conferir o `sortBy` na UI antes de trocar a constante |

Testes: `pytest tests/test_bestsellers_*.py` (parsers, portões, métricas,
persistência e import legado — nenhum toca a rede).
