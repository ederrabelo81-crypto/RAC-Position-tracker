-- docs/migrations/009_sku_resolucao_v2.sql
--
-- De-para v2 — resolução de SKU por ATRIBUTOS (FASES 1 e 3 do plano).
-- 100% ADITIVO e REVERSÍVEL: cria objetos NOVOS, não toca `coletas.produto`,
-- `coletas.sku_resolvido` nem `coletas.familia_resolvida` (legados intactos).
-- Rollback: `DROP` dos objetos no fim do arquivo.
--
-- ⚠️ APLICAÇÃO É GATED. Aplicar só após aprovação do dry-run
--    (reports/depara_dryrun.md) e da validação (FASE 4 no mesmo arquivo).
--    A POPULAÇÃO de `coletas_sku_resolucao` roda por
--    `scripts/resolve_sku_v2.py --apply` (Python, reusa utils/sku_matcher.py).
--    A PROMOÇÃO (dashboard consumir `vw_coletas_resolvida`) é passo à parte.
--
-- Pré-requisitos do dry-run (ver relatórios):
--   • família_v2: cobertura 100% no MAPEADO, modelo 95,3% vs pricetrack → OK.
--   • sku_v2 exato: 81% vs gabarito (catálogo tem SKUs duplicados por modelo)
--     → NÃO promover cravar SKU antes de deduplicar o catálogo (FASE 1.b).

-- ============================================================================
-- FASE 1 — Catálogo canônico de SKU (a partir do curado + namespace pricetrack)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.sku_catalog (
  sku             text PRIMARY KEY,
  marca           text,
  capacidade_btu  integer,
  ciclo           text,
  familia_linha   text,           -- refinada (linha re-derivada do pricetrack)
  edicao          text,           -- linha modal observada no pricetrack
  voltagem        text,
  sku_canonico    text,           -- dedup: SKU canônico do mesmo produto (build_sku_catalog.py)
  n_pricetrack    integer DEFAULT 0,
  tecnologia      text DEFAULT 'Inverter',
  in_pricetrack   boolean DEFAULT false,
  fonte           text,           -- 'produtos_catalogo' | 'pricetrack'
  updated_at      timestamptz DEFAULT now()
);

-- 1.a — curado (produtos_catalogo): atributos autoritativos para os 241 SKUs.
INSERT INTO public.sku_catalog
  (sku, marca, capacidade_btu, ciclo, familia_linha, edicao, voltagem,
   tecnologia, in_pricetrack, fonte)
SELECT
  pc.sku, pc.marca, pc.capacidade_btu, pc.ciclo, pc.familia_linha,
  NULLIF(trim(regexp_replace(
    regexp_replace(
      regexp_replace(COALESCE(pc.familia_linha, ''), '^' || pc.marca || '-', ''),
      '-' || pc.capacidade_btu || '-(F|QF|Q)$', ''),
    '-', ' ', 'g')), '') AS edicao,
  pc.voltagem,
  'Inverter',
  EXISTS (SELECT 1 FROM public.pricetrack_daily p WHERE p.sku = pc.sku),
  'produtos_catalogo'
FROM public.produtos_catalogo pc
WHERE pc.ativo
ON CONFLICT (sku) DO UPDATE SET
  marca = EXCLUDED.marca, capacidade_btu = EXCLUDED.capacidade_btu,
  ciclo = EXCLUDED.ciclo, familia_linha = EXCLUDED.familia_linha,
  edicao = EXCLUDED.edicao, voltagem = EXCLUDED.voltagem,
  in_pricetrack = EXCLUDED.in_pricetrack, fonte = EXCLUDED.fonte,
  updated_at = now();

-- 1.b — namespace completo: SKUs que só existem no pricetrack (p/ asserts).
--       Atributos ficam NULL (não temos colunas no pricetrack); marca = brand.
INSERT INTO public.sku_catalog (sku, marca, in_pricetrack, fonte)
SELECT DISTINCT p.sku, max(p.brand), true, 'pricetrack'
FROM public.pricetrack_daily p
WHERE p.sku NOT IN (SELECT sku FROM public.sku_catalog)
GROUP BY p.sku
ON CONFLICT (sku) DO NOTHING;

-- 1.c — REFINO (FASE 1.b): `familia_linha` re-derivada dos títulos do pricetrack
--        (split de linhas grossas) + `sku_canonico` (dedup de SKUs do mesmo
--        produto) são produzidos por `scripts/build_sku_catalog.py`. Esse passo
--        subiu o SKU-exato de 81% → 88,3% vs gabarito (ver reports/catalog_dedup.md).
--        A aplicação (UPDATE de familia_linha/sku_canonico) é GATED — carregar a
--        partir de reports/sku_catalog_refined.csv após revisão.

