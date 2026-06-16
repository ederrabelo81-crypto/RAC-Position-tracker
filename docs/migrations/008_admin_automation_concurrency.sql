-- docs/migrations/008_admin_automation_concurrency.sql
--
-- Aplicado em 16/06/2026 via Supabase MCP. Idempotente.
--
-- Serializa execuções da automação ADMIN (utils/admin_automation.py).
--
-- Sintoma: depois que a migration 007 zerou os timeouts do caso single-run
-- (um run pos_coleta rodou OK, 0 erros), três runs `dashboard_manual` quase
-- simultâneos (cliques repetidos em "Executar agora") voltaram a falhar em
-- `seed_depara` e `refresh_cache` com 57014 — um deles durou 383s. Causa: runs
-- CONCORRENTES travavam entre si:
--   • dois `REFRESH MATERIALIZED VIEW CONCURRENTLY` da mesma MV não rodam ao
--     mesmo tempo — o segundo espera o lock e estoura o statement_timeout;
--   • dois `seed_depara` (INSERT ... ON CONFLICT na mesma chave nova) idem.
--
-- Correção:
--   1. Mutex (admin_automation_lock) com TTL — claim atômico. O runner Python
--      pega o lock no início; se outro run estiver ativo, este pula (status
--      'skipped') em vez de disputar locks. TTL = auto-cura se um run morrer.
--   2. refresh_filter_options vira "skip-if-busy" via advisory lock — cobre o
--      botão "🔄 refresh filtros" do dashboard, que chama a RPC fora da pipeline
--      e poderia sobrepor o passo refresh_cache de um run.

-- ============================================================================
-- PASSO 1 — Mutex de execução (linha única + TTL anti-deadlock)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.admin_automation_lock (
    id          smallint    PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    holder      uuid,
    trigger     text,
    acquired_at timestamptz,
    expires_at  timestamptz
);
-- Alinha com as demais tabelas do projeto (acesso via service_role key).
ALTER TABLE public.admin_automation_lock DISABLE ROW LEVEL SECURITY;
INSERT INTO public.admin_automation_lock (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Claim atômico: o UPDATE só "pega" se o lock está livre ou expirado. Sob
-- concorrência o Postgres serializa o row lock de id=1 — só um run vence;
-- os demais veem o lock tomado e recebem false.
-- p_holder NULL é rejeitado: gravar holder=NULL faria o predicado "holder IS
-- NULL" tratar o lock como livre, permitindo que todo caller "adquirisse"
-- (bypass do mutex).
CREATE OR REPLACE FUNCTION public.admin_automation_try_lock(
    p_holder uuid, p_trigger text DEFAULT 'manual', p_ttl_seconds int DEFAULT 900)
RETURNS boolean LANGUAGE plpgsql SET search_path TO 'public' AS $$
DECLARE v_rows int;
BEGIN
    IF p_holder IS NULL THEN
        RAISE EXCEPTION 'admin_automation_try_lock: p_holder não pode ser NULL';
    END IF;
    UPDATE admin_automation_lock
    SET holder = p_holder, trigger = p_trigger,
        acquired_at = now(), expires_at = now() + make_interval(secs => p_ttl_seconds)
    WHERE id = 1 AND (holder IS NULL OR expires_at < now());
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows > 0;
END $$;

CREATE OR REPLACE FUNCTION public.admin_automation_unlock(p_holder uuid)
RETURNS boolean LANGUAGE plpgsql SET search_path TO 'public' AS $$
DECLARE v_rows int;
BEGIN
    UPDATE admin_automation_lock
    SET holder = NULL, trigger = NULL, acquired_at = NULL, expires_at = NULL
    WHERE id = 1 AND holder = p_holder;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows > 0;
END $$;

-- ============================================================================
-- PASSO 2 — refresh_filter_options: skip-if-busy (não espera o lock do MV)
-- ============================================================================

CREATE OR REPLACE FUNCTION public.refresh_filter_options()
RETURNS jsonb LANGUAGE plpgsql SET search_path TO 'public' AS $$
BEGIN
    -- Outro refresh já em andamento? Pula (o resultado seria o mesmo) em vez de
    -- esperar o lock do CONCURRENTLY e estourar o statement_timeout.
    IF NOT pg_try_advisory_xact_lock(hashtext('refresh_filter_options')) THEN
        RETURN jsonb_build_object('ok', true, 'skipped', 'refresh em andamento');
    END IF;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_filter_options_90d;
    RETURN jsonb_build_object('ok', true, 'refreshed_at', now());
END $$;

-- ============================================================================
-- VALIDAÇÕES
--   SELECT admin_automation_try_lock(gen_random_uuid());           -- true
--   SELECT admin_automation_try_lock(gen_random_uuid());           -- false (tomado)
--   -- (libere com admin_automation_unlock(<mesmo holder>))
-- ============================================================================
