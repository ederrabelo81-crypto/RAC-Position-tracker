-- Migração 014b — BACKFILL da identidade de oferta no histórico de `coletas`.
--
-- A 014 criou as colunas; esta preenche o histórico anterior à Fase 1
-- derivando a identidade da `url_produto` que já estava gravada.
--
-- ⚠️ JÁ APLICADA EM PRODUÇÃO em 26/08/2026 (376.289 de 379.848 linhas, 99,06%).
-- Este arquivo é o registro auditável do que rodou. É idempotente
-- (`WHERE offer_key IS NULL`), então re-executar não refaz trabalho.
--
-- ── Por que SQL e não o módulo Python ──────────────────────────────────────
-- `utils/offer_identity.py` é a fonte da verdade para a coleta CORRENTE e
-- continua sendo. Este SQL existe só para a passada única no histórico, onde
-- trazer 380 mil linhas para fora do banco e devolvê-las seria muito mais
-- lento e frágil. Para garantir que as duas implementações concordam, a
-- derivação SQL foi validada campo a campo contra o módulo Python numa
-- amostra estratificada de URLs REAIS das 7 plataformas (incluindo os dois
-- namespaces do Mercado Livre e a página de ajuda do Google Shopping):
-- 75 campos comparados, 100% idênticos. As funções auxiliares são criadas com
-- prefixo `_bf_` e DROPADAS ao final — não sobra implementação paralela viva.
--
-- ── O que NÃO foi preenchido, de propósito ─────────────────────────────────
-- 3.559 linhas (0,94%, todas da Casas Bahia) não têm `url_produto`. O degrau
-- final da escada da `offer_key` é o hash do TÍTULO CRU, e o banco só guarda
-- `produto` (já normalizado por `normalize_product_name`). Uma chave derivada
-- do título normalizado pareceria comparável com as chaves da coleta corrente
-- e NÃO seria — o erro mais caro possível numa coluna de identidade. Essas
-- linhas ficam com `offer_key` NULL, que é a informação verdadeira:
-- desconhecido.
--
-- Aplicação:
--   psql "$SUPABASE_DB_URL" -f docs/migrations/014b_backfill_offer_identity.sql

-- ── 1. Funções auxiliares temporárias (espelham utils/offer_identity.py) ────

CREATE OR REPLACE FUNCTION _bf_slug(plat text) RETURNS text AS $$
  SELECT COALESCE(NULLIF(regexp_replace(
    upper(translate(COALESCE(btrim(plat), ''),
      'ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ', 'AAAAAEEEEIIIIOOOOOUUUUC')),
    '[^A-Z0-9]', '', 'g'), ''), 'desconhecida');
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION _bf_canonical(u text, plat text) RETURNS text AS $$
DECLARE nofrag text; rest text; host text; path text; q text; kept text;
BEGIN
  IF u IS NULL OR btrim(u) = '' THEN RETURN NULL; END IF;
  IF lower(btrim(u)) !~ '^https?://' THEN RETURN NULL; END IF;
  nofrag := split_part(btrim(u), '#', 1);
  rest   := regexp_replace(nofrag, '^https?://', '');
  host   := lower(split_part(split_part(rest, '/', 1), '?', 1));
  IF host = '' THEN RETURN NULL; END IF;
  IF host LIKE 'www.www.%' THEN host := substring(host from 5); END IF;
  host := CASE host
    WHEN 'm.magazineluiza.com.br'      THEN 'www.magazineluiza.com.br'
    WHEN 'magazineluiza.com.br'        THEN 'www.magazineluiza.com.br'
    WHEN 'produto.mercadolivre.com.br' THEN 'www.mercadolivre.com.br'
    WHEN 'mercadolivre.com.br'         THEN 'www.mercadolivre.com.br'
    WHEN 'casasbahia.com.br'           THEN 'www.casasbahia.com.br'
    WHEN 'amazon.com.br'               THEN 'www.amazon.com.br'
    WHEN 'leroymerlin.com.br'          THEN 'www.leroymerlin.com.br'
    ELSE host END;
  path := split_part(substring(rest from length(split_part(rest, '/', 1)) + 1), '?', 1);
  IF path = '' THEN path := '/'; END IF;
  IF host LIKE '%amazon%' THEN path := regexp_replace(path, '/ref=[^/]*$', '', 'i'); END IF;
  IF length(path) > 1 AND right(path, 1) = '/' THEN
    path := regexp_replace(path, '/+$', '');
    IF path = '' THEN path := '/'; END IF;
  END IF;
  q := split_part(rest, '?', 2);
  -- idlojista/seller_id saem da URL canônica: identificam o SELLER, não o
  -- produto. Sem isso a mesma oferta vira duas quando a vitrine anexa o
  -- lojista numa coleta e não na outra.
  kept := (
    SELECT string_agg(kv, '&' ORDER BY kv)
    FROM unnest(string_to_array(nullif(q, ''), '&')) AS kv
    WHERE kv <> '' AND split_part(kv, '=', 2) <> ''
      AND lower(split_part(kv, '=', 1)) NOT IN (
        'ref','ref_','tag','utm_source','utm_medium','utm_campaign','utm_term',
        'utm_content','utm_id','gclid','fbclid','msclkid','pdp_filters',
        'search_layout','position','type','tracking_id','wid','sid','psc','th',
        'linkcode','creative','creativeasin','ascsubtag','smid','qid','sr',
        'keywords','source','srsltid','cor','sellerid','idlojista','seller_id',
        'partner_id')
  );
  RETURN 'https://' || host || path
      || CASE WHEN kept IS NULL OR kept = '' THEN '' ELSE '?' || kept END;
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION _bf_derive(u text, plat text,
  OUT pid text, OUT oid text, OUT sid text) AS $$
