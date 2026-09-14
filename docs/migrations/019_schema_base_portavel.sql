-- ---------------------------------------------------------------------------
-- 019_schema_base_portavel.sql — Base do schema para um Postgres QUALQUER.
--
-- POR QUE ESTE ARQUIVO EXISTE
-- ---------------------------
-- As migrações 001–018 sempre partiram de um pressuposto invisível: que as
-- tabelas `coletas`, `produtos_catalogo`, `produtos_aliases`,
-- `produtos_depara_nome` e `rac_products_magalu_shopee` JÁ EXISTIAM — elas
-- foram criadas à mão no painel do Supabase e nunca tiveram DDL versionado.
-- Enquanto houve um só banco isso passou despercebido. Na hora de levantar a
-- base em outro fornecedor, virou um buraco: `001_add_url_screenshot_columns`
-- faz ALTER TABLE numa tabela que não existe.
--
-- Este arquivo fecha o buraco. O DDL abaixo foi EXTRAÍDO do banco de produção
-- em 14/09/2026 (pg_catalog: format_type, pg_get_constraintdef, pg_indexes),
-- não escrito de memória — é a forma real das tabelas, não a imaginada.
--
-- ORDEM: rode este arquivo ANTES de 001. Depois dele, 001–018 aplicam na
-- ordem numérica normal.
--
-- Uso:
--     python scripts/db_bootstrap.py --dsn "$RAC_DB_DSN"
-- ---------------------------------------------------------------------------

-- ── 1. Papéis do Supabase ───────────────────────────────────────────────────
-- As migrações 012, 016, 017 e 018 fazem GRANT para `anon`, `authenticated` e
-- `service_role`. Esses papéis são criados pelo Supabase, não pelo Postgres:
-- em qualquer outro fornecedor eles não existem e o GRANT aborta a migração.
-- Criá-los aqui deixa as 18 migrações rodarem VERBATIM, sem fork de SQL — o
-- fork é que seria o erro, porque as duas cópias divergiriam na primeira
-- correção aplicada só de um lado.
--
-- `anon` é o papel de LEITURA do seller_app; a escrita segue exigindo o dono
-- do banco, exatamente como a policy do Supabase exigia `service_role`.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  -- Falha ALTO, de propósito. Seguir sem os papéis deixaria um schema pela
  -- metade: a 007 faz `ALTER ROLE service_role SET statement_timeout`, e a 015
  -- e a 016 criam POLICY ... TO anon — e essas duas criam, no mesmo arquivo,
  -- tabelas essenciais (`pipeline_heartbeat`, `seller_offer_daily`), então
  -- "pular a migração" não é uma saída. Abortar aqui, com o motivo na tela, é
  -- melhor que descobrir o buraco três migrações adiante.
  RAISE EXCEPTION
    'Sem permissão para criar os papéis anon/authenticated/service_role. '
    'O schema do RAC depende deles (007 faz ALTER ROLE; 015 e 016 criam '
    'POLICY ... TO anon). Use um usuário com permissão de CREATE ROLE — na '
    'Aiven é o avnadmin — ou um provedor que permita criar papéis.';
END
$$;

-- `unaccent` é usado por `unaccent_safe()` (migração 017) para canonizar nome
-- de seller. Sem a extensão a função cai no caminho degradado e "Friopeças" e
-- "Friopecas" viram sellers diferentes — o share de buy box volta a fatiar o
-- mesmo lojista, que é justamente o que utils/seller_names.py resolve.
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ── 2. coletas — o fato da janela quente ────────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS coletas_id_seq;

CREATE TABLE IF NOT EXISTS coletas (
  id bigint DEFAULT nextval('coletas_id_seq'::regclass) NOT NULL,
  data date NOT NULL,
  turno text,
  horario text,
  plataforma text NOT NULL,
  tipo text,
  keyword text,
  categoria text,
  marca text,
  produto text,
  posicao_organica integer,
  posicao_patrocinada integer,
  posicao_geral integer,
  preco numeric(10,2),
  seller text,
  fulfillment boolean,
  avaliacao numeric(3,2),
  qtd_avaliacoes integer,
  tag text,
  created_at timestamp with time zone DEFAULT now(),
  run_id uuid,
  url_produto text,
  screenshot_busca text,
  screenshot_produto text,
  familia_resolvida text,
  sku_resolvido text,
  estado_match text,
  voltagem_resolvida text,
  patrocinado boolean,
  buy_box_seller text,
  qtd_sellers integer,
  tipo_seller text,
  reputacao_seller text,
  produto_normalizado text,
  marketplace_product_id text,
  marketplace_offer_id text,
  seller_id text,
  canonical_url text,
  offer_key text
);
ALTER SEQUENCE coletas_id_seq OWNED BY coletas.id;

