-- scripts/retention_cleanup.sql — Política de retenção do Supabase (re-executável)
--
-- Contexto (2026-07-14): a org Supabase "Mydea" (projeto RAC) recebeu aviso de
-- uso acima da cota — banco em 3,09 GB. Diagnóstico do espaço:
--   pricetrack_daily  2344 MB (76%)  4,22M linhas  ← alvo principal
--   coletas            696 MB          643K linhas
--   rac_monitoramento   33 MB (legado, ainda lido/escrito pelo app → manter)
--   resto              < 6 MB (catálogo, de-para, logs → manter)
--
-- Estrutura que orienta a política:
--   1. pricetrack_daily grava 3 linhas por (sku,seller,dia): 'Diário' (preço
--      definitivo do dia fechado) + 'Manhã' + 'Tarde' (intra-dia). O intra-dia
--      só alimenta os turnos RECENTES do dashboard; apagar intra-dia ANTIGO não
--      perde NENHUMA série de preço diário (o 'Diário' é a fonte de verdade).
--   2. coletas é contexto (posição, buy box, patrocinado) — janela recente basta.
--
-- Política "Equilibrada" (escolhida pelo mantenedor em 2026-07-14):
--   • pricetrack_daily: manter TODO o histórico 'Diário' (desde jan/2026) +
--     apenas os últimos 30 dias de intra-dia (Manhã/Tarde).
--   • coletas: manter os últimos 90 dias.
--   • Nenhuma outra tabela é tocada.
--
-- Resultado do 1º run (2026-07-14): 2.061.970 linhas removidas
--   (pricetrack intra-dia >30d: 2.040.023 | coletas >90d: 21.947).
--   Após VACUUM FULL: pricetrack 2344→885 MB, coletas 696→505 MB,
--   banco 3093→1443 MB (−53%). Zero quebra (sem FKs de entrada; a MV
--   mv_filter_options_90d se recompõe no refresh).
--
-- IMPORTANTE — recuperar espaço de fato:
--   DELETE sozinho NÃO reduz o tamanho reportado (pg_database_size): o Postgres
--   mantém as páginas como "dead tuples" reutilizáveis. Só VACUUM FULL devolve
--   disco ao SO. VACUUM FULL NÃO roda dentro de transação e pega ACCESS
--   EXCLUSIVE lock — execute os comandos VACUUM FULL do rodapé um a um, fora de
--   qualquer BEGIN/COMMIT, numa janela de manutenção. No pooler do Supabase MCP
--   o comando pode "estourar" o timeout do cliente (60s) mas continua rodando
--   no servidor até concluir — confira com pg_stat_activity.
--
-- Cadência sugerida: mensal (ou quando o uso voltar a subir). O uso de
-- CURRENT_DATE - N torna este script re-executável sem edição.

-- ── 1. Poda do intra-dia antigo do pricetrack_daily (mantém todo o 'Diário') ──
SET statement_timeout = '1800s';

WITH del AS (
    DELETE FROM pricetrack_daily
    WHERE turno <> 'Diário'
      AND collection_date < CURRENT_DATE - 30
    RETURNING 1
)
SELECT COUNT(*) AS pricetrack_intraday_deleted FROM del;

-- ── 2. Poda das coletas antigas (janela de 90 dias) ──
WITH del AS (
    DELETE FROM coletas
    WHERE data < CURRENT_DATE - 90
    RETURNING 1
)
SELECT COUNT(*) AS coletas_deleted FROM del;

-- ── 3. Recuperar disco — rodar SEPARADAMENTE, fora de transação ──
-- VACUUM (FULL, ANALYZE) pricetrack_daily;
-- VACUUM (FULL, ANALYZE) coletas;

-- ── 4. Conferência ──
-- SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size,
--        pg_size_pretty(pg_total_relation_size('pricetrack_daily')) AS pricetrack,
--        pg_size_pretty(pg_total_relation_size('coletas')) AS coletas;
