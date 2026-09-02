-- Migration 006: base de preço explícita em pricetrack_daily
--
-- Motivo (Set/2026): as colunas min/avg/mode/max eram agregadas sobre UM preço
-- por oferta escolhido por `scripts/pricetrack_api_import.py::_pick_price`, que
-- pegava `spot_price` e só usava `pix_price` quando o spot vinha nulo — nunca o
-- MENOR entre os dois. O painel do PriceTrack mostra o menor à vista
-- (À Vista + PIX + "Menor"), então a base gravada ficava ~10% acima do que o
-- painel exibe em marketplaces com desconto PIX (Magazine Luiza).
-- Pior: o encadeamento de fallback caía em `forward_price` (a prazo) quando não
-- havia à vista, misturando bases dentro da mesma série de min/média/moda.
--
-- Além disso o agregado não guardava:
--   * qual coleta do dia gerou o número (o painel mostra a ÚLTIMA coleta);
--   * quantas observações estão por trás do agregado;
--   * quantas ofertas do grupo estavam UNAVAILABLE (nunca foram filtradas).
--
-- Esta migração é ADITIVA: nenhuma coluna existente muda de tipo ou de valor.
-- `price_basis` carimba a base de cada linha para que dado corrigido nunca seja
-- confundido com o histórico:
--   'spot_legacy' → linhas gravadas até 01/09/2026 (base spot, com fallback
--                   para a prazo; UNAVAILABLE incluída)
--   'best_cash'   → linhas do importador corrigido (menor entre spot e PIX,
--                   só AVAILABLE)
-- O histórico só vira 'best_cash' quando for REIMPORTADO do NDJSON bruto:
--   python scripts/pricetrack_api_import.py --force --start 2026-07-28 --end 2026-09-01

-- `price_basis` entra com DEFAULT 'spot_legacy': em Postgres 11+ adicionar
-- coluna com default constante é operação de METADADOS (instantânea), então o
-- histórico inteiro fica carimbado sem reescrever 1M de linhas. O default
-- também é a leitura certa para qualquer escrita futura que não se declare:
-- base desconhecida é base antiga, nunca "provavelmente está certa".
ALTER TABLE pricetrack_daily
    ADD COLUMN IF NOT EXISTS price_basis       TEXT DEFAULT 'spot_legacy',
    ADD COLUMN IF NOT EXISTS last_price        NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS last_hour         SMALLINT,
    ADD COLUMN IF NOT EXISTS spot_min_price    NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS pix_min_price     NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS obs_count         INTEGER,
    ADD COLUMN IF NOT EXISTS unavailable_count INTEGER;

COMMENT ON COLUMN pricetrack_daily.price_basis IS
    'Base dos preços da linha: best_cash = menor entre spotPrice e pixPrice, '
    'somente ofertas AVAILABLE. spot_legacy = base antiga (spot com fallback '
    'para forward, UNAVAILABLE incluída) — NÃO comparável com best_cash.';
COMMENT ON COLUMN pricetrack_daily.last_price IS
    'Melhor à vista da ÚLTIMA coleta da janela — é o que o painel do '
    'PriceTrack exibe ("Preço exibido: última coleta"). min_price continua '
    'sendo o piso da janela inteira.';
COMMENT ON COLUMN pricetrack_daily.last_hour IS
    'collection_hour (BRT) da observação que gerou last_price.';
COMMENT ON COLUMN pricetrack_daily.spot_min_price IS
    'Piso do spotPrice na janela — guardado separado para que a base à vista '
    'não se perca ao passarmos a agregar o menor entre spot e PIX.';
COMMENT ON COLUMN pricetrack_daily.pix_min_price IS
    'Piso do pixPrice na janela (NULL quando o marketplace não expõe PIX).';
COMMENT ON COLUMN pricetrack_daily.obs_count IS
    'Nº de observações AVAILABLE com preço à vista por trás do agregado. '
    'Uma linha de pricetrack_daily NÃO é uma oferta: é N coletas do dia.';
COMMENT ON COLUMN pricetrack_daily.unavailable_count IS
    'Nº de observações UNAVAILABLE do grupo (excluídas dos preços). Quando o '
    'grupo só teve UNAVAILABLE, os preços ficam NULL e a linha permanece — '
    'a listagem existiu, mas não competiu por preço.';

-- Carimba o histórico como base antiga. Sem isso não há como distinguir uma
-- linha corrigida de uma linha errada, e um gráfico de evolução emendaria as
-- duas bases numa série só (degrau artificial de ~10% no dia da correção).
UPDATE pricetrack_daily SET price_basis = 'spot_legacy' WHERE price_basis IS NULL;

CREATE INDEX IF NOT EXISTS idx_ptd_price_basis ON pricetrack_daily(price_basis);
