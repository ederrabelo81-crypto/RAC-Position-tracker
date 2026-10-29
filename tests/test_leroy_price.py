"""
Preço da Leroy Merlin lido no PDP (JSON-LD), não na média do índice Algolia.

Caso de origem (28/10/2026): Split Vize Midea 12K quente/frio (371381) gravado
a R$ 1.091,25 — `averagePromotionalPrice` — em todos os turnos por meses, abaixo
do modelo só frio (371378, R$ 1.552,81). Virou o piso do 📅 Daily Price Vision.
"""

import json

import pytest

import scrapers.leroy_merlin as leroy_mod
from scrapers.leroy_merlin import LeroyMerlinScraper
from utils.leroy_price import PdpPrice, extract_pdp_price
from utils.leroy_sellers import LeroySellerCache

SELLER_ID = "5be5eb765cb50968730358f5"


def _pdp(*ld_blocks, extra: str = "") -> str:
    """PDP sintético com blocos JSON-LD (+ HTML extra, ex. o rótulo de vendedor)."""
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in ld_blocks
    )
    return f"<html><head>{scripts}</head><body>{extra}</body></html>"


def _produto(offers) -> dict:
    return {"@context": "https://schema.org", "@type": "Product",
            "name": "Ar Condicionado Split", "offers": offers}


def _offer(price, availability="https://schema.org/InStock", seller=None) -> dict:
    o = {"@type": "Offer", "price": price, "priceCurrency": "BRL",
         "availability": availability}
    if seller:
        o["seller"] = {"@type": "Organization", "name": seller}
    return o


class TestExtractPdpPrice:
    def test_oferta_unica(self):
        r = extract_pdp_price(_pdp(_produto(_offer("2339.00"))))
        assert r == PdpPrice(2339.0, True, "jsonld")
        assert r.lido

    def test_preco_numerico_e_formato_brasileiro(self):
        assert extract_pdp_price(_pdp(_produto(_offer(1552.81)))).preco == 1552.81
        assert extract_pdp_price(_pdp(_produto(_offer("2.339,00")))).preco == 2339.0

    def test_indisponivel_nao_tem_preco(self):
        r = extract_pdp_price(_pdp(_produto(_offer("1091.25", "https://schema.org/OutOfStock"))))
        assert r.preco is None
        assert r.disponivel is False
        assert r.camada == "indisponivel"
        assert r.lido  # o PDP respondeu: não está à venda

    @pytest.mark.parametrize("disp", ["SoldOut", "Discontinued", "http://schema.org/InStoreOnly"])
    def test_outras_indisponibilidades(self, disp):
        assert extract_pdp_price(_pdp(_produto(_offer("999", disp)))).disponivel is False

    def test_availability_omitida_conta_como_disponivel(self):
        r = extract_pdp_price(_pdp(_produto({"@type": "Offer", "price": "1999.90"})))
        assert r.preco == 1999.9
        assert r.disponivel is None

    def test_aggregate_offer_usa_low_price(self):
        agg = {"@type": "AggregateOffer", "lowPrice": "1899.00", "highPrice": "2100.00",
               "availability": "https://schema.org/InStock"}
        assert extract_pdp_price(_pdp(_produto(agg))).preco == 1899.0

    def test_varias_ofertas_casa_com_o_vencedor(self):
        ofertas = [_offer("2100", seller="Frio Total"), _offer("2339", seller="Leroy Merlin")]
        r = extract_pdp_price(_pdp(_produto(ofertas)), vencedor="LEROY MERLIN")
        assert r == PdpPrice(2339.0, True, "jsonld_vencedor")

    def test_varias_ofertas_sem_vencedor_fica_o_menor_e_diz_isso(self):
        ofertas = [_offer("2100", seller="Frio Total"), _offer("2339", seller="Leroy Merlin")]
        r = extract_pdp_price(_pdp(_produto(ofertas)))
        assert r.preco == 2100.0
        assert r.camada == "jsonld_menor"

    def test_oferta_indisponivel_nao_compete(self):
        ofertas = [_offer("900", "OutOfStock"), _offer("2339")]
        assert extract_pdp_price(_pdp(_produto(ofertas))).preco == 2339.0

    def test_produtos_diferentes_no_mesmo_pdp_e_ambiguo(self):
        r = extract_pdp_price(_pdp(_produto(_offer("2339")), _produto(_offer("1500"))))
        assert r.preco is None
        assert r.camada == "ambiguo"
        assert not r.lido

    def test_mesmo_produto_repetido_nao_e_ambiguo(self):
        p = _produto(_offer("2339"))
        assert extract_pdp_price(_pdp(p, p)).preco == 2339.0

    def test_graph(self):
        bloco = {"@context": "https://schema.org",
                 "@graph": [{"@type": "BreadcrumbList"}, _produto(_offer("2339"))]}
        assert extract_pdp_price(_pdp(bloco)).preco == 2339.0

    def test_produto_relacionado_em_itemlist_nao_conta(self):
        lista = {"@type": "ItemList", "itemListElement": [_produto(_offer("500"))]}
        assert extract_pdp_price(_pdp(lista)) == PdpPrice(None, None, "sem_preco")

    @pytest.mark.parametrize("preco", ["0", "0.99", "250000"])
    def test_preco_fora_da_faixa_e_descartado(self, preco):
        assert extract_pdp_price(_pdp(_produto(_offer(preco)))).preco is None

    @pytest.mark.parametrize("html", ["", "<html></html>",
                                      '<script type="application/ld+json">{quebrado</script>'])
    def test_sem_json_ld(self, html):
        r = extract_pdp_price(html)
        assert r.preco is None and not r.lido


