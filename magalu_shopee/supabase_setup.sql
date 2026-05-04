-- Tabela para dados coletados pelo scraper Node.js (Magalu + Shopee)
CREATE TABLE IF NOT EXISTS rac_products_magalu_shopee (
  id              BIGSERIAL PRIMARY KEY,
  marketplace     TEXT NOT NULL CHECK (marketplace IN ('Magalu', 'Shopee')),
  product_id      TEXT NOT NULL,
  sku             TEXT,
  search_query    TEXT NOT NULL,
  page_number     INTEGER NOT NULL,
  position        INTEGER NOT NULL,
  product_name    TEXT NOT NULL,
  brand           TEXT,
  product_type    TEXT,
  capacity_btu    INTEGER,
  current_price   NUMERIC(10, 2),
  original_price  NUMERIC(10, 2),
  discount_percentage INTEGER,
  rating          NUMERIC(3, 2),
  review_count    INTEGER DEFAULT 0,
  stock_status    TEXT DEFAULT 'Em estoque',
  seller          TEXT,
  is_official     BOOLEAN DEFAULT FALSE,
  product_url     TEXT,
  image_url       TEXT,
  collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS: desabilita para uso interno (scraper usa service_role ou anon com política)
ALTER TABLE rac_products_magalu_shopee DISABLE ROW LEVEL SECURITY;

-- Se preferir manter RLS ativo, use estas políticas em vez de DISABLE:
-- CREATE POLICY "insert_all" ON rac_products_magalu_shopee FOR INSERT WITH CHECK (true);
-- CREATE POLICY "select_all" ON rac_products_magalu_shopee FOR SELECT USING (true);

-- Índices para queries analíticas comuns
CREATE INDEX IF NOT EXISTS idx_rac_ms_marketplace     ON rac_products_magalu_shopee (marketplace);
CREATE INDEX IF NOT EXISTS idx_rac_ms_brand           ON rac_products_magalu_shopee (brand);
CREATE INDEX IF NOT EXISTS idx_rac_ms_collected_at    ON rac_products_magalu_shopee (collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_rac_ms_search_query    ON rac_products_magalu_shopee (search_query);
CREATE INDEX IF NOT EXISTS idx_rac_ms_product_id      ON rac_products_magalu_shopee (marketplace, product_id);

-- View: posicionamento médio por marca e marketplace
CREATE OR REPLACE VIEW v_rac_brand_position AS
SELECT
  marketplace,
  brand,
  search_query,
  DATE(collected_at AT TIME ZONE 'America/Sao_Paulo') AS data_coleta,
  COUNT(*) AS total_produtos,
  ROUND(AVG(position), 1) AS posicao_media,
  MIN(position) AS melhor_posicao,
  ROUND(AVG(current_price), 2) AS preco_medio,
  MIN(current_price) AS menor_preco
FROM rac_products_magalu_shopee
WHERE brand IS NOT NULL
GROUP BY marketplace, brand, search_query, DATE(collected_at AT TIME ZONE 'America/Sao_Paulo')
ORDER BY data_coleta DESC, marketplace, brand;
