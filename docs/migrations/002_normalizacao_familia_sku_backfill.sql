-- docs/migrations/002_normalizacao_familia_sku_backfill.sql
--
-- Aplicado em 27/05/2026 via Supabase MCP. Idempotente.
--
-- Contexto: após a entrega das PRs #134 / #136 / #137 (normalização nome →
-- família + filtros no dashboard + admin page), descobriu-se que a página
-- admin "🧬 Família & SKU" estava mostrando "De-para vazio" mesmo com 1.925
-- nomes no DB. A causa foi RLS habilitado sem políticas nas 4 tabelas novas
-- — PostgREST devolve 0 linhas nesse caso. Esta migration:
--
--   1. Desabilita RLS nessas 4 tabelas (alinha com `coletas`, que também é
--      RLS off — o acesso já é restringido pela API key `sb_secret_*`).
--   2. Insere no de-para os nomes novos que entraram em coletas/rac_monitoramento
--      depois da última carga.
--   3. Re-roda a classificação automática (NAO_AC, FORA_ESCOPO, MAPEADO
--      genérico `<MARCA>-<BTU>-<CICLO>`).
--   4. Backfill TOTAL em `rac_monitoramento` e backfill incremental em
--      `coletas` (só linhas que ainda não tinham `estado_match`).
--   5. Refresh da materialized view de filtros do dashboard.
--
-- Estado final medido:
--   - coletas: 265.378 linhas, 192.582 MAPEADO (72,6%), 0 sem estado
--   - rac_monitoramento: 31.562 linhas, 22.886 MAPEADO (72,5%), 0 sem estado
--   - produtos_depara_nome: 1.932 nomes (436 MAPEADO, 724 FORA_ESCOPO,
--     182 NAO_AC, 590 REVISAR — fila humana)

-- ============================================================================
-- PASSO 1 — Desabilita RLS nas tabelas de catálogo + monitoramento
-- ============================================================================

ALTER TABLE public.produtos_catalogo     DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.produtos_aliases      DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.produtos_depara_nome  DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.rac_monitoramento     DISABLE ROW LEVEL SECURITY;


-- ============================================================================
-- PASSO 2 — Seed de nomes novos vindos de coletas (ON CONFLICT no-op)
-- ============================================================================

INSERT INTO public.produtos_depara_nome (nome_coletado, estado, marca_norm, origem)
SELECT DISTINCT c.produto, 'REVISAR'::text,
       CASE LOWER(COALESCE(c.marca, ''))
           WHEN 'springer midea' THEN 'MIDEA' WHEN 'midea carrier' THEN 'MIDEA'
           WHEN 'springer' THEN 'MIDEA'      WHEN 'midea' THEN 'MIDEA'
           WHEN 'lg' THEN 'LG'                WHEN 'samsung' THEN 'SAMSUNG'
           WHEN 'electrolux' THEN 'ELECTROLUX' WHEN 'elgin' THEN 'ELGIN'
           WHEN 'philco' THEN 'PHILCO'        WHEN 'gree' THEN 'GREE'
           WHEN 'tcl' THEN 'TCL'              WHEN 'agratto' THEN 'AGRATTO'
           WHEN 'hisense' THEN 'HISENSE'      WHEN 'carrier' THEN 'CARRIER'
           WHEN 'consul' THEN 'CONSUL'        WHEN 'daikin' THEN 'DAIKIN'
           WHEN 'fujitsu' THEN 'FUJITSU'      WHEN 'hitachi' THEN 'HITACHI'
           WHEN 'haier' THEN 'HAIER'          WHEN 'york' THEN 'YORK'
           WHEN 'eos' THEN 'EOS'              WHEN 'hq' THEN 'HQ'
           WHEN 'aiwa' THEN 'AIWA'            WHEN 'vix' THEN 'VIX'
           WHEN 'rheem' THEN 'RHEEM'          WHEN 'kian' THEN 'KIAN'
           WHEN 'britânia' THEN 'BRITANIA'    WHEN 'britania' THEN 'BRITANIA'
           WHEN 'equation' THEN 'EQUATION'    WHEN 'delonghi' THEN 'DELONGHI'
           WHEN 'aufit' THEN 'AUFIT'          WHEN 'komeco' THEN 'KOMECO'
           ELSE NULL
       END, 'seed'
FROM public.coletas c
WHERE c.produto IS NOT NULL
ON CONFLICT (nome_coletado) DO NOTHING;


-- ============================================================================
-- PASSO 3 — Reclassificação automática (idempotente: só onde estado='REVISAR')
-- ============================================================================

-- NAO_AC: peças automotivas, eletrodomésticos, acessórios, climatizadores
UPDATE public.produtos_depara_nome SET estado='NAO_AC', familia=NULL, sku=NULL, revisado_em=now()
WHERE estado='REVISAR' AND (
     nome_coletado ILIKE '%report a violation%'
  OR nome_coletado ~* '\mradiador\M|\mevaporador\M(?!.*split)'
  OR nome_coletado ~* '\m(polia|válvula|valvula|torneira|cooler intel|injetora|garrafa)\M'
  OR nome_coletado ~* '\mshampoo\M|\mcondicionador\M.*(hidratante|capilar|antiqueda)'
  OR nome_coletado ~* '\mcolch[aã]o\M|\mbarraca\M|\minfl[aá]vel\M'
  OR nome_coletado ~* '\mgeladeira\M|\mfrigobar\M|\mfreezer\M|\mair fryer\M|\mfritadeira\M'
  OR nome_coletado ~* '\mumidificador\M|\mclimatizador\M|\mventilador\M|\maromatizador\M'
  OR nome_coletado ~* '\mmini ar condicionado\M|\mcortina de ar\M'
  OR nome_coletado ~* '\ycervejeira\y|\ylavadora\y|\ysecadora\y|\ymicro-?ondas\y'
  OR nome_coletado ~* '\yserpentina\y|\ycontrole\y|\yorganizador\y|\ycarregador\y'
  OR nome_coletado ~* '\yunidade condensadora\y|\yhigienizador\y|\ymanifold\y|\ymangueira\y'
);

