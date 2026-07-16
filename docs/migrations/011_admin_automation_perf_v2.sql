-- docs/migrations/011_admin_automation_perf_v2.sql
--
-- Aplicado em 16/07/2026 via Supabase MCP. Idempotente.
--
-- Segunda rodada de correção de statement timeout (SQLSTATE 57014) da automação
-- ADMIN (utils/admin_automation.py). A migration 007 resolveu os timeouts da
-- época; com a tabela `coletas` crescendo para ~621k linhas / 542 MB, três
-- operações NOVAS voltaram a estourar o teto de tempo herdado do PostgREST e o
-- run passou a terminar `status=partial`:
--
--   • 🏪 Normalização de plataformas/sellers  → count/UPDATE em coletas.seller
--   • 🔢 Backfill de SKU (sync)                → existência de linhas órfãs por produto
--   • 🌱 Seed de nomes novos no de-para        → seed_depara_nomes_novos()
--   • ♻️  Refresh do cache de filtros           → refresh_filter_options() (MV)
--
-- IMPORTANTE — teto de tempo efetivo: medições em produção mostram que estas
-- chamadas REST correm sob o statement_timeout de ~8s do `authenticator`, e NÃO
-- sob os 120s do `service_role` que a migration 007 configurou. (ALTER ROLE
-- service_role SET statement_timeout NÃO se propaga através do SET ROLE do
-- PostgREST, e um SET statement_timeout dentro da função NÃO estende o timer do
-- statement já em execução — verificado empiricamente.) Portanto o alvo aqui é
-- deixar cada operação BARATA o suficiente para caber em ~8s, não depender de um
-- timeout maior. Se as chamadas passarem a rodar de fato como service_role
-- (chave SUPABASE_KEY = service_role), ganha-se folga extra de graça.
--
-- Causas-raiz medidas (EXPLAIN ANALYZE) e como cada uma é endereçada:
--
--   1. `coletas.seller` NÃO tinha índice. "WHERE seller = ..." (count e UPDATE
--      da normalização de sellers) fazia Parallel Seq Scan de 542 MB (~16s).
--        → idx_coletas_seller. Igualdade em seller: ~16s → ~0,1ms.
--
--   2. Backfill de SKU checava linhas órfãs com count="exact"/head=True em
--      "produto = ... AND sku_resolvido IS NULL": o índice idx_coletas_produto
--      achava as ~5k linhas do produto, mas o Bitmap Heap Scan filtrava
--      sku_resolvido linha a linha (~3,5s por nome × ~1.369 nomes). Em HEAD, o
--      timeout volta como 500/corpo vazio ("JSON could not be generated").
--        → idx_coletas_produto_orphan (parcial, WHERE sku_resolvido IS NULL).
--          Existência/contagem de órfãs por produto: ~3,5s → ~3ms.
--
--   3. seed_depara_nomes_novos() fazia DISTINCT + anti-join sobre as 621k linhas
--      de `coletas` a cada run (~25s) só para achar nomes novos — que só
--      aparecem em linhas novas.
--        → Novo parâmetro p_since_id: no hot path (pós-coleta) varre apenas
--          coletas.id > watermark (index scan na PK). ~25s → ~0,2s.
--          p_since_id = NULL (full_scan / --full) mantém a varredura completa.
--
--   4. refresh_filter_options() recalcula 5 array_agg(DISTINCT ...) sobre ~90
--      dias de `coletas` (≈ tabela inteira): dezenas de segundos, não cabe em
--      ~8s de forma alguma no volume atual.
--        → NÃO resolvido no banco aqui. O passo `refresh_cache` da automação
--          passou a TOLERAR o 57014 (utils/admin_automation._step_refresh_cache):
--          é só o cache de filtros do dashboard (tolerante a defasagem), então
--          um timeout vira "pulado" em vez de derrubar o run para `partial`. A
--          MV segue com o snapshot anterior até um refresh conseguir concluir.
--          Correção durável: rodar sob service_role (120s) OU agendar o refresh
--          via pg_cron (job de background, sem teto do PostgREST).
--
-- ============================================================================
-- PASSO 1 — Índices de apoio
--   CREATE INDEX CONCURRENTLY não roda dentro de transação. Rode este arquivo
--   com `psql -f` (autocommit por statement) OU aplique cada statement fora de
--   qualquer BEGIN/COMMIT. (Via Supabase MCP foram aplicados com execute_sql.)
-- ============================================================================

