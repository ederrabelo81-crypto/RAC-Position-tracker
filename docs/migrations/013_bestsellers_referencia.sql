-- Migração 013 — Coluna `referencia` na tabela `bestsellers`.
--
-- Contexto: a série passou a coletar DUAS medições distintas por lista:
--
--   * 'mais_vendidos' — a loja expõe ordenação por VENDAS e o endpoint prova
--                       isso (parâmetro de sort por vendas). Variável de
--                       RESULTADO.
--   * 'relevancia'    — a loja NÃO expõe ordenação por vendas; a lista é a
--                       ordem de relevância/destaque do próprio algoritmo
--                       (mix de venda, margem, estoque, curadoria). É PROXY
--                       de destaque, NUNCA medida de venda.
--
-- REGRA DURA registrada no schema: as duas referências NÃO se misturam. Não
-- comparar, agregar ou somar o % de topo de uma lista de 'mais_vendidos' com
-- o de uma 'relevancia' — são populações diferentes. O dashboard as mantém em
-- segmentos separados.
--
-- Aplicação:
--   psql "$SUPABASE_DB_URL" -f docs/migrations/013_bestsellers_referencia.sql
-- ou via Supabase SQL Editor / MCP apply_migration.

ALTER TABLE bestsellers
    ADD COLUMN IF NOT EXISTS referencia TEXT NOT NULL DEFAULT 'mais_vendidos';

COMMENT ON COLUMN bestsellers.referencia IS
    '''mais_vendidos'' (lista ordenada por vendas, variável de resultado) ou '
    '''relevancia'' (ordem de destaque da loja, proxy). NUNCA comparar ou '
    'somar as duas referências entre si.';

-- Backfill: as leituras existentes são todas de listas de mais vendidos
-- (o default já cobre isso). As plataformas de referência RELEVANCIA só
-- passam a existir a partir desta versão, então não há linha antiga delas —
-- mas o UPDATE abaixo deixa a intenção explícita e é idempotente.
UPDATE bestsellers
   SET referencia = 'relevancia'
 WHERE plataforma IN (
        'dufrio', 'centralar', 'leveros', 'ferreiracosta', 'engage'
     )
   AND referencia IS DISTINCT FROM 'relevancia';

-- Série do KPI recortada por referência: o painel lê a mesma plataforma ao
-- longo do tempo, mas nunca cruza referências.
CREATE INDEX IF NOT EXISTS idx_bestsellers_referencia
    ON bestsellers (referencia, plataforma, data, rank);
