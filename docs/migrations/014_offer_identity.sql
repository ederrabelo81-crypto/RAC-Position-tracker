-- Migração 014 — Identidade de oferta na tabela `coletas` (Fase 1 da auditoria).
--
-- Contexto: `reports/AUDITORIA_COLETA_2026-08.md` §2 mostrou que todo
-- identificador de marketplace já era extraído pelos coletores, usado para
-- montar a URL e então DESCARTADO — `scrapers/amazon.py` chegava a fazer
-- `record.pop("_asin")`. Sem id de oferta não existe série histórica: não dá
-- para separar "o preço desta oferta mudou" de "entrou outra oferta na
-- vitrine", e os indicadores de same-offer price change, offer churn e
-- seller churn são impossíveis de calcular.
--
-- Colunas adicionadas:
--   * marketplace_product_id — id de PRODUTO do marketplace (ASIN, MLB de
--                              catálogo, id numérico da CB, id do Magalu…)
--   * marketplace_offer_id   — id da OFERTA individual, preenchido SOMENTE
--                              quando o marketplace expõe um de verdade
--                              (anúncio MLB-…, par shopid_itemid da Shopee).
--                              Nunca sintetizado.
--   * seller_id              — id do seller (idLojista da CB, seller_id do
--                              Magalu, shopid da Shopee, sellerId da Algolia)
--   * canonical_url          — URL normalizada (tracking removido) para que a
--                              mesma oferta produza sempre a mesma string
--   * offer_key              — chave DERIVADA e VERSIONADA (prefixo `v1|`),
--                              sempre preenchida; é o fallback explícito para
--                              quando não há offer id nativo
--
-- Todas nullable e aditivas — seguras em produção sem downtime. Nenhuma
-- coluna existente é alterada ou removida; o histórico anterior a esta
-- migração simplesmente fica com os campos vazios.
--
-- Aplicação:
--   psql "$SUPABASE_DB_URL" -f docs/migrations/014_offer_identity.sql
-- ou via Supabase SQL Editor / MCP apply_migration.

ALTER TABLE coletas ADD COLUMN IF NOT EXISTS marketplace_product_id TEXT;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS marketplace_offer_id   TEXT;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS seller_id              TEXT;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS canonical_url          TEXT;
ALTER TABLE coletas ADD COLUMN IF NOT EXISTS offer_key              TEXT;

-- Série histórica da oferta: (plataforma, offer_key) ao longo do tempo.
-- É a consulta que a Fase 6 vai rodar para same-offer price change.
CREATE INDEX IF NOT EXISTS idx_coletas_offer_key_data
    ON coletas (plataforma, offer_key, data);

-- Deduplicação por oferta dentro do turno (camada `normalized_offers`).
CREATE INDEX IF NOT EXISTS idx_coletas_offer_turno
    ON coletas (data, turno, plataforma, offer_key);

-- Análises por seller: churn de seller e share de buy box por lojista.
CREATE INDEX IF NOT EXISTS idx_coletas_seller_id
    ON coletas (plataforma, seller_id, data)
    WHERE seller_id IS NOT NULL;

COMMENT ON COLUMN coletas.marketplace_offer_id IS
    'Id de oferta nativo do marketplace. NULL quando a plataforma nao expoe um; '
    'nunca sintetizado. Use offer_key para uma chave sempre presente.';
COMMENT ON COLUMN coletas.offer_key IS
    'Chave derivada e versionada (v1|<plataforma>|<escopo>:<valor>). A versao '
    'sobe se a regra de derivacao mudar, para que series de versoes diferentes '
    'nao sejam comparadas entre si.';
