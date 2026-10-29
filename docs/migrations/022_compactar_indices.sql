-- =============================================================================
-- 022_compactar_indices.sql — compactação de índice das tabelas podadas
-- =============================================================================
--
-- POR QUÊ (29/10/2026)
-- --------------------
-- O banco chegou a 561 MB contra os 500 MB do free tier com só ~330 MB de
-- dado. O resto era índice inchado: `pricetrack_daily` guardava 2 dias (20 MB
-- de heap) sob 111 MB de índice; `coletas`, 46 MB de heap sob 69 MB. São as
-- tabelas que a poda noturna (`history_cli.py tier`) esvazia por DELETE e a
-- coleta reenche todo dia — e nem `DELETE` nem o autovacuum devolvem página de
-- B-tree meio vazia. Um `REINDEX TABLE CONCURRENTLY` manual nas três levou o
-- banco de 561 para 336 MB sem apagar uma linha.
--
-- Esta função deixa o `tier` fazer isso sozinho, toda noite, pela MESMA
-- credencial que ele já usa (service_role via PostgREST, ou `utils/db.py`
-- contra um Postgres direto) — sem DSN extra no PC coletor.
--
-- DESENHO
-- -------
-- * Uma tabela por chamada: cada RPC é uma transação, então o lock do
--   REINDEX (bloqueia escrita e leitura que usa o índice) dura só o rebuild
--   daquela tabela — segundos —, não a soma de todas. `REINDEX CONCURRENTLY`
--   não roda dentro de função (exige estar fora de bloco de transação).
-- * Só reindexa quando o índice passou de `p_razao` × o heap (padrão 1,25).
--   Medido em 29/10/2026 logo após o REINDEX: índice/heap = 0,6
--   (pricetrack_daily), 0,4 (coletas), 0,8 (seller_offer_daily). Noite comum =
--   uma consulta de tamanho, sem lock.
-- * Lista branca fixa: SECURITY DEFINER + nome de tabela vindo de fora só é
--   aceitável se o nome não puder ser outro.
-- * `lock_timeout` local de 30 s: o `authenticator` do Supabase define 8 s,
--   pouco se um leitor do dashboard segurar a tabela. Estourou → erro, o
--   `tier` registra e tenta na noite seguinte.
--
-- Aplicar: psql "$SUPABASE_DSN" -f docs/migrations/022_compactar_indices.sql
-- (ou SQL Editor). Idempotente.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.compactar_indices(
    p_tabela text,
    p_razao  numeric DEFAULT 1.25
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_heap   bigint;
    v_antes  bigint;
    v_depois bigint;
    v_reidx  boolean := false;
BEGIN
    IF p_tabela NOT IN ('coletas', 'pricetrack_daily',
                        'seller_offer_daily', 'seller_coverage_daily') THEN
        RAISE EXCEPTION 'compactar_indices: tabela fora da lista branca: %', p_tabela;
    END IF;

    v_heap  := pg_relation_size(format('public.%I', p_tabela)::regclass);
    v_antes := pg_indexes_size(format('public.%I', p_tabela)::regclass);

    IF v_antes > v_heap * p_razao THEN
        PERFORM set_config('lock_timeout', '30s', true);
        EXECUTE format('REINDEX TABLE public.%I', p_tabela);
        v_reidx := true;
    END IF;

    v_depois := pg_indexes_size(format('public.%I', p_tabela)::regclass);

    RETURN jsonb_build_object(
        'tabela',          p_tabela,
        'reindexada',      v_reidx,
        'heap',            pg_size_pretty(v_heap),
        'indices_antes',   pg_size_pretty(v_antes),
        'indices_depois',  pg_size_pretty(v_depois),
        'banco',           pg_size_pretty(pg_database_size(current_database()))
    );
END;
$$;

COMMENT ON FUNCTION public.compactar_indices(text, numeric) IS
    'Reconstrói os índices de uma tabela podada pelo tier quando passam de '
    'p_razao × o heap. Chamada por scripts/history_cli.py tier --confirm '
    '(estágio D). Ver docs/migrations/022_compactar_indices.sql.';

REVOKE ALL ON FUNCTION public.compactar_indices(text, numeric) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.compactar_indices(text, numeric) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.compactar_indices(text, numeric) TO service_role;
