-- =============================================================================
-- 024_nubi_sellout_diario.sql — sell-out DIÁRIO do ML (exports da Nubimetrics)
-- =============================================================================
--
-- POR QUÊ (18/11/2026)
-- --------------------
-- A 023 guarda o que o MCP da Nubimetrics entrega: ranking de MÊS FECHADO e uma
-- foto do explorador. A série diária por marca/seller/anúncio só existe no
-- relatório dinâmico por publicações (/company/itemreport), que o MCP não
-- serve — ela entra pelo export XLSX dos templates "RAC | …", lido por
-- `python -m nubimetrics.import_export` (nubimetrics/README.md).
--
-- ORÇAMENTO (free tier, banco já perto da cota)
-- ---------------------------------------------
--   nubi_vendas_diarias_agg       dia × marca × seller × segmento × faixa
--                                 ~440 linhas/dia × 100 dias  ≈ 44 mil  (~8 MB)
--   nubi_vendas_diarias_anuncio   dia × (título, marca, seller)
--                                 ~600 linhas/dia × 35 dias   ≈ 21 mil  (~6 MB)
--   nubi_listing_attributes       1 linha por título                    (<1 MB)
--   nubi_segment_override         correção manual                       (dezenas)
--
-- O HISTÓRICO INTEIRO fica no Parquet (datasets `nubi_vendas_diarias`), nunca
-- no banco. A poda é por DELETE (janela móvel) → as duas tabelas diárias
-- entram na lista branca de `compactar_indices` (022, atualizada junto):
-- REAPLIQUE A 022 depois desta.
--
-- Sem código do anúncio: o template agrupa por TÍTULO. `anuncio_key` =
-- md5(título|marca|seller)[:20] (nubimetrics/diario.py::anuncio_key).
--
-- Aplicar: SQL Editor do Supabase (ou psql). Idempotente.
-- =============================================================================

CREATE TABLE IF NOT EXISTS nubi_vendas_diarias_agg (
    data           DATE          NOT NULL,
    marca          TEXT          NOT NULL,
    seller         TEXT          NOT NULL,      -- nickname como o Nubimetrics grava
    segmento       TEXT          NOT NULL,      -- 'High Wall Inverter' | 'Outros AC'
    faixa_btu      TEXT          NOT NULL,      -- '9k', '12k'… ou 'n.d.'
    unidades       NUMERIC(12,2) NOT NULL DEFAULT 0,
    gmv_brl        NUMERIC(16,2) NOT NULL DEFAULT 0,
    anuncios       INTEGER,
    atualizado_em  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (data, marca, seller, segmento, faixa_btu)
);

CREATE TABLE IF NOT EXISTS nubi_vendas_diarias_anuncio (
    data           DATE          NOT NULL,
    anuncio_key    TEXT          NOT NULL,
    titulo         TEXT          NOT NULL,
    marca          TEXT          NOT NULL,
    seller         TEXT          NOT NULL,
    segmento       TEXT          NOT NULL,
    faixa_btu      TEXT          NOT NULL,
    origem         TEXT,                        -- titulo | atributo | override
    unidades       NUMERIC(12,2) NOT NULL DEFAULT 0,
    gmv_brl        NUMERIC(16,2) NOT NULL DEFAULT 0,
    atualizado_em  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (data, anuncio_key)
);

-- Dimensão de produto vinda do relatório de ATRIBUTOS. O `Mes` daquele
-- relatório NÃO é data de venda (concentra o mês no dia do snapshot): aqui só
-- entra o mês do snapshot como carimbo, nunca uma série.
CREATE TABLE IF NOT EXISTS nubi_listing_attributes (
    titulo          TEXT        NOT NULL,
    marca           TEXT        NOT NULL DEFAULT '',
    ac_type_raw     TEXT,
    inverter_raw    TEXT,
    btu_raw         TEXT,
    snapshot_month  DATE,
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (titulo, marca)
);

