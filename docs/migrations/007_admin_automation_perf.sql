-- docs/migrations/007_admin_automation_perf.sql
--
-- Aplicado em 16/06/2026 via Supabase MCP. Idempotente.
--
-- Elimina os erros de statement timeout (SQLSTATE 57014) da automação ADMIN
-- (utils/admin_automation.py). Os cinco passos que falhavam no dashboard
-- "🤖 Automação" eram, na raiz, operações sobre a tabela `coletas` (411k
-- linhas / 486 MB) estourando o teto de 8s herdado do role `authenticator`:
--
--   • 🌱 Seed de nomes novos no de-para      (seed_depara_nomes_novos)
--   • 🧬 Auto-resolução da fila REVISAR       (admin_normalizar_nome)
--   • 🔗 Propagação de-para → coletas (RPC)   (resolver_coletas_pendentes)
--   • ♻️  Refresh do cache de filtros          (refresh_filter_options)
--   • 🏷️  Normalização de marcas               (UPDATE coletas.marca via PostgREST)
--
-- Causas-raiz medidas em produção e como cada uma é endereçada:
--
--   1. NÃO havia índice em `coletas.produto` (só aparecia como 5ª coluna do
--      índice composto coletas_unique_run). Todo "WHERE produto = ..." varria
--      a tabela inteira (~8s). Afetava admin_normalizar_nome, o JOIN do
--      resolver e o anti-join/DISTINCT do seed.
--        → idx_coletas_produto. Igualdade em produto: ~8s → ~15ms.
--
--   2. resolver_coletas_pendentes re-normalizava via "produto_normalizado
--      NOT LIKE '%'||sku" — full scan de ~216k linhas por run pra atualizar 0
--      (auto-cura de remap, evento raro).
--        → Removido do caminho quente. A normalização toca só linhas ainda
--          NULL (idx_coletas_norm_pending). O remap passa a ser normalizado na
--          origem por admin_normalizar_nome. Run: ~60s+ → <10ms.
--
--   3. refresh_filter_options fazia REFRESH simples (~40s: 5 agregados DISTINCT
--      sobre 411k linhas) segurando ACCESS EXCLUSIVE e estourando os 8s.
--        → REFRESH ... CONCURRENTLY (não bloqueia leitura do dashboard).
--          Exige índice único na MV.
--
--   4. O backend (service_role) não tinha statement_timeout próprio e herdava
--      os 8s do `authenticator` — apertado demais pro refresh da MV e pelos
--      UPDATEs de manutenção que varrem a tabela.
--        → service_role = 120s. anon (3s) e authenticated (8s), usados pelo
--          dashboard, ficam intactos (proteção do API público preservada).
--
-- ============================================================================
-- PASSO 1 — Índices de apoio
--   CREATE INDEX CONCURRENTLY não roda dentro de transação. Rode este arquivo
--   com `psql -f` (autocommit por statement) OU aplique estes dois statements
--   fora de qualquer BEGIN/COMMIT. (Via Supabase MCP foram aplicados com
--   execute_sql, não apply_migration, justamente por isso.)
-- ============================================================================

-- Igualdade em produto (admin_normalizar_nome, JOIN do resolver, seed).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_coletas_produto
    ON public.coletas (produto);

-- Linhas ainda sem produto_normalizado (Tier A/B do resolver) — partial index
-- pequeno que torna os UPDATEs de normalização instantâneos em regime.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_coletas_norm_pending
    ON public.coletas (estado_match, sku_resolvido)
    WHERE produto_normalizado IS NULL;

-- Índice único exigido pelo REFRESH ... CONCURRENTLY (MV tem 1 linha).
CREATE UNIQUE INDEX IF NOT EXISTS mv_filter_options_90d_uniq
    ON public.mv_filter_options_90d (refreshed_at);

-- ============================================================================
-- PASSO 2 — Refresh NÃO-bloqueante do cache de filtros
-- ============================================================================

CREATE OR REPLACE FUNCTION public.refresh_filter_options()
RETURNS jsonb LANGUAGE plpgsql SET search_path TO 'public' AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_filter_options_90d;
    RETURN jsonb_build_object('ok', true, 'refreshed_at', now());
END $$;

-- ============================================================================
-- PASSO 3 — Resolver sem o full scan de auto-cura
--   UPDATE #1 (estado via de-para) é idêntico ao da migration 004; #2 e #3
--   passam a tocar apenas linhas com produto_normalizado IS NULL.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.resolver_coletas_pendentes()
RETURNS TABLE(resolvidas bigint, tier_a bigint, tier_b bigint)
LANGUAGE plpgsql SET search_path TO 'public' AS $$
DECLARE v_res bigint; v_a bigint; v_b bigint;
BEGIN
  -- 1) de-para → coletas (apenas linhas ainda sem estado)
  UPDATE public.coletas c
  SET familia_resolvida = d.familia,
      sku_resolvido     = d.sku,
      estado_match      = d.estado
  FROM public.produtos_depara_nome d
  WHERE c.produto = d.nome_coletado
    AND c.estado_match IS NULL;
  GET DIAGNOSTICS v_res = ROW_COUNT;

  -- 2) Tier A — catálogo (só o que ainda está sem produto_normalizado)
  UPDATE public.coletas c
  SET produto_normalizado = public.fn_produto_normalizado_catalogo(c.sku_resolvido)
  WHERE c.produto_normalizado IS NULL
    AND c.sku_resolvido IS NOT NULL;
  GET DIAGNOSTICS v_a = ROW_COUNT;

  -- 3) Tier B — descritivo da família (MAPEADO sem SKU)
  UPDATE public.coletas c
  SET produto_normalizado = public.fn_produto_normalizado_familia(c.familia_resolvida)
  WHERE c.produto_normalizado IS NULL
    AND c.estado_match = 'MAPEADO'
    AND c.sku_resolvido IS NULL
    AND c.familia_resolvida ~ '-(\d{4,5})-(F|QF|Q)$';
  GET DIAGNOSTICS v_b = ROW_COUNT;

  RETURN QUERY SELECT v_res, v_a, v_b;
