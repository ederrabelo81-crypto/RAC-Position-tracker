"""
tests/test_leroy_sellers.py — resolução de seller da Leroy Merlin.

Cobre o gap diagnosticado em Jul/2026: o índice Algolia só devolve o ObjectId
do lojista em `marketplaceSellers`, então o nome precisa vir do PDP e ser
persistido em cache. Testa a extração (offline, sobre HTML fixo), o cache em
disco e a classificação 1P/3P do hit — sem nenhuma chamada de rede.

Rode: pytest tests/test_leroy_sellers.py
"""
import json

import pytest

from scrapers.leroy_merlin import LeroyMerlinScraper, UNRESOLVED_SELLER
from utils.leroy_sellers import (
    LeroySellerCache,
    clean_seller_name,
    extract_seller_from_pdp,
    is_leroy_self,
)

SELLER_ID = "5e6fd1d90a8aa474fe271e83"


def _pdp_next_data(seller_id: str, seller_name: str) -> str:
    """PDP com __NEXT_DATA__ carregando o seller ancorado no ObjectId."""
    payload = {
        "props": {"pageProps": {"product": {
            "name": "Ar Condicionado Split Inverter 12000BTUs",
            "offers": [{"sellerId": seller_id, "sellerName": seller_name, "price": 2199.0}],
        }}}
    }
    return (
        "<html><body><h1>Produto</h1>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )


def _pdp_text(seller_name: str) -> str:
    """PDP sem JSON útil — nome só no rótulo textual."""
    return (
        "<html><body>"
        "<h1>Ar Condicionado Split Inverter 9000BTUs</h1>"
        f"<div class='sc-abc'><span>Vendido e entregue por</span><strong>{seller_name}</strong></div>"
        "</body></html>"
    )


class TestCleanSellerName:
    def test_normaliza_espacos(self):
        assert clean_seller_name("  Refri   Center Ltda\n") == "Refri Center Ltda"

    def test_rejeita_objectid(self):
        assert clean_seller_name(SELLER_ID) is None

    def test_rejeita_texto_de_produto(self):
        # Rótulo colado em atributo do produto não é nome de loja
        assert clean_seller_name("Split 12000 BTUs 220V") is None

    def test_rejeita_vazio_e_none(self):
        assert clean_seller_name("") is None
        assert clean_seller_name(None) is None

    def test_rejeita_nome_longo_demais(self):
        assert clean_seller_name("X" * 80) is None

    def test_identifica_1p_da_leroy(self):
        assert is_leroy_self("Leroy Merlin") is True
        assert is_leroy_self("leroymerlin") is True
        assert is_leroy_self("Refri Center") is False


class TestExtractSellerFromPdp:
    def test_next_data_ancorado_no_id(self):
        html = _pdp_next_data(SELLER_ID, "Refri Center")
        assert extract_seller_from_pdp(html, SELLER_ID) == "Refri Center"

    def test_next_data_sem_id_usa_candidato_generico(self):
        html = _pdp_next_data(SELLER_ID, "Refri Center")
        assert extract_seller_from_pdp(html, None) == "Refri Center"

    def test_id_divergente_ainda_resolve_pelo_generico(self):
        """ID diferente no PDP não invalida o único seller 3P presente."""
        html = _pdp_next_data("outro_id_qualquer", "Refri Center")
        assert extract_seller_from_pdp(html, SELLER_ID) == "Refri Center"

    def test_fallback_rotulo_textual(self):
        html = _pdp_text("Climatiza Store")
        assert extract_seller_from_pdp(html, SELLER_ID) == "Climatiza Store"

    def test_fallback_vendido_por(self):
        html = "<html><body><p>Vendido por Loja do Ar</p></body></html>"
        assert extract_seller_from_pdp(html, SELLER_ID) == "Loja do Ar"

    def test_json_ld(self):
        ld = {
            "@type": "Product",
            "offers": {"@type": "Offer", "seller": {"@type": "Organization",
                                                    "name": "Ar Sul Distribuidora"}},
        }
        html = (
            "<html><body>"
            f'<script type="application/ld+json">{json.dumps(ld)}</script>'
            "</body></html>"
        )
        assert extract_seller_from_pdp(html, SELLER_ID) == "Ar Sul Distribuidora"

    def test_html_sem_seller_retorna_none(self):
        html = "<html><body><h1>Ar Condicionado</h1><p>Frete grátis</p></body></html>"
        assert extract_seller_from_pdp(html, SELLER_ID) is None

    def test_html_vazio_nao_quebra(self):
        assert extract_seller_from_pdp("", SELLER_ID) is None

    def test_json_corrompido_cai_para_texto(self):
        html = (
            '<html><body><script id="__NEXT_DATA__">{isso nao e json</script>'
            "<p>Vendido e entregue por Frio Total</p></body></html>"
        )
        assert extract_seller_from_pdp(html, SELLER_ID) == "Frio Total"


class TestLeroySellerCache:
    def test_persiste_e_recarrega(self, tmp_path):
        path = tmp_path / "leroy_sellers.json"
        cache = LeroySellerCache(path=path)
        cache.put(SELLER_ID, "Refri Center")
        assert cache.save() is True

        recarregado = LeroySellerCache(path=path)
        assert recarregado.get(SELLER_ID) == "Refri Center"
        assert len(recarregado) == 1

    def test_nao_grava_nome_invalido(self, tmp_path):
        cache = LeroySellerCache(path=tmp_path / "c.json")
        cache.put(SELLER_ID, SELLER_ID)  # ObjectId não é nome
        assert cache.get(SELLER_ID) is None

    def test_quarentena_evita_reconsulta(self, tmp_path):
        cache = LeroySellerCache(path=tmp_path / "c.json", retry_days=7)
        assert cache.should_retry(SELLER_ID) is True
        cache.mark_failed(SELLER_ID, "https://exemplo/p")
        assert cache.should_retry(SELLER_ID) is False

    def test_quarentena_expira(self, tmp_path):
        cache = LeroySellerCache(path=tmp_path / "c.json", retry_days=0)
        cache.mark_failed(SELLER_ID)
        assert cache.should_retry(SELLER_ID) is True

    def test_resolver_tira_da_quarentena(self, tmp_path):
        cache = LeroySellerCache(path=tmp_path / "c.json")
        cache.mark_failed(SELLER_ID)
        cache.put(SELLER_ID, "Refri Center")
        assert cache.should_retry(SELLER_ID) is True

    def test_arquivo_corrompido_nao_quebra(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text("{nao e json", encoding="utf-8")
        cache = LeroySellerCache(path=path)
        assert len(cache) == 0

    @pytest.mark.parametrize("conteudo", ["[]", "42", '"texto"', "null"])
    def test_json_valido_mas_nao_objeto_nao_quebra(self, tmp_path, conteudo):
        """JSON válido com raiz não-objeto não pode impedir o scraper de iniciar."""
        path = tmp_path / "c.json"
        path.write_text(conteudo, encoding="utf-8")
        cache = LeroySellerCache(path=path)
        assert len(cache) == 0
        assert cache.get(SELLER_ID) is None


@pytest.fixture
def scraper(tmp_path):
    """Scraper com cache isolado — nenhum teste toca data/leroy_sellers.json."""
    s = LeroyMerlinScraper()
    s._seller_cache = LeroySellerCache(path=tmp_path / "cache.json")
    return s


class TestClassifyHitSeller:
    def test_sem_marketplace_sellers_e_1p(self, scraper):
        info = scraper._classify_hit_seller({"name": "Ar Condicionado"})
        assert info["seller"] == "Leroy Merlin"
        assert info["tipo_seller"] == "1P"
        assert info["qtd_sellers"] == 1

    def test_id_desconhecido_fica_pendente(self, scraper):
        info = scraper._classify_hit_seller({
            "name": "Ar Condicionado", "marketplaceSellers": [SELLER_ID],
        })
        assert info["seller"] is None          # pendente de PDP
        assert info["seller_id"] == SELLER_ID
        assert info["tipo_seller"] == "3P"

    def test_cache_em_disco_resolve_sem_rede(self, scraper):
        scraper._seller_cache.put(SELLER_ID, "Refri Center")
        info = scraper._classify_hit_seller({
            "name": "Ar Condicionado", "marketplaceSellers": [SELLER_ID],
        })
        assert info["seller"] == "Refri Center"
        assert scraper._seller_metrics["resolved_via_disk_cache"] == 1

    def test_qtd_sellers_reflete_o_payload(self, scraper):
        info = scraper._classify_hit_seller({
            "name": "Ar Condicionado", "marketplaceSellers": [SELLER_ID, "outro_id"],
        })
        assert info["qtd_sellers"] == 2

    def test_dict_sem_nome_nao_vira_1p_falso(self, scraper):
        """
        `marketplaceSellers` preenchido já é evidência de oferta de marketplace.
        Um dict sem nome utilizável não pode ser medido como vitória 1P da
        Leroy — isso falsearia o share 1P vs 3P, que é o foco da coleta.
        """
        info = scraper._classify_hit_seller({
            "name": "Ar Condicionado", "marketplaceSellers": [{"algoInesperado": 1}],
        })
        assert info["tipo_seller"] == "3P"
        assert info["seller"] is None

    def test_shape_inesperado_nao_vira_1p_falso(self, scraper):
        """Idem para um shape que nenhuma das ramificações conhece."""
        info = scraper._classify_hit_seller({
            "name": "Ar Condicionado", "marketplaceSellers": {"id": "nao-e-dict"},
        })
        assert info["tipo_seller"] == "3P"
        assert info["seller"] is None

    def test_sellers_inline_tem_prioridade_sobre_pdp(self, scraper):
        info = scraper._classify_hit_seller({
            "name": "Ar Condicionado",
            "marketplaceSellers": [SELLER_ID],
            "sellers": [
                {"sellerId": "1", "sellerName": "Leroy Merlin"},
                {"sellerId": SELLER_ID, "sellerName": "Frio Total"},
            ],
        })
        assert info["seller"] == "Frio Total"
        assert scraper._seller_metrics["resolved_via_inline_hit"] == 1


class TestParseAlgoliaHitsSemRede:
    """Garante que a passada de PDP é pulada quando desligada/sem URL."""

    def test_pendente_vira_sentinela(self, scraper, monkeypatch):
        monkeypatch.setattr(scraper, "_resolve_pending_sellers", lambda pending: {})
        records = scraper._parse_algolia_hits(
            [{"name": "Ar Condicionado Split 12000", "marketplaceSellers": [SELLER_ID],
              "url": "/produto/p"}],
            "ar condicionado", {}, 0,
        )
        assert records[0]["Seller / Vendedor"] == UNRESOLVED_SELLER
        assert records[0]["Tipo Seller"] == "3P"

    def test_pdp_resolvido_preenche_buy_box(self, scraper, monkeypatch):
        monkeypatch.setattr(
            scraper, "_resolve_pending_sellers", lambda pending: {SELLER_ID: "Refri Center"}
        )
        records = scraper._parse_algolia_hits(
            [{"name": "Ar Condicionado Split 12000", "marketplaceSellers": [SELLER_ID],
              "url": "/produto/p"}],
            "ar condicionado", {}, 0,
        )
        assert records[0]["Seller / Vendedor"] == "Refri Center"
        assert records[0]["Buy Box Seller"] == "Refri Center"
        assert records[0]["Tipo Seller"] == "3P"

    def test_lista_de_hits_mutada_durante_o_parse_nao_desalinha(self, scraper, monkeypatch):
        """
        Regressão: no caminho de XHR, `hits` é a própria `_captured_products`,
        que o listener de `response` segue alimentando. Se a passada de PDP
        navegar o browser, os XHRs do PDP entram na lista depois de
        `seller_info` estar montado. O snapshot em `_parse_algolia_hits` evita
        o IndexError e a mistura de produtos do PDP no resultado da busca.
        """
        hits = [{"name": "Ar Condicionado Split 12000", "marketplaceSellers": [SELLER_ID],
                 "url": "/produto/p"}]

        def resolve_e_poluir(pending):
            # Simula o listener disparando durante a navegação ao PDP
            hits.append({"name": "Produto Relacionado do PDP", "url": "/outro/p"})
            return {SELLER_ID: "Refri Center"}

        monkeypatch.setattr(scraper, "_resolve_pending_sellers", resolve_e_poluir)
        records = scraper._parse_algolia_hits(hits, "ar condicionado", {}, 0)

        assert len(records) == 1                                   # sem o item intruso
        assert records[0]["Seller / Vendedor"] == "Refri Center"

    def test_sem_orcamento_de_pdp_nao_ha_espera(self, scraper, monkeypatch):
        """
        Regressão: com `_pdp_budget` esgotado, `_resolve_via_pdp` volta sem
        tocar a rede — a espera entre PDPs não deve acontecer, senão o run
        acumula minutos de sono inútil ao longo das 40 keywords.
        """
        dormiu = []
        monkeypatch.setattr(scraper, "_random_delay", lambda **kw: dormiu.append(kw))
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: None)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: None)
        scraper._pdp_budget = 0

        resolvidos = scraper._resolve_pending_sellers({SELLER_ID: "https://exemplo/p"})

        assert resolvidos == {}
        assert dormiu == []

    def test_id_em_quarentena_nao_gera_espera(self, scraper, monkeypatch):
        """Idem para ID já em quarentena: volta sem rede, logo sem espera."""
        dormiu = []
        monkeypatch.setattr(scraper, "_random_delay", lambda **kw: dormiu.append(kw))
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: None)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: None)
        scraper._seller_cache.mark_failed(SELLER_ID)

        scraper._resolve_pending_sellers({SELLER_ID: "https://exemplo/p"})

        assert dormiu == []
        assert scraper._seller_metrics["pdp_fetch_attempts"] == 0

    def test_fetch_real_ainda_espaca(self, scraper, monkeypatch):
        """Contraprova: quando houve requisição, a espera continua valendo."""
        dormiu = []
        monkeypatch.setattr(scraper, "_random_delay", lambda **kw: dormiu.append(kw))
        monkeypatch.setattr(
            scraper, "_fetch_pdp_requests",
            lambda url: "<html><body>" + "<p>x</p>" * 200 +
                        "<p>Vendido e entregue por Refri Center</p></body></html>",
        )
        resolvidos = scraper._resolve_pending_sellers({SELLER_ID: "https://exemplo/p"})

        assert resolvidos == {SELLER_ID: "Refri Center"}
        assert len(dormiu) == 1

    def test_challenge_http_200_nao_impede_o_fallback_de_browser(self, scraper, monkeypatch):
        """
        Regressão (P1): Akamai responde HTTP 200 com interstitial de JS, que
        passa no teste de tamanho. Antes isso era aceito como PDP, a extração
        falhava e o seller ia para quarentena de 7 dias sem o browser nunca ser
        tentado — justamente quando o fallback mais importa.
        """
        challenge = "<html><body>" + "<p>z</p>" * 200 + "</body></html>"
        pdp_bom = ("<html><body>" + "<p>x</p>" * 200 +
                   "<p>Vendido e entregue por Refri Center</p></body></html>")
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: challenge)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: pdp_bom)
        monkeypatch.setattr(scraper, "_random_delay", lambda **kw: None)

        nome = scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p")

        assert nome == "Refri Center"
        assert scraper._seller_cache.get(SELLER_ID) == "Refri Center"
        # e não entrou em quarentena
        assert scraper._seller_cache.should_retry(SELLER_ID) is True

    def test_pdp_que_resolve_para_a_propria_leroy_e_descartado(self, scraper, monkeypatch):
        """Um ID de marketplace não pode ser cacheado apontando para a Leroy 1P."""
        pdp_1p = ("<html><body>" + "<p>x</p>" * 200 +
                  "<p>Vendido e entregue por Leroy Merlin</p></body></html>")
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: pdp_1p)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: pdp_1p)

        assert scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p") is None
        assert scraper._seller_cache.get(SELLER_ID) is None

    def test_marcadores_de_challenge_sao_rejeitados_no_caminho_leve(self, scraper):
        """`_fetch_pdp_requests` não devolve página de challenge com HTTP 200."""
        class FakeResp:
            status_code = 200
            text = "<html><body><div id='px-captcha'></div>" + "<p>z</p>" * 300 + "</body></html>"

        class FakeSession:
            headers: dict = {}
            def get(self, *a, **kw):
                return FakeResp()

        scraper._pdp_session = FakeSession()
        assert scraper._fetch_pdp_requests("https://exemplo/p") is None
        assert scraper._pdp_requests_strikes == 1

    def test_pdp_legitimo_passa_no_caminho_leve(self, scraper):
        """Contraprova: PDP normal não é confundido com challenge."""
        class FakeResp:
            status_code = 200
            text = ("<html><body>" + "<p>x</p>" * 300 +
                    "<p>Vendido e entregue por Refri Center</p></body></html>")

        class FakeSession:
            headers: dict = {}
            def get(self, *a, **kw):
                return FakeResp()

        scraper._pdp_session = FakeSession()
        scraper._pdp_requests_strikes = 2
        assert scraper._fetch_pdp_requests("https://exemplo/p") is not None
        assert scraper._pdp_requests_strikes == 0   # sucesso zera os strikes

    def test_um_pdp_por_id_unico_e_nao_por_produto(self, scraper, monkeypatch):
        """3 produtos do mesmo seller → 1 única entrada pendente."""
        vistos = {}

        def fake_resolve(pending):
            vistos.update(pending)
            return {SELLER_ID: "Refri Center"}

        monkeypatch.setattr(scraper, "_resolve_pending_sellers", fake_resolve)
        hits = [
            {"name": f"Ar Condicionado {i}", "marketplaceSellers": [SELLER_ID],
             "url": f"/produto-{i}/p"}
            for i in range(3)
        ]
        records = scraper._parse_algolia_hits(hits, "ar condicionado", {}, 0)
        assert len(vistos) == 1
        assert all(r["Seller / Vendedor"] == "Refri Center" for r in records)


