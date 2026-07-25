# Solução radical? — agentes no Chrome, n8n e o destino dos dados

> **Data:** 25/07/2026 · **Contexto:** avaliação pedida pelo mantenedor —
> (1) dá para trocar a coleta por um agente automático (n8n) dirigindo um perfil
> do Google Chrome? (2) sem Supabase, dá para guardar os dados no Google
> Drive/Docs?
>
> **Método:** diagnóstico com dados reais do banco de produção, dos logs do
> GitHub Actions de 25/07 e do código do repositório. Nenhum número aqui é
> estimado sem estar marcado como tal.

---

## TL;DR

1. **A coleta não é o problema.** Ela rodou hoje (25/07) e produziu **5.651
   registros**. O que quebrou foi o **destino**: o Supabase responde HTTP 402
   (`exceed_db_size_quota`) e **rejeita 100% das gravações desde 16/07** — são
   **9 dias** de coleta que existem só como artifact do GitHub Actions.
2. **O agente no Chrome que você descreveu já está implementado** —
   `scrapers/local_browser.py` (Chrome real + perfil dedicado + CDP + IP
   residencial). Não é uma ideia nova a construir; é uma peça pronta que hoje
   depende do notebook estar ligado.
3. **n8n não resolve nada do que está quebrado hoje**, e um agente LLM dirigindo
   o browser seria mais caro e menos confiável que os parsers atuais. Mas a
   ideia acerta numa lacuna real: **ninguém foi avisado** de 9 dias de falha.
4. **Google Docs/Sheets: não.** **Google Drive guardando Parquet: sim, é
   viável** — mas antes disso existe a conta que decide tudo: **o plano free do
   Supabase comporta no máximo ~24 dias de histórico. Para sempre.**

---

## 1. Diagnóstico factual (25/07/2026)

### 1.1 A coleta está viva

Run `30162055309` (25/07, turno Abertura, GitHub Actions), conclusão `success`:

```
SUCCESS | CSV exportado: output/rac_monitoramento_20260725_1318.csv (5651 linhas)
INFO    | [Supabase] Enviando 5417 registros em 11 lote(s)...
INFO    | [INSERT] Tentadas=5417 | Inseridas=0 | Erros=500 | Não enviados (abortado)=4917
ERROR   | [Supabase] 🚫 Projeto RESTRITO por cota de armazenamento
          (exceed_db_size_quota): a API respondeu HTTP 402 e o upload foi
          abortado — 0 de 5417 registros gravados.
```

O workflow terminou **verde**. O `daily_status_check.py` — que detectaria isso —
**não está agendado em lugar nenhum** (`.github/workflows/` tem só `collect.yml`,
`pricetrack_daily.yml` e `pricetrack_intraday.yml`). Resultado: 9 dias de
"success" com zero linha gravada, sem nenhum alerta.

### 1.2 O banco está no teto do plano

| Medida | Valor |
|--------|-------|
| Plano da org `Mydea` | **free** (limite 500 MB) |
| `pg_database_size` | **449 MB** |
| Última linha em `coletas` | **2026-07-16** |
| Última linha em `pricetrack_daily` | **2026-07-16** |
| Último import PriceTrack com `SUCCESS` | 2026-07-16 20:13 UTC |
| Janela de histórico que sobrou | **26/06 → 16/07 = 21 dias corridos** |

Tabelas:

| Tabela | Tamanho | Linhas |
|--------|---------|--------|
| `pricetrack_daily` | 224 MB | 554.944 |
| `coletas` | 172 MB | 201.689 |
| `rac_monitoramento` (legado) | 33 MB | 38.509 |
| resto | < 8 MB | — |

### 1.3 A conta que decide o resto do documento

**449 MB para 21 dias de dados.** Esse é o número que importa:

> O plano free do Supabase (500 MB) comporta **~24 dias** do pipeline atual.
> Não "24 dias até precisar de faxina" — **24 dias, permanentemente**. Toda
> análise de tendência, sazonalidade, Black Friday vs. base, evolução de buy box
> mês a mês é estruturalmente impossível neste plano.

Ritmo observado (396 MB das duas tabelas ÷ 21 dias): **~26.400 linhas/dia** em
`pricetrack_daily` + **~9.600 linhas/dia** em `coletas` ≈ **19 MB/dia**. Descontando
o que não cresce (`rac_monitoramento` legado + catálogo ≈ 41 MB), sobram ~459 MB
de teto útil — daí os ~24 dias.

### 1.4 Dano colateral já ocorrido

