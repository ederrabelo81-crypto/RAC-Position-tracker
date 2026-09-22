# Retorno da janela quente: Aiven → Supabase (janela de 2 dias)

> **Decisão (22/09/2026):** desfazer a migração para a Aiven e voltar a janela
> quente para o **Supabase**, encolhendo a janela de **15 dias para 2 dias**.
> Com 2 dias, a vitrine quente cabe com folga no free tier de 500 MB do
> Supabase, e os artefatos diários (briefing das 07:00 e relatórios do Notion)
> passam a ler sempre o dado mais fresco do próprio banco. Todo o histórico
> continua — como sempre esteve — em **Parquet no Google Drive**.

Este documento é o par simétrico de `docs/MIGRACAO_AIVEN.md`. Lá, a saída do
Supabase. Aqui, a volta.

---

## 0. TL;DR — a virada em uma frase

A troca de banco é **credencial, não código**: com `RAC_DB_DSN` vazio, todo o
projeto volta a falar Supabase no comando seguinte (`utils/db.py::
resolve_backend_name`). O trabalho de verdade não é "virar a chave" — é fazer
o **Supabase caber em 500 MB** (evacuar as tabelas grandes para o Drive e podar
`coletas` para 2 dias) e fazer os **dashboards lerem 2 dias quentes do Supabase
+ o resto do Drive** (o `app.py` interno hoje NÃO faz isso — ver §4).

```text
Estado hoje (Set/2026)                    Estado alvo (este documento)
─────────────────────────                 ────────────────────────────
coleta ─┬─► Aiven  (quente, 15d)          coleta ─┬─► Supabase (quente, 2d)
        └─► Parquet/Drive (todo histórico)        └─► Parquet/Drive (todo histórico)
RAC_DB_DSN = <dsn da Aiven>               RAC_DB_DSN = (vazio)
RAC_HOT_WINDOW_DAYS = 15                  RAC_HOT_WINDOW_DAYS = 2
dashboard lê o banco direto (30/90d)      dashboard lê 2d do banco + resto do Drive
```

---

## 1. Por que 2 dias muda a matemática

A migração para a Aiven aconteceu por um motivo só: 15 dias de janela quente
**não cabem** em 500 MB.

| Janela | Custo (`coletas` ≈ 34 MB/dia) | Cabe no Supabase (500 MB)? |
|---|---:|---|
| 15 dias | ~510 MB | ❌ (foi o muro que empurrou para a Aiven) |
| 7 dias | ~238 MB | 🟡 apertado com as demais tabelas |
| **2 dias** | **~68 MB** | ✅ **com folga** — é o que viabiliza a volta |

Com 2 dias, `coletas` ocupa ~68 MB. Somadas as tabelas de **referência**
(catálogo + de-para + superfície, <6 MB, que **não** saem do banco), sobra
espaço de sobra dentro dos 500 MB — desde que as tabelas grandes de fato
**saiam** (§5, Passo 2). A restrição de cota do Supabase não expira com o
tempo: ela sai quando o banco encolhe abaixo de 500 MB.

**Benefício colateral (o motivo declarado da decisão):** com a janela curta e
no Supabase, o briefing e os relatórios do Notion voltam a bater no banco que
recebe a coleta agora, sem depender de um conector Postgres/Aiven que o
Cowork/claude.ai não oferece (ver a seção "Briefing/resumo diário" do
`CLAUDE.md`). O incidente de 12–17/09 (briefing publicando "outage" de memória)
nasceu justamente de a rotina não ter um caminho estável até o banco vivo.

---

## 2. O que foi para a Aiven — inventário do que reverter

A migração da Aiven foi desenhada para ser **reversível por variável**. Quase
nada tem `if provedor == "aiven"` no código; o que muda é para onde
`RAC_DB_DSN` aponta. Mesmo assim, é bom ter o mapa do que foi tocado:

| Peça | O que faz hoje | O que muda na volta |
|---|---|---|
| `utils/db.py` | Adaptador psycopg2 com a cara do `supabase-py`. Roteia por `RAC_DB_DSN`. | **Fica.** É ele que devolve tudo ao Supabase quando o DSN some. Não apagar. |
| `.env` do PC coletor | `RAC_DB_DSN` = DSN da Aiven | **Remover** `RAC_DB_DSN`; setar `RAC_HOT_WINDOW_DAYS=2` |
| Secrets do GitHub Actions | `RAC_DB_DSN` = DSN da Aiven | **Remover** o secret `RAC_DB_DSN` |
| `seller_app/` (Streamlit Cloud) | Já está no **Supabase** (`SUPABASE_ANON_KEY`); nunca migrou | Nada a reverter — mas revisar a janela (§6) |
| Coleta (`main.py`), automação ADMIN, resolvedores, PriceTrack, livro-razão | Roteados por `utils/db.py` | Voltam ao Supabase sozinhos ao remover o DSN |
| Dashboards (`app.py`, `pricetrack_dashboard/app.py`) | Leem o banco direto | **Exigem mudança de código** para 2 dias (§4) |
| Migrações/DDL (`docs/migrations/019_*`) | Schema portável, roda em qualquer Postgres | **Fica.** O Supabase já tem o schema; nada a reaplicar |