class TestExtractSellerIdsDoProbe:
    """
    O relatório de lacunas do `--scan` precisa enxergar os três shapes que o
    scraper aceita — contar um hit como 3P e omitir os IDs dele daria falsa
    sensação de cobertura do cache.
    """

    def test_lista_de_strings(self):
        from scripts.leroy_seller_probe import extract_seller_ids
        assert extract_seller_ids([SELLER_ID, "outro"]) == [SELLER_ID, "outro"]

    def test_lista_de_dicts(self):
        from scripts.leroy_seller_probe import extract_seller_ids
        ms = [{"sellerId": SELLER_ID, "sellerName": "Refri Center"},
              {"seller_id": "b"}, {"id": "c"}, {"_id": "d"}]
        assert extract_seller_ids(ms) == [SELLER_ID, "b", "c", "d"]

    def test_dict_indexado_por_id(self):
        from scripts.leroy_seller_probe import extract_seller_ids
        assert extract_seller_ids({SELLER_ID: {"sellerName": "X"}}) == [SELLER_ID]

    def test_dict_indexado_com_valor_escalar(self):
        """Chave com cara de ObjectId é aceita mesmo sem dict no valor."""
        from scripts.leroy_seller_probe import extract_seller_ids
        assert extract_seller_ids({SELLER_ID: "Refri Center"}) == [SELLER_ID]

    def test_dict_nao_indexado_nao_gera_seller_fantasma(self):
        """
        `{"id": "..."}` é o shape que o scraper trata como inesperado. Emitir a
        chave aqui criaria um seller chamado "id" no relatório — lacuna de cache
        inventada — e silenciaria o aviso de shape novo, que é o diagnóstico
        correto para esse caso.
        """
        from scripts.leroy_seller_probe import extract_seller_ids
        assert extract_seller_ids({"id": "nao-e-dict"}) == []
        assert extract_seller_ids({"sellerName": "Refri Center"}) == []

    def test_shapes_mistos_e_entradas_inuteis(self):
        from scripts.leroy_seller_probe import extract_seller_ids
        assert extract_seller_ids([SELLER_ID, {"semId": 1}, "", None]) == [SELLER_ID]

    def test_formato_irreconhecivel(self):
        from scripts.leroy_seller_probe import extract_seller_ids
        assert extract_seller_ids("string-solta") == []
        assert extract_seller_ids(None) == []
        assert extract_seller_ids(42) == []


