"""
tests/test_amazon_sellers.py — buy box da Amazon via PDP + cache.

Contexto (Ago/2026): `Buy Box Seller` da Amazon media 0% de preenchimento no
Supabase sobre 8.058 registros. Não era seletor quebrado — a SERP da Amazon
não expõe "Vendido por", só o PDP, e o scraper deixava o campo honestamente
vazio em vez de marcar "Amazon/1P" fantasma.

A resolução via PDP fecha o gap, mas custa uma requisição por ASIN novo na
plataforma mais agressiva em anti-bot do conjunto. Por isso os dois testes
mais importantes deste arquivo são os de SEGURANÇA: desligada por padrão, e
com teto por execução.

Rode: pytest tests/test_amazon_sellers.py
"""
import json

import pytest

from utils.amazon_sellers import (
    DEFAULT_BUDGET,
    AmazonSellerCache,
    clean_seller_name,
    extract_seller_from_pdp,
    is_amazon_self,
    pdp_budget,
    resolution_enabled,
)


class TestDesligadaPorPadrao:
    """A coleta da Amazon hoje é estável — nada pode mudar sem opt-in."""

    def test_sem_env_fica_desligada(self, monkeypatch):
        monkeypatch.delenv("RAC_AMAZON_PDP_BUYBOX", raising=False)
        assert resolution_enabled() is False

    @pytest.mark.parametrize("valor", ["1", "true", "TRUE", "yes", "on"])
    def test_env_liga(self, monkeypatch, valor):
        monkeypatch.setenv("RAC_AMAZON_PDP_BUYBOX", valor)
        assert resolution_enabled() is True

    @pytest.mark.parametrize("valor", ["0", "false", "no", "", "  "])
    def test_valores_negativos_mantem_desligada(self, monkeypatch, valor):
        monkeypatch.setenv("RAC_AMAZON_PDP_BUYBOX", valor)
        assert resolution_enabled() is False

    def test_orcamento_default(self, monkeypatch):
        monkeypatch.delenv("RAC_AMAZON_PDP_BUDGET", raising=False)
        assert pdp_budget() == DEFAULT_BUDGET

    def test_orcamento_configuravel(self, monkeypatch):
        monkeypatch.setenv("RAC_AMAZON_PDP_BUDGET", "5")
        assert pdp_budget() == 5

    def test_orcamento_zero_e_valido(self, monkeypatch):
        """0 desliga os PDPs mas mantém o cache sendo lido."""
        monkeypatch.setenv("RAC_AMAZON_PDP_BUDGET", "0")
        assert pdp_budget() == 0

    def test_orcamento_invalido_cai_no_default(self, monkeypatch):
        monkeypatch.setenv("RAC_AMAZON_PDP_BUDGET", "abc")
        assert pdp_budget() == DEFAULT_BUDGET


class TestExtractSellerFromPdp:
    def test_merchant_info(self):
        html = '<div id="merchant-info">Vendido por LojaX e enviado por Amazon</div>'
        assert extract_seller_from_pdp(html) == "LojaX"

    def test_seller_profile_trigger(self):
        html = '<a id="sellerProfileTriggerId">KARZEN ELETRO</a>'
        assert extract_seller_from_pdp(html) == "KARZEN ELETRO"

    def test_fallback_por_texto_sobrevive_a_redesign(self):
        """Sem nenhum id conhecido, o rótulo PT-BR ainda resolve."""
        html = "<div class='classe-nova-qualquer'>Vendido por REFRIGERACAO MOTA</div>"
        assert extract_seller_from_pdp(html) == "REFRIGERACAO MOTA"

    def test_sold_by_em_ingles(self):
        html = '<div id="merchant-info">Sold by ACME Store</div>'
        assert extract_seller_from_pdp(html) == "ACME Store"

    def test_pdp_sem_vendedor(self):
        assert extract_seller_from_pdp("<div>produto indisponível</div>") is None

    def test_html_vazio(self):
        assert extract_seller_from_pdp("") is None


class TestCleanSellerName:
    def test_remove_prefixo_e_espacos(self):
        assert clean_seller_name("  Vendido por:  LojaX  ") == "LojaX"

    def test_so_numeros_nao_e_vendedor(self):
        assert clean_seller_name("R$ 2.199,90") is None

    def test_texto_longo_demais_e_ruido(self):
        assert clean_seller_name("x" * 120) is None

    def test_none_e_vazio(self):
        assert clean_seller_name(None) is None
        assert clean_seller_name("") is None