END $$;

-- ============================================================================
-- PASSO 4 — admin_normalizar_nome normaliza produto_normalizado na origem
--   Ao gravar um MAPEADO recalcula produto_normalizado (catálogo p/ SKU,
--   família p/ MAPEADO sem SKU). Para estados não-MAPEADO mantém o valor atual
--   (preserva a normalização Python de marcas fora do catálogo). Fecha o gap
--   deixado pela remoção do auto-cura no resolver (PASSO 3).
-- ============================================================================

CREATE OR REPLACE FUNCTION public.admin_normalizar_nome(
    p_nome text, p_estado text, p_familia text DEFAULT NULL::text,
    p_sku text DEFAULT NULL::text, p_marca text DEFAULT NULL::text)
RETURNS jsonb LANGUAGE plpgsql SET search_path TO 'public' AS $function$
DECLARE
    v_atualizados_coletas int;
    v_atualizados_rac     int;
BEGIN
    IF p_estado NOT IN ('MAPEADO','FORA_ESCOPO','NAO_AC','REVISAR') THEN
        RAISE EXCEPTION 'Estado inválido: %', p_estado;
    END IF;

    IF p_sku IS NOT NULL AND NOT EXISTS (SELECT 1 FROM produtos_catalogo WHERE sku = p_sku) THEN
        RAISE EXCEPTION 'SKU não encontrado no catálogo: %', p_sku;
    END IF;

    INSERT INTO produtos_depara_nome (nome_coletado, estado, familia, sku, marca_norm, origem, revisado_em)
    VALUES (p_nome, p_estado,
            CASE WHEN p_estado = 'MAPEADO' THEN p_familia ELSE NULL END,
            CASE WHEN p_estado = 'MAPEADO' THEN p_sku ELSE NULL END,
            p_marca, 'manual', now())
    ON CONFLICT (nome_coletado) DO UPDATE
        SET estado     = EXCLUDED.estado,
            familia    = EXCLUDED.familia,
            sku        = EXCLUDED.sku,
            marca_norm = COALESCE(EXCLUDED.marca_norm, produtos_depara_nome.marca_norm),
            origem     = 'manual',
            revisado_em = now();

    -- Propaga para coletas (inclui produto_normalizado p/ MAPEADO)
    WITH upd AS (
        UPDATE coletas SET
            familia_resolvida = CASE WHEN p_estado = 'MAPEADO' THEN p_familia ELSE NULL END,
            sku_resolvido     = CASE WHEN p_estado = 'MAPEADO' THEN p_sku ELSE NULL END,
            estado_match      = p_estado,
            produto_normalizado = CASE
                WHEN p_estado = 'MAPEADO' AND p_sku IS NOT NULL
                    THEN public.fn_produto_normalizado_catalogo(p_sku)
                WHEN p_estado = 'MAPEADO' AND p_sku IS NULL
                     AND p_familia ~ '-(\d{4,5})-(F|QF|Q)$'
                    THEN public.fn_produto_normalizado_familia(p_familia)
                ELSE produto_normalizado
            END
        WHERE produto = p_nome
        RETURNING 1
    )
    SELECT count(*) INTO v_atualizados_coletas FROM upd;

    -- Propaga para rac_monitoramento
    WITH upd AS (
        UPDATE rac_monitoramento SET
            familia_resolvida = CASE WHEN p_estado = 'MAPEADO' THEN p_familia ELSE NULL END,
            sku_resolvido     = CASE WHEN p_estado = 'MAPEADO' THEN p_sku ELSE NULL END,
            estado_match      = p_estado
        WHERE produto_sku = p_nome
        RETURNING 1
    )
    SELECT count(*) INTO v_atualizados_rac FROM upd;

    RETURN jsonb_build_object(
        'ok', true,
        'coletas_atualizadas', v_atualizados_coletas,
        'rac_monitoramento_atualizadas', v_atualizados_rac
    );
END $function$;

-- ============================================================================
-- PASSO 5 — Folga de tempo só para o backend (service_role)
--   anon (3s) e authenticated (8s) — usados pelo dashboard — ficam intactos.
-- ============================================================================

ALTER ROLE service_role SET statement_timeout = '120s';
NOTIFY pgrst, 'reload config';

-- ============================================================================
-- VALIDAÇÕES
--   SELECT seed_depara_nomes_novos();                 -- <10ms
--   SELECT * FROM resolver_coletas_pendentes();        -- <10ms
--   SELECT refresh_filter_options();                   -- não bloqueia leitura
--   EXPLAIN ANALYZE SELECT count(*) FROM coletas WHERE produto = '...';  -- Index Only Scan
-- ============================================================================