**Regra dura da reversibilidade (`utils/db.py::dsn_from_env`):** `RAC_DB_DSN` e
`SUPABASE_DSN` apontam para bancos **diferentes**. `SUPABASE_DSN` é a conexão
Postgres direta do Supabase (usada para evacuar/podar, porque a cota derruba só
a API REST, não a porta 5432). Ele **não** seleciona o backend principal — quem
faz isso é só `RAC_DB_DSN`. Portanto, ter `SUPABASE_DSN` preenchido durante a
limpeza **não** desfaz a volta ao Supabase.

---

## 3. O modelo de dados alvo

```text
                    ┌──────────────────────────── coleta (3 turnos/dia) ───────────┐
                    │                                                              │
                    ▼                                                              ▼
        Supabase (janela QUENTE = 2 dias)                     Parquet no Google Drive (TODO o histórico)
        ─────────────────────────────────                    ────────────────────────────────────────────
        • coletas            (só D-1 e D0)                    • coletas      (desde jan/2026, para sempre)
        • pricetrack_daily   (só D-1 e D0)*                   • pricetrack   (idem)
        • referências (catálogo, de-para,                     (o histórico frio nunca parou de receber —
          superfície, seller_depara) ← curadas,                a gravação é dupla e independente)
          NÃO estão no Parquet, FICAM inteiras
```

*`pricetrack_daily`: ver §6 para a decisão de quanto manter quente.

**Leitura (o que os consumidores enxergam):**

- **Briefing/relatórios/Notion** → leem os 2 dias quentes do Supabase (dado
  fresco) e, quando precisam de mais, costuram o frio via
  `utils.history.read_coletas()`.
- **Dashboards** → 2 dias do Supabase **+** o resto do Drive, costurados na
  leitura (§4).
- **`seller_app`** → Supabase (inalterado no transporte; janela a decidir, §6).

A costura frio+quente **já existe e está testada**: `utils/history/
read_coletas(start, end, supabase_client=...)` lê o Parquet no intervalo
inteiro, lê a janela quente do banco (`hot_window_start(end)` — que passa a ser
`end - 1`), e resolve dias sobrepostos com **precedência do quente**. Nada disso
precisa ser reescrito; precisa ser **adotado** pelos dashboards que hoje batem
no banco direto.

---

## 4. A única mudança de código de fato: dashboards em 2 dias

Este é o ponto que o `MIGRACAO_AIVEN.md` não precisou enfrentar (lá a janela era
grande) e que a premissa de 2 dias torna obrigatório.

**Achado da análise:** o dashboard interno `app.py` lê `coletas`
**direto do banco**, em janelas de 30 e 90 dias, e por RPCs que varrem a tabela
inteira no servidor:

| Local em `app.py` | Padrão | Efeito com o banco em 2 dias |
|---|---|---|
| `get_filter_options` (~2320) | `client.table("coletas").gte("data", hoje-30)` + RPC `get_filter_options_fast(30)` | dropdowns encolhem para 2 dias |
| janela de 90 dias (~5205) | `count(*)` no banco | contagens caem |
| `get_mapeado_sem_sku`, cobertura, etc. | RPCs sobre `coletas` | passam a ver só 2 dias |
| `mv_filter_options_90d` (matview) | 90 dias no servidor | fica vazia fora da janela |

Ou seja: **podar o banco para 2 dias sem tocar no `app.py` faz o dashboard
interno perder 28–88 dias das suas análises.** A premissa "puxar 2 dias do
Supabase e o resto do Drive" **é** essa mudança de código.

`pricetrack_dashboard/app.py` já é parcialmente híbrido (tem
`_hot_window_days_safe()` e um `fallback_days` que cai no frio) — precisa só de
ajuste fino. O `app.py` interno é o trabalho real.

**Estratégia recomendada (faseada):**

