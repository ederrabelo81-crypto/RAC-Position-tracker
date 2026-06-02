-- docs/migrations/004_produto_normalizado.sql
--
-- Aplicado em 02/06/2026 via Supabase MCP. Idempotente.
--
-- Formato canônico v2 (SKU-anchored, UPPERCASE) na NOVA coluna
-- `coletas.produto_normalizado` — a coluna `produto` (legado) fica intacta.
--
--   AR CONDICIONADO SPLIT 12000 BTU FRIO ECOMASTER - INVERTER - MIDEA - 220V - 42EZVCA12M5
--
-- Estratégia de preenchimento:
--   Tier A — linhas com sku_resolvido → string completa montada do catálogo
--            (linha/voltagem/SKU autoritativos). ~162k linhas.
--   Tier B — MAPEADO sem SKU único → descritivo a partir de familia_resolvida
--            (marca + BTU + ciclo + linha se houver). Voltagem/SKU OMITIDOS.
--            ~65k linhas.
--   (Edge) ~776 linhas com familia fora do padrão BTU-CICLO ficam NULL aqui e
--          são cobertas pelo insert-time Python / scripts/backfill_produto_normalizado.py.
--
-- Marcas fora do catálogo (Daikin, Consul, etc.) e REVISAR: produto_normalizado
-- é preenchido pelo Python (normalize_product_name_v2) no insert e no backfill
-- script — não por este SQL (que é ancorado no catálogo RAC High Wall).

-- ============================================================================
-- PASSO 1 — Coluna nova (não destrutiva)
-- ============================================================================
ALTER TABLE public.coletas ADD COLUMN IF NOT EXISTS produto_normalizado text;


-- ============================================================================
-- PASSO 2 — Função: monta o nome v2 a partir do catálogo (sku → string)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.fn_produto_normalizado_catalogo(p_sku text)
RETURNS text LANGUAGE sql STABLE AS $$
  SELECT
    'AR CONDICIONADO SPLIT ' || pc.capacidade_btu || ' BTU ' || COALESCE(pc.ciclo, 'FRIO')
    -- linha = familia_linha sem prefixo <MARCA>- e sem sufixo -<BTU>-<CICLO>
    || COALESCE(NULLIF(' ' || trim(regexp_replace(
          regexp_replace(
            regexp_replace(pc.familia_linha, '^' || pc.marca || '-', ''),
            '-' || pc.capacidade_btu || '-(F|QF|Q)$', ''),
          '-', ' ', 'g')), ' '), '')
    -- catálogo RAC High Wall é 100% inverter (verificado: 0 linhas on/off)
    || ' - INVERTER - ' || pc.marca
    || CASE WHEN pc.voltagem IS NOT NULL THEN ' - ' || pc.voltagem ELSE '' END
    || ' - ' || pc.sku
  FROM public.produtos_catalogo pc
  WHERE pc.sku = p_sku
  LIMIT 1;
$$;


-- ============================================================================
-- PASSO 3 — Função: monta o nome v2 descritivo a partir de familia_resolvida
--           (Tier B — MAPEADO sem SKU). Voltagem/SKU omitidos.
-- ============================================================================
CREATE OR REPLACE FUNCTION public.fn_produto_normalizado_familia(p_familia text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_familia ~ '-(\d{4,5})-(F|QF|Q)$' THEN
      'AR CONDICIONADO SPLIT '
      || (regexp_match(p_familia, '-(\d{4,5})-(F|QF|Q)$'))[1] || ' BTU '
      || CASE (regexp_match(p_familia, '-(F|QF|Q)$'))[1]
           WHEN 'F'  THEN 'FRIO'
           WHEN 'QF' THEN 'QUENTE/FRIO'
           WHEN 'Q'  THEN 'QUENTE'
         END
      || COALESCE(' ' || replace(
            (regexp_match(p_familia, '^[A-Z]+-(.*)-\d{4,5}-(F|QF|Q)$'))[1], '-', ' '), '')
      || ' - INVERTER - ' || split_part(p_familia, '-', 1)
    ELSE NULL
  END;
$$;


-- ============================================================================
-- PASSO 4 — Backfill Tier A (sku_resolvido → catálogo)
-- ============================================================================
UPDATE public.coletas c
SET produto_normalizado = public.fn_produto_normalizado_catalogo(c.sku_resolvido)
WHERE c.sku_resolvido IS NOT NULL
  AND (c.produto_normalizado IS NULL
       OR c.produto_normalizado NOT LIKE '%' || c.sku_resolvido);


-- ============================================================================
-- PASSO 5 — Backfill Tier B (MAPEADO sem SKU → descritivo da família)
-- ============================================================================
UPDATE public.coletas c
SET produto_normalizado = public.fn_produto_normalizado_familia(c.familia_resolvida)
WHERE c.estado_match = 'MAPEADO'
  AND c.sku_resolvido IS NULL
  AND c.familia_resolvida ~ '-(\d{4,5})-(F|QF|Q)$'
  AND (c.produto_normalizado IS NULL OR c.produto_normalizado = '');


-- ============================================================================
-- PASSO 6 — Resolução DIÁRIA automatizada (task 4: histórico atualizado)
--   1) resolve estado/familia/sku de linhas novas via de-para (idempotente)
--   2) Tier A nas que ganharam SKU
--   3) Tier B nas MAPEADO sem SKU
-- Chamada via scripts/resolver_diario.py (rpc) após cada coleta.
-- ============================================================================
CREATE OR REPLACE FUNCTION public.resolver_coletas_pendentes()
RETURNS TABLE(resolvidas bigint, tier_a bigint, tier_b bigint)
LANGUAGE plpgsql AS $$
DECLARE v_res bigint; v_a bigint; v_b bigint;
BEGIN
  -- 1) de-para → coletas (só linhas ainda sem estado)
  UPDATE public.coletas c
  SET familia_resolvida = d.familia,
      sku_resolvido     = d.sku,
      estado_match      = d.estado
  FROM public.produtos_depara_nome d
  WHERE c.produto = d.nome_coletado
    AND c.estado_match IS NULL;
  GET DIAGNOSTICS v_res = ROW_COUNT;

  -- 2) Tier A — catálogo
  UPDATE public.coletas c
  SET produto_normalizado = public.fn_produto_normalizado_catalogo(c.sku_resolvido)
  WHERE c.sku_resolvido IS NOT NULL
    AND (c.produto_normalizado IS NULL
         OR c.produto_normalizado NOT LIKE '%' || c.sku_resolvido);
  GET DIAGNOSTICS v_a = ROW_COUNT;

  -- 3) Tier B — descritivo da família
  UPDATE public.coletas c
  SET produto_normalizado = public.fn_produto_normalizado_familia(c.familia_resolvida)
  WHERE c.estado_match = 'MAPEADO'
    AND c.sku_resolvido IS NULL
    AND c.familia_resolvida ~ '-(\d{4,5})-(F|QF|Q)$'
    AND (c.produto_normalizado IS NULL OR c.produto_normalizado = '');
  GET DIAGNOSTICS v_b = ROW_COUNT;

  RETURN QUERY SELECT v_res, v_a, v_b;
END $$;


-- ============================================================================
-- VALIDAÇÕES
--   SELECT count(*) FROM coletas WHERE produto_normalizado IS NOT NULL;
--   SELECT * FROM public.resolver_coletas_pendentes();
-- ============================================================================
