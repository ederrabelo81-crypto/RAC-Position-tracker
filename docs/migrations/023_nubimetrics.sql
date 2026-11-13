-- 023_nubimetrics.sql — Vendas estimadas do Mercado Livre (Nubimetrics MCP), Nov/2026
--
-- Módulo nubimetrics/ (ETL: nubimetrics/run_daily.py, workflow
-- nubimetrics_daily.yml). Mapeamento das fontes: nubimetrics/catalog/MAPPING.md.
--
-- ORÇAMENTO DO FREE TIER (regra do projeto — docs/RETORNO_SUPABASE.md §5.2):
-- tudo aqui é pequeno por construção.
--   nubi_mercado_mensal     categoria × mês (real + previsão)  ~25 linhas/ano
--   nubi_ranking_mensal     mês × dimensão × posição            ~7 mil linhas/ano (~2 MB)
--   nubi_anuncios           SÓ a foto mais recente do explorador (~4–6 mil linhas,
--                           ~3 MB, não cresce: troca atômica por RPC). A série
--                           diária por anúncio vai para o Parquet no Drive
--                           (dataset `nubimetrics`), nunca para o banco.
--   nubi_concorrente_diario 1 slot de concorrência × dia         ~365 linhas/ano
--   dim_partner_sellers     seed manual                          ~20 linhas
-- Sem coluna `raw jsonb` (o bruto mora no Parquet): jsonb por linha triplicaria
-- o tamanho.
--
-- RLS: leitura anon/authenticated (painel); escrita SÓ service_role (o ETL).
-- Mesmo desenho da 012/021 — a chave anon grava nada em silêncio.
--
-- Aplicar: psql "$SUPABASE_DB_URL" -f docs/migrations/023_nubimetrics.sql
-- (ou SQL Editor do Supabase). Idempotente.

-- ── Mercado da categoria (A + H) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nubi_mercado_mensal (
    categoria_id   TEXT        NOT NULL,
    mes            DATE        NOT NULL,           -- 1º dia do mês
    -- 'real' (nubi_category_market_size; o mês corrente vem PARCIAL) ou
    -- 'previsao' (nubi_category_forecast). Previsão nunca sobrescreve real.
    tipo           TEXT        NOT NULL CHECK (tipo IN ('real', 'previsao')),
    parcial        BOOLEAN     NOT NULL DEFAULT false,
    receita_brl    NUMERIC(16,2),
    unidades       NUMERIC(14,0),
    ticket_medio   NUMERIC(12,2),
    receita_brl_aa NUMERIC(16,2),                  -- mesmo mês do ano anterior
    unidades_aa    NUMERIC(14,0),
    sellers        INTEGER,
    sellers_prof   INTEGER,
    anuncios       INTEGER,
    -- meli_category_detail (só no mês fechado): saturação/oportunidade são
    -- níveis High/Medium/Low da CATEGORIA — o MCP não tem saturação por marca.
    saturacao      TEXT,
    oportunidade   TEXT,
    pct_catalogo   NUMERIC(7,3),
    pct_desconto   NUMERIC(7,3),
    pct_frete_gratis NUMERIC(7,3),
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (categoria_id, mes, tipo)
);

-- ── Rankings mensais (B, C, E, G) ───────────────────────────────────────────
-- Uma tabela para as 6 dimensões: items, brands, catalog, seller,
-- official_store (meli_market_rankings) e search_terms
-- (meli_search_trends_rankings). Mês FECHADO — o MCP não serve ranking do mês
-- corrente (period_fallback).
CREATE TABLE IF NOT EXISTS nubi_ranking_mensal (
    categoria_id      TEXT        NOT NULL,
    mes               DATE        NOT NULL,
    dimensao          TEXT        NOT NULL CHECK (dimensao IN
                        ('items', 'brands', 'catalog', 'seller', 'official_store', 'search_terms')),
    posicao           INTEGER     NOT NULL,
    nome              TEXT        NOT NULL,        -- marca normalizada / nickname / título / termo
    receita_brl       NUMERIC(16,2),
    unidades          NUMERIC(14,0),
    share_brl         NUMERIC(7,4),                -- % (brands, seller)
    cresc_receita_pct NUMERIC(12,4),
    cresc_unid_pct    NUMERIC(12,4),
    tendencia         TEXT,                        -- Stable / GoingUp / GoingDown
    posicao_anterior  INTEGER,                     -- search_terms
    qtd_sellers       INTEGER,
    qtd_anuncios      INTEGER,
    catalogo_pct      NUMERIC(7,4),
    atualizado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (categoria_id, mes, dimensao, posicao)
);
CREATE INDEX IF NOT EXISTS idx_nubi_ranking_dim_mes
    ON nubi_ranking_mensal (dimensao, mes DESC);

