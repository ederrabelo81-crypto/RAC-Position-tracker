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
6. **Mais Vendidos ≠ Relevância.** Cada lista carrega `referencia`
   (`mais_vendidos` × `relevancia`). Uma lista de *relevância* é a ordem de
   destaque do algoritmo da loja (mix de venda, margem, estoque, curadoria) —
   proxy, **não** medida de venda. As duas referências **nunca se comparam,
   agregam ou somam**; são séries próprias, em segmentos próprios no dashboard.

---

## Referência: Mais Vendidos × Relevância (Ago/2026)

Nem todo varejista expõe ordenação por vendas. Onde só há a lista de
"relevância" (Dufrio, Central Ar, Leveros, Ferreira Costa, Engage), a coleta a
registra com `referencia = relevancia` e mecânica `relevancia`, numa **série
separada**. Isso preserva a regra dura acima: o número de uma lista de
relevância nunca entra no mesmo KPI de uma lista de vendas.

- **Onde vive a distinção:** coluna `referencia` na tabela `bestsellers`
  (migração `013_bestsellers_referencia.sql`), gravada em toda linha a partir
  de `SourceSpec.referencia`.
- **Portão de ordenação:** continua exigindo prova no `endpoint` — só que o
  *token* difere. Mais Vendidos prova o sort por vendas
  (`OrderByTopSaleDESC`/`orders_desc`); Relevância prova sua âncora
  (`OrderByScoreDESC`/`sort=relevance`). Sem o token, quarentena — com a
  mensagem certa para cada referência.
- **Dashboard:** a página 🥇 Mais Vendidos tem um seletor **Referência da
  lista** na barra lateral. Trocar de referência recorta a série inteira antes
  de qualquer número; as duas nunca aparecem na mesma tabela.

Dealers de **Mais Vendidos** (VTEX, sort por vendas): Web Continental, Frio
Peças, Clima Rio, Ar Certo, Polo Ar, Bel Micro, Fast Shop, Bemol, Frigelar.

---

## Cadência

**Todo dia útil, entre 9h e 10h.** O ranking da Amazon é recalculado de hora em
hora — comparar sábado 22h com segunda 9h é ruído. Coletar sempre as seis
plataformas: faltou uma, o brief registra a ausência em vez de comparar bases
desiguais.

### Agendamento oficial: PC coletor Windows (Ago/2026)

A tarefa `RAC_Bestsellers` do Task Scheduler é **o** agendamento da rotina —
09:30, segunda a sexta, mais catch-up no logon. Roda no notebook porque é lá
que ficam o IP residencial e o Chrome logado: Amazon e Mercado Livre entram
pelas listas via browser e a Shopee só devolve lista com a sessão autenticada.
Em IP de datacenter (VM/Actions) a Amazon cai em CAPTCHA e a Shopee some — o
dia registraria ausência em duas das seis listas.

```powershell
# instala/atualiza as 3 tarefas (RAC_Local_Manha, RAC_Local_Noite, RAC_Bestsellers)
PowerShell -ExecutionPolicy Bypass -File scripts\setup_local_scheduler.ps1

Start-ScheduledTask -TaskName "RAC_Bestsellers"   # testar agora
PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1
```

Guardas (em `scripts\local_scheduled_collect.bat`, slot `bestsellers`):

| Guarda | Regra | Por quê |
|---|---|---|
| Dia útil | domingo e sábado são pulados | fim de semana tem calendário promocional próprio e o motor só compara mesmo dia da semana — a leitura extra não entra em delta nenhum |
| Janela | coleta de 9h a 11h; acima de 10h avisa no log | catch-up de logon não pode custar o dia inteiro, mas o deslocamento tem que aparecer no log |
| Marcador | `logs\coleta_bestsellers_<data>.done`, gravado só no sucesso | o gatilho de logon dispara várias vezes ao dia; a segunda coleta **substituiria** a leitura do dia (idempotência por `data`+`plataforma`), trocando a foto das 9h pela das 11h |

**Uma execução por dia, e só uma.** Se o cron da VM abaixo também estiver
instalado, os dois agendamentos colidem: não duplicam linha (a chave é
`data`+`plataforma`), mas o último a rodar sobrescreve a leitura do dia — e aí
a série mistura horários sem que nada no dado denuncie. Escolha um.

```bash
# cron (Oracle VM, BRT) — alternativa; NÃO usar junto com a tarefa do Windows
30 9 * * 1-5 cd ~/rac-position-tracker && ./venv/bin/python scripts/collect_bestsellers.py --notificar
```