def _pdp_app_router(seller_id: str, seller_name: str, split: bool = False) -> str:
    """
    PDP em App Router: sem `__NEXT_DATA__`, dados em `self.__next_f.push`.

    Foi este shape que fez 100% das resoluções falharem em produção (jul/2026):
    `pdp_fetch_attempts: 7 | resolved_via_pdp: 0`.
    """
    payload = (
        '{"product":{"name":"Ar Condicionado Split","offers":[{"sellerId":"'
        + seller_id + '","sellerName":"' + seller_name + '"}]}}'
    )
    if split:
        # O streaming real fragmenta no meio do JSON, às vezes no meio da chave
        corte = payload.index(seller_name) - 3
        pushes = (
            f"self.__next_f.push([1,{json.dumps(payload[:corte])}]);"
            f"self.__next_f.push([1,{json.dumps(payload[corte:])}])"
        )
    else:
        pushes = f"self.__next_f.push([1,{json.dumps(payload)}])"
    return f"<html><body><div id='__next'></div><script>{pushes}</script></body></html>"


class TestAppRouterPayload:
    def test_resolve_ancorado_no_id(self):
        html = _pdp_app_router(SELLER_ID, "Frio Total")
        assert extract_seller_from_pdp(html, SELLER_ID) == "Frio Total"

    def test_resolve_sem_ancora(self):
        html = _pdp_app_router(SELLER_ID, "Frio Total")
        assert extract_seller_from_pdp(html, None) == "Frio Total"

    def test_chunks_fragmentados_sao_reconstituidos(self):
        """A concatenação dos pushes remonta o JSON partido pelo streaming."""
        html = _pdp_app_router(SELLER_ID, "Refri Center", split=True)
        assert extract_seller_from_pdp(html, SELLER_ID) == "Refri Center"

    def test_shell_sem_dados_continua_none(self):
        shell = "<html><body><div id='__next'></div><script>self.__next_f=[]</script></body></html>"
        assert extract_seller_from_pdp(shell, SELLER_ID) is None

    def test_push_com_json_invalido_nao_quebra(self):
        """
        Exercita de fato o `except JSONDecodeError` de `next_flight_text`: o
        push precisa **casar** a regex e falhar no `json.loads`. `\\x` não é
        escape válido em JSON, mas passa pelo `\\\\.` da regex.
        """
        from utils.leroy_sellers import _FLIGHT_PUSH_RE, next_flight_text

        html = r'<html><body><script>self.__next_f.push([1,"ruim \x escape"])</script></body></html>'
        assert _FLIGHT_PUSH_RE.search(html), "o push precisa casar a regex"
        assert next_flight_text(html) == ""          # chunk descartado, sem exceção
        assert extract_seller_from_pdp(html, SELLER_ID) is None

    def test_push_que_nem_casa_a_regex_e_ignorado(self):
        """Push truncado (sem fechar a string) simplesmente não casa."""
        from utils.leroy_sellers import _FLIGHT_PUSH_RE

        html = '<html><body><script>self.__next_f.push([1,"truncado])</script></body></html>'
        assert not _FLIGHT_PUSH_RE.search(html)
        assert extract_seller_from_pdp(html, SELLER_ID) is None

    def test_chunk_valido_sobrevive_a_chunk_invalido(self):
        """Um chunk quebrado não pode derrubar a leitura dos demais."""
        payload = (
            f'{{"offers":[{{"sellerId":"{SELLER_ID}","sellerName":"Frio Total"}}]}}'
        )
        html = (
            "<html><body><script>"
            r'self.__next_f.push([1,"ruim \x escape"]);'
            f"self.__next_f.push([1,{json.dumps(payload)}])"
            "</script></body></html>"
        )
        assert extract_seller_from_pdp(html, SELLER_ID) == "Frio Total"

    def test_nao_devolve_a_propria_leroy(self):
        """Payload que só traz a Leroy 1P não serve para um ID de marketplace."""
        html = _pdp_app_router(SELLER_ID, "Leroy Merlin")
        assert extract_seller_from_pdp(html, SELLER_ID) is None

    def test_next_flight_text_vazio_sem_pushes(self):
        from utils.leroy_sellers import next_flight_text
        assert next_flight_text("<html><body>nada aqui</body></html>") == ""


