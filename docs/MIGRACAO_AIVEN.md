# Migração da janela quente: Supabase → Aiven PostgreSQL

> **Situação (14/09/2026):** o projeto Supabase está com **1052 MB** contra os
> **500 MB** do free tier. O Postgres segue saudável e gravável
> (`read_only=off`), mas a **API REST (PostgREST)** devolve HTTP 402 com
> `exceed_db_size_quota` em **todas** as operações — leitura e escrita. A
> última coleta que entrou foi **12/09**.
>
> **A restrição não expira com o tempo.** Ela sai quando o banco encolhe abaixo
> da cota. Não são 30 dias de espera: é uma questão de liberar espaço.

---

## 1. Por que migrar mesmo assim

O diagnóstico mostra que voltar ao estado anterior só adia o problema:

| Tabela | Tamanho | Linhas | Observação |
|---|---:|---:|---|
| `coletas` | **482 MB** | 486.034 | **só 14 dias** (30/08 → 12/09) |
| `pricetrack_daily` | **451 MB** | 995.026 | pendente de reimport (bug do PIX) |
| `seller_offer_daily` | 56 MB | 90.674 | |
| `rac_monitoramento` | 33 MB | 38.509 | tabela legada, sem coleta nova |
| demais | ~17 MB | | |

A janela quente custa **~34 MB/dia**. Quinze dias são **~510 MB** — sozinha,
ela já não cabe no free tier do Supabase. E não cabe em quase nenhum outro:

| Provedor | Free tier | Cabe 15 dias? |
|---|---:|---|
| **Aiven PostgreSQL** | **1 GB** | ✅ com ~50% de folga |
| Neon | 0,5 GB/projeto | ❌ |
| Supabase | 0,5 GB | ❌ (é o muro atual) |
| Prisma Postgres | 0,5 GB | ❌ |

Daí a escolha: **Aiven**, que é Postgres de verdade. As 19 migrações rodam sem
edição, as funções PL/pgSQL (`turno_ordinal`, `refresh_seller_offer_daily`) e
as views (`v_seller_buybox_share`) continuam valendo, e o repositório já falava
psycopg2 no importador do PriceTrack.

**O que NÃO muda:** o histórico completo continua em **Parquet no Google
Drive**, exatamente como a aplicação foi desenhada. A janela quente é só a
vitrine dos últimos 15 dias.

---

## 2. O que foi construído para isso

| Arquivo | Papel |
|---|---|
| `utils/db.py` | Adaptador que fala a **mesma API do `supabase-py`** sobre psycopg2. É o que permite trocar de banco sem mexer nas 112 chamadas `.table()` espalhadas pelo projeto. |
| `docs/migrations/019_schema_base_portavel.sql` | O DDL que **nunca existiu**: `coletas`, `produtos_catalogo`, `produtos_aliases`, `produtos_depara_nome`, `rac_monitoramento` e `rac_products_magalu_shopee` foram criadas à mão no painel do Supabase. Extraído da produção, não escrito de memória. |
| `scripts/db_bootstrap.py` | Aplica as 26 migrações na ordem certa, uma vez cada. |
| `scripts/db_migrate_hot.py` | Carrega os últimos 15 dias **do Parquet**, não do Supabase. |
| `scripts/evacuate_pricetrack.py` | Tira `pricetrack_daily` do Supabase para destravar a cota. |

---

## 3. Passo a passo

Tudo roda **no PC coletor** (é ele que tem o `.env` com as credenciais do Drive
e o IP residencial).

### Passo 0 — Atualizar o repositório e as dependências

```powershell
scripts\sync_windows.bat
pip install psycopg2-binary
```

> `psycopg2-binary` saiu de comentado para obrigatório no `requirements.txt`:
> é o driver do banco novo.

---

### Passo 1 — Criar a base na Aiven (~5 min, sem cartão)

1. Criar conta em <https://console.aiven.io/signup> (não pede cartão).
2. **Create service** → **PostgreSQL** → plano **Free** → região
   **aws-sa-east-1 (São Paulo)**, a mais perto do PC coletor.
3. Esperar o serviço sair de *Rebuilding* para **Running** (~3 min).
4. Em **Connection information**, copiar o **Service URI**. Ele tem esta cara:

   ```
   postgresql://avnadmin:SENHA@pg-xxxxx-rac.a.aivencloud.com:12345/defaultdb?sslmode=require
   ```