1. **Camada de leitura única.** Criar um `carregar_coletas(start, end,
   colunas=...)` no `app.py` que chama `utils.history.read_coletas(start, end,
   supabase_client=_get_supabase())`. Trocar os call sites de
   `client.table("coletas").select(...).gte("data", ...)` por essa função. A
   precedência quente-sobre-frio já vem de graça.
2. **RPCs e matview** (`get_filter_options_fast`, `mv_filter_options_90d`,
   `get_cobertura_resolucao`) rodam **no servidor** e só veem o que está no
   banco. Duas saídas:
   - **(a)** derivar as opções de filtro do DataFrame já costurado (frio+quente)
     em vez da RPC — mais simples, custa uma varredura em pandas do período;
   - **(b)** manter a RPC só para os 2 dias e unir com os valores distintos que
     vierem do frio. Preferir **(a)** para começar: menos superfície, e o frio
     é lido de qualquer forma.
3. **Cobertura/reconciliação** (`estado_match`, `sku_resolvido`): essas colunas
   são preenchidas pelo gatilho no banco e **não estão no Parquet**
   (`utils.supabase_client.map_record` não as inclui). Fora da janela de 2 dias
   elas não existem no frio — então os painéis de cobertura passam a ser,
   por construção, **painéis dos 2 dias quentes**. Deixar isso explícito na UI
   ("cobertura da janela quente") em vez de parecer um bug de dado sumido.

> **Por que não é opcional:** sem o passo 1, o item "os dashboards puxam os 2
> dias do Supabase e os demais do Drive" simplesmente não acontece — o
> dashboard interno mostraria 2 dias e ponto. Este é o maior item de esforço
> desta volta e deve ter a sua própria revisão/PR.

---

## 5. Passo a passo executável

Tudo roda **no PC coletor** (é ele que tem o `.env` com as credenciais do Drive
e o IP residencial), salvo os secrets do GitHub, que são no navegador.

> **Ordem importa.** Primeiro garanta que o frio está completo; só então apague
> qualquer coisa do banco. A regra de ouro é a mesma do
> `evacuate_pricetrack.py`: **exporta → confere → apaga**.

### Passo 0 — Repositório, dependências e conferência do frio

```powershell
scripts\sync_windows.bat
scripts\ensure_deps.bat --force
python scripts\gdrive_setup.py --check     # o histórico/CSV estão indo ao Drive?
```

Confirme que o Drive está configurado (`GDRIVE_FOLDER_ID` presente). Se o
backend cair em local, **pare aqui** — apagar do banco com o frio só no disco
desta máquina troca "cota cheia" por "dado perdido" (é a Guarda P1 do
`history_cli.py`, que se recusa a apagar nesse estado).

### Passo 1 — Conferir o estado atual do Supabase

O banco pode estar **acima** dos 500 MB (foi o que empurrou para a Aiven) e,
nesse estado, a API REST devolve 402 em tudo — mas a **conexão Postgres direta
segue funcionando**. Pegue a conexão pelo **Session pooler** (IPv4, porta 5432):
Supabase → Project Settings → Database → Connection string → **Session pooler**.

```powershell
$env:SUPABASE_DSN="postgresql://postgres.<project-ref>:SENHA@aws-0-<regiao>.pooler.supabase.com:5432/postgres"
```

Meça o tamanho (via `psql` ou pelo SQL Editor do painel):

```sql
SELECT pg_size_pretty(pg_database_size(current_database())) AS db,
       pg_size_pretty(pg_total_relation_size('pricetrack_daily')) AS pricetrack,
       pg_size_pretty(pg_total_relation_size('coletas'))          AS coletas,
       pg_size_pretty(pg_total_relation_size('rac_monitoramento')) AS legado;
```

### Passo 2 — Esvaziar as tabelas grandes para o Drive (liberar a cota)

**2a. `pricetrack_daily` (a maior — ~450 MB).** Já tem script dedicado que
exporta ao Parquet, confere dia a dia e só então apaga:

```powershell
python scripts\evacuate_pricetrack.py --dry-run          # o que sairia
python scripts\evacuate_pricetrack.py                    # exporta + CONFERE (não apaga)
python scripts\evacuate_pricetrack.py --confirmar-delete # apaga só depois de conferir
```

**2b. `coletas` antigas → Parquet, mantendo só a janela.** Com
`RAC_HOT_WINDOW_DAYS=2` já no `.env` (Passo 3), o migrador poda para 2 dias:

```powershell
python scripts\history_cli.py tier --dry-run   # quantos dias sairiam do banco
python scripts\history_cli.py tier --confirm   # migra p/ Parquet, confere e apaga
```