-- FASE 1 (vocabulário): variações de LINHA/edição observadas, por marca.
-- O vocabulário OPERATIVO vive em utils/normalize_product.py (_LINE_PATTERNS) e
-- utils/attr_parser.py; esta tabela é o espelho persistido/auditável.
CREATE TABLE IF NOT EXISTS public.sku_attr_vocab (
  atributo        text,           -- 'edicao' | 'ciclo' | 'tecnologia' | 'voltagem'
  marca           text,
  valor_canonico  text,
  n_skus          integer,
  PRIMARY KEY (atributo, marca, valor_canonico)
);

INSERT INTO public.sku_attr_vocab (atributo, marca, valor_canonico, n_skus)
SELECT 'edicao', marca, edicao, count(*)
FROM public.sku_catalog
WHERE edicao IS NOT NULL
GROUP BY marca, edicao
ON CONFLICT (atributo, marca, valor_canonico) DO UPDATE SET n_skus = EXCLUDED.n_skus;

-- ============================================================================
-- FASE 3 — Resolução v2 (uma linha por TÍTULO distinto) + pendências + view
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.coletas_sku_resolucao (
  produto      text PRIMARY KEY,         -- chave natural do de-para (= coletas.produto)
  sku_v2       text,                     -- só quando 1 SKU unívoco (confiança alta)
  familia_v2   text,                     -- granularidade honesta (cobertura ~100% MAPEADO)
  estado       text,                     -- MAPEADO | FORA_ESCOPO | NAO_AC | REVISAR
  confianca    text,                     -- alta | ambigua | baixa
  metodo       text,
  motivo       text,
  candidatos   text,                     -- SKUs candidatos (pipe-separated) p/ revisão
  atributos    jsonb,                    -- atributos parseados (auditoria)
  resolved_at  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_coletas_sku_resolucao_sku ON public.coletas_sku_resolucao (sku_v2);
CREATE INDEX IF NOT EXISTS idx_coletas_sku_resolucao_fam ON public.coletas_sku_resolucao (familia_v2);

-- Pendências p/ revisão humana: títulos AC sem SKU cravado, com atributos +
-- candidatos + motivo, e o impacto (linhas/plataformas) via join no coletas.
CREATE OR REPLACE VIEW public.depara_pendencias AS
SELECT
  r.produto,
  r.estado,
  r.confianca,
  r.motivo,
  r.familia_v2,
  r.candidatos,
  r.atributos,
  count(c.id)                              AS linhas_impacto,
  array_agg(DISTINCT c.plataforma)         AS plataformas,
  min(c.id)                                AS exemplo_linha_id
FROM public.coletas_sku_resolucao r
JOIN public.coletas c ON c.produto = r.produto
WHERE r.sku_v2 IS NULL
  AND r.estado IN ('MAPEADO', 'REVISAR')
GROUP BY r.produto, r.estado, r.confianca, r.motivo, r.familia_v2,
         r.candidatos, r.atributos;

-- View canônica que o dashboard PODE passar a consumir (após promoção aprovada).
-- Chave de agrupamento honesta: SKU quando unívoco, senão família.
-- NÃO sobrescreve nada: é um LEFT JOIN sobre coletas.
CREATE OR REPLACE VIEW public.vw_coletas_resolvida AS
SELECT
  c.*,
  r.sku_v2,
  r.familia_v2,
  r.confianca                              AS confianca_v2,
  COALESCE(r.sku_v2, r.familia_v2)         AS chave_agrupamento_v2
FROM public.coletas c
LEFT JOIN public.coletas_sku_resolucao r ON r.produto = c.produto;

-- ============================================================================
-- VALIDAÇÕES (rodar após popular via scripts/resolve_sku_v2.py --apply)
--   SELECT count(*) FROM public.sku_catalog;                       -- ~583
--   SELECT count(*) FROM public.coletas_sku_resolucao;             -- nº títulos
--   SELECT count(*) FROM public.depara_pendencias;                 -- fila humana
--   -- nenhum sku_v2 fora do namespace pricetrack:
--   SELECT count(*) FROM public.coletas_sku_resolucao r
--     WHERE r.sku_v2 IS NOT NULL
--       AND r.sku_v2 NOT IN (SELECT sku FROM public.sku_catalog WHERE in_pricetrack);
-- ============================================================================

-- ============================================================================
-- ROLLBACK (reverte 100%):
--   DROP VIEW IF EXISTS public.vw_coletas_resolvida;
--   DROP VIEW IF EXISTS public.depara_pendencias;
--   DROP TABLE IF EXISTS public.coletas_sku_resolucao;
--   DROP TABLE IF EXISTS public.sku_attr_vocab;
--   DROP TABLE IF EXISTS public.sku_catalog;
-- ============================================================================
