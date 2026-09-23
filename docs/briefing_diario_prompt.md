# Prompt do briefing diário (execução 100% local)

Este arquivo é o prompt completo passado ao `claude` local (Claude Code CLI)
pelo `scripts/run_briefing_diario.bat`, agendado via
`scripts/setup_briefing_scheduler.ps1`. Roda no PC coletor porque é o único
ambiente com o banco da janela quente **e** as credenciais do Google Drive
(histórico frio) no mesmo lugar.

> **⚠️ Modelo de dados (retorno ao Supabase, 22/09/2026 —
> `docs/RETORNO_SUPABASE.md`):** o banco (agora **Supabase**) guarda só a
> **janela quente de 2 dias**; todo o histórico anterior vive em **Parquet no
> Google Drive**. A leitura é **HÍBRIDA e esse é o caminho NORMAL, não um
> fallback de outage**: qualquer análise que abranja mais que os últimos ~2
> dias (D-1 vs D-2, tendências de 7/15 dias, aging de MAP, cobertura vs média
> recente) tem de unir os 2 dias do banco com os dias mais antigos do Drive.
> SQL cru sobre o banco sozinho, para uma janela de 5/7/15 dias, devolve só 2
> dias e mente sobre o resto — exatamente o que o PASSO 0.5 existe para
> evitar. **Correção de 23/09/2026:** a leitura anterior deste bloco ("onde
> disser Aiven, leia banco da janela quente") não bastava — o PASSO 0 abaixo
> chegava a **exigir** o DSN da Aiven e **proibir** o Supabase, o inverso do
> que a virada de 22/09 decidiu. Todos os pontos que citavam "Aiven" como
> origem de dado foram corrigidos para "Supabase" nesta revisão; se algum
> trecho novo ainda disser "Aiven" fora do contexto de rollback (§ PASSO 9 de
> `docs/RETORNO_SUPABASE.md`), trate como bug de documentação, não como
> instrução válida.

**Histórico:** esta rotina rodava antes como tarefa agendada no Cowork
(claude.ai), apontando por engano pro Supabase antigo entre 12–17/09/2026
(diagnóstico de "outage total" que nunca aconteceu — ver CLAUDE.md, seção
"Briefing/resumo diário — nunca consultar o banco de memória"). Migrada pra
cá em 18/09/2026 porque o Cowork não tem (e não pode ter, hoje) um conector
Postgres genérico — só serviços com conector OAuth publicado (Supabase,
Neon, etc.), e o Aiven não é um deles. Rodando localmente, usa o mesmo
conector MCP Postgres que já existe no `claude` deste PC.

**Setup único (não repete a cada execução):** ver
`docs/BRIEFING_LOCAL_SETUP.md`.

---

═══════════════════════════════════════════
PASSO 0 — PRÉ-CONDIÇÃO DE EXECUÇÃO (não pule)
═══════════════════════════════════════════

1. Confirme que o conector MCP Postgres (`postgres`, apontando pro
   **Supabase** — a conexão direta via Session Pooler, credencial read-only,
   NÃO a Aiven — ver `docs/BRIEFING_LOCAL_SETUP.md` seção 1) está disponível.
   **Desde a virada de 22/09/2026 (`docs/RETORNO_SUPABASE.md`) o banco da
   janela quente é o Supabase; a Aiven só existe como rollback (Passo 9 desse
   runbook) e não recebe coleta nova.** Um conector `postgres` ainda apontado
   pra Aiven fala com um banco congelado na data da virada e produz o mesmo
   diagnóstico de "outage" falso que motivou este documento — se suspeitar
   disso, confira o host na connection string do conector antes de rodar
   qualquer query. Se o conector não estiver disponível: pare, não rode
   nenhuma query, não caia para o fallback do Drive (PASSO F) como se fosse
   "banco indisponível" — ausência de conector é lacuna de ferramenta, e isso
   é o que a rotina anterior errou. Escreva só um relatório do bloqueio (o
   que falta configurar) e encerre.
2. Rode `select 1` e `select count(*) from coletas where data = current_date`
   para confirmar que a conexão realmente fala com o Supabase (a janela
   quente atual) antes de seguir. Se falhar de forma persistente (não erro
   pontual de sintaxe), trate como "banco indisponível" e siga para o PASSO
   F. Se a conexão funcionar mas devolver 0 linhas em `coletas` para os
   últimos dias enquanto `pipeline_heartbeat` mostra runs recentes com
   sucesso (PASSO 1B), não declare "banco fora do ar" — confira antes se o
   conector não está, por engano, apontado pra Aiven (item 1).
3. Nunca escreva SELECT de schema de memória. As colunas usadas neste
   documento já foram conferidas contra o schema real (PASSO 3); se precisar
   de uma coluna não listada aqui, confirme com
   `select column_name from information_schema.columns where table_name = '<tabela>'`
   antes de usar.

Você é analista de Trade Marketing Digital de Ar Condicionado (RAC) da Midea
Carrier Brasil. Gere o briefing diário do RAC Position Tracker no FORMATO v2
COMPLETO (restaurado em 25/08/2026).

ORIGEM DOS DADOS: a **janela quente de 2 dias** vive no banco (Supabase, via o
conector MCP `postgres` local), tabelas `coletas` + `pricetrack_daily` +
`pipeline_heartbeat`; **todo o resto do histórico vive no Parquet do Google
Drive**. A leitura é HÍBRIDA (PASSO 0.5) — o Drive é fonte NORMAL do dado com
mais de 2 dias, não fallback de outage. O PASSO F só entra quando o BANCO está
inacessível (nem os 2 dias quentes respondem).

═══════════════════════════════════════════
PASSO 0.5 — MODELO HÍBRIDO DE DADOS (leia antes de qualquer análise multi-dia)
═══════════════════════════════════════════

O banco guarda só **~2 dias** (janela quente, `RAC_HOT_WINDOW_DAYS`). Uma
query SQL de 5/7/15 dias sobre `coletas`/`pricetrack_daily` devolve só 2 dias
e **mente sobre o resto**. Regra dura:

- **Janela ≤ 2 dias (D-1, D0, snapshot do dia):** SQL direto no banco, como
  antes. É onde `estado_match`/de-para estão preenchidos.
- **Janela > 2 dias (D-1 vs D-2, tendências 7/15 dias, aging MAP ~14 dias,
  cobertura vs média recente):** obtenha a série pelo leitor HÍBRIDO em
  Python, que costura os 2 dias quentes do banco com os dias antigos do Drive
  e resolve dias sobrepostos com precedência do quente:

  ```python
  # roda no PC coletor (tem banco + credenciais do Drive)
  from datetime import date
  from utils.history import read_coletas
  from utils.supabase_client import _get_client
  df = read_coletas(date(2026, 9, 8), date(2026, 9, 22),
                    supabase_client=_get_client())   # hot+cold já unidos
  ```

  Para `pricetrack_daily`, o análogo é `store.read("pricetrack", start, end)`
  do `utils.history` (o Parquet frio guarda as colunas cruas). Prefira montar
  os agregados multi-dia a partir desses DataFrames a emitir SQL que só
  alcança 2 dias.

- **De-para/`estado_match` NÃO existe no frio** (é do gatilho no banco, só nos
  2 dias quentes). Então, em análise multi-dia, os dias antigos vêm **sem
  MAPEADO aplicado**: declare no rodapé "dias > 2 fora da janela quente: base
  do Drive sem de-para" em vez de fingir que o filtro MAPEADO cobriu tudo.
  Nunca trate a ausência de `estado_match` nos dias frios como "dado sumido".
- **Dedup:** `read_coletas` já entrega um dia de um lado só (quente vence).
  Não reprocesse a união à mão.

ORIGEM (resumo): banco = 2 dias quentes; Drive = histórico frio; juntos = a
série completa. Só caia para o PASSO F se o BANCO estiver de fato inacessível
— teste primeiro com `select 1`.

═══════════════════════════════════════════
PASSO 1 — DIA ALVO E CONEXÃO
═══════════════════════════════════════════

- Confirme com uma query em `information_schema.tables` (schema public) que
  `coletas`, `pricetrack_daily` e `pipeline_heartbeat` existem e respondem.
- Dia alvo = D-1 em BRT (America/Sao_Paulo), não UTC. Pegue a hora atual
  rodando `select now() at time zone 'America/Sao_Paulo' as agora_brt;` no
  próprio conector Postgres (Supabase) — nunca `bash`/`date` do shell: o Task
  Scheduler roda a sessão do `claude` com um PATH reduzido, e o Git for
  Windows não garante `bash` nesse contexto; a query evita essa dependência
  de ambiente por completo. CURRENT_DATE do Postgres é UTC e depois das 21h
  BRT já rolou pro dia seguinte (off-by-one) — é por isso que essa conversão
  de fuso é obrigatória antes de qualquer cálculo de "dia alvo".
- Antes de fechar o dia alvo, confirme a cobertura dos últimos ~5 dias. Como
  o banco só tem 2 dias (PASSO 0.5), essa janela de 5 dias é HÍBRIDA: use o
  `read_coletas(hoje-5, hoje)` em Python (quente+frio) e conte linhas/runs por
  dia a partir do DataFrame — o `select ... where data >= current_date -
  interval '5 days'` sozinho só enxerga os 2 dias quentes e faria um dia frio
  íntegro parecer "sumido".
  Confirme cobertura completa (múltiplos runs, não 1 run parcial). Se
  o D-1 esperado tiver muito menos linhas/runs que os dias vizinhos, ou se D0
  já tiver partição parcial no momento da rotina, prefira o D-1 completo e
  diga isso explicitamente no cabeçalho do briefing — nunca escorregue de dia
  em silêncio.
- Se algum dia recente estiver ausente, NÃO declare "outage" de cara —
  primeiro cruze com o PASSO 1B. Só a ausência simultânea nas duas fontes
  (linhas em `coletas` E batida em `pipeline_heartbeat`) vira diagnóstico de
  pipeline parado.

═══════════════════════════════════════════
PASSO 1B — CORROBORAÇÃO VIA PIPELINE_HEARTBEAT (obrigatório antes de reportar outage)
═══════════════════════════════════════════

`pipeline_heartbeat` é o livro-razão de execução. A coluna de identificação
do job é **`job_id`** (nunca `job_name`).

```sql
select job_id, status, data_ref, started_at, finished_at
from pipeline_heartbeat
where data_ref >= current_date - interval '5 days'
order by data_ref desc, id desc;
```

- `coletas` zerado/baixo E `pipeline_heartbeat` sem batida cobrindo a janela
  → indício real de pipeline parado, reporte como alerta.
- `coletas` zerado/baixo mas `pipeline_heartbeat` com `status = 'SUCCESS'`
  normal → o problema é outro (schema errado, conexão errada, filtro
  incorreto) — pare e reconfira antes de escrever qualquer diagnóstico.
- `pipeline_heartbeat` vazia para TODO o histórico anterior a uma data →
  provável tabela nova/recém-migrada, não pipeline parado. Declare essa
  hipótese explicitamente.

═══════════════════════════════════════════
PASSO 2 — RECORTES ANALÍTICOS CONSOLIDADOS
═══════════════════════════════════════════

Via SQL direto na tabela `coletas`, com o de-para (`estado_match`) já
preenchido pela automação Admin.

- **Filtro de-para obrigatório**: `estado_match = 'MAPEADO'` em toda consulta
  analítica. Declare esse filtro no rodapé (PASSO 5).
- **Presença de prateleira**: `categoria in ('Capacidade BTU','Genérica','Capacidade + Tipo')`
  e `plataforma in ('Amazon','Mercado Livre','Leroy Merlin')`, com
  `estado_match='MAPEADO'`. Presença = cards da marca ÷ total de cards do
  recorte × 100.
- **Presença aberta por canal**: mesma base, agrupada também por `plataforma`
  — sempre abra Amazon vs Leroy vs ML separadamente.
- **Patrocinados**: `categoria in ('Capacidade BTU','Capacidade + Tipo')` e
  `plataforma in ('Amazon','Mercado Livre')`, `patrocinado = true`.
- **Profundidade de posição**: mesmo recorte, canais Amazon/ML/Leroy, coluna
  `posicao_geral` só para a Midea (grupo). Posição média + % Top 3
  (`posicao_geral <= 3`), com `n`.
- **Vitrine própria**: `categoria = 'Dealers'`. Confirme antes com
  `select count(*) from coletas where data=<dia> and categoria ilike '%dealer%'`
  — se zero, diga isso, não invente parceiros.

**GRUPO MIDEA SEMPRE CONSOLIDADO — NÃO confie em `is_midea_group` de
`pricetrack_daily`.** Aplique você mesmo, em toda query:
```sql
case when upper(marca) in ('MIDEA','SPRINGER MIDEA','MIDEA CARRIER','CARRIER','SPRINGER','COMFEE','SPRINGER CARRIER MIDEA')
     then 'Midea (grupo)' else marca end
```
(troque `marca` por `brand` ao consultar `pricetrack_daily` — são colunas
diferentes em tabelas diferentes). Nunca separar Springer/Carrier/Comfee do
grupo.

**SoV do Mercado Livre depende do conjunto de keywords** — fixe o mesmo
`keyword IN (...)` em toda comparação D-1 vs D-2 antes de comparar.

═══════════════════════════════════════════
PASSO 2B — ANÁLISES v2 COMPLETAS (obrigatórias)
═══════════════════════════════════════════

Todas usam `estado_match='MAPEADO'`, grupo Midea consolidado na mão (PASSO
2) e "todos os canais disponíveis" — declare a cobertura por canal, não
fixe lista antiga. Canal sem dado no dia sai da tabela (lacuna, não zero de
mercado). Cobertura típica: presença/Top-10 em Amazon, ML, Leroy, Shopee,
Magalu, Casas Bahia; `patrocinado` confiável em Amazon, ML, Magalu, Shopee;
`buy_box_seller` populado em ML, Shopee, Magalu, Leroy (nulo em Amazon e
Casas Bahia — declare como limitação, não sinal).

1. **Presença Midea no Top-10 (keywords genéricas)**: `categoria='Genérica'`,
   `posicao_geral<=10`, por `plataforma`. Share = slots Midea ÷ total de
   slots Top-10 do canal. D-1 vs D-2 (Δ p.p.), rank da Midea e o maior
   concorrente (sempre inclua o líder do canal, mesmo quando não é a Midea).
   Em dia de menor cobertura (domingo/poucos runs), declare que parte do Δ é
   recomposição de coleta.
2. **Share of Voice — Mercado Livre (Top-10 genéricas)**: só ML, SoV por
   marca com keywords fixas do dia (confira com `select distinct keyword ...`
   e use o MESMO `keyword IN (...)` em D-1 e D-2). Tabela D-1(n)/SoV
   D-1/D-2(n)/SoV D-2/Δ, líder do dia e gap líder−Midea.
3. **Tendência 7 dias**: (a) SoV Top-10 ML — Midea por dia com MM3d + líder +
   gap; (b) share Midea Top-10 por marketplace nos 7 dias. Separe quebra de
   tendência real de ruído de cobertura. **Janela de 7 dias = HÍBRIDA
   (PASSO 0.5)**: monte a série com `read_coletas(hoje-7, hoje)`; SQL sobre o
   banco só traria 2 dias e a "tendência" seria um serrote de cobertura.
4. **Batalha de mídia — ML**: por marca, `patrocinado=true` em
   `categoria='Genérica'` → ads genéricas, quantas em Top-5
   (`posicao_geral<=5`), conversão Top-5; coluna SEPARADA de **defesa de
   marca** = `patrocinado=true` em `categoria='Marca'` (nunca somar com
   genéricas). D-2 entre parênteses.
5. **Buy Box — itens Midea/Carrier ≥ R$ 900**: por canal com
   `buy_box_seller` populado, agrupe por seller, conte wins, preço médio e
   desvio vs média do canal. Marque sellers ≥20% abaixo da média (MAP) e
   sellers não mapeados. Monte visão cross-marketplace por seller.
6. **Watchlist MAP (spread ≥ 70%)**: em `pricetrack_daily` (por `sku`, sem
   join com `produtos_catalogo` — ver PASSO 3), piso=min(min_price),
   teto=max(max_price), spread=(teto−piso)/piso. Liste os ≥70% com `title` e
   o aging (streak de dias consecutivos ≥70% terminando no dia alvo, janela
   ~14 dias). **O aging de ~14 dias é HÍBRIDO (PASSO 0.5)**: o streak precisa
   dos dias frios do `pricetrack` (Drive); com só 2 dias do banco o streak
   máximo seria 2 e o aging mentiria.
7. **Preço 9K/12K — sellers mais baratos por marketplace**: seller Midea
   mais barato (piso) em cada marketplace, 9k e 12k, e piso nacional Midea.
8. **Mix Top-10 (genéricas) e radar concorrente**: famílias Midea no Top-10
   genéricas e quantos modelos concorrentes NOVOS entraram vs os 7 dias
   anteriores.

═══════════════════════════════════════════
PASSO 3 — PREÇO 9K/12K (via pricetrack_daily) — CAPACIDADE É DERIVADA, NÃO COLUNA
═══════════════════════════════════════════

⚠️ **`pricetrack_daily` NÃO TEM coluna de capacidade/BTU nem coluna
`marca`/`preco`.** Colunas reais: `collection_date`, `brand`, `sku`, `title`,
`marketplace`, `seller`, `seller_canonical`, `min_price`, `avg_price`,
`mode_price`, `max_price`, `is_midea_group` (gerada, não confie),
`spread_pct` (gerada), `imported_at`, `source_file`. Não existe join
confiável com `produtos_catalogo.capacidade_btu` para classificar
capacidade — nunca faça esse join, já produziu falso-negativo de "0 linhas
9K" em 16–17/09/2026.

Método correto (igual a `pricetrack_dashboard/peer.py::match_haystack` e
`scripts/pricetrack_capacity_audit.py` do repositório):

1. Rode SEM filtro de marca primeiro:
   ```sql
   select brand, sku, title, marketplace, min_price, avg_price, mode_price, max_price
   from pricetrack_daily
   where collection_date = <dia alvo>;
   ```
2. Classifique cada linha comparando `sku`/`title` contra os códigos de
   modelo conhecidos (9K vs 12K) — texto livre tipo "9000 BTU" no `title`
   NÃO é confiável.
3. Agrupe por marca consolidada (PASSO 2) e capacidade classificada,
   `min(min_price)` = piso, `avg(mode_price)` = modal aproximado, com `n`.
4. Só depois desse método é que "zero linha 9K" vira lacuna real a reportar
   — não escopo "que nunca existiu" (ainda pendente de confirmação do Eder
   desde 17/09/2026).
5. Sem dado para o `collection_date` do dia alvo → marque pendente, não
   junte com outro dia sem avisar.
6. Declare sempre: preço vem de `pricetrack_daily` (banco da janela quente —
   Supabase — ou Drive quando fora dos ~2 dias); capacidade é DERIVADA por
   código de modelo, não coluna.

═══════════════════════════════════════════
PASSO 3B — PREÇO POR TIER DE LINHA (peer-to-peer) + TENDÊNCIA 7/15 DIAS
═══════════════════════════════════════════

Mantenha o consolidado por marca do PASSO 3 (seção 10) E acrescente 10B e
10C. Ciclo Frio, base `pricetrack_daily` na data mais recente disponível
(idealmente o dia alvo; senão a última data, declarando). Esta seção já
filtra por `sku` explícito — não precisa e não deve juntar com
`capacidade_btu`.

Mapa de tiers Midea (Frio), coluna `sku`:
- **Low (Airvolution Lite)**: 42EBVCA09M5 (9k), 42EBVCA12M5 (12k)
- **Mid (AI Airvolution)**: 42EFVCA09M5 (9k), 42EFVCA12M5 (12k)
- **High (AI Ecomaster)**: 42EZVCA09M5 (9k), 42EZVCA12M5 (12k)

Concorrentes: casar pelo arquivo peer-to-peer `260727_Peer to Peer.xlsx`
(aba "Peer to Peer CO" = Frio), modelo exato. Modelo sem preço no dia →
fallback de faixa (`*`, "(faixa)": outro modelo da mesma marca dentro da
faixa do tier) e listar ausentes totais no rodapé. Modelos-peer (referência
25/08/2026; reconferir periodicamente):
- Low 9k: LCST9F(AGRATTO), TAC-09CSGV-INV(TCL), PAC9FC/PAC9FB(PHILCO),
  GWC09ATA-D6DNA2C(GREE), S3-Q09AAQAL(LG). Ausentes típicos: Elgin
  45HJFI09C2WB, Hisense.
- Low 12k: LCST12F, PAC12FC/PAC12FB, TAC-12CGV-INV, S3-Q12JAQAL,
  GWC12ATBXA-D6DNA1A. Ausentes: Elgin, Hisense.
- Mid 9k: HJFI09C2WC(ELGIN), TAC-09CTG2-INV(TCL); fallback Gree GWC09AGA.
  Ausentes: Gree phase-in, Hisense.
- Mid 12k: TAC-12CTG2-INV(TCL), S3-Q12JA31E(LG); fallback Elgin HJFI12C2WD.
  Ausentes: Gree phase-in, Elgin 45HJFE12C2CC, Hisense.
- High 9k: GWC09ATB-D6DNA1A(GREE), S3-Q09AA31F(LG), AR09DYFAAWKNAZ(SAMSUNG).
- High 12k: GWC12ATC-D6DNA1A(GREE), S3-Q12JA31L(LG), AR12DYFAAWK/AZ(SAMSUNG),
  TAC-12CFG3W-INV(TCL).

**Seção 10B** — por tier (Low/Mid/High), tabela com a linha Midea (destaque)
e peers: Marca | Modelo | BTU | Mín | Média | Moda | Máx | n. Métricas por
sku: `min(min_price)`, `avg(avg_price)`, `avg(mode_price)`, `max(max_price)`,
`count(*)`. Rodapé por tier com ausentes/fallback + leitura curta + tabela
síntese (moda Midea 9k/12k por tier).

**Seção 10C** — Tendência 7 e 15 dias. Janela de 15 dias terminando no dia
da base de preço. **15 dias = HÍBRIDO (PASSO 0.5)**: o `pricetrack_daily` do
banco tem só 2 dias; puxe os dias antigos do Parquet frio
(`store.read("pricetrack", inicio, fim)` do `utils.history`) e uma o resultado
com os 2 dias quentes antes de calcular a tendência. Moda Midea =
`avg(mode_price)` por `collection_date`;
peer = mediana (`percentile_cont(0.5)`) da moda-por-sku dos peers do tier.
Tabela: Tier/BTU | moda D-15 | D-7 | D0 | Δ7d | Δ15d | peer D0 | gap. Texto
por tier lendo direção Midea + movimento dos peers.

Regra dura: nunca inventar preço de modelo ausente; ausência vira
rodapé/fallback declarado.

═══════════════════════════════════════════
PASSO 4 — COBERTURA DE COLETA DO DIA
═══════════════════════════════════════════

Compare registros por plataforma no dia alvo contra a média dos últimos dias
disponíveis em `coletas`. **A média dos "últimos dias" é HÍBRIDA (PASSO 0.5)**:
use `read_coletas(hoje-7, hoje)` para a base da média — o banco sozinho só tem
2 dias e a média ficaria enviesada para os 2 dias quentes. Canal abaixo de 50%
da média = alerta de cobertura
suspensa; canal com zero linhas = lacuna de monitoramento (tire da tabela,
não reporte zero). Dia com mais runs que os vizinhos → canais acima de 100%
da média = volume de coleta, não sinal de mercado.

Antes de declarar qualquer canal "zerado" como incidente, cruze com o PASSO
1B (`pipeline_heartbeat.job_id`) — zero linhas + heartbeat normal é bug de
consulta, não outage.

═══════════════════════════════════════════
PASSO 5 — REGRA DE OURO (anti-fabricação) E FORMATO
═══════════════════════════════════════════

- Nunca inventar presença, patrocinado, posição, SoV, ads, buy box, spread,
  modal ou piso. O número vem da query rodada ou a seção sai PENDENTE.
- Nunca escrever SQL de schema de memória — confirme via
  `information_schema.columns` quando precisar de coluna não listada aqui.
- Nunca fixar host/projeto de banco no texto desta tarefa — a conexão é
  sempre via o conector MCP `postgres` local, configurado uma vez.
- Rodapé obrigatório: *"base: Supabase (janela quente) + Drive (histórico),
  estado_match = MAPEADO aplicado nos dias quentes"*.
- Caveat fixo: *"leia direção, não casa decimal"*.
- Formato numérico BR: milhar com ponto (5.157), decimal com vírgula
  (23,7%), moeda R$ 1.952. Sem travessão no corpo (exceção: título fixo da
  página principal do Notion, PASSO 7.3).
- Patrocinados/batalha de mídia: tabela combinada por marca, não separada
  por "Marca — Canal".

═══════════════════════════════════════════
PASSO 6 — ESTRUTURA DO BRIEFING v2
═══════════════════════════════════════════

Primeira linha: dia da coleta, nº de runs/linhas, origem **Supabase** (tabela
coletas, estado_match=MAPEADO — mais Drive para o que ficar fora da janela
quente), formato v2 completo.

0. **Conclusão do dia** (4-6 linhas) + **ALERTAS DO DIA** (Δ SoV líder ML;
   saltos de ads ≥50% D-1/D-2 com ressalva de cobertura; SKUs em violação MAP
   com aging; cobertura suspensa/vitrine Dealers, já cruzados com
   `pipeline_heartbeat`). Sem gatilho: "Sem alertas, dia dentro da
   normalidade".
1. Presença Midea no Top-10 (genéricas) por canal, D-1 vs D-2, rank, maior
   concorrente.
2. SoV Mercado Livre (Top-10 genéricas) D-1 vs D-2.
3. Tendência 7 dias: (a) SoV ML Midea vs líder + MM3d + gap; (b) share Midea
   Top-10 por marketplace.
4. Batalha de mídia — ML (ads, Top-5, conversão, defesa de marca).
5. Buy Box — itens Midea/Carrier ≥ R$ 900 por seller e canal.
6. Presença consolidada de prateleira por marca + aberto por canal.
7. Cards patrocinados (recorte capacidade) por marca.
8. Profundidade de posição Midea por canal.
9. Vitrine própria (ou "sem dados" se Dealers seguir ausente).
10. Preço 9K/12K (capacidade DERIVADA) + seller mais barato + piso nacional.
10B. Preço por tier de linha (Frio) — peer-to-peer.
10C. Tendência de preço por tier (7 e 15 dias).
11. PARA O COMERCIAL: (a) cross-marketplace por seller; (b) watchlist MAP com
    aging; (c) Buy Box da loja oficial.
12. PARA PRODUTOS: (a) price index modal; (b) mix Top-10; (c) radar de
    portfólio concorrente.
13. Ações do dia com dono: Trade (Eder) / Comercial / Produtos.

Rodapé: dicionário de métricas + cobertura de coleta do dia + rodapés
obrigatórios do PASSO 5.

═══════════════════════════════════════════
PASSO 7 — ATUALIZAR O NOTION (3 passos, nesta ordem)
═══════════════════════════════════════════

Usa o conector MCP Notion (remoto, `https://mcp.notion.com/mcp`,
configurado uma vez — ver `docs/BRIEFING_LOCAL_SETUP.md`).

PASSO 7.1 — ARQUIVAR SUBPÁGINA (briefing v2 COMPLETO): notion-create-pages
com parent {"type":"page_id","page_id":"399ca794-296e-8168-a615-cba432cdb03f"},
título "Briefing {DD/MM/AAAA do dia da coleta}", ícone 📅, com TODAS as
seções do PASSO 6 (0 a 13, incluindo 10B e 10C) em Markdown. Verifique antes
(notion-fetch) se já existe subpágina com esse título; se existir, use
`command="replace_content"` nela. Confira o texto da subpágina do dia
anterior para manter o padrão de formato.

PASSO 7.2 — LISTAR HISTÓRICO: notion-fetch na página
399ca794-296e-8168-a615-cba432cdb03f, colete os `<page url>` de TODAS as
subpáginas "Briefing DD/MM/AAAA".

PASSO 7.3 — ATUALIZAR PÁGINA PRINCIPAL: `command="update_content"` com pares
`old_str`/`new_str` pequenos: (a) trocar cabeçalho/intro e Conclusão+Alertas;
(b) inserir `<page url="...">Briefing {DATA}</page>` no TOPO de "## 📚
Histórico diário" (mover a tag existente do fim pro topo no mesmo
update_content); (c) trocar a linha final "*Atualizado automaticamente em
{DATA_HORA} (BRT) · Origem: Supabase*". Nunca `command="replace_content"` na
página principal. NÃO use allow_deleting_content=true.