O `tier --confirm` migra todo dia anterior a `hot_window_start()` para o
Parquet, **confere** a gravação no Drive e só então apaga do banco (Guarda P1:
não apaga se o frio for local).

**2c. Recuperar disco de verdade.** `DELETE`/migração **não** encolhe o arquivo
— o Postgres guarda as páginas como dead tuples. Rode `VACUUM FULL` fora de
transação, numa janela de manutenção (pelo SQL Editor ou `psql`):

```sql
VACUUM (FULL, ANALYZE) pricetrack_daily;
VACUUM (FULL, ANALYZE) coletas;
```

**2d. Tabelas legadas.** `rac_monitoramento` (~33 MB) é legado. Confirme se o
`app.py` ainda a lê antes de mexer; se sim, mantenha; se não, ela pode sair.
`scripts/retention_cleanup.sql` tem a política de referência e é re-executável.

Confira que o banco ficou **abaixo de 500 MB** (repita o SQL do Passo 1). Assim
que cruzar o limiar, a API REST do Supabase volta a responder 200 sozinha.

### Passo 3 — Virar a chave no `.env` do PC coletor

```env
# ── Banco da janela quente: DE VOLTA ao Supabase ──
# RAC_DB_DSN removido/comentado → utils/db.py resolve para o Supabase.
# RAC_DB_DSN=postgresql://avnadmin:...        # (Aiven — desativado)

SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_KEY=<service_role>                    # escrita exige service_role

# Janela quente de 2 dias (antes 15)
RAC_HOT_WINDOW_DAYS=2
```

> **Regra dura:** a coleta com chave `anon` grava CSV/Parquet e deixa o banco
> para trás **em silêncio**. Confirme `service_role` (o log diz o papel da
> chave). `scripts\check_local_scheduler.ps1` confere.

### Passo 4 — Aplicar as mudanças de código dos dashboards (§4)

Faseado, na sua própria PR/revisão. Enquanto não estiver pronto, o dashboard
interno mostra só 2 dias — funcional, mas sem histórico. É a razão de este item
ter revisão separada e não entrar de carona na virada de infraestrutura.

### Passo 5 — Rodar uma coleta de verdade

```powershell
python main.py --platforms ml --pages 1
```

O log **não** deve mais dizer `[DB] ✓ Postgres direto`; deve mostrar
`[Supabase] ✓ Conexão estabelecida.` e o papel `service_role`. A coleta grava
**nos dois lugares**: Parquet no Drive (histórico) e Supabase (janela quente).

### Passo 6 — Remover o secret do GitHub Actions

GitHub → repositório → Settings → Secrets and variables → Actions → apague
`RAC_DB_DSN`. Sem o secret, os workflows
(`collect_amazon_sellers.yml`, `collect.yml`, `pricetrack_daily.yml`,
`watchdog.yml`, `pipeline_guard.yml`) voltam a gravar no Supabase. Cadastre
`RAC_HOT_WINDOW_DAYS=2` como *variable* (ou secret) se algum job podar/ler a
janela. **Atenção:** `.env` do PC e secrets do Actions são lugares diferentes —
mexer num não mexe no outro.

### Passo 7 — Validar

```powershell
python scripts\daily_status_check.py --no-notify
python scripts\pipeline_watch.py
streamlit run app.py
```

Confira: coleta grava no Supabase (não em 402); briefing/relatório leem dado
fresco; dashboard mostra 2 dias quentes + histórico do Drive (após o Passo 4);
`pipeline_watch` sem ausências novas.

### Passo 8 — Agendar a poda diária (manter os 2 dias)

A poda **tem que acontecer todo dia**, senão o Supabase volta a estourar em ~15
dias. Encaixe o `tier --confirm` como um estágio pós-coleta do turno da noite
(junto do `build_seller_offer_daily.py`), no `local_scheduled_collect.bat`:

```powershell
python scripts\history_cli.py tier --confirm
```

Idempotente por dia; só migra o que passou da janela. Some um alarme de tamanho
(o `pipeline_watch` já cobra ausência de execução, mas não tamanho de banco).

### Passo 9 — Desligar a Aiven (só depois de tudo verde)

Com uma semana de coleta estável no Supabase e o frio conferido, o serviço da
Aiven pode ser pausado/removido no console. **Não** antes: enquanto a volta não
estiver validada, a Aiven é o seu rollback de um comando (§7).

---

## 6. Decisões que a janela de 2 dias força

