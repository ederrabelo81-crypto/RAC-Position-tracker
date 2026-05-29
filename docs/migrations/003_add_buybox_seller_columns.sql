-- Migração 003 — Adiciona colunas de insight de buy box / seller à tabela `coletas`.
--
-- Contexto (Mai/2026): o foco da coleta deixou de ser preço e passou a ser
-- competição por buy box e inteligência de sellers. Os scrapers agora extraem,
-- quando a plataforma expõe:
--   * patrocinado       — se o anúncio é patrocinado (Sim/Não)
--   * buy_box_seller    — seller que vence a oferta principal (buy box) do produto
--   * qtd_sellers       — nº de sellers/ofertas competindo na mesma listagem
--   * tipo_seller       — classificação: 1P / 3P / Loja Oficial / Shopee Mall / Preferred+
--   * reputacao_seller  — nota/nível de reputação do seller (texto livre)
--
-- Todas as colunas são nullable e aditivas — seguras de aplicar em produção
-- sem downtime. Preço (coluna `preco`) é mantido como campo secundário.
--
-- Aplicação:
--   psql "$SUPABASE_DB_URL" -f docs/migrations/003_add_buybox_seller_columns.sql
-- ou via Supabase SQL Editor / MCP apply_migration.

ALTER TABLE coletas ADD COLUMN IF NOT EXISTS patrocinado      BOOLEAN;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS buy_box_seller   TEXT;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS qtd_sellers      INTEGER;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS tipo_seller      TEXT;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS reputacao_seller TEXT;

-- Índice para análises de share of buy box por seller/plataforma/data.
CREATE INDEX IF NOT EXISTS idx_coletas_buybox_seller
    ON coletas (plataforma, data, buy_box_seller);