# --------------------------------------------------------------------------
# Integração com o scraper
# --------------------------------------------------------------------------

def _rotulo(nome: str) -> str:
    return f"<div>Vendido e entregue por {nome}</div>"


@pytest.fixture
def leroy(tmp_path, monkeypatch):
    """Scraper isolado: sem rede, sem espera, PDPs servidos por `pdps`."""
    s = LeroyMerlinScraper()
    s._seller_cache = LeroySellerCache(path=tmp_path / "cache.json")
    s._buybox_pdp_enabled = True
    s._preco_pdp_enabled = True
    # O padrão do .env não pode decidir o teste (LEROY_PRECO_MEDIA_FALLBACK=1).
    monkeypatch.setattr(leroy_mod, "_PRECO_MEDIA_FALLBACK", False)
    s.pdps = {}
    s.abertos = []
    monkeypatch.setattr(s, "_random_delay", lambda **kw: None)
    monkeypatch.setattr(s, "_fetch_pdp_browser", lambda url: None)
    monkeypatch.setattr(s, "_fetch_pdp_browser_preco", lambda url: None)

    def fetch(url):
        s.abertos.append(url)
        return s.pdps.get(url.rsplit("/", 1)[-1])

    monkeypatch.setattr(s, "_fetch_pdp_requests", fetch)
    return s


def _hit(pid, url, media=1091.25, sellers=None):
    h = {"name": f"Ar Condicionado {pid}", "objectID": pid, "url": url,
         "averagePromotionalPrice": media}
    if sellers:
        h["marketplaceSellers"] = sellers
    return h


def _parse(s, hits, kw="midea 12000 btus"):
    return s._parse_algolia_hits(hits, kw, {}, 0)