-- ── Foto dos anúncios (I) — só a mais recente ───────────────────────────────
CREATE TABLE IF NOT EXISTS nubi_anuncios (
    item_hash        TEXT        PRIMARY KEY,      -- id interno da Nubimetrics (NÃO é MLB)
    snapshot_date    DATE        NOT NULL,
    titulo           TEXT,
    marca            TEXT,                         -- normalizada (caixa/acento)
    modelo           TEXT,
    sku              TEXT,
    gtin             TEXT,
    seller           TEXT,                         -- nickname
    seller_hash      TEXT,
    loja_oficial     TEXT,
    preco            NUMERIC(12,2),
    receita_brl      NUMERIC(16,2),                -- janela do explorador
    unidades         NUMERIC(12,0),
    unidades_total   NUMERIC(12,0),                -- desde a publicação
    catalogo         BOOLEAN,
    full_            BOOLEAN,
    flex             BOOLEAN,
    frete_gratis     BOOLEAN,
    exposicao        TEXT,                         -- gold_pro (Premium) / gold_special (Clássico) …
    segmento         TEXT,                         -- 'High Wall Inverter', 'High Wall On/Off', …
    btu              INTEGER,
    faixa_btu        TEXT,
    voltagem         TEXT,
    inverter         BOOLEAN,
    match_source     TEXT,                         -- 'attribute' | 'title_regex'
    criado_em_ml     TEXT,
    atualizado_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nubi_anuncios_snapshot
    ON nubi_anuncios (snapshot_date);

-- ── Concorrente acompanhado (J) — 1 slot da conta ───────────────────────────
CREATE TABLE IF NOT EXISTS nubi_concorrente_diario (
    data           DATE        NOT NULL,
    concorrente    TEXT        NOT NULL,           -- member_code (hash) do slot
    visitas        NUMERIC(14,0),
    unidades       NUMERIC(12,0),
    receita_brl    NUMERIC(16,2),
    anuncios       INTEGER,
    conversao_pct  NUMERIC(8,4),
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (data, concorrente)
);

-- ── Sellers parceiros (seed manual, editável) ───────────────────────────────
CREATE TABLE IF NOT EXISTS dim_partner_sellers (
    nickname         TEXT        PRIMARY KEY,      -- como o Nubimetrics grava
    display_name     TEXT        NOT NULL,         -- como o painel mostra
    is_midea_partner BOOLEAN     NOT NULL DEFAULT true,
    tier             TEXT,
    account_manager  TEXT,
    atualizado_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed PROVISÓRIO: os dealers do topo do ranking de AC (out/2026). Revise
-- is_midea_partner/tier/account_manager no SQL Editor. ON CONFLICT DO NOTHING:
-- reaplicar a migração nunca desfaz a sua edição.
INSERT INTO dim_partner_sellers (nickname, display_name) VALUES
    ('CLIMARIOREFRIGERACAO', 'Clima Rio'),
    ('DUFRIO REFRIGERAÇÃO',  'Dufrio'),
    ('CONTINENTALCENTER',    'Web Continental'),
    ('ECONTINENTAL ES',      'Web Continental ES'),
    ('LEVEROS_RESOLVE',      'Leveros'),
    ('CENTRALAR.COM',        'Central Ar'),
    ('BELMICROSHOP',         'Bel Micro'),
    ('FRIOPEÇAS',            'Frio Peças'),
    ('ENGAGEENGAGE20220727143357', 'Engage'),
    ('POLOAR',               'Polo Ar')
ON CONFLICT (nickname) DO NOTHING;

-- ── RLS ─────────────────────────────────────────────────────────────────────
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['nubi_mercado_mensal', 'nubi_ranking_mensal', 'nubi_anuncios',
                             'nubi_concorrente_diario', 'dim_partner_sellers']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        -- Papéis anon/authenticated/service_role são do Supabase (a 019 os cria
        -- em Postgres puro). Sem eles — Postgres direto via RAC_DB_DSN sem a
        -- 019 — a policy/grant é pulada em vez de abortar a migração inteira.
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
END $$;

-- ── Troca atômica da foto de anúncios ───────────────────────────────────────
-- TRUNCATE + INSERT numa transação: não deixa linha morta nem índice inchado
-- (o DELETE deixaria — regra da 022), e o painel nunca vê a tabela vazia no meio
-- da troca. Só o service_role executa.
CREATE OR REPLACE FUNCTION public.nubi_substituir_anuncios(p_linhas jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_n integer;
BEGIN
    IF p_linhas IS NULL OR jsonb_typeof(p_linhas) <> 'array' OR jsonb_array_length(p_linhas) = 0 THEN
        RAISE EXCEPTION 'nubi_substituir_anuncios: lote vazio — a foto anterior foi mantida';
    END IF;
    -- `atualizado_em` vem em cada linha (o ETL manda): jsonb_populate_recordset
    -- não aplica DEFAULT, e a coluna é NOT NULL.
    TRUNCATE nubi_anuncios;
    INSERT INTO nubi_anuncios
    SELECT * FROM jsonb_populate_recordset(NULL::nubi_anuncios, p_linhas)
    ON CONFLICT (item_hash) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
END;
$$;

DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.nubi_substituir_anuncios(jsonb) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.nubi_substituir_anuncios(jsonb) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON FUNCTION public.nubi_substituir_anuncios(jsonb) FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION public.nubi_substituir_anuncios(jsonb) TO service_role;
    END IF;
END $$;