DO $$
BEGIN
  ALTER TABLE coletas ADD CONSTRAINT coletas_pkey PRIMARY KEY (id);
EXCEPTION WHEN duplicate_table OR invalid_table_definition THEN NULL;
END $$;

-- Chave do upsert da coleta (`on_conflict` em utils/supabase_client.py). É o
-- índice mais caro da tabela (76 MB) e o mais usado (2,9 mi de varreduras):
-- sem ele, cada reenvio de lote duplica linha em vez de atualizar.
DO $$
BEGIN
  ALTER TABLE coletas ADD CONSTRAINT coletas_unique_run
    UNIQUE (data, turno, plataforma, keyword, produto, run_id);
EXCEPTION WHEN duplicate_table THEN NULL;
END $$;

-- ── 3. Catálogo e de-para ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS produtos_catalogo (
  sku text NOT NULL,
  familia text NOT NULL,
  marca text NOT NULL,
  produto text NOT NULL,
  capacidade_btu integer,
  ciclo text,
  ativo boolean DEFAULT true NOT NULL,
  -- Coluna GERADA (stored), não um default: o valor é recalculado a cada
  -- escrita e não pode ser sobrescrito à mão. Se virasse DEFAULT, um UPDATE
  -- em `marca` deixaria a flag velha para trás e a base de Midea mentiria.
  is_midea_group boolean GENERATED ALWAYS AS (
    marca = ANY (ARRAY['MIDEA'::text, 'SPRINGER MIDEA'::text, 'CARRIER'::text])
  ) STORED,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL,
  familia_linha text,
  voltagem text,
  CONSTRAINT produtos_catalogo_pkey PRIMARY KEY (sku)
);

CREATE SEQUENCE IF NOT EXISTS produtos_aliases_id_seq;
CREATE TABLE IF NOT EXISTS produtos_aliases (
  id bigint DEFAULT nextval('produtos_aliases_id_seq'::regclass) NOT NULL,
  titulo_norm text NOT NULL,
  sku text NOT NULL,
  titulo_exemplo text,
  origem text DEFAULT 'seed'::text NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT produtos_aliases_pkey PRIMARY KEY (id),
  CONSTRAINT produtos_aliases_titulo_norm_key UNIQUE (titulo_norm),
  CONSTRAINT produtos_aliases_sku_fkey FOREIGN KEY (sku)
    REFERENCES produtos_catalogo(sku) ON UPDATE CASCADE ON DELETE RESTRICT
);
ALTER SEQUENCE produtos_aliases_id_seq OWNED BY produtos_aliases.id;

CREATE SEQUENCE IF NOT EXISTS produtos_depara_nome_id_seq;
CREATE TABLE IF NOT EXISTS produtos_depara_nome (
  id bigint DEFAULT nextval('produtos_depara_nome_id_seq'::regclass) NOT NULL,
  nome_coletado text NOT NULL,
  estado text NOT NULL,
  familia text,
  sku text,
  marca_norm text,
  origem text DEFAULT 'seed'::text NOT NULL,
  revisado_em timestamp with time zone,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  voltagem text,
  CONSTRAINT produtos_depara_nome_pkey PRIMARY KEY (id),
  CONSTRAINT produtos_depara_nome_nome_coletado_key UNIQUE (nome_coletado),
  CONSTRAINT produtos_depara_nome_sku_fkey FOREIGN KEY (sku)
    REFERENCES produtos_catalogo(sku),
  CONSTRAINT produtos_depara_nome_estado_check CHECK (
    estado = ANY (ARRAY['MAPEADO'::text, 'FORA_ESCOPO'::text, 'NAO_AC'::text, 'REVISAR'::text])
  )
);
ALTER SEQUENCE produtos_depara_nome_id_seq OWNED BY produtos_depara_nome.id;

