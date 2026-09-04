-- Migração 016 — Fase 1 do Track Position Seller: o fato com sujeito SELLER.
--
-- Contexto: `docs/TRACK_POSITION_SELLER.md` §1.1. Hoje o sujeito analítico é
-- `marca_monitorada` e o seller é ATRIBUTO da oferta vencedora. O produto do
-- dealer inverte isso: o seller vira sujeito e a marca vira atributo do
-- portfólio. `coletas` já tem os índices certos, mas não tem o fato.
--
-- Duas tabelas, e a segunda existe por um motivo que não é óbvio:
--
--   seller_offer_daily     — o que foi OBSERVADO.
--   seller_coverage_daily  — o que era ESPERADO e se veio.
--
-- Tabela de fato não sabe dizer "isto não aconteceu": ausência não tem linha.
-- Sem a segunda, o Magalu bloqueado às 14:00 lê no painel do tenant como
-- "seus 12 concorrentes sumiram e você ganhou 100% da buy box" (§2.9). A
-- cobertura é o que transforma silêncio em INDETERMINADO em vez de zero.
--
-- Decisões de grão, todas conferidas contra a produção em 04/09/2026:
--
-- 1. POSIÇÃO É RELATIVA À KEYWORD e o grão não a carrega. Em 3 dias, 43,3%
--    dos grupos (data,turno,plataforma,offer_key) aparecem em mais de uma
--    keyword (média 2,85, máximo 40), e em 38,5% deles a posição varia mais de
--    5 lugares entre keywords. Colapsar para uma "posição" só seria inventar
--    um número que não existe. Por isso guardamos `posicao_melhor`,
--    `posicao_mediana` e `keywords_presente` — três fatos, nenhum deles
--    fingindo ser "a posição". Share of search precisa do grão de keyword e
--    é uma tabela separada, da Fase 2.
--
-- 2. BUY BOX SÓ EXISTE EM MARKETPLACE. Nas lojas próprias o buy box vem
--    preenchido em 100% das linhas e o vencedor é sempre o dono do site — ele
--    joga sozinho. Somar isso ao win rate faria o dealer aparecer ganhando
--    100% de um campeonato sem adversário. `superficie` (de
--    `utils/seller_surface.py`) é o que mantém loja própria fora do
--    denominador.
--
-- 3. `offer_key` ESTÁ 100% PREENCHIDA E NEM SEMPRE É ÚNICA. Duas plataformas
--    caem no degrau de hash de URL com uma URL de rodapé, e aí TUDO colapsa
--    numa chave só:
--       Google Shopping → canonical_url = .../googleshopping/answer/9128904
--                         (página de ajuda): 7.675 linhas, 566 produtos e
--                         190 sellers sob UMA chave — 100% da plataforma;
--       Mercado Livre   → canonical_url = publicidade.mercadolivre.com.br
--                         (página de publicidade): 4.315 linhas, 159 produtos,
--                         26 sellers — ~25% da plataforma.
--    Chave derivada que colapsa parece autoridade que o dado não tem, que é
--    exatamente o que `utils/offer_identity.py` existe para evitar. Enquanto o
--    coletor não for corrigido, `identidade_suspeita` marca a linha e ela FICA
--    FORA de win rate e de gap — visível, nunca somada em silêncio.
--
-- 4. A SERP SÓ MOSTRA O DETENTOR DA BUY BOX — não existe "perdedor" na linha.
--    Conferido em 04/09/2026 sobre 2 dias: em Magalu, Amazon, Shopee, Casas
--    Bahia, Leroy e Mercado Livre, `seller = buy_box_seller` em 100% das
--    linhas, e um produto tem UM seller observado por turno (Leroy: 17 grupos
--    com 2 em 1.854; ML: 1 em 1.205). O concorrente que perdeu a caixa
--    simplesmente não gera linha.
--
--    Consequências, e elas corrigem o esboço do §2.10 do documento:
--
--      * `venceu_buybox` como bandeira ganhou/perdeu seria SEMPRE verdadeira —
--        um número que não mede nada. A coluna virou `detentor_buybox`, que
--        diz o que de fato se observou: este seller detinha a caixa.
--      * Win rate não se calcula sobre as linhas do seller (daria 100%). Ele é
--        SHARE sobre o universo de produtos observados na plataforma — e por
--        isso mora na view `v_seller_buybox_share`, com denominador explícito.
--      * "Perder a buy box" não é observável numa foto; é observável na SÉRIE:
--        produto P com detentor A num turno e B no seguinte. É o que
--        `virou_no_turno` e `detentor_anterior` registram, e é o evento que o
--        alerta do tenant precisa.
--      * `gap_vs_buybox_pct` na linha seria sempre 0 — o preço da linha É o
--        preço da buy box, já que só o detentor aparece. O gap de verdade é
--        contra o preço do PRÓPRIO tenant, que é dado privado e vive no TPS
--        (§2.1). Fora do fato compartilhado, de propósito.
--
-- Aplicação:
--   psql "$SUPABASE_DB_URL" -f docs/migrations/016_seller_offer_daily.sql