> **Não remova o `?sslmode=require`.** A Aiven recusa conexão sem TLS, e o erro
> que aparece sem ele (`server closed the connection unexpectedly`) não diz que
> o problema é esse.

---

### Passo 2 — Guardar o DSN no `.env`

No `.env` do PC coletor:

```env
# Banco novo — janela quente (Aiven PostgreSQL)
RAC_DB_DSN=postgresql://avnadmin:SENHA@pg-xxxxx-rac.a.aivencloud.com:12345/defaultdb?sslmode=require

# Supabase — mantenha por enquanto; ainda serve para puxar as referências
SUPABASE_URL=https://ailbsczkrympslpjwwko.supabase.co
SUPABASE_KEY=<service_role>
```

Com `RAC_DB_DSN` preenchido passam a falar com o banco novo: a coleta
(`main.py`), o dashboard interno (`app.py`), o livro-razão
(`pipeline_heartbeat`), a automação ADMIN, a manutenção, os resolvedores de
de-para, a auditoria de preço, o reenvio de CSV e o PriceTrack (importador e
painel). Sem a variável, tudo segue no Supabase — ela é a chave de virada.

**A exceção é o `seller_app/`**, que roda no Streamlit Cloud com credencial
própria (`SUPABASE_ANON_KEY`) e **continua no Supabase**. Atenção: publicar
`RAC_DB_DSN` como secret NÃO o vira — o `seller_app` não lê essa variável.
Migrá-lo exige **mudança de código** (ver §5), e ficou fora desta PR de
propósito: o isolamento do painel do lojista é a **credencial**, não um `if` no
código (regra dura do `docs/TRACK_POSITION_SELLER.md`), então essa virada
merece a própria revisão em vez de pegar carona aqui.

> Por que isso importa para o PriceTrack em particular: se o importador
> continuasse escrevendo no Supabase depois da virada, o Passo 8 esvaziaria
> `pricetrack_daily` lá para liberar a cota e o próximo import **reconstruiria
> exatamente a tabela de 451 MB que acabou de sair**.

---

### Passo 3 — Levantar o schema

```powershell
python scripts\db_bootstrap.py --dry-run    # mostra as 26 migrações
python scripts\db_bootstrap.py              # aplica
```

Esperado: `[bootstrap] pronto — 26 migração(ões) aplicada(s)`.

O script registra o que aplicou em `schema_migrations`; rodar de novo é seguro
e só aplica o que falta.

> **O usuário do banco precisa poder criar papéis.** O schema depende de
> `anon`/`authenticated`/`service_role` em mais de um ponto: a migração 007 faz
> `ALTER ROLE service_role SET statement_timeout`, e a 015 e a 016 criam
> `POLICY ... TO anon` **no mesmo arquivo em que criam tabelas essenciais**
> (`pipeline_heartbeat`, `seller_offer_daily`). Não existe, portanto, "pular as
> migrações de permissão" — por isso a 019 aborta com o motivo na tela se não
> conseguir criar os papéis, em vez de deixar um schema pela metade. Na Aiven, o
> usuário `avnadmin` do Service URI tem essa permissão.

---

### Passo 4 — Carregar os últimos 15 dias (do Parquet)

```powershell
python scripts\db_migrate_hot.py --dry-run     # quantas linhas existem no frio
python scripts\db_migrate_hot.py --dias 15     # carrega
```

Esta carga **não depende do Supabase**. Ela lê do histórico em Parquet do
Drive, que nunca parou de receber: desde Jul/2026 a coleta grava primeiro no
histórico e só depois no banco.

> **Detalhe que importa:** `coletas` tem um gatilho `BEFORE INSERT` que
> recalcula `familia_resolvida`/`sku_resolvido`/`estado_match` consultando
> `produtos_depara_nome`. Como essa tabela nasce vazia, o gatilho **zeraria a
> resolução de todas as linhas carregadas**. O script desliga o gatilho durante
> a carga e o religa no fim — o que estava no Parquet entra como estava.

---

### Passo 5 — Copiar as tabelas de referência

O catálogo e o de-para são **curados**, não coletados: não estão no Parquet.
Eles vêm direto do Postgres do Supabase (a restrição derruba a API REST, não a
conexão Postgres).

Pegue a senha em **Supabase → Project Settings → Database → Connection string
→ URI** e rode:

```powershell
python scripts\db_migrate_hot.py --referencias --dsn-origem "postgresql://postgres:SENHA@db.ailbsczkrympslpjwwko.supabase.co:5432/postgres"
```

Copia `produtos_catalogo`, `produtos_depara_nome`, `produtos_aliases`,
`plataforma_superficie` e `seller_depara`.

> Sem este passo, toda coleta **nova** entra com `estado_match` nulo e o painel
> mostra "não mapeado" para produto que está mapeado.

---

### Passo 6 — Conferir antes de virar a chave

```powershell
python scripts\daily_status_check.py --no-notify
streamlit run app.py
```

Confira no painel: contagem dos últimos 15 dias, filtros populados, aba de buy
box com seller preenchido. Se o número bater com o que você via antes de 12/09,
a virada está feita.

---

### Passo 7 — Rodar uma coleta de verdade

```powershell
python main.py --platforms ml --pages 1
```

O log deve dizer `[DB] ✓ Postgres direto (janela quente fora do Supabase).`
A coleta continua gravando **nos dois lugares**: Parquet no Drive (histórico) e
banco novo (janela quente).

---

### Passo 8 — Destravar o Supabase (opcional, mas recomendado)

Com a coleta já salva na base nova, dá para recuperar o Supabase. Evacuar
`pricetrack_daily` (451 MB) derruba o banco para ~590 MB; somando a limpeza de
`rac_monitoramento` e dos índices podados, fica abaixo dos 500 MB.

```powershell
$env:SUPABASE_DSN="postgresql://postgres:SENHA@db.ailbsczkrympslpjwwko.supabase.co:5432/postgres"

python scripts\evacuate_pricetrack.py --dry-run          # o que sairia
python scripts\evacuate_pricetrack.py                    # exporta e CONFERE (não apaga)
python scripts\evacuate_pricetrack.py --confirmar-delete # apaga só depois de conferir
```

A ordem é **exporta → confere → apaga**. A conferência relê o Parquet gravado e
compara a contagem dia a dia; se um único dia não bater, **nada é apagado**.

> `pricetrack_daily` é a escolha certa para sair primeiro porque o CLAUDE.md já
> a marca como pendente de reimport: os 36 dias foram gravados com a base de
> preço errada (colapso spot/PIX, ~10% alto onde há PIX). Esse dado já ia ser
> reescrito.

---

## 4. Como voltar atrás

Apague (ou comente) `RAC_DB_DSN` do `.env`. Tudo volta a apontar para o
Supabase no mesmo comando seguinte — não há migração de volta a fazer, porque o
histórico em Parquet é o mesmo para os dois lados.

---

## 5. Limites que continuam de pé

- **1 GB não é infinito.** A 34 MB/dia, a janela de 15 dias ocupa ~510 MB.
  `scripts/pipeline_watch.py` já cobra ausência de execução; convém somar um
  alarme de tamanho antes de chegar em 800 MB.
- **A poda de `coletas` precisa acontecer de fato.** Se as linhas velhas não
  saírem, a Aiven vira o mesmo muro em ~30 dias. `RAC_HOT_WINDOW_DAYS` controla
  a janela.
- **O `seller_app` no Streamlit Cloud** aponta para o Supabase via
  `SUPABASE_ANON_KEY` e **ainda não foi migrado**. Só publicar um secret não
  resolve: `seller_app/app.py` chama `create_client(...)` direto e nunca
  consulta `RAC_DB_DSN`, então a virada dele precisa de uma alteração de código
  (rotear por `utils.db`, como os demais pontos desta PR). O SQL abaixo prepara
  o lado do banco para quando isso for feito — crie o usuário só-leitura na
  Aiven:

  ```sql
  CREATE ROLE seller_ro LOGIN PASSWORD 'troque-isto';
  GRANT CONNECT ON DATABASE defaultdb TO seller_ro;
  GRANT USAGE ON SCHEMA public TO seller_ro;
  GRANT SELECT ON seller_offer_daily, seller_coverage_daily, v_seller_buybox_share TO seller_ro;
  ```

  O isolamento continua sendo **a credencial**, não um `if` no código — a mesma
  regra dura do `docs/TRACK_POSITION_SELLER.md`.
- **Índices podados de propósito:** `idx_coletas_data` (duplicata exata de
  `coletas_data_idx`) e `idx_coletas_seller_id` (zero varreduras desde que foi
  criado). Estão comentados na migração 019, com o motivo.