`docs/DB_RETENTION.md` documenta a política "Equilibrada" de 14/07: preservar
**todo** o histórico `Diário` do PriceTrack (desde jan/2026) e 90 dias de
`coletas`. **Essa política não é mais a realidade do banco** — hoje o dado mais
antigo em ambas as tabelas é **26/06/2026**. A poda de emergência levou junto o
histórico de preço de janeiro a junho. O documento precisa ser corrigido para
não induzir a decisões erradas.

### 1.5 O que ainda dá para recuperar

Os 9 dias perdidos **não estão perdidos** — cada run do `collect.yml` sobe o CSV
como artifact com **retenção de 30 dias**:

- Artifacts de **16/07 expiram por volta de 15/08/2026**.
- `scripts/upload_csv.py` é **idempotente** (o próprio log do erro sugere ele).

**Ação com prazo:** baixar os artifacts de 17/07 a 25/07 e reenviar assim que
houver espaço. Depois de 15/08 esses dias somem de vez.

---

## 2. Pergunta 1 — "agente automático (n8n) assumindo um perfil no Chrome"

### 2.1 Isso já existe no repositório

`scrapers/local_browser.py` (478 linhas) faz exatamente o que a pergunta
descreve, e o `docs/COLETA_LOCAL_AUTENTICADA.md` documenta:

- Chrome **comum** (o binário real, sem flags de automação, sem
  `navigator.webdriver`), apontado para um perfil **dedicado e estável**
  (`data/chrome_profile/`), com a porta de debug ligada.
- Ataque via **CDP** (`connect_over_cdp`) com `rebrowser-playwright`, que oculta
  o `Runtime.enable` que o sensor.js do Akamai detecta.
- Roda no notebook, em **IP residencial** — a combinação que os antibots aceitam.
- Login manual **uma única vez** (só a Shopee exige conta); fica salvo no perfil.
- Ligado por `RAC_LOCAL_CHROME=1`, cobre **ML + Magalu + Shopee + Casas Bahia**,
  agendado via Task Scheduler com self-update por `git pull`.

Ou seja: a parte difícil da "solução radical" — fazer a coleta sair de um perfil
de Chrome de verdade, logado, com fingerprint aceito — **está pronta e em
produção**. O que falta nela não é inteligência: é que **depende do notebook
estar ligado**.

A prova disso apareceu no run de hoje: na VM/GitHub (IP de datacenter) a Casas
Bahia deu **0 registros** com circuit breaker do Akamai, e a própria mensagem de
erro do scraper aponta a saída — `RAC_LOCAL_CHROME=1` ou proxy residencial BR.

### 2.2 O que o n8n adicionaria — e o que não adicionaria

| n8n adicionaria | n8n **não** adicionaria |
|-----------------|-------------------------|
| Agendamento visual e retry com backoff | Passar por Akamai/PerimeterX (quem passa é o Chrome real + IP residencial) |
| Watchdog: "gravou linha hoje? não → Telegram" | Extrair posição/buy box do DOM (isso é dos parsers, com 592 testes) |
| Fan-out por plataforma (isolar falha de uma) | Resolver a cota do Supabase |
| Cola entre CSV → validação → upload | Tirar o notebook do caminho crítico |

Vale registrar o histórico: o n8n **já esteve neste projeto** e foi
**descontinuado em Jun/2026 por falta de uso** (README + `docs/n8n_orchestration.md`).
O motivo continua válido — ele roda self-hosted na máquina local, então herda
exatamente a fragilidade que se quer eliminar (PC desligado = nada roda).

**A lacuna que a sua intuição acertou é o watchdog.** Mas ela custa ~20 linhas
de YAML, não um servidor:

```yaml
# .github/workflows/watchdog.yml (esboço)
on:
  schedule: [{ cron: '0 23 * * *' }]   # 20:00 BRT
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install -r requirements.txt
      - run: python scripts/daily_status_check.py   # já existe, já notifica Telegram
```

Isso teria detectado o incidente em **24h em vez de 9 dias** e roda na
infraestrutura que já existe.

### 2.3 E um agente LLM dirigindo o browser (browser-use, AI Agent do n8n)?

Aqui é preciso ser direto sobre o trade-off:

| Critério | Parser determinístico (hoje) | Agente LLM no browser |
|----------|------------------------------|------------------------|
| Custo/dia | R$ 0 | 31 keywords × 7 plataformas × 2 turnos ≈ **434 extrações/dia**; a US$ 0,05–0,30 cada → **dezenas de dólares/dia** (estimativa) |
| Reprodutibilidade | Determinística, com fixture de produção (`tests/fixtures/ml_card_grid_20260725.html`) | Não determinística — duas execuções na mesma SERP podem divergir |
| Posição orgânica × patrocinada | Contagem exata do DOM | Sujeita a alucinação — e **posição é o produto** |
| `qtd_sellers`, buy box | Lido do JSON da API (Casas Bahia `sellers[]`, Leroy Algolia) | O agente teria que ler a mesma API — sem ganho |
| Manutenção | Ajuste de seletor quando o site muda | Prompt "se auto-conserta" — mas silenciosamente entrega dado errado |

