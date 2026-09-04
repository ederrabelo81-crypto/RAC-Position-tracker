-- Migração 017 — Correções na Fase 1, todas vindas da revisão do PR #351.
--
-- A 016 já está aplicada em produção; esta substitui as funções e a view, e
-- reprocessa o que a 016 gravou errado. Sete defeitos, na ordem de gravidade:
--
-- 1. TURNO COMPARADO COMO TEXTO — o pior deles, e gravou dado errado.
--    Alfabeticamente 'Abertura' < 'Fechamento' < 'Tarde'; cronologicamente é
--    Abertura(08h) < Tarde(14h) < Fechamento(20h). A busca pelo detentor
--    anterior usava `(data, turno) <` textual, então uma linha de Tarde casava
--    com o Fechamento do MESMO dia — seis horas no futuro — e uma de
--    Fechamento pulava a Tarde que de fato a precedeu. Todas as viradas de buy
--    box gravadas pela 016 saíram dessa ordem. Corrigido com `turno_ordinal`.
--
-- 2. COBERTURA NUNCA GRAVAVA `observado = false`. A 016 só criava linha onde
--    havia coleta, então a coluna existia e jamais era usada — a tabela não
--    conseguia dizer "esperávamos e não veio", que é a única razão de ela
--    existir (§2.9 do documento). Agora as células esperadas são
--    materializadas e marcadas.
--
-- 3. REFRESH DEIXAVA LINHA ÓRFÃ. Só havia upsert: se uma recoleta produzisse
--    menos ofertas, as antigas continuavam lá e entravam no denominador do
--    share. Agora a partição da data é reconstruída — o que sumiu da origem
--    some do fato.
--
-- 4. DENOMINADOR CONTAVA PRODUTO SEM BUY BOX OBSERVADA. Amazon e Casas Bahia
--    não expõem o vencedor (0% de preenchimento): seus produtos entravam no
--    universo sem que ninguém pudesse ser contado como detentor. Hoje não
--    distorce (essas duas não têm detentor algum, e a view as descarta no
--    JOIN), mas basta uma plataforma com preenchimento PARCIAL para o share
--    de todo mundo afundar. O universo agora exige buy box observada.
--
-- 5. PORTÃO DE IDENTIDADE OLHAVA O TÍTULO. Contava `produto` distinto por
--    chave. Onde há id de produto, dois títulos do mesmo item marcavam
--    suspeita falsa; o id é a identidade de verdade. O título continua como
--    recurso onde não há id — que é justamente o Google Shopping, onde o
--    colapso acontece.
--
-- 6. HEARTBEAT LIA `bool_or(status='SUCCESS')`. Uma tentativa que deu certo e
--    outra depois que falhou continuava lendo "ok". Agora vale a ÚLTIMA
--    tentativa do turno.
--
-- 7. `seller_key` DIVERGIA DO PYTHON. `utils.seller_names.seller_key` remove
--    sufixo de razão social (ltda, eireli) quando sobra nome suficiente; a SQL
--    não removia. "FRIGELAR LTDA" virava `frigelarltda` e não achava o de-para
--    — refragmentando o share exatamente como o módulo existe para evitar.

-- ───────────────────────────────────────────────────────────────────────────
-- 1. Ordem cronológica do turno. Sem isto, "anterior" é alfabético.
-- ───────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION turno_ordinal(p_turno text)
RETURNS int LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE lower(btrim(coalesce(p_turno, '')))
               WHEN 'abertura'   THEN 1   -- 08h
               WHEN 'tarde'      THEN 2   -- 14h
               WHEN 'fechamento' THEN 3   -- 20h
           END;
$$;

COMMENT ON FUNCTION turno_ordinal(text) IS
    'Ordem CRONOLOGICA do turno. NULL para turno desconhecido, de proposito: '
    'ordenar por um turno que ninguem classificou e pior que nao ordenar.';

