-- ============================================================================
-- 015_pipeline_heartbeat.sql — Livro-razão de EXECUÇÃO da pipeline
-- ============================================================================
-- Por que esta tabela existe
-- --------------------------
-- Até 28/08/2026 todo mecanismo de monitoramento do projeto olhava para o
-- DADO (`coletas`, `pricetrack_daily`, Parquet no Drive). Isso pega "rodou e
-- não trouxe nada", mas é estruturalmente cego para **"não rodou"** — e foi
-- justamente esse o modo de falha dos três incidentes de agosto:
--
--   1. O import do PriceTrack agendado para 06:00 BRT (cron 09:00 UTC) só
--      começou às 19:18 UTC de 27/08 — mais de 10h depois. O briefing das
--      07:00 saiu com preço de D-2 e ninguém foi avisado, porque o job não
--      falhou: ele ainda nem tinha rodado.
--   2. A coleta de Google Shopping vem zerada há ~1 mês. O job termina VERDE
--      (221 runs seguidos de `success` no `collect.yml`) porque uma plataforma
--      sem resultado não derruba o run.
--   3. A coleta da VM Oracle parou. Nada no repositório sabe que a VM existe:
--      não há canal "Oracle VM" no watchdog e `dealers` está desligado em
--      `ACTIVE_PLATFORMS`, então `_expected_platforms()` nem espera dealers.
--
-- Um livro-razão de execução resolve os três com a mesma pergunta invertida:
-- em vez de "o dado chegou?", **"quem prometeu rodar hoje e não bateu ponto?"**
-- Ausência de batida até o deadline é um evento positivo (dead man's switch),
-- não silêncio.
--
-- Aplicar: SQL Editor do Supabase (uma vez).
-- ============================================================================

CREATE TABLE IF NOT EXISTS pipeline_heartbeat (
    id               BIGSERIAL PRIMARY KEY,

    -- Identidade do job no registro canônico (utils/pipeline_registry.py).
    -- É texto livre de propósito: o registro é a fonte de verdade e evolui
    -- mais rápido que uma migração de enum.
    job_id           TEXT        NOT NULL,
    executor         TEXT        NOT NULL,   -- github_actions | oracle_vm | pc_local | externo

    -- Dia BRT ao qual a execução se refere. NÃO é `now()::date`: um job que
    -- começa 23:50 BRT e termina 00:10 pertence ao dia em que começou, e o
    -- import do PriceTrack das 03:20 se refere ao dia corrente, não a D-1.
    data_ref         DATE        NOT NULL,

    status           TEXT        NOT NULL
                     CHECK (status IN ('STARTED', 'SUCCESS', 'PARTIAL', 'FAILED')),

    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    duration_seconds NUMERIC,

    -- Quantas linhas a execução afirma ter gravado no destino. Serve para
    -- separar "rodou e trouxe dado" de "rodou verde e trouxe zero" — o modo
    -- de falha do Google Shopping.
    rows_written     INTEGER,

    host             TEXT,        -- hostname/runner: identifica QUAL máquina bateu
    run_url          TEXT,        -- URL do run no Actions, quando houver
    detail           TEXT,        -- uma linha de diagnóstico legível

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A consulta do supervisor é sempre "últimas batidas deste job neste dia".
CREATE INDEX IF NOT EXISTS idx_heartbeat_job_data
    ON pipeline_heartbeat (job_id, data_ref DESC, id DESC);

-- Varredura "o que aconteceu hoje", usada pelo relatório e pelo dashboard.
CREATE INDEX IF NOT EXISTS idx_heartbeat_data
    ON pipeline_heartbeat (data_ref DESC, id DESC);

COMMENT ON TABLE pipeline_heartbeat IS
    'Livro-razão de execução da pipeline. Ausência de batida até o deadline do '
    'job (utils/pipeline_registry.py) é o sinal de "não rodou" — o watchdog de '
    'dado não consegue distinguir isso de "rodou e veio vazio".';

-- ---------------------------------------------------------------------------
-- Última batida por (job, dia) — o supervisor lê isto, não a tabela crua.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW pipeline_heartbeat_ultimo AS
SELECT DISTINCT ON (job_id, data_ref)
    job_id,
    data_ref,
    executor,
    status,
    started_at,
    finished_at,
    duration_seconds,
    rows_written,
    host,
    run_url,
    detail
FROM pipeline_heartbeat
ORDER BY job_id, data_ref DESC, id DESC;

-- ---------------------------------------------------------------------------
-- Retenção: 180 dias bastam para ler sazonalidade de falha sem inchar a cota.
-- (O projeto já esteve restrito por cota de armazenamento — ver watchdog.yml.)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cleanup_pipeline_heartbeat(dias INTEGER DEFAULT 180)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    removidas INTEGER;
BEGIN
    DELETE FROM pipeline_heartbeat
     WHERE data_ref < (CURRENT_DATE - dias);
    GET DIAGNOSTICS removidas = ROW_COUNT;
    RETURN removidas;
END;
$$;

-- ---------------------------------------------------------------------------
-- RLS — mesma política das demais tabelas: leitura anon, escrita service_role.
-- O supervisor roda no Actions com service_role; o dashboard lê com anon.
-- ---------------------------------------------------------------------------
ALTER TABLE pipeline_heartbeat ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS pipeline_heartbeat_anon_select ON pipeline_heartbeat;
CREATE POLICY pipeline_heartbeat_anon_select
    ON pipeline_heartbeat FOR SELECT
    TO anon, authenticated
    USING (true);