class TestPrecoNoScraper:
    def test_caso_vize_midea_preco_do_pdp_substitui_a_media(self, leroy):
        leroy.pdps["vize"] = _pdp(_produto(_offer("1899.00")))
        rec = _parse(leroy, [_hit("371381", "/vize")])[0]
        assert rec["Preço (R$)"] == 1899.0
        assert leroy._seller_metrics["preco_pdp_lido"] == 1

    def test_indisponivel_fica_sem_preco_mas_a_linha_fica(self, leroy):
        leroy.pdps["vize"] = _pdp(_produto(_offer("1091.25", "OutOfStock")))
        recs = _parse(leroy, [_hit("371381", "/vize")])
        assert len(recs) == 1
        assert recs[0]["Preço (R$)"] is None
        assert leroy._seller_metrics["preco_pdp_indisponivel"] == 1

    def test_sem_leitura_nao_grava_a_media(self, leroy):
        rec = _parse(leroy, [_hit("371381", "/bloqueado")])[0]
        assert rec["Preço (R$)"] is None
        assert leroy._seller_metrics["preco_sem_leitura"] == 1

    def test_contingencia_explicita_grava_a_media(self, leroy, monkeypatch):
        monkeypatch.setattr(leroy_mod, "_PRECO_MEDIA_FALLBACK", True)
        rec = _parse(leroy, [_hit("371381", "/bloqueado")])[0]
        assert rec["Preço (R$)"] == 1091.25
        assert leroy._seller_metrics["preco_media_fallback"] == 1

    def test_leitura_desligada_volta_ao_comportamento_antigo(self, leroy):
        leroy._preco_pdp_enabled = False
        leroy._buybox_pdp_enabled = False
        rec = _parse(leroy, [_hit("371381", "/vize")])[0]
        assert rec["Preço (R$)"] == 1091.25
        assert leroy.abertos == []

    def test_pdp_da_buy_box_nao_e_reaberto_pelo_preco(self, leroy):
        ofertas = [_offer("2100", seller="Frio Total"), _offer("2339", seller="Leroy Merlin")]
        leroy.pdps["elgin"] = _pdp(_produto(ofertas), extra=_rotulo("LEROY MERLIN"))
        rec = _parse(leroy, [_hit("3962339062", "/elgin", media=2290.53,
                                  sellers=[SELLER_ID])])[0]
        assert rec["Buy Box Seller"] == "Leroy Merlin"
        assert rec["Preço (R$)"] == 2339.0          # oferta do vencedor, não a menor
        assert leroy.abertos.count("https://www.leroymerlin.com.br/elgin") == 1
        assert leroy._seller_metrics["preco_pdp_abertos"] == 0

    def test_url_repetida_entre_keywords_custa_um_pdp(self, leroy):
        leroy.pdps["vize"] = _pdp(_produto(_offer("1899.00")))
        _parse(leroy, [_hit("371381", "/vize")], kw="midea 12000 btus")
        rec = _parse(leroy, [_hit("371381", "/vize")], kw="ar condicionado 12000")[0]
        assert rec["Preço (R$)"] == 1899.0
        assert leroy.abertos == ["https://www.leroymerlin.com.br/vize"]

    def test_orcamento_esgotado_deixa_sem_preco(self, leroy):
        leroy._preco_budget = 1
        leroy.pdps["a"] = _pdp(_produto(_offer("1899")))
        leroy.pdps["b"] = _pdp(_produto(_offer("2099")))
        recs = _parse(leroy, [_hit("1", "/a"), _hit("2", "/b")])
        assert [r["Preço (R$)"] for r in recs] == [1899.0, None]

    def test_falhas_seguidas_abortam_a_passada(self, leroy, monkeypatch):
        monkeypatch.setattr(leroy_mod, "_PRECO_MAX_FALHAS_SEGUIDAS", 3)
        hits = [_hit(str(i), f"/x{i}") for i in range(6)]  # nenhum PDP responde
        _parse(leroy, hits)
        assert leroy._preco_abortado is True
        assert leroy._seller_metrics["preco_pdp_abertos"] == 3


