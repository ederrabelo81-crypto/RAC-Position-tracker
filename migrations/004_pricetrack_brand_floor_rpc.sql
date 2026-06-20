-- Migration 004: RPC do piso diário por marca (sparkline Daily Vision)
--
-- Problema: o sparkline 7d do Daily Price Vision puxava linhas cruas de
-- `pricetrack_daily` e agregava no cliente com um único `.limit(20000)`.
-- O PostgREST capa a resposta em 1000 linhas (max_rows), e uma só marca
-- popular (Midea: 2,6k–8,6k linhas/dia) já estoura esse teto — então o
-- recorte de 1000 linhas cobria só um punhado de (marca, dia) e o
-- sparkline saía esparso/vazio (ex.: lista [1648, NaN×6]).
--
-- Solução: empurrar o GROUP BY pro Postgres. Esta função devolve
-- MIN(min_price) por (collection_date, brand) já filtrado, retornando
-- ~marcas×7 ≈ 100 linhas — cabe folgado numa única resposta, sem
-- paginação e sem risco de statement_timeout.
--
-- Filtros opcionais (NULL = sem filtro), espelhando a query principal:
--   p_brands : lista de valores RAW de `brand` (caller expande aliases)
--   p_btus   : capacidades; casa via title ILIKE '%btu%' e '%b.tu%'
--   p_skus   : SKUs canônicos do catálogo

CREATE OR REPLACE FUNCTION pricetrack_brand_daily_floor(
    p_start  date,
    p_end    date,
    p_brands text[] DEFAULT NULL,
    p_btus   text[] DEFAULT NULL,
    p_skus   text[] DEFAULT NULL
)
RETURNS TABLE (
    collection_date date,
    brand           text,
    floor_price     numeric
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        ptd.collection_date,
        ptd.brand,
        MIN(ptd.min_price) AS floor_price
    FROM pricetrack_daily ptd
    WHERE ptd.collection_date BETWEEN p_start AND p_end
      AND ptd.min_price IS NOT NULL
      AND (p_brands IS NULL OR ptd.brand = ANY (p_brands))
      AND (p_skus   IS NULL OR ptd.sku   = ANY (p_skus))
      AND (
            p_btus IS NULL
            OR EXISTS (
                SELECT 1
                FROM unnest(p_btus) AS b
                WHERE ptd.title ILIKE '%' || b || '%'
                   -- 12000 → 12.000 (formato pontuado comum nos títulos)
                   OR ptd.title ILIKE '%'
                      || regexp_replace(b, '(\d)(\d{3})$', '\1.\2')
                      || '%'
            )
          )
    GROUP BY ptd.collection_date, ptd.brand;
$$;

-- PostgREST expõe a função via /rpc/pricetrack_brand_daily_floor.
COMMENT ON FUNCTION pricetrack_brand_daily_floor(date, date, text[], text[], text[])
    IS 'Piso diário (MIN min_price) por marca p/ o sparkline 7d do Daily Vision. '
       'Agrega server-side pra fugir do cap de 1000 linhas do PostgREST.';