-- Correção manual: `chave` = anuncio_key OU o título exato.
CREATE TABLE IF NOT EXISTS nubi_segment_override (
    chave          TEXT        PRIMARY KEY,
    segmento       TEXT        NOT NULL CHECK (segmento IN ('High Wall Inverter', 'Outros AC')),
    motivo         TEXT,
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS nubi_vda_seg_data ON nubi_vendas_diarias_agg (segmento, data);

CREATE OR REPLACE VIEW v_nubi_hw_inverter_daily AS
SELECT data, marca, seller, faixa_btu, unidades, gmv_brl, anuncios
  FROM nubi_vendas_diarias_agg
 WHERE segmento = 'High Wall Inverter';

-- ── RLS: leitura anon (painel), escrita só service_role ─────────────────────
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['nubi_vendas_diarias_agg', 'nubi_vendas_diarias_anuncio',
                             'nubi_listing_attributes', 'nubi_segment_override']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
           AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_leitura', t);
            EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO anon, authenticated USING (true)',
                           t || '_leitura', t);
            EXECUTE format('GRANT SELECT ON %I TO anon, authenticated', t);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
            EXECUTE format('GRANT ALL ON %I TO service_role', t);
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT SELECT ON v_nubi_hw_inverter_daily TO anon, authenticated;
    END IF;
END $$;

-- ── Troca atômica de um intervalo de dias + poda da janela ─────────────────
-- Reimportar um export substitui os dias que ele cobre INTEIROS (a Nubimetrics
-- revisa a estimativa; um upsert deixaria linha velha de um seller/segmento
-- que mudou e somaria duas vezes). DELETE + INSERT numa transação: o painel
-- nunca vê o dia pela metade. A poda (data < corte) vai junto.
CREATE OR REPLACE FUNCTION public.nubi_substituir_diario(
    p_desde        date,
    p_ate          date,
    p_agg          jsonb,
    p_anuncio      jsonb,
    p_corte_agg    date,
    p_corte_anuncio date
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_agg integer;
    v_anu integer;
    v_poda_agg integer;
    v_poda_anu integer;
BEGIN
    IF p_agg IS NULL OR jsonb_typeof(p_agg) <> 'array' OR jsonb_array_length(p_agg) = 0 THEN
        RAISE EXCEPTION 'nubi_substituir_diario: lote agregado vazio — nada foi trocado';
    END IF;
    DELETE FROM nubi_vendas_diarias_agg WHERE data BETWEEN p_desde AND p_ate;
    INSERT INTO nubi_vendas_diarias_agg
    SELECT * FROM jsonb_populate_recordset(NULL::nubi_vendas_diarias_agg, p_agg);
    GET DIAGNOSTICS v_agg = ROW_COUNT;

    DELETE FROM nubi_vendas_diarias_anuncio WHERE data BETWEEN p_desde AND p_ate;
    IF p_anuncio IS NOT NULL AND jsonb_typeof(p_anuncio) = 'array' THEN
        INSERT INTO nubi_vendas_diarias_anuncio
        SELECT * FROM jsonb_populate_recordset(NULL::nubi_vendas_diarias_anuncio, p_anuncio);
        GET DIAGNOSTICS v_anu = ROW_COUNT;
    END IF;

    DELETE FROM nubi_vendas_diarias_agg WHERE data < p_corte_agg;
    GET DIAGNOSTICS v_poda_agg = ROW_COUNT;
    DELETE FROM nubi_vendas_diarias_anuncio WHERE data < p_corte_anuncio;
    GET DIAGNOSTICS v_poda_anu = ROW_COUNT;

    RETURN jsonb_build_object('agg', v_agg, 'anuncio', COALESCE(v_anu, 0),
                              'podadas_agg', v_poda_agg, 'podadas_anuncio', v_poda_anu);
END;
$$;

DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.nubi_substituir_diario(date, date, jsonb, jsonb, date, date) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.nubi_substituir_diario(date, date, jsonb, jsonb, date, date) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON FUNCTION public.nubi_substituir_diario(date, date, jsonb, jsonb, date, date) FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION public.nubi_substituir_diario(date, date, jsonb, jsonb, date, date) TO service_role;
    END IF;
END $$;
