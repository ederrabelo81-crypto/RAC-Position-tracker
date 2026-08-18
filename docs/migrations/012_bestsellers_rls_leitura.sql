-- Migração 012 — Policy de LEITURA para a tabela `bestsellers`.
--
-- Contexto: a 011 cria a tabela e NÃO liga RLS; no projeto de produção ela foi
-- ligada depois, à mão, e ficou assim: RLS LIGADA e nenhuma policy. Daí os
-- dois comandos abaixo — o ALTER cobre a instalação limpa (onde ele é o que
-- torna a policy efetiva) e é no-op onde a RLS já estava ligada.
--
-- O efeito colateral de RLS sem policy só aparece do lado de quem lê: com a
-- chave `anon`, o PostgREST responde `[]` e HTTP 200 — sem erro, sem log, sem
-- nada. A coleta grava (a chave `service_role` ignora RLS), e o dashboard
-- mostra uma página vazia indistinguível de "não coletou". Foi esse o
-- sintoma que motivou a página 🥇 Mais Vendidos: o dado existia no banco e
-- não aparecia em lugar nenhum.
--
-- Duas saídas, e esta migração é a SEGUNDA:
--
--   1. Manter a tabela fechada e dar ao dashboard a chave `service_role`
--      em `SUPABASE_KEY`. Preferível quando o painel roda em máquina
--      controlada (PC coletor, VM), porque a chave nunca sai dali.
--   2. Aplicar esta migração: leitura liberada para `anon`/`authenticated`,
--      escrita continua exclusiva do `service_role` (nenhuma policy de
--      INSERT/UPDATE/DELETE é criada, e com RLS ligada a ausência de policy
--      é negação). Necessária quando o dashboard roda no Streamlit Cloud
--      com a chave pública.
--
-- Exposição: o conteúdo é o ranking público das listas de mais vendidos dos
-- varejistas — o mesmo que qualquer pessoa vê abrindo a URL da lista. Ainda
-- assim, prefira a opção 1 quando ela for possível: liberar SELECT expõe
-- também o histórico consolidado, que é trabalho acumulado.
--
-- APLICADA no projeto RAC (ailbsczkrympslpjwwko) em 18/08/2026. Verificado na
-- ocasião: a `anon` passou a enxergar as 161 linhas (antes, zero) e o INSERT
-- pela `anon` continua recusado com insufficient_privilege.
--
-- NÃO é aplicada automaticamente por nada no repositório. Aplicação manual:
--   psql "$SUPABASE_DB_URL" -f docs/migrations/012_bestsellers_rls_leitura.sql
-- ou via Supabase SQL Editor / MCP apply_migration.

-- Sem RLS, policy é decoração: o acesso cai nos GRANTs da tabela, a policy
-- fica inerte e a escrita pela `anon` segue liberada. Ligar aqui é idempotente
-- e é o que torna verdadeira a promessa desta migração — leitura liberada,
-- escrita negada por ausência de policy.
ALTER TABLE bestsellers ENABLE ROW LEVEL SECURITY;

-- Idempotente: repetir a aplicação não falha.
DROP POLICY IF EXISTS bestsellers_leitura_publica ON bestsellers;

CREATE POLICY bestsellers_leitura_publica
    ON bestsellers
    FOR SELECT
    TO anon, authenticated
    USING (true);

COMMENT ON POLICY bestsellers_leitura_publica ON bestsellers IS
    'Leitura para o dashboard. Escrita segue exclusiva do service_role: sem '
    'policy de INSERT/UPDATE/DELETE, a RLS nega. Ver docs/BESTSELLERS.md.';

-- Conferência (RLS ligada + uma policy de SELECT):
--   SELECT rowsecurity FROM pg_tables
--    WHERE schemaname = 'public' AND tablename = 'bestsellers';
--   SELECT polname, polcmd FROM pg_policy
--    WHERE polrelid = 'public.bestsellers'::regclass;
--
-- Reversão:
--   DROP POLICY IF EXISTS bestsellers_leitura_publica ON bestsellers;