- **`pricetrack_daily`:** hoje guarda 3 linhas por (sku,seller,dia) — `Diário` +
  intra-dia. Com 2 dias quentes, o intra-dia antigo sai; o `Diário` histórico
  vive no Parquet (`DATASET_PRICETRACK`). O painel do PriceTrack já sabe cair no
  frio (`_hot_window_days_safe`), então precisa só herdar `RAC_HOT_WINDOW_DAYS=2`.
- **`seller_offer_daily` / `seller_app`:** o fato do seller é **reprocessado de
  `coletas`** (`refresh_seller_offer_daily(data)`). Com `coletas` em 2 dias, só
  2 dias são reconstruíveis. Duas opções, a decidir explicitamente:
  **(a)** aceitar que o `seller_app` é um painel de janela curta (2–N dias); ou
  **(b)** dar ao `seller_offer_daily` uma retenção **própria** maior que a de
  `coletas` (ele é derivado e menor, ~56 MB para 90 dias) — o que exige não podar
  essa tabela junto com `coletas`. Recomendo **(b)** se o `seller_app` precisa de
  série; senão **(a)**.
- **`bestsellers`:** tabela pequena, cadência própria, `referencia`-aware. Não
  entra na conta dos 2 dias; mantenha a política atual.
- **Cobertura/`estado_match`:** não está no Parquet (é do gatilho). Fora dos 2
  dias, não existe no frio — os painéis de cobertura passam a ser da janela
  quente por construção (§4, item 3).

---

## 7. Como voltar atrás (rollback)

Simétrico à ida. Reponha `RAC_DB_DSN` (Aiven) no `.env` e no secret do Actions;
tudo volta a gravar na Aiven no comando seguinte, sem migração — o histórico em
Parquet é o mesmo dos dois lados. Por isso o Passo 9 (desligar a Aiven) é o
**último** e só depois de validado.

Se quiser forçar explicitamente durante a transição:
`RAC_DB_BACKEND=supabase` ignora qualquer DSN e crava o Supabase;
`RAC_DB_BACKEND=postgres` faz o inverso.

---

## 8. Limites que continuam de pé

- **A poda precisa rodar de fato (Passo 8).** 2 dias só continuam 2 dias se algo
  apagar o resto todo dia. Sem isso, a cota estoura de novo.
- **`service_role` obrigatória para escrita.** Chave `anon` grava CSV/Parquet e
  ignora o banco em silêncio — o modo de falha da migração 012.
- **Dashboards híbridos são código, não config (§4).** Até o Passo 4, o painel
  interno é de 2 dias.
- **Referências não saem do banco.** `produtos_catalogo`, `produtos_depara_nome`,
  `produtos_aliases`, `plataforma_superficie`, `seller_depara` são curadas, não
  estão no Parquet e alimentam o gatilho `trg_resolve_familia_coletas`. Apagá-las
  quebra a resolução de família/SKU. **Nunca** entram na poda.
- **500 MB não é infinito.** 2 dias de `coletas` (~68 MB) + referências + o que
  ficar de `pricetrack_daily`/`seller_offer_daily` tem que somar < 500 MB.
  Monitore com o SQL do Passo 1.

---

## 9. Checklist

- [ ] Drive configurado e recebendo (`gdrive_setup.py --check`)
- [ ] Tamanho atual do Supabase medido (Passo 1)
- [ ] `pricetrack_daily` evacuada e conferida (Passo 2a)
- [ ] `coletas` podada para a janela e conferida (Passo 2b)
- [ ] `VACUUM FULL` rodado; banco < 500 MB (Passo 2c)
- [ ] `.env`: `RAC_DB_DSN` removido, `RAC_HOT_WINDOW_DAYS=2`, `service_role` (Passo 3)
- [ ] Dashboards híbridos (frio+quente) implantados (Passo 4 — PR própria)
- [ ] Coleta de teste grava no Supabase, não em 402 (Passo 5)
- [ ] Secret `RAC_DB_DSN` removido do GitHub Actions (Passo 6)
- [ ] Validação: briefing/relatório/painel/`pipeline_watch` (Passo 7)
- [ ] Poda diária agendada (Passo 8)
- [ ] Aiven desligada só após ≥1 semana verde (Passo 9)

---

*Criado em 22/09/2026. Par simétrico de `docs/MIGRACAO_AIVEN.md`.*
*A troca de backend é dirigida por `RAC_DB_DSN` (`utils/db.py`); a janela, por
`RAC_HOT_WINDOW_DAYS` (`utils/history/store.py`); a costura frio+quente, por
`utils.history.read_coletas`.*