-- ───────────────────────────────────────────────────────────────────────────
-- Referência de superfície. Fonte de verdade em `utils/seller_surface.py`;
-- esta tabela é o espelho que a SQL consegue ler.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plataforma_superficie (
    plataforma  text PRIMARY KEY,
    superficie  text NOT NULL
        CHECK (superficie IN ('marketplace', 'loja_propria', 'comparador')),
    atualizado_em timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE plataforma_superficie IS
    'Onde a disputa acontece. Espelho de utils/seller_surface.py; sincronizado '
    'por scripts/build_seller_offer_daily.py --sync-superficie. Plataforma '
    'ausente daqui e a superficie_de() default valem loja_propria, que é o '
    'lado seguro: loja propria fica FORA do win rate de buy box.';

-- ───────────────────────────────────────────────────────────────────────────
-- O fato com sujeito seller.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS seller_offer_daily (
    data                   date    NOT NULL,
    turno                  text    NOT NULL,
    plataforma             text    NOT NULL,
    seller_canonical       text    NOT NULL,
    offer_key              text    NOT NULL,   -- v1|... (versionada)

    superficie             text    NOT NULL,
    marketplace_product_id text,
    marca                  text,
    produto                text,

    -- Posição: três fatos, nenhum deles "a posição" (ver decisão 1).
    posicao_melhor         integer,
    posicao_mediana        numeric(6,2),
    keywords_presente      integer NOT NULL DEFAULT 0,
    patrocinado_em_alguma  boolean,

    -- Buy box: só interpretável onde superficie = 'marketplace'.
    -- Não há bandeira ganhou/perdeu — a SERP só mostra o detentor (decisão 4).
    detentor_buybox        boolean,
    detentor_anterior      text,               -- quem detinha no turno anterior
    virou_no_turno         boolean,            -- a caixa trocou de dono agora?
    qtd_sellers            integer,

    -- Preço do DETENTOR. Não existe `preco_buybox` separado: como só o
    -- detentor aparece, este preço é o preço da buy box. O gap que interessa
    -- é contra o preço do próprio tenant, que é privado e vive no TPS.
    preco                  numeric(12,2),

    tipo_seller            text,
    reputacao_seller       text,

    -- Portões de qualidade. Nenhum dos dois é cosmético: são o que impede o
    -- número de sair errado sem ninguém ver.
    identidade_suspeita    boolean NOT NULL DEFAULT false,
    produtos_na_chave      integer NOT NULL DEFAULT 1,

    observacoes            integer NOT NULL DEFAULT 0,
    atualizado_em          timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (data, turno, plataforma, offer_key, seller_canonical)
);

COMMENT ON COLUMN seller_offer_daily.posicao_melhor IS
    'Melhor (menor) posicao_geral entre as keywords em que a oferta apareceu '
    'no turno. NAO e "a posicao" da oferta: posicao e relativa a keyword.';
COMMENT ON COLUMN seller_offer_daily.keywords_presente IS
    'Em quantas keywords distintas a oferta apareceu no turno. E o que da '
    'sentido a posicao_melhor/mediana — 1 keyword e um numero, 40 e outro.';
COMMENT ON COLUMN seller_offer_daily.detentor_buybox IS
    'true = observou-se este seller detendo a buy box do produto no turno. '
    'NULL fora de marketplace e onde a plataforma nao expoe buy box (a SERP da '
    'Amazon e a da Casas Bahia nao trazem: 0% de preenchimento). NULL significa '
    'NAO OBSERVADO, nunca "perdeu" — nao existe linha de perdedor na SERP.';
COMMENT ON COLUMN seller_offer_daily.virou_no_turno IS
    'true quando o detentor mudou em relacao ao turno observado anterior do '
    'mesmo produto. E o evento de buy box que o alerta do tenant consome. '
    'NULL quando nao ha turno anterior observado — ausencia nao e virada.';
COMMENT ON COLUMN seller_offer_daily.identidade_suspeita IS
    'true quando a offer_key cobre mais de um produto distinto no mesmo '
    '(data,turno,plataforma) — colapso do degrau de hash de URL. Linha marcada '
    'NAO entra em win rate nem em gap; fica visivel para nao sumir em silencio.';

CREATE INDEX IF NOT EXISTS idx_sod_seller_data
    ON seller_offer_daily (seller_canonical, data DESC, plataforma);
CREATE INDEX IF NOT EXISTS idx_sod_offer_serie
    ON seller_offer_daily (offer_key, data);
-- O recorte que o painel do seller faz o tempo todo: minhas linhas confiáveis
-- onde a buy box de fato é disputada.
CREATE INDEX IF NOT EXISTS idx_sod_buybox_limpo
    ON seller_offer_daily (seller_canonical, data DESC)
    WHERE superficie = 'marketplace' AND NOT identidade_suspeita;

-- Sustenta a subconsulta de `detentor_anterior` na transformação. Sem ele, a
-- busca por "quem detinha a buy box deste produto na observação anterior" faz
-- varredura sequencial POR LINHA: com 900 linhas de teste não aparece, com
-- ~20 mil linhas/dia em produção a transformação estoura o timeout. Foi
-- exatamente o que aconteceu na primeira aplicação (04/09/2026).
CREATE INDEX IF NOT EXISTS idx_sod_detentor_anterior
    ON seller_offer_daily (plataforma, marketplace_product_id, data DESC, turno DESC)
    WHERE detentor_buybox AND marketplace_product_id IS NOT NULL;

-- ───────────────────────────────────────────────────────────────────────────
-- Cobertura: o que era esperado e se veio. Sem isto, ausência vira zero.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS seller_coverage_daily (
    data          date    NOT NULL,
    turno         text    NOT NULL,
    plataforma    text    NOT NULL,
    observado     boolean NOT NULL,          -- houve QUALQUER linha coletada?
    linhas        integer NOT NULL DEFAULT 0,
    ofertas       integer NOT NULL DEFAULT 0,
    heartbeat_ok  boolean,                   -- NULL = livro-razão não sabe
    job_id        text,
    atualizado_em timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data, turno, plataforma)
);

