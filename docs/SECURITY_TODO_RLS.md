# 🔒 Security TODO — RLS desabilitado no schema `public`

> **Status:** ⚠️ Aberto · **Severidade:** ERROR (externa) · **Origem:** validação
> read-only 2026-06-16 + Supabase Security Advisor (confirmado 2026-06-17).
> **Escopo:** infraestrutura/banco — **fora do escopo** do módulo Price Evolution.
> Registrado aqui por exigência do guardrail da tarefa.

## Problema

Todas as tabelas do schema `public` estão **expostas via PostgREST com RLS
desabilitado**. Como o dashboard usa a *service_role* mas a *anon key* também
alcança a Data API, qualquer um com a `anon` key **lê e escreve tudo**
(`coletas`, `pricetrack_daily`, `produtos_catalogo`, de-para, locks de
automação, etc.).

### Lints `rls_disabled_in_public` (ERROR) — tabelas afetadas
- `public.coletas`
- `public.pricetrack_daily`
- `public.rac_monitoramento` (legada/vazia, mas exposta)
- `public.pricetrack_import_log`
- `public.produtos_catalogo`
- `public.produtos_aliases`
- `public.produtos_depara_nome`
- `public.rac_products_magalu_shopee`
- `public.admin_automation_runs`
- `public.admin_automation_lock`

### Achados correlatos do advisor
- `policy_exists_rls_disabled` em `produtos_aliases` e `produtos_catalogo`
  (têm policy `*_select_publico` criada, mas RLS **não** ligado — a policy não
  vale nada até habilitar RLS).
- `security_definer_view` em `v_rac_brand_position` e
  `v_monitoramento_normalizado`.
- `materialized_view_in_api` em `mv_filter_options_90d` (selecionável por
  `anon`/`authenticated`).
- `anon_security_definer_function_executable` /
  `authenticated_...` em `public.rls_auto_enable()` (executável por `anon`).
- `function_search_path_mutable` em várias `fn_*`.

## Remediação sugerida (revisar antes de aplicar — **não** automatizado aqui)

```sql
-- 1) Habilitar RLS em cada tabela pública
alter table public.coletas              enable row level security;
alter table public.pricetrack_daily     enable row level security;
alter table public.produtos_catalogo    enable row level security;
-- ... idem para as demais tabelas listadas acima ...

-- 2) Policy de leitura pública (se o dashboard precisa de leitura anônima):
create policy "ro_select_public" on public.coletas
  for select to anon, authenticated using (true);

-- 3) Escrita só via service_role (a anon NÃO deve escrever)
--    -> não criar policy de insert/update/delete para anon/authenticated.

-- 4) Fixar search_path nas funções e revisar SECURITY DEFINER views/functions.
```

Docs: <https://supabase.com/docs/guides/database/database-linter?lint=0013_rls_disabled_in_public>

> ⚠️ Habilitar RLS **sem** policy de SELECT vai **quebrar** as leituras do
> dashboard (anon). Planejar a migração com as policies de leitura antes de
> ligar o RLS, idealmente fora do horário de coleta.