-- Igualdade em seller (normalização de plataformas/sellers; também usado por
-- consultas de buy box / seller).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_coletas_seller
    ON public.coletas (seller);

-- Linhas ainda não resolvidas (sku_resolvido NULL) — índice parcial pequeno que
-- torna a checagem de órfãs por produto (backfill de SKU) instantânea.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_coletas_produto_orphan
    ON public.coletas (produto)
    WHERE sku_resolvido IS NULL;

-- ============================================================================
-- PASSO 2 — Seed incremental por watermark de coletas.id
--   Substitui a versão sem argumentos. A antiga precisa ser removida senão a
--   chamada sem args fica ambígua entre as duas assinaturas.
-- ============================================================================

DROP FUNCTION IF EXISTS public.seed_depara_nomes_novos();

CREATE OR REPLACE FUNCTION public.seed_depara_nomes_novos(p_since_id bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SET search_path TO 'public' AS $function$
DECLARE
    v_coletas int;
    v_rac     int;
BEGIN
    -- Incremental: quando p_since_id é informado, varre só coletas.id > watermark
    -- (index scan na PK) em vez da tabela inteira. Nomes novos só aparecem em
    -- linhas novas, então o hot path (pós-coleta) fica em ~200ms; full_scan passa
    -- p_since_id=NULL e mantém a varredura histórica completa.
    --
    -- Os ramos NULL/incremental são SEPARADOS de propósito: um guard único
    -- "(p_since_id IS NULL OR c.id > p_since_id)" impede o planner de fixar a
    -- condição de índice "c.id > $1" quando o PL/pgSQL troca para o generic
    -- cached plan (após ~5 execuções), fazendo o hot path recair em seq scan.
    -- Com o predicado isolado, o generic plan mantém o Index Scan na PK.
    IF p_since_id IS NULL THEN
        INSERT INTO produtos_depara_nome (nome_coletado, estado, marca_norm, origem)
        SELECT DISTINCT c.produto, 'REVISAR', fn_marca_norm_seed(c.marca), 'auto_seed'
        FROM coletas c
        WHERE NULLIF(BTRIM(c.produto), '') IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM produtos_depara_nome d
              WHERE d.nome_coletado = c.produto
          )
        ON CONFLICT (nome_coletado) DO NOTHING;
    ELSE
        INSERT INTO produtos_depara_nome (nome_coletado, estado, marca_norm, origem)
        SELECT DISTINCT c.produto, 'REVISAR', fn_marca_norm_seed(c.marca), 'auto_seed'
        FROM coletas c
        WHERE c.id > p_since_id
          AND NULLIF(BTRIM(c.produto), '') IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM produtos_depara_nome d
              WHERE d.nome_coletado = c.produto
          )
        ON CONFLICT (nome_coletado) DO NOTHING;
    END IF;
    GET DIAGNOSTICS v_coletas = ROW_COUNT;

    INSERT INTO produtos_depara_nome (nome_coletado, estado, marca_norm, origem)
    SELECT DISTINCT r.produto_sku, 'REVISAR', fn_marca_norm_seed(r.marca_monitorada), 'auto_seed'
    FROM rac_monitoramento r
    WHERE NULLIF(BTRIM(r.produto_sku), '') IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM produtos_depara_nome d
          WHERE d.nome_coletado = r.produto_sku
      )
    ON CONFLICT (nome_coletado) DO NOTHING;
    GET DIAGNOSTICS v_rac = ROW_COUNT;

    RETURN jsonb_build_object(
        'ok', true,
        'novos_coletas', v_coletas,
        'novos_rac', v_rac,
        'since_id', p_since_id
    );
END $function$;

GRANT EXECUTE ON FUNCTION public.seed_depara_nomes_novos(bigint)
    TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- VALIDAÇÕES
--   EXPLAIN ANALYZE SELECT count(*) FROM coletas WHERE seller = '...';
--       -> Index Only Scan using idx_coletas_seller (~0,1ms)
--   EXPLAIN ANALYZE SELECT id FROM coletas
--       WHERE produto = '...' AND sku_resolvido IS NULL LIMIT 1;
--       -> Index Scan using idx_coletas_produto_orphan (~3ms)
--   SELECT seed_depara_nomes_novos(<max_id_anterior>);   -- incremental, ~200ms
--   SELECT seed_depara_nomes_novos();                    -- full scan
-- ============================================================================
