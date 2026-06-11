-- Migration 002: índice composto (sku, collection_date) em pricetrack_daily
-- Roadmap: docs/PRICETRACK_INSIGHTS.md §3 item 1.
--
-- Motivação: as análises de preço×buy box (§2.2) e posição×preço (§2.5)
-- filtram SKU **dentro de uma janela de datas**. O índice simples idx_ptd_sku
-- resolve só o predicado de sku e deixa o filtro de data para um scan das
-- linhas retornadas; o composto atende os dois predicados no próprio índice.
--
-- Aplicação: Supabase SQL Editor (ou psql). Em tabela grande sob escrita
-- ativa, prefira a variante CONCURRENTLY (fora de transação):
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ptd_sku_date
--       ON pricetrack_daily (sku, collection_date);

CREATE INDEX IF NOT EXISTS idx_ptd_sku_date
    ON pricetrack_daily (sku, collection_date);

-- O índice simples em sku fica redundante: o composto cobre qualquer
-- consulta que filtre apenas por sku (coluna líder do b-tree).
DROP INDEX IF EXISTS idx_ptd_sku;