**Pré-requisito de credencial:** a tabela `bestsellers` é a única com RLS
**ligada** e sem policy — só a chave `service_role` escreve nela. Com a chave
`anon` no `.env`, a coleta roda, o CSV e o master saem, e só o banco fica para
trás. `scripts\check_local_scheduler.ps1` verifica o papel da chave.

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

## Onde ver o resultado: dashboard 🥇 Mais Vendidos

```bash
streamlit run app.py   # menu INSIGHTS → 🥇 Mais Vendidos
```

A coleta grava em três destinos e a página mostra a **união** de
**Supabase `bestsellers`** com **`data/bestsellers/master_bestsellers.csv`**
(dedup por `data`+`plataforma`+`rank`, a mesma chave idempotente do coletor,
com o banco tendo precedência); os CSVs brutos de
`output/bestsellers/bestsellers_*.csv` entram só quando não há master. Unir em
vez de escolher a primeira fonte é deliberado: o banco fica para trás sempre
que o upload falha e o master só tem o que aquela máquina coletou — ler um só
esconderia dias inteiros. O rodapé de origem diz quantos dias vieram de cada
fonte, e o motivo de o banco não ter servido aparece no topo em vez de sumir:
cair no CSV local em silêncio faria um banco mudo passar despercebido por
semanas.

Leitura reprovada no portão de ordenação entra em **quarentena antes de
qualquer número** — sai do KPI, dos deltas e dos gráficos, e aparece
nomeada na aba 🩺 Validação.

| Aba | O que responde |
|---|---|
| 🎯 KPI top N | % do top N da Midea por plataforma no último dia, delta contra a base, e a série diária |
| 📈 Evolução | agregação semanal/mensal e o ganho/perda em p.p. entre períodos (`tendencia_valida` visível) |
| 🏁 Ranking do dia | a lista inteira de uma plataforma, com ou sem o recorte split hi-wall |
| 🥊 Competição | quem ocupa o topo, o #1 de cada lista, marcas fora do radar, velocidade declarada e piso de preço |
| 🩺 Validação | os portões de qualidade do dia, cobertura por plataforma, estabilidade e o endpoint que prova a ordenação |
| 📄 Brief | o brief do dia — o arquivo gravado pela coleta ou remontado a partir da série |

A página **não** usa os Filtros Globais da sidebar: aqueles recortam a tabela
`coletas`, que é outra população. As regras duras valem igual na tela — nenhum
gráfico soma posições entre plataformas, e todo delta compara a mesma
plataforma contra o mesmo dia da semana.

### RLS: leitura liberada (18/08/2026), escrita não

`bestsellers` nasceu como a única tabela com RLS **ligada e sem policy**. O
efeito era um vazio-sem-erro: com a chave `anon` o PostgREST devolve `[]` e
HTTP 200 — sem erro, sem log —, a coleta grava normalmente (a `service_role`
ignora RLS) e o painel fica vazio, indistinguível de "não coletou". A página
ainda detecta esse caso comparando a tabela com a view
`bestsellers_kpi_top10` (que é do owner e continua legível), para o dia em que
alguém apontar o painel para outro projeto.

`012_bestsellers_rls_leitura.sql` foi **aplicada em 18/08/2026**: a `anon` lê a
tabela, e o painel funciona em qualquer host, inclusive Streamlit Cloud.

**A escrita continua exigindo `service_role`** — e é aí que mora a falha que
sobra. Sem policy de INSERT, a coleta rodando com a chave `anon` gera CSV e
master normalmente e só o banco fica para trás, em silêncio. Antes de
investigar "sumiu dia no painel", confira o papel da chave no PC coletor:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\check_local_scheduler.ps1
```

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

1. Achar a URL com a ordenação **por vendas** e testá-la no navegador.
   - Se existir sort por vendas → `referencia = mais_vendidos`.
   - Se a loja **só** tiver a ordem de relevância/destaque → `referencia =
     relevancia`. Entra numa **série própria**, nunca comparada com as listas
     de vendas (não é "não serve", é outra medição). NUNCA registre uma lista
     de relevância como `mais_vendidos`.
2. Registrar a fonte em `bestsellers/config.py`: `SOURCES` (URL pública,
   parâmetros de ordenação aceitos, `referencia`, mecânica, base de `vendidos`)
   e — se for dealer VTEX/HTML genérico — a `ColetaSpec` em `COLETA`.
3. Dealer VTEX/HTML: **nada mais a implementar** — os coletores genéricos
   (`sources/vtex_generic.py`, `sources/html_generic.py`) e o registro em
   `SOURCE_CLASSES` se resolvem por `COLETA`. Plataforma com mecânica/anti-bot
   próprio: implementar a classe em `bestsellers/sources/` e registrá-la em
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