-- ───────────────────────────────────────────────────────────────────────────
-- 7. Paridade com utils.seller_names.seller_key — sufixo de razão social.
-- ───────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION seller_key(p text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    WITH base AS (
        SELECT regexp_replace(lower(unaccent_safe(coalesce(p, ''))), '[^a-z0-9]', '', 'g') AS k
    )
    -- Mesma regra do Python: só remove se sobrar nome com folga (len > sufixo+2),
    -- senão "Ltda Materiais" viraria string vazia.
    SELECT CASE
        WHEN k ~ 'ltda$'   AND length(k) > 6 THEN left(k, length(k) - 4)
        WHEN k ~ 'eireli$' AND length(k) > 8 THEN left(k, length(k) - 6)
        ELSE k
    END FROM base;
$$;

-- ───────────────────────────────────────────────────────────────────────────
-- A transformação, corrigida.
-- ───────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION refresh_seller_offer_daily(p_data date)
RETURNS TABLE (ofertas int, suspeitas int, coberturas int)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_ofertas int; v_susp int; v_cob int;
BEGIN
    -- ── Passo 1: cobertura ────────────────────────────────────────────────
    -- Esperado × observado. O "esperado" vem da própria história: plataforma
    -- vista nos 7 dias anteriores é plataforma que deveria aparecer hoje.
    -- É data-driven de propósito — uma lista fixa em SQL divergiria de
    -- config.ACTIVE_PLATFORMS sem ninguém perceber.
    WITH esperado AS (
        SELECT DISTINCT c.plataforma, t.turno
        FROM coletas c
        CROSS JOIN (VALUES ('Abertura'),('Tarde'),('Fechamento')) t(turno)
        WHERE c.data BETWEEN p_data - 7 AND p_data
    ),
    observado AS (
        SELECT c.plataforma, c.turno,
               count(*)::int AS linhas,
               count(DISTINCT c.offer_key)::int AS ofertas
        FROM coletas c
        WHERE c.data = p_data
        GROUP BY 1,2
    ),
    -- 6. A ÚLTIMA tentativa do turno manda, não "alguma deu certo".
    hb AS (
        SELECT DISTINCT ON (job_id) job_id, status = 'SUCCESS' AS ok
        FROM pipeline_heartbeat
        WHERE data_ref = p_data AND status <> 'STARTED'
        ORDER BY job_id, id DESC
    )
    INSERT INTO seller_coverage_daily
        (data, turno, plataforma, observado, linhas, ofertas, heartbeat_ok, job_id, atualizado_em)
    SELECT p_data, e.turno, e.plataforma,
           coalesce(o.linhas, 0) > 0,
           coalesce(o.linhas, 0), coalesce(o.ofertas, 0),
           hb.ok,
           CASE e.turno WHEN 'Abertura' THEN 'local_manha'
                        WHEN 'Tarde' THEN 'local_tarde'
                        ELSE 'local_noite' END,
           now()
    FROM esperado e
    LEFT JOIN observado o ON o.plataforma = e.plataforma AND o.turno = e.turno
    LEFT JOIN hb ON hb.job_id = CASE e.turno
                                    WHEN 'Abertura' THEN 'local_manha'
                                    WHEN 'Tarde' THEN 'local_tarde'
                                    ELSE 'local_noite' END
    ON CONFLICT (data, turno, plataforma) DO UPDATE SET
        observado = EXCLUDED.observado, linhas = EXCLUDED.linhas,
        ofertas = EXCLUDED.ofertas, heartbeat_ok = EXCLUDED.heartbeat_ok,
        job_id = EXCLUDED.job_id, atualizado_em = now();
    GET DIAGNOSTICS v_cob = ROW_COUNT;

    -- ── Passo 2: o fato ───────────────────────────────────────────────────
    -- `ON COMMIT DROP` só limpa no COMMIT: duas chamadas na MESMA transação
    -- (um SELECT ... UNION ALL de várias datas, por exemplo) encontrariam a
    -- temp table de pé e falhariam com "relation _novo already exists".
    DROP TABLE IF EXISTS _novo;
    CREATE TEMP TABLE _novo ON COMMIT DROP AS
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
    -- 5. Identidade: o id do produto é a verdade; o título só onde não há id
    -- (Google Shopping, onde o colapso de fato acontece).
    identidade AS (
        SELECT data, turno, plataforma, offer_key,
               greatest(
                   count(DISTINCT marketplace_product_id),
                   CASE WHEN count(marketplace_product_id) = 0
                        THEN count(DISTINCT nullif(btrim(coalesce(produto,'')), ''))
                        ELSE 0 END
               ) AS produtos_na_chave
        FROM bruto GROUP BY 1,2,3,4
    )
    SELECT b.data, b.turno, b.plataforma, b.offer_key, b.seller_canonical,
           coalesce(ps.superficie, 'loja_propria') AS superficie,
           min(b.marketplace_product_id) AS marketplace_product_id,
           min(b.marca) AS marca, min(b.produto) AS produto,
           min(b.posicao_geral) AS posicao_melhor,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY b.posicao_geral)::numeric(6,2)
               AS posicao_mediana,
           count(DISTINCT b.keyword)::int AS keywords_presente,
           bool_or(b.patrocinado) AS patrocinado_em_alguma,
           CASE WHEN coalesce(ps.superficie,'loja_propria') = 'marketplace'
                     AND bool_or(b.buybox_canonical IS NOT NULL AND b.buybox_canonical <> '')
                THEN bool_or(b.seller_canonical = b.buybox_canonical)
           END AS detentor_buybox,
           max(b.qtd_sellers) AS qtd_sellers,
           avg(b.preco)::numeric(12,2) AS preco,
           min(b.tipo_seller) AS tipo_seller,
           min(b.reputacao_seller) AS reputacao_seller,
           count(*)::int AS observacoes,
           greatest(max(i.produtos_na_chave), 1) AS produtos_na_chave
    FROM bruto b
    JOIN identidade i USING (data, turno, plataforma, offer_key)
    LEFT JOIN plataforma_superficie ps ON ps.plataforma = b.plataforma
    GROUP BY b.data, b.turno, b.plataforma, b.offer_key, b.seller_canonical, ps.superficie;

    -- 3. Reconstrói a partição: o que sumiu da origem some do fato.
    DELETE FROM seller_offer_daily s
    WHERE s.data = p_data
      AND NOT EXISTS (
          SELECT 1 FROM _novo n
          WHERE n.turno = s.turno AND n.plataforma = s.plataforma
            AND n.offer_key = s.offer_key AND n.seller_canonical = s.seller_canonical);

    -- 1. Detentor anterior pela ordem CRONOLÓGICA, olhando o dia corrente
    -- (que já está em `_novo`) antes de olhar o histórico gravado.
    INSERT INTO seller_offer_daily (
        data, turno, plataforma, seller_canonical, offer_key, superficie,
        marketplace_product_id, marca, produto,
        posicao_melhor, posicao_mediana, keywords_presente, patrocinado_em_alguma,
        detentor_buybox, detentor_anterior, virou_no_turno, qtd_sellers,
        preco, tipo_seller, reputacao_seller,
        identidade_suspeita, produtos_na_chave, observacoes, atualizado_em)
    SELECT n.data, n.turno, n.plataforma, n.seller_canonical, n.offer_key, n.superficie,
           n.marketplace_product_id, n.marca, n.produto,
           n.posicao_melhor, n.posicao_mediana, n.keywords_presente, n.patrocinado_em_alguma,
           n.detentor_buybox, ant.detentor,
           CASE WHEN n.detentor_buybox AND ant.detentor IS NOT NULL
                THEN ant.detentor IS DISTINCT FROM n.seller_canonical END,
           n.qtd_sellers, n.preco, n.tipo_seller, n.reputacao_seller,
           n.produtos_na_chave > 1, n.produtos_na_chave, n.observacoes, now()
    FROM _novo n
    LEFT JOIN LATERAL (
        SELECT x.seller_canonical AS detentor FROM (
            -- turnos anteriores do MESMO dia, que a 016 não enxergava
            SELECT m.seller_canonical, m.data, turno_ordinal(m.turno) ord
            FROM _novo m
            WHERE m.plataforma = n.plataforma
              AND m.marketplace_product_id IS NOT NULL
              AND m.marketplace_product_id = n.marketplace_product_id
              AND m.detentor_buybox
              AND turno_ordinal(m.turno) < turno_ordinal(n.turno)
            UNION ALL
            -- dias anteriores já gravados
            SELECT s.seller_canonical, s.data, turno_ordinal(s.turno)
            FROM seller_offer_daily s
            WHERE s.plataforma = n.plataforma
              AND s.marketplace_product_id IS NOT NULL
              AND s.marketplace_product_id = n.marketplace_product_id
              AND s.detentor_buybox AND s.data < n.data
        ) x
        WHERE n.marketplace_product_id IS NOT NULL
        ORDER BY x.data DESC, x.ord DESC
        LIMIT 1
    ) ant ON true
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
-- 4. Universo só com buy box observada.
-- ───────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_seller_buybox_share
WITH (security_invoker = true) AS
WITH universo AS (
    SELECT data, plataforma,
           count(DISTINCT marketplace_product_id) AS produtos_universo
    FROM seller_offer_daily
    WHERE superficie = 'marketplace' AND NOT identidade_suspeita
      AND marketplace_product_id IS NOT NULL
      AND detentor_buybox IS NOT NULL   -- buy box NAO observada nao e denominador
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
    'Share de buy box por seller/plataforma/dia. Denominador = produtos com buy '
    'box OBSERVADA na plataforma (nao linhas do seller, e nao produto de '
    'plataforma que nao expoe vencedor). So marketplace, so identidade '
    'confiavel. Seller ausente do dia nao aparece: leia junto de '
    'seller_coverage_daily para nao confundir ausencia com zero.';