O argumento a favor do agente é o custo de manutenção de seletor. Só que o
histórico recente do repo não sustenta esse medo: os fixes de Julho (ML, Leroy,
Shopee) foram **pontuais e cobertos por teste** — não é um pipeline se
desfazendo.

**Onde um agente LLM vale de verdade neste projeto:**

1. **Auto-diagnóstico**, não coleta: "Casas Bahia deu 0 hoje — abra o HTML salvo
   em `logs/casasbahia_debug_p1_*.html` e diga se é challenge, mudança de
   seletor ou catálogo vazio." Isso é caro por incidente, barato por mês, e
   ataca o que realmente consome tempo humano.
2. **Fallback de emergência** para uma plataforma que quebrou e ainda não tem
   fix, para não perder o dia.
3. **Descoberta de seletor** — o agente lê o HTML novo e *propõe o patch do
   parser*, que entra no fluxo normal de PR e teste. O LLM escreve o código, não
   substitui o código.

### 2.4 O que seria radical *de verdade*

O gargalo estrutural não é o browser nem a inteligência da extração. São dois:

1. **IP residencial** — hoje ele só existe enquanto o notebook está ligado. A
   saída já mapeada em `docs/AUTOMACAO_COLETAS_AUTENTICADAS.md` (Opção 2) é
   **proxy residencial/móvel BR** na VM (US$ 4–8/GB, consumo estimado 1–3 GB/mês
   para 2 coletas diárias). Isso destrava Casas Bahia, Magalu, Shopee e ML sem
   depender de ninguém ligar um PC.
2. **Destino dos dados** — a seção 3.

---

## 3. Pergunta 2 — guardar os dados no Google Drive/Docs?

### 3.1 Resposta curta por formato

| Destino | Veredito | Por quê |
|---------|----------|---------|
| **Google Docs** | ❌ Não | É documento de texto. 5.651 linhas/dia × 27 colunas não é texto — é tabela. Sem query, sem tipo, sem join. |
| **Google Sheets** | ❌ Não nesta escala | Limite de **10 milhões de células**. Com 27 colunas, isso é ~370 mil linhas ≈ **~39 dias de `coletas`** (e não caberia o PriceTrack junto) — praticamente o mesmo teto do Supabase free, com muito mais trabalho. E a API tem cota de escrita que 5,6 mil linhas/dia estressam. |
| **Drive como armazenamento de arquivos (Parquet)** | ✅ Viável | Ver 3.2. |
| **Drive guardando os CSVs crus** | 🟡 Melhor que nada | Resolve *não perder o dado*; não resolve *consultar o dado*. Serve como backup imediato hoje. |

### 3.2 Drive + Parquet + DuckDB — a versão que funciona

O padrão é "data lake de orçamento zero": em vez de um banco, arquivos
colunares particionados por data, consultados direto pelo dashboard.

```
Drive/rac-tracker/
├── coletas/data=2026-07-25/parte.parquet
└── pricetrack/collection_date=2026-07-25/parte.parquet
```

- **Espaço:** as colunas são altamente repetitivas (plataforma, keyword, marca,
  seller). Parquet com dicionário + zstd costuma comprimir 10–20× contra o
  Postgres. Estimativa: **~1–2 MB/dia**, ou **~500 MB/ano** — contra os 15 GB
  gratuitos do Drive. Deixa de existir "janela de retenção".
- **Leitura:** DuckDB lê Parquet direto e o `app.py` é Streamlit — a troca fica
  contida na camada de acesso a dados, sem reescrever as 19 páginas.
- **Escrita:** append de arquivo novo por dia. Sem concorrência, sem `VACUUM
  FULL`, sem cota estourando.

**O custo honesto:** perde-se SQL server-side (as RPCs de piso por marca, a
materialized view `mv_filter_options_90d`), perde-se escrita concorrente e
perde-se o `upsert` idempotente por `run_id` — o dedup passaria a ser
responsabilidade da escrita do arquivo. É um refactor de dias, não de horas.

### 3.3 Comparativo das saídas reais

| Opção | Custo/mês | Janela de histórico | Esforço | Dashboard |
|-------|-----------|---------------------|---------|-----------|
| **Supabase Pro (8 GB)** | US$ 25 | ~14 meses no ritmo atual | **Zero código** | Intacto |
| Continuar no free + emagrecer schema | US$ 0 | ~40 dias (ver 3.4) | Médio | Intacto |
| Postgres gerenciado alternativo (Neon/Turso) | US$ 0 no free | 0,5–5 GB conforme o serviço | Médio (migração SQL) | Quase intacto (é Postgres) |
| **Drive + Parquet + DuckDB** | US$ 0 | Anos | Alto | Reescreve camada de leitura |
| Google Sheets | US$ 0 | ~39 dias (só `coletas`) | Alto | Reescreve |
| Google Docs | US$ 0 | — | — | Inviável |

