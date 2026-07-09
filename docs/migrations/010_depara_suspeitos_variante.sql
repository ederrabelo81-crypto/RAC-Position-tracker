-- docs/migrations/010_depara_suspeitos_variante.sql
--
-- Detector de de-para de VARIANTE ERRADA: SKUs cujo piso diário das coletas
-- diverge de forma persistente (|Δ| > p_delta_pct em ≥ p_min_dias dias) do
-- piso do PriceTrack para o mesmo (sku, dia).
--
-- Motivação (validação de 09/07/2026, janela 01/06–09/07): no pareamento
-- global PT × Coletas (11.454 pares sku×plataforma×dia), 16% dos pares
-- divergem mais de 25% — e essa cauda concentra-se em SKUs específicos com
-- desvio de SINAL CONSTANTE (ex.: Gree GWC30ATE +60%, Samsung WindFree 18k
-- −32%, Philco PAC12000IQFM15 −31%). Δ persistente dessa magnitude não é
-- reprecificação: é anúncio de outra variante (capacidade, voltagem, kit vs
-- unidade, só-evaporadora) resolvendo para o mesmo código em uma das fontes.
--
-- Consumidor: página 🧬 Família & SKU (painel "⚠️ Suspeitos de variante
-- errada"), via client.rpc("depara_suspeitos_variante", {...}).
-- Função read-only (STABLE); roda no papel do caller (dashboard usa a
-- service key, statement_timeout 120s — migration 007).

CREATE OR REPLACE FUNCTION public.depara_suspeitos_variante(
    p_days      integer DEFAULT 30,
    p_delta_pct numeric DEFAULT 25,
    p_min_dias  integer DEFAULT 5
)
RETURNS TABLE (
    sku                  text,
    produto_catalogo     text,
    dias_pareados        bigint,
    dias_extremos        bigint,
    delta_mediano_pct    numeric,
    piso_pt_mediano      numeric,
    piso_coletas_mediano numeric
)
LANGUAGE sql STABLE SET search_path TO 'public' AS $$
WITH col AS (
    SELECT c.sku_resolvido AS sku, c.data AS d, min(c.preco) AS piso
    FROM coletas c
    WHERE c.data >= current_date - p_days
      AND c.sku_resolvido IS NOT NULL
      AND c.preco > 0
    GROUP BY 1, 2
), pt AS (
    SELECT p.sku, p.collection_date AS d, min(p.min_price) AS piso
    FROM pricetrack_daily p
    WHERE p.collection_date >= current_date - p_days
      AND p.turno = 'Diário'
      AND p.sku IN (SELECT DISTINCT col.sku FROM col)
    GROUP BY 1, 2
), pareado AS (
    SELECT col.sku, col.d, pt.piso AS pt_piso, col.piso AS col_piso,
           100 * (col.piso - pt.piso) / pt.piso AS dpct
    FROM col
    JOIN pt ON pt.sku = col.sku AND pt.d = col.d
    WHERE pt.piso > 0
)
SELECT
    pa.sku,
    pc.produto AS produto_catalogo,
    count(*) AS dias_pareados,
    count(*) FILTER (WHERE abs(pa.dpct) > p_delta_pct) AS dias_extremos,
    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY pa.dpct))::numeric, 1)
        AS delta_mediano_pct,
    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY pa.pt_piso))::numeric, 2)
        AS piso_pt_mediano,
    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY pa.col_piso))::numeric, 2)
        AS piso_coletas_mediano
FROM pareado pa
LEFT JOIN produtos_catalogo pc ON pc.sku = pa.sku
GROUP BY pa.sku, pc.produto
HAVING count(*) FILTER (WHERE abs(pa.dpct) > p_delta_pct) >= p_min_dias
ORDER BY count(*) FILTER (WHERE abs(pa.dpct) > p_delta_pct) DESC, pa.sku;
$$;

-- ============================================================================
-- ROLLBACK (reverte 100%):
--   DROP FUNCTION IF EXISTS public.depara_suspeitos_variante(integer, numeric, integer);
-- ============================================================================
