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
        monkeypatch.setattr(scraper, "_fetch_pdp_html", lambda url: None)
        scraper._pdp_budget = 0

        resolvidos = scraper._resolve_pending_sellers({SELLER_ID: "https://exemplo/p"})

        assert resolvidos == {}
        assert dormiu == []

    def test_id_em_quarentena_nao_gera_espera(self, scraper, monkeypatch):
        """Idem para ID já em quarentena: volta sem rede, logo sem espera."""
        dormiu = []
        monkeypatch.setattr(scraper, "_random_delay", lambda **kw: dormiu.append(kw))
        monkeypatch.setattr(scraper, "_fetch_pdp_html", lambda url: None)
        scraper._seller_cache.mark_failed(SELLER_ID)

        scraper._resolve_pending_sellers({SELLER_ID: "https://exemplo/p"})

        assert dormiu == []
        assert scraper._seller_metrics["pdp_fetch_attempts"] == 0

    def test_fetch_real_ainda_espaca(self, scraper, monkeypatch):
        """Contraprova: quando houve requisição, a espera continua valendo."""
        dormiu = []
        monkeypatch.setattr(scraper, "_random_delay", lambda **kw: dormiu.append(kw))
        monkeypatch.setattr(
            scraper, "_fetch_pdp_html",
            lambda url: "<html><body>" + "<p>x</p>" * 200 +
                        "<p>Vendido e entregue por Refri Center</p></body></html>",
        )
        resolvidos = scraper._resolve_pending_sellers({SELLER_ID: "https://exemplo/p"})

        assert resolvidos == {SELLER_ID: "Refri Center"}
        assert len(dormiu) == 1

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
