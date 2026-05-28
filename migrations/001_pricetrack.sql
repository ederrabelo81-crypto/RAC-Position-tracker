-- Migration 001: tabelas PriceTrack
-- Objetivo: receber import diário do PriceTrack como fonte de verdade
-- de preços/share, mantendo idempotência em reimports.

CREATE TABLE IF NOT EXISTS pricetrack_daily (
    id                 BIGSERIAL PRIMARY KEY,
    collection_date    DATE NOT NULL,
    brand              TEXT NOT NULL,
    sku                TEXT NOT NULL,
    title              TEXT NOT NULL,
    marketplace        TEXT NOT NULL,
    seller             TEXT NOT NULL,
    seller_canonical   TEXT NOT NULL,
    min_price          NUMERIC(10,2),
    avg_price          NUMERIC(10,2),
    mode_price         NUMERIC(10,2),
    max_price          NUMERIC(10,2),
    spread_pct         NUMERIC(7,2) GENERATED ALWAYS AS (
        CASE WHEN min_price IS NOT NULL AND min_price > 0 AND max_price IS NOT NULL
             THEN ROUND(((max_price - min_price) / min_price) * 100, 2)
             ELSE NULL END
    ) STORED,
    is_midea_group     BOOLEAN GENERATED ALWAYS AS (
        brand IN ('MIDEA', 'SPRINGER MIDEA', 'SPRINGER CARRIER MIDEA', 'CARRIER')
    ) STORED,
    imported_at        TIMESTAMPTZ DEFAULT NOW(),
    source_file        TEXT,
    CONSTRAINT pricetrack_daily_unique UNIQUE (collection_date, brand, sku, marketplace, seller)
);

CREATE INDEX IF NOT EXISTS idx_ptd_date_brand        ON pricetrack_daily(collection_date, brand);
CREATE INDEX IF NOT EXISTS idx_ptd_sku               ON pricetrack_daily(sku);
CREATE INDEX IF NOT EXISTS idx_ptd_marketplace       ON pricetrack_daily(marketplace);
CREATE INDEX IF NOT EXISTS idx_ptd_canonical_seller  ON pricetrack_daily(seller_canonical);
CREATE INDEX IF NOT EXISTS idx_ptd_midea_date        ON pricetrack_daily(collection_date) WHERE is_midea_group = TRUE;


CREATE TABLE IF NOT EXISTS pricetrack_import_log (
    id              BIGSERIAL PRIMARY KEY,
    source_file     TEXT NOT NULL,
    import_started  TIMESTAMPTZ DEFAULT NOW(),
    import_finished TIMESTAMPTZ,
    rows_total      INT,
    rows_inserted   INT,
    rows_updated    INT,
    rows_rejected   INT,
    rejection_log   JSONB,
    status          TEXT CHECK (status IN ('SUCCESS','PARTIAL','FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_ptil_source_file ON pricetrack_import_log(source_file);
CREATE INDEX IF NOT EXISTS idx_ptil_status      ON pricetrack_import_log(status, import_finished DESC);