class TestRevisaoCubic:
    """Achados da revisão do PR #413."""

    def test_tipo_como_iri_completo(self):
        p = _produto(_offer("2339"))
        p["@type"] = "https://schema.org/Product"
        assert extract_pdp_price(_pdp(p)).preco == 2339.0

    def test_aggregate_com_filhas_sem_preco_usa_low_price(self):
        agg = {"@type": "AggregateOffer", "lowPrice": "1899", "offers": [
            {"@type": "Offer", "seller": {"name": "Loja X"}}]}
        assert extract_pdp_price(_pdp(_produto(agg))).preco == 1899.0

    def test_vencedor_sem_oferta_casada_fica_sem_preco(self):
        ofertas = [_offer("2100", seller="Frio Total"), _offer("2339", seller="Refri Center")]
        r = extract_pdp_price(_pdp(_produto(ofertas)), vencedor="LEROY MERLIN")
        assert r.preco is None and r.camada == "ambiguo"

    def test_primeiro_seller_listado_nao_vira_vencedor(self, leroy):
        """2+ sellers sem leitura da buy box: preço não é atribuído ao 1º listado."""
        leroy._buybox_pdp_enabled = False
        ofertas = [_offer("2100", seller="Frio Total"), _offer("2339", seller="Leroy Merlin")]
        leroy.pdps["p"] = _pdp(_produto(ofertas))
        hit = _hit("3900000001", "/p", sellers=[SELLER_ID, "5be5eb765cb50968730358f4"])
        leroy._seller_cache.put(SELLER_ID, "Frio Total")
        rec = _parse(leroy, [hit])[0]
        assert rec["Preço (R$)"] == 2100.0                     # menor, sinalizado
        assert leroy._seller_metrics["preco_pdp_menor_oferta"] == 1


class _PaginaFake:
    """Página Playwright mínima: registra o que foi esperado."""

    def __init__(self, html="<html>pdp</html>", erro_goto=None):
        self.html, self.erro_goto = html, erro_goto
        self.esperas, self.fechada = [], False

    def goto(self, url, **kw):
        if self.erro_goto:
            raise self.erro_goto

    def wait_for_selector(self, sel, **kw):
        self.esperas.append(("ld+json", sel))

    def wait_for_function(self, js, **kw):
        self.esperas.append(("rotulo", js))

    def wait_for_load_state(self, *a, **kw):
        self.esperas.append(("rede", a))

    def content(self):
        return self.html

    def set_default_timeout(self, ms):
        pass

    def is_closed(self):
        return self.fechada

    def close(self):
        self.fechada = True


class _ChromeLocalFake:
    def __init__(self, pagina):
        self.pagina, self.aquecido = pagina, []

    def new_page(self):
        return self.pagina

    def warmup(self, page, url, **kw):
        self.aquecido.append(url)
        return True