DECLARE slug text; q text; item text;
BEGIN
  pid := NULL; oid := NULL; sid := NULL;
  IF u IS NULL OR btrim(u) = '' THEN RETURN; END IF;
  slug := _bf_slug(plat);
  q := lower(split_part(u, '?', 2));

  IF slug = 'AMAZON' THEN
    pid := substring(u from '/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)');

  ELSIF slug = 'MERCADOLIVRE' THEN
    -- Dois namespaces distintos: /p/MLB… é catálogo (produto, buy box
    -- disputada); /MLB-… e /up/MLBU… são anúncios de um seller (oferta).
    pid := upper(substring(u from '(?i)/p/(MLB[0-9]+)(?:[/?]|$)'));
    item := substring(u from '(?i)/(MLB-?[0-9]+|up/MLBU[0-9]+)(?:[-/?]|$)');
    IF item IS NOT NULL THEN
      item := upper(replace(replace(item, '-', ''), 'up/', ''));
      IF item IS DISTINCT FROM upper(COALESCE(pid, '')) THEN oid := item; END IF;
    END IF;

  ELSIF slug = 'MAGALU' THEN
    pid := upper(substring(u from '(?i)/p/([a-z0-9]{6,})(?:[/?]|$)'));
    sid := nullif(substring(q from '(?:^|&)seller_id=([^&]+)'), '');

  ELSIF slug = 'CASASBAHIA' THEN
    pid := substring(u from '/p/([0-9]{5,})(?:[/?]|$)');
    sid := nullif(substring(q from '(?:^|&)idlojista=([^&]+)'), '');

  ELSIF slug = 'SHOPEE' THEN
    sid := substring(u from '/product/([0-9]+)/[0-9]+(?:[/?]|$)');
    pid := substring(u from '/product/[0-9]+/([0-9]+)(?:[/?]|$)');
    IF sid IS NOT NULL AND pid IS NOT NULL THEN oid := sid || '_' || pid; END IF;

  ELSIF slug = 'LEROYMERLIN' THEN
    pid := substring(u from '_([0-9]{6,})(?:[/?]|$)');
  END IF;
END $$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION _bf_key(plat text, oid text, pid text, sid text, curl text)
RETURNS text AS $$
DECLARE slug text; sufixo text;
BEGIN
  slug := _bf_slug(plat);
  IF oid IS NOT NULL AND oid <> '' THEN
    RETURN 'v1|' || slug || '|offer:' || oid; END IF;
  IF pid IS NOT NULL AND pid <> '' AND sid IS NOT NULL AND sid <> '' THEN
    RETURN 'v1|' || slug || '|prod:' || pid || '@' || sid; END IF;
  IF pid IS NOT NULL AND pid <> '' THEN
    RETURN 'v1|' || slug || '|prod:' || pid; END IF;
  -- O seller entra também no degrau derivado: sem isso dois lojistas na mesma
  -- página de produto colapsam numa série só.
  sufixo := CASE WHEN sid IS NOT NULL AND sid <> '' THEN '@' || sid ELSE '' END;
  IF curl IS NOT NULL AND curl <> '' THEN
    RETURN 'v1|' || slug || '|url:'
        || substr(encode(digest(curl, 'sha1'), 'hex'), 1, 16) || sufixo;
  END IF;
  RETURN NULL;  -- sem sinal algum: desconhecido, não inventado
