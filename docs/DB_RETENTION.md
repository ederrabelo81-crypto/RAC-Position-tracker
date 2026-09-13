# Política de Retenção do Banco (Supabase)

> **Status:** ✅ Ativa desde 2026-07-14 · Projeto `RAC` (org Mydea) · região sa-east-1
> **Script:** [`scripts/retention_cleanup.sql`](../scripts/retention_cleanup.sql)

> ⚠️ **Leia primeiro (Set/2026): este SQL é o DESBLOQUEIO DE EMERGÊNCIA, não o
> regime permanente.** A retenção do dia a dia agora é a **poda automática
> `tier`** (job `local_tier_migration`, estágio D da coleta noturna — ver
> [`HISTORICO_DRIVE.md`](HISTORICO_DRIVE.md)), que mantém no Supabase só a janela
> quente de **15 dias** e move o resto para o Drive. Use este SQL **só** quando o
> banco já estourou a cota e está **restrito** (HTTP 402): nesse estado a REST
> recusa até leitura, então o `tier` não consegue rodar e o espaço precisa ser
> liberado direto pelo SQL Editor. Depois de desbloquear (DELETE + `VACUUM
> FULL`), a poda noturna assume e mantém os 15 dias sozinha.
>
> **Por que a cota voltou a estourar (Set/2026).** A limpeza de 14/07 foi um
> *one-off* manual que ninguém agendou, e a própria meta "Equilibrada" (~1,4 GB)
> já era acima da cota de 1,1 GB. Pior: o motor de crescimento mudou — a coleta
> passou a 3 turnos/dia × todas as plataformas + dealers + Amazon (Set/2026), e a
> `coletas` sozinha chegou a **1,1 GB em 40 dias**. A janela de 90 dias desta
> política ficou inútil (nada em `coletas` tinha mais de 90 dias). A correção
> durável não é rodar este SQL de novo — é o **tier automático de 15 dias**.

## Por quê

Em 2026-07-14 o banco chegou a **3,09 GB** e a Supabase avisou uso acima da
cota. O espaço estava concentrado em duas tabelas:

| Tabela | Antes | Papel |
|--------|-------|-------|
| `pricetrack_daily` | 2344 MB (76%) | Fonte de verdade de **preço** (min/avg/mode/max por `data, turno, marca, sku, marketplace, seller`) |
| `coletas` | 696 MB | **Contexto**: posição, buy box, patrocinado, avaliação |
| `rac_monitoramento` | 33 MB | Legado, ainda lido/escrito pelo `app.py` → mantido |
| resto | < 6 MB | Catálogo, de-para, logs → mantido |

## Política "Equilibrada"

Escolhida por priorizar **preservar todo o histórico de preço diário** e cortar
só o que não sustenta análise de tendência:

- **`pricetrack_daily`** — mantém **100% das linhas `Diário`** (preço definitivo
  do dia fechado, desde jan/2026) e apenas os **últimos 30 dias** de intra-dia
  (`Manhã`/`Tarde`). O intra-dia antigo só alimentava os turnos recentes do
  dashboard; removê-lo **não perde nenhuma série de preço diário**.
- **`coletas`** — mantém os **últimos 90 dias**.
- Nenhuma outra tabela é tocada.

## Resultado do 1º run (2026-07-14)

| | Antes | Depois |
|---|---|---|
| Banco | 3093 MB | **1443 MB** (−53%) |
| `pricetrack_daily` | 2344 MB | 885 MB |
| `coletas` | 696 MB | 505 MB |

Linhas removidas: **2.061.970** (pricetrack intra-dia >30d: 2.040.023 ·
coletas >90d: 21.947). Histórico `Diário` intacto desde `2026-01-01`; coletas
desde `2026-04-15`. Sem quebra — não há FK de entrada nessas tabelas e a
materialized view `mv_filter_options_90d` se recompõe no refresh.

## Como re-executar

Cadência sugerida: **mensal** (ou quando o uso voltar a subir). O script usa
`CURRENT_DATE - N`, então é re-executável sem editar.

```bash
# via psql / SQL editor do Supabase
\i scripts/retention_cleanup.sql
```

> ⚠️ **VACUUM FULL é obrigatório para recuperar espaço.** `DELETE` sozinho não
> reduz o tamanho reportado (`pg_database_size`) — o Postgres mantém as páginas
> como *dead tuples*. Rode `VACUUM (FULL, ANALYZE) pricetrack_daily;` e
> `VACUUM (FULL, ANALYZE) coletas;` **fora de transação** (pega `ACCESS
> EXCLUSIVE` lock; use janela de manutenção). Pelo pooler do MCP o comando pode
> estourar o timeout de 60s do cliente mas **continua rodando no servidor** —
> confira com `SELECT * FROM pg_stat_activity WHERE query ILIKE '%VACUUM%'`.

## Para caber numa cota menor (<1,1 GB)

A política Equilibrada mantém o banco em ~1,4 GB. Se a cota exigir menos sem
upgrade de plano, as alavancas (custo analítico crescente) são:

1. Reduzir a janela de intra-dia do pricetrack (30 → 14 ou 7 dias).
2. Reduzir a janela de `coletas` (90 → 60 dias).
3. Só então cortar histórico `Diário` antigo (perde tendência de preço) — ou
   sumarizá-lo em tabela agregada semanal (roadmap item #7 do
   [PRICETRACK_INSIGHTS](PRICETRACK_INSIGHTS.md)).

## Nota de segurança (não relacionada ao tamanho)

O advisor da Supabase aponta **RLS desabilitado** em 10 tabelas `public` —
qualquer um com a `anon key` pode ler/escrever tudo. Habilitar RLS sem policies
**bloqueia todo acesso**, então não foi aplicado aqui. Ver
[`docs/SECURITY_TODO_RLS.md`](SECURITY_TODO_RLS.md) para o plano de policies.
