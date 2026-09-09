-- 018_seller_offer_daily_amazon_buybox.sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Amazon (e qualquer plataforma cujo `seller` da SERP é só o nome da própria
-- loja) sumia do seller_app. Causa: na Amazon o "Vendido por" não aparece na
-- SERP, só no PDP, então o coletor grava SEMPRE `seller = 'Amazon'` (placeholder)
-- e põe o vendedor REAL (Web Continental, Engage Eletro, Leveros, Frigelar,
-- Dufrio, Denteck…) apenas em `buy_box_seller`.
--
-- A refresh_seller_offer_daily usava `seller_canonico(seller)` como identidade,
-- então TODA oferta da Amazon virava o vendedor-fantasma "Amazon"; como
-- "Amazon" != buy_box_seller, `detentor_buybox` nunca era true, e a view
-- v_seller_buybox_share (que exige detentor_buybox = true) não tinha NENHUMA
-- linha da Amazon — logo a Amazon não entrava no seletor de seller, na aba de
-- Share nem no Ranking.
--
-- Correção (só a expressão de `seller_canonical` no CTE `bruto` muda): quando o
-- `seller` for o próprio nome da plataforma (placeholder) E houver
-- `buy_box_seller`, usar o vendedor da buy box como identidade da oferta. Assim
-- a oferta é atribuída ao vendedor real e `detentor_buybox` vira true
-- naturalmente (seller_canonical = buybox_canonical). Seguro para as outras
-- plataformas: quando o `seller` já traz o vendedor real (ML/Magalu/Shopee/
-- Leroy nas linhas não-placeholder), o CASE cai no ELSE e nada muda; quando o
-- placeholder é 1P (ex.: "Leroy Merlin"/"Mercado Livre" vendendo direto, com
-- buy_box_seller = o mesmo nome), a identidade continua a mesma.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION refresh_seller_offer_daily(p_data date)
RETURNS TABLE (ofertas int, suspeitas int, coberturas int)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_ofertas int; v_susp int; v_cob int;
BEGIN
    WITH esperado AS (
        SELECT DISTINCT c.plataforma, t.turno
        FROM coletas c
        CROSS JOIN (VALUES ('Abertura'),('Tarde'),('Fechamento')) t(turno)
        WHERE c.data BETWEEN p_data - 7 AND p_data
    ),
    observado AS (
        SELECT c.plataforma, c.turno, count(*)::int AS linhas,
               count(DISTINCT c.offer_key)::int AS ofertas
        FROM coletas c WHERE c.data = p_data GROUP BY 1,2
    ),
    hb AS (
        SELECT DISTINCT ON (job_id) job_id, status = 'SUCCESS' AS ok
        FROM pipeline_heartbeat
        WHERE data_ref = p_data AND status <> 'STARTED'
        ORDER BY job_id, id DESC
    )
    INSERT INTO seller_coverage_daily
        (data, turno, plataforma, observado, linhas, ofertas, heartbeat_ok, job_id, atualizado_em)
    SELECT p_data, e.turno, e.plataforma,
           coalesce(o.linhas, 0) > 0, coalesce(o.linhas, 0), coalesce(o.ofertas, 0), hb.ok,
           CASE e.turno WHEN 'Abertura' THEN 'local_manha'
                        WHEN 'Tarde' THEN 'local_tarde' ELSE 'local_noite' END,
           now()
    FROM esperado e
    LEFT JOIN observado o ON o.plataforma = e.plataforma AND o.turno = e.turno
    LEFT JOIN hb ON hb.job_id = CASE e.turno WHEN 'Abertura' THEN 'local_manha'
                                             WHEN 'Tarde' THEN 'local_tarde'
                                             ELSE 'local_noite' END
    ON CONFLICT (data, turno, plataforma) DO UPDATE SET
        observado = EXCLUDED.observado, linhas = EXCLUDED.linhas,
        ofertas = EXCLUDED.ofertas, heartbeat_ok = EXCLUDED.heartbeat_ok,
        job_id = EXCLUDED.job_id, atualizado_em = now();
    GET DIAGNOSTICS v_cob = ROW_COUNT;

    DROP TABLE IF EXISTS _novo;
    CREATE TEMP TABLE _novo ON COMMIT DROP AS
    WITH bruto AS (
        SELECT c.data, c.turno, c.plataforma, c.offer_key,
               -- ⬇ ÚNICA mudança da 017: na Amazon o vendedor real só existe em
               -- buy_box_seller; quando o `seller` é o próprio nome da plataforma
               -- (placeholder) e há buy_box_seller, a identidade da oferta é o
               -- vendedor da buy box — senão tudo vira o fantasma "Amazon".
               seller_canonico(
                   CASE WHEN seller_key(c.seller) = seller_key(c.plataforma)
                             AND nullif(btrim(c.buy_box_seller), '') IS NOT NULL
                        THEN c.buy_box_seller
                        ELSE c.seller END
               )                                 AS seller_canonical,
               seller_canonico(c.buy_box_seller) AS buybox_canonical,
               c.marketplace_product_id, c.marca, c.produto,
               c.posicao_geral, c.keyword, c.patrocinado, c.preco,
               c.qtd_sellers, c.tipo_seller, c.reputacao_seller
        FROM coletas c
        WHERE c.data = p_data AND c.offer_key IS NOT NULL
          AND c.seller IS NOT NULL AND btrim(c.seller) <> ''
    ),
    identidade AS (
        SELECT data, turno, plataforma, offer_key,
               greatest(count(DISTINCT marketplace_product_id),
                        CASE WHEN count(marketplace_product_id) = 0
                             THEN count(DISTINCT nullif(btrim(coalesce(produto,'')), ''))
                             ELSE 0 END) AS produtos_na_chave
        FROM bruto GROUP BY 1,2,3,4
    )
    SELECT b.data, b.turno, b.plataforma, b.offer_key, b.seller_canonical,
           coalesce(ps.superficie, 'loja_propria') AS superficie,
           min(b.marketplace_product_id) AS marketplace_product_id,
           min(b.marca) AS marca, min(b.produto) AS produto,
           min(b.posicao_geral) AS posicao_melhor,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY b.posicao_geral)::numeric(6,2) AS posicao_mediana,
           count(DISTINCT b.keyword)::int AS keywords_presente,
           bool_or(b.patrocinado) AS patrocinado_em_alguma,
           CASE WHEN coalesce(ps.superficie,'loja_propria') = 'marketplace'
                     AND bool_or(b.buybox_canonical IS NOT NULL AND b.buybox_canonical <> '')
                THEN bool_or(b.seller_canonical = b.buybox_canonical) END AS detentor_buybox,
           max(b.qtd_sellers) AS qtd_sellers, avg(b.preco)::numeric(12,2) AS preco,
           min(b.tipo_seller) AS tipo_seller, min(b.reputacao_seller) AS reputacao_seller,
           count(*)::int AS observacoes,
           greatest(max(i.produtos_na_chave), 1) AS produtos_na_chave
    FROM bruto b
    JOIN identidade i USING (data, turno, plataforma, offer_key)
    LEFT JOIN plataforma_superficie ps ON ps.plataforma = b.plataforma
    GROUP BY b.data, b.turno, b.plataforma, b.offer_key, b.seller_canonical, ps.superficie;

    DELETE FROM seller_offer_daily s
    WHERE s.data = p_data
      AND NOT EXISTS (SELECT 1 FROM _novo n
          WHERE n.turno = s.turno AND n.plataforma = s.plataforma
            AND n.offer_key = s.offer_key AND n.seller_canonical = s.seller_canonical);

    INSERT INTO seller_offer_daily (
        data, turno, plataforma, seller_canonical, offer_key, superficie,
        marketplace_product_id, marca, produto, posicao_melhor, posicao_mediana,
        keywords_presente, patrocinado_em_alguma, detentor_buybox, detentor_anterior,
        virou_no_turno, qtd_sellers, preco, tipo_seller, reputacao_seller,
        identidade_suspeita, produtos_na_chave, observacoes, atualizado_em)
    SELECT n.data, n.turno, n.plataforma, n.seller_canonical, n.offer_key, n.superficie,
           n.marketplace_product_id, n.marca, n.produto, n.posicao_melhor, n.posicao_mediana,
           n.keywords_presente, n.patrocinado_em_alguma, n.detentor_buybox, ant.detentor,
           CASE WHEN n.detentor_buybox AND ant.detentor IS NOT NULL
                THEN ant.detentor IS DISTINCT FROM n.seller_canonical END,
           n.qtd_sellers, n.preco, n.tipo_seller, n.reputacao_seller,
           n.produtos_na_chave > 1, n.produtos_na_chave, n.observacoes, now()
    FROM _novo n
    LEFT JOIN LATERAL (
        SELECT x.seller_canonical AS detentor FROM (
            SELECT m.seller_canonical, m.data, turno_ordinal(m.turno) ord
            FROM _novo m
            WHERE m.plataforma = n.plataforma AND m.marketplace_product_id IS NOT NULL
              AND m.marketplace_product_id = n.marketplace_product_id
              AND m.detentor_buybox AND turno_ordinal(m.turno) < turno_ordinal(n.turno)
            UNION ALL
            SELECT s.seller_canonical, s.data, turno_ordinal(s.turno)
            FROM seller_offer_daily s
            WHERE s.plataforma = n.plataforma AND s.marketplace_product_id IS NOT NULL
              AND s.marketplace_product_id = n.marketplace_product_id
              AND s.detentor_buybox AND s.data < n.data
        ) x
        WHERE n.marketplace_product_id IS NOT NULL
        ORDER BY x.data DESC, x.ord DESC LIMIT 1
    ) ant ON true
    ON CONFLICT (data, turno, plataforma, offer_key, seller_canonical) DO UPDATE SET
        superficie = EXCLUDED.superficie,
        marketplace_product_id = EXCLUDED.marketplace_product_id,
        marca = EXCLUDED.marca, produto = EXCLUDED.produto,
        posicao_melhor = EXCLUDED.posicao_melhor, posicao_mediana = EXCLUDED.posicao_mediana,
        keywords_presente = EXCLUDED.keywords_presente,
        patrocinado_em_alguma = EXCLUDED.patrocinado_em_alguma,
        detentor_buybox = EXCLUDED.detentor_buybox,
        detentor_anterior = EXCLUDED.detentor_anterior,
        virou_no_turno = EXCLUDED.virou_no_turno,
        qtd_sellers = EXCLUDED.qtd_sellers, preco = EXCLUDED.preco,
        tipo_seller = EXCLUDED.tipo_seller, reputacao_seller = EXCLUDED.reputacao_seller,
        identidade_suspeita = EXCLUDED.identidade_suspeita,
        produtos_na_chave = EXCLUDED.produtos_na_chave,
        observacoes = EXCLUDED.observacoes, atualizado_em = now();
    GET DIAGNOSTICS v_ofertas = ROW_COUNT;

    SELECT count(*)::int INTO v_susp
    FROM seller_offer_daily WHERE data = p_data AND identidade_suspeita;

    RETURN QUERY SELECT v_ofertas, v_susp, v_cob;
END;
$fn$;