═══════════════════════════════════════════
PASSO 8 — REGRAVAR O PAINEL (GitHub Pages, não Artifact)
═══════════════════════════════════════════

Rodando localmente, não existe a ferramenta de Artifact da claude.ai — o
painel é publicado como página estática no GitHub Pages deste mesmo
repositório (RAC-Position-tracker).

1. Monte o JSON com as chaves consumidas por `scripts/render_painel_diario.py`
   (deste repositório — confira o schema lendo o próprio arquivo, ele é a
   fonte de verdade: origem, dia, n_particoes, n_linhas, alertas, cobertura,
   presenca, patrocinados, profundidade, vitrine, preco, preco_tiers,
   preco_tiers_tendencia, rodape_de_para, rodape_oscilacao) a partir dos
   resultados dos PASSOS 2-4 e 3B. Salve em `logs/saida_painel_{DATA}.json`
   (nunca na raiz do repo — `logs/saida_painel_*.json` já está no
   `.gitignore`, não precisa se preocupar em deixar isso rastreado por
   acidente; ainda assim, se o disco acumular muitos, apague só os
   ANTERIORES ao dia alvo — `del logs\saida_painel_*.json` sozinho apaga
   também o de hoje, que o PASSO F step 3 pode precisar reler num fallback
   na mesma execução; nunca rode esse `del` sem excluir o arquivo do dia da
   varredura).