COMMENT ON TABLE seller_coverage_daily IS
    'Uma linha por (data,turno,plataforma) ESPERADO. observado=false significa '
    'INDETERMINADO: a leitura nao aconteceu. Toda leitura do tenant junta com '
    'esta tabela antes de mostrar numero ou disparar alerta — celula nao '
    'observada nunca vira zero, nunca vira "concorrente saiu" e nunca alerta.';
COMMENT ON COLUMN seller_coverage_daily.heartbeat_ok IS
    'Do pipeline_heartbeat, no grao do JOB (local_manha/tarde/noite) — nao por '
    'plataforma, porque o livro-razao nao tem esse grao. NULL = sem batida '
    'registrada, que se le como nao-sei, nunca como sucesso.';

-- ───────────────────────────────────────────────────────────────────────────
-- De-para de seller. Espelho de `utils/seller_names.SELLER_GROUPS`, para que a
-- SQL colapse as grafias sem depender do Python. Sem isto, Web Continental
-- vira 5 sellers e o share mente sobre quem lidera (§2.2 do documento).
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS seller_depara (
    variante_key text PRIMARY KEY,   -- grafia normalizada (sem caixa/acento)
    canonical    text NOT NULL
);

-- `unaccent` mora numa extensão que pode não estar instalada; esta tradução
-- explícita dos acentos do português evita depender dela.
CREATE OR REPLACE FUNCTION unaccent_safe(p text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT translate(coalesce(p, ''),
                     'áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ',
                     'aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC');
$$;

-- Normalização idêntica à de `utils.seller_names.seller_key`: sem acento, sem
-- caixa, sem pontuação. Divergir daqui refragmenta o share em silêncio.
CREATE OR REPLACE FUNCTION seller_key(p text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT regexp_replace(lower(unaccent_safe(coalesce(p, ''))), '[^a-z0-9]', '', 'g');
$$;

CREATE OR REPLACE FUNCTION seller_canonico(p text)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT coalesce(
        (SELECT d.canonical FROM seller_depara d WHERE d.variante_key = seller_key(p)),
        btrim(p)
    );
$$;

-- ───────────────────────────────────────────────────────────────────────────
-- A transformação. Idempotente: rodar duas vezes para a mesma data dá o mesmo
-- resultado. Roda no servidor — nenhuma linha de `coletas` sai do banco.
-- ───────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION refresh_seller_offer_daily(p_data date)
RETURNS TABLE (ofertas int, suspeitas int, coberturas int)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_ofertas int; v_susp int; v_cob int;
BEGIN
    -- Passo 1 — cobertura. PRIMEIRO, de propósito: se a coleta não veio, o
    -- fato fica vazio e é esta tabela que diz a diferença entre "não houve
    -- oferta" e "não olhamos".
    INSERT INTO seller_coverage_daily
        (data, turno, plataforma, observado, linhas, ofertas, heartbeat_ok, job_id, atualizado_em)
    SELECT c.data, c.turno, c.plataforma, true,
           count(*)::int, count(DISTINCT c.offer_key)::int, hb.ok, hb.job_id, now()
    FROM coletas c
    LEFT JOIN LATERAL (
        SELECT h.job_id, bool_or(h.status = 'SUCCESS') AS ok
        FROM pipeline_heartbeat h
        WHERE h.data_ref = c.data
          AND h.job_id = CASE lower(c.turno)
                             WHEN 'abertura'   THEN 'local_manha'
                             WHEN 'tarde'      THEN 'local_tarde'
                             WHEN 'fechamento' THEN 'local_noite'
                         END
        GROUP BY h.job_id
    ) hb ON true
    WHERE c.data = p_data
    GROUP BY c.data, c.turno, c.plataforma, hb.ok, hb.job_id
    ON CONFLICT (data, turno, plataforma) DO UPDATE SET
        observado = EXCLUDED.observado, linhas = EXCLUDED.linhas,
        ofertas = EXCLUDED.ofertas, heartbeat_ok = EXCLUDED.heartbeat_ok,
        job_id = EXCLUDED.job_id, atualizado_em = now();
    GET DIAGNOSTICS v_cob = ROW_COUNT;

    -- Passo 2 — o fato.
    WITH bruto AS (
        SELECT c.data, c.turno, c.plataforma, c.offer_key,
               seller_canonico(c.seller)         AS seller_canonical,
               seller_canonico(c.buy_box_seller) AS buybox_canonical,
               c.marketplace_product_id, c.marca, c.produto,
               c.posicao_geral, c.keyword, c.patrocinado, c.preco,
               c.qtd_sellers, c.tipo_seller, c.reputacao_seller
        FROM coletas c
        WHERE c.data = p_data
          AND c.offer_key IS NOT NULL
          AND c.seller IS NOT NULL AND btrim(c.seller) <> ''
    ),
    -- Portão de identidade: chave que cobre mais de um produto no mesmo turno
    -- colapsou (decisão 3). Conta ANTES de agregar, senão o colapso some
    -- dentro do próprio agregado.
    identidade AS (
        SELECT data, turno, plataforma, offer_key,
               count(DISTINCT coalesce(produto, '')) AS produtos_na_chave
        FROM bruto GROUP BY 1,2,3,4
    ),
    agregado AS (
        SELECT b.data, b.turno, b.plataforma, b.offer_key, b.seller_canonical,
               coalesce(ps.superficie, 'loja_propria') AS superficie,
               min(b.marketplace_product_id) AS marketplace_product_id,
               min(b.marca) AS marca, min(b.produto) AS produto,
               min(b.posicao_geral) AS posicao_melhor,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY b.posicao_geral)::numeric(6,2)
                   AS posicao_mediana,
               count(DISTINCT b.keyword)::int AS keywords_presente,
               bool_or(b.patrocinado) AS patrocinado_em_alguma,
               -- NULL fora de marketplace e onde a buy box não é exposta —
               -- nunca false, que se leria como "perdeu".
               CASE WHEN coalesce(ps.superficie,'loja_propria') = 'marketplace'
                         AND bool_or(b.buybox_canonical IS NOT NULL AND b.buybox_canonical <> '')
                    THEN bool_or(b.seller_canonical = b.buybox_canonical)
               END AS detentor_buybox,
               max(b.qtd_sellers) AS qtd_sellers,
               avg(b.preco)::numeric(12,2) AS preco,
               min(b.tipo_seller) AS tipo_seller,
               min(b.reputacao_seller) AS reputacao_seller,
               count(*)::int AS observacoes,
               max(i.produtos_na_chave) AS produtos_na_chave
        FROM bruto b
        JOIN identidade i USING (data, turno, plataforma, offer_key)
        LEFT JOIN plataforma_superficie ps ON ps.plataforma = b.plataforma
        GROUP BY b.data, b.turno, b.plataforma, b.offer_key, b.seller_canonical, ps.superficie
    ),
    -- Virada da buy box: quem detinha o MESMO produto na observação anterior.
    -- Sem produto identificado não dá para perguntar — fica NULL.
    com_anterior AS (
        SELECT a.*,
               (SELECT s.seller_canonical FROM seller_offer_daily s
                 WHERE s.plataforma = a.plataforma
                   AND s.marketplace_product_id IS NOT NULL
                   AND s.marketplace_product_id = a.marketplace_product_id
                   AND s.detentor_buybox
                   AND (s.data, s.turno) < (a.data, a.turno)
                 ORDER BY s.data DESC, s.turno DESC LIMIT 1) AS detentor_anterior
        FROM agregado a
    )
    INSERT INTO seller_offer_daily (
        data, turno, plataforma, seller_canonical, offer_key, superficie,
        marketplace_product_id, marca, produto,
        posicao_melhor, posicao_mediana, keywords_presente, patrocinado_em_alguma,
        detentor_buybox, detentor_anterior, virou_no_turno, qtd_sellers,
        preco, tipo_seller, reputacao_seller,
        identidade_suspeita, produtos_na_chave, observacoes, atualizado_em)
    SELECT data, turno, plataforma, seller_canonical, offer_key, superficie,
           marketplace_product_id, marca, produto,
           posicao_melhor, posicao_mediana, keywords_presente, patrocinado_em_alguma,
           detentor_buybox, detentor_anterior,
           CASE WHEN detentor_buybox AND detentor_anterior IS NOT NULL
                THEN detentor_anterior IS DISTINCT FROM seller_canonical END,
           qtd_sellers, preco, tipo_seller, reputacao_seller,
           produtos_na_chave > 1, produtos_na_chave, observacoes, now()
    FROM com_anterior
    ON CONFLICT (data, turno, plataforma, offer_key, seller_canonical) DO UPDATE SET
        superficie = EXCLUDED.superficie,
        marketplace_product_id = EXCLUDED.marketplace_product_id,
        marca = EXCLUDED.marca, produto = EXCLUDED.produto,
        posicao_melhor = EXCLUDED.posicao_melhor,
        posicao_mediana = EXCLUDED.posicao_mediana,
        keywords_presente = EXCLUDED.keywords_presente,
        patrocinado_em_alguma = EXCLUDED.patrocinado_em_alguma,
        detentor_buybox = EXCLUDED.detentor_buybox,
        detentor_anterior = EXCLUDED.detentor_anterior,
        virou_no_turno = EXCLUDED.virou_no_turno,
        qtd_sellers = EXCLUDED.qtd_sellers, preco = EXCLUDED.preco,
        tipo_seller = EXCLUDED.tipo_seller,
        reputacao_seller = EXCLUDED.reputacao_seller,
        identidade_suspeita = EXCLUDED.identidade_suspeita,
        produtos_na_chave = EXCLUDED.produtos_na_chave,
        observacoes = EXCLUDED.observacoes, atualizado_em = now();
    GET DIAGNOSTICS v_ofertas = ROW_COUNT;

    SELECT count(*)::int INTO v_susp
    FROM seller_offer_daily WHERE data = p_data AND identidade_suspeita;

    RETURN QUERY SELECT v_ofertas, v_susp, v_cob;
END;
$fn$;

-- ───────────────────────────────────────────────────────────────────────────
-- Share de buy box — o KPI do seller, com DENOMINADOR EXPLÍCITO.
--
-- Não é "das minhas linhas, quantas ganhei" (daria 100%, decisão 4). É: dos
-- produtos observados na plataforma naquele dia, em quantos EU detinha a
-- caixa. Identidade suspeita fica fora do numerador E do denominador — chave
-- colapsada não vira estatística.
-- ───────────────────────────────────────────────────────────────────────────
-- `security_invoker = true` NÃO é detalhe: por padrão a view roda com as
-- permissões de quem a criou e **ignora a RLS** das tabelas de baixo. Hoje a
-- policy é leitura-para-todos e isso não vazaria nada — mas na Fase 2, quando
-- a RLS passar a recortar por tenant, uma view sem esta cláusula entregaria as
-- linhas de todos os tenants a qualquer um. Melhor nascer certa.
CREATE OR REPLACE VIEW v_seller_buybox_share
WITH (security_invoker = true) AS
WITH universo AS (
    SELECT data, plataforma,
           count(DISTINCT marketplace_product_id) AS produtos_universo
    FROM seller_offer_daily
    WHERE superficie = 'marketplace' AND NOT identidade_suspeita
      AND marketplace_product_id IS NOT NULL
    GROUP BY 1,2
),
detidos AS (
    SELECT data, plataforma, seller_canonical,
           count(DISTINCT marketplace_product_id) AS produtos_detidos,
           count(*) FILTER (WHERE virou_no_turno) AS viradas_a_favor
    FROM seller_offer_daily
    WHERE superficie = 'marketplace' AND NOT identidade_suspeita
      AND marketplace_product_id IS NOT NULL AND detentor_buybox
    GROUP BY 1,2,3
)
SELECT d.data, d.plataforma, d.seller_canonical,
       d.produtos_detidos, u.produtos_universo, d.viradas_a_favor,
       round(100.0 * d.produtos_detidos / nullif(u.produtos_universo, 0), 2)
           AS share_buybox_pct
FROM detidos d JOIN universo u USING (data, plataforma);

COMMENT ON VIEW v_seller_buybox_share IS
    'Share de buy box por seller/plataforma/dia. Denominador = produtos '
    'observados na plataforma (nao linhas do seller). So marketplace, so '
    'identidade confiavel. Seller ausente do dia nao aparece: leia sempre '
    'junto de seller_coverage_daily para nao confundir ausencia com zero.';
-- ───────────────────────────────────────────────────────────────────────────
-- RLS — o padrão validado na migração 012: leitura liberada, escrita negada
-- por ausência de policy. O conteúdo é observação de vitrine pública, mas o
-- histórico consolidado é trabalho acumulado; a escrita segue exclusiva do
-- `service_role`, que ignora RLS.
-- ───────────────────────────────────────────────────────────────────────────
ALTER TABLE seller_offer_daily    ENABLE ROW LEVEL SECURITY;
ALTER TABLE seller_coverage_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE plataforma_superficie ENABLE ROW LEVEL SECURITY;
ALTER TABLE seller_depara         ENABLE ROW LEVEL SECURITY;

-- Os papéis `anon`/`authenticated` são do Supabase e não existem num Postgres
-- puro. Sem esta guarda a migração não roda fora do Supabase — e uma migração
-- que só dá para testar em produção não é testada.
DO $policies$
DECLARE
    t text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        RAISE NOTICE 'Papel anon ausente (Postgres puro): policies de leitura '
                     'nao criadas. RLS fica LIGADA e sem policy, o que nega '
                     'leitura a todo mundo menos ao dono — o lado seguro.';
        RETURN;
    END IF;
    FOREACH t IN ARRAY ARRAY['seller_offer_daily', 'seller_coverage_daily',
                             'plataforma_superficie', 'seller_depara']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_leitura', t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR SELECT TO anon, authenticated USING (true)',
            t || '_leitura', t);
    END LOOP;
END
$policies$;