class TestQuarentenaLimpeza:
    def test_clear_quarantine_libera_ids(self, tmp_path):
        cache = LeroySellerCache(path=tmp_path / "c.json")
        cache.mark_failed(SELLER_ID, "https://exemplo/p")
        cache.mark_failed("outro_id")
        assert cache.should_retry(SELLER_ID) is False

        assert cache.clear_quarantine() == 2
        assert cache.should_retry(SELLER_ID) is True
        assert cache.quarantined == {}

    def test_clear_quarantine_persiste(self, tmp_path):
        path = tmp_path / "c.json"
        cache = LeroySellerCache(path=path)
        cache.mark_failed(SELLER_ID)
        cache.save()

        c2 = LeroySellerCache(path=path)
        assert c2.should_retry(SELLER_ID) is False
        c2.clear_quarantine()
        c2.save()

        assert LeroySellerCache(path=path).should_retry(SELLER_ID) is True

    def test_clear_quarantine_vazia_e_noop(self, tmp_path):
        cache = LeroySellerCache(path=tmp_path / "c.json")
        assert cache.clear_quarantine() == 0
        assert cache.save() is False


class TestAppRouterNaoConfundeProdutoComSeller:
    """
    Regressão (P1): a passada ancorada usava a chave genérica `name` numa janela
    de 400 chars ao redor do seller ID. O título do produto cai nessa janela e
    não é pego pelo blocklist, então virava o "nome do lojista" — e ia para
    `data/leroy_sellers.json`, corrompendo todos os registros futuros daquele
    seller.
    """

    @staticmethod
    def _flight(payload: str) -> str:
        return (
            "<html><body><script>"
            f"self.__next_f.push([1,{json.dumps(payload)}])"
            "</script></body></html>"
        )

    def test_titulo_de_produto_nao_vira_seller(self):
        payload = (
            '{"product":{"name":"Ar Condicionado Portatil Philco","brand":"Philco",'
            '"description":"Refrigeracao potente para ambientes de ate 20m2",'
            f'"offers":[{{"sellerId":"{SELLER_ID}","sellerName":"Loja X"}}]}}}}'
        )
        assert extract_seller_from_pdp(self._flight(payload), SELLER_ID) == "Loja X"

    def test_sem_seller_no_payload_nao_devolve_titulo(self):
        """Só título de produto: melhor None do que um nome errado no cache."""
        payload = (
            '{"product":{"name":"Ar Condicionado Portatil Philco"},'
            f'"sellerId":"{SELLER_ID}"}}'
        )
        assert extract_seller_from_pdp(self._flight(payload), SELLER_ID) is None

    def test_multi_seller_pega_o_do_mesmo_objeto(self):
        """
        O nome do seller anterior fica fisicamente mais perto do ID do que o
        nome que lhe pertence — por isso a busca é delimitada pelo objeto JSON,
        não por distância.
        """
        payload = (
            '{"offers":[{"sellerId":"61940b77c220aa3ead390ee2","sellerName":"Errado Ltda"},'
            f'{{"sellerId":"{SELLER_ID}","sellerName":"Certo Ltda"}}]}}'
        )
        assert extract_seller_from_pdp(self._flight(payload), SELLER_ID) == "Certo Ltda"

    def test_ordem_invertida_das_chaves(self):
        """Nome antes do ID no mesmo objeto continua sendo encontrado."""
        payload = f'{{"offers":[{{"sellerName":"Antes Ltda","sellerId":"{SELLER_ID}"}}]}}'
        assert extract_seller_from_pdp(self._flight(payload), SELLER_ID) == "Antes Ltda"

    def test_seller_aninhado_com_name_generico(self):
        """`name` só é aceito sob a chave "seller", onde é inequívoco."""
        payload = (
            f'{{"offers":[{{"sellerId":"{SELLER_ID}",'
            '"seller":{"id":"x","name":"Frio Total"}}]}'
        )
        assert extract_seller_from_pdp(self._flight(payload), SELLER_ID) == "Frio Total"

    def test_objeto_ambiguo_nao_arrisca_o_cache(self):
        """Vários nomes no mesmo objeto → não chuta, deixa para as camadas seguintes."""
        payload = (
            f'{{"sellerId":"{SELLER_ID}","sellerName":"Um Ltda","storeName":"Outro Ltda"}}'
        )
        # O fallback genérico ainda pode responder, mas nunca por proximidade
        # arbitrária dentro de um objeto ambíguo.
        resultado = extract_seller_from_pdp(self._flight(payload), SELLER_ID)
        assert resultado in ("Um Ltda", None)