CREATE SEQUENCE IF NOT EXISTS rac_products_magalu_shopee_id_seq;
CREATE TABLE IF NOT EXISTS rac_products_magalu_shopee (
  id bigint DEFAULT nextval('rac_products_magalu_shopee_id_seq'::regclass) NOT NULL,
  marketplace text NOT NULL,
  product_id text NOT NULL,
  sku text,
  search_query text NOT NULL,
  page_number integer NOT NULL,
  "position" integer NOT NULL,
  product_name text NOT NULL,
  brand text,
  product_type text,
  capacity_btu integer,
  current_price numeric(10,2),
  original_price numeric(10,2),
  discount_percentage integer,
  rating numeric(3,2),
  review_count integer DEFAULT 0,
  stock_status text DEFAULT 'Em estoque'::text,
  seller text,
  is_official boolean DEFAULT false,
  product_url text,
  image_url text,
  collected_at timestamp with time zone DEFAULT now() NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT rac_products_magalu_shopee_pkey PRIMARY KEY (id),
  CONSTRAINT rac_products_magalu_shopee_marketplace_check CHECK (
    marketplace = ANY (ARRAY['Magalu'::text, 'Shopee'::text])
  )
);
ALTER SEQUENCE rac_products_magalu_shopee_id_seq OWNED BY rac_products_magalu_shopee.id;

-- `rac_monitoramento` é a tabela ORIGINAL do projeto, anterior a `coletas`
-- (note `data text`, não `date`). Não recebe coleta nova há tempos, mas as
-- migrações 002 e 009 fazem ALTER/backfill nela e a view
-- `v_monitoramento_normalizado` a consulta — sem a tabela, a cadeia de
-- migrações para na 002. Nasce vazia na base nova: os 38 mil registros dela
-- são histórico frio e o lugar deles é o Parquet no Drive, não um free tier
-- de 1 GB.
CREATE SEQUENCE IF NOT EXISTS rac_monitoramento_id_seq;
CREATE TABLE IF NOT EXISTS rac_monitoramento (
  id bigint DEFAULT nextval('rac_monitoramento_id_seq'::regclass) NOT NULL,
  data text NOT NULL,
  turno text,
  horario text,
  analista text,
  plataforma text NOT NULL,
  tipo_plataforma text,
  keyword_buscada text,
  categoria_keyword text,
  marca_monitorada text,
  produto_sku text,
  posicao_organica integer,
  posicao_patrocinada integer,
  posicao_geral integer,
  preco numeric(10,2),
  seller_vendedor text,
  fulfillment text,
  avaliacao numeric(3,2),
  qtd_avaliacoes integer,
  tag_destaque text,
  created_at timestamp without time zone DEFAULT now(),
  familia_resolvida text,
  sku_resolvido text,
  estado_match text,
  voltagem_resolvida text,
  CONSTRAINT rac_monitoramento_pkey PRIMARY KEY (id),
  CONSTRAINT uc_registro UNIQUE (data, plataforma, keyword_buscada, produto_sku)
);
ALTER SEQUENCE rac_monitoramento_id_seq OWNED BY rac_monitoramento.id;

CREATE INDEX IF NOT EXISTS idx_data ON rac_monitoramento USING btree (data);
CREATE INDEX IF NOT EXISTS idx_plataforma ON rac_monitoramento USING btree (plataforma);
CREATE INDEX IF NOT EXISTS idx_marca ON rac_monitoramento USING btree (marca_monitorada);
CREATE INDEX IF NOT EXISTS idx_racmon_estado_match ON rac_monitoramento USING btree (estado_match);
CREATE INDEX IF NOT EXISTS idx_racmon_familia_resolvida ON rac_monitoramento USING btree (familia_resolvida);