class TestBrowserDoPreco:
    @pytest.fixture(autouse=True)
    def _sem_chrome_local(self, monkeypatch):
        # O .env do PC coletor liga RAC_LOCAL_CHROME: o teste não pode abrir o Chrome real.
        monkeypatch.setattr(leroy_mod, "is_local_chrome_enabled", lambda: False)

    def test_preco_nao_espera_o_rotulo(self):
        s = LeroyMerlinScraper()
        s._page = _PaginaFake()
        assert s._fetch_pdp_browser_preco("https://x/p") == "<html>pdp</html>"
        assert [tipo for tipo, _ in s._page.esperas] == ["ld+json"]

    def test_buybox_segue_esperando_o_rotulo(self):
        s = LeroyMerlinScraper()
        s._page = _PaginaFake()
        s._fetch_pdp_browser("https://x/p")
        assert s._page.esperas[0][0] == "rotulo"

    def test_fetch_preco_usa_o_caminho_sem_rotulo(self, monkeypatch):
        s = LeroyMerlinScraper()
        s._pdp_requests_strikes = 3  # requests fora
        chamados = []
        monkeypatch.setattr(s, "_fetch_pdp_browser", lambda u: chamados.append("rotulo"))
        monkeypatch.setattr(s, "_fetch_pdp_browser_preco", lambda u: chamados.append("preco"))
        s._fetch_preco("https://x/p", None)
        assert chamados == ["preco"]

    def test_chrome_local_abre_os_pdps(self, monkeypatch):
        local, propria = _PaginaFake("<html>local</html>"), _PaginaFake("<html>propria</html>")
        chrome = _ChromeLocalFake(local)
        monkeypatch.setattr(leroy_mod, "is_local_chrome_enabled", lambda: True)
        monkeypatch.setattr(leroy_mod, "get_local_browser", lambda: chrome)
        s = LeroyMerlinScraper()
        s._page = propria
        assert s._fetch_pdp_browser_preco("https://x/p") == "<html>local</html>"
        assert s._fetch_pdp_browser_preco("https://x/q") == "<html>local</html>"
        assert chrome.aquecido == [leroy_mod._HOME_URL]  # warm-up uma vez só
        s._close()
        assert local.fechada

    def test_sem_chrome_local_cai_no_browser_proprio(self, monkeypatch):
        chamadas = []
        monkeypatch.setattr(leroy_mod, "is_local_chrome_enabled", lambda: True)
        monkeypatch.setattr(leroy_mod, "get_local_browser", lambda: chamadas.append(1))
        s = LeroyMerlinScraper()
        s._page = _PaginaFake("<html>propria</html>")
        assert s._fetch_pdp_browser_preco("https://x/p") == "<html>propria</html>"
        s._fetch_pdp_browser_preco("https://x/q")
        assert len(chamadas) == 1  # não insiste a cada PDP

    def test_janela_local_fechada_reabre_na_proxima(self, monkeypatch):
        morta = _PaginaFake(erro_goto=Exception("Target page, context or browser has been closed"))
        nova = _PaginaFake("<html>nova</html>")
        paginas = [morta, nova]
        chrome = _ChromeLocalFake(None)
        chrome.new_page = lambda: paginas.pop(0)
        monkeypatch.setattr(leroy_mod, "is_local_chrome_enabled", lambda: True)
        monkeypatch.setattr(leroy_mod, "get_local_browser", lambda: chrome)
        s = LeroyMerlinScraper()
        assert s._fetch_pdp_browser_preco("https://x/p") is None
        assert s._fetch_pdp_browser_preco("https://x/q") == "<html>nova</html>"

    def test_mais_vendidos_fecha_a_aba_local(self, monkeypatch):
        """Sem context manager não há `_close()`: o `_fechar` da fonte fecha a aba."""
        from bestsellers.sources.leroy_merlin import LeroyMerlinBestSellers

        local = _PaginaFake("<html>local</html>")
        monkeypatch.setattr(leroy_mod, "is_local_chrome_enabled", lambda: True)
        monkeypatch.setattr(leroy_mod, "get_local_browser", lambda: _ChromeLocalFake(local))
        fonte = LeroyMerlinBestSellers()
        fonte._abrir()
        fonte._leroy._fetch_pdp_browser("https://x/p")
        fonte._fechar()
        assert local.fechada

    def test_warmup_que_falha_e_retentado_com_teto(self, monkeypatch):
        local = _PaginaFake("<html>local</html>")
        chrome = _ChromeLocalFake(local)
        chrome.warmup = lambda page, url, **kw: chrome.aquecido.append(url) or False
        monkeypatch.setattr(leroy_mod, "is_local_chrome_enabled", lambda: True)
        monkeypatch.setattr(leroy_mod, "get_local_browser", lambda: chrome)
        s = LeroyMerlinScraper()
        for _ in range(5):
            assert s._fetch_pdp_browser_preco("https://x/p") == "<html>local</html>"
        assert len(chrome.aquecido) == leroy_mod._PDP_WARMUP_TENTATIVAS

    def test_aba_nova_apos_reconexao_reaquece(self, monkeypatch):
        morta = _PaginaFake(erro_goto=Exception("Target page, context or browser has been closed"))
        nova = _PaginaFake("<html>nova</html>")
        paginas = [morta, nova]
        chrome = _ChromeLocalFake(None)
        chrome.new_page = lambda: paginas.pop(0)
        monkeypatch.setattr(leroy_mod, "is_local_chrome_enabled", lambda: True)
        monkeypatch.setattr(leroy_mod, "get_local_browser", lambda: chrome)
        s = LeroyMerlinScraper()
        s._fetch_pdp_browser_preco("https://x/p")
        s._fetch_pdp_browser_preco("https://x/q")
        assert len(chrome.aquecido) == 2  # uma por aba