END $$ LANGUAGE plpgsql IMMUTABLE;

-- ── 2. Mapa: deriva UMA vez por URL distinta ───────────────────────────────
-- 379.848 linhas, mas só 30.133 pares (plataforma, url) distintos. Sem este
-- passo a derivação roda 1,5M de vezes e a transação estoura o timeout.

DROP TABLE IF EXISTS _bf_map;
CREATE TABLE _bf_map AS
SELECT plataforma, url_produto,
       (d).pid AS pid, (d).oid AS oid, (d).sid AS sid, curl,
       _bf_key(plataforma, (d).oid, (d).pid, (d).sid, curl) AS okey
FROM (
  SELECT plataforma, url_produto,
         _bf_derive(url_produto, plataforma) AS d,
         _bf_canonical(url_produto, plataforma) AS curl
  FROM (SELECT DISTINCT plataforma, url_produto
          FROM coletas WHERE url_produto IS NOT NULL) s
) t;
CREATE UNIQUE INDEX _bf_map_pk ON _bf_map (plataforma, url_produto);

-- ── 3. Backfill em lotes por faixa de id ───────────────────────────────────
-- Lotes são necessários: um UPDATE único em 380 mil linhas estoura o limite
-- de tempo da conexão. `offer_key IS NULL` torna cada lote idempotente e
-- permite retomar de onde parou.

UPDATE coletas c SET
  marketplace_product_id = m.pid, marketplace_offer_id = m.oid,
  seller_id = m.sid, canonical_url = m.curl, offer_key = m.okey
FROM _bf_map m
WHERE c.plataforma = m.plataforma AND c.url_produto = m.url_produto
  AND c.id < 1700000 AND c.offer_key IS NULL;

UPDATE coletas c SET
  marketplace_product_id = m.pid, marketplace_offer_id = m.oid,
  seller_id = m.sid, canonical_url = m.curl, offer_key = m.okey
FROM _bf_map m
WHERE c.plataforma = m.plataforma AND c.url_produto = m.url_produto
  AND c.id >= 1700000 AND c.id < 1900000 AND c.offer_key IS NULL;

UPDATE coletas c SET
  marketplace_product_id = m.pid, marketplace_offer_id = m.oid,
  seller_id = m.sid, canonical_url = m.curl, offer_key = m.okey
FROM _bf_map m
WHERE c.plataforma = m.plataforma AND c.url_produto = m.url_produto
  AND c.id >= 1900000 AND c.id < 2020000 AND c.offer_key IS NULL;

UPDATE coletas c SET
  marketplace_product_id = m.pid, marketplace_offer_id = m.oid,
  seller_id = m.sid, canonical_url = m.curl, offer_key = m.okey
FROM _bf_map m
WHERE c.plataforma = m.plataforma AND c.url_produto = m.url_produto
  AND c.id >= 2020000 AND c.offer_key IS NULL;

-- ── 4. Limpeza: nenhuma implementação paralela sobrevive ao backfill ───────

DROP TABLE IF EXISTS _bf_map;
DROP FUNCTION IF EXISTS _bf_key(text,text,text,text,text);
DROP FUNCTION IF EXISTS _bf_derive(text,text);
DROP FUNCTION IF EXISTS _bf_canonical(text,text);
DROP FUNCTION IF EXISTS _bf_slug(text);

-- ── 5. Verificação ────────────────────────────────────────────────────────
--   SELECT count(*) total, count(offer_key) com_chave,
--          count(*) FILTER (WHERE offer_key IS NULL AND url_produto IS NOT NULL) faltando
--   FROM coletas;
-- Esperado após a execução de 26/08/2026:
--   total 379.848 | com_chave 376.289 (99,06%) | faltando 0
