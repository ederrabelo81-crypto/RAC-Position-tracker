-- Migration 007: subcategoria e família do produto em pricetrack_daily
--
-- Motivo (Nov/2026): o painel Diagnóstico é só ar-condicionado, com padrão
-- Split Hi-Wall Inverter (docs/diagnostico_fase0.md §8). O nome do produto
-- NÃO identifica Hi-Wall — o AI Ecomaster da Midea, linha principal, se chama
-- "AR CONDICIONADO SPLIT 18000 BTU ..." — então a subcategoria tem de vir do
-- campo `subcategory` do export da API.
--
-- Aditiva e nullable: em Postgres 11+ é operação de metadados (instantânea).
-- Linhas antigas ficam NULL; o painel as classifica pelo de-para
-- SKU → subcategoria (utils/ac_attributes.py::subcategory_map).
-- O importador grava sem estas colunas (com WARNING) enquanto ela não for
-- aplicada — ao contrário da 006, não aborta.

ALTER TABLE pricetrack_daily
    ADD COLUMN IF NOT EXISTS subcategory TEXT,
    ADD COLUMN IF NOT EXISTS family      TEXT;

COMMENT ON COLUMN pricetrack_daily.subcategory IS
    'Subcategoria do produto no catálogo PriceTrack (HI-WALL, CASSETE, TETO, '
    'JANELA, PORTÁTIL, PAREDE, DUTO), em maiúsculas. NULL = gravada antes de '
    '05/11/2026 ou import sem a migração 007.';
COMMENT ON COLUMN pricetrack_daily.family IS
    'Família do produto no catálogo PriceTrack (na prática igual ao SKU).';
