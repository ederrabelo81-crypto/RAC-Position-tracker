-- Migração 001 — Adiciona colunas de URL do produto e evidência de screenshot
-- à tabela `coletas`.
--
-- Contexto: os scrapers passaram a coletar a URL do PDP de cada produto e o
-- caminho/URL pública do screenshot da página de busca. Estas colunas são
-- nullable e aditivas — seguras de aplicar em produção sem downtime.
--
-- Aplicação:
--   psql "$SUPABASE_DB_URL" -f docs/migrations/001_add_url_screenshot_columns.sql
-- ou via Supabase SQL Editor / MCP apply_migration.

ALTER TABLE coletas ADD COLUMN IF NOT EXISTS url_produto        TEXT;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS screenshot_busca   TEXT;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS screenshot_produto TEXT;