### 3.4 "Emagrecer o schema" — o que dá para cortar sem perder análise

Se a decisão for continuar no free, estes são os alvos, em ordem de retorno:

1. **Intra-dia do PriceTrack** — de 26.400 linhas/dia, só ~9.200 são `Diário`
   (a fonte de verdade de preço). Os outros ~17.250 (`Manhã`/`Tarde`) servem só
   aos turnos recentes do dashboard. Reduzir a retenção de intra-dia de 30 para
   **7 dias** corta ~65% do crescimento diário da maior tabela.
2. **`rac_monitoramento`** — 33 MB de tabela legada. O README a marca como
   "legado, ainda lido/escrito pelo `app.py`". Migrar as leituras e dropar
   devolve 33 MB (~2 dias de coleta).
3. **Colunas de screenshot** — `screenshot_busca`/`screenshot_produto` só fazem
   sentido se os screenshots estiverem ligados.
4. **Índices** — parte dos 449 MB é índice, não dado. Vale um
   `pg_stat_user_indexes` atrás de índice nunca escaneado.

Somadas, essas medidas derrubam o crescimento de 19 para ~12 MB/dia, o que leva
o teto do plano free de ~24 para **~40 dias**. Ou seja: **elas compram semanas,
não resolvem a necessidade de histórico longo.**

---

## 4. Recomendação

### Agora (destrava o serviço)

1. **Baixar os artifacts de 17/07–25/07** antes de 15/08 e guardá-los fora do
   GitHub (o Drive serve perfeitamente para isso — é o uso em que ele é bom).
2. **Liberar espaço** no Supabase (retenção de intra-dia para 7 dias +
   `VACUUM FULL`), então **reenviar os 9 dias** com `scripts/upload_csv.py`.
3. **Agendar o `daily_status_check.py` no GitHub Actions.** É o item de maior
   retorno por linha de código do documento inteiro: transforma "descobrimos
   depois de 9 dias" em "avisou no mesmo dia".

### Decisão de fundo (a pergunta real)

**Se a análise precisa de mais de ~24 dias de histórico — e para RAC, com
sazonalidade de verão e datas duplas, precisa — o plano free não é uma opção
técnica, é um teto.** As duas saídas coerentes:

- **US$ 25/mês no Supabase Pro** — compra ~14 meses de histórico, zero linha de
  código alterada, mantém as RPCs e o dashboard como estão. É a opção
  recomendada se houver qualquer orçamento.
- **Drive + Parquet + DuckDB** — custo zero e histórico ilimitado, ao preço de
  um refactor da camada de leitura e da perda das RPCs server-side. É a opção
  coerente se o orçamento for rigorosamente zero.

O caminho híbrido que combina os dois é defensável e provavelmente o melhor
custo-benefício: **Parquet no Drive como arquivo histórico completo** (barato,
imutável, cresce para sempre) + **Supabase free como janela quente de ~20 dias**
para o dashboard operacional. Cada camada faz o que faz bem.

### Sobre o "radical"

Não vale trocar os parsers por um agente LLM: eles funcionam, são baratos e são
testados. Vale usar agente para **diagnóstico e proposta de patch**, e vale ser
radical no que de fato limita o projeto — **IP residencial** (proxy BR na VM) e
**destino dos dados** (acima). O agente de Chrome que motivou a pergunta já está
implementado e em produção; ele só precisa deixar de depender de um notebook
ligado.

---

## Referências no repositório

| Arquivo | O que traz |
|---------|-----------|
| `scrapers/local_browser.py` | O "agente no perfil do Chrome" já implementado |
| `docs/COLETA_LOCAL_AUTENTICADA.md` | Setup e agendamento do Chrome local |
| `docs/AUTOMACAO_COLETAS_AUTENTICADAS.md` | Opção 2 — proxy residencial BR na VM |
| `docs/n8n_orchestration.md` | O n8n que existiu e por que saiu |
| `docs/DB_RETENTION.md` | Política de retenção (⚠️ desatualizada — ver §1.4) |
| `scripts/retention_cleanup.sql` | Poda re-executável + `VACUUM FULL` |
| `scripts/daily_status_check.py` | Watchdog pronto — falta agendar |
| `scripts/upload_csv.py` | Reenvio idempotente dos CSVs |
| `utils/supabase_client.py` | `is_quota_restricted_error()` — o fail-fast do 402 |
