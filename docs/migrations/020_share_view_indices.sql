-- Migração 020 — Índices para a v_seller_buybox_share não estourar o timeout.
--
-- Sintoma (produção, Out/2026): o seller_app (sellers2app.streamlit.app)
-- mostrava "Falha ao carregar o mercado: {'message': 'canceling statement due
-- to statement timeout', 'code': '57014', ...}" ao entrar e ao mexer no slider
-- de dias. É o PostgREST do Supabase cortando a consulta: o painel lê com a
-- chave `anon`, cujo `statement_timeout` é 3s (migração 007 só elevou o do
-- service_role para 120s).
--
-- Causa raiz (EXPLAIN ANALYZE da consulta do painel, janela de 14 dias):
--   • A CTE `universo` faz `count(DISTINCT marketplace_product_id)` por
--     (data, plataforma) SEM filtro de data. O `data >= X` que o painel manda
--     não chega nela: é um predicado de RANGE, e o planner não o propaga pela
--     equi-junção `USING (data, plataforma)` como faria com igualdade. Então
--     ela reprocessa TODO o histórico a cada request — Seq Scan na tabela
--     inteira + Sort em disco (`external merge`), ~3,1s só nela.
--   • A CTE `detidos` já restringia por data (via pkey), mas pagava um
--     Incremental Sort por não ter índice na ordem do próprio GROUP BY.
--   • Total medido: ~4,2s > 3s → 57014 intermitente, piorando conforme a base
--     cresce (a `universo` varre cada vez mais linhas).
--
-- Correção: dois índices parciais que cobrem exatamente o WHERE de cada CTE e
-- já vêm na ordem do GROUP BY + a coluna do DISTINCT. Com eles as duas CTEs
-- viram Index Only Scan (Heap Fetches: 0), sem Seq Scan e sem sort em disco.
-- Medido depois: 14 dias 4189ms → 37ms; 60 dias (janela máxima) → 69ms. Nada
-- muda na view nem no app — é só o caminho de acesso.
--
-- `idx_sod_share_universo` — denominador: produtos com buy box OBSERVADA
-- (detentor_buybox IS NOT NULL) por (data, plataforma). A coluna
-- marketplace_product_id entra no índice para o DISTINCT sair da própria ordem.
--
-- `idx_sod_share_detidos` — numerador: produtos DETIDOS (detentor_buybox =
-- true) por (data, plataforma, seller_canonical). `virou_no_turno` vai em
-- INCLUDE para o `count(*) FILTER (WHERE virou_no_turno)` da view ser
-- resolvido sem ir ao heap (mantém o Index Only Scan).
--
-- NOTA: as duas são partial indexes com predicados DIFERENTES, de propósito —
-- `universo` quer `detentor_buybox IS NOT NULL` (TRUE ou FALSE: a buy box foi
-- observada), `detidos` quer `detentor_buybox` (só TRUE: este seller detém).
--
-- APLICAÇÃO: CREATE INDEX CONCURRENTLY não roda dentro de transação (mesma
-- ressalva da migração 007). Rode cada statement sozinho, fora de BEGIN/COMMIT;
-- não envolva este arquivo num bloco transacional. CONCURRENTLY não trava
-- escrita na tabela, então é seguro aplicar com a coleta rodando. Já aplicado
-- na produção (projeto Supabase RAC) em 2026-10-02; estes comandos são o
-- registro versionado e são idempotentes (IF NOT EXISTS).

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_sod_share_universo
    ON seller_offer_daily (data, plataforma, marketplace_product_id)
    WHERE superficie = 'marketplace' AND NOT identidade_suspeita
      AND marketplace_product_id IS NOT NULL
      AND detentor_buybox IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_sod_share_detidos
    ON seller_offer_daily (data, plataforma, seller_canonical, marketplace_product_id)
    INCLUDE (virou_no_turno)
    WHERE superficie = 'marketplace' AND NOT identidade_suspeita
      AND marketplace_product_id IS NOT NULL
      AND detentor_buybox;