class TestClearQuarantineCLI:
    """
    `--clear-quarantine` não pode anunciar sucesso se a gravação falhou: os IDs
    seguiriam bloqueados após o processo sair, e o operador acharia que liberou.
    """

    def test_retorna_erro_quando_nao_persiste(self, tmp_path, monkeypatch):
        from scripts.leroy_seller_probe import cmd_clear_quarantine

        cache = LeroySellerCache(path=tmp_path / "c.json")
        cache.mark_failed(SELLER_ID, "https://exemplo/p")
        monkeypatch.setattr(cache, "save", lambda: False)   # simula OSError na escrita

        assert cmd_clear_quarantine(cache) == 1

    def test_retorna_sucesso_quando_persiste(self, tmp_path):
        from scripts.leroy_seller_probe import cmd_clear_quarantine

        path = tmp_path / "c.json"
        cache = LeroySellerCache(path=path)
        cache.mark_failed(SELLER_ID)

        assert cmd_clear_quarantine(cache) == 0
        # e a liberação sobrevive ao processo
        assert LeroySellerCache(path=path).should_retry(SELLER_ID) is True

    def test_quarentena_vazia_e_sucesso_sem_escrita(self, tmp_path):
        from scripts.leroy_seller_probe import cmd_clear_quarantine

        cache = LeroySellerCache(path=tmp_path / "c.json")
        assert cmd_clear_quarantine(cache) == 0


