-- 021_clima_diario.sql — Radar meteorológico (Out/2026)
--
-- Temperatura e chuva diárias por cidade (Open-Meteo: ERA5 no observado,
-- previsão de 16 dias), para cruzar calor com venda de ar condicionado.
-- Escrito por scripts/clima_collect.py (workflow clima_daily.yml); lógica em
-- utils/clima.py.
--
-- Tamanho: 17 cidades × 1 linha/dia ≈ 6.200 linhas/ano (~1 MB/ano) — não pesa
-- na cota do free tier, ao contrário de `coletas`. Por isso o histórico
-- inteiro fica aqui (backfill desde 2016), sem poda.
--
-- Aplicar:
--   psql "$SUPABASE_DB_URL" -f docs/migrations/021_clima_diario.sql
-- ou via Supabase SQL Editor / MCP apply_migration. Idempotente.

CREATE TABLE IF NOT EXISTS clima_diario (
    data          DATE        NOT NULL,
    cidade        TEXT        NOT NULL,
    uf            TEXT        NOT NULL,
    regiao        TEXT        NOT NULL,
    -- 'observado' (dia já passou) ou 'previsao'. Previsão nunca vira
    -- observado: a linha observada do mesmo dia entra com outra chave.
    tipo          TEXT        NOT NULL CHECK (tipo IN ('observado', 'previsao')),
    tmax          NUMERIC(4,1),
    tmin          NUMERIC(4,1),
    tmed          NUMERIC(4,1),
    sensacao_max  NUMERIC(4,1),
    precip_mm     NUMERIC(6,1),
    -- 'archive' (ERA5, reanálise revisada) ou 'forecast' (API de previsão,
    -- inclusive os dias passados que ela devolve com past_days).
    fonte         TEXT        NOT NULL,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (data, cidade, tipo)
);

CREATE INDEX IF NOT EXISTS idx_clima_diario_regiao_data
    ON clima_diario (regiao, data DESC);

COMMENT ON TABLE clima_diario IS
    'Clima diário por cidade (Open-Meteo) para o radar meteorológico × venda. '
    'Ver utils/clima.py.';

-- Leitura para os dashboards; escrita só pelo service_role (RLS sem policy de
-- escrita nega) — mesmo desenho da 012.
ALTER TABLE clima_diario ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS clima_diario_leitura_publica ON clima_diario;

CREATE POLICY clima_diario_leitura_publica
    ON clima_diario
    FOR SELECT
    TO anon, authenticated
    USING (true);

GRANT SELECT ON clima_diario TO anon, authenticated;
GRANT ALL ON clima_diario TO service_role;
