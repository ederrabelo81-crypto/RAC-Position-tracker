-- Migration 003: turno (Diário/Manhã/Tarde) em pricetrack_daily
-- Objetivo: o export bruto da PriceTrack carimba `collection_hour` (hora real
-- do crawl, 24/7). O importer passa a recortar as ofertas por hora em
-- Manhã (08–12h) e Tarde (18–22h) BRT, além do agregado "Diário" (dia inteiro),
-- para que o PriceTrack alimente os turnos do dashboard (coletas viram fallback).
--
-- Linhas já existentes (1 por dia/grupo) representam o dia inteiro → 'Diário'.
-- O DEFAULT garante isso para o histórico sem UPDATE explícito; em Postgres 11+
-- adicionar coluna NOT NULL com DEFAULT constante é operação de metadados (rápida).

ALTER TABLE pricetrack_daily
    ADD COLUMN IF NOT EXISTS turno TEXT NOT NULL DEFAULT 'Diário';

-- A unicidade agora inclui `turno`: cada (data, grupo) pode ter até 3 linhas
-- (Diário, Manhã, Tarde). Trocamos a constraint antiga pela nova. As linhas
-- atuais (todas 'Diário') continuam únicas sob a nova chave, então a recriação
-- do índice único não encontra conflito.
ALTER TABLE pricetrack_daily
    DROP CONSTRAINT IF EXISTS pricetrack_daily_unique;

ALTER TABLE pricetrack_daily
    ADD CONSTRAINT pricetrack_daily_unique
    UNIQUE (collection_date, turno, brand, sku, marketplace, seller);

-- Índice para os recortes por (data, turno) que o dashboard passa a filtrar.
CREATE INDEX IF NOT EXISTS idx_ptd_date_turno
    ON pricetrack_daily(collection_date, turno);