class TestAncoraRobusta:
    """
    Achados da 5ª rodada de revisão: a âncora do App Router precisa sobreviver a
    chaves dentro de strings, aceitar o shape `{sellerId, name}` sem reabrir a
    porta para o título do produto, e a passada genérica precisa cobrir as
    mesmas chaves da ancorada.
    """

    @staticmethod
    def _flight(payload: str) -> str:
        return (
            "<html><body><script>"
            f"self.__next_f.push([1,{json.dumps(payload)}])"
            "</script></body></html>"
        )

    def test_chave_dentro_de_string_nao_desbalanceia_o_recorte(self):
        """
        Nome de loja com `{`/`}` desbalancearia a contagem e expandiria o
        recorte até a oferta seguinte, associando o seller errado.
        """
        payload = (
            '{"offers":[{"sellerId":"61940b77c220aa3ead390ee2","sellerName":"Errado {Ltda}"},'
            f'{{"sellerId":"{SELLER_ID}","sellerName":"Certo Ltda"}}]}}'
        )
        assert extract_seller_from_pdp(self._flight(payload), SELLER_ID) == "Certo Ltda"

    def test_enclosing_object_ignora_chaves_em_string(self):
        from utils.leroy_sellers import _enclosing_object

        texto = f'{{"a":"tem {{ chave"}},{{"sellerId":"{SELLER_ID}","sellerName":"Z"}}'
        recorte = _enclosing_object(texto, texto.find(SELLER_ID))
        assert recorte.startswith('{"sellerId"')
        assert "tem { chave" not in recorte

    def test_seller_id_com_name_puro_na_folha(self):
        """Shape legítimo `{"sellerId": …, "name": "Loja X"}` volta a resolver."""
        payload = f'{{"offers":[{{"sellerId":"{SELLER_ID}","name":"Loja Do Ar"}}]}}'
        assert extract_seller_from_pdp(self._flight(payload), SELLER_ID) == "Loja Do Ar"

    def test_name_de_produto_continua_rejeitado(self):
        """A liberação do `name` puro não pode reabrir o P1 do título."""
        payload = (
            '{"product":{"name":"Ar Condicionado Portatil Philco","brand":"Philco",'
            f'"offers":[{{"sellerId":"{SELLER_ID}","sellerName":"Loja X"}}]}}}}'
        )
        assert extract_seller_from_pdp(self._flight(payload), SELLER_ID) == "Loja X"

    def test_objeto_com_chave_de_produto_nao_libera_name(self):
        from utils.leroy_sellers import _is_seller_leaf

        assert _is_seller_leaf(f'{{"sellerId":"{SELLER_ID}","name":"Loja"}}', SELLER_ID)
        assert not _is_seller_leaf(
            f'{{"sellerId":"{SELLER_ID}","name":"X","description":"y"}}', SELLER_ID
        )
        assert not _is_seller_leaf('{"sellerId":"outro","name":"X"}', SELLER_ID)

    @pytest.mark.parametrize("chave", ["seller_name", "store_name", "sellerName", "storeName"])
    def test_passada_generica_cobre_as_mesmas_chaves(self, chave):
        """Sem âncora de ID, snake_case precisa resolver igual ao camelCase."""
        payload = '{"offers":[{"%s":"Snake Ltda"}]}' % chave
        assert extract_seller_from_pdp(self._flight(payload), None) == "Snake Ltda"


class TestRotuloCompartilhado:
    def test_probe_e_coleta_usam_o_mesmo_padrao(self):
        """
        Divergência entre o regex do diagnóstico e o da espera de hidratação faz
        o probe afirmar "hidratou" onde a coleta não reconhece — e gasta o
        timeout inteiro mais o fallback em cada PDP.
        """
        from scripts.leroy_seller_probe import _SELLER_LABEL_RE
        from scrapers.leroy_merlin import SELLER_LABEL_PATTERN as no_scraper
        from utils.leroy_sellers import SELLER_LABEL_PATTERN

        assert _SELLER_LABEL_RE.pattern == SELLER_LABEL_PATTERN
        assert no_scraper == SELLER_LABEL_PATTERN

    @pytest.mark.parametrize("texto", [
        "Vendido e entregue por Loja X",
        "VENDIDO POR Loja X",
        "vendido  e  entregue  por Loja X",
        "Vendido\ne entregue\npor Loja X",
    ])
    def test_padrao_tolera_variacoes(self, texto):
        import re as _re
        from utils.leroy_sellers import SELLER_LABEL_PATTERN

        assert _re.search(SELLER_LABEL_PATTERN, texto, _re.IGNORECASE)

    def test_padrao_nao_casa_defendido(self):
        import re as _re
        from utils.leroy_sellers import SELLER_LABEL_PATTERN

        assert not _re.search(SELLER_LABEL_PATTERN, "produto defendido por garantia", _re.IGNORECASE)


class TestRotuloUnicoNaoDiverge:
    """
    Manter duas listas de rótulos fazia o parser aceitar "Entregue por" e "Loja
    parceira:" sem que a espera de hidratação os reconhecesse: o PDP resolvia,
    mas só depois de queimar o timeout inteiro + fallback de rede. Agora as duas
    derivam de `_SELLER_LABEL_ALTERNATIVES`, e este teste trava o acoplamento.
    """

    @pytest.mark.parametrize("texto", [
        "Vendido e entregue por Loja X",
        "Vendido por Loja X",
        "Entregue por Loja X",
        "Vendido e entregue: Loja X",
        "Loja parceira: Loja X",
        "LOJA PARCEIRA Loja X",
    ])
    def test_parser_e_espera_concordam(self, texto):
        import re as _re
        from utils.leroy_sellers import SELLER_LABEL_PATTERN, _PDP_LABEL_RE

        assert bool(_PDP_LABEL_RE.search(texto)) == bool(
            _re.search(SELLER_LABEL_PATTERN, texto, _re.IGNORECASE)
        )

    def test_padrao_e_compativel_com_regex_js(self):
        """Vai para dentro de `wait_for_function` como /.../i — sem lookbehind,
        sem grupos nomeados, sem `/` a escapar."""
        from utils.leroy_sellers import SELLER_LABEL_PATTERN

        assert "(?<" not in SELLER_LABEL_PATTERN
        assert "(?P<" not in SELLER_LABEL_PATTERN
        assert "/" not in SELLER_LABEL_PATTERN

    def test_regex_morta_foi_removida(self):
        """`_RAW_JSON_NAME_RE` ficou sem uso quando os dois call sites migraram."""
        import utils.leroy_sellers as mod

        assert not hasattr(mod, "_RAW_JSON_NAME_RE")