-- FORA_ESCOPO: janela, portátil, cassete, multi-split, BTU fora do range RAC
UPDATE public.produtos_depara_nome SET estado='FORA_ESCOPO', familia=NULL, sku=NULL, revisado_em=now()
WHERE estado='REVISAR' AND (
     nome_coletado ~* '\m(janela|janeleiro|window)\M'
  OR nome_coletado ~* '\mport[aá]til\M|\mcassete\M|\mcassette\M|piso[ \-]teto'
  OR nome_coletado ~* 'multi[ \-]?split|multisplit|bi[\s-]?split'
  OR nome_coletado ~* '\y(36|32|34|48|57|60)\.?000\s*btu'
  OR nome_coletado ~* '\ysplit[aã]o\y|\ytrif[aá]sico\y'
);

-- FORA_ESCOPO por marca fora do catálogo
UPDATE public.produtos_depara_nome SET estado='FORA_ESCOPO', familia=NULL, sku=NULL, revisado_em=now()
WHERE estado='REVISAR' AND marca_norm IN (
   'DAIKIN','FUJITSU','HITACHI','HAIER','YORK','EOS','HQ','AIWA','CONSUL',
   'VIX','RHEEM','KIAN','BRITANIA','EQUATION','DELONGHI','CARRIER','AUFIT','KOMECO'
);

-- MAPEADO genérico (marca em catálogo + BTU extraível + ciclo extraível)
WITH base AS (
    SELECT id, nome_coletado, marca_norm,
        (REGEXP_MATCHES(LOWER(nome_coletado),
            '\m(9\.000|9000|9k|12\.000|12000|12k|18\.000|18000|18k|22\.000|22000|22k|24\.000|24000|24k|30\.000|30000|30k)\M'))[1] AS btu_raw,
        CASE
            WHEN LOWER(nome_coletado) ~ 'quente[\s/]*(e\s*)?frio|quente\s*/\s*frio|\yq\s*/\s*f\y|\yqf\y' THEN 'QF'
            WHEN LOWER(nome_coletado) ~ '\yfrio\y'   THEN 'F'
            WHEN LOWER(nome_coletado) ~ '\yquente\y' THEN 'Q'
            ELSE NULL
        END AS ciclo_code
    FROM public.produtos_depara_nome
    WHERE estado='REVISAR'
      AND marca_norm IN ('MIDEA','LG','SAMSUNG','ELECTROLUX','ELGIN','PHILCO','GREE','TCL','AGRATTO','HISENSE')
),
norm AS (
    SELECT id, marca_norm, ciclo_code,
        CASE btu_raw
            WHEN '9.000'  THEN 9000  WHEN '9000'  THEN 9000  WHEN '9k'  THEN 9000
            WHEN '12.000' THEN 12000 WHEN '12000' THEN 12000 WHEN '12k' THEN 12000
            WHEN '18.000' THEN 18000 WHEN '18000' THEN 18000 WHEN '18k' THEN 18000
            WHEN '22.000' THEN 22000 WHEN '22000' THEN 22000 WHEN '22k' THEN 22000
            WHEN '24.000' THEN 24000 WHEN '24000' THEN 24000 WHEN '24k' THEN 24000
            WHEN '30.000' THEN 30000 WHEN '30000' THEN 30000 WHEN '30k' THEN 30000
        END AS btu
    FROM base
)
UPDATE public.produtos_depara_nome d
SET estado='MAPEADO',
    familia = n.marca_norm || '-' || n.btu || '-' || n.ciclo_code,
    origem='seed_generic', revisado_em=now()
FROM norm n WHERE d.id = n.id AND n.btu IS NOT NULL AND n.ciclo_code IS NOT NULL;


-- ============================================================================
-- PASSO 4 — Backfill em rac_monitoramento e coletas
-- ============================================================================

UPDATE public.rac_monitoramento m
SET familia_resolvida = d.familia,
    sku_resolvido     = d.sku,
    estado_match      = d.estado
FROM public.produtos_depara_nome d
WHERE m.produto_sku = d.nome_coletado;

-- coletas: incremental (só linhas órfãs). Para refazer total:
--   remova o "AND c.estado_match IS NULL".
UPDATE public.coletas c
SET familia_resolvida = d.familia,
    sku_resolvido     = d.sku,
    estado_match      = d.estado
FROM public.produtos_depara_nome d
WHERE c.produto = d.nome_coletado
  AND c.estado_match IS NULL;


-- ============================================================================
-- PASSO 5 — Refresh da materialized view de filtros
-- ============================================================================

REFRESH MATERIALIZED VIEW public.mv_filter_options_90d;


-- ============================================================================
-- VALIDAÇÕES
-- ============================================================================
--
-- SELECT estado_match, count(*) FROM coletas           GROUP BY 1 ORDER BY 2 DESC;
-- SELECT estado_match, count(*) FROM rac_monitoramento GROUP BY 1 ORDER BY 2 DESC;
-- SELECT estado,       count(*) FROM produtos_depara_nome GROUP BY 1 ORDER BY 2 DESC;
-- SELECT count(*) FROM coletas WHERE estado_match IS NULL;  -- esperado: 0