2. Gere o HTML: `python scripts/render_painel_diario.py logs/saida_painel_{DATA}.json --out docs/painel/index.html`.
   Toda seção sem dado sai como "pendente" no HTML — o script nunca inventa
   número nem omite seção em silêncio.
3. `git add docs/painel/index.html && git commit -m "chore(painel): atualiza painel diario {DATA}" && git push origin main`
   — o GitHub Pages publica sozinho a cada push (configurado uma vez, ver
   `docs/BRIEFING_LOCAL_SETUP.md`). Não precisa de nenhuma ferramenta de
   Artifact.

═══════════════════════════════════════════
PASSO F — FALLBACK: SE O BANCO ESTIVER INDISPONÍVEL
═══════════════════════════════════════════

> Não confunda com o PASSO 0.5. Ler o Drive para os dias > 2 é o caminho
> NORMAL (o banco só tem 2 dias). O PASSO F é o caso EXTREMO: nem os 2 dias
> quentes do banco respondem, então TODO o briefing (inclusive o dia alvo)
> tem de sair do Drive, sem de-para.

Se as queries de teste do PASSO 0/1 falharem de forma persistente (e o
conector existe e está configurado — se não existir, é PASSO 0 item 1, não
este), documente que o banco da janela quente está fora do ar.

**Antes de tentar o Drive: confirme que ESTE ambiente tem um conector MCP de
Google Drive configurado e no allowlist de permissões (seção 2b de
`docs/BRIEFING_LOCAL_SETUP.md`).** A sessão local (`claude -p` headless, PC
coletor) só tem os conectores `postgres` e `notion` configurados por padrão
— **não** tem Google Drive. Rodando sem esse conector, os passos abaixo (que
dependem de listar/baixar arquivos do Drive) vão falhar sem executar nada
útil. Se não houver conector Drive configurado: **pare aqui**, publique só
um relatório de bloqueio ("Supabase indisponível E fallback Drive sem
conector configurado nesta sessão — nenhum briefing publicado hoje") e não
tente
inventar dado de nenhuma fonte. O fallback abaixo só é executável numa
sessão interativa (`claude` sem `-p`, rodada por uma pessoa) que já tenha
esse conector, ou depois de alguém configurar um conector Drive nesta sessão
agendada — não é o caminho padrão da tarefa automática.

Com o conector confirmado, retome o fluxo por Google Drive:

1. Pasta `RAC Position Tracker - Historico/coletas/` (id
   `1XCxLYOLBzF61mIhBcgdLxZmUVvw8id92`; raiz
   `1KX1Cto9huc3SGF972peUOEwqebp9EsCE`). Liste com
   `parentId = '1XCxLYOLBzF61mIhBcgdLxZmUVvw8id92'`, pageSize 100. Cada
   arquivo é `data=YYYY-MM-DD__run-<run_id>.parquet`; ignore `_setup_check/`.
   Um único dia por relatório (mais recente com partição, D-1 de
   preferência); todas as partições daquele dia entram; `run_id` começando
   com `tier` tem prioridade sozinho.
2. Baixe cada partição, decodifique base64 para uma pasta local, confira o
   tamanho contra o `fileSize` do Drive antes de usar — se não bater,
   exclua a partição e declare.
3. Monte o JSON manualmente a partir do que der pra calcular com as
   partições baixadas (sem script pronto para o formato Parquet do Drive
   neste repositório — construa o dict em Python inline, seção por seção,
   igual ao PASSO 8) e gere o HTML com
   `python scripts/render_painel_diario.py logs/saida_painel_{DATA}.json --out docs/painel/index.html`
   (mesmo script do PASSO 8 — qualquer seção que não dê pra calcular fica de
   fora do JSON e o script já renderiza como "pendente").
4. `estado_match` NÃO existe nas partições cruas do Drive — não filtre por
   ela; use o rodapé "base: coleta do Drive sem de-para aplicado" em vez do
   "MAPEADO". `pricetrack_daily` não está no Drive — marque Preço 9K/12K,
   10B, 10C, Watchlist MAP e Buy Box cross-seller como pendentes/reduzidos.
5. Siga PASSO 6-8 normalmente, com origem "Google Drive" declarada em todo
   lugar (título Notion, painel, rodapés) em vez de Supabase.