-- ── 4. Índices de `coletas` ─────────────────────────────────────────────────
-- Em produção a tabela carrega 20 índices somando 162 MB — 34% do tamanho
-- dela. Num free tier de 1 GB isso é caro, então a lista abaixo foi PODADA
-- com base no uso real (pg_stat_user_indexes, medido em 14/09/2026). Os dois
-- podados estão no fim do arquivo, comentados, com o motivo.
CREATE INDEX IF NOT EXISTS coletas_data_idx ON coletas USING btree (data DESC);
CREATE INDEX IF NOT EXISTS coletas_marca_data_idx ON coletas USING btree (marca, data);
CREATE INDEX IF NOT EXISTS coletas_plataforma_data_idx ON coletas USING btree (plataforma, data);
CREATE INDEX IF NOT EXISTS idx_coletas_buybox_seller ON coletas USING btree (plataforma, data, buy_box_seller);
CREATE INDEX IF NOT EXISTS idx_coletas_data_turno_plat ON coletas USING btree (data, turno, plataforma, run_id);
CREATE INDEX IF NOT EXISTS idx_coletas_estado_match ON coletas USING btree (estado_match);
CREATE INDEX IF NOT EXISTS idx_coletas_familia_resolvida ON coletas USING btree (familia_resolvida);
CREATE INDEX IF NOT EXISTS idx_coletas_norm_pending ON coletas USING btree (estado_match, sku_resolvido) WHERE (produto_normalizado IS NULL);
CREATE INDEX IF NOT EXISTS idx_coletas_offer_key_data ON coletas USING btree (plataforma, offer_key, data);
CREATE INDEX IF NOT EXISTS idx_coletas_offer_turno ON coletas USING btree (data, turno, plataforma, offer_key);
CREATE INDEX IF NOT EXISTS idx_coletas_posicao_data_id ON coletas USING btree (posicao_geral, data DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_coletas_produto ON coletas USING btree (produto);
CREATE INDEX IF NOT EXISTS idx_coletas_produto_orphan ON coletas USING btree (produto) WHERE (sku_resolvido IS NULL);
CREATE INDEX IF NOT EXISTS idx_coletas_run_id ON coletas USING btree (run_id);
CREATE INDEX IF NOT EXISTS idx_coletas_seller ON coletas USING btree (seller);
CREATE INDEX IF NOT EXISTS idx_coletas_voltagem ON coletas USING btree (voltagem_resolvida);

-- PODADOS de propósito — não recriar sem medir antes:
--
-- idx_coletas_data ON coletas (data)
--   Duplica `coletas_data_idx`, que é a MESMA coluna. Um btree serve os dois
--   sentidos de ordenação, então o par nunca precisou existir. 3,3 MB e uma
--   escrita a mais por linha inserida, por nada.
--
-- idx_coletas_seller_id ON coletas (plataforma, seller_id, data) WHERE seller_id IS NOT NULL
--   1,5 MB e ZERO varreduras desde que foi criado (migração 014). Índice que
--   nunca foi lido só cobra: ocupa disco e encarece todo INSERT.

-- ── 5. Índices das demais tabelas ───────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_aliases_sku ON produtos_aliases USING btree (sku);
CREATE INDEX IF NOT EXISTS idx_catalogo_btu ON produtos_catalogo USING btree (capacidade_btu);
CREATE INDEX IF NOT EXISTS idx_catalogo_familia ON produtos_catalogo USING btree (familia);
CREATE INDEX IF NOT EXISTS idx_catalogo_familia_linha ON produtos_catalogo USING btree (familia_linha);
CREATE INDEX IF NOT EXISTS idx_catalogo_marca ON produtos_catalogo USING btree (marca);
CREATE INDEX IF NOT EXISTS idx_depara_estado ON produtos_depara_nome USING btree (estado);
CREATE INDEX IF NOT EXISTS idx_depara_familia ON produtos_depara_nome USING btree (familia);
CREATE INDEX IF NOT EXISTS idx_rac_ms_brand ON rac_products_magalu_shopee USING btree (brand);
CREATE INDEX IF NOT EXISTS idx_rac_ms_collected_at ON rac_products_magalu_shopee USING btree (collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_rac_ms_marketplace ON rac_products_magalu_shopee USING btree (marketplace);
CREATE INDEX IF NOT EXISTS idx_rac_ms_product_id ON rac_products_magalu_shopee USING btree (marketplace, product_id);
CREATE INDEX IF NOT EXISTS idx_rac_ms_search_query ON rac_products_magalu_shopee USING btree (search_query);

-- ── 6. Funções, gatilhos e views que nunca foram versionados ────────────────
-- Todos extraídos da produção em 14/09/2026 (pg_get_functiondef,
-- pg_get_triggerdef, pg_get_viewdef). O gatilho de `coletas` é o item mais
-- importante do arquivo inteiro: sem ele toda linha nova entra com
-- `estado_match` NULO, o de-para para de resolver e o painel passa a mostrar
-- "não mapeado" para produto que está mapeado — degradação silenciosa, sem
-- erro em lugar nenhum.

CREATE OR REPLACE FUNCTION public.fn_normaliza_titulo(p_titulo text)
 RETURNS text LANGUAGE sql IMMUTABLE
AS $function$
    SELECT regexp_replace(
             regexp_replace(
               regexp_replace(
                 regexp_replace(upper(coalesce(p_titulo,'')), '\s*/?\s*MP?[0-9]{6,}', '', 'g'),
                 '[0-9]{6,}', '', 'g'),
               '[^A-Z0-9]+', ' ', 'g'),
             '\s+', ' ', 'g')
$function$;

CREATE OR REPLACE FUNCTION public.fn_touch_updated_at()
 RETURNS trigger LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.fn_resolve_familia()
 RETURNS trigger LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.produto_sku IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT d.familia, d.sku, d.estado
      INTO NEW.familia_resolvida, NEW.sku_resolvido, NEW.estado_match
    FROM public.produtos_depara_nome d
    WHERE d.nome_coletado = NEW.produto_sku;
    RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION public.fn_resolve_familia_coletas()
 RETURNS trigger LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.produto IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT d.familia, d.sku, d.estado
      INTO NEW.familia_resolvida, NEW.sku_resolvido, NEW.estado_match
    FROM public.produtos_depara_nome d
    WHERE d.nome_coletado = NEW.produto;
    RETURN NEW;
END $function$;

DROP TRIGGER IF EXISTS trg_resolve_familia_coletas ON public.coletas;
CREATE TRIGGER trg_resolve_familia_coletas
  BEFORE INSERT ON public.coletas
  FOR EACH ROW EXECUTE FUNCTION fn_resolve_familia_coletas();

DROP TRIGGER IF EXISTS trg_resolve_familia ON public.rac_monitoramento;
CREATE TRIGGER trg_resolve_familia
  BEFORE INSERT ON public.rac_monitoramento
  FOR EACH ROW EXECUTE FUNCTION fn_resolve_familia();

DROP TRIGGER IF EXISTS trg_catalogo_touch ON public.produtos_catalogo;
CREATE TRIGGER trg_catalogo_touch
  BEFORE UPDATE ON public.produtos_catalogo
  FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();

-- Opções dos filtros do dashboard, materializadas. `refresh_filter_options()`
-- (migração 006) é quem atualiza.
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_filter_options_90d AS
 SELECT (SELECT array_agg(DISTINCT s.plataforma ORDER BY s.plataforma)
           FROM (SELECT coletas.plataforma FROM coletas
                  WHERE coletas.data >= (CURRENT_DATE - '90 days'::interval)
                    AND coletas.plataforma IS NOT NULL) s) AS platforms,
    (SELECT array_agg(DISTINCT s.tipo ORDER BY s.tipo)
           FROM (SELECT coletas.tipo FROM coletas
                  WHERE coletas.data >= (CURRENT_DATE - '90 days'::interval)
                    AND coletas.tipo IS NOT NULL) s) AS platform_types,
    (SELECT array_agg(DISTINCT s.marca ORDER BY s.marca)
           FROM (SELECT coletas.marca FROM coletas
                  WHERE coletas.data >= (CURRENT_DATE - '90 days'::interval)
                    AND coletas.marca IS NOT NULL) s) AS brands,
    (SELECT array_agg(DISTINCT s.keyword ORDER BY s.keyword)
           FROM (SELECT coletas.keyword FROM coletas
                  WHERE coletas.data >= (CURRENT_DATE - '90 days'::interval)
                    AND coletas.keyword IS NOT NULL) s) AS keywords,
    (SELECT array_agg(DISTINCT s.seller ORDER BY s.seller)
           FROM (SELECT coletas.seller FROM coletas
                  WHERE coletas.data >= (CURRENT_DATE - '90 days'::interval)
                    AND coletas.seller IS NOT NULL) s) AS sellers,
    now() AS refreshed_at;

CREATE OR REPLACE FUNCTION public.get_filter_options_fast(window_days integer DEFAULT 90)
 RETURNS jsonb LANGUAGE sql STABLE SET search_path TO 'public'
AS $function$
    SELECT jsonb_build_object(
        'platforms',      COALESCE(to_jsonb(platforms),      '[]'::jsonb),
        'platform_types', COALESCE(to_jsonb(platform_types), '[]'::jsonb),
        'brands',         COALESCE(to_jsonb(brands),         '[]'::jsonb),
        'keywords',       COALESCE(to_jsonb(keywords),       '[]'::jsonb),
        'sellers',        COALESCE(to_jsonb(sellers),        '[]'::jsonb),
        'refreshed_at',   refreshed_at
    )
    FROM mv_filter_options_90d;
$function$;

CREATE OR REPLACE FUNCTION public.get_cobertura_resolucao()
 RETURNS jsonb LANGUAGE sql STABLE SET search_path TO 'public'
AS $function$
    SELECT jsonb_build_object(
        'total',       count(*),
        'MAPEADO',     count(*) FILTER (WHERE estado_match = 'MAPEADO'),
        'FORA_ESCOPO', count(*) FILTER (WHERE estado_match = 'FORA_ESCOPO'),
        'NAO_AC',      count(*) FILTER (WHERE estado_match = 'NAO_AC'),
        'REVISAR',     count(*) FILTER (WHERE estado_match = 'REVISAR'),
        'NULL',        count(*) FILTER (WHERE estado_match IS NULL)
    )
    FROM coletas;
$function$;

CREATE OR REPLACE VIEW v_monitoramento_normalizado AS
 SELECT m.id, m.data, m.turno, m.horario, m.analista, m.plataforma,
    m.tipo_plataforma, m.keyword_buscada, m.categoria_keyword,
    m.marca_monitorada, m.produto_sku, m.posicao_organica,
    m.posicao_patrocinada, m.posicao_geral, m.preco, m.seller_vendedor,
    m.fulfillment, m.avaliacao, m.qtd_avaliacoes, m.tag_destaque, m.created_at,
    a.sku AS sku_resolvido,
    c.familia AS familia_resolvida,
    c.marca AS marca_resolvida,
    c.produto AS produto_canonico,
    c.capacidade_btu, c.ciclo, c.is_midea_group,
    a.sku IS NOT NULL AS match_ok
   FROM rac_monitoramento m
     LEFT JOIN produtos_aliases a ON a.titulo_norm = TRIM(BOTH FROM fn_normaliza_titulo(m.produto_sku))
     LEFT JOIN produtos_catalogo c ON c.sku = a.sku;

CREATE OR REPLACE VIEW v_rac_brand_position AS
 SELECT marketplace, brand, search_query,
    date((collected_at AT TIME ZONE 'America/Sao_Paulo'::text)) AS data_coleta,
    count(*) AS total_produtos,
    round(avg("position"), 1) AS posicao_media,
    min("position") AS melhor_posicao,
    round(avg(current_price), 2) AS preco_medio,
    min(current_price) AS menor_preco
   FROM rac_products_magalu_shopee
  WHERE brand IS NOT NULL
  GROUP BY marketplace, brand, search_query,
           (date((collected_at AT TIME ZONE 'America/Sao_Paulo'::text)))
  ORDER BY (date((collected_at AT TIME ZONE 'America/Sao_Paulo'::text))) DESC,
           marketplace, brand;

-- NÃO portado de propósito: `rls_auto_enable()`, um EVENT TRIGGER da
-- plataforma Supabase que liga RLS em toda tabela nova. É infraestrutura do
-- fornecedor, não do projeto, e event trigger exige superusuário — que
-- provedor gerenciado não concede. O RLS que o projeto realmente precisa
-- (leitura do seller_app) é declarado nas migrações 012, 016 e 018.
