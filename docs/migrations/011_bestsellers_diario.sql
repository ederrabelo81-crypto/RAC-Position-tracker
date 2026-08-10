-- Migração 011 — Tabela `bestsellers`: série diária das listas "Mais Vendidos".
--
-- Contexto: a tabela `coletas` guarda a coleta dirigida por KEYWORD — presença,
-- posição e buy box numa SERP de busca. Esta tabela guarda outra coisa: o
-- ranking inteiro da lista ordenada por vendas de cada varejista. São duas
-- populações diferentes (uma responde "onde apareço quando buscam X", a outra
-- "o que está vendendo"), por isso não compartilham tabela.
--
-- Por que ela existe: a coleta de posição/preço NÃO contém volume de venda.
-- As listas de mais vendidos são a única variável de RESULTADO disponível no
-- nível do SKU, a custo zero.
--
-- REGRA DURA registrada no schema: `rank` é ORDINAL. Não somar entre
-- plataformas, não converter em share de mercado (isso vem de GfK/Neotrust).
-- `vendidos` só é comparável dentro da mesma `base_vendidos` — mensal
-- (Shopee) e acumulado (Mercado Livre) são unidades distintas.
--
-- Aplicação:
--   psql "$SUPABASE_DB_URL" -f docs/migrations/011_bestsellers_diario.sql
-- ou via Supabase SQL Editor / MCP apply_migration.

CREATE TABLE IF NOT EXISTS bestsellers (
    id                  BIGSERIAL PRIMARY KEY,

    -- Identificação da leitura
    data                DATE        NOT NULL,
    horario             TEXT,
    run_id              UUID,

    -- Origem e prova da ordenação
    plataforma          TEXT        NOT NULL,   -- chave estável: amazon, shopee…
    plataforma_nome     TEXT,
    mecanica            TEXT,                   -- velocidade | acumulado | vendas_mes | declarado
    url_coleta          TEXT,                   -- URL pública, para auditoria humana
    endpoint            TEXT,                   -- o que foi REALMENTE chamado
    parametro_ordenacao TEXT,                   -- trecho canônico que prova "mais vendidos"

    -- Posição e produto
    rank                INTEGER     NOT NULL,   -- ORDINAL. Ver nota no topo.
    titulo              TEXT,
    marca               TEXT,
    grupo_midea         BOOLEAN,                -- Midea/Carrier/Springer/Comfee (corte GfK)
    btu                 INTEGER,
    tipo                TEXT,                   -- SPLIT_HW | JANELA | PORTATIL | OUTROS_RAC | FORA_ESCOPO | IND
    no_escopo           BOOLEAN,                -- FALSE = contaminação da categoria

    -- Métricas do anúncio
    preco               NUMERIC(10, 2),
    preco_de            NUMERIC(10, 2),         -- preço riscado; lixo na Casas Bahia
    rating              NUMERIC(3, 2),
    reviews             INTEGER,
    vendidos            NUMERIC(12, 2),
    base_vendidos       TEXT,                   -- 'mes' | 'acumulado' | NULL
    seller              TEXT,
    sku_plataforma      TEXT,                   -- ASIN/MLB/itemid — chave de pareamento entre dias
    url_produto         TEXT,
    patrocinado         BOOLEAN,

    criado_em           TIMESTAMPTZ DEFAULT NOW()
);

-- Uma posição por lista por dia. É o que torna a recoleta (após conserto de
-- parser) idempotente: sem isso, rodar de novo duplicaria o ranking e o KPI
-- do dia sairia errado sem nenhum erro no log.
CREATE UNIQUE INDEX IF NOT EXISTS idx_bestsellers_posicao
    ON bestsellers (data, plataforma, rank);

-- Série do KPI: % do top N por plataforma ao longo do tempo.
CREATE INDEX IF NOT EXISTS idx_bestsellers_serie
    ON bestsellers (plataforma, data, rank);

-- Recorte do grupo Midea dentro do escopo do KPI (split hi-wall).
CREATE INDEX IF NOT EXISTS idx_bestsellers_grupo
    ON bestsellers (data, plataforma, grupo_midea)
    WHERE tipo = 'SPLIT_HW';

-- Leitura competitiva por marca.
CREATE INDEX IF NOT EXISTS idx_bestsellers_marca
    ON bestsellers (marca, data);

COMMENT ON TABLE bestsellers IS
    'Ranking diário das listas "Mais Vendidos" por varejista. rank é ORDINAL: '
    'não somar entre plataformas nem converter em share de mercado.';
COMMENT ON COLUMN bestsellers.endpoint IS
    'Endpoint efetivamente chamado. É a evidência de que a lista está ordenada '
    'por vendas — sem o parâmetro canônico, a leitura vai para quarentena.';
COMMENT ON COLUMN bestsellers.base_vendidos IS
    '''mes'' (Shopee) ou ''acumulado'' (Mercado Livre). NUNCA somar as duas.';
COMMENT ON COLUMN bestsellers.no_escopo IS
    'FALSE marca contaminação da categoria (umidificador, depurador, '
    'ventilador). O item fica na base para o portão de validação medi-la.';

-- ---------------------------------------------------------------------------
-- View de apoio: KPI diário já calculado, para o dashboard não repetir a regra
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW bestsellers_kpi_top10 AS
SELECT
    data,
    plataforma,
    COUNT(*)                                        AS itens_top10,
    COUNT(*) FILTER (WHERE grupo_midea)             AS midea_top10,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE grupo_midea) / NULLIF(COUNT(*), 0), 1
    )                                               AS pct_top10,
    MIN(rank) FILTER (WHERE grupo_midea)            AS melhor_rank
FROM bestsellers
WHERE tipo = 'SPLIT_HW' AND rank <= 10
GROUP BY data, plataforma;

COMMENT ON VIEW bestsellers_kpi_top10 IS
    'KPI único da rotina: % do top 10 ocupado pelo grupo Midea, por plataforma '
    'e por dia. Escopo: split hi-wall. Comparar apenas a MESMA plataforma ao '
    'longo do tempo, no mesmo dia da semana.';