class TestChavesEmStringNaoContamAninhamento:
    def test_structural_braces_ignora_strings(self):
        from utils.leroy_sellers import _structural_braces

        frag = f'{{"sellerId":"{SELLER_ID}","name":"Loja {{Matriz}}"}}'
        assert frag.count("{") == 2          # contagem bruta engana
        assert _structural_braces(frag) == 1  # estrutural é 1

    def test_is_seller_leaf_com_chave_no_nome(self):
        from utils.leroy_sellers import _is_seller_leaf

        frag = f'{{"sellerId":"{SELLER_ID}","name":"Loja {{Matriz}}"}}'
        assert _is_seller_leaf(frag, SELLER_ID) is True

    def test_objeto_realmente_aninhado_continua_rejeitado(self):
        from utils.leroy_sellers import _is_seller_leaf

        frag = f'{{"sellerId":"{SELLER_ID}","name":"X","extra":{{"a":1}}}}'
        assert _is_seller_leaf(frag, SELLER_ID) is False

    def test_resolve_name_puro_com_chave_no_nome(self):
        payload = f'{{"sellerId":"{SELLER_ID}","name":"Loja {{Matriz}}"}}'
        html = (
            "<html><body><script>"
            f"self.__next_f.push([1,{json.dumps(payload)}])"
            "</script></body></html>"
        )
        assert extract_seller_from_pdp(html, SELLER_ID) == "Loja {Matriz}"


class TestQuarentenaTransitoria:
    """
    Produção (jul/2026): um bloqueio momentâneo trancou 8 IDs por 7 dias e a
    coleta seguinte registrou `pdp_fetch_attempts: 0` — sendo que a mesma URL
    devolveu 339 KB minutos depois. Falha de acesso não é seller inexistente.
    """

    def test_transitoria_libera_na_proxima_tentativa(self, tmp_path):
        cache = LeroySellerCache(path=tmp_path / "c.json", retry_days=7)
        cache.mark_failed(SELLER_ID, "https://exemplo/p", transient=True)
        assert cache.should_retry(SELLER_ID) is True

    def test_definitiva_entra_na_quarentena_cheia(self, tmp_path):
        """Página chegou e não tinha seller → não adianta insistir amanhã."""
        cache = LeroySellerCache(path=tmp_path / "c.json", retry_days=7)
        cache.mark_failed(SELLER_ID, "https://exemplo/p", transient=False)
        assert cache.should_retry(SELLER_ID) is False

    def test_transitoria_persistente_vira_definitiva(self, tmp_path):
        """Sem teto, um ID morto queimaria orçamento de PDP em toda coleta."""
        cache = LeroySellerCache(path=tmp_path / "c.json", retry_days=7)
        for _ in range(LeroySellerCache.TRANSIENT_ATTEMPTS):
            cache.mark_failed(SELLER_ID, transient=True)
        assert cache.should_retry(SELLER_ID) is False

    def test_definitiva_nao_volta_a_transitoria(self, tmp_path):
        """Um bloqueio posterior não pode reabrir um ID já julgado definitivo."""
        cache = LeroySellerCache(path=tmp_path / "c.json", retry_days=7)
        cache.mark_failed(SELLER_ID, transient=False)
        cache.mark_failed(SELLER_ID, transient=True)
        assert cache.should_retry(SELLER_ID) is False

    def test_transitoria_sobrevive_ao_disco(self, tmp_path):
        path = tmp_path / "c.json"
        cache = LeroySellerCache(path=path, retry_days=7)
        cache.mark_failed(SELLER_ID, transient=True)
        cache.save()
        assert LeroySellerCache(path=path, retry_days=7).should_retry(SELLER_ID) is True


class TestResolveViaPdpClassificaFalha:
    def test_pagina_nao_chegou_e_transitoria(self, scraper, monkeypatch):
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: None)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: None)

        assert scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p") is None
        assert scraper._seller_metrics["pdp_bloqueios"] == 1
        assert scraper._seller_cache.should_retry(SELLER_ID) is True

    def test_pagina_chegou_sem_seller_e_definitiva(self, scraper, monkeypatch):
        # Acima de `_PDP_MIN_BYTES`: uma resposta pequena hoje é tratada como
        # bloqueio (transitória), não como página íntegra sem seller.
        pagina = "<html><body>" + "<p>x</p>" * 3_000 + "</body></html>"
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: pagina)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: pagina)

        assert scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p") is None
        assert scraper._seller_metrics["pdp_bloqueios"] == 0
        assert scraper._seller_cache.should_retry(SELLER_ID) is False


class TestCamadaDeResolucao:
    """
    Saber a camada decide se a espera de hidratação (até 10s por PDP) é
    necessária: `json`/`flight` vêm no HTML servido, `texto` exige hidratação.
    """

    def test_json_ld(self):
        from utils.leroy_sellers import extract_seller_with_layer

        ld = {"offers": {"seller": {"name": "Central Ar"}}}
        html = f'<html><script type="application/ld+json">{json.dumps(ld)}</script></html>'
        assert extract_seller_with_layer(html, SELLER_ID) == ("Central Ar", "json")

    def test_flight(self):
        from utils.leroy_sellers import extract_seller_with_layer

        payload = f'{{"offers":[{{"sellerId":"{SELLER_ID}","sellerName":"Frio Total"}}]}}'
        html = (
            "<html><body><script>"
            f"self.__next_f.push([1,{json.dumps(payload)}])"
            "</script></body></html>"
        )
        assert extract_seller_with_layer(html, SELLER_ID) == ("Frio Total", "flight")

    def test_texto_exige_hidratacao(self):
        from utils.leroy_sellers import extract_seller_with_layer

        html = "<html><body><p>Vendido e entregue por Central Ar</p></body></html>"
        assert extract_seller_with_layer(html, SELLER_ID) == ("Central Ar", "texto")

    def test_nada_resolve(self):
        from utils.leroy_sellers import extract_seller_with_layer

        assert extract_seller_with_layer('<html><div id="__next"></div></html>', SELLER_ID) == (None, None)

    def test_wrapper_mantem_a_assinatura_antiga(self):
        html = "<html><body><p>Vendido e entregue por Central Ar</p></body></html>"
        assert extract_seller_from_pdp(html, SELLER_ID) == "Central Ar"