class TestIsAmazonSelf:
    @pytest.mark.parametrize("nome", ["Amazon", "amazon.com.br", "AMAZON BR"])
    def test_1p(self, nome):
        assert is_amazon_self(nome) is True

    @pytest.mark.parametrize("nome", ["LojaX", "KARZEN ELETRO", None, ""])
    def test_3p_ou_desconhecido(self, nome):
        assert is_amazon_self(nome) is False


class TestAmazonSellerCache:
    def test_put_get_roundtrip(self, tmp_path):
        cache = AmazonSellerCache(path=tmp_path / "c.json")
        cache.put("B0ABCDEFGH", "LojaX")
        assert cache.get("B0ABCDEFGH") == "LojaX"

    def test_persiste_em_disco(self, tmp_path):
        p = tmp_path / "c.json"
        cache = AmazonSellerCache(path=p)
        cache.put("B0ABCDEFGH", "LojaX")
        assert cache.save() is True

        recarregado = AmazonSellerCache(path=p)
        assert recarregado.get("B0ABCDEFGH") == "LojaX"

    def test_should_retry_asin_novo(self, tmp_path):
        cache = AmazonSellerCache(path=tmp_path / "c.json")
        assert cache.should_retry("B0NOVO12345") is True

    def test_nao_retenta_asin_ja_resolvido(self, tmp_path):
        cache = AmazonSellerCache(path=tmp_path / "c.json")
        cache.put("B0ABCDEFGH", "LojaX")
        assert cache.should_retry("B0ABCDEFGH") is False

    def test_quarentena_protege_o_orcamento(self, tmp_path):
        """ASIN que falhou não pode consumir um PDP na execução seguinte."""
        cache = AmazonSellerCache(path=tmp_path / "c.json")
        cache.mark_failed("B0MORTO1234", "PDP sem vendedor")
        assert cache.should_retry("B0MORTO1234") is False

    def test_quarentena_expira(self, tmp_path):
        cache = AmazonSellerCache(path=tmp_path / "c.json", retry_days=0)
        cache.mark_failed("B0MORTO1234")
        assert cache.should_retry("B0MORTO1234") is True

    def test_resolver_tira_da_quarentena(self, tmp_path):
        cache = AmazonSellerCache(path=tmp_path / "c.json")
        cache.mark_failed("B0ABCDEFGH")
        cache.put("B0ABCDEFGH", "LojaX")
        assert cache.get("B0ABCDEFGH") == "LojaX"
        assert cache.should_retry("B0ABCDEFGH") is False

    def test_cache_corrompido_nao_derruba_o_scraper(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text("{ isso não é json", encoding="utf-8")
        assert len(AmazonSellerCache(path=p)) == 0

    def test_cache_com_raiz_lista_nao_derruba(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text(json.dumps(["a", "b"]), encoding="utf-8")
        assert len(AmazonSellerCache(path=p)) == 0


class TestOrcamentoNoScraper:
    """O teto é a proteção que impede a resolução de derrubar a coleta."""

    @pytest.fixture
    def scraper(self, tmp_path, monkeypatch):
        from scrapers.amazon import AmazonScraper

        monkeypatch.setenv("RAC_AMAZON_PDP_BUYBOX", "1")
        monkeypatch.setenv("RAC_AMAZON_PDP_BUDGET", "3")
        s = AmazonScraper()
        s._seller_cache = AmazonSellerCache(path=tmp_path / "c.json")
        s._pdp_budget_left = 3
        return s

    @staticmethod
    def _registros(n):
        return [
            {"Buy Box Seller": None, "_asin": f"B0TESTE{i:04d}"}
            for i in range(n)
        ]

    def test_para_no_teto(self, scraper, monkeypatch):
        chamadas = []
        monkeypatch.setattr(
            scraper, "_fetch_pdp_seller",
            lambda asin: chamadas.append(asin) or "LojaX",
        )

        registros = self._registros(10)
        scraper._resolve_buybox_via_pdp(registros)

        assert len(chamadas) == 3, "orçamento de 3 PDPs não foi respeitado"
        assert sum(1 for r in registros if r["Buy Box Seller"]) == 3
        # Os demais seguem sem buy box — degradar, não derrubar.
        assert sum(1 for r in registros if not r["Buy Box Seller"]) == 7

    def test_cache_nao_gasta_orcamento(self, scraper, monkeypatch):
        scraper._seller_cache.put("B0TESTE0000", "LojaCache")
        chamadas = []
        monkeypatch.setattr(
            scraper, "_fetch_pdp_seller",
            lambda asin: chamadas.append(asin) or "LojaPDP",
        )

        registros = self._registros(1)
        scraper._resolve_buybox_via_pdp(registros)

        assert chamadas == [], "ASIN em cache não pode gastar um PDP"
        assert registros[0]["Buy Box Seller"] == "LojaCache"

    def test_desligada_nao_faz_nada(self, scraper, monkeypatch):
        monkeypatch.setenv("RAC_AMAZON_PDP_BUYBOX", "0")
        chamadas = []
        monkeypatch.setattr(
            scraper, "_fetch_pdp_seller",
            lambda asin: chamadas.append(asin) or "LojaX",
        )

        registros = self._registros(5)
        scraper._resolve_buybox_via_pdp(registros)

        assert chamadas == []
        assert all(r["Buy Box Seller"] is None for r in registros)

    def test_pdp_que_falha_vai_pra_quarentena(self, scraper, monkeypatch):
        monkeypatch.setattr(scraper, "_fetch_pdp_seller", lambda asin: None)

        registros = self._registros(1)
        scraper._resolve_buybox_via_pdp(registros)

        assert registros[0]["Buy Box Seller"] is None
        assert scraper._seller_cache.should_retry("B0TESTE0000") is False

    def test_classifica_1p_vs_3p(self, scraper, monkeypatch):
        monkeypatch.setattr(
            scraper, "_fetch_pdp_seller", lambda asin: "Amazon.com.br",
        )
        registros = self._registros(1)
        scraper._resolve_buybox_via_pdp(registros)
        assert registros[0]["Tipo Seller"] == "1P"

    def test_registro_sem_asin_e_ignorado(self, scraper, monkeypatch):
        chamadas = []
        monkeypatch.setattr(
            scraper, "_fetch_pdp_seller",
            lambda asin: chamadas.append(asin) or "LojaX",
        )
        registros = [{"Buy Box Seller": None, "_asin": None}]
        scraper._resolve_buybox_via_pdp(registros)
        assert chamadas == []


class TestFulfillmentNaoVazaNoNome:
    """
    O PDP escreve "Vendido por X e enviado PELA Amazon".

    A primeira versão cortava só o literal " e enviado por", que NÃO casa com
    "e enviado pela" — e o teste da época passava porque eu o escrevi com a
    mesma redação errada do código. O efeito em produção seria gravar
    "KARZEN ELETRO e enviado pela Amazon" como vendedor: nome sujo no relatório
    e, pior, um produto vendido pela própria Amazon classificado como 3P,
    corrompendo justamente o share of buy box que a coleta existe para medir.
    """

    @pytest.mark.parametrize("html,esperado", [
        ('<div id="merchant-info">Vendido por KARZEN e enviado pela Amazon</div>',
         "KARZEN"),
        ('<div id="merchant-info">Vendido por LojaX e enviado por Amazon</div>',
         "LojaX"),
        ('<div id="merchant-info">Vendido por LojaY e entregue pelo parceiro</div>',
         "LojaY"),
        ('<div id="merchant-info">Vendido por LojaZ E ENVIADO PELA AMAZON</div>',
         "LojaZ"),
        ('<div id="merchant-info">Sold by ACME and Fulfilled by Amazon</div>',
         "ACME"),
        ('<div id="merchant-info">Sold by ACME and shipped by Amazon</div>',
         "ACME"),
    ])
    def test_corta_o_trecho_de_fulfillment(self, html, esperado):
        assert extract_seller_from_pdp(html) == esperado

    def test_1p_com_fulfillment_continua_1p(self):
        """O caso que a classificação errada estragava."""
        html = ('<div id="merchant-info">Vendido por Amazon.com.br '
                'e enviado pela Amazon</div>')
        assert is_amazon_self(extract_seller_from_pdp(html)) is True


class TestQuarentenaPersiste:
    def test_run_sem_nenhum_sucesso_ainda_grava_a_quarentena(self, tmp_path, monkeypatch):
        """
        O caso all-fail: a Amazon bloqueia todos os PDPs.

        A quarentena existe para os ASINs mortos não reconsumirem o orçamento
        na execução seguinte. Se ela só fosse gravada quando algum PDP desse
        certo, o cenário em que ela mais importa — nenhum deu — seria
        exatamente o que não persistia.
        """
        from scrapers.amazon import AmazonScraper

        monkeypatch.setenv("RAC_AMAZON_PDP_BUYBOX", "1")
        cache_path = tmp_path / "c.json"

        s = AmazonScraper()
        s._seller_cache = AmazonSellerCache(path=cache_path)
        s._pdp_budget_left = 5
        monkeypatch.setattr(s, "_fetch_pdp_seller", lambda asin: None)

        s._resolve_buybox_via_pdp(
            [{"Buy Box Seller": None, "_asin": "B0MORTO1234"}]
        )

        assert cache_path.exists(), "quarentena precisa chegar ao disco"
        recarregado = AmazonSellerCache(path=cache_path)
        assert recarregado.should_retry("B0MORTO1234") is False