class TestClassificacaoDeBloqueio:
    """
    Regressão dos dois P1 da 7ª rodada. O bloqueio observado em produção veio
    **pelo browser**, com 3.126 bytes de HTML não-vazio: tratar isso como
    "página chegou" julgava a falha definitiva e trancava o seller 7 dias —
    justamente o que a classificação transitória deveria impedir.
    """

    @pytest.mark.parametrize("html,esperado", [
        ("<html>" + "x" * 339_000 + "</html>", True),      # PDP real
        ("<html>" + "y" * 3_000 + "</html>", False),       # challenge de 3 KB
        ("<html><div id='px-captcha'></div>" + "z" * 50_000 + "</html>", False),
        ("", False),
    ])
    def test_looks_like_pdp(self, html, esperado):
        assert LeroyMerlinScraper._looks_like_pdp(html) is esperado

    def test_marcador_depois_dos_primeiros_kb(self):
        """
        Interstitial grande pode trazer o marcador longe do início. Limitar a
        varredura aos primeiros 20 KB classificaria isso como PDP íntegro e
        renderia quarentena de 7 dias.
        """
        html = "<html>" + "z" * 100_000 + "<div id='px-captcha'></div></html>"
        assert LeroyMerlinScraper._looks_like_pdp(html) is False

    def test_challenge_grande_pelo_browser_segue_transitorio(self, scraper, monkeypatch):
        html = "<html>" + "z" * 100_000 + "<div id='px-captcha'></div></html>"
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: None)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: html)

        assert scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p") is None
        assert scraper._seller_metrics["pdp_bloqueios"] == 1
        assert scraper._seller_cache.should_retry(SELLER_ID) is True

    def test_challenge_pelo_browser_e_transitorio(self, scraper, monkeypatch):
        """O caso de produção: 3 KB pelo browser não pode virar definitivo."""
        challenge = "<html>" + "y" * 3_000 + "</html>"
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: None)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: challenge)

        assert scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p") is None
        assert scraper._seller_metrics["pdp_bloqueios"] == 1
        assert scraper._seller_cache.should_retry(SELLER_ID) is True

    def test_pdp_grande_sem_seller_e_definitivo(self, scraper, monkeypatch):
        """Página íntegra e sem seller: insistir amanhã não muda nada."""
        pdp = "<html><body>" + "<p>sem seller aqui</p>" * 3_000 + "</body></html>"
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: pdp)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: pdp)

        assert scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p") is None
        assert scraper._seller_metrics["pdp_bloqueios"] == 0
        assert scraper._seller_cache.should_retry(SELLER_ID) is False


class TestUmaTentativaPorRun:
    """
    O mesmo seller ID reaparece em várias páginas e keywords. Sem trava por
    execução, um run bloqueado gastaria as 3 tentativas transitórias de uma vez
    e a coleta seguinte já cairia na quarentena de 7 dias.
    """

    def test_segunda_chamada_no_mesmo_run_nao_tenta(self, scraper, monkeypatch):
        chamadas = []
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: None)
        monkeypatch.setattr(
            scraper, "_fetch_pdp_browser",
            lambda url: chamadas.append(url) or None,
        )

        scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p")
        scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p")
        scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p")

        assert len(chamadas) == 1
        assert scraper._seller_metrics["pdp_fetch_attempts"] == 1
        # uma única tentativa transitória gravada → segue liberado
        assert scraper._seller_cache.should_retry(SELLER_ID) is True

    def test_nao_gasta_orcamento_repetindo_o_mesmo_id(self, scraper, monkeypatch):
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: None)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: None)
        orcamento_inicial = scraper._pdp_budget

        for _ in range(5):
            scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/p")

        assert scraper._pdp_budget == orcamento_inicial - 1

    def test_ids_diferentes_seguem_sendo_tentados(self, scraper, monkeypatch):
        monkeypatch.setattr(scraper, "_fetch_pdp_requests", lambda url: None)
        monkeypatch.setattr(scraper, "_fetch_pdp_browser", lambda url: None)

        scraper._resolve_via_pdp(SELLER_ID, "https://exemplo/a")
        scraper._resolve_via_pdp("61940b77c220aa3ead390ee2", "https://exemplo/b")

        assert scraper._seller_metrics["pdp_fetch_attempts"] == 2

    def test_tres_runs_bloqueados_escalam_para_definitivo(self, scraper, tmp_path, monkeypatch):
        """A escalada continua existindo — só não acontece dentro de um run."""
        from utils.leroy_sellers import LeroySellerCache

        path = tmp_path / "c.json"
        for _ in range(LeroySellerCache.TRANSIENT_ATTEMPTS):
            s = LeroyMerlinScraper()
            s._seller_cache = LeroySellerCache(path=path)
            monkeypatch.setattr(s, "_fetch_pdp_requests", lambda url: None)
            monkeypatch.setattr(s, "_fetch_pdp_browser", lambda url: None)
            s._resolve_via_pdp(SELLER_ID, "https://exemplo/p")
            s._seller_cache.save()

        assert LeroySellerCache(path=path).should_retry(SELLER_ID) is False


class TestQuarentenaLegada:
    """
    Produção (jul/2026): a coleta pré-correção gravou ~40 IDs sem as chaves
    `transient`/`definitive`. Instalar a correção não os liberava — `should_retry`
    caía na janela de 7 dias — e a coleta seguinte fez **zero** tentativas de
    PDP. O upgrade precisa ser auto-recuperável, não depender de intervenção.
    """

    @staticmethod
    def _cache_legado(path, attempts=1):
        import json as _json
        path.write_text(_json.dumps({
            "sellers": {},
            "unresolved": {
                SELLER_ID: {
                    "attempts": attempts,
                    "last_try": "2026-07-25T01:31:00+00:00",
                    "sample_url": "https://exemplo/p",
                }
            },
        }), encoding="utf-8")
        return LeroySellerCache(path=path, retry_days=7)

    def test_entrada_sem_flags_recebe_beneficio_da_duvida(self, tmp_path):
        cache = self._cache_legado(tmp_path / "c.json")
        assert cache.should_retry(SELLER_ID) is True

    def test_legado_com_muitas_tentativas_permanece_bloqueado(self, tmp_path):
        """O teto de tentativas continua valendo — não é liberação irrestrita."""
        cache = self._cache_legado(
            tmp_path / "c.json", attempts=LeroySellerCache.TRANSIENT_ATTEMPTS
        )
        assert cache.should_retry(SELLER_ID) is False

    def test_definitivo_explicito_nao_e_confundido_com_legado(self, tmp_path):
        cache = LeroySellerCache(path=tmp_path / "c.json", retry_days=7)
        cache.mark_failed(SELLER_ID, transient=False)
        assert cache.should_retry(SELLER_ID) is False
